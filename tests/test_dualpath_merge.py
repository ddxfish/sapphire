"""
Million Dollar Bug Hunt — dual-path merge (2026-08-17).

chat_streaming.chat_stream() is THE one turn engine. LLMChat.chat() is a
blocking CONSUMER of it: run the generator to completion, keep the final
text, discard the play-by-play. The old ~470-line parallel blocking engine
(_chat_inner) is gone.

Contract under test:
  1. The engine emits a terminal {"type": "final", "text", "cancelled",
     "error"} event on every clean exit (normal, cancel, skip_llm hook,
     vault refusal, forced-final). Raise paths emit none.
  2. The consumer prefers the final event's text, falls back to accumulated
     content (think-stripped) if an exit ever misses its final, converts
     notice events to pending_notices, never raises to the doors, and never
     writes a second error row (the engine saves before raising).
  3. suppress_tts on the stream keeps the TTS pump inert (the blocking
     doors speak the returned blob themselves — no double-speak).

Run with: pytest tests/test_dualpath_merge.py -v
"""
from unittest.mock import MagicMock, patch

import pytest

import config


# =============================================================================
# The consumer (LLMChat.chat) — fake stream, real consumer logic
# =============================================================================

class FakeStream:
    """Scripted chat_stream: yields events, optionally raising mid-stream."""

    def __init__(self, events=None, exc=None, raise_after=None):
        self.events = list(events or [])
        self.exc = exc
        self.raise_after = len(self.events) if raise_after is None else raise_after

    def chat_stream(self, user_input, **kwargs):
        for i, ev in enumerate(self.events):
            if self.exc is not None and i == self.raise_after:
                raise self.exc
            yield ev
        if self.exc is not None and self.raise_after >= len(self.events):
            raise self.exc


def _consumer(stream):
    """Bare LLMChat wired to a scripted stream; returns (obj, end_calls)."""
    from core.chat.chat import LLMChat
    with patch.object(LLMChat, '__init__', lambda self: None):
        obj = LLMChat()
    obj.pending_notices = []
    obj.session_manager = MagicMock()
    end_calls = []
    obj.begin_stream = lambda chat_name: (stream, "sid1", "chat1")
    obj.end_stream = lambda sid, chat: end_calls.append((sid, chat))
    return obj, end_calls


class TestBlockingConsumer:
    def test_returns_final_event_text(self):
        s = FakeStream([
            {"type": "stream_started"},
            {"type": "content", "text": "Hel"},
            {"type": "content", "text": "lo"},
            {"type": "final", "text": "Hello", "cancelled": False, "error": False},
        ])
        obj, end_calls = _consumer(s)
        assert obj.chat("hi") == "Hello"
        assert end_calls == [("sid1", "chat1")]

    def test_final_text_preferred_over_content_concat(self):
        """The final event carries the canonical text (prefills, hook
        mutations) — content concat is only the defensive fallback."""
        s = FakeStream([
            {"type": "content", "text": "streamed view"},
            {"type": "final", "text": "canonical view", "cancelled": False, "error": False},
        ])
        obj, _ = _consumer(s)
        assert obj.chat("hi") == "canonical view"

    def test_fallback_concat_strips_think_tags(self):
        """No final event (defensive path): accumulated content includes
        UI-wrapped thinking — voice must not read reasoning aloud."""
        s = FakeStream([
            {"type": "content", "text": "<think>"},
            {"type": "content", "text": "secret reasoning"},
            {"type": "content", "text": "</think>\n\n"},
            {"type": "content", "text": "The answer is 4."},
        ])
        obj, _ = _consumer(s)
        assert obj.chat("hi") == "The answer is 4."

    def test_notice_events_become_pending_notices(self):
        s = FakeStream([
            {"type": "notice", "message": "Toolset 'x' is missing", "severity": "warning"},
            {"type": "final", "text": "ok", "cancelled": False, "error": False},
        ])
        obj, _ = _consumer(s)
        assert obj.chat("hi") == "ok"
        assert obj.pending_notices == [
            {"message": "Toolset 'x' is missing", "severity": "warning"}]

    def test_play_by_play_events_ignored(self):
        s = FakeStream([
            {"type": "iteration_start", "iteration": 1},
            {"type": "tool_pending", "name": "t", "index": 0},
            {"type": "tool_start", "id": "1", "name": "t", "args": {}},
            {"type": "tool_end", "id": "1", "name": "t", "result": "r", "error": False},
            {"type": "tts_stream_start", "stream_id": "s"},
            {"type": "tts_chunk", "audio": "..."},
            {"type": "tts_stream_end", "stream_id": "s"},
            {"type": "final", "text": "done", "cancelled": False, "error": False},
        ])
        obj, _ = _consumer(s)
        assert obj.chat("hi") == "done"

    def test_cancelled_final_returns_partial(self):
        s = FakeStream([
            {"type": "content", "text": "partial pro"},
            {"type": "final", "text": "partial pro", "cancelled": True, "error": False},
        ])
        obj, _ = _consumer(s)
        assert obj.chat("hi") == "partial pro"

    def test_suppress_tts_set_before_streaming(self):
        s = FakeStream([{"type": "final", "text": "x", "cancelled": False, "error": False}])
        obj, _ = _consumer(s)
        obj.chat("hi")
        assert getattr(s, "suppress_tts", False) is True

    def test_raise_returns_friendly_string_never_raises(self):
        s = FakeStream([{"type": "content", "text": "st"}],
                       exc=ConnectionError("no llm providers available"), raise_after=1)
        obj, end_calls = _consumer(s)
        out = obj.chat("hi")
        assert "No LLM providers" in out
        assert end_calls, "end_stream must run even on raise"

    def test_privacy_block_message_passes_through_verbatim(self):
        msg = "This prompt is marked private — unlock the vault first."
        s = FakeStream(exc=ConnectionError(msg))
        obj, _ = _consumer(s)
        assert obj.chat("hi") == msg

    def test_consumer_never_saves_a_second_error_row(self):
        """The engine saves the error row before raising (chat_stream's
        outer handlers) — the consumer writing another would double-log."""
        s = FakeStream(exc=RuntimeError("boom"))
        obj, _ = _consumer(s)
        out = obj.chat("hi")
        assert "unexpected technical issue" in out
        obj.session_manager.add_assistant_final.assert_not_called()

    def test_legacy_string_events_collected_in_fallback(self):
        s = FakeStream(["legacy module response"])
        obj, _ = _consumer(s)
        assert obj.chat("hi") == "legacy module response"


class TestConsumerOnEvent:
    def test_on_event_sees_every_event_in_order(self):
        events = [
            {"type": "stream_started"},
            {"type": "content", "text": "Hi"},
            {"type": "final", "text": "Hi", "cancelled": False, "error": False},
        ]
        obj, _ = _consumer(FakeStream(events))
        seen = []
        assert obj.chat("hi", on_event=seen.append) == "Hi"
        assert seen == events

    def test_broken_on_event_does_not_kill_turn(self):
        obj, _ = _consumer(FakeStream([
            {"type": "content", "text": "Hi"},
            {"type": "final", "text": "Hi", "cancelled": False, "error": False},
        ]))
        calls = []

        def boom(ev):
            calls.append(ev)
            raise RuntimeError("observer died")

        assert obj.chat("hi", on_event=boom) == "Hi"
        assert len(calls) == 1   # dropped after first failure, turn unharmed


# =============================================================================
# The wake door — process_llm_query(voice_turn=True) event contract
# =============================================================================

def _bare_system(chat_side_effect):
    import threading as _threading
    from sapphire import VoiceChatSystem
    sys_obj = VoiceChatSystem.__new__(VoiceChatSystem)
    sys_obj._processing_lock = _threading.Lock()
    sys_obj.llm_chat = MagicMock()
    sys_obj.llm_chat.get_active_chat.return_value = "default"
    sys_obj.llm_chat.chat.side_effect = chat_side_effect
    sys_obj.llm_chat.pending_notices = []
    sys_obj.tts = MagicMock()
    return sys_obj


class TestVoiceTurnEvents:
    def test_voice_turn_publishes_start_chunks_end_and_speaks(self):
        def fake_chat(query, on_event=None):
            on_event({"type": "content", "text": "Hel"})
            on_event({"type": "content", "text": "lo"})
            on_event({"type": "final", "text": "Hello",
                      "cancelled": False, "error": False})
            return "Hello"

        sys_obj = _bare_system(fake_chat)
        published = []
        with patch('sapphire.publish',
                   side_effect=lambda et, data=None, **kw: published.append((et, data))):
            out = sys_obj.process_llm_query("hi there", voice_turn=True)

        assert out == "Hello"
        types = [et for et, _ in published]
        assert types == ["voice_turn_start", "voice_turn_chunk",
                         "voice_turn_chunk", "voice_turn_end"]
        start = published[0][1]
        assert start["user_text"] == "hi there"
        assert start["foreign"] is False
        assert "".join(d["text"] for et, d in published
                       if et == "voice_turn_chunk") == "Hello"
        assert published[-1][1]["cancelled"] is False
        sys_obj.tts.speak.assert_called_once_with("Hello")

    def test_cancelled_voice_turn_does_not_speak(self):
        def fake_chat(query, on_event=None):
            on_event({"type": "content", "text": "partial"})
            on_event({"type": "final", "text": "partial",
                      "cancelled": True, "error": False})
            return "partial"

        sys_obj = _bare_system(fake_chat)
        published = []
        with patch('sapphire.publish',
                   side_effect=lambda et, data=None, **kw: published.append((et, data))):
            out = sys_obj.process_llm_query("hi", voice_turn=True)

        assert out == "partial"          # partial still returned + in history
        sys_obj.tts.speak.assert_not_called()
        assert published[-1][0] == "voice_turn_end"
        assert published[-1][1]["cancelled"] is True

    def test_default_doors_publish_nothing(self):
        """REST /api/chat and body keep the quiet path — no live paint of
        another client's turn into whatever chat the operator has open."""
        sys_obj = _bare_system(lambda query, on_event=None: "ok")
        published = []
        with patch('sapphire.publish',
                   side_effect=lambda et, data=None, **kw: published.append(et)):
            out = sys_obj.process_llm_query("hi", skip_tts=True)

        assert out == "ok"
        assert published == []
        # and the consumer got no observer to feed
        assert sys_obj.llm_chat.chat.call_args.kwargs.get("on_event") is None

    def test_eviction_mid_turn_withholds_tts(self):
        """F1 post-eviction cloud-TTS corner: a vault seal mid-turn evicts to
        a public landing chat BEFORE the speak call — the reply was generated
        in the private chat and must not reach TTS through the landing
        chat's permissive gate."""
        sys_obj = _bare_system(lambda query, on_event=None: "private reply")
        # Turn starts in the private chat; by speak time the world moved.
        sys_obj.llm_chat.get_active_chat.side_effect = ["secrets", "default"]
        out = sys_obj.process_llm_query("hi")

        assert out == "private reply"      # caller still gets the text
        sys_obj.tts.speak.assert_not_called()

    def test_stable_chat_speaks_normally(self):
        sys_obj = _bare_system(lambda query, on_event=None: "hello")
        sys_obj.llm_chat.get_active_chat.side_effect = ["default", "default"]
        assert sys_obj.process_llm_query("hi") == "hello"
        sys_obj.tts.speak.assert_called_once_with("hello")

    def test_end_event_fires_even_when_chat_raises(self):
        def fake_chat(query, on_event=None):
            raise RuntimeError("engine died unexpectedly")

        sys_obj = _bare_system(fake_chat)
        sys_obj.speak_error = MagicMock()
        published = []
        with patch('sapphire.publish',
                   side_effect=lambda et, data=None, **kw: published.append(et)):
            sys_obj.process_llm_query("hi", voice_turn=True)

        assert published[0] == "voice_turn_start"
        assert published[-1] == "voice_turn_end"   # bubble always reconciles


