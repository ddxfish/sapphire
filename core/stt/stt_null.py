"""Null STT implementation when STT is disabled"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class NullWhisperClient:
    """No-op Whisper client used when STT_ENABLED=False"""
    
    def __init__(self, server_url: str = None):
        logger.info("STT disabled - using NullWhisperClient")
        self.server_url = server_url
        
    def transcribe_file(self, audio_file: str) -> Optional[str]:
        """Return empty string - no transcription"""
        return ""


class NullAudioRecorder:
    """No-op audio recorder used when STT_ENABLED=False"""
    
    def __init__(self):
        logger.info("STT disabled - using NullAudioRecorder")
        self.format = None
        self.audio = None
        self.level_history = []
        self.adaptive_threshold = 0
        self._stream = None
        self._recording = False
        self.device_index = None
        self.rate = 16000
        
    def _init_pyaudio(self):
        """No-op"""
        pass
        
    def _cleanup_pyaudio(self):
        """No-op"""
        pass
        
    def _find_input_device(self):
        """No-op - return dummy values"""
        return None, 16000
        
    def _test_device(self, device_index: int, sample_rate: int) -> bool:
        """No-op"""
        return False
        
    def _update_threshold(self, level: float) -> None:
        """No-op"""
        pass
        
    def _is_silent(self, audio_data) -> bool:
        """No-op"""
        return True
        
    def _open_stream(self) -> bool:
        """No-op"""
        return False
        
    def record_audio(self) -> Optional[str]:
        """Return None - no recording"""
        return None
        
    def stop(self) -> None:
        """No-op"""
        pass
        
    def __del__(self):
        """No-op"""
        pass


def null_initialize_model(model_size=None, language=None):
    """No-op model initialization - returns False immediately"""
    logger.info("STT disabled - skipping model initialization")
    return False


def null_run_server(host=None, port=None):
    """No-op server - does nothing"""
    logger.info("STT disabled - not starting server")
    pass