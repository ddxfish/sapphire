"""In-place Continue — 2026-09-10 (record: tmp/continue-in-place-plan.md).

The old Continue popped the turn's last assistant row, deleted the turn's
bubble, then re-sent the ORIGINAL user text after the tool result with the
popped prose as a trailing prefill. Three faults: the model saw a fresh user
turn (never continued), a Stop before the first token re-saved nothing (the
sentence was gone for good), and the error path overwrote the prefill with
an error row.

Now `continue_from=<turn timestamp>`: the row stays in history, the engine
reads it as the prefill (session_manager.continue_target), sends history
with nothing appended (_build_base_messages continue_mode), and writes the
result INTO that row (continue_assistant) when the continuation was pure
prose. Stop with nothing generated touches nothing. Tools during a
continuation append rows after the untouched prefill row.

Run with: pytest tests/test_continue_in_place.py -v
"""
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
from core.chat.chat_streaming import StreamingChat

ROOT = Path(__file__).resolve().parent.parent
PREFILL = "Half a sent"


# ─── engine harness (shape of tests/test_llm_done_split._run) ───────────────

def _run(provider_events=(), *, continue_from="T1", prefill=PREFILL, raise_exc=None,
         cancel_on=None, cancel_at_start=False, hook_stub=None, provider_side_effect=None):
    mock_main = MagicMock()
    mock_main.system = None
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
    sm.continue_target.return_value = prefill
    mock_main._build_base_messages.return_value = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": (prefill or "") + "  ", "thinking": "old"},
    ]
    provider = MagicMock()
    provider.provider_name = "test"
    provider.model = "test-model"
    if raise_exc is not None:
        provider.chat_completion_stream.side_effect = raise_exc
    elif provider_side_effect is not None:
        provider.chat_completion_stream.side_effect = provider_side_effect
    else:
        provider.chat_completion_stream.return_value = iter(list(provider_events))
    mock_main._select_provider.return_value = ("test", provider, "")
    mock_main.tool_engine.extract_function_call_from_text.return_value = None
    sc = StreamingChat(mock_main)
    out = []
    patches = [
        patch('core.chat.chat_streaming.get_generation_params', return_value={}),
        patch.object(config, 'FORCE_THINKING', False, create=True),
        patch.object(config, 'TTS_ENABLED', False, create=True),
        patch.object(config, 'TTS_STREAMING_ENABLED', False, create=True),
        patch('core.voice_privacy.tts_gate_reason', return_value=""),
        patch('core.chat.chat_streaming.publish', lambda *a, **k: None),
    ]
    if hook_stub is not None:
        patches.append(patch('core.chat.chat_streaming.hook_runner', hook_stub))
    for p in patches:
        p.start()
    try:
        for ev in sc.chat_stream("", continue_from=continue_from):
            out.append(ev)
            if not isinstance(ev, dict):
                continue
            if cancel_at_start and ev.get("type") == "iteration_start":
                sc.cancel_flag = True
            if cancel_on and ev.get("type") == "content" and cancel_on in ev.get("text", ""):
                sc.cancel_flag = True
    finally:
        for p in reversed(patches):
            p.stop()
    return sc, out, mock_main, provider


def _content(out):
    return [e["text"] for e in out if isinstance(e, dict) and e.get("type") == "content"]


def _final(out):
    return next(e for e in out if isinstance(e, dict) and e.get("type") == "final")


