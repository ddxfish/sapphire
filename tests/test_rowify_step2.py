"""Rowify build-step 2 — new chats born as rows + the watermark writer.

Covers plan tests T5 (fresh rows chat round-trips, format='rows') and T6
(continuity append to the ACTIVE chat: watermark bump, in-memory sync, no
duplicate on next save), plus the step-2 write machinery: incremental
INSERT beyond the watermark, full window resync after mutations,
thinking_raw stripped from rows even MID tool cycle, clear() as a total
wipe, and the blob write path still exercised for legacy chats.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

TEST_DEFAULTS = {"prompt": "default"}
VERIFIER = Path(__file__).resolve().parent.parent / "tools" / "verify_chat_storage.py"


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
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


def db_rows(tmp_path, chat):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT seq, role, message_json FROM chat_messages "
        "WHERE chat_name = ? ORDER BY seq", (chat,)).fetchall()
    conn.close()
    return rows


def db_chat(tmp_path, chat):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM chats WHERE name = ?", (chat,)).fetchone()
    conn.close()
    return row


class TestT5BornRows:
    def test_new_chat_is_rows_format_and_round_trips(self, chat_env, tmp_path):
        mgr = chat_env()
        assert db_chat(tmp_path, "default")["storage_format"] == "rows"

        mgr.add_user_message("first")
        mgr.add_assistant_final("second", thinking="thought", metadata={"p": 1})
        mgr.add_user_message("third")
        snapshot = mgr.get_messages()

        rows = db_rows(tmp_path, "default")
        assert [r["seq"] for r in rows] == [0, 1, 2]
        assert [json.loads(r["message_json"]) for r in rows] == snapshot
        assert [r["role"] for r in rows] == ["user", "assistant", "user"]
        # Blob stays empty forever on a born-rows chat.
        assert db_chat(tmp_path, "default")["messages"] == "[]"

        # T5 gate: fresh instance reads back exactly.
        assert chat_env().get_messages() == snapshot

    def test_incremental_save_no_duplicates(self, chat_env, tmp_path):
        mgr = chat_env()
        for i in range(3):
            mgr.add_user_message(f"u{i}")
            mgr.add_assistant_final(f"a{i}")
        rows = db_rows(tmp_path, "default")
        assert [r["seq"] for r in rows] == list(range(6))
        assert len(mgr.get_messages()) == 6

    def test_thinking_raw_never_reaches_rows_even_mid_cycle(self, chat_env, tmp_path):
        """Stronger than golden T2: the strip happens at WRITE time, so even
        the mid-cycle save (before clear_thinking_raw runs) is clean."""
        mgr = chat_env()
        mgr.add_user_message("tool time")
        mgr.add_assistant_with_tool_calls(
            "", [{"id": "c1", "type": "function",
                  "function": {"name": "t", "arguments": "{}"}}],
            thinking="visible",
            thinking_raw=[{"type": "thinking", "thinking": "raw", "signature": "s"}],
        )
        # Mid-cycle: in-memory HAS thinking_raw, rows must NOT.
        assert any("thinking_raw" in m for m in mgr.current_chat.messages)
        for r in db_rows(tmp_path, "default"):
            assert "thinking_raw" not in json.loads(r["message_json"])


class TestMutationResync:
    def test_remove_last_messages_shrinks_rows(self, chat_env, tmp_path):
        mgr = chat_env()
        for i in range(3):
            mgr.add_user_message(f"u{i}")
            mgr.add_assistant_final(f"a{i}")
        assert mgr.remove_last_messages(2)

        rows = db_rows(tmp_path, "default")
        assert [r["seq"] for r in rows] == [0, 1, 2, 3]
        assert json.loads(rows[-1]["message_json"])["content"] == "a1"
        # And appends after a resync continue cleanly.
        mgr.add_user_message("after")
        assert [r["seq"] for r in db_rows(tmp_path, "default")] == [0, 1, 2, 3, 4]

    def test_edit_by_timestamp_updates_row(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("original")
        ts = mgr.get_messages()[0]["timestamp"]
        assert mgr.edit_message_by_timestamp("user", ts, "edited")

        rows = db_rows(tmp_path, "default")
        assert json.loads(rows[0]["message_json"])["content"] == "edited"
        assert chat_env().get_messages()[0]["content"] == "edited"

    def test_remove_tool_call_resyncs(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("q")
        mgr.add_assistant_with_tool_calls(
            "", [{"id": "cx", "type": "function",
                  "function": {"name": "t", "arguments": "{}"}}])
        mgr.add_tool_result("cx", "t", "res")
        mgr.add_assistant_final("done")
        assert mgr.remove_tool_call("cx")

        reloaded = chat_env().get_messages()
        assert reloaded == mgr.get_messages()
        assert not any(m.get("tool_call_id") == "cx" for m in reloaded)
        rows = db_rows(tmp_path, "default")
        assert [r["seq"] for r in rows] == list(range(len(reloaded)))

    def test_clear_wipes_all_rows(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("gone soon")
        mgr.add_assistant_final("yep")
        mgr.clear()
        assert db_rows(tmp_path, "default") == []
        assert chat_env().get_messages() == []

    def test_import_raw_assignment_resyncs_rows(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("old")
        new = [{"role": "user", "content": "imported", "timestamp": "t0"}]
        mgr.current_chat.messages = list(new)
        mgr._save_current_chat()
        rows = db_rows(tmp_path, "default")
        assert len(rows) == 1
        assert json.loads(rows[0]["message_json"]) == new[0]


class TestT6ContinuityAppend:
    def test_append_to_active_rows_chat_no_dup_on_next_save(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("foreground")
        mgr.add_assistant_final("reply")

        assert mgr.append_messages_to_chat("default", [
            {"role": "user", "content": "cron says"},
            {"role": "assistant", "content": "heartbeat reply"},
        ])
        # In-memory synced...
        assert len(mgr.get_messages()) == 4
        assert [r["seq"] for r in db_rows(tmp_path, "default")] == [0, 1, 2, 3]

        # ...and the NEXT foreground save must not re-INSERT them (T6).
        mgr.add_user_message("after heartbeat")
        rows = db_rows(tmp_path, "default")
        assert [r["seq"] for r in rows] == [0, 1, 2, 3, 4]
        assert chat_env().get_messages() == mgr.get_messages()

    def test_append_to_non_active_rows_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("target")
        assert mgr.append_messages_to_chat("target", [
            {"role": "user", "content": "cron 1"},
            {"role": "assistant", "content": "cron 2"},
        ])
        assert mgr.append_messages_to_chat("target", [
            {"role": "user", "content": "cron 3"},
        ])
        rows = db_rows(tmp_path, "target")
        assert [r["seq"] for r in rows] == [0, 1, 2]
        assert mgr.active_chat_name == "default"  # never switched

        mgr.set_active_chat("target")
        assert [m["content"] for m in mgr.get_messages()] == ["cron 1", "cron 2", "cron 3"]


class TestBlobPathStillLives:
    """Legacy (pre-rowify) chats keep the blob WRITE path until step 3's
    convert-on-write — guard the dual-writer against cold-rot."""

    def _make_legacy(self, tmp_path):
        db = str(tmp_path / "sapphire_history.db")
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE chats (
            name TEXT PRIMARY KEY, settings TEXT NOT NULL,
            messages TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute(
            "INSERT INTO chats VALUES ('legacy', '{}', ?, 't')",
            (json.dumps([{"role": "user", "content": "old", "timestamp": "t"}]),))
        conn.commit()
        conn.close()

    def test_legacy_chat_writes_stay_blob(self, chat_env, tmp_path):
        self._make_legacy(tmp_path)
        mgr = chat_env()
        assert mgr.set_active_chat("legacy")
        mgr.add_user_message("new in blob")

        chat = db_chat(tmp_path, "legacy")
        assert chat["storage_format"] == "blob"  # step 2 does NOT convert
        blob = json.loads(chat["messages"])
        assert [m["content"] for m in blob] == ["old", "new in blob"]
        assert db_rows(tmp_path, "legacy") == []

    def test_latched_chat_append_stays_blob(self, chat_env, tmp_path):
        """Step 3 made plain blob chats CONVERT on append (see
        test_rowify_step3.py). The blob-append path's permanent population
        is conversion_failed-latched chats — pin the anti-cold-rot coverage
        there."""
        self._make_legacy(tmp_path)
        mgr = chat_env()
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute("UPDATE chats SET conversion_failed = 1 WHERE name = 'legacy'")
        conn.commit()
        conn.close()

        assert mgr.append_messages_to_chat("legacy", [
            {"role": "user", "content": "cron to blob"}])
        chat = db_chat(tmp_path, "legacy")
        assert chat["storage_format"] == "blob"
        assert json.loads(chat["messages"])[-1]["content"] == "cron to blob"


class TestVerifierIntegration:
    def test_verifier_passes_on_busy_rows_db(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.create_chat("second")
        mgr.add_user_message("hello")
        mgr.add_assistant_with_tool_calls(
            "", [{"id": "cv", "type": "function",
                  "function": {"name": "t", "arguments": "{}"}}],
            thinking_raw=[{"type": "thinking", "thinking": "x"}])
        mgr.add_tool_result("cv", "t", "img <<IMG::tool:vimg>>")
        mgr.add_assistant_final("done")
        mgr.save_tool_image("vimg", b"V", "image/png")
        mgr.remove_last_messages(1)  # force one resync through the machinery
        mgr.add_assistant_final("done again")

        proc = subprocess.run(
            [sys.executable, str(VERIFIER), str(tmp_path / "sapphire_history.db")],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout
