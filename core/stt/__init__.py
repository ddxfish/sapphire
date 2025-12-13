import config

if config.STT_ENABLED:
    from .client import WhisperClient
    from .server import initialize_model, run_server
    from .recorder import AudioRecorder
else:
    from .stt_null import NullWhisperClient as WhisperClient
    from .stt_null import null_initialize_model as initialize_model
    from .stt_null import null_run_server as run_server
    from .stt_null import NullAudioRecorder as AudioRecorder

__all__ = [
    'WhisperClient',
    'initialize_model',
    'run_server',
    'AudioRecorder'
]