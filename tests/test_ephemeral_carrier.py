"""[REGRESSION_GUARD] Ephemeral carrier + null-room limbo — 2026-09-03.

Absence-of-a-chat used to be unrepresentable: a background continuity task
or chatless agent installed no stream-brain override, so every "this chat"
seam (_effective_chat_name, update_chat_settings, clear, saves, metrics)
fell through to the OPERATOR'S live chat — a heartbeat task's reset_chat
could wipe the user's open conversation mid-stream. The carrier
(session_manager.make_ephemeral_override → {"chat": None, "ephemeral":
True, ...}) makes chatlessness first-class: writers refuse loudly, the
name resolves '', readers see the task's declared settings.

Null room: mode:limbo ('backrooms') is the vault-eviction terminal landing
— it only ever hosts the pointer when EVERY chat is private and sealed, so
it is a parking space, not a conversation. chat_streaming refuses turns
there; eviction stamps toolset 'none' and wipes clear_chat-grade
(tool_images + plugin_chat_data), and the .active_chat marker blanks.

Plan: tmp/reset-chat-ephemeral-plan.md.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.chat import stream_brain

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def sm(tmp_path):
    """Hermetic ChatSessionManager over a tmp DB (same shape as the
    vaulted-chats fixtures): 'pub' public with a message, 'other' public."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.append_messages_to_chat("pub", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        m.create_chat("other")
        yield m


@pytest.fixture(autouse=True)
def _clean_override():
    """Every test starts and ends with no stream-brain override — a leaked
    ContextVar from a failed test must not bleed forward."""
    tok = stream_brain.set_override(None)
    yield
    stream_brain.reset_override(tok)


def _ephemeral(sm, **kw):
    tok = stream_brain.set_override(sm.make_ephemeral_override(**kw))
    return tok


# ─── the carrier itself ─────────────────────────────────────────────────────

class TestCarrierShape:
    def test_factory_shape(self, sm):
        ov = sm.make_ephemeral_override()
        assert ov["chat"] is None
        assert ov["ephemeral"] is True
        assert ov["history"] is not None
        assert ov["settings"] == {"private_chat": False}

    def test_privacy_from_flag_and_task_settings(self, sm):
        assert sm.make_ephemeral_override(privacy_required=True)[
            "settings"]["private_chat"] is True
        assert sm.make_ephemeral_override(
            {"privacy_required": True})["settings"]["private_chat"] is True
        assert sm.make_ephemeral_override(
            {"provider": "x"})["settings"]["private_chat"] is False

    def test_task_settings_reach_readers(self, sm):
        """Hook privacy resolvers / meta tools read get_chat_settings() —
        under the carrier they must see the TASK's world, not the
        operator's."""
        _ephemeral(sm, task_settings={"toolset": "none", "provider": "p"})
        got = sm.get_chat_settings()
        assert got.get("toolset") == "none"
        assert got.get("provider") == "p"

    def test_is_ephemeral_helper(self, sm):
        assert not stream_brain.is_ephemeral()
        _ephemeral(sm)
        assert stream_brain.is_ephemeral()


class TestNameResolution:
    def test_effective_name_blank_not_operator(self, sm):
        sm.set_active_chat("pub")
        _ephemeral(sm)
        assert sm._effective_chat_name() == ""

    def test_named_override_still_resolves(self, sm):
        tok = stream_brain.set_override(sm.make_agent_override("other"))
        try:
            assert sm._effective_chat_name() == "other"
        finally:
            stream_brain.reset_override(tok)


class TestWriterRefusals:
    def test_update_chat_settings_refuses(self, sm):
        sm.set_active_chat("pub")
        before = sm.get_settings_for("pub")
        _ephemeral(sm)
        assert sm.update_chat_settings({"toolset": "all"}) is False
        assert sm.get_settings_for("pub") == before

    def test_clear_refuses(self, sm):
        sm.set_active_chat("pub")
        _ephemeral(sm)
        sm.clear()
        stream_brain.set_override(None)
        assert len(sm.read_chat_messages("pub")) == 2

    def test_save_current_chat_drops(self, sm):
        sm.set_active_chat("pub")
        _ephemeral(sm)
        assert sm._save_current_chat() is False


