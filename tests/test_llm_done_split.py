"""LLM-done split — 2026-09-08 (record: tmp/llm-done-split-plan.md).

The SSE `done` was the only end-of-turn signal on the wire and it waited on
the streaming-TTS drain (flush_and_close): seconds of Kokoro on a slow CPU
during which Stop LLM stayed up, metrics didn't paint, and AI_TYPING_END
didn't fire. Now the engine yields `llm_done` the moment the history row is
written, publishes AI_TYPING_END there (exactly once), and
begin_stream(exclusive=True) stops counting a tail-only stream as live —
muting its pump when a new turn is admitted.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest

import config
from core.chat.chat import LLMChat, ChatBusy
from core.chat.chat_streaming import StreamingChat


# ─── engine harness ─────────────────────────────────────────────────────────

class _StreamingProvider:
    audio_content_type = "audio/ogg"
    supports_streaming = True

    def __init__(self):
        self.calls = []

    def generate_stream(self, text, voice, speed, **kw):
        self.calls.append(text)
        yield b"OGGS_fake_" + text.encode()[:8]


def _run(provider_events, tts=False, chat_settings=None, hook_stub=None,
         cancel_on=None):
    """Drive the REAL chat_stream. Returns (stream, events, published) where
    published = [(event_name, n_events_yielded_so_far)] so a publish can be
    placed relative to the yielded sequence."""
    mock_main = MagicMock()
    if tts:
        sys_obj = MagicMock()
        sys_obj.tts._provider = _StreamingProvider()
        sys_obj.tts.voice_name = "af_heart"
        sys_obj.tts.speed = 1.0
        sys_obj.tts.pitch_shift = 1.0
        mock_main.system = sys_obj
    else:
        mock_main.system = None
    fm = mock_main.function_manager
    fm.snapshot_scopes.return_value = {}
    fm.snapshot_executors.return_value = {}
    fm.enabled_tools = []
    fm.last_dangling_toolset = None
    sm = mock_main.session_manager
    sm.get_chat_settings.return_value = chat_settings or {}
    sm.get_active_chat_name.return_value = "t_chat"
    sm._effective_chat_name.return_value = "t_chat"
    sm._in_tool_cycle = False
    mock_main._build_base_messages.return_value = [{"role": "user", "content": "hi"}]
    provider = MagicMock()
    provider.provider_name = "test"
    provider.model = "test-model"
    provider.chat_completion_stream.return_value = iter(provider_events)
    mock_main._select_provider.return_value = ("test", provider, "")
    mock_main.tool_engine.extract_function_call_from_text.return_value = None

    sc = StreamingChat(mock_main)
    out, published = [], []
    patches = [
        patch('core.chat.chat_streaming.get_generation_params', return_value={}),
        patch.object(config, 'FORCE_THINKING', False, create=True),
        patch.object(config, 'TTS_ENABLED', tts, create=True),
        patch.object(config, 'TTS_STREAMING_ENABLED', tts, create=True),
        patch('core.voice_privacy.tts_gate_reason', return_value=""),
        patch('core.chat.chat_streaming.publish',
              side_effect=lambda et, data=None, **kw: published.append((et, len(out)))),
    ]
    if hook_stub is not None:
        patches.append(patch('core.chat.chat_streaming.hook_runner', hook_stub))
    for p in patches:
        p.start()
    try:
        for ev in sc.chat_stream("hello"):
            out.append(ev)
            if cancel_on and isinstance(ev, dict) and ev.get("type") == "content" \
                    and cancel_on in ev.get("text", ""):
                sc.cancel_flag = True
    finally:
        for p in reversed(patches):
            p.stop()
    return sc, out, published


def _idx(events, etype):
    return [i for i, e in enumerate(events)
            if isinstance(e, dict) and e.get("type") == etype]


# ─── engine: llm_done placement ─────────────────────────────────────────────

class TestLlmDoneEvent:
    def test_llm_done_lands_before_the_tts_drain(self):
        """The whole point: history is complete BEFORE flush_and_close, and
        the wire hears it then — the chunk held for the drain (paragraph
        mode keeps the last paragraph until flush) comes AFTER llm_done."""
        sc, out, published = _run([
            {"type": "content", "text": "First para.\n\n"},
            {"type": "content", "text": "Second para."},
            {"type": "done", "response": None},
        ], tts=True)
        ld = _idx(out, "llm_done")
        assert len(ld) == 1
        (i_ld,) = ld
        (i_end,) = _idx(out, "tts_stream_end")
        (i_final,) = _idx(out, "final")
        assert i_ld < i_end < i_final
        assert out[i_ld]["tts_streamed"] is True
        assert sc.llm_done is True
        # The flush-held chunk is the tail
        tail = [i for i, e in enumerate(out) if isinstance(e, dict)
                and e.get("type") == "tts_chunk" and "Second" in e.get("text", "")]
        assert tail and tail[0] > i_ld
        # AI_TYPING_END: exactly once, and at the llm_done moment (not the finally)
        ends = [n for et, n in published if et == "ai_typing_end"]
        assert ends == [i_ld]

    def test_no_pump_reports_tts_streamed_false(self):
        """Streaming TTS off / no provider: the browser falls back to whole-
        blob playback ONLY on this signal."""
        _, out, published = _run([
            {"type": "content", "text": "Hello"},
            {"type": "done", "response": None},
        ], tts=False)
        (i_ld,) = _idx(out, "llm_done")
        assert out[i_ld]["tts_streamed"] is False
        assert i_ld < _idx(out, "final")[0]
        assert [et for et, _ in published].count("ai_typing_end") == 1

    def test_user_muted_tail_still_counts_as_streamed(self):
        """Mic ⏹ mid-turn sets tts_stopped; the pump ran, so no fallback —
        this is the exact replay-from-the-top bug."""
        sc, out, _ = _run([
            {"type": "content", "text": "Hello there.\n\n"},
            {"type": "content", "text": "Bye."},
            {"type": "done", "response": None},
        ], tts=True)
        # Simulate the mute landing before the drain would have run: the
        # flag the browser reads must not depend on tts_stopped.
        (i_ld,) = _idx(out, "llm_done")
        assert out[i_ld]["tts_streamed"] is True

    def test_cancel_emits_no_llm_done_and_typing_end_once(self):
        """Stop LLM: route breaks on cancel_flag before anything more reaches
        the wire; typing-end comes from the finally, exactly once."""
        sc, out, published = _run([
            {"type": "content", "text": "Hel"},
            {"type": "content", "text": "lo"},
            {"type": "content", "text": " world"},
            {"type": "done", "response": None},
        ], cancel_on="Hel")
        assert _idx(out, "llm_done") == []
        assert sc.llm_done is False
        finals = [out[i] for i in _idx(out, "final")]
        assert finals and finals[0]["cancelled"] is True
        assert [et for et, _ in published].count("ai_typing_end") == 1

    def test_limbo_refusal_emits_llm_done_before_final(self):
        _, out, published = _run([], chat_settings={"mode": "limbo"})
        (i_ld,) = _idx(out, "llm_done")
        (i_final,) = _idx(out, "final")
        assert i_ld < i_final
        assert out[i_ld]["tts_streamed"] is False
        assert [et for et, _ in published].count("ai_typing_end") == 1

    def test_pre_chat_skip_publishes_typing_end_exactly_once(self):
        """Before the split this path published AI_TYPING_END explicitly AND
        again in the finally (double flip on mirror tabs). One now."""
        class HookStub:
            def has_handlers(self, name):
                return name == "pre_chat"

            def fire(self, name, event=None):
                if name == "pre_chat" and event is not None:
                    event.skip_llm = True
                    event.response = "canned reply"
                    event.ephemeral = False
                return event

        _, out, published = _run([], hook_stub=HookStub())
        (i_ld,) = _idx(out, "llm_done")
        (i_final,) = _idx(out, "final")
        assert i_ld < i_final
        assert [et for et, _ in published].count("ai_typing_end") == 1


# ─── gate: tail-only streams don't occupy ───────────────────────────────────

@pytest.fixture
def llm():
    with patch.object(LLMChat, '__init__', lambda self: None):
        obj = LLMChat()
    obj._streams_by_id = {}
    obj._streams_by_chat = {}
    obj._streams_lock = threading.Lock()
    obj.tool_engine = MagicMock()
    obj.session_manager = MagicMock()
    obj.session_manager.get_active_chat_name.return_value = 'trinity'
    return obj


def test_tail_only_stream_does_not_occupy(llm):
    s1, _, _ = llm.begin_stream(exclusive=True)
    with pytest.raises(ChatBusy):
        llm.begin_stream(exclusive=True)          # still generating: refused
    s1.llm_done = True                            # history written, audio tail
    s2, sid2, _ = llm.begin_stream(exclusive=True)
    assert sid2 in llm._streams_by_id
    assert s2.llm_done is False


def test_admitting_a_turn_mutes_the_tail_pump(llm):
    """A slow CPU must not keep synthesizing audio the browser already
    drops (new tts_stream_start preempts the old stream_id)."""
    s1, _, _ = llm.begin_stream(exclusive=True)
    s1.llm_done = True
    s1.tts_pump = MagicMock()
    s1.tts_pump._skip_turn = False
    llm.begin_stream(exclusive=True)
    assert s1.tts_stopped is True
    assert s1.tts_pump._skip_turn is True


def test_non_exclusive_admit_leaves_tails_alone(llm):
    """chat() / phone lanes pass no flag — they never muted anything before
    and still don't."""
    s1, _, _ = llm.begin_stream(exclusive=True)
    s1.llm_done = True
    llm.begin_stream()
    assert s1.tts_stopped is False


# ─── route: llm_done rides the wire before done ─────────────────────────────

def test_stream_route_passes_llm_done_before_done(client, mock_system):
    c, csrf = client
    stream = MagicMock()
    stream.cancel_flag = False
    stream.ephemeral = False
    stream.chat_stream.return_value = iter([
        {"type": "content", "text": "hello"},
        {"type": "llm_done", "tts_streamed": True},
        {"type": "tts_stream_end", "stream_id": "s", "chunk_count": 0, "interrupted": False},
        {"type": "final", "text": "hello", "cancelled": False, "error": False},
    ])
    mock_system.llm_chat.begin_stream.return_value = (stream, 'sid1', 'trinity')
    r = c.post('/api/chat/stream', json={'text': 'hi'},
               headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    i_ld = r.text.find('"type": "llm_done"')
    i_end = r.text.find('"type": "tts_stream_end"')
    i_done = r.text.find('"done": true')
    assert 0 <= i_ld < i_end < i_done
    assert '"tts_streamed": true' in r.text
    assert '"type": "final"' not in r.text
