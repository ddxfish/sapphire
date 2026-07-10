"""Rowify build-step 3 — lazy blob→rows conversion + the migration safety net.

The two conversion triggers (end_streaming 1→0 for the active chat, the
continuity append path for the rest), the strict in-txn verify, the
conversion_failed latch discipline (shape errors latch, transient errors
retry), the privacy gate, the one-shot pre-conversion snapshot, and both
revert paths (manager method + standalone CLI).
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
REVERT_CLI = Path(__file__).resolve().parent.parent / "tools" / "revert_chat_storage.py"

BLOB_MSGS = [
    {"role": "user", "content": "legacy hello", "timestamp": "t1"},
    {"role": "assistant", "content": "legacy reply", "timestamp": "t2"},
]


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


def seed_blob_chat(tmp_path, name, messages=BLOB_MSGS):
    """A pre-rowify chat: blob content, storage_format='blob'."""
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.execute(
        "INSERT INTO chats (name, settings, messages, updated_at, storage_format) "
        "VALUES (?, '{}', ?, 't', 'blob')",
        (name, json.dumps(messages) if isinstance(messages, list) else messages))
    conn.commit()
    conn.close()


def chat_row(tmp_path, name):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM chats WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row


def msg_rows(tmp_path, name):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT seq, message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq",
        (name,)).fetchall()
    conn.close()
    return rows


def run_verifier(tmp_path):
    return subprocess.run(
        [sys.executable, str(VERIFIER), str(tmp_path / "sapphire_history.db")],
        capture_output=True, text=True)


class TestActiveChatConversion:
    def test_end_streaming_converts_active_blob_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        seed_blob_chat(tmp_path, "legacy")
        assert mgr.set_active_chat("legacy")

        # A full simulated turn: saves run mid-stream through the BLOB path,
        # conversion fires on the end_streaming 1→0 transition.
        mgr.begin_streaming()
        mgr.add_user_message("turn during stream")
        mgr.add_assistant_final("reply during stream")
        assert chat_row(tmp_path, "legacy")["storage_format"] == "blob"  # not yet
        mgr.end_streaming()

        row = chat_row(tmp_path, "legacy")
        assert row["storage_format"] == "rows"
        rows = msg_rows(tmp_path, "legacy")
        assert [json.loads(r["message_json"]) for r in rows] == mgr.get_messages()
        # Frozen blob = content at conversion point (recovery window, R5).
        frozen = json.loads(row["messages"])
        assert len(frozen) == 4
        # Post-conversion writes are incremental rows.
        mgr.add_user_message("after conversion")
        assert [r["seq"] for r in msg_rows(tmp_path, "legacy")] == [0, 1, 2, 3, 4]
        assert run_verifier(tmp_path).returncode == 0

    def test_double_convert_is_guarded(self, chat_env, tmp_path):
        mgr = chat_env()
        seed_blob_chat(tmp_path, "legacy")
        mgr.set_active_chat("legacy")
        mgr.begin_streaming(); mgr.end_streaming()   # converts
        n_before = len(msg_rows(tmp_path, "legacy"))
        mgr.begin_streaming(); mgr.end_streaming()   # re-probe must no-op
        assert len(msg_rows(tmp_path, "legacy")) == n_before
        assert chat_row(tmp_path, "legacy")["storage_format"] == "rows"


class TestAppendPathConversion:
    def test_append_converts_non_active_blob_chat(self, chat_env, tmp_path):
        mgr = chat_env()
        seed_blob_chat(tmp_path, "cron_target")
        assert mgr.append_messages_to_chat("cron_target", [
            {"role": "user", "content": "heartbeat in"},
            {"role": "assistant", "content": "heartbeat out"},
        ])
        row = chat_row(tmp_path, "cron_target")
        assert row["storage_format"] == "rows"
        contents = [json.loads(r["message_json"])["content"] for r in msg_rows(tmp_path, "cron_target")]
        assert contents == ["legacy hello", "legacy reply", "heartbeat in", "heartbeat out"]
        # Frozen blob holds the PRE-append snapshot (conversion point).
        assert len(json.loads(row["messages"])) == 2
        assert run_verifier(tmp_path).returncode == 0


class TestSnapshot:
    def test_snapshot_created_once(self, chat_env, tmp_path):
        mgr = chat_env()
        seed_blob_chat(tmp_path, "legacy_a")
        seed_blob_chat(tmp_path, "legacy_b")

        mgr.append_messages_to_chat("legacy_a", [{"role": "user", "content": "x"}])
        snaps = list(tmp_path.glob("pre_rowify_*.db"))
        assert len(snaps) == 1
        assert (tmp_path / ".pre_rowify_snapshot_done").exists()
        # Snapshot is a valid pre-conversion copy: legacy_a still blob inside it.
        sconn = sqlite3.connect(f"file:{snaps[0]}?mode=ro", uri=True)
        fmt = sconn.execute(
            "SELECT storage_format FROM chats WHERE name='legacy_a'").fetchone()[0]
        sconn.close()
        assert fmt == "blob"

        mgr.append_messages_to_chat("legacy_b", [{"role": "user", "content": "y"}])
        assert len(list(tmp_path.glob("pre_rowify_*.db"))) == 1  # latched


class TestBootPreWarm:
    def test_boot_snapshots_when_blob_chats_exist(self, chat_env, tmp_path):
        """Race scout + day-ruiner (same finding): the lazy snapshot runs
        VACUUM INTO under the global lock mid-traffic. Boot pre-warm takes
        it while nothing else runs — a fresh manager init on a DB with blob
        chats must create the snapshot BEFORE any write."""
        chat_env()                       # first init: rows-only DB
        seed_blob_chat(tmp_path, "old_timer")
        assert not list(tmp_path.glob("pre_rowify_*.db"))

        chat_env()                       # simulated boot with a blob chat present
        assert len(list(tmp_path.glob("pre_rowify_*.db"))) == 1
        assert (tmp_path / ".pre_rowify_snapshot_done").exists()

    def test_boot_skips_snapshot_on_rows_only_db(self, chat_env, tmp_path):
        chat_env()
        chat_env()
        assert not list(tmp_path.glob("pre_rowify_*.db"))  # nothing to protect


class TestFailureDiscipline:
    def test_shape_error_latches_and_falls_back(self, chat_env, tmp_path):
        mgr = chat_env()
        seed_blob_chat(tmp_path, "corrupt", messages='{"not": "a list"}')

        # Conversion attempt hits the shape error → latch; the append then
        # falls back to the blob path (which also fails on the dict — same
        # as pre-rowify behavior for a corrupt blob — but must not raise).
        mgr.append_messages_to_chat("corrupt", [{"role": "user", "content": "z"}])

        row = chat_row(tmp_path, "corrupt")
        assert row["storage_format"] == "blob"
        assert row["conversion_failed"] == 1
        assert msg_rows(tmp_path, "corrupt") == []  # rollback left no rows

        # Latch respected: no second attempt (would re-log/latch — format stable).
        mgr.append_messages_to_chat("corrupt", [{"role": "user", "content": "z2"}])
        assert chat_row(tmp_path, "corrupt")["conversion_failed"] == 1

    def test_transient_error_does_not_latch(self, chat_env, tmp_path, monkeypatch):
        from core.chat.history import ChatSessionManager
        mgr = chat_env()
        seed_blob_chat(tmp_path, "busy")

        def boom(msg):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(ChatSessionManager, "_row_json", staticmethod(boom))
        mgr.append_messages_to_chat("busy", [{"role": "user", "content": "later"}])
        monkeypatch.undo()

        row = chat_row(tmp_path, "busy")
        assert row["storage_format"] == "blob"
        assert row["conversion_failed"] == 0  # NOT latched — retries next write
        # Blob fallback still saved the message.
        assert json.loads(row["messages"])[-1]["content"] == "later"

        # Next write retries and succeeds.
        mgr.append_messages_to_chat("busy", [{"role": "user", "content": "retry"}])
        assert chat_row(tmp_path, "busy")["storage_format"] == "rows"

    def test_privacy_mode_blocks_conversion(self, chat_env, tmp_path, monkeypatch):
        import core.privacy as privacy
        mgr = chat_env()
        seed_blob_chat(tmp_path, "private_era")
        mgr.set_active_chat("private_era")

        monkeypatch.setattr(privacy, "is_privacy_mode", lambda: True, raising=False)
        mgr.begin_streaming(); mgr.end_streaming()
        assert chat_row(tmp_path, "private_era")["storage_format"] == "blob"

        monkeypatch.setattr(privacy, "is_privacy_mode", lambda: False, raising=False)
        mgr.begin_streaming(); mgr.end_streaming()
        assert chat_row(tmp_path, "private_era")["storage_format"] == "rows"


class TestRevert:
    def test_manager_revert_round_trip(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("born rows")
        mgr.add_assistant_final("indeed")
        before = mgr.get_messages()

        assert mgr.revert_chat_to_blob("default")
        row = chat_row(tmp_path, "default")
        assert row["storage_format"] == "blob"
        assert json.loads(row["messages"]) == before
        assert msg_rows(tmp_path, "default") == []
        assert run_verifier(tmp_path).returncode == 0

        # Fresh instance reads the blob identically...
        assert chat_env().get_messages() == before
        # ...and the next turn lazily RE-converts (full circle).
        mgr2 = chat_env()
        mgr2.begin_streaming()
        mgr2.add_user_message("round two")
        mgr2.end_streaming()
        assert chat_row(tmp_path, "default")["storage_format"] == "rows"
        assert run_verifier(tmp_path).returncode == 0

    def test_cli_revert(self, chat_env, tmp_path):
        mgr = chat_env()
        mgr.add_user_message("cli test")
        before = mgr.get_messages()
        db = str(tmp_path / "sapphire_history.db")

        proc = subprocess.run(
            [sys.executable, str(REVERT_CLI), "default", db],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout
        row = chat_row(tmp_path, "default")
        assert row["storage_format"] == "blob"
        assert json.loads(row["messages"]) == before
        assert run_verifier(tmp_path).returncode == 0

    def test_cli_refuses_blob_chat(self, chat_env, tmp_path):
        chat_env()
        seed_blob_chat(tmp_path, "still_blob")
        proc = subprocess.run(
            [sys.executable, str(REVERT_CLI), "still_blob",
             str(tmp_path / "sapphire_history.db")],
            capture_output=True, text=True)
        assert proc.returncode == 1
        assert "not 'rows'" in proc.stdout
