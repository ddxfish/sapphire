# core/continuity/execution_context.py — Isolated execution environment
#
# Each task (heartbeat, daemon, foreground) gets its own ExecutionContext.
# Zero shared mutable state — no singleton mutations, no bleed between tasks.
#
# The FunctionManager is treated as a READ-ONLY registry.
# Prompt, tools, scopes, provider are all resolved at construction time.

import logging
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

import config
from core.chat.llm_providers import get_provider_by_key, get_first_available_provider, get_generation_params

logger = logging.getLogger(__name__)

# Tracks the persona of the task currently running in this thread/async context.
# Set by ExecutionContext.run() and used by tools that need to know the
# *parent's* persona — notably spawn_agent(prompt='self'), which must inherit
# from the spawning agent, NOT from the user's foreground chat. Scout #7.
current_task_persona: ContextVar[Optional[str]] = ContextVar('current_task_persona', default=None)

# Warn once per process about a loop-guard version skew (see helper below).
_loop_guard_skew_warned = False


def _exec_with_loop_counts(fn, *args, loop_counts=None, **kwargs):
    """Call fn(*args, loop_counts=..., **kwargs), degrading gracefully if the tool
    engine predates the loop guard.

    The loop guard is a cosmetic warning feature. On a partial update where
    core/chat/chat_tool_calling.py is older than this file, fn won't accept the
    loop_counts kwarg and Python raises TypeError AT BIND TIME - before any tool
    runs - so it is safe to retry without it (no double execution). We drop the
    loop guard for this call and keep the trigger running. Warn once per process so
    the skew shows up in logs without spamming every trigger. A TypeError that is
    NOT about loop_counts is a real error, re-raised untouched."""
    global _loop_guard_skew_warned
    try:
        return fn(*args, loop_counts=loop_counts, **kwargs)
    except TypeError as e:
        if "loop_counts" not in str(e):
            raise
        if not _loop_guard_skew_warned:
            _loop_guard_skew_warned = True
            logger.warning(
                "[CONTINUITY] tool engine does not accept 'loop_counts' - core/chat/"
                "chat_tool_calling.py is older than execution_context.py (partial update). "
                "Loop guard disabled for triggers; continuing DEGRADED. Re-run the update so "
                "that file matches, then restart to re-enable."
            )
        return fn(*args, **kwargs)


