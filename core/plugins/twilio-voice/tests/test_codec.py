"""G.711 μ-law codec tests (twilio-voice).

Pure numpy functions — no SIP, no network. Covers the round-trip fidelity,
μ-law silence encoding, and the 20ms framing (including the intentional
trailing-sub-frame drop that Scout 3 flagged as L3 — documented here so a future
change to that behavior is a conscious one, not a silent regression).
"""
import importlib.util
from pathlib import Path

import numpy as np

_CODEC_PATH = Path(__file__).resolve().parent.parent / "codec.py"


def _load():
    spec = importlib.util.spec_from_file_location("twilio_codec_undertest", _CODEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


codec = _load()


# ── μ-law silence ────────────────────────────────────────────────────────────

def test_silence_encodes_to_0xff():
    assert codec.ulaw_encode(np.array([0], dtype=np.int16)) == bytes([codec.ULAW_SILENCE])


def test_silence_decodes_to_zero():
    assert int(codec.ulaw_decode(bytes([codec.ULAW_SILENCE]))[0]) == 0


def test_silence_frame_is_160_bytes_of_silence():
    assert codec.SILENCE_FRAME == bytes([codec.ULAW_SILENCE]) * codec.FRAME_SAMPLES
    assert len(codec.SILENCE_FRAME) == 160


# ── encode / decode round-trip ───────────────────────────────────────────────

def test_encode_output_length_and_dtype():
    pcm = np.zeros(160, dtype=np.int16)
    enc = codec.ulaw_encode(pcm)
    assert isinstance(enc, bytes) and len(enc) == 160
    dec = codec.ulaw_decode(enc)
    assert dec.dtype == np.int16 and len(dec) == 160


def test_roundtrip_preserves_sign():
    pcm = np.array([0, 100, -100, 1000, -1000, 8000, -8000, 30000, -30000], dtype=np.int16)
    out = codec.ulaw_decode(codec.ulaw_encode(pcm))
    assert np.array_equal(np.sign(out), np.sign(pcm))


def test_roundtrip_bounded_quantization_error():
    """μ-law is ~8-bit log PCM — decode(encode(x)) stays within ~13% relative
    error on non-tiny samples (the log companding guarantee)."""
    pcm = np.array([600, -600, 1500, -1500, 5000, -5000, 20000, -20000, 32000], dtype=np.int16)
    out = codec.ulaw_decode(codec.ulaw_encode(pcm)).astype(float)
    rel = np.abs(out - pcm) / np.abs(pcm)
    assert np.max(rel) < 0.13


# ── outbound framing (engine_to_phone_frames) ────────────────────────────────

def test_frames_are_whole_20ms_only():
    """400 samples @8k → 2 whole 160-sample frames; the trailing 80 are dropped."""
    frames = codec.engine_to_phone_frames(np.zeros(400, dtype=np.int16), codec.PHONE_RATE)
    assert len(frames) == 2
    assert all(len(f) == codec.FRAME_SAMPLES for f in frames)


def test_frames_exact_multiple_no_drop():
    frames = codec.engine_to_phone_frames(np.zeros(320, dtype=np.int16), codec.PHONE_RATE)
    assert len(frames) == 2


def test_frames_short_input_yields_nothing():
    """A sub-frame chunk (<160 @8k) produces no frames — the L3 sub-frame drop."""
    assert codec.engine_to_phone_frames(np.zeros(80, dtype=np.int16), codec.PHONE_RATE) == []


def test_frames_resample_by_src_rate():
    """16k engine audio is downsampled 2:1 before framing → ~half the frames."""
    n = 6400
    f8 = codec.engine_to_phone_frames(np.zeros(n, dtype=np.int16), codec.PHONE_RATE)
    f16 = codec.engine_to_phone_frames(np.zeros(n, dtype=np.int16), codec.ENGINE_RATE)
    assert len(f8) == 40                         # 6400 / 160
    assert abs(len(f16) - 20) <= 1               # resampled to ~3200 → ~20 frames


def test_phone_to_engine_upsamples_8k_to_16k():
    """Inbound μ-law 8k → int16 16k (~2x samples) for the VAD/STT pipeline."""
    ulaw = bytes([codec.ULAW_SILENCE]) * 160     # 20ms @8k
    pcm16 = codec.phone_to_engine(ulaw)
    assert pcm16.dtype == np.int16
    assert abs(len(pcm16) - 320) <= 2            # ~2x upsample
