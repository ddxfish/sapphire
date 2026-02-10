"""Shared STT guard logic."""
import config
from core.stt.stt_null import NullWhisperClient


def can_transcribe(whisper_client) -> tuple[bool, str]:
    """Check if STT transcription is available.

    Returns:
        (ok, reason) - ok=True if transcription can proceed,
                       reason explains why not if ok=False
    """
    if not config.STT_ENABLED:
        return False, "Speech-to-text is disabled"
    if isinstance(whisper_client, NullWhisperClient):
        return False, "STT enabled but not initialized — downloading or loading speech model"
    return True, ""
