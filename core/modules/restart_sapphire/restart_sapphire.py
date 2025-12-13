import logging
import subprocess
import time
import threading

logger = logging.getLogger(__name__)

class RestartSapphire:
    """Module to restart the Sapphire voice assistant system."""
    
    def __init__(self):
        self.keyword_match = None
        self.full_command = None
        self.voice_chat_system = None
        
    def process(self, user_input):
        """Process restart command with simple confirmation."""
        logger.info("Restart requested, executing after delay...")
        
        # Start restart in background thread
        threading.Thread(target=self._delayed_restart, daemon=True).start()
        return "Restarting Sapphire now. Goodbye!"
    
    def _delayed_restart(self):
        """Execute restart command after a delay to allow TTS to finish."""
        time.sleep(3)  # Wait for TTS to finish
        try:
            subprocess.run(["/usr/bin/systemctl", "--user", "restart", "sapphire"])
        except Exception as e:
            logger.error(f"Error in restart command: {e}")
        
    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info("Restart module attached to system")