def _scrub_inline_images(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Strip inline base64 image blocks from a user message before persistence.

    Tool-returned images are injected mid-loop as a base64 user message (for the
    LLM to see THIS turn) by `_inject_tool_images`, and are separately saved to
    the chat DB with a `<<IMG::tool:id>>` marker in the tool-result message. So
    the base64 copy is turn-only — persisting it inline would replay the image
    every turn and bloat the chat (the same hazard the event-image dual-form swap
    guards). This drops only `type:image` blocks; `tool_result` and `text` blocks
    are preserved untouched (so wire_to_canonical's tool-result handling is safe).
    A text-only remainder collapses to a plain string — the safest persisted shape.
    Pure function; returns the message unchanged when there's nothing to scrub.
    2026-06-13.
    """
    if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
        return msg
    kept = [b for b in msg["content"]
            if not (isinstance(b, dict) and b.get("type") == "image")]
    if len(kept) == len(msg["content"]):
        return msg  # no image blocks — untouched (e.g. tool_result messages)
    if not kept:
        # Content was image(s) only — never persist an empty-content user message
        # (invisible-bubble hazard). Unreachable from current producers (they all
        # prepend a text block) but guarded so a future one can't trip it.
        return {**msg, "content": "[image]"}
    if all(isinstance(b, dict) and b.get("type") == "text" for b in kept):
        # str()-coerce defensively — a malformed text block (text=None/int) must
        # collapse cleanly, never crash the persist with a join TypeError.
        return {**msg, "content": " ".join(str(b.get("text") or "") for b in kept).strip()}
    return {**msg, "content": kept}


class ExecutionContext:
    """Self-contained execution environment for a single task run.

    Resolves prompt, tools, scopes, and provider at construction.
    Runs LLM + tool loop without touching any singleton state.
    """

    def __init__(self, function_manager, tool_engine, task_settings: Dict[str, Any]):
        self.fm = function_manager
        self.tool_engine = tool_engine
        self.task_settings = task_settings

        # Resolve everything upfront — all read-only operations.
        # Provider BEFORE scopes: _build_scopes stamps tool provenance
        # (set_tool_context) with the RESOLVED provider/model — the old
        # order left provider always-None and model stale in every
        # continuity run's mindpalace metadata.
        self.system_prompt = self._build_prompt()
        self.tools = self._resolve_tools()
        self._allowed_tool_names = {t["function"]["name"] for t in self.tools if "function" in t} if self.tools else None
        self.provider_key, self.provider, self.model_override = self._resolve_provider()
        self.scopes = self._build_scopes()
        self.gen_params = self._build_gen_params()
        self.tool_log = []  # List of tool names called during run()
        # Populated by run() when the LLM loop didn't produce a real reply
        # (tool-round exhaustion, context overflow, empty LLM output). Lets
        # callers distinguish "clean done with response" from "done with a
        # placeholder" so agent UI / status reports don't render a green
        # success for a no-op run. Scout #15 — 2026-04-20. None = clean run.
        self.degraded_reason: Optional[str] = None

    # ── Construction (read-only) ──

    def _build_prompt(self) -> str:
        """Build system prompt from task settings. No global mutation."""
        prompt_name = self.task_settings.get("prompt", "sapphire")
        from core import prompts

        prompt_data = prompts.get_prompt(prompt_name)
        # Stashed for _build_scopes/_resolve_provider: a privacy_required
        # prompt must not run on a non-local provider in this lane (there is
        # no private_chat toggle here — local-only IS the guarantee).
        # OR of the task's persisted carrier and the live prompt-derived flag
        # (vault phase 0c): the live half catches prompts that BECAME private
        # after the task was saved; the carrier half survives the prompt not
        # resolving at all (deleted, or asleep in a locked vault) — before
        # this, an unresolvable prompt silently dropped privacy and the task
        # ran on whatever provider it was pinned to, cloud included.
        self._prompt_privacy_required = (
            bool(self.task_settings.get("privacy_required"))
            or bool(isinstance(prompt_data, dict)
                    and prompt_data.get('privacy_required')))
        if prompt_data:
            system_prompt = prompt_data.get("content") if isinstance(prompt_data, dict) else str(prompt_data)
        else:
            logger.warning(
                f"[ExecCtx] Prompt '{prompt_name}' not found — running as "
                f"generic assistant"
                + (" (task carries privacy_required — local-only still enforced)"
                   if self._prompt_privacy_required else ""))
            system_prompt = "You are a helpful assistant."

        # Name substitutions
        username = getattr(config, 'DEFAULT_USERNAME', 'Human')
        ai_name = 'Sapphire'
        system_prompt = system_prompt.replace("{user_name}", username).replace("{ai_name}", ai_name)

        # Datetime injection. inject_datetime='date' stamps the DAY only —
        # for multi-run sessions (librarian nights) a minute stamp would
        # change the system prompt every run and torch the provider's
        # prompt cache across the whole session. True keeps the classic
        # minute form.
        inject_dt = self.task_settings.get("inject_datetime")
        if inject_dt:
            try:
                from zoneinfo import ZoneInfo
                tz_name = getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC'
                now = datetime.now(ZoneInfo(tz_name))
                tz_label = f" ({tz_name})"
            except Exception:
                now = datetime.now()
                tz_label = ""
            if inject_dt == 'date':
                system_prompt = f"{system_prompt}\n\nCurrent date: {now.strftime('%A, %B %d, %Y')}{tz_label}"
            else:
                system_prompt = f"{system_prompt}\n\nCurrent date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}{tz_label}"

        # Task-supplied append (e.g. the librarian's session-start self
        # snapshot) — ambient identity context, not a message. Rides every
        # round; stable within a session so the prompt cache holds.
        extra = self.task_settings.get("system_append")
        if extra:
            system_prompt = f"{system_prompt}\n\n{extra}"

        return system_prompt

    def _resolve_tools(self) -> Optional[List[Dict]]:
        """Resolve toolset to tool list. READ-ONLY — no mutation of FunctionManager.
        `extra_toolsets` in task settings unions module/toolset sets on top,
        same semantics as update_enabled_functions."""
        toolset_name = self.task_settings.get("toolset", "none")
        extra_toolsets = self.task_settings.get("extra_toolsets") or []

        if (not toolset_name or toolset_name == "none") and not extra_toolsets:
            return None

        # Resolve toolset name to function names — same logic as update_enabled_functions
        # but without mutating _enabled_tools or current_toolset_name
        from core.toolsets import toolset_manager

        def _fn_names(name):
            if name in self.fm.function_modules:
                return self.fm.function_modules[name]['available_functions']
            if toolset_manager.toolset_exists(name):
                return toolset_manager.get_toolset_functions(name)
            return [name]

        if not toolset_name or toolset_name == "none":
            tools = []
        elif toolset_name == "all":
            tools = self.fm.all_possible_tools.copy()
        else:
            fn_set = set(_fn_names(toolset_name))
            tools = [t for t in self.fm.all_possible_tools
                     if t['function']['name'] in fn_set]

            if not tools:
                logger.warning(f"[ExecCtx] Toolset '{toolset_name}' resolved to 0 tools")

        if extra_toolsets:
            have = {t['function']['name'] for t in tools}
            extra_set = set()
            for name in extra_toolsets:
                extra_set |= set(_fn_names(name))
            extra_set -= have
            tools += [t for t in self.fm.all_possible_tools
                      if t['function']['name'] in extra_set]

        # Apply mode filter + settings gate (both read-only) — same pair the
        # web path applies. Skipping the gate here exposed gated tools
        # (switch_model et al) to daemon runs even with their Settings > Tools
        # toggles OFF; execution was still refused in-handler, but the AI saw
        # a tool it could never use. Silent-default class.
        tools = self.fm._apply_settings_gate(self.fm._apply_mode_filter(tools))
        logger.info(f"[ExecCtx] Toolset '{toolset_name}': {len(tools)} tools")
        return tools if tools else None

    def _build_scopes(self) -> Optional[Dict]:
        """Build scopes from task settings. Sets ContextVars for this thread only.
        Always resets scopes to prevent bleed between queued task iterations."""
        from core.chat.function_manager import (
            apply_scopes_from_settings, reset_scopes, snapshot_all_scopes, SCOPE_REGISTRY,
        )

        # Always reset first — prevents scope bleed when queue drains multiple
        # iterations on the same thread (previous task's scopes would linger)
        reset_scopes()

        # NOTE: we do NOT short-circuit on `not self.tools` before the force-None
        # closure below. An empty-toolset agent still needs its scopes closed —
        # hook_runner fires during agent runs, plugin handlers read ContextVars,
        # event publishes read scope from the current thread. If the closure
        # is skipped, the thread's scopes stay at registry defaults (including
        # 'default' — a real scope with user data). Silent-default leak for
        # toolset-less agents. H2 fix 2026-04-22.
        #
        # Apply task-specific scopes to this thread's ContextVars.
        apply_scopes_from_settings(self.fm, self.task_settings)
        # Force-None every SCOPE_REGISTRY key NOT explicitly present in the
        # task's settings. This is the silent-default class closure.
        #
        # Background: `'default'` is doing double duty in this system — it's
        # both the registry default assigned at ContextVar registration time
        # AND a real scope name where user data lives. After `reset_scopes()`
        # every ContextVar sits at `'default'`. `apply_scopes_from_settings`
        # sets the ones the task listed, leaving the rest at `'default'` — a
        # real scope containing the user's memories, knowledge, people,
        # goals. An agent (or any task) running through ExecutionContext
        # without listing every registered scope would silently write into
        # that personal bucket.
        #
        # The previous narrower fix gated this force-None only on
        # `prompt == 'agent'`, but tasks can resolve to sapphire/rook/custom
        # personas (and spawn_agent(prompt='self') inherits non-agent
        # personas routinely). Three scouts converged on this exact gap.
        # Drop the gate — apply the stronger invariant universally. Any task
        # that doesn't EXPLICITLY list a scope key gets None (disabled) for
        # that scope, not the registry default. Silent-default closed.
        # Scout day-ruiner #1 / chaos #4 / #9 — 2026-04-21.
        for name, reg in list(SCOPE_REGISTRY.items()):
            setting_key = reg.get('setting')
            if setting_key and setting_key not in self.task_settings:
                # Bool-typed flags (e.g. scope_private) aren't scopes — their
                # registered default IS the disabled state (False). Force-None
                # on them sets a None value that breaks callers expecting bool.
                # Scopes are string-name ContextVars — those get None.
                default_val = reg.get('default')
                disabled_val = False if isinstance(default_val, bool) else None
                try:
                    reg['var'].set(disabled_val)
                except Exception as e:
                    logger.warning(f"[ExecCtx] Could not force-disable scope {name}: {e}")
        # Also clear rag since tasks don't use it. private_chat follows the
        # prompt: a privacy_required prompt keeps its network-tool gating even
        # in this lane (the old unconditional False actively STRIPPED privacy
        # from cron/heartbeat/agent runs).
        self.fm.set_rag_scope(None)
        self.fm.set_private_chat(getattr(self, '_prompt_privacy_required', False))

        # Provenance for tool executors (mindpalace metadata, get_self_info).
        # Set before the snapshot so it rides along. provider/model are the
        # RESOLVED values — the constructor already ran _resolve_provider,
        # including the event-payload override — so tools report what this
        # run actually executes on, not the task's pre-override fields.
        from core.chat.function_manager import set_tool_context
        set_tool_context(None, chat=self.task_settings.get('chat_target'),
                         persona=self.task_settings.get('prompt'),
                         model=(getattr(self, 'model_override', None)
                                or getattr(getattr(self, 'provider', None), 'model', '')
                                or self.task_settings.get('model')),
                         provider=getattr(self, 'provider_key', None),
                         toolset=self.task_settings.get('toolset'),
                         channel='continuity')

        return snapshot_all_scopes()

    def _resolve_provider(self) -> Tuple:
        """Select LLM provider from task settings. Returns (key, provider, model_override)."""
        provider_key = self.task_settings.get("provider", "auto")
        model_override = self.task_settings.get("model", "")

        # Daemon-event payloads may carry a per-event provider/model override
        # (e.g. the discord plugin's per-guild model routing) — payload wins
        # over task settings.
        from core.continuity.executor import current_event_data
        event = current_event_data.get() or {}
        ev_provider = str(event.get("llm_primary") or "").strip()
        if ev_provider and ev_provider != "auto":
            provider_key = ev_provider
            model_override = str(event.get("llm_model") or "").strip()

        providers_config = {**getattr(config, 'LLM_PROVIDERS', {}), **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
        requires_privacy = getattr(self, '_prompt_privacy_required', False)

        if provider_key and provider_key not in ("auto", ""):
            # Prompt-privacy gate: a privacy_required prompt never runs on a
            # non-local provider in the continuity lane (cron/heartbeat/
            # agents/daemons). Before 2026-08-09 this lane had NO privacy
            # check at all — a private persona on a cloud-pinned task shipped
            # its prompt straight out.
            if requires_privacy:
                from core.chat.llm_providers import PROVIDER_METADATA
                pconf = providers_config.get(provider_key, {})
                meta = PROVIDER_METADATA.get(provider_key, {})
                if not pconf.get('is_local', meta.get('is_local', False)):
                    raise ConnectionError(
                        f"Task requires privacy (private target chat, or prompt "
                        f"'{self.task_settings.get('prompt')}' is privacy_required); "
                        f"provider '{provider_key}' is not marked local — task refused.")
            provider = get_provider_by_key(
                provider_key, providers_config,
                config.LLM_REQUEST_TIMEOUT,
                model_override=model_override
            )
            if not provider:
                raise ConnectionError(f"Provider '{provider_key}' not available")
            return provider_key, provider, model_override

        # Auto mode — fallback order (force_privacy filters to local providers
        # when the prompt demands it)
        fallback_order = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config.keys()))
        result = get_first_available_provider(
            providers_config, fallback_order, config.LLM_REQUEST_TIMEOUT,
            force_privacy=requires_privacy
        )
        if result:
            pk, prov = result
            return pk, prov, model_override

        raise ConnectionError("No LLM providers available")

    def _build_gen_params(self) -> Dict:
        """Build generation parameters for the resolved provider/model."""
        effective_model = self.model_override if self.model_override else self.provider.model
        params = get_generation_params(
            self.provider_key, effective_model,
            {**getattr(config, 'LLM_PROVIDERS', {}), **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
        )
        if self.model_override:
            params['model'] = self.model_override
        return params

    # ── Execution ──

    def run(self, user_input: str, history_messages: List[Dict] = None,
            images: List[Dict] = None) -> str:
        """Run LLM + tool loop in complete isolation. Returns response text.

        After run() completes, self.new_messages contains all messages generated
        during this execution (user, assistant w/ tool_calls, tool results, final
        assistant). Callers can use this to persist the full conversation including
        tool calls — not just the final response.

        Args:
            user_input: The user/event message. When images are passed, this is
                        the PERSISTED form (text + <<IMG::...>> markers) — the
                        marker is what lands in chat history, never base64.
            history_messages: Optional prior messages for foreground chat continuity.
                              If None, runs ephemeral (system + user only).
            images: Optional plugin-supplied images [{"data": b64, "media_type": str}].
                    Shown to the LLM as base64 blocks THIS turn only, gated on
                    provider vision support; non-vision falls back to a CLIP vibe
                    description. new_messages always persists the marker form.
        """
        from core.chat.chat import filter_to_thinking_only, _inject_tool_images

        # Stamp this thread's ContextVar with the running task's persona so
        # tools invoked during the loop (spawn_agent, etc.) can inherit the
        # CORRECT parent. Reset on exit so nothing leaks back to the shared
        # main-chat path. Scout #7 — 2026-04-20.
        _persona_token = current_task_persona.set(self.task_settings.get("prompt"))
        try:
            return self._run_inner(user_input, history_messages,
                                   filter_to_thinking_only, _inject_tool_images,
                                   images)
        finally:
            current_task_persona.reset(_persona_token)

    def _build_user_message(self, user_input, images):
        """Build the user message content the LLM sees this turn.

        Returns (llm_content, persist_content). llm_content carries base64 image
        blocks for vision models (or vibe-augmented text otherwise); persist_content
        is always the marker-string `user_input` so chat history never stores base64.
        """
        if not images:
            return user_input, user_input
        from core.chat.chat_tool_calling import strip_ui_markers
        clean_text = strip_ui_markers(user_input) if "<<" in user_input else user_input
        if getattr(self.provider, "supports_images", False):
            content = [{"type": "text", "text": clean_text}]
            for img in images:
                content.append({
                    "type": "image",
                    "data": img.get("data", ""),
                    "media_type": img.get("media_type", "image/jpeg"),
                })
            return content, user_input
        # No vision: describe the image(s) so the model gets the vibe, not a drop.
        import base64
        vibe_parts = []
        for img in images:
            try:
                from core import vibes as _vibes
                vibe_parts.append(_vibes.describe(base64.b64decode(img["data"])))
            except Exception as e:
                logger.warning(f"[EVENT-IMG] vibe describe failed: {e}")
        augmented = user_input
        if vibe_parts:
            augmented = (user_input + "\n\n" + "\n\n".join(p for p in vibe_parts if p)).strip()
        # Persist the marker form (user_input), but the LLM this turn sees the
        # vibe-augmented text. Persisting the marker keeps replay clean.
        return augmented, user_input

    def _run_inner(self, user_input, history_messages, filter_to_thinking_only, _inject_tool_images,
                   images=None):
        # Build the user message. llm_content is what the model sees (may carry
        # base64 image blocks); persist_content is the marker form for history.
        llm_content, persist_content = self._build_user_message(user_input, images)
        # Build messages. Hold a reference to the user-message OBJECT so its index
        # can be re-derived by identity after the loop — the in-loop context trim
        # deletes front messages, which would make a frozen msg_start_idx stale and
        # silently drop the user turn from the persisted slice. 2026-06-13.
        user_msg = {"role": "user", "content": llm_content}
        if history_messages is not None:
            # Foreground mode — use existing chat history
            messages = [{"role": "system", "content": self.system_prompt}] + history_messages
            msg_start_idx = len(messages)  # initial position + fallback anchor
            messages.append(user_msg)
        else:
            # Ephemeral — no history
            messages = [
                {"role": "system", "content": self.system_prompt},
            ]
            msg_start_idx = len(messages)
            messages.append(user_msg)

        # The scheduler stores 0 as the "unset / use default" sentinel for
        # max_tool_rounds and max_parallel_tools (scheduler.py:320-321,
        # frontend never exposes these by default). Treat 0 as "fall through
        # to config default" — `or` does this naturally for both None and 0.
        # Then max(1, ...) defends against negative or otherwise-falsy
        # surprises. Without this, a heartbeat task created without explicit
        # rounds was capped to 1 iteration: LLM uses it on a tool call, never
        # gets to respond, loop exhausts silently. 2026-05-10 bug.
        # Chaos scout #5/#10 (2026-04-20) protected against literal-0 cap-out
        # but mistook the scheduler's 0-default as a real value — preserved
        # the safety here by falling through to config defaults instead.
        # context_limit IS different: 0 there legitimately means "unlimited"
        # and the downstream `if context_limit > 0` check expects that.
        _rounds = self.task_settings.get("max_tool_rounds") or config.MAX_TOOL_ITERATIONS
        max_iterations = max(1, _rounds)
        _parallel = self.task_settings.get("max_parallel_tools") or config.MAX_PARALLEL_TOOLS
        max_parallel = max(1, _parallel)
        _ctx = self.task_settings.get("context_limit")
        context_limit = _ctx if _ctx is not None else getattr(config, 'CONTEXT_LIMIT', 0)

        # Tool schemas are part of the actual API payload, so they MUST be
        # included in the budget. Without this, a task with 158 tools loaded
        # blows past the provider context cap even when message-content trim
        # claims things are fine. Reported in the wild 2026-05-05 — a heartbeat
        # with a heavy toolset hit this and produced an empty bubble. Trim of
        # message content can't free schema bytes; the user has to either
        # reduce the toolset or raise context_limit. Surfacing both numbers
        # in the error message tells them which lever to pull.
        from core.chat.history import count_tokens
        tool_schema_tokens = 0
        if self.tools:
            try:
                import json as _json
                tool_schema_tokens = sum(
                    count_tokens(_json.dumps(t, ensure_ascii=False))
                    for t in self.tools
                )
            except Exception:
                # Rough fallback if a tool schema isn't JSON-serializable
                tool_schema_tokens = len(self.tools) * 100

        logger.info(f"[ExecCtx] Running: provider='{self.provider_key}', "
                     f"tools={len(self.tools) if self.tools else 0} "
                     f"(~{tool_schema_tokens} schema tokens), "
                     f"history={len(history_messages) if history_messages else 0} msgs")
        final_content = None
        llm_retries = 0  # one rescue decode per run — shared by both garbage shapes below

        overflow_reason = None
        loop_counts = {}  # per-turn per-tool call counts (loop guard); turn-local by design
        for i in range(max_iterations):
            # Context limit check — auto-trim oldest messages rather than bail.
            # Previously this break fired before we ever called the LLM whenever
            # loaded history was already >90% of the task's context_limit,
            # producing a silent "(No response — tool loop exhausted)" placeholder.
            # Now we aggressively trim the oldest non-system messages, clean up
            # orphaned tool-result heads, retry under 80% of limit, and only
            # give up with a specific reason if trim can't help.
            if context_limit > 0:
                msg_tokens = sum(count_tokens(str(m.get("content", ""))) for m in messages)
                total_tokens = msg_tokens + tool_schema_tokens
                if total_tokens > context_limit * 0.9:
                    sys_idx = 1 if messages and messages[0].get("role") == "system" else 0
                    non_system = len(messages) - sys_idx
                    if non_system > 4:
                        drop = max(1, non_system // 4)
                        # Trim is for OLD history ONLY — never delete the current
                        # turn's user message or anything after it. On a short/empty
                        # chat user_msg sits near the front and the raw drop range
                        # would delete it, which loses the LLM's actual prompt AND
                        # reopens the stale-anchor bug via the identity fallback
                        # (next(...) misses → falls back to a stale msg_start_idx).
                        # Clamp so the delete never reaches user_msg. 2026-06-13.
                        protect_idx = next((i for i, m in enumerate(messages) if m is user_msg), len(messages))
                        drop = min(drop, max(0, protect_idx - sys_idx))
                        if drop > 0:
                            del messages[sys_idx:sys_idx + drop]
                            # Strip any orphaned tool-result messages now at the front
                            while len(messages) > sys_idx and messages[sys_idx].get("role") == "tool":
                                messages.pop(sys_idx)
                            # Also strip an assistant that had tool_calls whose results
                            # just got dropped (would become orphan at LLM call time)
                            if len(messages) > sys_idx and messages[sys_idx].get("role") == "assistant" and messages[sys_idx].get("tool_calls"):
                                messages.pop(sys_idx)
                            new_msg_tokens = sum(count_tokens(str(m.get("content", ""))) for m in messages)
                            new_total = new_msg_tokens + tool_schema_tokens
                            logger.warning(
                                f"[ExecCtx] Context trim: dropped ~{drop} oldest msgs "
                                f"({total_tokens} → {new_total} tokens, "
                                f"limit {context_limit}; tool schemas {tool_schema_tokens})"
                            )
                            total_tokens = new_total
                            msg_tokens = new_msg_tokens
                    if total_tokens > context_limit * 0.9:
                        # Trim couldn't rescue this turn — give a specific reason
                        # that points at the actual lever to pull. With heavy
                        # toolsets, schemas often exceed the limit on their own
                        # and clearing chat history won't help.
                        n_tools = len(self.tools) if self.tools else 0
                        if tool_schema_tokens > context_limit * 0.7:
                            advice = (
                                f"Toolset is too large for this context_limit "
                                f"({n_tools} tools = {tool_schema_tokens} schema tokens). "
                                f"Reduce toolset size or raise context_limit."
                            )
                        else:
                            advice = (
                                f"Clear older chat history or raise context_limit."
                            )
                        overflow_reason = (
                            f"(Context overflow — {total_tokens}/{context_limit} tokens "
                            f"({msg_tokens} messages + {tool_schema_tokens} tool schemas "
                            f"from {n_tools} tools). {advice})"
                        )
                        logger.error(f"[ExecCtx] {overflow_reason}")
                        break

            response_msg = self.tool_engine.call_llm_with_metrics(
                self.provider, messages, self.gen_params, tools=self.tools
            )

            if response_msg.has_tool_calls:
                filtered = filter_to_thinking_only(response_msg.content or "")
                tool_calls = response_msg.get_tool_calls_as_dicts()[:max_parallel]
                # Carry `thinking` for DeepSeek-reasoner round-trip on next
                # iteration; harmless for other providers. 2026-05-14.
                _thinking = getattr(response_msg, "thinking", None)
                messages.append({
                    "role": "assistant", "content": filtered,
                    "tool_calls": tool_calls,
                    "thinking": _thinking,
                })
                self.tool_log.extend(tc.get('function', {}).get('name', '?') for tc in tool_calls)
                # Cap at source: a runaway agent can append thousands. The
                # poll-payload cap in BaseWorker.to_dict is a safety net; this
                # is the actual bound. Keep the last 500. Scout longevity #3.
                if len(self.tool_log) > 500:
                    del self.tool_log[:-500]
                tools_executed, tool_images = _exec_with_loop_counts(
                    self.tool_engine.execute_tool_calls,
                    tool_calls, messages, None, self.provider, scopes=self.scopes,
                    allowed_tools=self._allowed_tool_names, loop_counts=loop_counts
                )
                if tool_images:
                    _inject_tool_images(messages, tool_images, self.provider)
                logger.info(f"[ExecCtx] Loop {i+1}: {tools_executed} tools executed")
                # If the LLM requested tool calls but NONE executed (hallucinated
                # tool names filtered out by allowed_tools, every call rejected),
                # continuing just invites the LLM to re-request the same ghosts
                # forever. Break with degraded_reason so the caller sees amber.
                # Scout chaos #6.
                if tools_executed == 0:
                    self.degraded_reason = (
                        f"LLM requested {len(tool_calls)} tool call(s) but none "
                        f"executed (likely hallucinated names not in toolset). "
                        f"Breaking loop to avoid infinite retry."
                    )
                    logger.warning(f"[ExecCtx] {self.degraded_reason}")
                    break
                continue

            elif response_msg.content:
                fn_data = self.tool_engine.extract_function_call_from_text(response_msg.content)
                if fn_data:
                    self.tool_log.append(fn_data.get('name', '?'))
                    if len(self.tool_log) > 500:
                        del self.tool_log[:-500]
                    filtered = filter_to_thinking_only(response_msg.content)
                    _, tool_images = _exec_with_loop_counts(
                        self.tool_engine.execute_text_based_tool_call,
                        fn_data, filtered, messages, None, self.provider, scopes=self.scopes,
                        allowed_tools=self._allowed_tool_names, loop_counts=loop_counts
                    )
                    if tool_images:
                        _inject_tool_images(messages, tool_images, self.provider)
                    continue

                # A tool task whose reply hit max_tokens WITHOUT a tool call is
                # a failed generation, not an answer (librarian b12 2026-07-19:
                # 35K chars of looping thinking, finish=length, the call never
                # came — one more sentence of budget and it would have landed).
                # Retry once with identical messages: nothing is appended, so
                # the garbage never poisons the fresh decode. A second length-
                # death keeps the text (the chat is the audit surface) but
                # flags degraded so workers don't count the message as ok.
                # Provider dialects for "hit the token ceiling": openai-compat
                # says 'length', Claude/anthropic-compat pass raw 'max_tokens',
                # openai_responses says 'incomplete' (fixer-scout, 2026-07-19 —
                # matching only 'length' left the b12 class live elsewhere).
                _finish = getattr(response_msg, 'finish_reason', None)
                if _finish in ('length', 'max_tokens', 'incomplete') and self.tools:
                    if llm_retries < 1 and i + 1 < max_iterations:
                        # (last-iteration length-deaths skip the retry — the
                        # continue would exit the loop and DROP the text the
                        # audit contract promises to keep.)
                        llm_retries += 1
                        logger.warning(
                            f"[ExecCtx] finish={_finish} with no tool call "
                            f"({len(response_msg.content)} chars) — retrying "
                            f"turn once with a fresh decode.")
                        continue
                    self.degraded_reason = (
                        "LLM hit max_tokens without completing a tool call "
                        "(truncated text kept for audit)."
                    )
                    logger.warning(f"[ExecCtx] {self.degraded_reason}")

                final_content = response_msg.content
                messages.append({"role": "assistant", "content": final_content})
                break
            else:
                # Provider returned no content AND no tool_calls. This is
                # rare but happens with some smaller / quantized models or
                # when a provider trims to fit its own input cap and has
                # no budget left for output. Same rescue as the length-death
                # above: empty is never a legitimate answer, so spend the
                # run's one retry before breaking degraded. Scout 2 #3.
                if llm_retries < 1 and i + 1 < max_iterations:
                    llm_retries += 1
                    logger.warning(
                        "[ExecCtx] LLM returned empty content with no tool "
                        "calls — retrying turn once.")
                    continue
                self.degraded_reason = (
                    "LLM returned empty content with no tool calls "
                    "(provider may have hit its own input cap or model "
                    "produced no output)."
                )
                logger.warning(f"[ExecCtx] {self.degraded_reason}")
                break

        # Before synthesizing a final placeholder, inject tool-result placeholders
        # for any dangling assistant(tool_calls) that never got their responses
        # (loop exhausted mid-round, worker cancelled, tools_executed==0 break).
        # Without this, the persisted sequence becomes asst(tc) → asst(text) with
        # no tool-role messages in between — OpenAI/Claude reject that structure
        # on the NEXT user turn and the chat is wedged. Placeholder content is
        # provider-agnostic (plain string, no special formatting). Scout chaos #12.
        def _patch_dangling_tool_calls():
            # Walk from end backward to find asst messages with tool_calls and
            # check whether their tool responses are present below them.
            for idx in range(len(messages) - 1, -1, -1):
                m = messages[idx]
                if m.get("role") != "assistant":
                    continue
                tcs = m.get("tool_calls") or []
                if not tcs:
                    break  # most-recent asst has no tool_calls — we're clean
                # Defensive: a misbehaving provider adapter or hand-edited
                # history could produce non-list tool_calls (dict, str). The
                # set-comprehension below would iterate a string as chars and
                # crash on `.get`, killing the whole task. Skip with a warning.
                # Witch-hunt 2026-04-21 finding H11.
                if not isinstance(tcs, list):
                    logger.warning(
                        f"[ExecCtx] _patch_dangling_tool_calls: msg[{idx}] tool_calls is "
                        f"{type(tcs).__name__}, not list — skipping patch"
                    )
                    break
                expected = {tc.get("id") for tc in tcs if isinstance(tc, dict) and tc.get("id")}
                got = set()
                insert_after = idx
                for j in range(idx + 1, len(messages)):
                    nxt = messages[j]
                    if nxt.get("role") == "tool" and nxt.get("tool_call_id") in expected:
                        got.add(nxt["tool_call_id"])
                        insert_after = j
                    elif nxt.get("role") == "assistant":
                        break  # new assistant turn — stop scanning
                missing = expected - got
                if missing:
                    # Insert placeholders right after the last real tool response
                    # (or right after the asst if there are none), preserving order.
                    pos = insert_after + 1
                    # Map tool_call_id -> function name from the asst(tc) so the
                    # placeholder carries `name`. history.get_messages_for_llm
                    # used to do msg["name"] (KeyError → swallowed → empty
                    # history return). Reader is fixed too, but writing the
                    # field is the right thing — keeps persisted history
                    # well-formed for any other consumer. 2026-05-10.
                    name_by_id = {}
                    for tc in tcs:
                        if isinstance(tc, dict):
                            tc_id = tc.get("id")
                            fn_name = (tc.get("function") or {}).get("name", "tool")
                            if tc_id:
                                name_by_id[tc_id] = fn_name
                    for tc_id in missing:
                        messages.insert(pos, {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": name_by_id.get(tc_id, "tool"),
                            "content": "(Tool did not execute — loop exhausted before resolution.)",
                        })
                        pos += 1
                break  # only patch the most recent asst(tc) — older ones already closed
        _patch_dangling_tool_calls()

        # If we exhausted iterations, try to extract last content
        if final_content is None and messages:
            last = messages[-1]
            if last.get("role") == "assistant" and last.get("content"):
                final_content = last["content"]
            else:
                # Diagnostic text MUST stay in degraded_reason for UI badges /
                # logs only — it must NEVER end up as final_content because
                # final_content becomes TTS speech, Discord channel posts,
                # Telegram replies, email bodies. Sapphire saying "(No response
                # — tool loop exhausted or LLM returned empty)" out loud was
                # the 2026-04-24 Evening-mode incident; the same string going
                # to Discord channels was the local-Sapph variant. Both close
                # by keeping final_content empty here so caller truthy-checks
                # (`if response:`) drop the output cleanly. Scout #15 / Krem
                # 2026-04-24.
                if overflow_reason:
                    self.degraded_reason = overflow_reason
                else:
                    self.degraded_reason = (
                        f"Tool loop exhausted after {max_iterations} rounds without "
                        f"a final reply. Tools called: {', '.join(self.tool_log) or '(none)'}."
                    )
                final_content = ""
                # Empty assistant content keeps the chat-history pair intact
                # (closes the orphaned-user-turn hazard the original synth was
                # written to address) without putting engineering text in front
                # of users.
                messages.append({"role": "assistant", "content": ""})

        # ANCHOR BY IDENTITY: re-derive the user message's CURRENT index — the
        # in-loop context trim may have deleted messages before it, shifting it
        # down. A frozen index would point past the real turn and drop it from
        # the persisted slice. Derive ONCE here: the swap below replaces the
        # object (so it can't be re-found afterward), but the swap doesn't change
        # list length, so cur_idx stays valid for the slice. Multiple trims are
        # handled for free — this reflects the final position. 2026-06-13.
        cur_idx = next((i for i, m in enumerate(messages) if m is user_msg), msg_start_idx)

        # DUAL-FORM SWAP: the LLM saw the image as base64 across this turn's
        # rounds, but history must persist the MARKER form (text + <<IMG::...>>),
        # never inline base64 — otherwise every replay re-sends the image and
        # bloats the chat. Swap the anchored user message's content to the marker
        # form. No-op when no images were passed (persist_content == llm_content).
        if images and len(messages) > cur_idx:
            _um = messages[cur_idx]
            if _um.get("role") == "user":
                messages[cur_idx] = {**_um, "content": persist_content}

        # Expose messages generated during this run. Scrub inline base64 image
        # blocks (appended mid-loop by _inject_tool_images for tool-returned
        # images) down to text — those images are referenced by their own
        # <<IMG::tool:id>> markers in the tool-result messages, so the base64 is
        # turn-only and must not persist (same replay-bloat hazard the swap
        # guards for event images). 2026-06-13. Only include if we got a response —
        # an orphaned user message with no assistant reply corrupts chat history.
        new = [_scrub_inline_images(m) for m in messages[cur_idx:]]
        has_assistant = any(m.get("role") == "assistant" for m in new)
        self.new_messages = new if has_assistant else []

        return final_content or ""
