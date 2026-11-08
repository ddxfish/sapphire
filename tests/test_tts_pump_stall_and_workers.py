"""Stream pump: the hard timeout catches stalls, not slow-but-steady engines,
and a serial engine gets one worker so chunks reach it in order.

2026-11-08: an Audio8 reply's first chunk (746 chars) waited 18s behind chunk 1
for the GPU (two workers, unfair lock), synthesized in 22s, and was dropped at
the fixed 30s deadline with its audio 11s from done.
"""
import time
from unittest.mock import MagicMock

import pytest

from core.tts.stream_pump import StreamingTTSPump


class _SegmentedProvider:
    """Streams `n` segments, `gap` seconds apart (a slow but alive engine)."""
    audio_content_type = "audio/ogg"
    supports_streaming = True

    def __init__(self, n=3, gap=0.4, synth_workers=None):
        self.n, self.gap = n, gap
        if synth_workers is not None:
            self.synth_workers = synth_workers

    def generate(self, text, voice, speed):
        return b"OGGS\x00one"

    def generate_stream(self, text, voice, speed, **kw):
        for i in range(self.n):
            time.sleep(self.gap)
            yield b"OGGS\x00seg%d" % i


def _system(provider):
    sys = MagicMock()
    sys.tts._provider = provider
    sys.tts.voice_name = "v"
    sys.tts.speed = 1.0
    return sys


@pytest.fixture
def streaming(monkeypatch):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_SPLIT_MODE", "sentence", raising=False)


def test_steady_progress_outlives_the_stall_timeout(streaming, monkeypatch):
    """1.2s of total synth against a 0.6s ceiling: every segment arrives within
    0.4s of the last, so nothing is a stall and nothing drops."""
    import core.tts.stream_pump as sp
    monkeypatch.setattr(sp, "_FLUSH_HARD_TIMEOUT_S", 0.6)
    pump = StreamingTTSPump(system=_system(_SegmentedProvider(n=3, gap=0.4)))
    pump.push("A whole sentence that will take a while to say.")
    out = list(pump.flush_and_close())
    chunks = [e for e in out if e.get("type") == "tts_chunk"]
    assert len(chunks) == 3
    assert not [e for e in out if e.get("type") == "notice"]


def test_a_real_stall_still_trips(streaming, monkeypatch):
    import core.tts.stream_pump as sp
    monkeypatch.setattr(sp, "_FLUSH_HARD_TIMEOUT_S", 0.3)
    pump = StreamingTTSPump(system=_system(_SegmentedProvider(n=2, gap=1.0)))
    pump.push("Nothing comes back for far too long.")
    t0 = time.monotonic()
    out = list(pump.flush_and_close())
    assert time.monotonic() - t0 < 2.0
    assert [e for e in out if e.get("type") == "notice"]


@pytest.mark.parametrize("declared,expected", [(None, 2), (1, 1), (3, 3), ("bad", 2), (0, 1)])
def test_executor_sized_by_synth_workers(streaming, declared, expected):
    pump = StreamingTTSPump(system=_system(_SegmentedProvider(synth_workers=declared)
                                           if declared is not None else _SegmentedProvider()))
    try:
        assert pump._executor()._max_workers == expected
    finally:
        pump._executor().shutdown(wait=False)


def test_serial_engine_synthesizes_in_order(streaming):
    order = []

    class Serial(_SegmentedProvider):
        synth_workers = 1

        def generate_stream(self, text, voice, speed, **kw):
            order.append(text)
            time.sleep(0.05)
            yield b"OGGS\x00x"

    pump = StreamingTTSPump(system=_system(Serial()))
    for s in ("First one here. ", "Second one here. ", "Third one here."):
        pump.push(s)
    list(pump.flush_and_close())
    assert [t.split()[0] for t in order] == ["First", "Second", "Third"]


def test_base_provider_default_is_two():
    from core.tts.providers.base import BaseTTSProvider
    assert BaseTTSProvider.synth_workers == 2