# =============================================================================
# The engine — real chat_stream, mocked provider: final-event contract
# =============================================================================

def _engine_run(provider_events, suppress_tts=False, cancel_via=None,
                stamped_chat=None, hook_stub=None):
    """Drive the REAL chat_stream with a mocked main_chat + provider.
    Returns (stream_obj, collected_events)."""
    from core.chat.chat_streaming import StreamingChat

    mock_main = MagicMock()
    mock_main.system = None                       # pump: no tts provider
    fm = mock_main.function_manager
    fm.snapshot_scopes.return_value = {}
    fm.snapshot_executors.return_value = {}
    fm.enabled_tools = []
    fm.last_dangling_toolset = None
    sm = mock_main.session_manager
    sm.get_chat_settings.return_value = {}
    sm.get_active_chat_name.return_value = "t_chat"
    sm._effective_chat_name.return_value = "t_chat"
    sm._in_tool_cycle = False
    mock_main._build_base_messages.return_value = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
    ]
    provider = MagicMock()
    provider.provider_name = "test"
    provider.model = "test-model"
    provider.chat_completion_stream.return_value = iter(provider_events)
    mock_main._select_provider.return_value = ("test", provider, "")
    mock_main.tool_engine.extract_function_call_from_text.return_value = None

    sc = StreamingChat(mock_main)
    if suppress_tts:
        sc.suppress_tts = True
    if stamped_chat is not None:
        sc.active_chat_name = stamped_chat       # begin_stream's stamp
    if cancel_via is not None:
        cancel_via(sc)

    patches = [
        patch('core.chat.chat_streaming.get_generation_params', return_value={}),
        patch.object(config, 'FORCE_THINKING', False, create=True),
    ]
    if hook_stub is not None:
        patches.append(patch('core.chat.chat_streaming.hook_runner', hook_stub))
    out = []
    try:
        for p in patches:
            p.start()
        out = list(sc.chat_stream("hello"))
    finally:
        for p in reversed(patches):
            p.stop()
    return sc, out


def _finals(events):
    return [e for e in events if isinstance(e, dict) and e.get("type") == "final"]


