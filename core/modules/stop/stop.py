import logging

logger = logging.getLogger(__name__)

class Stop:
    """Stop TTS playback module."""
    
    def __init__(self):
        self.voice_chat_system = None
    
    def process(self, user_input, llm_client=None):
        """Process stop command."""
        logger.info("Stop command received")
        
        if self.voice_chat_system and hasattr(self.voice_chat_system, 'tts'):
            self.voice_chat_system.tts.stop()
            logger.info("TTS playback stopped")
            # Return a non-empty string for success
            return "Stopped playback"
        
        # Default return
        return "Stopped"
        
    def attach_system(self, voice_chat_system):
        """Attach voice chat system."""
        self.voice_chat_system = voice_chat_system
        logger.info("Stop module attached")