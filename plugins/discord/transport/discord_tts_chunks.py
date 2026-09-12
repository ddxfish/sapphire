"""Decode Sapphire TTS stream chunks for Discord voice playback."""

from __future__ import annotations

import base64
import io
import logging
import struct

from plugins.discord.transport.discord_audio import (
    DISCORD_CHANNELS,
    DISCORD_SAMPLE_RATE,
    DISCORD_SAMPLE_WIDTH,
    _resample_int16,
)

logger = logging.getLogger(__name__)


def _mono_to_stereo_int16(mono: bytes) -> bytes:
    if not mono:
        return b''
    count = len(mono) // DISCORD_SAMPLE_WIDTH
    if count < 1:
        return b''
    samples = struct.unpack(f'<{count}h', mono)
    stereo = []
    for sample in samples:
        stereo.extend((sample, sample))
    return struct.pack(f'<{len(stereo)}h', *stereo)


def pcm_to_discord_stereo(pcm_mono: bytes, *, sample_rate: int) -> bytes:
    """Normalize mono int16 PCM to 48 kHz stereo for Discord voice output."""
    if not pcm_mono:
        return b''
    if sample_rate != DISCORD_SAMPLE_RATE:
        pcm_mono = _resample_int16(pcm_mono, sample_rate, DISCORD_SAMPLE_RATE)
    return _mono_to_stereo_int16(pcm_mono)


def decode_tts_chunk(chunk: dict | None) -> bytes:
    """Decode a core `tts_chunk` event dict into 48 kHz stereo PCM bytes."""
    if not chunk or not chunk.get('audio_b64'):
        return b''
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        logger.warning('discord_tts_chunks: soundfile/numpy unavailable')
        return b''
    try:
        raw = base64.b64decode(chunk['audio_b64'])
        data, sample_rate = sf.read(io.BytesIO(raw), dtype='float32')
        if getattr(data, 'size', 0) == 0:
            return b''
        if data.ndim > 1:
            data = data.mean(axis=1)
        pcm16 = (np.clip(data, -1.0, 1.0) * 32767.0).astype('<i2')
        return pcm_to_discord_stereo(pcm16.tobytes(), sample_rate=int(sample_rate))
    except Exception as exc:
        content_type = chunk.get('content_type') if isinstance(chunk, dict) else None
        logger.warning('discord_tts_chunks: decode failed (%s): %s', content_type, exc)
        return b''
