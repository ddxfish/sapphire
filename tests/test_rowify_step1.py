"""Rowify build-step 1 — schema migration + dual-read layer.

Step 1 of tmp/chat-storage-rowify-plan.md: chat_messages table, format
columns on chats (guarded ALTERs for pre-rowify DBs), format-aware read
layer, created_at rider (tmp/chat-manager.md). All chats remain 'blob' —
these tests verify the plumbing is real while behavior is unchanged.

The rows READ path is exercised here by hand-inserting a rows-format chat
(step 2 adds the writer); the golden masters in
test_rowify_golden_master.py keep pinning blob behavior.
"""
import json
import sqlite3
import pytest
from unittest.mock import patch


TEST_DEFAULTS = {"prompt": "default"}


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    """Factory for ChatSessionManager instances on a shared temp DB."""
    import core.privacy as privacy
    monkeypatch.setattr(privacy, "is_privacy_mode", lambda: False, raising=False)

    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager

        def make():
            return ChatSessionManager(history_dir=str(tmp_path))

        yield make


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


class TestSchemaMigration:
    def test_fresh_db_has_rowify_schema(self, chat_env, tmp_path):
        chat_env()
        db = str(tmp_path / "sapphire_history.db")

        cols = _columns(db, "chats")
        assert {"storage_format", "conversion_failed", "created_at"} <= cols
        assert _columns(db, "chat_messages") == {"chat_name", "seq", "role", "message_json"}

    def test_pre_rowify_db_gets_guarded_alters(self, chat_env, tmp_path):
        """A database created with the OLD schema gains the new columns on
        first manager init, and existing chat rows default to blob format."""
        db = str(tmp_path / "sapphire_history.db")
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE chats (
            name TEXT PRIMARY KEY, settings TEXT NOT NULL,
            messages TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute(
            "INSERT INTO chats VALUES (?, ?, ?, ?)",
            ("legacy_chat", json.dumps({"prompt": "default"}),
             json.dumps([{"role": "user", "content": "old", "timestamp": "t"}]),
             "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        mgr = chat_env()  # init runs the guarded ALTERs

        cols = _columns(db, "chats")
        assert {"storage_format", "conversion_failed", "created_at"} <= cols

        conn = sqlite3.connect(db)
        fmt, failed, created = conn.execute(
            "SELECT storage_format, conversion_failed, created_at "
            "FROM chats WHERE name='legacy_chat'").fetchone()
        conn.close()
        assert fmt == "blob"
        assert failed == 0
        assert created is None  # predates the column — UI shows "—"

        # And the legacy chat still reads fine through the manager.
        assert mgr.set_active_chat("legacy_chat")
        assert mgr.get_messages() == [{"role": "user", "content": "old", "timestamp": "t"}]

    def test_reinit_is_idempotent(self, chat_env):
        chat_env()
        chat_env()
        mgr = chat_env()
        assert mgr.active_chat_name == "default"


class TestCreatedAtRider:
    def test_new_chat_is_stamped_and_listed(self, chat_env):
        mgr = chat_env()
        assert mgr.create_chat("stamped")

        by_name = {c["name"]: c for c in mgr.list_chat_files()}
        assert by_name["stamped"]["created"]  # ISO string, truthy
        assert "created" in by_name["default"]  # field present on every row


def _insert_rows_chat(db_path, name, messages):
    """Simulate what step 2's writer will produce: a rows-format chat."""
    conn = sqlite3.connect(db_path)
    now = "2026-07-09T00:00:00"
    conn.execute(
        "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
        "VALUES (?, ?, '[]', ?, 'rows')",
        (name, json.dumps({"prompt": "default"}), now),
    )
    for seq, msg in enumerate(messages):
        conn.execute(
            "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
            "VALUES (?, ?, ?, ?)",
            (name, seq, msg.get("role"), json.dumps(msg)),
        )
    conn.commit()
    conn.close()


class TestRowsReadPath:
    MSGS = [
        {"role": "user", "content": "row msg 1", "timestamp": "t1"},
        {"role": "assistant", "content": "row msg 2", "timestamp": "t2"},
        {"role": "user", "content": "row msg 3", "timestamp": "t3"},
    ]

    def test_load_chat_reads_rows_in_seq_order(self, chat_env, tmp_path):
        mgr = chat_env()
        _insert_rows_chat(str(tmp_path / "sapphire_history.db"), "rowschat", self.MSGS)

        assert mgr.set_active_chat("rowschat")
        assert mgr.get_messages() == self.MSGS  # exact, oldest-first

    def test_read_chat_messages_branches_on_format(self, chat_env, tmp_path, monkeypatch):
        mgr = chat_env()
        _insert_rows_chat(str(tmp_path / "sapphire_history.db"), "rowschat", self.MSGS)

        # Disable both trims so the LLM-shaped read returns everything.
        import core.chat.history as hist
        monkeypatch.setattr(hist.config, "LLM_MAX_HISTORY", 0, raising=False)
        monkeypatch.setattr(hist.config, "CONTEXT_LIMIT", 0, raising=False)

        llm_msgs = mgr.read_chat_messages("rowschat")
        assert [m["content"] for m in llm_msgs] == ["row msg 1", "row msg 2", "row msg 3"]

    def test_list_chat_files_counts_rows_chats(self, chat_env, tmp_path):
        mgr = chat_env()
        _insert_rows_chat(str(tmp_path / "sapphire_history.db"), "rowschat", self.MSGS)

        by_name = {c["name"]: c for c in mgr.list_chat_files()}
        assert by_name["rowschat"]["message_count"] == 3  # COUNT(*), not the '[]' blob

    def test_load_cap_takes_newest_rows(self, chat_env, tmp_path, monkeypatch):
        """The rows analogue of the 50MB blob guard: cap keeps the NEWEST N."""
        from core.chat.history import ChatSessionManager
        monkeypatch.setattr(ChatSessionManager, "_ROWS_LOAD_CAP", 2)

        mgr = chat_env()
        _insert_rows_chat(str(tmp_path / "sapphire_history.db"), "bigchat", self.MSGS)

        assert mgr.set_active_chat("bigchat")
        assert [m["content"] for m in mgr.get_messages()] == ["row msg 2", "row msg 3"]

    def test_delete_chat_sweeps_chat_messages(self, chat_env, tmp_path):
        mgr = chat_env()
        db = str(tmp_path / "sapphire_history.db")
        _insert_rows_chat(db, "doomed", self.MSGS)

        assert mgr.delete_chat("doomed")

        conn = sqlite3.connect(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE chat_name='doomed'").fetchone()[0]
        conn.close()
        assert count == 0
