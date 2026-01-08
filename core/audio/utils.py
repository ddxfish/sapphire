# core/audio/utils.py - Audio processing utilities
"""
Shared audio processing functions for Sapphire.
Handles format conversion, resampling, and file operations.
"""

import numpy as np
import tempfile
import os
import logging

logger = logging.getLogger(__name__)


def get_temp_dir() -> str:
    """
    Get optimal temp directory for audio files.
    
    Prefers /dev/shm (Linux RAM disk) for speed when available,
    falls back to system temp directory.
    
    Returns:
        Path to temp directory
    """
    shm = '/dev/shm'
    if os.path.exists(shm) and os.access(shm, os.W_OK):
        return shm
    return tempfile.gettempdir()


def convert_to_mono(audio_data: np.ndarray) -> np.ndarray:
    """
    Convert stereo audio to mono by averaging channels.
    
    Args:
        audio_data: Audio array, either 1D (mono) or 2D (stereo)
        
    Returns:
        1D mono audio array as int16
    """
    if len(audio_data.shape) > 1 and audio_data.shape[1] == 2:
        # Average left and right channels
        # Use int32 intermediate to avoid overflow
        return ((audio_data[:, 0].astype(np.int32) + 
                 audio_data[:, 1].astype(np.int32)) // 2).astype(np.int16)
    return audio_data.flatten().astype(np.int16)


def resample_audio(audio_data: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """
    Resample audio from one sample rate to another.
    
    Uses linear interpolation - simple and fast enough for 
    speech processing (STT and wake word detection).
    
    Args:
        audio_data: Input audio samples
        from_rate: Source sample rate in Hz
        to_rate: Target sample rate in Hz
        
    Returns:
        Resampled audio as int16 array
    """
    if from_rate == to_rate:
        return audio_data
    
    ratio = to_rate / from_rate
    new_length = int(len(audio_data) * ratio)
    
    if new_length == 0:
        return np.array([], dtype=np.int16)
    
    # Linear interpolation
    old_indices = np.arange(len(audio_data))
    new_indices = np.linspace(0, len(audio_data) - 1, new_length)
    resampled = np.interp(new_indices, old_indices, audio_data.astype(np.float32))
    
    return resampled.astype(np.int16)


def calculate_rms(audio_data: np.ndarray) -> float:
    """
    Calculate RMS (root mean square) level of audio.
    
    Args:
        audio_data: Audio samples (int16 or float)
        
    Returns:
        RMS level normalized to 0.0-1.0 range
    """
    if len(audio_data) == 0:
        return 0.0
    
    # Normalize to float range
    if audio_data.dtype == np.int16:
        samples = audio_data.astype(np.float32) / 32768.0
    else:
        samples = audio_data.astype(np.float32)
    
    return float(np.sqrt(np.mean(samples ** 2)))


def calculate_peak(audio_data: np.ndarray) -> float:
    """
    Calculate peak level of audio.
    
    Args:
        audio_data: Audio samples (int16 or float)
        
    Returns:
        Peak level normalized to 0.0-1.0 range
    """
    if len(audio_data) == 0:
        return 0.0
    
    # Normalize to float range
    if audio_data.dtype == np.int16:
        samples = audio_data.astype(np.float32) / 32768.0
    else:
        samples = audio_data.astype(np.float32)
    
    return float(np.max(np.abs(samples)))