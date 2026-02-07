import config

if config.STT_ENABLED:
    from .server import WhisperSTT
    from .recorder import AudioRecorder
else:
    from .stt_null import NullWhisperClient as WhisperSTT
    from .stt_null import NullAudioRecorder as AudioRecorder

__all__ = [
    'WhisperSTT',
    'AudioRecorder'
]
