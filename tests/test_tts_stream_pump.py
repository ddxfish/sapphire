"""Tests for the brain-side streaming TTS pump.

Focus: the orchestration glue around SpeechChunker — that push() yields
the right SSE events, that disabled/no-provider/no-stream-support all
correctly fall back to no-op, and that cancel/close don't leak threads.

Synth is mocked — the chunker itself is tested separately in
tests/test_tts_streaming.py.
"""
import base64
import time
from unittest.mock import MagicMock, patch

import pytest

from core.tts.stream_pump import StreamingTTSPump


class _FakeProvider:
    audio_content_type = "audio/ogg"
    supports_streaming = True

    def __init__(self, audio_bytes=b"OGGS\x00fake", delay=0.0, fail=False):
        self.audio_bytes = audio_bytes
        self.delay = delay
        self.fail = fail
        self.calls = []

    def generate(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("simulated synth failure")
        return self.audio_bytes


def _make_system(provider=None, voice="af_heart", speed=1.0, tts_attr=True):
    sys = MagicMock()
    if tts_attr:
        sys.tts._provider = provider
        sys.tts.voice_name = voice
        sys.tts.speed = speed
    else:
        sys.tts = None
    return sys


@pytest.fixture
def enable_streaming(monkeypatch):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", True, raising=False)


def test_disabled_when_streaming_setting_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", False, raising=False)
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    assert pump.enabled is False
    assert pump.push("Hello. World. ") == []
    assert list(pump.flush_and_close()) == []


def test_disabled_when_no_provider(enable_streaming):
    pump = StreamingTTSPump(system=_make_system(tts_attr=False))
    assert pump.enabled is False


def test_disabled_when_provider_no_stream_support(enable_streaming):
    class NoStream:
        supports_streaming = False
        def generate(self, *a, **kw): return b""
    pump = StreamingTTSPump(system=_make_system(provider=NoStream()))
    assert pump.enabled is False


def test_first_push_emits_stream_start(enable_streaming):
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    out = pump.push("Just a short bit")  # no boundary yet
    types = [e["type"] for e in out]
    assert "tts_stream_start" in types
    pump.cancel()


def test_complete_sentence_produces_audio_event(enable_streaming):
    fp = _FakeProvider(audio_bytes=b"FAKE_OGG_BYTES")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    # Push enough to satisfy sentence boundary (period + space + capital)
    pump.push("Hello there. ")
    pump.push("More text here.")
    out = list(pump.flush_and_close())
    # Expect at least one tts_chunk and a final tts_stream_end
    types = [e["type"] for e in out]
    assert "tts_stream_end" in types
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    assert len(chunks) >= 1
    # Audio is base64-encoded
    decoded = base64.b64decode(chunks[0]["audio_b64"])
    assert decoded == b"FAKE_OGG_BYTES"
    # Metadata round-trips
    assert chunks[0]["content_type"] == "audio/ogg"
    assert "boundary" in chunks[0]
    assert "pause_after_ms" in chunks[0]


def test_synth_failure_swallowed_no_event(enable_streaming):
    fp = _FakeProvider(fail=True)
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    assert chunks == []  # all synths failed
    assert any(e["type"] == "tts_stream_end" for e in out)


def test_cancel_no_more_events(enable_streaming):
    fp = _FakeProvider(delay=0.2)
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("First sentence here. ")
    pump.push("Second sentence here. ")
    pump.push("Third.")
    # Cancel without waiting
    pump.cancel()
    # Subsequent push should be no-op
    assert pump.push("more") == []
    # flush_and_close after cancel is no-op
    assert list(pump.flush_and_close()) == []


def test_push_with_no_text_no_events(enable_streaming):
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    assert pump.push("") == []
    assert pump.push(None) == []


def test_flush_emits_end_only_when_started(enable_streaming):
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    # No push happened
    out = list(pump.flush_and_close())
    assert out == []  # no stream_start means no stream_end either


# ---------------------------------------------------------------------------
# Hook surface tests (M5)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_hooks():
    """Each hook test starts with a clean hook registry and restores after."""
    from core.hooks import hook_runner
    snapshot = dict(hook_runner._hooks)
    hook_runner._hooks.clear()
    hook_runner._sorted.clear()
    yield hook_runner
    hook_runner._hooks.clear()
    hook_runner._sorted.clear()
    hook_runner._hooks.update(snapshot)


def test_tts_stream_start_hook_fires(enable_streaming, fresh_hooks):
    seen = []
    fresh_hooks.register("tts_stream_start", lambda ev: seen.append(ev.metadata.copy()), plugin_name="t")
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    pump.push("Anything.")
    assert len(seen) == 1
    assert "stream_id" in seen[0]
    assert seen[0]["voice"] == "af_heart"
    pump.cancel()


def test_tts_stream_start_skip_disables_turn(enable_streaming, fresh_hooks):
    def cancel(ev):
        ev.skip_tts = True
    fresh_hooks.register("tts_stream_start", cancel, plugin_name="killer")
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    out = pump.push("Hello there. ")
    out += pump.push("More text here.")
    out += list(pump.flush_and_close())
    # Skip means no events emitted at all (not even stream_start in output)
    assert out == []


def test_tts_chunk_text_hook_can_mutate(enable_streaming, fresh_hooks):
    fp = _FakeProvider()
    def mutate(ev):
        ev.tts_text = (ev.tts_text or "").upper()
    fresh_hooks.register("tts_chunk_text", mutate, plugin_name="up")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Hello there. ")
    pump.push("More text.")
    list(pump.flush_and_close())
    # Provider should have been called with uppercased text
    assert any(call[0].startswith("HELLO") for call in fp.calls), fp.calls


def test_tts_chunk_text_skip_drops_chunk(enable_streaming, fresh_hooks, monkeypatch):
    """Per-chunk selective skip via tts_chunk_text hook. Requires multiple
    distinct pump-chunks, which the paragraph-mode default collapses into
    one. Force sentence mode so each `pump.push("Foo. ")` yields its own
    chunk and we can selectively skip chunk 0."""
    import config
    monkeypatch.setattr(config, "TTS_STREAMING_SPLIT_MODE", "sentence", raising=False)
    fp = _FakeProvider()
    def drop_first(ev):
        if ev.metadata.get("chunk_index") == 0:
            ev.skip_tts = True
    fresh_hooks.register("tts_chunk_text", drop_first, plugin_name="dropper")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("First sentence here. ")
    pump.push("Second sentence here. ")
    pump.push("Third.")
    out = list(pump.flush_and_close())
    # First chunk was skipped — provider was NOT called for it
    chunk_texts = [c[0] for c in fp.calls]
    assert not any("First sentence" in t for t in chunk_texts)
    # Other chunks still synthesized
    assert any("Second sentence" in t for t in chunk_texts)


def test_tts_chunk_audio_hook_can_replace_bytes(enable_streaming, fresh_hooks):
    fp = _FakeProvider(audio_bytes=b"ORIGINAL")
    def replace(ev):
        carrier = ev.metadata.get("audio")
        if carrier is not None:
            carrier["audio_bytes"] = b"REPLACED"
    fresh_hooks.register("tts_chunk_audio", replace, plugin_name="swapper")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    assert chunks
    assert base64.b64decode(chunks[0]["audio_b64"]) == b"REPLACED"


def test_tts_chunk_audio_hook_can_drop(enable_streaming, fresh_hooks):
    fp = _FakeProvider(audio_bytes=b"ORIGINAL")
    def drop(ev):
        carrier = ev.metadata.get("audio")
        if carrier is not None:
            carrier["audio_bytes"] = None
    fresh_hooks.register("tts_chunk_audio", drop, plugin_name="muter")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    assert chunks == []  # all chunks muted


def test_tts_stream_end_hook_observes_completion(enable_streaming, fresh_hooks):
    seen = []
    fresh_hooks.register("tts_stream_end", lambda ev: seen.append(ev.metadata.copy()), plugin_name="t")
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    pump.push("Hello there. ")
    pump.push("More.")
    list(pump.flush_and_close())
    assert len(seen) == 1
    assert seen[0]["interrupted"] is False
    assert seen[0]["chunk_count"] >= 1
    assert "stream_id" in seen[0]


def test_tts_stream_end_hook_fires_on_cancel_with_interrupted_true(enable_streaming, fresh_hooks):
    seen = []
    fresh_hooks.register("tts_stream_end", lambda ev: seen.append(ev.metadata.copy()), plugin_name="t")
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider(delay=0.1)))
    pump.push("Hello there. ")
    pump.push("More.")
    pump.cancel()
    assert len(seen) == 1
    assert seen[0]["interrupted"] is True


def test_settings_chunk_bounds_reach_chunker(monkeypatch, enable_streaming):
    """User-tunable min/max chars from settings get applied to the chunker."""
    import config
    monkeypatch.setattr(config, "TTS_STREAMING_MIN_CHARS", 50, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_MAX_CHARS", 300, raising=False)
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    assert pump.chunker.min_chars == 50
    assert pump.chunker.max_chars == 300
    pump.cancel()


def test_settings_clamps_protect_against_bad_input(monkeypatch, enable_streaming):
    """Garbage settings (zero, negative, max < min) don't yield a broken chunker."""
    import config
    monkeypatch.setattr(config, "TTS_STREAMING_MIN_CHARS", 0, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_MAX_CHARS", -1, raising=False)
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    assert pump.chunker.min_chars >= 5
    assert pump.chunker.max_chars > pump.chunker.min_chars
    pump.cancel()


def test_stream_id_stable_across_hooks(enable_streaming, fresh_hooks):
    """A plugin can correlate the 4 hooks via stream_id within one turn."""
    ids = {}
    fresh_hooks.register("tts_stream_start", lambda ev: ids.setdefault("start", ev.metadata["stream_id"]), plugin_name="t")
    fresh_hooks.register("tts_chunk_text", lambda ev: ids.setdefault("text", ev.metadata["stream_id"]), plugin_name="t")
    fresh_hooks.register("tts_chunk_audio", lambda ev: ids.setdefault("audio", ev.metadata["stream_id"]), plugin_name="t")
    fresh_hooks.register("tts_stream_end", lambda ev: ids.setdefault("end", ev.metadata["stream_id"]), plugin_name="t")
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    pump.push("Hello there. ")
    pump.push("More.")
    list(pump.flush_and_close())
    assert ids["start"] == ids["text"] == ids["audio"] == ids["end"]


# ---------------------------------------------------------------------------
# M7 Stop coordination — cancel_check during flush_and_close
# ---------------------------------------------------------------------------


def test_flush_polls_cancel_check_and_bails_early(enable_streaming, fresh_hooks):
    """User hits Stop AFTER LLM finishes but BEFORE all synth completes —
    flush_and_close must detect the cancel and emit end(interrupted=True)
    instead of blocking until every future resolves."""
    cancel_flag = {"value": False}
    fp = _FakeProvider(delay=0.4)  # slow synth so cancel can race in
    pump = StreamingTTSPump(
        system=_make_system(provider=fp),
        cancel_check=lambda: cancel_flag["value"],
    )
    pump.push("First sentence here. ")
    pump.push("Second sentence here. ")
    pump.push("Third sentence here.")
    # Trip cancel BEFORE flush — flush should bail immediately on first poll
    cancel_flag["value"] = True
    out = list(pump.flush_and_close())
    end_events = [e for e in out if e["type"] == "tts_stream_end"]
    assert len(end_events) == 1
    assert end_events[0]["interrupted"] is True


def test_flush_fires_end_hook_with_interrupted_true_on_user_stop(enable_streaming, fresh_hooks):
    """Plugins listening to tts_stream_end must see interrupted=True so
    they can finalize state properly (e.g. close a partial recording)."""
    seen = []
    fresh_hooks.register("tts_stream_end", lambda ev: seen.append(ev.metadata.copy()), plugin_name="t")
    cancel_flag = {"value": False}
    fp = _FakeProvider(delay=0.4)
    pump = StreamingTTSPump(
        system=_make_system(provider=fp),
        cancel_check=lambda: cancel_flag["value"],
    )
    pump.push("First sentence. ")
    pump.push("Second sentence here.")
    cancel_flag["value"] = True
    list(pump.flush_and_close())
    assert len(seen) == 1
    assert seen[0]["interrupted"] is True


def test_flush_normal_completion_marks_not_interrupted(enable_streaming, fresh_hooks):
    """Sanity: no cancel signal means interrupted=False in both the
    SSE event and the hook payload."""
    seen = []
    fresh_hooks.register("tts_stream_end", lambda ev: seen.append(ev.metadata.copy()), plugin_name="t")
    pump = StreamingTTSPump(
        system=_make_system(provider=_FakeProvider()),
        cancel_check=lambda: False,
    )
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    end_events = [e for e in out if e["type"] == "tts_stream_end"]
    assert end_events[0]["interrupted"] is False
    assert seen[0]["interrupted"] is False


def test_cancel_check_exception_treated_as_false(enable_streaming):
    """A misbehaving cancel_check (raises) must not crash the drain loop —
    treat it as 'not cancelled' and continue."""
    pump = StreamingTTSPump(
        system=_make_system(provider=_FakeProvider()),
        cancel_check=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    pump.push("Hello there. ")
    pump.push("More.")
    out = list(pump.flush_and_close())
    assert any(e["type"] == "tts_stream_end" for e in out)


def test_cancel_method_still_works_after_flush(enable_streaming):
    """The legacy cancel() path (called from finally) must remain a safe
    no-op after flush_and_close has already closed the pump."""
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    pump.push("Hello there. ")
    pump.push("More.")
    list(pump.flush_and_close())
    # Second cancel/close — should not raise or fire end-hook again
    pump.cancel()  # safe no-op


# ---------------------------------------------------------------------------
# Plugin-contract guard (herring-table #4 #8 #12)
# ---------------------------------------------------------------------------


def test_plugin_non_string_tts_text_does_not_crash_turn(enable_streaming, fresh_hooks):
    """A buggy plugin that sets ev.tts_text to a non-string (list/dict/int)
    must NOT crash the LLM turn — pump uses original text and logs warning."""
    fp = _FakeProvider()
    def bad_handler(ev):
        ev.tts_text = ["accidentally", "a", "list"]
    fresh_hooks.register("tts_chunk_text", bad_handler, plugin_name="buggy")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    # No exception should escape push() or flush_and_close()
    pump.push("Hello there. ")
    pump.push("More text here.")
    out = list(pump.flush_and_close())
    # Synth was called with ORIGINAL text (not the bad list), so chunks emitted
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    assert chunks, "expected chunks despite buggy plugin"
    # First call should have used the original text, not the list
    assert any("Hello" in call[0] for call in fp.calls), fp.calls


def test_plugin_empty_string_tts_text_skips_chunk_not_clobbers_to_original(enable_streaming, fresh_hooks):
    """Plugin setting ev.tts_text = '' means 'mute this chunk', NOT 'use
    original'. Content-moderation plugins rely on this contract."""
    fp = _FakeProvider()
    def mute(ev):
        if ev.metadata.get("chunk_index") == 0:
            ev.tts_text = ""
    fresh_hooks.register("tts_chunk_text", mute, plugin_name="muter")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("First sentence here. ")
    pump.push("Second sentence here. ")
    pump.push("Third.")
    list(pump.flush_and_close())
    # First chunk MUST NOT have been synthesized — its text was muted to ""
    synth_texts = [call[0] for call in fp.calls]
    assert not any("First sentence" in t for t in synth_texts), (
        f"plugin muted first chunk but original text was synthesized anyway: {synth_texts}"
    )


def test_plugin_whitespace_tts_text_also_skips_chunk(enable_streaming, fresh_hooks):
    """Whitespace-only counts as 'mute', not 'use original'."""
    fp = _FakeProvider()
    def whitespace(ev):
        ev.tts_text = "   \t\n  "
    fresh_hooks.register("tts_chunk_text", whitespace, plugin_name="wsp")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Anything. ")
    pump.push("More text here.")
    list(pump.flush_and_close())
    # No synth should have happened at all — every chunk was whitespace-muted
    assert fp.calls == [], f"whitespace-muted chunks were synthesized: {fp.calls}"


def test_plugin_None_tts_text_uses_original(enable_streaming, fresh_hooks):
    """ev.tts_text=None (plugin didn't touch it) means 'use original text'."""
    fp = _FakeProvider()
    def noop(ev):
        # Don't touch tts_text; HookEvent initializes it from the kwarg passed
        # by _fire_hook, but None at the field-default level means "untouched"
        pass
    fresh_hooks.register("tts_chunk_text", noop, plugin_name="noop")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Hello there. ")
    pump.push("More.")
    list(pump.flush_and_close())
    # Synth should have used original chunk text
    assert any("Hello" in call[0] for call in fp.calls), fp.calls


class _StreamingFakeProvider:
    """Like _FakeProvider but with a real generate_stream that yields
    multiple segments per chunk — exercises the new pump wiring (herring #7)."""
    audio_content_type = "audio/ogg"
    supports_streaming = True

    def __init__(self, segments_per_chunk=3, segment_bytes=b"OGGS_FAKE_SEGMENT"):
        self.segments_per_chunk = segments_per_chunk
        self.segment_bytes = segment_bytes
        self.calls = []

    def generate(self, text, voice, speed):
        # Should NOT be called when supports_streaming is True
        raise AssertionError("generate() should not be called when streaming")

    def generate_stream(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        for i in range(self.segments_per_chunk):
            yield self.segment_bytes + bytes([i])


def test_streaming_provider_emits_one_event_per_segment(enable_streaming, monkeypatch):
    """When provider.supports_streaming=True, the pump should emit one
    tts_chunk SSE event per yielded segment — that's the M2 latency win
    we wired up 2026-05-18 (herring #7). Before this wiring, all segments
    were collected into one blob and one event was emitted per chunk.

    Note: push() can emit segments inline (when worker finished by the
    time the next push runs) — test collects events from ALL three
    method calls so we don't miss inline drains.

    Forces sentence mode so each `pump.push("Foo. ")` becomes its own
    chunk (paragraph mode collapses them all into one)."""
    import config
    monkeypatch.setattr(config, "TTS_STREAMING_SPLIT_MODE", "sentence", raising=False)
    fp = _StreamingFakeProvider(segments_per_chunk=4)
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    out = []
    out.extend(pump.push("Hello there. "))
    out.extend(pump.push("More text here."))
    out.extend(list(pump.flush_and_close()))
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    # 2 pump-chunks × 4 segments each = 8 events
    assert len(chunks) == 8, f"expected 8 segment events, got {len(chunks)}: {[c['index'] for c in chunks]}"
    # First 4 should be index 0 (from first pump-chunk), next 4 index 1
    indices = [c["index"] for c in chunks]
    assert indices == [0, 0, 0, 0, 1, 1, 1, 1], f"order/index wrong: {indices}"
    # The legacy generate() must NOT have been called
    assert len(fp.calls) == 2  # one generate_stream call per pump-chunk


def test_streaming_provider_preserves_order_with_concurrent_workers(enable_streaming, monkeypatch):
    """With 2 executor workers, the FIRST pump-chunk's segments must emit
    before the SECOND pump-chunk's even if the second's worker finishes
    first (faster synth). Order is preserved at the deque level.

    Forces sentence mode so two push() calls produce two separate chunks
    that the executor can race in parallel."""
    import config
    monkeypatch.setattr(config, "TTS_STREAMING_SPLIT_MODE", "sentence", raising=False)
    import time as _time

    class _OrderedProvider:
        audio_content_type = "audio/ogg"
        supports_streaming = True
        def __init__(self):
            self.calls = []
        def generate(self, *a, **kw):
            raise AssertionError("should not call generate")
        def generate_stream(self, text, voice, speed):
            self.calls.append(text)
            # Text-keyed delay so "first" pump-chunk is slow regardless
            # of which executor worker picks up which future.
            if "First" in text:
                _time.sleep(0.15)
            label = text.split()[0]  # 'First.' or 'Second'
            for i in range(2):
                yield f"{label}_seg_{i}".encode()

    op = _OrderedProvider()
    pump = StreamingTTSPump(system=_make_system(provider=op))
    pump.push("First chunk. ")
    pump.push("Second chunk text.")
    out = list(pump.flush_and_close())
    chunks = [e for e in out if e["type"] == "tts_chunk"]
    decoded = [base64.b64decode(c["audio_b64"]).decode() for c in chunks]
    # First pump-chunk's segments (despite slower synth) emit BEFORE
    # second's. The deque holds the order; workers don't.
    assert decoded == [
        "First_seg_0", "First_seg_1",
        "Second_seg_0", "Second_seg_1",
    ], f"order broken: {decoded}"


def test_skip_tts_at_stream_start_fires_end_hook_for_plugin_cleanup(enable_streaming, fresh_hooks):
    """When a plugin cancels the whole turn via tts_stream_start skip_tts,
    tts_stream_end MUST still fire (with interrupted=True) so plugins that
    opened state in start can finalize — otherwise state leaks across turns."""
    starts = []
    ends = []
    fresh_hooks.register("tts_stream_start", lambda ev: (starts.append(ev.metadata.copy()), setattr(ev, "skip_tts", True)), plugin_name="cancel")
    fresh_hooks.register("tts_stream_end", lambda ev: ends.append(ev.metadata.copy()), plugin_name="t")
    pump = StreamingTTSPump(system=_make_system(provider=_FakeProvider()))
    out = pump.push("Hello there. ")
    out += list(pump.flush_and_close())
    # No SSE events emitted (plugin cancelled the whole turn)
    assert out == []
    # Start hook fired exactly once
    assert len(starts) == 1
    # End hook ALSO fired with interrupted=True — closing the contract
    assert len(ends) == 1, f"expected end hook to fire on skip-turn cancel, got {ends}"
    assert ends[0]["interrupted"] is True
    assert ends[0]["chunk_count"] == 0
    assert starts[0]["stream_id"] == ends[0]["stream_id"]


# ─── Meta-dict cumulative counter (cross-push regression) ─────────────────────
# Fix: segments_emitted moved from local var in _drain_ready / flush_and_close
# to meta dict (persists across push() invocations). Before the fix, a chunk
# whose segment was drained in push N and whose worker finished between N and
# N+1 would be falsely accounted as dropped because the local counter reset
# between calls. Notice would fire saying chunks were lost when none were.

def test_meta_segments_emitted_persists_across_drain_calls(enable_streaming):
    """Drain chunk 0's segment in push #1 while worker still pending.
    Between pushes, worker finishes. Push #2 must not flag chunk 0 as
    dropped just because its local segments_emitted is 0 on this call.

    Uses Event-based synchronization on both yield-completed and continue
    signals so the test doesn't depend on timing."""
    import threading

    yielded = threading.Event()
    continue_ = threading.Event()

    class _ControlledProvider:
        audio_content_type = "audio/ogg"
        supports_streaming = True
        def generate(self, *a, **kw):
            raise AssertionError("streaming path expected")
        def generate_stream(self, text, voice, speed):
            yield b"OGGS_FIRST_SEG"
            # Once consumer pulls the first segment + asks for next, we know
            # the segment is in the queue. Signal here, then block until
            # test releases us.
            yielded.set()
            continue_.wait(timeout=2.0)

    pump = StreamingTTSPump(system=_make_system(provider=_ControlledProvider()))
    # Push submits chunk AND runs its own _drain_ready internally — depending
    # on worker scheduling, the segment may be drained by push itself or by
    # our follow-up call. Test the END-STATE invariant: total drained = 1
    # AND meta["segments_emitted"] reflects it cumulatively.
    out_push = pump.push("This is a long enough chunk to emit.\n\n")

    # Synchronize on worker yield (segment is either in queue or already drained)
    assert yielded.wait(timeout=2.0), "worker never yielded segment"

    # Second drain — catches anything push didn't already get
    out_extra = pump._drain_ready()

    # Combined: exactly one tts_chunk event emitted across both drain points
    total_chunks = sum(1 for e in (out_push + out_extra) if e.get("type") == "tts_chunk")
    assert total_chunks == 1, f"expected 1 segment drained total, got {total_chunks}"

    # CRITICAL: meta dict carries cumulative count regardless of which
    # _drain_ready call captured the segment. Pre-fix this would reset.
    assert len(pump.pending) == 1
    assert pump.pending[0][3]["segments_emitted"] == 1, \
        f"meta counter not persisting cumulative count: {pump.pending[0][3]}"

    # Release worker, wait for completion
    continue_.set()
    pump.pending[0][2].result(timeout=2.0)

    # Final flush: meta count is 1; flush must NOT mark chunk dropped.
    # Pre-fix: flush's local counter started at 0, saw queue empty + done,
    # falsely accounted as dropped → notice fires.
    out_final = list(pump.flush_and_close())
    notices = [e for e in out_final if e.get("type") == "notice"]
    assert notices == [], f"false drop notice (was the pre-fix bug): {notices}"
    assert pump._dropped_chunks == [], f"false drop accounting: {pump._dropped_chunks}"


# ─── Drop notice wording (UX regression) ──────────────────────────────────────
# 2026-05-26: previous wording was "{N} TTS chunk(s) lost — speech may have
# gaps. Check Kokoro server health." That blamed the server for what was
# usually unsupported-text (CJK, emoji). New wording leads with the common
# cause, ends with reassurance, and handles singular/plural properly.

class _NoAudioStreamingProvider:
    """Yields nothing — simulates Kokoro 400 on text it can't synthesize
    (CJK, emoji-only, etc.)."""
    audio_content_type = "audio/ogg"
    supports_streaming = True
    def generate(self, *a, **kw):
        raise AssertionError("streaming path expected")
    def generate_stream(self, text, voice, speed):
        return
        yield  # unreachable but makes this a generator


def test_drop_notice_uses_voice_cant_render_framing(enable_streaming):
    """Notice should reflect 'voice doesn't support these characters' not
    'check server health'. Most common real cause is content, not server."""
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    pump.push("Hello world.")
    out = list(pump.flush_and_close())
    notices = [e for e in out if e.get("type") == "notice"]
    assert len(notices) == 1, f"expected one drop notice, got {notices}"
    msg = notices[0]["message"]
    # New phrasing
    assert "couldn't be voiced" in msg, f"missing new framing: {msg}"
    assert "Text is unaffected" in msg, f"missing reassurance: {msg}"
    # Old alarming wording removed
    assert "Check Kokoro server health" not in msg, f"old wording still present: {msg}"
    assert "lost" not in msg, f"old 'lost' framing still present: {msg}"


def test_drop_notice_singular_for_one_dropped(enable_streaming):
    """1 → 'chunk' (singular), no 's'."""
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    pump.push("Hello world.")
    out = list(pump.flush_and_close())
    notices = [e for e in out if e.get("type") == "notice"]
    assert len(notices) == 1
    msg = notices[0]["message"]
    assert "1 TTS chunk " in msg, f"singular form wrong: {msg!r}"
    assert "1 TTS chunks " not in msg, f"plural leaked in for n=1: {msg!r}"


def test_drop_notice_plural_for_multiple_dropped(enable_streaming):
    """2+ → 'chunks' (plural)."""
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    # Two paragraphs (each >= min_chars) → two pump-chunks, both drop
    pump.push("Hello there friend.\n\nMore text follows here.")
    out = list(pump.flush_and_close())
    notices = [e for e in out if e.get("type") == "notice"]
    assert len(notices) == 1
    msg = notices[0]["message"]
    assert "2 TTS chunks " in msg, f"plural form wrong: {msg!r}"


# ─── New diagnostic log lines (visibility regression) ─────────────────────────
# 2026-05-26: added synth-done, emitted, dropped, and end-of-stream-summary
# log lines to close the diagnostic blind spot from the no-audio user report.
# These let future debug sessions see exactly what happened per chunk.

def test_new_diagnostic_log_lines_fire(enable_streaming, caplog):
    """The four new [TTS-STREAM] line classes must all appear for a
    normal multi-chunk stream: synth done, emitted, end-of-stream summary."""
    caplog.set_level("INFO", logger="core.tts.stream_pump")
    fp = _FakeProvider(audio_bytes=b"FAKE_OGG")
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    pump.push("Hello there.\n\nMore text here.")
    list(pump.flush_and_close())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("synth done:" in m and "segments" in m for m in msgs), \
        f"missing 'synth done' log: {msgs}"
    assert any("emitted" in m and "segment" in m for m in msgs), \
        f"missing 'emitted' log: {msgs}"
    assert any("done:" in m and "chunks emitted" in m and "interrupted=False" in m
               for m in msgs), f"missing end-of-stream summary: {msgs}"


def test_zero_segments_logs_dropped_warning(enable_streaming, caplog):
    """When kokoro returns zero segments, a WARNING-level 'chunk N dropped:
    zero playable segments emitted' line must fire — not silent absence."""
    caplog.set_level("INFO", logger="core.tts.stream_pump")
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    pump.push("Hello world.")
    list(pump.flush_and_close())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("synth done:" in m and "0 segments" in m for m in msgs), \
        f"missing zero-segments synth done: {msgs}"
    assert any("dropped: zero playable segments" in m for m in msgs), \
        f"missing dropped warning: {msgs}"


# ─── Bonus edge cases — low-hanging fruit coverage ────────────────────────────

def test_emoji_only_chunk_skipped_before_synth(enable_streaming, caplog):
    """A chunk with no speakable characters (emoji-only) is skipped by the
    `_HAS_SPEECH_RE` filter in _submit — never reaches Kokoro. This is the
    pre-submit guard; distinct from the drop accounting path."""
    caplog.set_level("INFO", logger="core.tts.stream_pump")
    fp = _FakeProvider()
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    # Emoji-only paragraph; Unicode-aware regex matches \w in any script,
    # so we need genuinely punctuation/symbol-only content.
    pump.push("😀 🎉 ✨ 🌊 🔥 🎵 ⭐ 🚀.\n\n")
    out = list(pump.flush_and_close())
    msgs = [r.getMessage() for r in caplog.records]
    # The "no speakable chars" filter logged it
    assert any("no speakable chars" in m for m in msgs), \
        f"emoji chunk should hit pre-submit speakable filter: {msgs}"
    # Provider was NOT called — the chunk got filtered before submit
    assert fp.calls == [], f"provider called despite filter: {fp.calls}"


def test_cjk_chunk_passes_filter_then_dropped_by_provider(enable_streaming):
    """CJK (kokoro_means_heart_in_japanese) passes `_HAS_SPEECH_RE` (Unicode
    letter chars match \\w) but Kokoro returns no audio — the chunk lands
    in _dropped_chunks and the notice fires with the new friendly wording.

    This is the exact scenario from the user report where Sapphire emitted
    the 心 character and got an alarming 'Check Kokoro server health' toast."""
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    # CJK text long enough to satisfy min_chars=15
    pump.push("心 心 心 心 心 心 心 心.\n\n")
    out = list(pump.flush_and_close())
    notices = [e for e in out if e.get("type") == "notice"]
    assert len(notices) == 1, f"expected drop notice for CJK: {notices}"
    msg = notices[0]["message"]
    assert "couldn't be voiced" in msg
    assert "Text is unaffected" in msg


def test_maxlen_boundary_triggers_for_long_text(enable_streaming, monkeypatch):
    """When text exceeds max_chars without a natural boundary, chunker
    force-splits at the last whitespace inside the window. No trailing
    paragraph break in this test (that would short-circuit to a single
    paragraph chunk before maxlen logic runs)."""
    import config
    # Lower max_chars so we don't need to push 200+ chars of text
    monkeypatch.setattr(config, "TTS_STREAMING_MAX_CHARS", 50, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_MIN_CHARS", 15, raising=False)
    fp = _FakeProvider()
    pump = StreamingTTSPump(system=_make_system(provider=fp))
    # Long run of words, no \n\n — chunker hits maxlen before any natural break
    long_text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima."
    pump.push(long_text)
    list(pump.flush_and_close())
    # Provider should have been called at least twice (maxlen split + final flush)
    assert len(fp.calls) >= 2, f"expected maxlen split, got {len(fp.calls)} calls: {fp.calls}"
    # At least one call should have a "maxlen"-shaped chunk (no trailing punctuation)
    chunk_texts = [c[0] for c in fp.calls]
    assert any(not t.endswith(".") for t in chunk_texts), \
        f"no mid-split chunk found: {chunk_texts}"


def test_interrupted_stream_does_not_emit_drop_notice(enable_streaming):
    """When the user cancels (interrupted=True), drop notices are suppressed
    even if some chunks were in flight — they were intentionally killed,
    not lost. Prevents misleading 'chunks couldn't be voiced' warning on Stop."""
    cancelled = {"flag": False}
    fp = _NoAudioStreamingProvider()
    pump = StreamingTTSPump(
        system=_make_system(provider=fp),
        cancel_check=lambda: cancelled["flag"],
    )
    pump.push("Hello there.\n\nMore text here.")
    cancelled["flag"] = True  # user pressed Stop
    out = list(pump.flush_and_close())
    notices = [e for e in out if e.get("type") == "notice"]
    assert notices == [], f"drop notice fired on user cancel: {notices}"
    # Should still see the end event with interrupted=True
    ends = [e for e in out if e.get("type") == "tts_stream_end"]
    assert ends and ends[0]["interrupted"] is True


def test_multiple_drops_aggregate_into_one_notice(enable_streaming):
    """N dropped chunks produce exactly ONE notice SSE event with count=N,
    not N separate notices. Prevents toast spam on a flaky run."""
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    # Three paragraphs → 3 chunks, all drop (provider yields nothing)
    pump.push("First paragraph.\n\nSecond paragraph.\n\nThird paragraph here.")
    out = list(pump.flush_and_close())
    notices = [e for e in out if e.get("type") == "notice"]
    assert len(notices) == 1, f"expected ONE aggregated notice, got {len(notices)}: {notices}"
    assert "3 TTS chunks " in notices[0]["message"], \
        f"count not aggregated correctly: {notices[0]['message']!r}"


def test_end_of_stream_summary_log_includes_dropped_count(enable_streaming, caplog):
    """The end-of-stream summary log line must include the dropped count
    so future diagnostics can see partial-failure runs at a glance."""
    caplog.set_level("INFO", logger="core.tts.stream_pump")
    pump = StreamingTTSPump(system=_make_system(provider=_NoAudioStreamingProvider()))
    pump.push("Hello there friend.\n\nMore text follows here.")
    list(pump.flush_and_close())
    msgs = [r.getMessage() for r in caplog.records]
    end_summary = [m for m in msgs if "done:" in m and "chunks emitted" in m]
    assert end_summary, f"missing end-of-stream summary: {msgs}"
    assert "2 dropped" in end_summary[0], \
        f"end summary doesn't include drop count: {end_summary[0]!r}"


# --- force: a satellite's turn runs the pump whatever the switch says ----------------

def test_a_forced_pump_runs_with_the_streaming_switch_off_and_a_blob_provider(monkeypatch):
    import config
    monkeypatch.setattr(config, "TTS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", False, raising=False)

    class Blob:                                  # a cloud voice: one blob per request, no stream door
        audio_content_type = "audio/ogg"
        supports_streaming = False

        def __init__(self):
            self.calls = []

        def generate(self, text, voice, speed):
            self.calls.append(text)
            return b"OGGS-" + text.encode()
    provider = Blob()
    with patch("core.voice_privacy.tts_gate_reason", return_value=None):
        pump = StreamingTTSPump(system=_make_system(provider=provider), force=True,
                                split_override="sentence", chat_settings={"private_chat": False})
        assert pump.enabled is True
        events = pump.push("It is noon here. The sun is out. ")
        events += list(pump.flush_and_close())
    chunks = [e for e in events if e["type"] == "tts_chunk"]
    assert [c["text"] for c in chunks] == ["It is noon here.", "The sun is out."]
    assert provider.calls == ["It is noon here.", "The sun is out."]      # rendered one sentence at a time


def test_a_forced_pump_still_needs_a_voice_engine(monkeypatch):
    import config
    monkeypatch.setattr(config, "TTS_STREAMING_ENABLED", False, raising=False)
    assert StreamingTTSPump(system=_make_system(tts_attr=False), force=True).enabled is False
