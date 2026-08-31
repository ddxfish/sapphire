# core/hooks.py — Plugin hook system for Sapphire
#
# Priority-ordered hook runner with mutable event objects.
# Plugins register handlers for named hooks; the runner fires them
# in priority order with error isolation.

import re
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HookEvent:
    """Mutable event object passed through hook handlers.

    Handlers can mutate any field. Changes are visible to subsequent handlers.

    Hook points (in pipeline order):
        post_stt:          After voice transcription — mutate `input` to correct STT
        on_wake:           Wakeword detected — notification only (must return fast)
        pre_chat:          Before LLM — mutate `input`, set `skip_llm`/`response` to bypass
        prompt_inject:     During prompt build — append to `context_parts` (system prompt)
        ghost_inject:      Per-turn ephemeral content — set `ghost_text` to inject a
                           non-persisted operator-metadata note into THIS turn's LLM
                           call only. Goes through the ghost-message rail (a labeled
                           user-role message just before the new user input), never
                           saved to chat history, attributed to the contributing
                           plugin in the envelope so the assistant can see who's
                           talking. Use for time-sensitive context (weather, calendar,
                           ambient state). Subject to elevated store review — this
                           hook can shape replies invisibly to the user, so plugins
                           must declare WHAT they inject and WHY in their manifest.
                           Plugins that fingerprint user content and inject opinion-
                           shaping text get rejected (Vanta-shape). 2026-05-08.
        post_llm:          After LLM response, before save — mutate `response` to filter/translate
        post_chat:         After response saved — observational (`input`, `response`)
        pre_execute:       Before tool call — mutate `arguments`, block with `skip_llm`
        post_execute:      After tool call — observational (`function_name`, `result`)
        tools_filter:      Per-request tool schema — fired after toolset
                           resolution with the FINAL tool list for this turn's
                           LLM call in `tools`; remove entries to withhold
                           tools from this turn (e.g. the game-room's
                           per-scenario fence). SUBTRACT-ONLY: the fire site
                           intersects the result against the original list,
                           so additions never land. Errors fail open (the
                           unfiltered list ships). `chat_name` is pre-stamped
                           by the fire site. 2026-08-24.
        pre_tts:           Before speech — mutate `tts_text`, cancel with `skip_tts`. metadata['tts_client'] = calling TTSClient
        post_tts:          After playback — observational (`tts_text`, metadata has `duration`)
        provider_switched: After TTS/STT/embed provider hot-swap. metadata: `kind` (tts|stt|embed), `provider` (new key). Observational — plugins warm caches / reset state.
        plugins_ready:     After a registration wave settles. Fires on FOUR legs:
                           the boot scan, reload_plugin, rescan (per newly loaded
                           plugin), and toggle-ON — handlers must be idempotent
                           and must re-check their plugin is still loaded (a
                           handler thread can outlive a toggle-OFF). The "after
                           my own registration" moment. 2026-08-05 / 2026-08-17.
        chat_renamed:      A chat was renamed. metadata: `old`, `new`. Any plugin
                           keying data by chat name MUST carry it across, or that
                           data strands and a later chat recycling the old name
                           inherits a ghost. Fires after the rename succeeds.
        chat_deleted:      A chat was deleted. metadata: `name`. NOTE (v1.3):
                           core already deleted the chat's plugin_chat_data rows
                           before this fires — PluginChatState reads here return
                           nothing and archive-on-delete is not possible from
                           this hook. Use it for state kept OUTSIDE the chat DB
                           (files, caches, registries). Fires after the delete
                           succeeds, on EVERY path (single, bulk, manage).
        chat_vaulted:      A chat was just encrypted into the vault (marked
                           private). metadata: `name`. Plugins holding plaintext
                           deposits keyed by or containing this chat's name
                           scrub them here (full-scrub ruling F5, 2026-08-15).
                           Delivered ONLY to privacy_aware plugins — the event
                           itself says "this name is private".

    Fields:
        input: User's message / STT transcription (mutable in post_stt, pre_chat)
        skip_llm: Set True to bypass LLM entirely (voice commands, cached responses)
        response: Direct response text when skip_llm is True / post_chat final response
        context_parts: Append strings to inject into system prompt (prompt_inject hooks)
        ghost_text: Set in `ghost_inject` to contribute a per-turn ephemeral note.
                    Goes into the ghost message envelope, never persisted, attributed
                    by `ghost_label` so the assistant sees which plugin spoke.
        ghost_label: Plugin name for ghost attribution. Auto-set by the runner from
                     the registering plugin's manifest. Plugins should not override.
        stop_propagation: Set True to prevent lower-priority hooks from firing
        config: System config object (read-only by convention)
        metadata: Arbitrary data — may include 'system' (VoiceChatSystem instance)
        function_name: Tool name for pre_execute/post_execute hooks
        arguments: Tool arguments for pre_execute (mutable — plugins can modify)
        result: Tool result for post_execute
        tts_text: Text about to be spoken for pre_tts (mutable) / spoken text for post_tts
        skip_tts: Set True in pre_tts to cancel TTS entirely
        ephemeral: Set True with skip_llm to show response without persisting to history
        chat_name: THIS turn's effective chat (stamped by the runner via the
                   privacy resolver; fire sites may pre-stamp). None until resolved.
        chat_private: True when that chat is private (vaulted chats Phase 3).
                   None = not yet resolved — the runner resolves it once per fire.
                   Private turns are delivered ONLY to plugins that declare
                   `"privacy_aware": true` in their manifest (ruling F2: core
                   withholds; a plugin with no privacy signal must not receive
                   private-chat content it might persist or transmit).
    """
    input: str = ""
    skip_llm: bool = False
    response: Optional[str] = None
    context_parts: List[str] = field(default_factory=list)
    # Ghost-injection rail (set by handlers in `ghost_inject` hook). Plugins
    # set `ghost_text` to contribute. The runner collects (label, text) pairs
    # in `ghost_contributions` so build_ghost_message can attribute each line
    # to the originating plugin in the envelope. Plugins should NOT modify
    # ghost_label or ghost_contributions — those are runner-managed.
    ghost_text: Optional[str] = None
    ghost_label: str = ""
    ghost_contributions: List[tuple] = field(default_factory=list)
    stop_propagation: bool = False
    config: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    function_name: Optional[str] = None
    arguments: Optional[dict] = None
    result: Optional[str] = None
    # tools_filter rail: the request's tool-schema list. Handlers REMOVE
    # entries; the fire site enforces subtract-only via intersection.
    tools: Optional[List[Dict]] = None
    tts_text: Optional[str] = None
    skip_tts: bool = False
    ephemeral: bool = False
    chat_name: Optional[str] = None
    chat_private: Optional[bool] = None
    # Surface this turn's chat is on ('chat', 'game'…) — stamped by the fire
    # site from the chat's `surface` setting. Presence hooks (prompt_inject /
    # ghost_inject) are withheld from plugins whose surfaces exclude it.
    # None = unknown → deliver to all (plugin surfaces, 2026-08-23).
    surface: Optional[str] = None


