"""Decode Sapphire TTS audio (stream chunks and whole blobs) for Discord voice playback.

One decoder, in-process: libsndfile via soundfile reads every container core
TTS emits (WAV, OGG Vorbis/Opus, MP3, FLAC — verified 2026-09-13) straight from
bytes. The batch "speak" lane used to write the blob to a temp file and shell
out to ffmpeg (the plugin's only ffmpeg site, H17); it now rides this decoder
into the same 48 kHz stereo PCM queue the conversational lane streams into.
"""

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
    mono = mono[:count * DISCORD_SAMPLE_WIDTH]
    try:
        import numpy as np
    except ImportError:
        samples = struct.unpack(f'<{count}h', mono)
        stereo = []
        for sample in samples:
            stereo.extend((sample, sample))
        return struct.pack(f'<{len(stereo)}h', *stereo)
    # Whole batch blobs come through here now (H17): a 30 s reply is ~1.4 M
    # samples — the struct loop cost ~300 ms on the daemon loop, np.repeat ~2 ms.
    return np.repeat(np.frombuffer(mono, dtype='<i2'), 2).tobytes()


def pcm_to_discord_stereo(pcm_mono: bytes, *, sample_rate: int) -> bytes:
    """Normalize mono int16 PCM to 48 kHz stereo for Discord voice output."""
    if not pcm_mono:
        return b''
    if sample_rate != DISCORD_SAMPLE_RATE:
        pcm_mono = _resample_int16(pcm_mono, sample_rate, DISCORD_SAMPLE_RATE)
    return _mono_to_stereo_int16(pcm_mono)


def decode_tts_audio(raw: bytes, *, label: str = 'audio') -> bytes:
    """Decode one encoded TTS blob (any libsndfile container) into 48 kHz stereo PCM.

    Returns b'' on empty input, missing decoder, or undecodable bytes — the
    caller reports an honest error instead of playing silence.
    """
    if not raw:
        return b''
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        logger.warning('discord_tts_chunks: soundfile/numpy unavailable')
        return b''
    try:
        data, sample_rate = sf.read(io.BytesIO(raw), dtype='float32')
        if getattr(data, 'size', 0) == 0:
            return b''
        if data.ndim > 1:
            data = data.mean(axis=1)
        pcm16 = (np.clip(data, -1.0, 1.0) * 32767.0).astype('<i2')
        return pcm_to_discord_stereo(pcm16.tobytes(), sample_rate=int(sample_rate))
    except Exception as exc:
        logger.warning('discord_tts_chunks: decode failed (%s): %s', label, exc)
        return b''


def decode_tts_chunk(chunk: dict | None) -> bytes:
    """Decode a core `tts_chunk` event dict into 48 kHz stereo PCM bytes."""
    if not chunk or not chunk.get('audio_b64'):
        return b''
    try:
        raw = base64.b64decode(chunk['audio_b64'])
    except Exception as exc:
        logger.warning('discord_tts_chunks: bad audio_b64: %s', exc)
        return b''
    return decode_tts_audio(raw, label=str(chunk.get('content_type') or 'chunk'))
