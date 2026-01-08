# core/audio/errors.py - Audio error classification and handling
"""
Unified audio error handling for Sapphire.
Provides actionable error messages for common audio issues.
"""

import logging

logger = logging.getLogger(__name__)


class AudioError(Exception):
    """Base exception for audio subsystem errors."""
    pass


class DeviceNotFoundError(AudioError):
    """No suitable audio device could be found."""
    pass


class DeviceConfigError(AudioError):
    """Device found but configuration failed."""
    pass


class StreamError(AudioError):
    """Error during audio streaming."""
    pass


def classify_audio_error(e: Exception) -> str:
    """
    Classify audio exception and return actionable user message.
    
    Analyzes exception type and message to provide helpful guidance
    for resolving common audio issues across platforms.
    
    Args:
        e: The exception to classify
        
    Returns:
        Human-readable error message with resolution steps
    """
    err_str = str(e).lower()
    err_type = type(e).__name__
    
    # Permission denied
    if any(x in err_str for x in ['permission denied', 'eperm', 'eacces', 'access denied']):
        return (
            "Microphone access denied. "
            "Linux: run 'sudo usermod -aG audio $USER' then logout/login. "
            "Windows: check Settings > Privacy > Microphone."
        )
    
    # Device busy
    if any(x in err_str for x in ['device or resource busy', 'ebusy', 'already in use', 'exclusive']):
        return (
            "Microphone in use by another application. "
            "Close Discord, Zoom, Teams, or other audio apps and retry."
        )
    
    # Invalid sample rate (PortAudio error -9997)
    if any(x in err_str for x in ['invalid sample rate', '-9997', 'sample rate', 'samplerate']):
        return (
            "Device rejected all sample rates. "
            "Try adding your device's native rate to AUDIO_SAMPLE_RATES in settings."
        )
    
    # Invalid channel count
    if any(x in err_str for x in ['invalid number of channels', 'channel', '-9998']):
        return (
            "Device rejected channel configuration. "
            "This is unusual - check if device supports audio input."
        )
    
    # PortAudio not initialized / not found
    if any(x in err_str for x in ['portaudio', 'not initialized', 'pa_', 'libportaudio']):
        return (
            "Audio system not ready. "
            "Linux: run 'sudo apt install libportaudio2 portaudio19-dev'. "
            "Windows: reinstall Python audio packages."
        )
    
    # Device not found
    if any(x in err_str for x in ['no such device', 'device not found', 'invalid device']):
        return (
            "Audio device not found. "
            "Check USB connection or select a different device in settings."
        )
    
    # Timeout / underrun
    if any(x in err_str for x in ['timeout', 'underrun', 'overrun', 'xrun']):
        return (
            "Audio buffer underrun. "
            "Try increasing buffer size in settings or closing other audio apps."
        )
    
    # Generic with original error
    return f"Audio error: {err_type}: {e}"