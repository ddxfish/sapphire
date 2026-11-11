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
    monkeypatch.setattr(sp, "_STALL_S_PER_CHAR", 0)   # the floor alone
    pump = StreamingTTSPump(system=_system(_SegmentedProvider(n=3, gap=0.4)))
    pump.push("A whole sentence that will take a while to say.")
    out = list(pump.flush_and_close())
    chunks = [e for e in out if e.get("type") == "tts_chunk"]
    assert len(chunks) == 3
    assert not [e for e in out if e.get("type") == "notice"]


def test_a_real_stall_still_trips(streaming, monkeypatch):
    import core.tts.stream_pump as sp
    monkeypatch.setattr(sp, "_FLUSH_HARD_TIMEOUT_S", 0.3)
    monkeypatch.setattr(sp, "_STALL_S_PER_CHAR", 0)   # the floor alone
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


# ── Ultrareview 2026-11-08: the clock counts from the latest of head-of-line,
# first attempt, last segment — never from a retry ──

def test_retries_never_restart_the_stall_clock(streaming, monkeypatch, caplog):
    """Each attempt ends empty in 0.3s against a 0.45s ceiling: two attempts
    are 0.6s of nothing. Resetting the clock per attempt never tripped."""
    import core.tts.stream_pump as sp
    monkeypatch.setattr(sp, "_FLUSH_HARD_TIMEOUT_S", 0.45)
    monkeypatch.setattr(sp, "_STALL_S_PER_CHAR", 0)   # the floor alone

    class Empty(_SegmentedProvider):
        def generate_stream(self, text, voice, speed, **kw):
            time.sleep(0.3)
            return iter(())

    pump = StreamingTTSPump(system=_system(Empty()))
    pump.push("Every attempt comes back with nothing at all.")
    with caplog.at_level("WARNING"):
        list(pump.flush_and_close())
    assert "flush hard-timeout" in caplog.text


def test_a_chunk_queued_behind_the_engine_is_not_charged_for_the_wait(streaming, monkeypatch):
    """Two workers, one engine lock: chunk B starts at once but waits 0.6s for
    A. Counting from B's start dropped it the moment it reached the head."""
    import threading
    import core.tts.stream_pump as sp
    monkeypatch.setattr(sp, "_FLUSH_HARD_TIMEOUT_S", 0.45)
    monkeypatch.setattr(sp, "_STALL_S_PER_CHAR", 0)   # the floor alone
    engine = threading.Lock()

    class Locked(_SegmentedProvider):
        def generate_stream(self, text, voice, speed, **kw):
            with engine:
                n, gap = (3, 0.2) if text.startswith("First") else (1, 0.1)
                for i in range(n):
                    time.sleep(gap)
                    yield b"OGGS\x00seg%d" % i

    pump = StreamingTTSPump(system=_system(Locked()))
    pump.push("First sentence takes the engine for a while. ")
    time.sleep(0.05)
    pump.push("Second sentence waits its turn.")
    out = list(pump.flush_and_close())
    assert len([e for e in out if e.get("type") == "tts_chunk"]) == 4
    assert not [e for e in out if e.get("type") == "notice"]


def test_a_long_chunk_made_in_one_piece_gets_time_for_its_length():
    """Audio8 hands a group back whole: 750 chars on a slow GPU passed 30 s with
    no progress to report, and the reply was dropped with nothing wrong."""
    import core.tts.stream_pump as sp
    assert sp._stall_limit({"text": "x" * 20}) == sp._FLUSH_HARD_TIMEOUT_S
    assert sp._stall_limit({"text": "x" * 750}) == 750 * sp._STALL_S_PER_CHAR > sp._FLUSH_HARD_TIMEOUT_S


def test_a_pump_is_stopped_by_its_id_whoever_owns_it(streaming):
    """/api/tts/stop names the stream the browser hears. A Replay's or the
    FlashHead app page's pump was registered nowhere, so the ⏹ never found it."""
    import core.tts.stream_pump as sp
    pump = StreamingTTSPump(system=_system(_SegmentedProvider(n=6, gap=0.1)))
    pump.push("A sentence long enough to still be making when it's stopped.")
    assert sp.stop_stream(pump._stream_id)
    out = list(pump.flush_and_close())
    assert out[-1]["type"] == "tts_stream_end" and out[-1]["interrupted"]
    assert not sp.stop_stream(pump._stream_id)             # closed: gone from the registry


def test_a_reply_made_whole_and_stopped_while_written_asks_the_engine_nothing(monkeypatch):
    """Stream speech off: the reply is held until the LLM is done. A ⏹ in the
    meantime still sent every held chunk to the engine (a billed one too)."""
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", False, raising=False)
    asked = []

    class Counting(_SegmentedProvider):
        def generate_stream(self, text, voice, speed, **kw):
            asked.append(text)
            yield b"OGGS\x00seg"

    pump = StreamingTTSPump(system=_system(Counting()))
    pump.push("First sentence here. Second sentence here.\n\n")
    pump._skip_turn = True                                 # stop_tts
    out = list(pump.flush_and_close())
    assert not asked and out[-1]["interrupted"]


def test_a_remote_engine_keeps_the_ramp_when_a_face_plays_her(streaming, monkeypatch):
    """Whole paragraphs after a take are for an engine sharing the face's GPU;
    on another computer they only delayed her first words."""
    import core.tts.stream_pump as sp
    monkeypatch.setitem(sp._TAKEN, "last", True)
    local = StreamingTTSPump(system=_system(_SegmentedProvider()))
    remote_provider = _SegmentedProvider()
    remote_provider.remote = True
    remote = StreamingTTSPump(system=_system(remote_provider))
    assert not local.chunker.ramp and remote.chunker.ramp
