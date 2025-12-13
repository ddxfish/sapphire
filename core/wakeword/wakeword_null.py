"""Null wakeword implementation when wakeword is disabled"""
import logging

logger = logging.getLogger(__name__)

class NullAudioRecorder:
    """No-op audio recorder used when WAKE_WORD_ENABLED=False"""
    
    def __init__(self):
        logger.info("Wakeword disabled - using NullAudioRecorder")
        
    def start_recording(self):
        """No-op start_recording"""
        pass
        
    def stop_recording(self):
        """No-op stop_recording"""
        pass
        
    def get_stream(self):
        """Return None - no stream available"""
        return None
        
    def get_latest_chunk(self, duration):
        """Return empty array"""
        import numpy as np
        return np.array([], dtype=np.int16)


class NullWakeWordDetector:
    """No-op wake word detector used when WAKE_WORD_ENABLED=False"""
    
    def __init__(self, model_path):
        logger.info("Wakeword disabled - using NullWakeWordDetector")
        
    def set_audio_recorder(self, audio_recorder):
        """No-op set_audio_recorder"""
        pass
        
    def add_detection_callback(self, callback):
        """No-op add_detection_callback"""
        pass
        
    def set_system(self, system):
        """No-op set_system"""
        pass
        
    def start_listening(self):
        """No-op start_listening"""
        pass
        
    def stop_listening(self):
        """No-op stop_listening"""
        pass