class TestMetricsLabel:
    def test_ephemeral_masks_background(self, sm):
        sm.set_active_chat("pub")
        _ephemeral(sm)
        assert sm.metrics_chat_label() == "__background__"

    def test_private_task_masks_private(self, sm):
        _ephemeral(sm, privacy_required=True)
        assert sm.metrics_chat_label() == "__private__"

    def test_limbo_masks(self, sm):
        sm.set_named_chat_settings("other", {"mode": "limbo"})
        sm.set_active_chat("other")
        assert sm.metrics_chat_label() == "__limbo__"


class TestClearChatByName:
    def test_active_by_name_under_ephemeral_clears_for_real(self, sm):
        """Named intent beats the ephemeral guard: clear_chat('pub') from a
        background thread wipes pub even while pub is active (the override
        is suspended for the call)."""
        sm.set_active_chat("pub")
        _ephemeral(sm)
        assert sm.clear_chat("pub") is True
        stream_brain.set_override(None)
        assert sm.read_chat_messages("pub") == []

    def test_active_by_name_under_named_override_clears_named(self, sm):
        """Pre-existing crossing: a daemon pinned to 'other' calling
        clear_chat('pub') (pub active) used to wipe its own override
        history and leave pub intact while returning True."""
        sm.set_active_chat("pub")
        ov = sm.make_agent_override("other")
        ov["history"].messages = [{"role": "user", "content": "daemon turn"}]
        tok = stream_brain.set_override(ov)
        try:
            assert sm.clear_chat("pub") is True
            assert ov["history"].messages, "override history must survive"
        finally:
            stream_brain.reset_override(tok)
        assert sm.read_chat_messages("pub") == []


# ─── reset_chat tool ────────────────────────────────────────────────────────

def _meta_system(sm):
    sys_mock = MagicMock()
    sys_mock.llm_chat.session_manager = sm
    return sys_mock


class TestResetChatTool:
    def _run(self, sm, args, live_calls=None):
        from functions import meta
        sys_mock = _meta_system(sm)
        sys_mock._conversation_manager.external_chats.return_value = \
            set(live_calls or ())
        with patch.object(meta, "_system", return_value=sys_mock):
            return meta._reset_chat(args)

    def test_no_arg_ephemeral_refuses(self, sm):
        sm.set_active_chat("pub")
        _ephemeral(sm)
        msg, ok = self._run(sm, {"reason": "housekeeping"})
        assert ok is False and "not in a chat" in msg
        stream_brain.set_override(None)
        assert len(sm.read_chat_messages("pub")) == 2

    def test_no_arg_operator_clears_and_drops_transients(self, sm):
        sm.set_active_chat("pub")
        with patch("core.prompts.clear_transients") as ct:
            msg, ok = self._run(sm, {"reason": "fresh start"})
        assert ok is True
        assert sm.read_chat_messages("pub") == []
        ct.assert_called_once()

    def test_no_arg_named_override_keeps_operator_transients(self, sm):
        """A daemon resetting its own chat must not kill the operator's
        process-global TTL mood pieces (pre-existing wart, closed)."""
        sm.set_active_chat("pub")
        tok = stream_brain.set_override(sm.make_agent_override("other"))
        try:
            with patch("core.prompts.clear_transients") as ct:
                msg, ok = self._run(sm, {"reason": "daemon tidy"})
            assert ok is True
            ct.assert_not_called()
        finally:
            stream_brain.reset_override(tok)

    def test_named_target_clears_by_name(self, sm):
        sm.set_active_chat("other")
        msg, ok = self._run(sm, {"reason": "cleanup", "chat_name": "pub"})
        assert ok is True and "'pub'" in msg
        assert sm.read_chat_messages("pub") == []

    def test_named_target_missing_refuses(self, sm):
        msg, ok = self._run(sm, {"reason": "x", "chat_name": "ghost"})
        assert ok is False

    def test_named_target_live_call_refuses(self, sm):
        msg, ok = self._run(sm, {"reason": "x", "chat_name": "pub"},
                            live_calls={"pub"})
        assert ok is False and "phone call" in msg
        assert len(sm.read_chat_messages("pub")) == 2


# ─── switch_toolset / activate_prompt ephemeral flips ───────────────────────

