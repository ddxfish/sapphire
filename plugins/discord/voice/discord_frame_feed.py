"""48 kHz Discord stereo → 16 kHz mono engine frames for conversation VAD."""

from __future__ import annotations

import struct
from typing import Callable

from plugins.discord.transport.discord_audio import (
    DISCORD_CHANNELS,
    DISCORD_SAMPLE_WIDTH,
    WHISPER_SAMPLE_RATE,
    _resample_int16,
    _stereo_to_mono_int16,
)

ENGINE_FRAME_SAMPLES = 512
ENGINE_FRAME_BYTES = ENGINE_FRAME_SAMPLES * DISCORD_SAMPLE_WIDTH


class DiscordFrameFeed:
    """Accumulate resampled mono PCM and emit 512-sample int16 frames."""

    def __init__(self, push_frame: Callable[..., None]):
        self._push_frame = push_frame
        self._buf = b''

    def push_stereo_pcm(
        self,
        pcm_stereo: bytes,
        *,
        sample_rate: int = 48000,
        is_speech: bool | None = None,
    ) -> None:
        if not pcm_stereo:
            return
        mono = _stereo_to_mono_int16(pcm_stereo)
        if sample_rate != WHISPER_SAMPLE_RATE:
            mono = _resample_int16(mono, sample_rate, WHISPER_SAMPLE_RATE)
        if not mono:
            return
        self._buf += mono
        while len(self._buf) >= ENGINE_FRAME_BYTES:
            frame = self._buf[:ENGINE_FRAME_BYTES]
            self._buf = self._buf[ENGINE_FRAME_BYTES:]
            if is_speech is None:
                self._push_frame(frame)
            else:
                self._push_frame(frame, is_speech=bool(is_speech))

    def reset(self) -> None:
        self._buf = b''


def stereo_frame_rms(pcm_stereo: bytes) -> float:
    if not pcm_stereo:
        return 0.0
    count = len(pcm_stereo) // DISCORD_SAMPLE_WIDTH
    if count < 1:
        return 0.0
    samples = struct.unpack(f'<{count}h', pcm_stereo[: count * DISCORD_SAMPLE_WIDTH])
    if DISCORD_CHANNELS == 2:
        mono = [abs(samples[i]) for i in range(0, len(samples), 2)]
    else:
        mono = [abs(value) for value in samples]
    if not mono:
        return 0.0
    return sum(value * value for value in mono) / len(mono)