class TestEngine:
    def test_pure_prose_continuation_is_written_into_the_row(self):
        sc, out, main, provider = _run([
            {"type": "content", "text": "ence"},
            {"type": "content", "text": " done."},
            {"type": "done", "response": None},
        ])
        sm = main.session_manager
        sm.continue_target.assert_called_once_with("T1")
        sm.continue_assistant.assert_called_once()
        args, kwargs = sm.continue_assistant.call_args
        assert args == ("T1", "Half a sentence done.")
        sm.add_assistant_final.assert_not_called()      # no second row
        sm.add_user_message.assert_not_called()         # no user row
        assert _final(out)["text"] == "Half a sentence done."
        # The prefill is already on the page — never echoed to the wire.
        assert PREFILL not in "".join(_content(out))
        assert _content(out) == ["ence", " done."]

    def test_prompt_is_history_as_is_with_the_row_as_its_tail(self):
        _, out, main, provider = _run([{"type": "done", "response": None}])
        main._build_base_messages.assert_called_once()
        assert main._build_base_messages.call_args.kwargs["continue_mode"] is True
        sent = provider.chat_completion_stream.call_args.args[0]
        tail = sent[-1]
        assert tail["role"] == "assistant"
        assert tail["content"] == PREFILL                 # rstripped, string
        assert "thinking" not in tail and "thinking_raw" not in tail
        assert [m["role"] for m in sent] == ["system", "user", "assistant"]
        gp = provider.chat_completion_stream.call_args.kwargs["generation_params"]
        assert gp.get("disable_thinking") is True

    def test_pre_chat_hook_does_not_fire_on_a_continue(self):
        hooks = MagicMock()
        hooks.has_handlers.side_effect = lambda name: name == "pre_chat"
        _run([{"type": "done", "response": None}], hook_stub=hooks)
        assert not any(c.args and c.args[0] == "pre_chat" for c in hooks.fire.call_args_list)

    def test_stop_before_any_prose_touches_nothing(self):
        _, out, main, _ = _run([
            {"type": "content", "text": "ence"},
            {"type": "done", "response": None},
        ], cancel_at_start=True)
        sm = main.session_manager
        sm.continue_assistant.assert_not_called()
        sm.add_assistant_final.assert_not_called()
        fin = _final(out)
        assert fin["cancelled"] is True and fin["text"] == ""

    def test_stop_after_partial_prose_writes_the_partial_into_the_row(self):
        _, out, main, _ = _run([
            {"type": "content", "text": "ence"},
            {"type": "content", "text": " never seen"},
            {"type": "done", "response": None},
        ], cancel_on="ence")
        sm = main.session_manager
        sm.continue_assistant.assert_called_once()
        assert sm.continue_assistant.call_args.args == ("T1", "Half a sentence")
        sm.add_assistant_final.assert_not_called()
        assert _final(out)["cancelled"] is True

    def test_provider_error_writes_no_error_row(self):
        main_ref = []
        orig_init = StreamingChat.__init__

        def init(self, main_chat, *a, **k):
            main_ref.append(main_chat)
            orig_init(self, main_chat, *a, **k)

        with patch.object(StreamingChat, "__init__", init):
            with pytest.raises(RuntimeError):
                _run(raise_exc=RuntimeError("boom"))
        sm = main_ref[0].session_manager
        sm.add_assistant_final.assert_not_called()
        sm.continue_assistant.assert_not_called()

    def test_refused_when_the_turn_is_not_the_last_prose_row(self):
        _, out, main, provider = _run([{"type": "done", "response": None}], prefill=None)
        provider.chat_completion_stream.assert_not_called()
        notices = [e for e in out if isinstance(e, dict) and e.get("type") == "notice"]
        assert notices and "last reply" in notices[0]["message"]
        fin = _final(out)
        assert fin["error"] is True and fin["text"] == ""
        sm = main.session_manager
        sm.continue_assistant.assert_not_called()
        sm.add_assistant_final.assert_not_called()

    def test_tools_during_a_continuation_append_after_the_untouched_row(self):
        """A text-based tool call on the first iteration, prose on the second:
        the prefill row is never edited, the tool row carries only THIS
        stream's prose, and the final lands as a new row without the prefix."""
        first = iter([{"type": "content", "text": "TOOL"}, {"type": "done", "response": None}])
        second = iter([{"type": "content", "text": "After the tool."}, {"type": "done", "response": None}])
        with patch('core.chat.chat_streaming.get_generation_params', return_value={}):
            mock_main = MagicMock()
            mock_main.system = None
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
            sm.continue_target.return_value = PREFILL
            mock_main._build_base_messages.return_value = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": PREFILL},
            ]
            provider = MagicMock()
            provider.provider_name = "test"
            provider.model = "test-model"
            provider.chat_completion_stream.side_effect = [first, second]
            mock_main._select_provider.return_value = ("test", provider, "")
            te = mock_main.tool_engine
            te.extract_function_call_from_text.side_effect = [
                {"function_call": {"name": "t", "arguments": {}}}, None, None, None]
            te.execute_text_based_tool_call.return_value = (None, [])
            sc = StreamingChat(mock_main)
            with patch.object(config, 'FORCE_THINKING', False, create=True), \
                 patch.object(config, 'TTS_ENABLED', False, create=True), \
                 patch.object(config, 'TTS_STREAMING_ENABLED', False, create=True), \
                 patch('core.voice_privacy.tts_gate_reason', return_value=""), \
                 patch('core.chat.chat_streaming.publish', lambda *a, **k: None):
                out = list(sc.chat_stream("", continue_from="T1"))
        sm.continue_assistant.assert_not_called()
        # tool row content = this stream's prose only (the prefill row stands)
        assert te.execute_text_based_tool_call.call_args.args[1] == "TOOL"
        sm.add_assistant_final.assert_called_once()
        assert sm.add_assistant_final.call_args.kwargs["content"] == "After the tool."
        assert _final(out)["text"] == "After the tool."


# ─── history: continue_target / continue_assistant on a real store ──────────

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def mgr_factory(tmp_path):
    with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager

        def make():
            return ChatSessionManager(history_dir=str(tmp_path))
        yield make