class TestEngineFinalEvent:
    def test_normal_turn_emits_one_final_matching_content(self):
        _, out = _engine_run([
            {"type": "content", "text": "Hello "},
            {"type": "content", "text": "world"},
            {"type": "done", "response": None},
        ])
        finals = _finals(out)
        assert len(finals) == 1
        assert finals[0] == {"type": "final", "text": "Hello world",
                             "cancelled": False, "error": False}
        content = "".join(e.get("text", "") for e in out
                          if isinstance(e, dict) and e.get("type") == "content")
        assert content == finals[0]["text"]
        # Terminal position: nothing after the final event
        assert out[-1]["type"] == "final"

    def test_cancel_emits_final_with_cancelled_flag(self):
        """Barge-in mid-generation: the partial is saved and the final event
        carries it with cancelled=True."""
        holder = {}

        class LateIter:
            # Lazy: the generator body reads holder at first next(), which
            # happens inside chat_stream — after cancel_via stashed the sc.
            def __iter__(self):
                def gen():
                    yield {"type": "content", "text": "partial"}
                    holder["sc"].cancel_flag = True
                    yield {"type": "content", "text": " never seen"}
                return gen()

        sc, out = _engine_run(LateIter(),
                              cancel_via=lambda sc: holder.__setitem__("sc", sc))
        finals = _finals(out)
        assert len(finals) == 1
        assert finals[0]["cancelled"] is True
        assert finals[0]["text"] == "partial"

    def test_skip_llm_hook_emits_final(self):
        class HookStub:
            def has_handlers(self, name):
                return name == "pre_chat"

            def fire(self, name, event=None):
                if name == "pre_chat" and event is not None:
                    event.skip_llm = True
                    event.response = "canned reply"
                    event.ephemeral = False
                return event

        _, out = _engine_run([], hook_stub=HookStub())
        finals = _finals(out)
        assert len(finals) == 1
        assert finals[0]["text"] == "canned reply"
        assert finals[0]["error"] is False

    def test_stamped_chat_mismatch_refusal_emits_final(self):
        """Vault R3 refusal (active chat moved during setup) now reaches
        blocking consumers as a final too — voice hears the refusal."""
        _, out = _engine_run([{"type": "done", "response": None}],
                             stamped_chat="other_chat")
        finals = _finals(out)
        assert len(finals) == 1
        assert finals[0]["error"] is True
        assert "vault locked" in finals[0]["text"].lower() or "🔒" in finals[0]["text"]

    def test_llm_raise_saves_error_row_before_raising(self):
        """Raise paths: NO final event (the consumer's except handles the
        raise), and the engine saves the error row before re-raising —
        which is why the consumer must never write a second one."""
        from core.chat.chat_streaming import StreamingChat
        mock_main = MagicMock()
        mock_main.system = None
        mock_main.function_manager.snapshot_scopes.return_value = {}
        mock_main.function_manager.snapshot_executors.return_value = {}
        mock_main.function_manager.enabled_tools = []
        mock_main.function_manager.last_dangling_toolset = None
        mock_main.session_manager.get_chat_settings.return_value = {}
        mock_main.session_manager.get_active_chat_name.return_value = "t"
        mock_main.session_manager._in_tool_cycle = False
        mock_main._build_base_messages.return_value = [{"role": "user", "content": "hi"}]
        provider = MagicMock()
        provider.provider_name = "test"
        provider.model = "m"
        provider.chat_completion_stream.side_effect = ConnectionError("dead provider")
        mock_main._select_provider.return_value = ("test", provider, "")

        sc = StreamingChat(mock_main)
        out = []
        with patch('core.chat.chat_streaming.get_generation_params', return_value={}), \
             patch.object(config, 'FORCE_THINKING', False, create=True):
            with pytest.raises(ConnectionError):
                for ev in sc.chat_stream("hello"):
                    out.append(ev)
        assert not _finals(out), "raise paths must not emit a final event"
        saved = mock_main.session_manager.add_assistant_final.call_args_list
        assert saved, "engine must save an error row before re-raising"
        assert "dead provider" in str(saved[0])

    def test_suppress_tts_disables_pump(self):
        sc, out = _engine_run([{"type": "content", "text": "hi"},
                               {"type": "done", "response": None}],
                              suppress_tts=True)
        assert sc.tts_pump._skip_turn is True
        assert not any(isinstance(e, dict) and str(e.get("type", "")).startswith("tts_")
                       for e in out)

    def test_no_suppress_leaves_pump_armed(self):
        sc, _ = _engine_run([{"type": "content", "text": "hi"},
                             {"type": "done", "response": None}])
        assert sc.tts_pump._skip_turn is False


# =============================================================================
# The pump's disabled mode
# =============================================================================

class TestPumpDisabled:
    def test_disabled_pump_is_inert(self):
        from core.tts.stream_pump import StreamingTTSPump
        pump = StreamingTTSPump(system=None, disabled=True)
        assert pump.push("Hello there, this is a full sentence.") == []
        assert list(pump.flush_and_close()) == []   # silent — no tts_stream_end
        pump.cancel()                               # no-op, no raise

    def test_default_pump_not_disabled(self):
        from core.tts.stream_pump import StreamingTTSPump
        pump = StreamingTTSPump(system=None)
        assert pump._skip_turn is False
        assert pump._closed is False
