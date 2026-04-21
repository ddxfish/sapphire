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

        # Essential-plugin boot assertion — any plugin with manifest.essential=true
        # MUST be loaded or we scream loud. Silent boot without memory/core tools is
        # worse than refusing to work. (Doesn't raise — degraded-mode is still better
        # than dead — but surfaces the failure to the UI and logs.)
        self._missing_essential_plugins = []
        try:
            from core.plugin_loader import plugin_loader as _pl
            for _name, _info in _pl._plugins.items():
                if _info.get("manifest", {}).get("essential") and not _info.get("loaded"):
                    self._missing_essential_plugins.append(_name)
                    reason = _info.get("verify_msg") or ("disabled" if not _info.get("enabled") else "load failed")
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

        # Re-apply toolset now that plugin tools are registered
        # (toolset was applied before plugins loaded, so plugin tools were missed)
        fm = self.llm_chat.function_manager
        if fm.current_toolset_name and fm.current_toolset_name != "none":
            fm.update_enabled_functions([fm.current_toolset_name])
            logger.info(f"Toolset '{fm.current_toolset_name}' re-applied after plugin scan")

        # RAG orphan cleanup runs AFTER plugin_loader.scan() (Phase 4 reorder).
        # Previously this ran at line 100, BEFORE plugin loading, which meant it
        # imported memory/knowledge via the regular Python import path before the
        # plugin loader had a chance to install the exec'd module in sys.modules.
        # That created a double-module hazard (Scout 3 L1). Now cleanup runs in a
        # fully-initialized environment — memory plugin is registered, sys.modules
        # has the canonical entry, and the import here resolves to the SAME module
        # the plugin loader registered.
        self._cleanup_orphaned_rag()

        logger.info(f"System init took: {(time.time() - start_time)*1000:.1f}ms")

    @property
    def _web_active(self):
        return self._web_active_count > 0

    def web_active_inc(self):
        self._web_active_count += 1

    def web_active_dec(self):
        self._web_active_count = max(0, self._web_active_count - 1)

    def _cleanup_orphaned_rag(self):
        """Remove RAG scopes for chats that no longer exist."""
        try:
            from plugins.memory.tools import knowledge_tools as knowledge
            chat_names = [c["name"] for c in self.llm_chat.list_chats()]
            knowledge.cleanup_orphaned_rag_scopes(chat_names)
        except Exception as e:
            logger.warning(f"RAG orphan cleanup failed: {e}", exc_info=True)

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
                prompts.apply_scenario(prompt_name)
            logger.info(f"System primed with '{prompt_name}' prompt.")
        except Exception as e:
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
        """Apply chat settings for the active chat on startup."""
        try:
            settings = self.llm_chat.session_manager.get_chat_settings()
            
            if "voice" in settings:
                from core.tts.utils import validate_voice
                voice = validate_voice(settings["voice"])
                self.tts.set_voice(voice)
            if "pitch" in settings:
                self.tts.set_pitch(settings["pitch"])
            if "speed" in settings:
                self.tts.set_speed(settings["speed"])
            
            # Prompt already handled by _prime_default_prompt (checks chat settings first)

            toolset_key = "toolset" if "toolset" in settings else "ability" if "ability" in settings else None
            if toolset_key:
                toolset_name = settings[toolset_key]
                self.llm_chat.function_manager.update_enabled_functions([toolset_name])
                logger.info(f"Applied toolset on startup: {toolset_name}")
            
            logger.info(f"Applied chat settings on startup")
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

    def switch_stt_provider(self, provider_name: str):
        """Hot-swap STT provider at runtime."""
        if not provider_name or provider_name == 'none':
            from core.stt.stt_null import NullAudioRecorder
            if not isinstance(self.whisper_client, NullWhisperClient):
                logger.info("STT stopped, unloading provider")
                self.whisper_client = NullWhisperClient()
                self.whisper_recorder = NullAudioRecorder()
            return True

        try:
            logger.info(f"Hot-loading STT provider: {provider_name}")
            self.whisper_client = get_stt_provider(provider_name)
            # Ensure real recorder if switching from disabled (not needed for router)
            from core.stt.stt_null import NullAudioRecorder
            if provider_name == 'sapphire_router':
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
            return False

    def toggle_stt(self, enabled: bool):
        """Legacy compat — maps to switch_stt_provider. Persists STT_PROVIDER."""
        from core.settings_manager import settings as _settings
        if enabled:
            provider = getattr(config, 'STT_PROVIDER', 'faster_whisper')
            if provider == 'none':
                provider = 'faster_whisper'
            if self.switch_stt_provider(provider):
                _settings.set('STT_PROVIDER', provider, persist=True)
                return True
            return False
        _settings.set('STT_PROVIDER', 'none', persist=True)
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
        self.tts_server_manager = ProcessManager(
            script_path=tts_script, log_name="kokoro", base_dir=base_dir
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

    def cancel_generation(self) -> bool:
        """Public cancel for in-progress LLM streaming.

        Replaces the `system.llm_chat.streaming_chat.cancel_flag = True`
        reach-in that plugin docs previously advertised (voice-commands
        stop hook, etc.). Plugin code should call this instead — keeps
        the cancel surface owned by core, not by private-attribute
        mutation on a sub-object. Returns True if the flag was set,
        False if there's no streaming client to cancel.
        Wolf-Claude finding 2026-04-21.
        """
        try:
            streaming = getattr(getattr(self, 'llm_chat', None), 'streaming_chat', None)
            if streaming is None:
                return False
            streaming.cancel_flag = True
            logger.info("cancel_generation: flag set via public API")
            return True
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
                _settings.set('TTS_PROVIDER', provider, persist=True)
                return True
            return False
        _settings.set('TTS_PROVIDER', 'none', persist=True)
        return self.switch_tts_provider('none')

    def speak_error(self, error_type):
        error_messages = {
            'file': "File creation error",
            'speech': "No speech heard",
            'recording': "Recording error",
            'processing': "Processing error"
        }
        self.tts.speak(error_messages.get(error_type, "Error"))

    def process_llm_query(self, query, skip_tts=False):
        if not self._processing_lock.acquire(timeout=0.5):
            logger.warning("process_llm_query: already processing, skipping duplicate")
            return None
        try:
            publish(Events.AI_TYPING_START)
            response_text = self.llm_chat.chat(query)

            if response_text:
                publish(Events.AI_TYPING_END)
                if not skip_tts:
                    self.tts.speak(response_text)
                return response_text
            else:
                publish(Events.AI_TYPING_END)
                logger.warning("Empty response from processing")

        except Exception as e:
            publish(Events.AI_TYPING_END)
            logger.error(f"Error in process_llm_query: {e}")
            if not skip_tts:
                self.speak_error('processing')
        finally:
            self._processing_lock.release()

        return None

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
                    except Exception as mic_err:
                        logger.warning(f"No mic available — STT will work via web UI only: {mic_err}")
                        from core.stt.stt_null import NullAudioRecorder
                        self.whisper_recorder = NullAudioRecorder()
            except ImportError as e:
                logger.error(f"STT provider '{provider}' not available: {e}")
                self.whisper_client = NullWhisperClient()
                return False
            except RuntimeError as e:
                logger.error(f"Failed to initialize STT provider '{provider}': {e}")
                self.whisper_client = NullWhisperClient()
                return False
        else:
            logger.info("STT disabled - skipping initialization")

        return True

    def stop(self):
        """Stop all components with error isolation - one failure won't block others."""
        logger.info("Stopping voice chat system...")

        from core.plugin_loader import plugin_loader as _pl
        stop_actions = [
            ("agents", lambda: hasattr(self, 'agent_manager') and self.agent_manager and self.agent_manager.shutdown()),
            ("voice components", self.stop_components),
            ("continuity scheduler", lambda: hasattr(self, 'continuity_scheduler') and self.continuity_scheduler and self.continuity_scheduler.stop()),
            ("backup scheduler", lambda: __import__('core.backup', fromlist=['backup_manager']).backup_manager.stop()),
            ("TTS server", lambda: self.tts_server_manager and self.tts_server_manager.stop()),
            ("settings watcher", settings.stop_file_watcher),
            ("prompt watcher", lambda: prompts.prompt_manager.stop_file_watcher()),
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
            from datetime import datetime
            # Try ZoneInfo-based detection first (Python 3.9+)
            import time as _time
            _tz_name = _time.tzname[0] if _time.daylight == 0 else None
            # tzname gives abbreviations like 'EST' — not IANA names. Use /etc/localtime instead.
            _tz_name = None
            # Read /etc/localtime symlink (Linux) — gives proper IANA name
            from pathlib import Path as _P
            _link = _P('/etc/localtime')
            if _link.is_symlink() and 'zoneinfo/' in str(_link.resolve()):
                _tz_name = str(_link.resolve()).split('zoneinfo/')[-1]
            if not _tz_name:
                # Last resort: try tzinfo.key (works with ZoneInfo, not with fixed-offset)
                _detected = datetime.now().astimezone().tzinfo
                _tz_name = getattr(_detected, 'key', None)
            if _tz_name and _tz_name != 'UTC':
                settings.set('USER_TIMEZONE', _tz_name, persist=True)
                logger.info(f"Auto-detected timezone: {_tz_name}")
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
        print(f"\n{CYAN_BG}{BLACK}{BOLD} ✨ SAPPHIRE IS NOW ACTIVE: {url} {RESET}\n")

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