"""Offline tests for DuplexConversationSource — drive the real-time callback directly
(no audio device) to validate the DTLN-in-callback + decimation + VAD-block path.

Proves the two things that matter: it CANCELS her echo (mic==loopback) and it KEEPS
the user's voice (mic == echo + user). Loads the real onnx from user/models/dtln/.
"""
import base64
import io
import types

import numpy as np
import pytest
import soundfile as sf

from core.conversation.duplex_source import DuplexConversationSource, _OnnxDTLN, _resample

SR = 16000


def _src(dev_rate=SR):
    pushed = []
    driver = types.SimpleNamespace(system=None, push_frame=lambda b, s: pushed.append((b, s)))
    gate = types.SimpleNamespace(is_speech=lambda blk: True)
    s = DuplexConversationSource(driver, gate, dtln_model="256", dev_rate=dev_rate, device=(None, None))
    s._pushed = pushed
    try:
        s._eng = _OnnxDTLN(s._models_dir, "256")
    except Exception as e:                       # onnx missing -> skip (CI without weights)
        pytest.skip(f"DTLN onnx not available: {e}")
    s._warmup()
    return s


def _am_noise(n, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n).astype(np.float32)
    x = x - np.convolve(x, np.ones(64, np.float32) / 64, "same")
    t = np.arange(n) / SR
    env = (0.5 + 0.45 * np.sin(2 * np.pi * 3.5 * t)).astype(np.float32)
    return (0.35 * env * x).astype(np.float32)


def _drain_rms(src):
    out = []
    while not src._vad_q.empty():
        blk = src._vad_q.get_nowait()
        out.append(float(np.sqrt(np.mean((blk.astype(np.float64) / 32768.0) ** 2))))
    return out


def _rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)


def test_resample_length_ratio():
    x = np.ones(44100, np.float32)
    assert abs(len(_resample(x, 44100, 16000)) - 16000) <= 1
    assert len(_resample(x, 16000, 16000)) == 44100   # passthrough


def test_callback_cancels_her_echo():
    """mic == loopback (she talks, user silent) -> cleaned mic should be strongly attenuated."""
    s = _src()
    her = _am_noise(SR)                            # 1s
    s._out_chunks.append(her.copy())               # what we 'play' = the loopback ref
    indata = her.reshape(-1, 1)                     # mic hears exactly the echo
    outdata = np.zeros((SR, 1), np.float32)
    s._callback(indata, outdata, SR, None, None)
    assert not s._cb_err
    assert np.allclose(outdata[:, 0], her, atol=1e-6)   # she still plays out full-rate
    cleaned = _drain_rms(s)
    assert cleaned, "no VAD blocks produced"
    erle = 20 * np.log10(_rms(her) / (np.mean(cleaned) + 1e-12))
    assert erle > 12, f"echo not cancelled (ERLE {erle:.1f} dB)"


def test_callback_keeps_user_voice():
    """mic == her echo + loud user -> cleaned mic should RETAIN the user (not cancel them)."""
    s = _src()
    her = _am_noise(SR, seed=1)
    t = np.arange(SR) / SR
    user = (0.5 * np.sin(2 * np.pi * 320 * t)).astype(np.float32)   # loud user tone
    s._out_chunks.append(her.copy())               # loopback = her only
    indata = (her + user).reshape(-1, 1)           # mic = echo + user
    outdata = np.zeros((SR, 1), np.float32)
    s._callback(indata, outdata, SR, None, None)
    assert not s._cb_err
    cleaned = _drain_rms(s)
    assert cleaned
    # user energy must survive; compare to the echo-only case (which cancels to near-zero)
    assert max(cleaned) > 0.03, f"user voice was wrongly cancelled (max cleaned rms {max(cleaned):.4f})"


def test_callback_variable_frames_no_error():
    """Feed odd-sized blocks (like PortAudio's variable callback) — buffering must not choke."""
    s = _src(dev_rate=44100)                       # exercise the 44.1k->16k decimation path
    her = _am_noise(int(44100 * 0.5))
    s._out_chunks.append(her.copy())
    pos = 0
    for fr in (441, 512, 300, 1024, 256):
        if pos + fr > len(her):
            break
        s._callback(her[pos:pos + fr].reshape(-1, 1), np.zeros((fr, 1), np.float32), fr, None, None)
        pos += fr
    assert not s._cb_err
    assert not s._vad_q.empty()                    # produced 16k VAD blocks despite odd frames


