"""[REGRESSION_GUARD] F2 Wave 2 — read-only-degraded chat latch (2026-08-17).

Before the latch, ONE bad row bricked or hollowed a chat:
  - corrupt JSON in a message row → json.loads threw inside
    _read_rows_messages → _load_chat's blanket except → False → the whole
    chat unloadable ("bricked");
  - an undecryptable row (foreign-vault ciphertext, tamper) → silently
    skipped → the chat loaded HOLLOW with no trace.

The latch: the load succeeds with what's readable, the chat latches
read-only-degraded (in-memory, per session), and every write path refuses —
protecting the corrupt-but-recoverable rows from a destructive resync.
clear/delete stay allowed as explicit escape hatches. Vault unlock drops
decrypt-cause latches (they may have just healed) and re-evaluates.

Run with: pytest tests/test_degraded_chats.py -v
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

TEST_DEFAULTS = {"prompt": "default"}
_KEY = bytes(range(32))


@pytest.fixture
def sm(tmp_path):
    """Hermetic ChatSessionManager with chat 'pub' (rows format, 4 messages)."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.append_messages_to_chat("pub", [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer"},
        ])
        yield m


def raw(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def corrupt_row(tmp_path, chat="pub", seq=0, payload="{this is not json"):
    raw(tmp_path, "UPDATE chat_messages SET message_json = ? "
                  "WHERE chat_name = ? AND seq = ?", (payload, chat, seq))


def activate(sm, name="pub"):
    assert sm.set_active_chat(name), f"activation of '{name}' failed"


class TestLatchOnLoad:
    def test_bad_json_row_no_longer_bricks_the_chat(self, sm, tmp_path):
        """THE regression pin: pre-latch this load returned False and the
        entire chat was unreachable."""
        corrupt_row(tmp_path)
        activate(sm)
        # Load survived, remaining 3 messages readable
        assert len(sm.current_chat.messages) == 3
        assert sm.current_chat.messages[0]["content"] == "first answer"

    def test_bad_row_latches_with_parse_cause(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        deg = sm.is_chat_degraded("pub")
        assert deg is not None
        assert deg["skipped"] == 1
        assert deg["causes"] == {"decrypt": 0, "parse": 1}
        assert deg["at"]

    def test_healthy_chat_never_latches(self, sm):
        activate(sm)
        assert sm.is_chat_degraded("pub") is None
        assert len(sm.current_chat.messages) == 4

    def test_clean_reload_clears_stale_latch(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        assert sm.is_chat_degraded("pub")
        # Heal the row out-of-band (what repair will do), reload
        raw(tmp_path, "UPDATE chat_messages SET message_json = ? "
                      "WHERE chat_name = 'pub' AND seq = 0",
            (json.dumps({"role": "user", "content": "healed"}),))
        activate(sm, "default")
        activate(sm, "pub")
        assert sm.is_chat_degraded("pub") is None
        assert len(sm.current_chat.messages) == 4

    def test_foreign_ciphertext_latches_with_decrypt_cause(self, sm, tmp_path,
                                                           monkeypatch):
        """The silent-hollow fix: a plaintext chat carrying rows encrypted by
        a vault that isn't this one (key present, blob undecryptable) used to
        just lose those rows without a trace."""
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "chat_data_key", lambda: _KEY)
        corrupt_row(tmp_path, payload="@enc1:bm90LXJlYWwtY2lwaGVydGV4dA==")
        activate(sm)
        deg = sm.is_chat_degraded("pub")
        assert deg is not None
        assert deg["causes"]["decrypt"] == 1
        assert len(sm.current_chat.messages) == 3


class TestWritesRefused:
    def test_save_refused_and_salvage_survives(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        sm.current_chat.messages.append(
            {"role": "user", "content": "typed into a degraded chat"})
        sm.current_chat._needs_full_resync = True
        assert sm._save_current_chat() is False
        # The corrupt row is STILL on disk — nothing resynced over it
        rows = raw(tmp_path, "SELECT message_json FROM chat_messages "
                             "WHERE chat_name = 'pub' AND seq = 0")
        assert rows[0]["message_json"] == "{this is not json"
        rows = raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                             "WHERE chat_name = 'pub'")
        assert rows[0]["n"] == 4    # unchanged

    def test_append_refused(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        activate(sm, "default")     # append is the by-name background path
        ok = sm.append_messages_to_chat("pub", [
            {"role": "user", "content": "heartbeat write"}])
        assert ok is False
        rows = raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                             "WHERE chat_name = 'pub'")
        assert rows[0]["n"] == 4

    def test_replace_refused(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        activate(sm, "default")
        ok, err = sm.replace_messages("pub", [{"role": "user", "content": "x"}])
        assert ok is False
        assert "degraded" in err

    def test_vault_chat_refused(self, sm, tmp_path, monkeypatch):
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "chat_data_key", lambda: _KEY)
        corrupt_row(tmp_path)
        activate(sm)
        ok, err = sm.vault_chat("pub")
        assert ok is False
        assert "repair" in err

    def test_revert_to_blob_refused(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        activate(sm, "default")
        assert sm.revert_chat_to_blob("pub") is False


class TestEscapeHatches:
    def test_clear_allowed_and_unlatches(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        assert sm.is_chat_degraded("pub")
        assert sm.clear_chat("pub") is True
        assert sm.is_chat_degraded("pub") is None
        rows = raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                             "WHERE chat_name = 'pub'")
        assert rows[0]["n"] == 0
        # And the chat is writable again
        assert sm.append_messages_to_chat("pub", [
            {"role": "user", "content": "fresh start"}]) is True

    def test_delete_clears_latch(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        assert sm.is_chat_degraded("pub")
        activate(sm, "default")
        assert sm.delete_chat("pub") is True
        assert sm.is_chat_degraded("pub") is None

    def test_rename_carries_latch(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        activate(sm, "default")
        ok, new_name = sm.rename_chat("pub", "pub2")
        assert ok
        assert sm.is_chat_degraded("pub") is None
        assert sm.is_chat_degraded(new_name) is not None


class TestSurfaces:
    def test_list_chat_files_carries_degraded_flag(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        entries = {c["name"]: c for c in sm.list_chat_files()}
        assert entries["pub"]["degraded"] is True
        assert entries["default"]["degraded"] is False


class TestRepair:
    def test_preview_writes_nothing(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        ok, report = sm.repair_chat_rows("pub", preview=True)
        assert ok
        assert report["planned_quarantine"] == 1
        assert report["bad"] == [{"seq": 0, "reason": "parse"}]
        # Nothing moved, nothing deleted
        assert raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                             "WHERE chat_name='pub'")[0]["n"] == 4
        assert raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages_quarantine")[0]["n"] == 0
        assert sm.is_chat_degraded("pub")   # still latched

    def test_repair_quarantines_reseqs_and_unlatches(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        ok, report = sm.repair_chat_rows("pub", preview=False)
        assert ok, report
        assert report["quarantined"] == 1
        assert report["kept"] == 3
        # Quarantine holds the bad payload VERBATIM
        q = raw(tmp_path, "SELECT * FROM chat_messages_quarantine WHERE chat_name='pub'")
        assert len(q) == 1
        assert q[0]["message_json"] == "{this is not json"
        assert q[0]["orig_seq"] == 0
        assert q[0]["reason"] == "parse"
        # Survivors contiguous from 0
        seqs = [r["seq"] for r in raw(
            tmp_path, "SELECT seq FROM chat_messages WHERE chat_name='pub' ORDER BY seq")]
        assert seqs == [0, 1, 2]
        # Unlatched + active chat reloaded + writable again
        assert sm.is_chat_degraded("pub") is None
        assert len(sm.current_chat.messages) == 3
        sm.current_chat.messages.append({"role": "user", "content": "post-repair"})
        sm.current_chat._needs_full_resync = True
        assert sm._save_current_chat() is True

    def test_gap_only_repair_closes_gaps(self, sm, tmp_path):
        raw(tmp_path, "DELETE FROM chat_messages WHERE chat_name='pub' AND seq=1")
        activate(sm)
        assert sm.is_chat_degraded("pub") is None   # gaps don't latch
        ok, report = sm.repair_chat_rows("pub", preview=False)
        assert ok
        assert report["quarantined"] == 0
        seqs = [r["seq"] for r in raw(
            tmp_path, "SELECT seq FROM chat_messages WHERE chat_name='pub' ORDER BY seq")]
        assert seqs == [0, 1, 2]

    def test_healthy_chat_repair_is_no_op(self, sm, tmp_path):
        activate(sm)
        ok, report = sm.repair_chat_rows("pub", preview=False)
        assert ok
        assert report.get("no_op") is True
        assert raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                             "WHERE chat_name='pub'")[0]["n"] == 4

    def test_vault_mismatch_refuses_without_force(self, sm, tmp_path, monkeypatch):
        """Three-laws tripwire: >50% undecryptable = wrong vault — restoring
        the matching backup recovers everything; repair must not default to
        quarantining most of a chat."""
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "chat_data_key", lambda: _KEY)
        for seq in (0, 1, 2):
            corrupt_row(tmp_path, seq=seq,
                        payload="@enc1:bm90LXJlYWwtY2lwaGVydGV4dA==")
        activate(sm)
        ok, err = sm.repair_chat_rows("pub", preview=False)
        assert ok is False
        assert "vault mismatch" in err
        # force overrides after the extra confirm
        ok, report = sm.repair_chat_rows("pub", preview=False, force=True)
        assert ok, report
        assert report["quarantined"] == 3
        assert report["kept"] == 1
        # Ciphertext preserved verbatim in quarantine (still recoverable)
        q = raw(tmp_path, "SELECT message_json FROM chat_messages_quarantine "
                          "WHERE chat_name='pub'")
        assert all(r["message_json"].startswith("@enc1:") for r in q)

    def test_keyless_decrypt_repair_refused(self, sm, tmp_path, monkeypatch):
        """Encrypted rows with NO key present may decrypt fine after unlock —
        quarantining them while sealed would be theft."""
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "chat_data_key", lambda: None)
        corrupt_row(tmp_path, payload="@enc1:bm90LXJlYWwtY2lwaGVydGV4dA==")
        activate(sm)
        ok, err = sm.repair_chat_rows("pub", preview=False)
        assert ok is False
        assert "unlock" in err

    def test_quarantine_rides_rename_and_dies_with_delete(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        assert sm.repair_chat_rows("pub", preview=False)[0]
        activate(sm, "default")
        ok, new_name = sm.rename_chat("pub", "pub2")
        assert ok
        assert raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages_quarantine "
                             "WHERE chat_name=?", (new_name,))[0]["n"] == 1
        assert sm.delete_chat(new_name) is True
        assert raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages_quarantine")[0]["n"] == 0

    def test_diagnose_is_read_only(self, sm, tmp_path):
        corrupt_row(tmp_path)
        report = sm.diagnose_chat_rows("pub")
        assert report["total"] == 4
        assert report["ok"] == 3
        assert report["bad"] == [{"seq": 0, "reason": "parse"}]
        assert raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                             "WHERE chat_name='pub'")[0]["n"] == 4


class TestUnlockReeval:
    def test_decrypt_cause_dropped_and_active_reloaded(self, sm, tmp_path,
                                                       monkeypatch):
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "chat_data_key", lambda: _KEY)
        corrupt_row(tmp_path, payload="@enc1:bm90LXJlYWwtY2lwaGVydGV4dA==")
        activate(sm)
        assert sm.is_chat_degraded("pub")["causes"]["decrypt"] == 1
        # "Unlock" heals the row (simulating: it belonged to the now-open vault)
        raw(tmp_path, "UPDATE chat_messages SET message_json = ? "
                      "WHERE chat_name = 'pub' AND seq = 0",
            (json.dumps({"role": "user", "content": "decrypted fine now"}),))
        sm.reeval_degraded_chats()
        assert sm.is_chat_degraded("pub") is None
        assert len(sm.current_chat.messages) == 4   # active chat reloaded

    def test_parse_cause_survives_reeval(self, sm, tmp_path):
        corrupt_row(tmp_path)
        activate(sm)
        assert sm.is_chat_degraded("pub")
        sm.reeval_degraded_chats()
        assert sm.is_chat_degraded("pub") is not None   # a key heals nothing
