"""PumpkinChunker (local streaming TTS sink) tests.

Decode/resample is exercised on a real (WAV) chunk; playback is exercised against
a fake OutputStream so no audio device is needed. Covers: resample to output rate,
the slice write loop, stop() closing an open stream, feed-after-stop ignored, and
the worker draining to completion.
"""
import base64
import io
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tts.pumpkin_chunker import PumpkinChunker


def wav_chunk(freq=220, dur=0.2, rate=24000, pause_after_ms=0, stream_id="t1"):
    """A tts_chunk-shaped dict carrying a WAV-encoded tone (sf.read auto-detects)."""
    t = np.linspace(0, dur, int(rate * dur), endpoint=False)
    data = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, data, rate, format="WAV", subtype="PCM_16")
    return {
        "type": "tts_chunk",
        "audio_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "content_type": "audio/wav", "index": 0, "boundary": "sentence",
        "pause_after_ms": pause_after_ms, "text": "hi", "stream_id": stream_id,
    }


class FakeStream:
    def __init__(self):
        self.writes = []
        self.closed = False
    def write(self, x):
        self.writes.append(np.asarray(x).shape)
    def stop(self):
        pass
    def close(self):
        self.closed = True


def test_decode_resamples_to_output_rate():
    pc = PumpkinChunker(output_rate=48000)
    pcm = pc._decode(wav_chunk(dur=0.1, rate=24000))   # 2400 samples @24k -> ~4800 @48k
    assert pcm.dtype == np.float32
    assert 4600 <= len(pcm) <= 5000


def test_play_one_opens_stream_and_writes_slices():
    fake = FakeStream()
    pc = PumpkinChunker(output_rate=48000, stream_factory=lambda: fake)
    pc._play_one(wav_chunk(dur=0.25, rate=48000), pc.should_stop)   # 12000 samples -> ~3 slices of 4800
    assert pc._is_playing is True
    assert len(fake.writes) >= 2


def test_stop_closes_open_stream():
    fake = FakeStream()
    pc = PumpkinChunker(output_rate=48000, stream_factory=lambda: fake)
    pc._stream = fake
    pc._is_playing = True
    pc.stop()
    assert pc.should_stop.is_set()
    assert fake.closed is True


def test_feed_after_stop_is_ignored():
    pc = PumpkinChunker(output_rate=48000, stream_factory=lambda: FakeStream())
    pc.should_stop.set()
    pc.feed_chunk(wav_chunk())
    assert pc._queue.qsize() == 0


def test_worker_drains_and_closes():
    fake = FakeStream()
    pc = PumpkinChunker(output_rate=48000, stream_factory=lambda: fake)
    pc.start()
    pc.feed_chunk(wav_chunk(dur=0.1, rate=48000))
    pc.finish()
    pc._worker.join(timeout=3.0)
    assert pc._worker.is_alive() is False
    assert fake.closed is True
    assert pc._is_playing is False