def test_barge_guard_and_energy_floor():
    """During her playback: onset guard squelches, then quiet residual is rejected by the floor
    while a loud (real) barge passes. When she's not playing, speech passes normally."""
    import time as _t
    s = _src()
    s._guard_s = 10.0
    s._barge_floor = 0.03
    LOUD, QUIET = 0.2, 0.005
    s._playing = True
    s._play_start = _t.time()                        # inside the onset guard
    assert s._barge_ok(True, LOUD) is False          # guard squelches even loud speech
    s._play_start = _t.time() - 20                    # guard elapsed
    assert s._barge_ok(True, LOUD) is True            # real (loud) barge passes
    assert s._barge_ok(True, QUIET) is False          # quiet residual echo rejected by the floor
    s._playing = False
    assert s._barge_ok(True, QUIET) is True           # not playing -> normal endpointing, no floor
    assert s._barge_ok(False, LOUD) is False          # silence is never speech


def test_delay_compensation_aligns_delayed_echo():
    """Echo arrives D samples late, BEYOND DTLN's ~512-sample tolerance. With the loopback delayed
    by D, DTLN re-aligns and cancels; without it the ref is misaligned and cancels far worse."""
    D = 640                                          # > 512-sample window -> comp actually matters
    her = _am_noise(SR, seed=3)
    mic = np.concatenate([np.zeros(D, np.float32), her[:-D]]).astype(np.float32)  # echo lags by D

    def run(delay):
        s = _src()
        s._delay_16k = delay
        s._lpb16 = np.zeros(delay, np.float32)     # mimic _apply_aec_delay()
        s._out_chunks.append(her.copy())            # what we 'played' = the reference
        s._callback(mic.reshape(-1, 1), np.zeros((SR, 1), np.float32), SR, None, None)
        cleaned = _drain_rms(s)
        return np.mean(cleaned) if cleaned else 1.0

    comp = run(D)        # reference delayed to match the echo
    nocomp = run(0)      # reference not delayed -> misaligned
    assert comp < nocomp, f"delay-comp ({comp:.4f}) should beat no-comp ({nocomp:.4f})"
    erle = 20 * np.log10(_rms(mic) / (comp + 1e-12))
    assert erle > 12, f"delayed echo not cancelled with comp (ERLE {erle:.1f} dB)"


def test_prebuffer_holds_until_cushion():
    """Output holds (silence) until the pre-roll cushion fills, then drains; finish() releases early."""
    s = _src()
    s._prebuffer = 8000                              # require an 8000-sample cushion
    s._released = False
    buf = io.BytesIO()
    sf.write(buf, (0.2 * np.sin(np.linspace(0, 30, 4000))).astype(np.float32), SR, format="WAV")
    s.feed_chunk({"audio_b64": base64.b64encode(buf.getvalue()).decode()})   # 4000 < cushion
    assert _rms(s._pull_output(1000)) < 1e-6         # held: cushion not full
    s.finish()
    assert _rms(s._pull_output(1000)) > 0.01         # finish() releases -> draining


def test_sink_pull_and_stop():
    s = _src()
    buf = io.BytesIO()
    sf.write(buf, (0.2 * np.sin(np.linspace(0, 50, 8000))).astype(np.float32), SR, format="WAV")
    b64 = base64.b64encode(buf.getvalue()).decode()
    s.feed_chunk({"audio_b64": b64})
    assert s._playing
    out = s._pull_output(8000)
    assert _rms(out) > 0.01                         # decoded audio came out
    tail = s._pull_output(8000)
    assert _rms(tail) < 1e-6                         # buffer drained -> silence
    s.feed_chunk({"audio_b64": b64})
    s.stop()
    assert s._stop_flag.is_set()
    assert _rms(s._pull_output(8000)) < 1e-6         # stop() flushed queued audio
