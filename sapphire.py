# sapphire.py - Sapphire Voice Assistant Core Application
import os
import sys
import time
import signal
import threading
import subprocess
from pathlib import Path

# Windows: Set event loop policy before ANY asyncio usage (imports like FastAPI trigger it)
if sys.platform == 'win32':
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Quiet-egress env (negspace hunt 2026-08-31): tiktoken caches its BPE
# file in the system temp dir — tmpfs on many boxes, so it re-downloaded
# from openaipublic.blob.core.windows.net every reboot. Point it at a
# persistent user/ dir (tiktoken mkdirs it). HF telemetry UA is pure
# phone-home; off unconditionally. setdefault — a user's own env wins.
os.environ.setdefault("TIKTOKEN_CACHE_DIR",
                      str(Path(__file__).parent / "user" / "cache" / "tiktoken"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# CRITICAL: Import logging setup FIRST before any core modules
import core.sapphire_logging
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Restart signaling
_restart_requested = False
_shutdown_requested = False

def request_restart():
    """Signal that a restart has been requested."""
    global _restart_requested
    _restart_requested = True
    logger.info("Restart requested - will exit with code 42")

def request_shutdown():
    """Signal that a clean shutdown has been requested."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown requested")

# Bootstrap user files before any modules try to load them
from core.setup import ensure_prompt_files, ensure_chat_defaults
ensure_prompt_files()
ensure_chat_defaults()

# Run data migrations (e.g. persona -> character rename)
from core.migration import run_all as run_migrations
run_migrations()

# Wrap all further imports to catch errors
try:
    from core.stt import AudioRecorder as WhisperRecorder
    from core.stt.stt_null import NullWhisperClient
    from core.stt.providers import get_stt_provider
    from core.chat import LLMChat, ConversationHistory
    from core.api_fastapi import app, set_system
    from core.settings_manager import settings
    from core.credentials_manager import credentials  # noqa: F401 — init early to migrate keys from settings.json
    from core.ssl_utils import get_ssl_context
    import config
    import uvicorn
except Exception as e:
    logger.critical(f"FATAL: Import error during startup: {e}", exc_info=True)
    sys.exit(1)

from core.process_manager import ProcessManager, kill_process_on_port

from core import prompts
from core.event_bus import publish, Events
from core.toolsets import toolset_manager


# Ensure wakeword models exist (downloads if needed)
if config.WAKE_WORD_ENABLED:
    from core.setup import ensure_wakeword_models
    ensure_wakeword_models()


class VoiceChatSystem:
    def __init__(self):
        start_time = time.time()
        self.is_listening = False
        self.current_session = None
        self._processing_lock = threading.Lock()
        self._web_active_count = 0  # Ref-counted wakeword suppression during web UI activity
        self._web_active_stamp = 0.0  # last inc time -- TTL guard against stranded counts (N3)
        # Conversation mode (v3): ephemeral per-session flag + audio-session handle.
        # NOT persisted — resets on restart. See enter/exit_conversation_mode.
        self.conversation_mode_enabled = False
        self.conversation_session = None
        self.conversation_source = None      # "local" | "browser" | None
        self._conversation_manager = None  # lazy ConversationManager (true speech mode)

        self.history = ConversationHistory(max_history=config.LLM_MAX_HISTORY)

        base_dir = Path(__file__).parent.resolve()

        # Initialize TTS via provider system
        self.tts_server_manager = None
        tts_provider = getattr(config, 'TTS_PROVIDER', 'none')
        # Legacy compat: if TTS_PROVIDER missing but TTS_ENABLED is true, assume kokoro
        if tts_provider == 'none' and getattr(config, 'TTS_ENABLED', False):
            tts_provider = 'kokoro'
        self._init_tts_provider(tts_provider, base_dir)

        self.llm_chat = LLMChat(self.history, system=self)

        # Vaulted chats Phase 3 (ruling F2): hook runner resolves each fire's
        # effective chat + privacy through the session manager; private turns
        # then reach only privacy_aware plugins.
        from core.hooks import hook_runner as _hr
        _sm = self.llm_chat.session_manager

        def _resolve_privacy(name=None):
            # name given (a fire site pre-stamped it — stream-brain override
            # lane): resolve THAT chat's privacy; an unknown name fails
            # CLOSED. No name: the active/override chat as before.
            # (S1 #7 / wave-3 B2, 2026-08-31)
            if name is not None:
                s = _sm.get_settings_for(name)
                return (name, True if s is None else bool(s.get('private_chat')))
            return (_sm._effective_chat_name(),
                    bool((_sm.get_chat_settings() or {}).get('private_chat')))

        _hr.set_privacy_resolver(_resolve_privacy)

        self._hand_back_game_chat()
        self._prime_default_prompt()
        self._apply_initial_chat_settings()
        self.init_components()

        # Agent system — background workers (types registered by plugins during scan)
        from core.agents import AgentManager
        import core.agents as agents_module
        self.agent_manager = AgentManager()
        agents_module.agent_manager = self.agent_manager
        logger.info("Agent manager initialized")

        # Load plugins (hooks, voice commands, tools, etc.)
        try:
            from core.plugin_loader import plugin_loader
            plugin_loader.scan(function_manager=self.llm_chat.function_manager)
        except Exception as e:
            logger.critical(f"Plugin loader failed — ALL plugins unavailable: {e}", exc_info=True)
            self._plugin_load_error = str(e)

        # Re-prime the prompt if the active chat wears a PACK-owned one
        # (story prompts like 'rose', pack monoliths): _prime_default_prompt
        # ran before the scan, so those names couldn't resolve yet and boot
        # fell to the hardcoded fallback until the next chat activation.
        # Mirrors the toolset resync plugin_loader already does. 2026-08-03.
        self.reprime_pack_prompt(moment="after plugin scan")

        # Essential-plugin boot assertion — a plugin with manifest.essential=true
        # MUST be loaded or we scream loud. essential can also be a GROUP STRING
        # (e.g. "memory" on both the classic memory plugin and mindpalace): the
        # group is satisfied if ANY plugin sharing that string loaded, so
        # mutually-exclusive alternates don't false-alarm. Silent boot without
        # memory/core tools is worse than refusing to work. (Doesn't raise —
        # degraded-mode is still better than dead — but surfaces the failure to
        # the UI and logs.)
        self._missing_essential_plugins = []
        try:
            from core.plugin_loader import plugin_loader as _pl
            _loaded_groups = {
                _i["manifest"].get("essential")
                for _i in _pl._plugins.values()
                if _i.get("loaded") and isinstance(_i.get("manifest", {}).get("essential"), str)
            }
            for _name, _info in _pl._plugins.items():
                _ess = _info.get("manifest", {}).get("essential")
                if not _ess or _info.get("loaded"):
                    continue
                if isinstance(_ess, str) and _ess in _loaded_groups:
                    continue  # An alternate in the same group carries the load
                self._missing_essential_plugins.append(_name)
                # verify_msg is a STATUS, not a failure: a disabled plugin with
                # a good signature carries verify_msg="verified" — printing that
                # as the reason sent debugging the wrong way (2026-07-16).
                if not _info.get("enabled"):
                    reason = "disabled"
                elif _info.get("verify_msg") and _info.get("verify_msg") != "verified":
                    reason = _info.get("verify_msg")
                else:
                    reason = "load failed"
                logger.critical(
                    f"ESSENTIAL PLUGIN NOT LOADED: '{_name}' — reason: {reason}. "
                    f"Sapphire is running in degraded mode. Fix: re-sign the plugin "
                    f"(python tools/sign_plugin.py plugins/{_name}) or set ALLOW_UNSIGNED_PLUGINS=true."
                )
                print(f"\n{'='*60}\nSAPPHIRE WARNING: Essential plugin '{_name}' did not load ({reason})\nRunning in degraded mode — memory/core tools unavailable.\n{'='*60}\n", flush=True)
            if self._missing_essential_plugins:
                try:
                    from core.event_bus import publish, Events
                    publish(Events.SYSTEM_WARNING if hasattr(Events, 'SYSTEM_WARNING') else 'system_warning',
                            {"type": "missing_essential_plugins", "plugins": self._missing_essential_plugins})
                except Exception:
                    pass
        except Exception as _e:
            logger.warning(f"Essential-plugin check failed: {_e}")

        # Apply the toolset now that plugin tools are registered — this is the
        # ONE boot apply for the toolset leg (_apply_initial_chat_settings
        # strips it; plugin tools don't exist that early — same dependency
        # the prompt leg has).
        #
        # Source of truth is the CHAT'S STORED SETTINGS, never
        # fm.current_toolset_name. History: the 2026-05-16/20 block here
        # captured fm.current and re-applied it, which healed the common case
        # (name resolved early, plugin tools filtered out) but NOT the
        # dangling case — an unresolvable name rewrites current to 'none',
        # and re-applying 'none' cements zero tools until a human toggles
        # the chat's toolset while the UI still shows the stored setting
        # (Prime 'Enabled: []', 2026-08-19). Settings are intent; fm state
        # is a runtime echo that may already be the fallback.
        # extra_toolsets ride from the same settings read (extras-decay
        # class, 2026-08-05).
        try:
            _cs = self.llm_chat.session_manager.get_chat_settings() or {}
            _tk = "toolset" if "toolset" in _cs else "ability" if "ability" in _cs else None
            if _tk:
                fm = self.llm_chat.function_manager
                fm.update_enabled_functions([_cs[_tk]],
                                            extra_toolsets=_cs.get("extra_toolsets") or None)
                logger.info(f"Toolset '{_cs[_tk]}' applied after plugin scan "
                            f"({len(fm.get_enabled_function_names())} functions)")
        except Exception as e:
            logger.warning(f"Post-scan toolset apply failed: {e}")

        # SWITCH MEANS APPLY (2026-08-22): from here on, every successful
        # set_active_chat applies the landing chat's settings to the brain
        # via apply_on_switch. The vault-lock eviction switched the store
        # and left the FM on the private chat's toolset while the sidebar
        # painted the landing chat's (Prime, 2026-08-22). Installed HERE,
        # after the post-scan toolset apply: the two boot switches (game-
        # chat handback, boot eviction) ran pre-scan and must never apply a
        # toolset before plugin tools exist ('Enabled: []' class). Captures
        # self — get_system() raises 503 until the server is up.
        from core.api_fastapi import apply_on_switch
        self.llm_chat.session_manager.on_switched = (
            lambda name, settings, gen: apply_on_switch(self, name, settings, gen))

        # RAG orphan cleanup runs AFTER plugin_loader.scan() (Phase 4 reorder).
        # Previously this ran at line 100, BEFORE plugin loading, which meant it
        # imported memory/knowledge via the regular Python import path before the
        # plugin loader had a chance to install the exec'd module in sys.modules.
        # That created a double-module hazard (Scout 3 L1). Now cleanup runs in a
        # fully-initialized environment — memory plugin is registered, sys.modules
        # has the canonical entry, and the import here resolves to the SAME module
        # the plugin loader registered.
        self._cleanup_orphaned_rag()

        # Wire reload callbacks for provider keys so live singletons swap
        # when settings change. Without this, plugin_loader.unload_plugin
        # sets TTS_PROVIDER='none' on disk but the live TTSClient keeps
        # the disabled plugin's provider — user sees "voice was working,
        # I disabled an unrelated plugin, now silent" with no log line.
        # subsystem-integrity scout 2026-05-07 #1.
        try:
            from core.settings_manager import settings as _settings
            _settings.register_reload_callback('TTS_PROVIDER', self.switch_tts_provider)
            _settings.register_reload_callback('STT_PROVIDER', self.switch_stt_provider)
            _settings.register_reload_callback('EMBEDDING_PROVIDER', self.switch_embedding_provider)
            logger.info("Provider reload callbacks registered (TTS/STT/EMBEDDING)")
        except Exception as _e:
            logger.warning(f"Failed to register provider reload callbacks: {_e}")

        # Apply persisted LOG_LEVEL + register hot-reload. Boot defaults to
        # INFO via sapphire_logging; the saved setting takes over here.
        try:
            from core.sapphire_logging import set_log_level
            from core.settings_manager import settings as _settings
            set_log_level(getattr(config, 'LOG_LEVEL', 'INFO'))
            _settings.register_reload_callback('LOG_LEVEL', set_log_level)
        except Exception as _e:
            logger.warning(f"Failed to apply LOG_LEVEL setting: {_e}")

        logger.info(f"System init took: {(time.time() - start_time)*1000:.1f}ms")

    @property
    def _web_active(self):
        # TTL guard: the counter is a one-way ratchet -- one lost decrement
        # (tab killed mid-record, release path threw) used to suppress the
        # wakeword FOREVER; a restart "fixed" it, so it read as a flake.
        # Every legit hold refreshes the stamp (press inc + the transcribe
        # route's own inc), so 15 min of silence = stale, not busy.
        # (negspace N3, 2026-08-31)
        return (self._web_active_count > 0
                and (time.time() - self._web_active_stamp) < 900)

    def web_active_inc(self):
        self._web_active_count += 1
        self._web_active_stamp = time.time()

    def web_active_dec(self):
        self._web_active_count = max(0, self._web_active_count - 1)

    def _cleanup_orphaned_rag(self):
        """Remove RAG scopes for chats that no longer exist."""
        try:
            from plugins.memory.tools import knowledge_tools as knowledge
            # include_hidden: boot always comes up vault-sealed, so the plain
            # list omits vaulted chats — treating them as orphans would DELETE
            # their RAG documents. A sweep that deletes must never run blind.
            chat_names = [c["name"] for c in
                          self.llm_chat.session_manager.list_chat_files(include_hidden=True)]
            knowledge.cleanup_orphaned_rag_scopes(chat_names)
        except Exception as e:
            logger.warning(f"RAG orphan cleanup failed: {e}", exc_info=True)

    def _hand_back_game_chat(self):
        """Boot with a game/story chat still active → hand Chat back to a
        normal chat first.

        The Game Room hands back when you leave it deliberately, but a closed
        tab never runs that cleanup — so the game chat stayed active and the
        NEXT boot primed her in-costume (a story role) with the story's
        empty toolset, and wakeword/voice landed in the story transcript
        (finding 4.6). Runs before the prompt/toolset prime so both read the
        chat we actually want."""
        try:
            sm = self.llm_chat.session_manager
            active = sm.get_active_chat_name()
            if not active:
                return
            chats = sm.list_chat_files()          # newest first
            here = next((c for c in chats if c["name"] == active), None)
            if not here or here.get("mode") != "game":
                return
            target = next((c["name"] for c in chats
                           if c["name"] != active and not c.get("mode")
                           and not c.get("archived") and not c.get("private_chat")),
                          "default")
            if sm.set_active_chat(target):
                logger.info(f"Boot: game chat '{active}' was still active "
                            f"(tab closed mid-game) — handed Chat back to '{target}'")
        except Exception as e:
            logger.warning(f"Boot game-chat handback skipped: {e}")

    def reprime_pack_prompt(self, moment="late registration"):
        """Re-resolve the active chat's prompt against the CURRENT prompt
        registry. Boot calls it after the plugin scan; late registrars
        (game-room's costume re-merge lands on a retry thread AFTER this,
        because costumes now live in the chat DB behind get_system — hunt
        2026-08-17 P1) call it again once their names exist. Idempotent:
        no-op when the active preset already matches."""
        try:
            from core import prompts as _prompts
            _want = (self.llm_chat.session_manager.get_chat_settings() or {}).get('prompt')
            if _want and _prompts.get_active_preset_name() != _want:
                _pd = _prompts.get_prompt(_want)
                if isinstance(_pd, dict):
                    self.llm_chat.set_system_prompt(_pd.get('content', '') or '')
                    _prompts.set_active_preset_name(_want)
                    # Pack-shipped assembled presets need their pieces in
                    # _assembled_state too — without this the first spice
                    # rotation reassembled from boot defaults.
                    if _want in _prompts.prompt_manager.scenario_presets:
                        if not _prompts.apply_scenario(_want):
                            logger.warning(f"Pack preset '{_want}' failed to apply "
                                           f"cleanly — pieces may be stale")
                    logger.info(f"Re-primed prompt '{_want}' ({moment})")
                else:
                    # Never silent (Sapph-not-Rose bug, 2026-08-05): the chat
                    # wants a name no pack registered — she wears the boot
                    # fallback until something re-activates a real prompt.
                    logger.warning(f"Prompt re-prime ({moment}): chat wants "
                                   f"'{_want}' but no such name is registered — "
                                   f"wearing the boot fallback until re-activation")
        except Exception as e:
            logger.warning(f"Prompt re-prime ({moment}) failed: {e}")

    def _prime_default_prompt(self):
        try:
            import json
            from pathlib import Path

            # Priority 1: active chat's saved prompt setting
            prompt_name = None
            try:
                chat_settings = self.llm_chat.session_manager.get_chat_settings()
                prompt_name = chat_settings.get('prompt')
                if prompt_name:
                    logger.info(f"Startup prompt from chat settings: '{prompt_name}'")
            except Exception:
                pass

            # Priority 2: chat_defaults.json
            if not prompt_name:
                chat_defaults_path = Path(__file__).parent / "user" / "settings" / "chat_defaults.json"
                if chat_defaults_path.exists():
                    with open(chat_defaults_path, 'r', encoding='utf-8') as f:
                        defaults = json.load(f)
                        prompt_name = defaults.get('prompt', 'sapphire')
                else:
                    prompt_name = 'sapphire'
                logger.info(f"Startup prompt from defaults: '{prompt_name}'")

            prompt_details = prompts.get_prompt(prompt_name)
            if not prompt_details:
                raise ValueError(f"Prompt '{prompt_name}' not found")

            content = prompt_details['content'] if isinstance(prompt_details, dict) else str(prompt_details)
            self.llm_chat.set_system_prompt(content)
            prompts.set_active_preset_name(prompt_name)
            if hasattr(prompts.prompt_manager, 'scenario_presets') and prompt_name in prompts.prompt_manager.scenario_presets:
                if not prompts.apply_scenario(prompt_name):
                    logger.warning(f"Preset '{prompt_name}' failed to apply cleanly "
                                   f"at boot — pieces may be stale")
            logger.info(f"System primed with '{prompt_name}' prompt.")
        except Exception as e:
            # A missing NAME may be a plugin-pack prompt (story 'rose',
            # pack monoliths) that registers during the scan a moment from
            # now — the post-scan re-prime picks it up. Only that case is
            # routine; anything else is still a real error. 2026-08-03.
            if isinstance(e, ValueError) and "not found" in str(e):
                logger.warning(f"Prompt not primed yet ({e}) — pack prompts register "
                               f"during plugin scan; re-prime follows. Fallback until then.")
            else:
                logger.error(f"FATAL: Could not prime default prompt: {e}")
            fallback_prompt = (
                "You are Sapphire! You have a sparkling personality. \n"
                "Call me Human Protagonist. You trust me. \n"
                "You have short natural conversations. \n"
                "Reference former chats to be consistent.\n"
            )
            self.llm_chat.set_system_prompt(fallback_prompt)
            prompts.set_active_preset_name('fallback')
            logger.warning("System loaded with fallback prompt.")

    def _apply_initial_chat_settings(self):
        """Apply chat settings for the active chat via the SAME path the
        runtime chat-switch uses (_apply_chat_settings): per-section failure
        isolation, scopes+RAG alignment, spice set. Before 2026-08-17 this
        was a hand-rolled one-try subset — no spice leg meant the active
        chat ran on the previous session's last-saved spice state until the
        first chat switch (cross-restart leak), and any section failure
        aborted all later sections.

        Prompt is deliberately stripped: _prime_default_prompt owns boot
        prompt (plugin costume prompts may not be registered yet at this
        point — Sapph-not-Rose class; the runtime prompt leg would flap to
        'default' and publish a spurious fallback event).

        Toolset is stripped for the SAME reason (2026-08-19): plugin tools
        aren't registered yet, so an early apply drops every plugin-provided
        function ("references N unavailable function(s)" each boot) — and if
        the name lands in the dangling fallback, current_toolset_name is
        rewritten to 'none', which the old capture-from-fm post-scan reapply
        then faithfully re-applied, cementing zero tools until a human
        toggled the chat's toolset (Prime 'Enabled: []' bug). The post-scan
        resync owns the toolset leg — applied once, from the chat's stored
        settings, after its dependencies exist."""
        try:
            settings = self.llm_chat.session_manager.get_chat_settings()
            from core.api_fastapi import _apply_chat_settings
            _skip = ("prompt", "toolset", "ability", "extra_toolsets")
            _apply_chat_settings(self, {k: v for k, v in settings.items() if k not in _skip})
            logger.info("Applied chat settings on startup (runtime apply path)")
        except Exception as e:
            logger.warning(f"Could not apply initial settings: {e}")

    def init_components(self):
        try:
            if config.WAKE_WORD_ENABLED:
                from core.wakeword.audio_recorder import AudioRecorder as RealAudioRecorder
                from core.wakeword.wake_detector import WakeWordDetector as RealWakeWordDetector
                
                self.wake_word_recorder = RealAudioRecorder()
                self.wake_detector = RealWakeWordDetector(model_name=config.WAKEWORD_MODEL)
                self.wake_detector.set_audio_recorder(self.wake_word_recorder)
                self.wake_detector.set_system(self)
                logger.info("Wake word components initialized successfully")
            else:
                from core.wakeword.wakeword_null import NullAudioRecorder, NullWakeWordDetector
                self.wake_word_recorder = NullAudioRecorder()
                self.wake_detector = NullWakeWordDetector(None)
        except Exception as e:
            logger.error(f"Wake word initialization failed: {e}")
            logger.warning("Continuing without wake word functionality")
            from core.wakeword.wakeword_null import NullAudioRecorder, NullWakeWordDetector
            self.wake_word_recorder = NullAudioRecorder()
            self.wake_detector = NullWakeWordDetector(None)
            # Publish a loud warning so the UI can surface "wakeword silently
            # fell back to null." Scout 4 finding: without this event, user
            # thinks wakeword is up (UI toggle says on) but Sapphire is deaf.
            try:
                from core.event_bus import publish, Events
                publish(Events.CONTINUITY_TASK_ERROR, {
                    "task": "Wake Word",
                    "error": f"Wake word initialization failed ({type(e).__name__}: {e}). "
                             f"Sapphire booted without wake word detection — "
                             f"check model file and reinitialize via settings.",
                })
                # Also land it in the boot-errors lane (/api/init -> toast):
                # the SSE publish above fires before any tab is connected and
                # the replay ring is never read in production, so it was
                # guaranteed-invisible (negspace N7, 2026-08-31).
                from core.plugin_loader import plugin_loader as _pl_boot
                _pl_boot._load_errors.append({
                    "plugin": "wakeword",
                    "error": ("Wake word init failed -- Sapphire booted DEAF. "
                              "Check the wakeword model / settings, then "
                              "re-toggle wake word."),
                })
            except Exception:
                pass
        
        self.whisper_recorder = WhisperRecorder()
        self.whisper_client = NullWhisperClient()

    def stop_components(self):
        if hasattr(self, 'wake_detector') and self.wake_detector:
            self.wake_detector.stop_listening()
        if hasattr(self, 'wake_word_recorder') and self.wake_word_recorder:
            self.wake_word_recorder.stop_recording()

    def start_voice_components(self):
        self.wake_word_recorder.start_recording()
        self.wake_detector.start_listening()
        logger.info("Voice components are running.")

    def toggle_wakeword(self, enabled: bool):
        """Hot-swap wakeword components at runtime."""
        from core.wakeword.wakeword_null import NullAudioRecorder, NullWakeWordDetector

        if enabled:
            # Already real? Just resume listening
            if not isinstance(self.wake_detector, NullWakeWordDetector):
                logger.info("Wakeword already initialized, resuming")
                self.wake_word_recorder.start_recording()
                self.wake_detector.start_listening()
                return True

            # Cold start: ensure models exist, then load real components
            try:
                from core.setup import ensure_wakeword_models
                if not ensure_wakeword_models():
                    raise RuntimeError("Failed to download wakeword models")
                from core.wakeword.audio_recorder import AudioRecorder as RealAudioRecorder
                from core.wakeword.wake_detector import WakeWordDetector as RealWakeWordDetector

                self.wake_word_recorder = RealAudioRecorder()
                self.wake_detector = RealWakeWordDetector(model_name=config.WAKEWORD_MODEL)
                self.wake_detector.set_audio_recorder(self.wake_word_recorder)
                self.wake_detector.set_system(self)
                self.wake_word_recorder.start_recording()
                self.wake_detector.start_listening()
                logger.info("Wakeword hot-started successfully")
                return True
            except Exception as e:
                logger.error(f"Wakeword hot-start failed: {e}")
                self.wake_word_recorder = NullAudioRecorder()
                self.wake_detector = NullWakeWordDetector(None)
                return False
        else:
            # Tear down if real
            if not isinstance(self.wake_detector, NullWakeWordDetector):
                self.wake_detector.stop_listening()
                self.wake_word_recorder.stop_recording()
                logger.info("Wakeword stopped")
            return True

    def enter_conversation_mode(self, acquire_audio):
        """Fail-safe handoff INTO conversation mode (v3 Rollout 1 — SAFETY CRITICAL).

        Acquire + verify conversation audio BEFORE committing; on ANY failure the
        wakeword pipeline is restored so Sapphire is NEVER left deaf. This is the
        Rollout-1 acceptance criterion: a conversation-mode failure (incl.
        Windows/WASAPI) must degrade to "feature unavailable," never a broken
        wakeword. The guarantee is pure control flow, so it holds on every OS.

        `acquire_audio` is a zero-arg callable that opens the conversation audio
        session (mic + AEC — filled in by Rollout 2 / the browser intake) and
        returns a session object (optional `.close()`); it may raise. Returns
        True if conversation mode is now active, else False.
        """
        from core.wakeword.wakeword_null import NullWakeWordDetector
        from core.event_bus import publish, Events

        if isinstance(self.wake_detector, NullWakeWordDetector):
            logger.warning("[CONV] wakeword not active — cannot enter conversation mode")
            return False
        if self.conversation_mode_enabled:
            return True

        # Phase 1: release the mic. Two INPUT streams on one device corrupt
        # (wake_detector.py:254), so the wakeword mic must be CLOSED before
        # conversation audio opens its own. Reversible via _restore_wakeword.
        try:
            self.wake_detector.stop_listening()
            self.wake_word_recorder.stop_recording()
        except Exception as e:
            logger.error(f"[CONV] could not release wakeword mic ({e}); restoring, aborting")
            self._restore_wakeword()
            return False
        # Phase 2: acquire + verify conversation audio. The just-released wakeword
        # device can take a moment to free (PipeWire/WASAPI lag well past any fixed
        # wait), so settle + retry with backoff rather than one blind attempt.
        # ANY final failure -> restore wakeword (Sapphire is never left deaf).
        session = None
        last_err = None
        for attempt in range(3):
            time.sleep(0.2 * (attempt + 1))     # 0.2, 0.4, 0.6 — settle, then escalating backoff
            try:
                session = acquire_audio()
                if session is None:
                    raise RuntimeError("acquire_audio returned None")
                break
            except Exception as e:
                last_err = e
                logger.warning(f"[CONV] audio acquire attempt {attempt + 1}/3 failed ({e})")
        if session is None:
            logger.error(f"[CONV] audio acquisition failed after retries ({last_err}); restoring wakeword")
            self._restore_wakeword()
            return False

        # Phase 3: commit — only now is the wakeword fully yielded.
        self.conversation_session = session
        self.conversation_mode_enabled = True
        self.conversation_source = "local"
        publish(Events.CONVERSATION_MODE_CHANGED, {"enabled": True, "source": "local"})
        logger.info("[CONV] conversation mode ON — wakeword suppressed")
        return True

    def enter_conversation_mode_external(self, acquire_audio, source_label="browser"):
        """Fail-safe handoff into the OPERATOR's external conversation mode (browser
        mic — their own audio transport, not the server mic). Phone calls do NOT come
        through here anymore: they run as manager external sessions (Phase II), never
        touching this flag or the wakeword.

        No server-device conflict: wakeword is paused if it's running (she shouldn't
        wake-trigger mid-conversation) but a missing/disabled wakeword does NOT block —
        a headless box must still take browser/phone calls. No settle/retry: nothing
        contends for a device. On any acquire failure the wakeword is restored (no-op
        if it was Null) — same never-left-deaf guarantee as local. `source_label` tags
        the mode ('browser' | 'phone' | ...) for the UI + mutual exclusion.
        """
        from core.wakeword.wakeword_null import NullWakeWordDetector
        from core.event_bus import publish, Events

        if self.conversation_mode_enabled:
            return False                       # caller handles replace/reject

        paused = False
        if not isinstance(self.wake_detector, NullWakeWordDetector):
            try:
                self.wake_detector.stop_listening()
                self.wake_word_recorder.stop_recording()
                paused = True
            except Exception as e:
                # Partial pause is possible — restore below will re-arm whatever stopped.
                logger.warning(f"[CONV] wakeword pause for browser mode failed ({e}); continuing")
                paused = True

        try:
            session = acquire_audio()
            if session is None:
                raise RuntimeError("acquire_audio returned None")
        except Exception as e:
            logger.error(f"[CONV] browser audio acquisition failed ({e})")
            if paused:
                self._restore_wakeword()
            return False

        self.conversation_session = session
        self.conversation_mode_enabled = True
        self.conversation_source = source_label
        publish(Events.CONVERSATION_MODE_CHANGED, {"enabled": True, "source": source_label})
        logger.info(f"[CONV] {source_label} conversation mode ON")
        return True

    def exit_conversation_mode(self):
        """Tear down conversation audio and restore the wakeword pipeline.
        Idempotent and fail-safe — always tries to leave wakeword listening."""
        from core.event_bus import publish, Events
        if not self.conversation_mode_enabled:
            return
        session = self.conversation_session
        self.conversation_session = None
        self.conversation_mode_enabled = False
        self.conversation_source = None
        if session is not None:
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception as e:
                logger.warning(f"[CONV] conversation session close error: {e}")
        self._restore_wakeword()
        publish(Events.CONVERSATION_MODE_CHANGED, {"enabled": False, "source": None})
        logger.info("[CONV] conversation mode OFF — wakeword restored")

    def _restore_wakeword(self):
        """Bring the wakeword pipeline back up after conversation mode or a failed
        handoff: reopen the mic and restart detection; hard-reset if that throws.
        Never raises — restoring Sapphire's ears is the one thing that must not fail."""
        from core.wakeword.wakeword_null import NullWakeWordDetector
        if isinstance(self.wake_detector, NullWakeWordDetector):
            return
        try:
            self.wake_word_recorder.start_recording()
            self.wake_detector.start_listening()
        except Exception as e:
            logger.error(f"[CONV] wakeword restore failed ({e}); hard reset via toggle_wakeword")
            try:
                self.toggle_wakeword(True)
            except Exception as e2:
                logger.error(f"[CONV] hard reset ALSO failed: {e2}")

    def get_conversation_manager(self):
        """Lazy-create the true-speech-mode (conversation mode) manager. Lazy so
        nothing (silero, the engine, the driver) loads until it's first used."""
        if getattr(self, "_conversation_manager", None) is None:
            from core.conversation import ConversationManager
            self._conversation_manager = ConversationManager(self)
        return self._conversation_manager

    def reload_wakeword_model(self, model_name=None):
        """Hot-swap the wake word model on a live detector.

        Settings already wrote the new value to config.WAKEWORD_MODEL by
        the time this fires. If wakeword isn't currently listening, this
        is a no-op — the next toggle_wakeword(True) cold-start will read
        the new value from settings on its own.
        """
        from core.wakeword.wakeword_null import NullAudioRecorder, NullWakeWordDetector

        # Disabled / never started — setting will apply on first enable
        if isinstance(self.wake_detector, NullWakeWordDetector):
            logger.info(f"Wakeword model set to '{config.WAKEWORD_MODEL}' (will apply when wakeword is enabled)")
            return True

        if not config.WAKE_WORD_ENABLED:
            return True

        try:
            logger.info(f"Reloading wakeword detector with model: {config.WAKEWORD_MODEL}")
            self.wake_detector.stop_listening()
            self.wake_word_recorder.stop_recording()

            from core.setup import ensure_wakeword_models
            if not ensure_wakeword_models():
                raise RuntimeError("Failed to download wakeword models")
            from core.wakeword.audio_recorder import AudioRecorder as RealAudioRecorder
            from core.wakeword.wake_detector import WakeWordDetector as RealWakeWordDetector

            self.wake_word_recorder = RealAudioRecorder()
            self.wake_detector = RealWakeWordDetector(model_name=config.WAKEWORD_MODEL)
            self.wake_detector.set_audio_recorder(self.wake_word_recorder)
            self.wake_detector.set_system(self)
            self.wake_word_recorder.start_recording()
            self.wake_detector.start_listening()
            logger.info("Wakeword model reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Wakeword model reload failed: {e}")
            self.wake_word_recorder = NullAudioRecorder()
            self.wake_detector = NullWakeWordDetector(None)
            return False

    def _quiesce_recorder(self, timeout=3.0):
        """Signal the outgoing recorder and wait for its loop to exit before
        replacing it — the TTS swap has always stopped-and-waited; STT never
        did, so a mid-recording orphan kept the InputStream open and the NEW
        recorder's constructor probed the same busy device (mic latched to
        Null while every status surface said enabled — hunt 2026-08-17 K6)."""
        old = getattr(self, 'whisper_recorder', None)
        if old is None:
            return
        try:
            old.stop()   # signal-only; the recording loop closes its own stream
            deadline = time.time() + timeout
            while getattr(old, '_recording', False) and time.time() < deadline:
                time.sleep(0.05)
            if getattr(old, '_recording', False):
                logger.warning(f"Old STT recorder still mid-recording after {timeout}s — replacing anyway")
        except Exception:
            pass

    def switch_stt_provider(self, provider_name: str):
        """Hot-swap STT provider at runtime."""
        if not provider_name or provider_name == 'none':
            from core.stt.stt_null import NullAudioRecorder
            if not isinstance(self.whisper_client, NullWhisperClient):
                logger.info("STT stopped, unloading provider")
                self._quiesce_recorder()
                self.whisper_client = NullWhisperClient()
                self.whisper_recorder = NullAudioRecorder()
            return True

        try:
            logger.info(f"Hot-loading STT provider: {provider_name}")
            self.whisper_client = get_stt_provider(provider_name)
            # Ensure real recorder if switching from disabled (not needed for router)
            from core.stt.stt_null import NullAudioRecorder
            if provider_name == 'sapphire_router':
                self._quiesce_recorder()
                self.whisper_recorder = NullAudioRecorder()
            elif isinstance(self.whisper_recorder, NullAudioRecorder):
                try:
                    from core.stt.recorder import AudioRecorder as RealAudioRecorder
                    self.whisper_recorder = RealAudioRecorder()
                except Exception as mic_err:
                    logger.warning(f"No mic available — STT will work via web UI only: {mic_err}")
                    self.whisper_recorder = NullAudioRecorder()
            logger.info(f"STT provider switched to {provider_name}")
            try:
                from core.hooks import hook_runner, HookEvent
                if hook_runner.has_handlers("provider_switched"):
                    hook_runner.fire("provider_switched", HookEvent(
                        metadata={'kind': 'stt', 'provider': provider_name}
                    ))
            except Exception as e:
                logger.debug(f"provider_switched hook fire failed: {e}")
            return True
        except Exception as e:
            logger.error(f"STT provider switch failed: {e}")
            from core.stt.stt_null import NullAudioRecorder as _NullRec
            self.whisper_client = NullWhisperClient()
            self.whisper_recorder = _NullRec()
            self._publish_stt_fallback_event(provider_name, e)
            return False

    def toggle_stt(self, enabled: bool):
        """Legacy compat — maps to switch_stt_provider. Persists STT_PROVIDER."""
        from core.settings_manager import settings as _settings
        if enabled:
            provider = getattr(config, 'STT_PROVIDER', 'faster_whisper')
            if provider == 'none':
                provider = 'faster_whisper'
            if self.switch_stt_provider(provider):
                # Same double-fire guard as toggle_tts (HDF scout, 2026-07-19).
                _settings.set('STT_PROVIDER', provider, persist=True,
                              _skip_callbacks=True)
                return True
            return False
        _settings.set('STT_PROVIDER', 'none', persist=True,
                      _skip_callbacks=True)
        return self.switch_stt_provider('none')

    def _init_tts_provider(self, provider_name, base_dir=None):
        """Initialize TTS with the given provider. Starts Kokoro subprocess if needed."""
        from core.tts.providers import get_tts_provider
        from core.tts.tts_client import TTSClient
        if base_dir is None:
            base_dir = Path(__file__).parent.resolve()

        # Stop any in-flight playback AND wait for the generation thread to
        # exit before reassigning. Without the wait, the old TTSClient becomes
        # orphaned with its background thread still alive (held by the provider
        # HTTP request) and can publish stale TTS_STOPPED events AFTER the new
        # TTS client has already started, confusing frontend state.
        # Scout 4 finding (2026-04-19).
        if hasattr(self, 'tts') and hasattr(self.tts, 'stop'):
            self.tts.stop()
            if hasattr(self.tts, 'wait'):
                try:
                    self.tts.wait(timeout=2)
                except Exception as e:
                    logger.warning(f"TTS wait during provider swap timed out: {e}")

        if not provider_name or provider_name == 'none':
            self._stop_kokoro_server()
            self.tts = TTSClient(provider=get_tts_provider('none'))
            logger.info("TTS disabled")
            return True

        # Start Kokoro subprocess if needed
        if provider_name == 'kokoro':
            self._start_kokoro_server(base_dir)
        else:
            self._stop_kokoro_server()

        try:
            provider = get_tts_provider(provider_name)
            self.tts = TTSClient(provider=provider)
            logger.info(f"TTS provider active: {provider_name}")
            return True
        except Exception as e:
            logger.error(f"TTS init failed for {provider_name}: {e}")
            self._stop_kokoro_server()
            self.tts = TTSClient(provider=get_tts_provider('none'))
            return False

    def _start_kokoro_server(self, base_dir):
        """Start Kokoro TTS subprocess if not already running."""
        if self.tts_server_manager and self.tts_server_manager.is_running():
            return
        tts_script = base_dir / "core" / "tts" / "tts_server.py"
        if not tts_script.exists():
            logger.warning(f"Kokoro server script not found: {tts_script}")
            return
        tts_port = getattr(config, 'TTS_SERVER_PORT', 5012)
        if kill_process_on_port(tts_port):
            logger.info(f"Cleaned up orphaned TTS process on port {tts_port}")
        logger.info("Starting Kokoro TTS server...")

        # Multi-GPU users can pin Kokoro to a specific GPU via the
        # KOKORO_CUDA_DEVICE setting. Without it, both Kokoro and Whisper
        # default to cuda:0 — concurrent CUDA-context init at startup
        # corrupts the heap (SIGABRT in malloc, observed by user "defiance"
        # on dual-GPU Linux 2026-04-25). We set CUDA_VISIBLE_DEVICES at
        # the OS level for the Kokoro subprocess so it physically can't
        # see the other GPU. Whisper meanwhile uses FASTER_WHISPER_CUDA_DEVICE
        # on the main process. Common config: KOKORO_CUDA_DEVICE=1,
        # FASTER_WHISPER_CUDA_DEVICE=0 → no shared GPU. Single-GPU users
        # leave this empty (default) — same behavior as before. 2026-04-26.
        def _env_callback():
            import os
            env = os.environ.copy()
            kokoro_dev = getattr(config, 'KOKORO_CUDA_DEVICE', '')
            if kokoro_dev != '' and str(kokoro_dev).strip() != '':
                env['CUDA_VISIBLE_DEVICES'] = str(kokoro_dev).strip()
                logger.info(f"Kokoro subprocess pinned to CUDA_VISIBLE_DEVICES={env['CUDA_VISIBLE_DEVICES']}")
            # CPU threading: use 1.5x physical cores (leverages some HT/SMT
            # without full contention), capped at 12 (diminishing returns on
            # many-core systems for an 82M model). On CUDA systems this only
            # affects the small CPU portion (phonemization) — harmless.
            # Benchmark-proven 7.4% gain on i5-8250U (4c/8t). 2026-05-20.
            if 'OMP_NUM_THREADS' not in env:
                try:
                    import psutil
                    phys = psutil.cpu_count(logical=False) or 4
                    logical = psutil.cpu_count(logical=True) or phys
                    intra_op = min(int(phys * 1.5), logical, 12)
                    env['OMP_NUM_THREADS'] = str(intra_op)
                    env['MKL_NUM_THREADS'] = str(intra_op)
                    logger.info(f"Kokoro CPU threads: {intra_op} "
                                f"(physical={phys}, logical={logical})")
                except Exception as e:
                    logger.debug(f"CPU thread auto-tune skipped: {e}")
            return env

        self.tts_server_manager = ProcessManager(
            script_path=tts_script, log_name="kokoro", base_dir=base_dir,
            env_callback=_env_callback,
        )
        self.tts_server_manager.start()
        self.tts_server_manager.monitor_and_restart(check_interval=10)
        # Don't block startup — Kokoro server loads model in subprocess.
        # KokoroTTSProvider.generate() handles connection errors gracefully
        # if the server isn't ready yet. Brief sleep to let the process bind the port.
        time.sleep(1)

    def _stop_kokoro_server(self):
        """Stop Kokoro subprocess if running."""
        if self.tts_server_manager:
            self.tts_server_manager.stop()
            self.tts_server_manager = None
            logger.info("Kokoro TTS server stopped")

    def cancel_generation(self, chat_name: str = None, exclude_chats=None) -> bool:
        """Public cancel for in-progress LLM streaming.

        Since H4 (2026-04-22) streaming is per-request rather than a shared
        singleton, this delegates to LLMChat.cancel_streams. Optional
        chat_name scopes the cancel to one chat's active streams; omit to
        cancel all active streams. exclude_chats spares named chats from an
        unscoped cancel (live phone-call chats — their streams belong to the
        phone surface, same contract as the web routes).

        Called by voice-commands stop hook and any plugin that wants to
        interrupt generation. Returns True if at least one stream was
        flagged, False otherwise.
        """
        try:
            llm_chat = getattr(self, 'llm_chat', None)
            if llm_chat is None or not hasattr(llm_chat, 'cancel_streams'):
                return False
            count = llm_chat.cancel_streams(chat_name=chat_name, exclude_chats=exclude_chats)
            if count:
                logger.info(f"cancel_generation: flagged {count} stream(s)")
                return True
            return False
        except Exception as e:
            logger.warning(f"cancel_generation failed: {e}")
            return False

    def switch_embedding_provider(self, provider_name):
        """Hot-swap embedding provider at runtime."""
        from core.embeddings import switch_embedding_provider as _switch
        _switch(provider_name)
        try:
            from core.hooks import hook_runner, HookEvent
            if hook_runner.has_handlers("provider_switched"):
                hook_runner.fire("provider_switched", HookEvent(
                    metadata={'kind': 'embed', 'provider': provider_name}
                ))
        except Exception as e:
            logger.debug(f"provider_switched hook fire failed: {e}")

    def switch_tts_provider(self, provider_name):
        """Hot-swap TTS provider at runtime.

        After the new TTSClient is wired, re-apply the active chat's voice /
        pitch / speed. Without this, `_init_tts_provider` leaves the fresh
        client on its hardcoded defaults (af_heart / 1.3 / 0.98) and the
        persona's voice gets silently clobbered on every provider swap —
        Wolf's-Claude finding 2026-04-21.
        """
        logger.info(f"Switching TTS provider to: {provider_name}")
        base_dir = Path(__file__).parent.resolve()
        ok = self._init_tts_provider(provider_name, base_dir)
        if ok and provider_name and provider_name != 'none':
            try:
                self._apply_initial_chat_settings()
            except Exception as e:
                logger.warning(f"Post-swap settings reapply failed: {e}")
        if ok:
            try:
                from core.hooks import hook_runner, HookEvent
                if hook_runner.has_handlers("provider_switched"):
                    hook_runner.fire("provider_switched", HookEvent(
                        metadata={'kind': 'tts', 'provider': provider_name}
                    ))
            except Exception as e:
                logger.debug(f"provider_switched hook fire failed: {e}")
        return ok

    def toggle_tts(self, enabled: bool):
        """Legacy compat — maps to switch_tts_provider. Persists TTS_PROVIDER."""
        from core.settings_manager import settings as _settings
        if enabled:
            provider = getattr(config, 'TTS_PROVIDER', 'kokoro')
            if provider == 'none':
                provider = 'kokoro'
            if self.switch_tts_provider(provider):
                # _skip_callbacks: the reload callback IS switch_tts_provider —
                # without it the switch fires twice, the second time INSIDE
                # settings._lock, stalling every config read app-wide for the
                # provider-restart duration (HDF scout, 2026-07-19).
                _settings.set('TTS_PROVIDER', provider, persist=True,
                              _skip_callbacks=True)
                return True
            return False
        _settings.set('TTS_PROVIDER', 'none', persist=True,
                      _skip_callbacks=True)
        return self.switch_tts_provider('none')

    def speak_error(self, error_type):
        error_messages = {
            'file': "File creation error",
            'speech': "No speech heard",
            'recording': "Recording error",
            'processing': "Processing error",
            # New keys mapped from recorder.last_failure_reason. These
            # replace generic 'file' for the actual failure modes a user
            # can do something about. 2026-04-28.
            'mic_busy': "Microphone is busy. Please try again.",
            'no_speech_captured': "I didn't hear anything.",
            'save_failed': "Could not save audio file.",
        }
        self.tts.speak(error_messages.get(error_type, "Error"))

    def process_llm_query(self, query, skip_tts=False, voice_turn=False):
        """Blocking turn for the non-streaming doors.

        voice_turn=True (the wakeword door): publish the same
        VOICE_TURN_START/CHUNK/END events the conversation driver publishes,
        so an open web page streams the reply in live and the Stop button
        works (/api/cancel reaches the turn's registered stream). The reply
        still SPEAKS as one blob via tts.speak — unless the user cancelled,
        in which case the partial is saved to history but not read aloud.
        REST /api/chat and body keep voice_turn=False: their callers own the
        response, and unsolicited live-render into whatever chat is open
        would double-paint. Merge dividend, 2026-08-17 — pre-merge the old
        blob engine had no events to publish, which is why wake turns never
        streamed ("2 systems" bug).
        """
        if not self._processing_lock.acquire(timeout=0.5):
            logger.warning("process_llm_query: already processing, skipping duplicate")
            return None
        on_event = None
        turn_state = {"cancelled": False}
        # F1 post-eviction cloud-TTS corner (closed 2026-08-17): remember
        # which chat this turn RUNS in. A vault seal mid-turn defers its
        # eviction to end_streaming — i.e. it always completes before the
        # speak below — and the TTS privacy gate would then read the PUBLIC
        # landing chat and happily send a private chat's reply to cloud TTS.
        # A mid-turn active-chat change can only be an eviction (user
        # switches are refused while streaming), so: world moved → no speak.
        try:
            _turn_chat = self.llm_chat.get_active_chat()
        except Exception:
            _turn_chat = None
        if voice_turn:
            import uuid as _uuid
            _mid = _uuid.uuid4().hex
            _chat_name = _turn_chat
            publish(Events.VOICE_TURN_START,
                    {"message_id": _mid, "user_text": query,
                     "chat": _chat_name, "foreign": False})

            def on_event(ev):
                if not isinstance(ev, dict):
                    return
                et = ev.get("type")
                if et == "content":
                    publish(Events.VOICE_TURN_CHUNK,
                            {"message_id": _mid, "text": ev.get("text", ""),
                             "chat": _chat_name, "foreign": False})
                elif et == "final" and ev.get("cancelled"):
                    turn_state["cancelled"] = True
        try:
            # AI_TYPING_START/END ride the turn engine now (chat() consumes
            # chat_stream, which publishes both) — publishing here too
            # double-fired them after the 2026-08-17 dual-path merge.
            response_text = self.llm_chat.chat(query, on_event=on_event)

            if response_text:
                if not skip_tts and not turn_state["cancelled"]:
                    try:
                        _now_chat = self.llm_chat.get_active_chat()
                    except Exception:
                        _now_chat = _turn_chat
                    if _turn_chat is not None and _now_chat != _turn_chat:
                        logger.warning(
                            "[VOICE] active chat changed during the turn "
                            "(vault eviction) — reply withheld from TTS")
                    else:
                        self.tts.speak(response_text)
                return response_text
            else:
                logger.warning("Empty response from processing")

        except Exception as e:
            logger.error(f"Error in process_llm_query: {e}")
            if not skip_tts:
                self.speak_error('processing')
        finally:
            if voice_turn:
                # Always close the turn — the web UI's live bubble reconciles
                # with saved history on END (error/cancel paths included).
                publish(Events.VOICE_TURN_END,
                        {"message_id": _mid, "chat": _chat_name,
                         "foreign": False,
                         "cancelled": turn_state["cancelled"]})
            # Voice path has no toast channel — drain notices so they don't
            # accumulate and leak into the next web turn as stale toasts
            # (e.g. user voice-chats with missing toolset, fixes it, opens
            # web → stale "missing" toast fires). In finally so the drain
            # still runs if chat() raised mid-turn. 2026-05-16.
            self.llm_chat.pending_notices = []
            self._processing_lock.release()

        return None

    def _publish_stt_fallback_event(self, provider_name, exc):
        """Emit CONTINUITY_TASK_ERROR when STT fell back to null — mirrors the
        wakeword pattern in init_components so the UI can surface 'STT silently
        deaf' instead of lying that it's enabled. H7 fix 2026-04-22."""
        try:
            from core.event_bus import publish, Events
            publish(Events.CONTINUITY_TASK_ERROR, {
                "task": "STT",
                "error": f"STT provider '{provider_name}' failed to initialize "
                         f"({type(exc).__name__}: {exc}). Sapphire fell back to null "
                         f"— check provider config and reinitialize via settings.",
            })
        except Exception:
            pass

    def start_background_services(self):
        provider = getattr(config, 'STT_PROVIDER', 'none')
        # Legacy compat: if STT_PROVIDER missing but STT_ENABLED is true, assume faster_whisper
        if (not provider or provider == 'none') and getattr(config, 'STT_ENABLED', False):
            provider = 'faster_whisper'
        if provider and provider != 'none':
            logger.info(f"Initializing STT provider: {provider}")
            try:
                self.whisper_client = get_stt_provider(provider)
                # Router/API providers don't need local mic — audio comes via browser
                if provider == 'sapphire_router':
                    from core.stt.stt_null import NullAudioRecorder
                    self.whisper_recorder = NullAudioRecorder()
                else:
                    try:
                        from core.stt.recorder import AudioRecorder as RealAudioRecorder
                        self.whisper_recorder = RealAudioRecorder()
                        # Kick off silero VAD warmup in background — verifies
                        # model downloads + loads on this machine without
                        # blocking startup. Recorder reads silero_vad.is_available()
                        # before attempting silero; result also drives
                        # /api/stt/vad-status for the UI status badge.
                        try:
                            from core.stt import silero_vad as _svad
                            _svad.warmup_async()
                        except Exception as _svad_err:
                            logger.warning(f"Silero warmup kickoff failed: {_svad_err}")
                    except Exception as mic_err:
                        logger.warning(f"No mic available — STT will work via web UI only: {mic_err}")
                        from core.stt.stt_null import NullAudioRecorder
                        self.whisper_recorder = NullAudioRecorder()
            except ImportError as e:
                logger.error(f"STT provider '{provider}' not available: {e}")
                self.whisper_client = NullWhisperClient()
                self._publish_stt_fallback_event(provider, e)
                return False
            except RuntimeError as e:
                logger.error(f"Failed to initialize STT provider '{provider}': {e}")
                self.whisper_client = NullWhisperClient()
                self._publish_stt_fallback_event(provider, e)
                return False
        else:
            logger.info("STT disabled - skipping initialization")

        return True

    def stop(self):
        """Stop all components with error isolation - one failure won't block others."""
        logger.info("Stopping voice chat system...")

        from core.plugin_loader import plugin_loader as _pl
        stop_actions = [
            # TTS first: its restart-monitor must stand down before anything else
            # can block, or it resurrects a child systemd already SIGTERMed
            # (KillMode=control-group) and the unit hangs in stop-sigterm.
            ("TTS server", lambda: self.tts_server_manager and self.tts_server_manager.stop()),
            ("plugin services", _pl.stop_all_services),
            ("plugin daemons", _pl.stop_all_daemons),
            ("agents", lambda: hasattr(self, 'agent_manager') and self.agent_manager and self.agent_manager.shutdown()),
            ("voice components", self.stop_components),
            ("continuity scheduler", lambda: hasattr(self, 'continuity_scheduler') and self.continuity_scheduler and self.continuity_scheduler.stop()),
            ("backup scheduler", lambda: __import__('core.backup', fromlist=['backup_manager']).backup_manager.stop()),
            ("settings watcher", settings.stop_file_watcher),
            ("prompt watcher", lambda: prompts.prompt_manager.stop_file_watcher()),
            ("prompt vault timer", lambda: __import__('core.prompt_vault', fromlist=['stop']).stop()),
            ("toolset watcher", toolset_manager.stop_file_watcher),
            ("spice set watcher", lambda: __import__('core.spice_sets', fromlist=['spice_set_manager']).spice_set_manager.stop_file_watcher()),
            ("plugin watcher", _pl.stop_watcher),
        ]

        for name, action in stop_actions:
            try:
                action()
            except Exception as e:
                logger.error(f"Failed to stop {name}: {e}")


