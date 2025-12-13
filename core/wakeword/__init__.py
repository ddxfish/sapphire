import config

if config.WAKE_WORD_ENABLED:
    from .audio_recorder import AudioRecorder
    from .wake_detector import WakeWordDetector
else:
    from .wakeword_null import NullAudioRecorder as AudioRecorder
    from .wakeword_null import NullWakeWordDetector as WakeWordDetector

__all__ = ['AudioRecorder', 'WakeWordDetector']