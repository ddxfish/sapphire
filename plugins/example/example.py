import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Example:
    """Simple example module that reports current time."""
    
    def __init__(self):
        """Initialize the example module."""
        self.keyword_match = None
        self.full_command = None
        self.voice_chat_system = None
    
    def process(self, user_input, llm_client=None):
        """Process the example command - report current time."""
        logger.info(f"Example module processing: '{user_input}'")
        
        # Get current time
        current_time = datetime.now().strftime("%H:%M")
        
        return f"Example received at {current_time}"
        
    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info(f"Example module attached to system: {type(voice_chat_system).__name__}")