# The surfaces a chat can be shown on. A plugin's manifest `surfaces` list
# (top-level, like privacy_aware) names where its PRESENCE injections
# belong — an avatar that only renders in the chat view has no business
# telling her she's visible in a story. Absent = everywhere. The user
# overrides per plugin from the Plugins page (plugin-state `surfaces`).
SURFACES = (("chat", "Chat"), ("game", "Game Room"))
SURFACE_HOOKS = frozenset({"prompt_inject", "ghost_inject"})


class HookRunner:
    """Priority-ordered hook dispatcher with error isolation.

    Priority bands:
        System plugins (0-99): Always fire first
        User plugins (100-199): Fire after system plugins
        Default priority: 50

    Guidelines within each band:
        0-19:  Critical intercepts (stop, security)
        20-49: Input modification (translation, formatting)
        50-79: Context enrichment (prompt injection, state)
        80-99: Observation (logging, analytics)
    """

    # Hooks that deliver regardless of chat privacy (vaulted chats ruling F2
    # carve-out): rename/delete are HOUSEKEEPING — withholding them strands a
    # private chat's plugin-side data under a stale name, creating the exact
    # plaintext orphan the gate exists to prevent. The others carry no chat
    # content and no chat identity.
    ALWAYS_DELIVER = frozenset({
        "chat_renamed", "chat_deleted", "chat_cleared", "plugins_ready",
        "provider_switched", "on_wake",
    })

    def __init__(self):
        # {hook_name: [(priority, handler, plugin_name, voice_match)]}
        self._hooks: Dict[str, List[tuple]] = {}
        self._sorted: Dict[str, bool] = {}
        self._lock = threading.Lock()
        # Vaulted chats Phase 3 (ruling F2): core installs a resolver at boot
        # returning (effective_chat_name, is_private) for the current turn;
        # plugins whose manifest declares privacy_aware register here.
        self._privacy_resolver: Optional[Callable] = None
        self._privacy_aware: set = set()
        # {plugin_name: frozenset(surfaces)} — only plugins that declared
        # (or were overridden) appear here; absent = everywhere.
        self._surfaces: Dict[str, frozenset] = {}

    def set_surfaces(self, plugin_name: str, surfaces):
        """Record where a plugin's presence hooks belong (effective list:
        user override if set, else the manifest default). None/non-list
        clears the restriction."""
        if isinstance(surfaces, (list, tuple, set, frozenset)):
            self._surfaces[plugin_name] = frozenset(str(s) for s in surfaces)
        else:
            self._surfaces.pop(plugin_name, None)

    def surfaces_of(self, plugin_name: str):
        s = self._surfaces.get(plugin_name)
        return sorted(s) if s is not None else None

    def set_privacy_resolver(self, resolver: Optional[Callable]):
        """Install the (chat_name, is_private) resolver. None uninstalls
        (tests / pre-boot): with no resolver, turns resolve as public."""
        self._privacy_resolver = resolver

    def mark_privacy_aware(self, plugin_name: str, aware: bool = True):
        """Record a plugin's manifest-declared privacy awareness. Only
        privacy_aware plugins receive hooks for private-chat turns."""
        if aware:
            self._privacy_aware.add(plugin_name)
        else:
            self._privacy_aware.discard(plugin_name)

    def register(self, hook_name: str, handler: Callable, priority: int = 50,
                 plugin_name: str = "", voice_match: dict = None):
        """Register a handler for a hook point.

        Args:
            hook_name: Hook to register for (pre_chat, prompt_inject, etc.)
            handler: Callable that takes a HookEvent
            priority: 0-199, lower fires first
            plugin_name: For logging and unregistration
            voice_match: Optional dict with 'triggers' list and 'match' type
                         for voice command pre-filtering (exact/starts_with/contains/regex)
        """
        with self._lock:
            if hook_name not in self._hooks:
                self._hooks[hook_name] = []
            self._hooks[hook_name].append((priority, handler, plugin_name, voice_match))
            self._sorted[hook_name] = False
        logger.debug(f"[HOOKS] Registered {plugin_name}:{handler.__name__} on '{hook_name}' (priority {priority})")

    def unregister(self, hook_name: str, plugin_name: str):
        """Remove all handlers for a plugin from a specific hook."""
        with self._lock:
            if hook_name in self._hooks:
                before = len(self._hooks[hook_name])
                self._hooks[hook_name] = [
                    h for h in self._hooks[hook_name] if h[2] != plugin_name
                ]
                removed = before - len(self._hooks[hook_name])
                if removed:
                    self._sorted[hook_name] = False
                    logger.info(f"[HOOKS] Unregistered {removed} handler(s) for '{plugin_name}' from '{hook_name}'")

    def unregister_plugin(self, plugin_name: str):
        """Remove all handlers for a plugin from all hooks."""
        for hook_name in list(self._hooks.keys()):
            self.unregister(hook_name, plugin_name)
        self._privacy_aware.discard(plugin_name)
        self._surfaces.pop(plugin_name, None)

    def _ensure_sorted(self, hook_name: str):
        if not self._sorted.get(hook_name, True):
            self._hooks[hook_name].sort(key=lambda h: h[0])
            self._sorted[hook_name] = True

    def _check_voice_match(self, voice_match: dict, input_text: str) -> bool:
        """Check if input matches voice command triggers.

        Returns True if no voice_match (regular hook, always fires)
        or if input matches the declared pattern.
        """
        if not voice_match:
            return True

        triggers = voice_match.get("triggers", [])
        match_type = voice_match.get("match", "exact")
        input_lower = input_text.lower().strip()

        if match_type == "exact":
            return input_lower in [t.lower() for t in triggers]
        elif match_type == "starts_with":
            return any(input_lower.startswith(t.lower()) for t in triggers)
        elif match_type == "contains":
            return any(t.lower() in input_lower for t in triggers)
        elif match_type == "regex":
            return any(re.search(t, input_text, re.IGNORECASE) for t in triggers)

        return False

    def fire(self, hook_name: str, event: HookEvent) -> HookEvent:
        """Fire all handlers for a hook in priority order.

        Each handler receives the mutable event object. Errors in individual
        handlers are logged and skipped — a buggy plugin never crashes the pipeline.

        Args:
            hook_name: Which hook to fire (pre_chat, prompt_inject, etc.)
            event: Mutable event object

        Returns:
            The (possibly mutated) event object
        """
        with self._lock:
            handlers = self._hooks.get(hook_name)
            if not handlers:
                return event
            self._ensure_sorted(hook_name)
            snapshot = list(handlers)

        is_ghost_hook = (hook_name == "ghost_inject")

        # Resolve this turn's chat identity once per fire (ruling F2). A fire
        # site may pre-stamp chat_private (e.g. chat_vaulted = True by
        # definition); ALWAYS_DELIVER hooks skip resolution entirely; a
        # resolver failure fails CLOSED (treat as private).
        if event.chat_private is None:
            if hook_name in self.ALWAYS_DELIVER or self._privacy_resolver is None:
                event.chat_private = False
            else:
                try:
                    if event.chat_name is not None:
                        # Pre-stamped identity (stream-brain override lane —
                        # the override contextvar resets across Starlette
                        # generator yields, so fire sites stamp the name from
                        # a local). Resolve privacy FOR THAT chat: pre-fix the
                        # resolver overwrote the stamp with the ACTIVE chat's
                        # name+privacy at the one seam built to distinguish
                        # them (S1 #7 / wave-3 B2, 2026-08-31). A resolver
                        # without per-name support raises here -> fail closed.
                        _rn, event.chat_private = self._privacy_resolver(event.chat_name)
                    else:
                        event.chat_name, event.chat_private = self._privacy_resolver()
                except Exception:
                    event.chat_private = True

        withhold = bool(event.chat_private) and hook_name not in self.ALWAYS_DELIVER
        surface = event.surface if hook_name in SURFACE_HOOKS else None

        for priority, handler, plugin_name, voice_match in snapshot:
            if withhold and plugin_name not in self._privacy_aware:
                logger.debug(f"[HOOKS] '{hook_name}' withheld from {plugin_name} "
                             f"(private chat, plugin not privacy_aware)")
                continue
            if surface is not None:
                allowed = self._surfaces.get(plugin_name)
                if allowed is not None and surface not in allowed:
                    logger.debug(f"[HOOKS] '{hook_name}' withheld from {plugin_name} "
                                 f"(surface '{surface}' not in {sorted(allowed)})")
                    continue
            if not self._check_voice_match(voice_match, event.input):
                continue

            # For ghost_inject: stamp the label, clear any previous handler's
            # text, so each contribution is attributed to the right plugin.
            # Plugins set `event.ghost_text = "..."` to contribute; the runner
            # captures it after the handler returns and resets for the next
            # plugin. Attribution lives in `ghost_contributions`. 2026-05-08.
            if is_ghost_hook:
                event.ghost_label = plugin_name
                event.ghost_text = None

            try:
                handler(event)
            except Exception as e:
                logger.error(
                    f"[HOOKS] Error in {plugin_name}:{handler.__name__} on '{hook_name}': {e}",
                    exc_info=True
                )
                continue

            if is_ghost_hook and event.ghost_text:
                event.ghost_contributions.append((plugin_name, event.ghost_text))

            if event.stop_propagation:
                logger.info(f"[HOOKS] Propagation stopped by {plugin_name} on '{hook_name}'")
                break

        # Clear scratch fields after ghost dispatch so accidental reads of
        # ghost_text/ghost_label after fire() don't carry the last plugin's
        # state. ghost_contributions is the canonical output.
        if is_ghost_hook:
            event.ghost_text = None
            event.ghost_label = ""

        return event

    def get_handlers(self, hook_name: str) -> list:
        """Get registered handlers for a hook (for debugging/introspection)."""
        with self._lock:
            self._ensure_sorted(hook_name)
            return list(self._hooks.get(hook_name, []))

    def has_handlers(self, hook_name: str) -> bool:
        """Check if any handlers are registered for a hook."""
        return bool(self._hooks.get(hook_name))

    def clear(self):
        """Remove all handlers. Used for testing."""
        with self._lock:
            self._hooks.clear()
            self._sorted.clear()
            self._privacy_resolver = None
            self._privacy_aware.clear()
            self._surfaces.clear()


# Singleton
hook_runner = HookRunner()
