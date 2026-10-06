"""Null STT implementation when STT is disabled."""
import logging
from typing import Optional

from core.stt.providers.base import BaseSTTProvider

logger = logging.getLogger(__name__)


class NullWhisperClient(BaseSTTProvider):
    """No-op STT provider used when STT is disabled."""

    def __init__(self):
        logger.info("STT disabled - using NullWhisperClient")

    def _transcribe_impl(self, audio_file: str) -> Optional[str]:
        return ""

    def is_available(self) -> bool:
        return False


class NullAudioRecorder:
    """No-op audio recorder used when STT is disabled."""

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
        pass

    def _cleanup_pyaudio(self):
        pass

    def _find_input_device(self):
        return None, 16000

    def _test_device(self, device_index: int, sample_rate: int) -> bool:
        return False

    def _update_threshold(self, level: float) -> None:
        pass

    def _is_silent(self, audio_data) -> bool:
        return True

    def _open_stream(self) -> bool:
        return False

    def record_audio(self, max_seconds=None, no_speech_timeout=None) -> Optional[str]:
        return None

    def stop(self) -> None:
        pass

    def __del__(self):
        pass
