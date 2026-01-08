# core/audio - Unified audio subsystem for Sapphire
#
# Provides shared device detection, configuration, and utilities
# used by both STT recorder and wakeword detection.

from .device_manager import DeviceManager, get_device_manager
from .errors import AudioError, classify_audio_error
from .utils import convert_to_mono, resample_audio, get_temp_dir

__all__ = [
    'DeviceManager',
    'get_device_manager', 
    'AudioError',
    'classify_audio_error',
    'convert_to_mono',
    'resample_audio',
    'get_temp_dir',
]