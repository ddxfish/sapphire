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
        pre_tts:           Before speech — mutate `tts_text`, cancel with `skip_tts`. metadata['tts_client'] = calling TTSClient
        post_tts:          After playback — observational (`tts_text`, metadata has `duration`)
        provider_switched: After TTS/STT/embed provider hot-swap. metadata: `kind` (tts|stt|embed), `provider` (new key). Observational — plugins warm caches / reset state.
        plugins_ready:     Once per plugin scan, after ALL enabled plugins have
                           registered (tools, packs, games). The "after my own
                           registration" moment — e.g. re-merging runtime-rendered
                           prompt-pack entries so they resolve at boot. 2026-08-05.
        chat_renamed:      A chat was renamed. metadata: `old`, `new`. Any plugin
                           keying data by chat name MUST carry it across, or that
                           data strands and a later chat recycling the old name
                           inherits a ghost. Fires after the rename succeeds.
        chat_deleted:      A chat was deleted. metadata: `name`. Plugins drop the
                           chat's per-chat state here — otherwise recreating the
                           name resurrects it. Fires after the delete succeeds,
                           on EVERY path (single, bulk, manage). 2026-08-05.

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
    tts_text: Optional[str] = None
    skip_tts: bool = False
    ephemeral: bool = False


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

    def __init__(self):
        # {hook_name: [(priority, handler, plugin_name, voice_match)]}
        self._hooks: Dict[str, List[tuple]] = {}
        self._sorted: Dict[str, bool] = {}
        self._lock = threading.Lock()

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

        for priority, handler, plugin_name, voice_match in snapshot:
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


# Singleton
hook_runner = HookRunner()
