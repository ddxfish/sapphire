"""
F4 (2026-08-17): 16kHz wakeword deafness tests — no audio hardware required.

The bug: _listen_loop read 1280 RAW device frames regardless of device rate.
At 48kHz that's 26.7ms of audio fed to OpenWakeWord as "80ms" (3x time-stretch,
-1.58 octave — every formant lands below the trained band). Second kill: stereo
devices had their (N,2) reads .flatten()ed, interleaving L/R samples — deaf
even at native 16kHz. The correct downmix+resample path existed (audio_recorder
get_latest_chunk) but had ZERO callers.

Test plan (fork-scopes-20260817.md):
  A  math + spectrum + broken-path canary (pins what the bug did)
  B  read_frames table per device rate (asserted via FakeStream read sizes)
  C  FakeStream drives the REAL _listen_loop across rate/channel matrix;
     every predict() input must be exactly 1280 int16 samples
  D  bit-identical on healthy 16k/mono (the do-no-harm proof)
  E  real "hey sapphire" fixture clip — pending (Sapphire records it)

Run with: pytest tests/test_wakeword_16k.py -v
"""
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest


def sounddevice_available():
    try:
        import sounddevice  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


# Mock sounddevice BEFORE core.wakeword imports if not available (CI/Windows)
if not sounddevice_available():
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    mock_sd.default.device = (0, 0)
    sys.modules['sounddevice'] = mock_sd


from core.audio.utils import convert_to_mono, resample_audio  # noqa: E402


TARGET_RATE = 16000
FRAME = 1280
COMMON_RATES = [16000, 22050, 24000, 32000, 44100, 48000, 96000]


def read_frames_for(rate):
    """Mirror of the loop's per-pass computation."""
    return int(round(FRAME * rate / TARGET_RATE)) or FRAME


def dominant_freq(samples, rate):
    """Peak frequency of a windowed FFT, DC bin excluded."""
    x = samples.astype(np.float64) * np.hanning(len(samples))
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
    return freqs[np.argmax(spec[1:]) + 1]


def sine_int16(freq, rate, n_samples, amp=10000):
    t = np.arange(n_samples) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


# =============================================================================
# A — math, spectrum, and the broken-path canary
# =============================================================================

class TestFrameMath:
    @pytest.mark.parametrize("rate", COMMON_RATES)
    def test_resample_yields_exact_frame(self, rate):
        """Every common device rate resamples to exactly 1280 — no drift."""
        rf = read_frames_for(rate)
        out = resample_audio(np.zeros(rf, dtype=np.int16), rate, TARGET_RATE)
        assert len(out) == FRAME

    def test_broken_path_canary_48k(self):
        """REGRESSION PIN: 1280 raw 48k samples interpreted as 16k audio
        shift a 440Hz tone to ~146.7Hz (440 * 16000/48000). This is what the
        pre-fix loop fed OWW. If this assert ever fails, the physics of the
        old bug changed — re-read the F4 record before touching it."""
        sine48 = sine_int16(440, 48000, 48000)
        raw_chunks = sine48[:FRAME * 8]           # what the old loop read
        f = dominant_freq(raw_chunks, TARGET_RATE)  # what OWW heard
        assert abs(f - 440 * TARGET_RATE / 48000) < 5

    def test_fixed_path_spectrum_48k(self):
        """Resampled 48k audio keeps its true pitch at 16k."""
        sine48 = sine_int16(440, 48000, 48000)
        f = dominant_freq(resample_audio(sine48, 48000, TARGET_RATE), TARGET_RATE)
        assert abs(f - 440) < 5

    def test_stereo_interleave_corrupts_downmix_does_not(self):
        """The second kill: .flatten() on (N,2) interleaves L/R (doubles
        apparent length, halves pitch); convert_to_mono preserves the signal."""
        mono = sine_int16(440, TARGET_RATE, TARGET_RATE)
        stereo = np.stack([mono, mono], axis=1)
        interleaved = stereo.flatten().astype(np.int16)     # old behavior
        downmixed = convert_to_mono(stereo)                 # new behavior
        assert abs(dominant_freq(interleaved[:FRAME * 8], TARGET_RATE) - 220) < 5
        assert np.array_equal(downmixed, mono)


# =============================================================================
# Harness: FakeStream drives the REAL _listen_loop
# =============================================================================

class FakeStream:
    """Serves a prepared (N, channels) int16 device signal to stream.read.
    When exhausted, flips detector.running so the loop exits cleanly."""

    def __init__(self, data_2d, detector):
        self.data = data_2d
        self.pos = 0
        self.detector = detector
        self.read_sizes = []

    def read(self, frames):
        self.read_sizes.append(frames)
        ch = self.data.shape[1]
        if self.pos >= len(self.data):
            self.detector.running = False
            return np.zeros((frames, ch), dtype=np.int16), False
        chunk = self.data[self.pos:self.pos + frames]
        self.pos += frames
        if len(chunk) < frames:
            chunk = np.vstack([chunk, np.zeros((frames - len(chunk), ch), dtype=np.int16)])
        return chunk, False


