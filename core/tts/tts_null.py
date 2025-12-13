"""Null TTS implementation when TTS is disabled"""
import logging

logger = logging.getLogger(__name__)

class NullTTS:
    """No-op TTS implementation used when TTS_ENABLED=False"""
    
    def __init__(self):
        logger.info("TTS disabled - using NullTTS")
        
    def speak(self, text):
        """No-op speak"""
        pass
        
    def stop(self):
        """No-op stop"""
        pass
        
    def set_voice(self, voice_name):
        """No-op set_voice"""
        return True
        
    def set_speed(self, speed):
        """No-op set_speed"""
        return True
        
    def set_pitch(self, pitch):
        """No-op set_pitch"""
        return True
        
    def generate_audio_data(self, text):
        """Return None - no audio generated"""
        return None