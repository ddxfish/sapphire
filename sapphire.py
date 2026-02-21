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
from core.setup import ensure_prompt_files, ensure_chat_defaults, ensure_story_presets
ensure_prompt_files()
ensure_chat_defaults()
ensure_story_presets()

# Run data migrations (e.g. persona -> character rename)
from core.migration import run_all as run_migrations
run_migrations()

# Wrap all further imports to catch errors
try:
    from core.stt import WhisperSTT
    from core.stt import AudioRecorder as WhisperRecorder
    from core.stt.stt_null import NullWhisperClient
    from core.chat import LLMChat, ConversationHistory
    from core.api_fastapi import app, set_system
    from core.settings_manager import settings
    from core.ssl_utils import get_ssl_context
    import config
    import string
    import re
    import uvicorn
except Exception as e:
    logger.critical(f"FATAL: Import error during startup: {e}", exc_info=True)
    sys.exit(1)

from core.process_manager import ProcessManager, kill_process_on_port

from core.modules.system import prompts
from core.modules.system.toolsets import toolset_manager


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

        # Start TTS server if enabled
        self.tts_server_manager = None
        if config.TTS_ENABLED:
            tts_script = base_dir / "core" / "tts" / "tts_server.py"
            if tts_script.exists():
                # Kill any orphaned TTS process from previous crash
                tts_port = getattr(config, 'TTS_SERVER_PORT', 5012)
                if kill_process_on_port(tts_port):
                    logger.info(f"Cleaned up orphaned TTS process on port {tts_port}")

                logger.info("Starting Kokoro TTS server...")
                self.tts_server_manager = ProcessManager(
                    script_path=tts_script,
                    log_name="kokoro",
                    base_dir=base_dir
                )
                self.tts_server_manager.start()
                self.tts_server_manager.monitor_and_restart(check_interval=10)
                time.sleep(3)  # Let model load
            else:
                logger.warning(f"TTS enabled but server.py not found at {tts_script}")

        # Initialize TTS client
        logger.info("Initializing TTS client")
        if config.TTS_ENABLED:
            try:
                from core.tts.tts_client import TTSClient
                self.tts = TTSClient()
                logger.info("TTS client initialized")
            except ImportError as e:
                from core.tts.tts_null import NullTTS
                self.tts = NullTTS()
                logger.error(f"TTS import failed, using NullTTS: {e}")
        else:
            from core.tts.tts_null import NullTTS
            self.tts = NullTTS()

        self.llm_chat = LLMChat(self.history, system=self)
        self._prime_default_prompt()
        self._apply_initial_chat_settings()
        self._init_modules()
        self.init_components()
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
            from functions import knowledge
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
                        prompt_name = defaults.get('prompt', 'default')
                else:
                    prompt_name = 'default'
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
                self.tts.set_voice(settings["voice"])
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

    def _init_modules(self):
        try:
            for module_name in self.llm_chat.module_loader.module_instances:
                module = self.llm_chat.module_loader.module_instances[module_name]
                if hasattr(module, 'attach_system'):
                    module.attach_system(self)
                    logger.info(f"Attached system to module: {module_name}")
        except Exception as e:
            logger.error(f"Error initializing modules: {e}")

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

    def toggle_stt(self, enabled: bool):
        """Hot-swap STT components at runtime."""
        if enabled:
            # Already real? Nothing to do
            if not isinstance(self.whisper_client, NullWhisperClient):
                logger.info("STT already initialized")
                return True

            # Cold start: import real WhisperSTT and AudioRecorder directly
            # (module-level imports may be Null if STT was disabled at import time)
            try:
                from core.stt.server import WhisperSTT as RealWhisperSTT
                from core.stt.recorder import AudioRecorder as RealAudioRecorder
                logger.info(f"Hot-loading {config.STT_ENGINE} model...")
                self.whisper_client = RealWhisperSTT()
                self.whisper_recorder = RealAudioRecorder()
                logger.info("STT hot-started successfully")
                return True
            except ImportError as e:
                logger.error(f"STT not installed: {e}")
                self.whisper_client = NullWhisperClient()
                return False
            except Exception as e:
                logger.error(f"STT hot-start failed: {e}")
                self.whisper_client = NullWhisperClient()
                return False
        else:
            # Swap to null (free model memory)
            from core.stt.stt_null import NullAudioRecorder
            if not isinstance(self.whisper_client, NullWhisperClient):
                logger.info("STT stopped, unloading model")
                self.whisper_client = NullWhisperClient()
                self.whisper_recorder = NullAudioRecorder()
            return True

    def toggle_tts(self, enabled: bool):
        """Hot-swap TTS server + client at runtime."""
        from core.tts.tts_null import NullTTS
        base_dir = Path(__file__).parent.resolve()

        if enabled:
            # Already running?
            if self.tts_server_manager and self.tts_server_manager.is_running():
                logger.info("TTS server already running")
                return True

            try:
                tts_script = base_dir / "core" / "tts" / "tts_server.py"
                tts_port = getattr(config, 'TTS_SERVER_PORT', 5012)

                # Kill any orphaned process on the port
                if kill_process_on_port(tts_port):
                    logger.info(f"Cleaned up orphaned TTS process on port {tts_port}")

                logger.info("Hot-starting TTS server...")
                self.tts_server_manager = ProcessManager(
                    script_path=tts_script,
                    log_name="kokoro",
                    base_dir=base_dir
                )
                self.tts_server_manager.start()
                self.tts_server_manager.monitor_and_restart(check_interval=10)
                time.sleep(3)  # Let model load

                from core.tts.tts_client import TTSClient
                self.tts = TTSClient()
                logger.info("TTS hot-started successfully")
                return True
            except Exception as e:
                logger.error(f"TTS hot-start failed: {e}")
                self.tts = NullTTS()
                return False
        else:
            # Stop server + swap to null
            if self.tts_server_manager:
                self.tts_server_manager.stop()
                self.tts_server_manager = None
                logger.info("TTS server stopped")
            if not isinstance(self.tts, NullTTS):
                self.tts = NullTTS()
            return True

    def reset_chat(self):
        self.llm_chat.reset()
        self.tts.speak("reset.")
        logger.info("Chat history reset")
        
    def speak_error(self, error_type):
        error_messages = {
            'file': "File creation error",
            'speech': "No speech heard",
            'recording': "Recording error",
            'processing': "Processing error"
        }
        self.tts.speak(error_messages.get(error_type, "Error"))

    def _clean_text(self, text: str) -> str:
        text = text.lower()
        text = text.translate(str.maketrans('', '', string.punctuation))
        return text.strip()

    def process_llm_query(self, query, skip_tts=False):
        if not self._processing_lock.acquire(timeout=0.5):
            logger.warning("process_llm_query: already processing, skipping duplicate")
            return None
        try:
            clean_query = self._clean_text(query)

            if clean_query == "stop":
                logger.info("Stop command detected, stopping TTS")
                self.tts.stop()
                return

            if clean_query == "reset":
                logger.info("Reset command detected, resetting chat")
                self.reset_chat()
                return

            response_text = self.llm_chat.chat(query)

            if response_text:
                if not skip_tts:
                    self.tts.speak(response_text)
                return response_text
            else:
                logger.warning("Empty response from processing")

        except Exception as e:
            logger.error(f"Error in process_llm_query: {e}")
            if not skip_tts:
                self.speak_error('processing')
        finally:
            self._processing_lock.release()

        return None

    def start_background_services(self):
        if config.STT_ENABLED:
            logger.info(f"Initializing {config.STT_ENGINE} model...")
            try:
                from core.stt.server import WhisperSTT as RealWhisperSTT
                from core.stt.recorder import AudioRecorder as RealAudioRecorder
                self.whisper_client = RealWhisperSTT()
                self.whisper_recorder = RealAudioRecorder()
            except ImportError as e:
                logger.error(f"STT not installed: {e}")
                return False
            except RuntimeError as e:
                logger.error(f"Failed to initialize {config.STT_ENGINE}: {e}")
                return False
        else:
            logger.info("STT disabled - skipping model initialization")

        return True

    def stop(self):
        """Stop all components with error isolation - one failure won't block others."""
        logger.info("Stopping voice chat system...")

        stop_actions = [
            ("voice components", self.stop_components),
            ("continuity scheduler", lambda: hasattr(self, 'continuity_scheduler') and self.continuity_scheduler and self.continuity_scheduler.stop()),
            ("TTS server", lambda: self.tts_server_manager and self.tts_server_manager.stop()),
            ("settings watcher", settings.stop_file_watcher),
            ("prompt watcher", lambda: prompts.prompt_manager.stop_file_watcher()),
            ("toolset watcher", toolset_manager.stop_file_watcher),
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
        from core.modules.continuity import ContinuityScheduler, ContinuityExecutor
        continuity_executor = ContinuityExecutor(voice_chat)
        continuity_scheduler = ContinuityScheduler(voice_chat, continuity_executor)
        voice_chat.continuity_scheduler = continuity_scheduler  # Attach for stop() and API routes
        continuity_scheduler.start()
        logger.info("Continuity scheduler started")

        settings.start_file_watcher()

        from core.modules.system import prompts
        prompts.prompt_manager.start_file_watcher()
        logger.info("Prompt file watcher started")

        toolset_manager.start_file_watcher()
        logger.info("Toolset file watcher started")

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
            time.sleep(0.5)

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