class RecordingModel:
    """Fake OWW model: records every predict() input; optionally goes hot on
    one frame index."""

    def __init__(self, hot_frame=None, score=0.9):
        self.inputs = []
        self.hot_frame = hot_frame
        self.score = score

    def predict(self, frame):
        self.inputs.append(np.array(frame, copy=True))
        hot = self.hot_frame is not None and len(self.inputs) - 1 == self.hot_frame
        return {'testmodel': self.score if hot else 0.0}

    def concat(self):
        return np.concatenate(self.inputs) if self.inputs else np.zeros(0, dtype=np.int16)


def run_listen_loop(monkeypatch, device_data_2d, rate, model=None, threshold=0.6):
    """Build a bare WakeWordDetector (no hardware, no OWW) and run the real
    _listen_loop on the current thread until FakeStream exhausts."""
    from core.wakeword.wake_detector import WakeWordDetector

    det = WakeWordDetector.__new__(WakeWordDetector)
    det.model_name = 'testmodel'
    det.threshold = threshold
    det.running = True
    det.listen_thread = threading.current_thread()
    det.model = model or RecordingModel()

    stream = FakeStream(device_data_2d, det)
    det.audio_recorder = SimpleNamespace(actual_rate=rate, get_stream=lambda: stream)

    # Isolated config + no real sleeping — nothing leaks into shared modules.
    monkeypatch.setattr('core.wakeword.wake_detector.config',
                        SimpleNamespace(WAKE_WORD_ENABLED=True))
    monkeypatch.setattr('core.wakeword.wake_detector.time',
                        SimpleNamespace(sleep=lambda s: None, time=__import__('time').time))

    det._listen_loop()
    return det, stream


def device_signal(rate, channels, seconds_16k_frames=10, seed=42):
    """Random int16 signal sized to yield exactly `seconds_16k_frames` OWW
    frames, shaped (N, channels) with identical channels."""
    n = read_frames_for(rate) * seconds_16k_frames
    mono = np.random.RandomState(seed).randint(-2000, 2000, n).astype(np.int16)
    return np.stack([mono] * channels, axis=1), mono


# =============================================================================
# B + C — the real loop across the rate/channel matrix
# =============================================================================

class TestListenLoop:
    @pytest.mark.parametrize("rate,channels", [
        (16000, 1), (16000, 2), (32000, 1),
        (44100, 1), (48000, 1), (48000, 2),
    ])
    def test_every_predict_input_is_1280_int16(self, monkeypatch, rate, channels):
        data, _ = device_signal(rate, channels)
        model = RecordingModel()
        _, stream = run_listen_loop(monkeypatch, data, rate, model)

        assert len(model.inputs) >= 10
        for frame in model.inputs:
            assert len(frame) == FRAME
            assert frame.dtype == np.int16
        # B: the loop must request device-rate-sized reads, not 1280 raw
        assert stream.read_sizes[0] == read_frames_for(rate)

    def test_48k_audio_keeps_true_pitch_through_loop(self, monkeypatch):
        """End-to-end: a 440Hz tone captured at 48k reaches OWW at 440Hz."""
        n = read_frames_for(48000) * 10
        sine48 = sine_int16(440, 48000, n)
        model = RecordingModel()
        run_listen_loop(monkeypatch, sine48.reshape(-1, 1), 48000, model)

        heard = model.concat()[:FRAME * 10]
        assert abs(dominant_freq(heard, TARGET_RATE) - 440) < 5

    def test_activation_fires_and_loop_survives(self, monkeypatch):
        """A hot frame triggers _on_activation exactly once; the loop keeps
        reading afterward instead of dying."""
        data, _ = device_signal(48000, 1, seconds_16k_frames=10)
        model = RecordingModel(hot_frame=3)
        activations = []

        from core.wakeword.wake_detector import WakeWordDetector
        det = WakeWordDetector.__new__(WakeWordDetector)
        det.model_name = 'testmodel'
        det.threshold = 0.6
        det.running = True
        det.listen_thread = threading.current_thread()
        det.model = model
        stream = FakeStream(data, det)
        det.audio_recorder = SimpleNamespace(actual_rate=48000, get_stream=lambda: stream)
        det._on_activation = lambda: activations.append(1)

        monkeypatch.setattr('core.wakeword.wake_detector.config',
                            SimpleNamespace(WAKE_WORD_ENABLED=True))
        monkeypatch.setattr('core.wakeword.wake_detector.time',
                            SimpleNamespace(sleep=lambda s: None,
                                            time=__import__('time').time))
        det._listen_loop()

        assert activations == [1]
        assert len(model.inputs) > 4  # kept listening after the activation


# =============================================================================
# D — bit-identical on healthy 16k/mono (the do-no-harm proof)
# =============================================================================

class TestDoNoHarm:
    def test_16k_mono_bit_identical(self, monkeypatch):
        """On a healthy native-16k mono device the new loop must feed OWW the
        EXACT bytes the old loop did — same samples, same order, no resample
        artifacts."""
        data, mono = device_signal(16000, 1)
        model = RecordingModel()
        run_listen_loop(monkeypatch, data, 16000, model)

        assert np.array_equal(model.concat()[:len(mono)], mono)

    def test_16k_stereo_equal_channels_preserved(self, monkeypatch):
        """L==R stereo at 16k downmixes to the original signal (the old loop
        interleaved it into garbage)."""
        data, mono = device_signal(16000, 2)
        model = RecordingModel()
        run_listen_loop(monkeypatch, data, 16000, model)

        assert np.array_equal(model.concat()[:len(mono)], mono)
