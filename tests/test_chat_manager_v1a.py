"""Chat Manager v1a — by-name storage operations (tmp/chat-manager.md).

clear_chat / export_chat / rename_chat / list_chat_files(stats=True) on the
SessionManager, plus knowledge rename_scope. Route layer is a thin loop over
these; live-call and agent guards are exercised by the live system.
"""
import json
import sqlite3
from pathlib import Path

import pytest
from unittest.mock import patch

TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager

        def make():
            return ChatSessionManager(history_dir=str(tmp_path))

        yield make


def raw(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


class TestClearChatByName:
    def test_clears_non_active_rows_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("bulk_target")
        mgr.append_messages_to_chat("bulk_target", [
            {"role": "user", "content": "wipe me"}])
        mgr.save_tool_image("bt_img", b"X", "image/png", chat_name="bulk_target")

        assert mgr.clear_chat("bulk_target")
        assert mgr.active_chat_name == "default"  # never switched
        assert raw(tmp_path, "SELECT * FROM chat_messages WHERE chat_name='bulk_target'") == []
        assert raw(tmp_path, "SELECT * FROM tool_images WHERE chat_name='bulk_target'") == []
        assert mgr.export_chat("bulk_target")["messages"] == []

    def test_clears_active_chat_including_memory(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("in memory too")
        assert mgr.clear_chat("default")
        assert mgr.get_messages() == []  # in-memory reset via clear()

    def test_missing_chat_returns_false(self, chat_env):
        assert not chat_env().clear_chat("ghost")


class TestExportChat:
    def test_export_is_raw_and_untrimmed(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("q")
        mgr.add_assistant_with_tool_calls(
            "", [{"id": "ce", "type": "function",
                  "function": {"name": "t", "arguments": "{}"}}])
        mgr.add_tool_result("ce", "t", "res", inputs={"a": 1})
        mgr.add_assistant_final("done", thinking="thought")

        out = mgr.export_chat("default")
        assert out["messages"] == mgr.get_messages()  # timestamps, thinking, tool_inputs intact
        assert out["settings"]["prompt"] == "default"
        assert mgr.export_chat("ghost") is None

    def test_export_blob_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        msgs = [{"role": "user", "content": "old blob", "timestamp": "t"}]
        conn.execute(
            "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
            "VALUES ('legacy', '{}', ?, 't', 'blob')", (json.dumps(msgs),))
        conn.commit(); conn.close()
        assert mgr.export_chat("legacy")["messages"] == msgs


class TestRenameChat:
    def test_rename_moves_every_tentacle(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("old_name")
        mgr.append_messages_to_chat("old_name", [{"role": "user", "content": "hi"}])
        mgr.save_tool_image("rn_img", b"X", "image/png", chat_name="old_name")

        ok, result = mgr.rename_chat("old_name", "New Name")
        assert ok
        assert result == "new_name"  # sanitized like create_chat
        assert raw(tmp_path, "SELECT 1 FROM chats WHERE name='old_name'") == []
        assert len(raw(tmp_path, "SELECT 1 FROM chat_messages WHERE chat_name='new_name'")) == 1
        assert len(raw(tmp_path, "SELECT 1 FROM tool_images WHERE chat_name='new_name'")) == 1
        # Content survives the move intact.
        assert mgr.export_chat("new_name")["messages"][0]["content"] == "hi"

    def test_rename_active_chat_updates_pointer(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("workbench")
        mgr.set_active_chat("workbench")
        mgr.add_user_message("keep me")

        ok, result = mgr.rename_chat("workbench", "garage")
        assert ok
        assert mgr.active_chat_name == "garage"
        assert (tmp_path / ".active_chat").read_text().strip() == "garage"
        # Saves keep flowing to the new name (watermark state moved too).
        mgr.add_user_message("after rename")
        assert len(raw(tmp_path, "SELECT 1 FROM chat_messages WHERE chat_name='garage'")) == 2
        # Fresh instance restores the renamed chat as active.
        assert chat_env().active_chat_name == "garage"

    def test_rename_refusals(self, chat_env):
        mgr = chat_env()
        mgr.create_chat("a_chat")
        mgr.create_chat("b_chat")
        assert mgr.rename_chat("default", "anything")[0] is False   # default blocked
        assert mgr.rename_chat("a_chat", "b_chat")[0] is False      # collision
        assert mgr.rename_chat("ghost", "whatever")[0] is False     # missing
        assert mgr.rename_chat("a_chat", "!!!")[0] is False         # sanitizes to empty
        assert mgr.rename_chat("a_chat", "a_chat")[0] is False      # unchanged


class TestStats:
    def test_stats_adds_size_bytes(self, chat_env):
        mgr = chat_env()
        mgr.add_user_message("some content for sizing")
        mgr.save_tool_image("sz_img", b"0123456789", "image/png")

        plain = {c["name"]: c for c in mgr.list_chat_files()}
        assert "size_bytes" not in plain["default"]

        stat = {c["name"]: c for c in mgr.list_chat_files(stats=True)}
        assert stat["default"]["size_bytes"] > 10  # rows bytes + 10 image bytes
        assert "created" in stat["default"]


@pytest.fixture
def kt_tmp(tmp_path, monkeypatch):
    """knowledge_tools pointed at a THROWAWAY db. The path lives in the
    module-global `_db_path` cache (via _get_db_path) — patch THAT, not an
    invented constant: a raising=False patch of a wrong name silently runs
    the test against the REAL user knowledge.db (burned 2026-07-10)."""
    from plugins.memory.tools import knowledge_tools as kt
    monkeypatch.setattr(kt, "_db_path", tmp_path / "knowledge.db")
    # _ensure_db latches _db_initialized after the FIRST init — a second
    # test's fresh tmp DB would never get schema. Reset it alongside.
    monkeypatch.setattr(kt, "_db_initialized", False)
    # Sanity tripwire: the connection must resolve inside tmp_path.
    assert str(tmp_path) in str(kt._get_db_path())
    return kt


class TestKnowledgeRenameScope:
    def test_rename_scope_moves_tabs(self, kt_tmp):
        kt = kt_tmp
        kt.create_scope("__rag__:old_chat")
        assert kt.rename_scope("__rag__:old_chat", "__rag__:new_chat")
        names = [s["name"] for s in kt.get_scopes()]
        assert "__rag__:new_chat" in names
        assert "__rag__:old_chat" not in names

    def test_rename_missing_scope_is_noop_true(self, kt_tmp):
        kt = kt_tmp
        assert kt.rename_scope("__rag__:never_existed", "__rag__:x") is True
        assert kt.rename_scope("default", "nope") is False
