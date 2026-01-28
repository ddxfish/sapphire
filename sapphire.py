# sapphire.py - Sapphire Voice Assistant Core Application
import os
import sys
import time
import signal
import threading
import subprocess
from pathlib import Path

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
from core.setup import ensure_prompt_files, ensure_chat_defaults, ensure_state_presets
ensure_prompt_files()
ensure_chat_defaults()
ensure_state_presets()

# Wrap all further imports to catch errors
try:
    from core.stt import initialize_model, run_server, WhisperClient
    from core.stt import AudioRecorder as WhisperRecorder
    from core.chat import LLMChat, ConversationHistory
    from core.api import app, create_api
    from core.settings_api import create_settings_api
    from core.settings_manager import settings
    from core.event_handler import EventScheduler
    import config
    import string
    import re
except Exception as e:
    logger.critical(f"FATAL: Import error during startup: {e}", exc_info=True)
    sys.exit(1)

from core.process_manager import ProcessManager

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
        
        self.history = ConversationHistory(max_history=config.LLM_MAX_HISTORY)

        base_dir = Path(__file__).parent.resolve()
        
        # Start web interface
        self.web_interface_manager = ProcessManager(
            script_path=base_dir / "interfaces" / "web" / "web_interface.py",
            log_name="web_interface",
            base_dir=base_dir
        )
        self.web_interface_manager.start()
        
        # Start TTS server if enabled
        self.tts_server_manager = None
        if config.TTS_ENABLED:
            tts_script = base_dir / "core" / "tts" / "tts_server.py"
            if tts_script.exists():
                logger.info("Starting Kokoro TTS server...")
                self.tts_server_manager = ProcessManager(
                    script_path=tts_script,
                    log_name="kokoro_tts",
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
        self.event_scheduler = EventScheduler(self)
        self._init_modules()
        self.init_components()
        
        logger.info(f"System init took: {(time.time() - start_time)*1000:.1f}ms")

    def _prime_default_prompt(self):
        try:
            # Read default prompt name from chat_defaults.json
            import json
            from pathlib import Path
            chat_defaults_path = Path(__file__).parent / "user" / "settings" / "chat_defaults.json"
            
            prompt_name = 'default'  # fallback if file missing
            if chat_defaults_path.exists():
                with open(chat_defaults_path, 'r', encoding='utf-8') as f:

                    defaults = json.load(f)
                    prompt_name = defaults.get('prompt', 'default')
            
            prompt_details = prompts.get_prompt(prompt_name)
            if not prompt_details:
                raise ValueError(f"Prompt '{prompt_name}' not found")
            
            content = prompt_details['content'] if isinstance(prompt_details, dict) else str(prompt_details)
            self.llm_chat.set_system_prompt(content)
            prompts.set_active_preset_name(prompt_name)
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
            
            if "prompt" in settings:
                from core.modules.system import prompts
                prompt_name = settings["prompt"]
                prompt_data = prompts.get_prompt(prompt_name)
                if prompt_data:
                    content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
                    if content:
                        self.llm_chat.set_system_prompt(content)
                        prompts.set_active_preset_name(prompt_name)
                        
                        if hasattr(prompts.prompt_manager, 'scenario_presets') and prompt_name in prompts.prompt_manager.scenario_presets:
                            prompts.apply_scenario(prompt_name)
            
            if "ability" in settings:
                ability_name = settings["ability"]
                self.llm_chat.function_manager.update_enabled_functions([ability_name])
                logger.info(f"Applied ability on startup: {ability_name}")
            
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
        self.whisper_client = WhisperClient(server_url=config.STT_SERVER_URL)

    def stop_components(self):
        if hasattr(self, 'wake_detector') and self.wake_detector:
            self.wake_detector.stop_listening()
        if hasattr(self, 'wake_word_recorder') and self.wake_word_recorder:
            self.wake_word_recorder.stop_recording()

    def start_voice_components(self):
        self.wake_word_recorder.start_recording()
        self.wake_detector.start_listening()
        logger.info("Voice components are running.")

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
                
        return None

    def start_background_services(self):
        if config.STT_ENABLED:
            logger.info(f"Initializing {config.STT_ENGINE} model...")
            if not initialize_model():
                logger.error(f"Failed to initialize {config.STT_ENGINE} model")
                return False

            logger.info("Starting speech-to-text server...")
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()
            time.sleep(2)
        else:
            logger.info("STT disabled - skipping model and server initialization")

        logger.info("Starting event scheduler...")
        event_thread = threading.Thread(target=self.run_event_scheduler, daemon=True)
        event_thread.start()
        
        return True

    def run_event_scheduler(self):
        logger.info("Background event scheduler started.")
        while True:
            self.event_scheduler.check_and_trigger_events()
            time.sleep(1.0)

    def stop(self):
        """Stop all components with error isolation - one failure won't block others."""
        logger.info("Stopping voice chat system...")
        
        stop_actions = [
            ("voice components", self.stop_components),
            ("continuity scheduler", lambda: hasattr(self, 'continuity_scheduler') and self.continuity_scheduler and self.continuity_scheduler.stop()),
            ("web interface", lambda: self.web_interface_manager and self.web_interface_manager.stop()),
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
    
    print("Starting API server")
    def run_api_server():
        try:
            app.run(host=config.API_HOST, port=config.API_PORT, debug=False, threaded=True)
        except Exception as e:
            logger.error(f"API server crashed: {e}", exc_info=True)

    try:
        if not voice_chat.start_background_services():
            logger.critical("Essential background services failed to start.")
            voice_chat.stop()
            return 1

        voice_chat.start_voice_components()
        api_blueprint = create_api(voice_chat, restart_callback=request_restart, shutdown_callback=request_shutdown)
        app.register_blueprint(api_blueprint)
        
        settings_api_blueprint = create_settings_api()
        app.register_blueprint(settings_api_blueprint, url_prefix='/api')
        
        from core.modules.system.prompts_api import create_prompts_api
        prompts_api_blueprint = create_prompts_api(voice_chat)
        app.register_blueprint(prompts_api_blueprint)
        
        from core.abilities_api import create_abilities_api
        abilities_api_blueprint = create_abilities_api(voice_chat)
        app.register_blueprint(abilities_api_blueprint, url_prefix='/api')
        
        from core.modules.system.spices_api import create_spices_api
        spices_api_blueprint = create_spices_api()
        app.register_blueprint(spices_api_blueprint)
        
        # Continuity - scheduled autonomous tasks
        from core.modules.continuity import ContinuityScheduler, ContinuityExecutor
        from core.modules.continuity.continuity_api import create_continuity_api
        continuity_executor = ContinuityExecutor(voice_chat)
        continuity_scheduler = ContinuityScheduler(voice_chat, continuity_executor)
        voice_chat.continuity_scheduler = continuity_scheduler  # Attach for stop()
        continuity_api_blueprint = create_continuity_api(continuity_scheduler)
        app.register_blueprint(continuity_api_blueprint, url_prefix='/api/continuity')
        continuity_scheduler.start()
        logger.info("Continuity scheduler started")
        
        settings.start_file_watcher()

        from core.modules.system import prompts
        prompts.prompt_manager.start_file_watcher()
        logger.info("Prompt file watcher started")
        
        toolset_manager.start_file_watcher()
        logger.info("Toolset file watcher started")
        
        api_thread = threading.Thread(target=run_api_server, daemon=True)
        api_thread.start()
        
        logger.info(f"Sapphire is running. API is live.")
        
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
            
    finally:
        voice_chat.stop()
    
    return exit_code


if __name__ == "__main__":
    # Allow direct execution for debugging
    sys.exit(run())