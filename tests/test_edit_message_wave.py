"""Edit button wave — 2026-09-10 (record: tmp/edit-button-wave.md).

Server half: an edit keeps the row's stored `thinking` when the new text
carries no <think> tags (the editor shows prose only now); tags still split
out and replace it; every edit publishes MESSAGE_ADDED stamped with the
editing tab's origin, so OTHER tabs repaint and the editing tab drops its
own echo (event-bus.js: origin === sessionId).

Client half (source tripwires): the editor keeps the bubble (hides only the
prose past the last tool accordion / gallery row), Save/Cancel swap ONE
message (never the transcript, so a live reply below survives), a refresh
held during the edit is replayed, Esc / Ctrl+Enter work.

Run with: pytest tests/test_edit_message_wave.py -v
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.event_bus import Events
from core.request_context import session_origin

ROOT = Path(__file__).resolve().parent.parent
TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def mgr(tmp_path):
    with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        yield ChatSessionManager(history_dir=str(tmp_path))


def _reply(mgr, content="old prose", thinking="deep thoughts"):
    mgr.add_user_message("q")
    mgr.add_assistant_final(content, thinking=thinking)
    return mgr.get_messages()[-1]["timestamp"]


class TestServer:
    def test_prose_edit_keeps_the_stored_thinking(self, mgr):
        ts = _reply(mgr)
        assert mgr.edit_message_by_timestamp("assistant", ts, "new prose")
        row = mgr.get_messages()[-1]
        assert row["content"] == "new prose"
        assert row["thinking"] == "deep thoughts"

    def test_tagged_edit_replaces_the_thinking(self, mgr):
        ts = _reply(mgr)
        assert mgr.edit_message_by_timestamp("assistant", ts, "<think>other</think>\n\nnew prose")
        row = mgr.get_messages()[-1]
        assert row["content"].strip() == "new prose"
        assert row["thinking"] == "other"

    def test_edit_publishes_with_the_tabs_origin(self, mgr):
        ts = _reply(mgr)
        user_ts = mgr.get_messages()[0]["timestamp"]
        token = session_origin.set("tab-1")
        try:
            with patch("core.chat.history.publish") as pub:
                assert mgr.edit_message_by_timestamp("assistant", ts, "edited")
                assert mgr.edit_message_by_timestamp("user", user_ts, "q2")
        finally:
            session_origin.reset(token)
        calls = [c for c in pub.call_args_list if c.args[0] == Events.MESSAGE_ADDED]
        assert [c.args[1]["role"] for c in calls] == ["assistant", "user"]
        for c in calls:
            assert c.args[1]["edited"] is True
            assert c.args[1]["origin"] == "tab-1"
            assert c.args[1]["chat_name"]

    def test_edit_persists_across_reload(self, mgr, tmp_path):
        ts = _reply(mgr)
        mgr.edit_message_by_timestamp("assistant", ts, "kept")
        with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(TEST_DEFAULTS)), \
             patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(TEST_DEFAULTS)):
            from core.chat.history import ChatSessionManager
            again = ChatSessionManager(history_dir=str(tmp_path))
        row = again.get_messages()[-1]
        assert row["content"] == "kept" and row["thinking"] == "deep thoughts"


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestClientWiring:
    def test_handler_swaps_one_message_and_replays_a_held_refresh(self):
        h = _src("interfaces/web/static/handlers/message-handlers.js")
        assert "ui.replaceMessage(msgEl, payload, idx, total)" in h
        assert "if (chat.takeHeldRefresh()) await refresh(false)" in h
        assert "text.replace(THINK_RE, '')" in h
        assert "exitEditMode" not in h

    def test_editor_keeps_the_tool_half_and_has_keys(self):
        ui = _src("interfaces/web/static/ui.js")
        assert "export const replaceMessage" in ui
        assert "details.accordion-tool, .gallery-row" in ui
        assert "e.key === 'Escape'" in ui
        assert "e.key === 'Enter' && (e.ctrlKey || e.metaKey)" in ui
        assert "content.dataset.original" not in ui          # no innerHTML save/restore
        assert "exitEditMode" not in ui

    def test_render_hold_is_remembered(self):
        c = _src("interfaces/web/static/chat.js")
        assert "_heldRefresh = true" in c
        assert "export const takeHeldRefresh" in c