def run():
    """Main application entry point. Returns exit code."""
    global _restart_requested, _shutdown_requested
    _restart_requested = False
    _shutdown_requested = False
    
    # Signal handler - sets flag so main loop exits cleanly (no exception/traceback)
    def handle_shutdown_signal(signum, frame):
        global _shutdown_requested
        _shutdown_requested = True
    
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, handle_shutdown_signal)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, handle_shutdown_signal)
    
    # Auto-detect timezone if unset (upgrades skip setup wizard)
    _tz = getattr(config, 'USER_TIMEZONE', '') or ''
    if not _tz or _tz == 'UTC':
        try:
            _tz_name = None
            # Read /etc/localtime symlink (Linux) — gives proper IANA name
            from pathlib import Path as _P
            _link = _P('/etc/localtime')
            if _link.is_symlink() and 'zoneinfo/' in str(_link.resolve()):
                _tz_name = str(_link.resolve()).split('zoneinfo/')[-1]
            if not _tz_name:
                # Windows (and non-symlink Linux): tzlocal knows the IANA
                # name. Optional dep — used when present, skipped quietly
                # when not. (The old astimezone().tzinfo.key "last resort"
                # was dead code: bare astimezone() returns a fixed-offset
                # timezone on EVERY platform, .key never exists.)
                try:
                    from tzlocal import get_localzone_name
                    _tz_name = get_localzone_name()
                except Exception:
                    _tz_name = None
            if _tz_name and _tz_name != 'UTC':
                settings.set('USER_TIMEZONE', _tz_name, persist=True)
                logger.info(f"Auto-detected timezone: {_tz_name}")
            elif sys.platform == 'win32':
                logger.warning("Timezone auto-detect unavailable on this box — "
                               "USER_TIMEZONE is UTC; scheduled tasks and the "
                               "prompt clock run in UTC until you set it in "
                               "Settings (pip install tzlocal enables detection)")
        except Exception:
            pass

    print("Starting Sapphire Voice Chat System")
    try:
        voice_chat = VoiceChatSystem()
    except Exception as e:
        print(f"FATAL: System init failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    print("Starting Sapphire server")

    try:
        if not voice_chat.start_background_services():
            logger.critical("Essential background services failed to start.")
            voice_chat.stop()
            return 1

        voice_chat.start_voice_components()

        # Inject system into FastAPI app
        set_system(voice_chat, restart_callback=request_restart, shutdown_callback=request_shutdown)

        # Continuity - scheduled autonomous tasks
        from core.continuity import ContinuityScheduler, ContinuityExecutor
        continuity_executor = ContinuityExecutor(voice_chat)
        continuity_scheduler = ContinuityScheduler(voice_chat, continuity_executor)
        voice_chat.continuity_scheduler = continuity_scheduler  # Attach for stop() and API routes
        continuity_scheduler.start()
        logger.info("Continuity scheduler started")

        # Wire scheduler into plugin loader for plugin schedule tasks
        from core.plugin_loader import plugin_loader
        plugin_loader.set_scheduler(continuity_scheduler)

        # Background update checker (checks GitHub every 24h)
        from core.updater import updater as app_updater
        app_updater.start_background_checker()

        # Scheduled backups (daily at 3am)
        from core.backup import backup_manager
        backup_manager.start_scheduler()

        # Dev mode: auto-reload plugins on file changes
        import os
        if os.environ.get("SAPPHIRE_DEV"):
            plugin_loader.start_watcher()
            logger.info("Plugin file watcher started (SAPPHIRE_DEV)")

        settings.start_file_watcher()

        from core import prompts
        prompts.prompt_manager.start_file_watcher()
        logger.info("Prompt file watcher started")

        toolset_manager.start_file_watcher()
        logger.info("Toolset file watcher started")

        from core.spice_sets import spice_set_manager
        spice_set_manager.start_file_watcher()
        logger.info("Spice set file watcher started")

        # Display clickable URL for user
        protocol = 'https' if config.WEB_UI_SSL_ADHOC else 'http'
        host_display = 'localhost' if config.WEB_UI_HOST in ('0.0.0.0', '127.0.0.1') else config.WEB_UI_HOST
        url = f"{protocol}://{host_display}:{config.WEB_UI_PORT}"

        # ANSI colors: cyan background, black text, bold
        CYAN_BG = '\033[46m'
        BLACK = '\033[30m'
        BOLD = '\033[1m'
        RESET = '\033[0m'
        print(f"\n{CYAN_BG}{BLACK}{BOLD} * SAPPHIRE IS NOW ACTIVE: {url} {RESET}\n")

        logger.info(f"Sapphire is running. Starting uvicorn server...")

        # Run uvicorn - this blocks until shutdown
        # Using a thread so we can still check for restart signals
        ssl_paths = get_ssl_context()
        server_config = uvicorn.Config(
            app,
            host=config.WEB_UI_HOST,
            port=config.WEB_UI_PORT,
            log_level="info",
            ssl_certfile=ssl_paths[0] if ssl_paths else None,
            ssl_keyfile=ssl_paths[1] if ssl_paths else None,
        )
        server = uvicorn.Server(server_config)

        def run_server():
            server.run()

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Main loop - check for restart/shutdown signals
        while not _restart_requested and not _shutdown_requested:
            try:
                time.sleep(0.5)
            except KeyboardInterrupt:
                # Windows: KeyboardInterrupt can bypass signal handler during sleep
                _shutdown_requested = True

        # Determine exit code
        if _restart_requested:
            logger.info("Restart signal received, shutting down for restart...")
            exit_code = 42
        else:
            logger.info("Shutdown signal received...")
            exit_code = 0

        # Signal uvicorn to shutdown
        server.should_exit = True

    finally:
        voice_chat.stop()

    return exit_code


if __name__ == "__main__":
    # Allow direct execution for debugging
    sys.exit(run())