"""G.711 μ-law (PCMU) codec + telephone-band resampling for Twilio voice.

Numpy-vectorized — stdlib `audioop` is gone in Python 3.13 (Sapphire ships
cross-platform), so we roll our own. Telephony is 8kHz mono μ-law, 20ms frames
(160 samples). The conversation engine wants 16kHz int16, so we resample both
ways at the codec edge.
"""
import numpy as np

from core.audio import resample_audio

_BIAS = 0x84
_CLIP = 32635
PHONE_RATE = 8000
ENGINE_RATE = 16000
FRAME_SAMPLES = 160          # 20ms @ 8kHz
ULAW_SILENCE = 0xFF          # μ-law encoding of 0


def _build_decode_table():
    out = np.empty(256, dtype=np.int16)
    for b in range(256):
        u = ~b & 0xFF
        sign, exp, mant = u & 0x80, (u >> 4) & 0x07, u & 0x0F
        val = (((mant << 3) + _BIAS) << exp) - _BIAS
        out[b] = -val if sign else val
    return out


_DECODE = _build_decode_table()


def ulaw_decode(ulaw_bytes):
    """μ-law bytes -> int16 PCM @ 8kHz."""
    return _DECODE[np.frombuffer(ulaw_bytes, dtype=np.uint8)]


def ulaw_encode(pcm16):
    """int16 PCM @ 8kHz -> μ-law bytes (vectorized G.711)."""
    pcm = pcm16.astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0).astype(np.int32)
    mag = np.minimum(np.abs(pcm), _CLIP) + _BIAS
    exponent = np.clip(np.floor(np.log2(np.maximum(mag, 1))).astype(np.int32) - 7, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa)
    return (ulaw & 0xFF).astype(np.uint8).tobytes()


def phone_to_engine(ulaw_bytes):
    """Inbound: μ-law 8k -> int16 16k for the VAD/STT pipeline."""
    pcm8 = ulaw_decode(ulaw_bytes)
    return resample_audio(pcm8, PHONE_RATE, ENGINE_RATE)


def engine_to_phone_frames(pcm16_any_rate, src_rate):
    """Outbound: TTS int16 @ src_rate -> list of 160-sample μ-law frames @ 8k.
    Returns whole 20ms frames; the trailing partial (<160) is dropped (next
    chunk's audio picks up ~contiguously; a 20ms seam is inaudible)."""
    pcm8 = resample_audio(pcm16_any_rate.astype(np.int16), src_rate, PHONE_RATE)
    n = len(pcm8) // FRAME_SAMPLES
    return [ulaw_encode(pcm8[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES])
            for i in range(n)]


SILENCE_FRAME = bytes([ULAW_SILENCE]) * FRAME_SAMPLES