def _tool_turn(mgr, final=PREFILL):
    mgr.add_user_message("look at this")
    mgr.add_assistant_with_tool_calls(
        content="", tool_calls=[{"id": "c1", "type": "function",
                                 "function": {"name": "t", "arguments": "{}"}}],
        thinking=None, thinking_raw=None, metadata=None)
    mgr.add_tool_result("c1", "t", "ok")
    if final is not None:
        mgr.add_assistant_final(final, metadata={"tokens": {"content": 3}})
    return mgr.get_messages()


class TestHistory:
    def test_target_is_the_turns_last_prose_row_by_either_timestamp(self, mgr_factory):
        mgr = mgr_factory()
        rows = _tool_turn(mgr)
        first_ts = rows[1]["timestamp"]     # the tool-calls row (what a merged display turn may carry)
        last_ts = rows[-1]["timestamp"]
        assert mgr.continue_target(first_ts) == PREFILL
        assert mgr.continue_target(last_ts) == PREFILL

    def test_refuses_when_a_later_turn_exists(self, mgr_factory):
        mgr = mgr_factory()
        rows = _tool_turn(mgr)
        ts = rows[-1]["timestamp"]
        mgr.add_user_message("something later")
        assert mgr.continue_target(ts) is None

    def test_refuses_a_turn_that_ends_in_a_tool_result_or_tool_calls(self, mgr_factory):
        mgr = mgr_factory()
        rows = _tool_turn(mgr, final=None)          # ends on the tool result
        assert mgr.continue_target(rows[1]["timestamp"]) is None
        mgr2 = mgr_factory()
        mgr2.add_user_message("q")
        mgr2.add_assistant_with_tool_calls(
            content="thinking aloud", tool_calls=[{"id": "c9", "type": "function",
                                                   "function": {"name": "t", "arguments": "{}"}}],
            thinking=None, thinking_raw=None, metadata=None)
        ts = mgr2.get_messages()[-1]["timestamp"]
        assert mgr2.continue_target(ts) is None     # last row carries tool_calls

    def test_unknown_timestamp_refuses(self, mgr_factory):
        mgr = mgr_factory()
        _tool_turn(mgr)
        assert mgr.continue_target("nope") is None
        assert mgr.continue_assistant("nope", "x") is False

    def test_continue_assistant_edits_in_place_and_persists(self, mgr_factory):
        mgr = mgr_factory()
        rows = _tool_turn(mgr)
        ts = rows[-1]["timestamp"]
        n = len(rows)
        assert mgr.continue_assistant(ts, PREFILL + "ence done.", thinking="t", metadata={"x": 1})
        rows2 = mgr.get_messages()
        assert len(rows2) == n                              # no new row
        assert rows2[-1]["content"] == "Half a sentence done."
        assert rows2[-1]["thinking"] == "t" and rows2[-1]["metadata"] == {"x": 1}
        assert rows2[-1]["timestamp"] == ts                 # same row
        assert rows2[1]["tool_calls"] and rows2[2]["role"] == "tool"   # tool half intact
        # Reload from disk: the in-place edit hit the store.
        again = mgr_factory()
        assert again.get_messages()[-1]["content"] == "Half a sentence done."
        assert len(again.get_messages()) == n

    def test_continue_assistant_keeps_old_thinking_and_metadata_when_none_given(self, mgr_factory):
        mgr = mgr_factory()
        rows = _tool_turn(mgr)
        ts = rows[-1]["timestamp"]
        assert mgr.continue_assistant(ts, PREFILL + "ence.")
        row = mgr.get_messages()[-1]
        assert row["metadata"] == {"tokens": {"content": 3}}


# ─── wiring tripwires: route + client ───────────────────────────────────────

def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestWiring:
    def test_route_passes_continue_from_to_the_engine(self):
        src = _src("core/routes/chat.py")
        assert "data.get('continue_from')" in src
        assert "continue_from=continue_from" in src

    def test_client_no_longer_pops_the_row_or_resends_the_user_text(self):
        chat_js = _src("interfaces/web/static/chat.js")
        api_js = _src("interfaces/web/static/api.js")
        assert "removeLastAssistant" not in chat_js
        assert "removeLastAssistant" not in api_js
        assert "skip_user_message" not in api_js
        assert "continue_from: timestamp" in api_js
        assert "ui.continueStreaming(msgEl" in chat_js
        assert "idx !== hist.length - 1" in chat_js          # last reply only

    def test_client_adopts_the_bubble_and_gates_the_button(self):
        ui_js = _src("interfaces/web/static/ui.js")
        stream_js = _src("interfaces/web/static/ui-streaming.js")
        assert "export const continueStreaming" in ui_js
        assert "act === 'continue' && !(role === 'assistant' && idx === total - 1)" in ui_js
        assert "state.paraBuf = adopt.seed" in stream_js
        assert "if (!adopt) container.appendChild(messageElement)" in stream_js