class TestIdiomFlips:
    def test_switch_toolset_ephemeral_refuses(self, sm):
        from functions import meta
        sys_mock = _meta_system(sm)
        sys_mock.llm_chat.function_manager.get_available_toolsets.return_value = ["all"]
        _ephemeral(sm)
        from core.toolsets import toolset_manager as _tsm
        with patch.object(meta, "_system", return_value=sys_mock), \
             patch.object(_tsm, "get_toolset_names", return_value=[]):
            msg, ok = meta._switch_toolset({"name": "all"})
        assert ok is False and "not in a chat" in msg
        sys_mock.llm_chat.function_manager.update_enabled_functions.assert_not_called()

    def test_activate_prompt_ephemeral_refuses(self, sm):
        from core import prompt_crud
        _ephemeral(sm)
        sys_mock = _meta_system(sm)
        with patch.object(prompt_crud, "get_prompt",
                          return_value={"content": "You are X"}):
            ok, msg = prompt_crud.activate_prompt("x", sys_mock)
        assert ok is False and "not in a chat" in msg
        sys_mock.llm_chat.set_system_prompt.assert_not_called()


# ─── null-room limbo ────────────────────────────────────────────────────────

def _seal(monkeypatch, sealed=True):
    import core.chat.history as hist
    monkeypatch.setattr(hist, "_vault_sealed", lambda: sealed)


class TestNullRoom:
    def _land_in_backrooms(self, sm, monkeypatch):
        import core.chat.history as hist
        monkeypatch.setattr(hist, "publish",
                            lambda et, data=None: None)
        for name in ("default", "pub", "other"):
            sm.set_named_chat_settings(name, {"private_chat": True})
        sm.create_chat("priv")
        sm.set_named_chat_settings("priv", {"private_chat": True})
        sm.set_active_chat("priv")
        _seal(monkeypatch, True)
        return sm.evict_private_active()

    def test_terminal_landing_gets_quarantine_stamp(self, sm, monkeypatch):
        assert self._land_in_backrooms(sm, monkeypatch) == "backrooms"
        s = sm.get_settings_for("backrooms") or {}
        assert s.get("mode") == "limbo"
        assert s.get("toolset") == "none"

    def test_landing_wipes_plugin_rows_and_tool_images(self, sm, monkeypatch):
        sm.create_chat("backrooms")
        sm.set_named_chat_settings("backrooms", {"mode": "limbo"})
        with sm._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO plugin_chat_data "
                "(plugin, chat_name, key, value, seq, updated_at) "
                "VALUES ('game-room', 'backrooms', 'save', 'x', 1, 'now')")
            conn.execute(
                "INSERT INTO tool_images (id, chat_name, data, media_type, created_at) "
                "VALUES ('img1.jpg', 'backrooms', X'00', 'image/jpeg', 'now')")
            conn.commit()
        assert self._land_in_backrooms(sm, monkeypatch) == "backrooms"
        with sm._get_connection() as conn:
            n_plug = conn.execute(
                "SELECT COUNT(*) FROM plugin_chat_data WHERE chat_name='backrooms'"
            ).fetchone()[0]
            n_img = conn.execute(
                "SELECT COUNT(*) FROM tool_images WHERE chat_name='backrooms'"
            ).fetchone()[0]
        assert n_plug == 0 and n_img == 0

    def test_marker_blanks_for_limbo(self, sm, monkeypatch, tmp_path):
        sm.create_chat("backrooms")
        sm.set_named_chat_settings("backrooms", {"mode": "limbo"})
        sm.set_active_chat("backrooms")
        marker = tmp_path / ".active_chat"
        assert marker.read_text(encoding="utf-8").strip() == ""

    def test_streaming_refuses_limbo_turn(self, sm):
        """The engine-level wall: any door's turn in a mode:limbo chat
        yields a refusal + final(error) and never reaches the LLM."""
        from core.chat.chat_streaming import StreamingChat
        sm.create_chat("backrooms")
        sm.set_named_chat_settings("backrooms", {"mode": "limbo"})
        sm.set_active_chat("backrooms")
        main = MagicMock()
        main.session_manager = sm
        chat = StreamingChat(main)
        chat.target_chat = None
        with patch("core.chat.chat_streaming.StreamingTTSPump") as pump:
            pump.return_value = MagicMock()
            events = []
            for ev in chat.chat_stream("hello?"):
                events.append(ev)
                if isinstance(ev, dict) and ev.get("type") == "final":
                    break
        finals = [e for e in events
                  if isinstance(e, dict) and e.get("type") == "final"]
        assert finals and finals[0]["error"] is True
        assert "holding room" in finals[0]["text"]
        main.refresh_spice_if_needed.assert_not_called()
