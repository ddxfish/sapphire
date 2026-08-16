"""[REGRESSION_GUARD] Vaulted chats Phase 2 — encryption at rest.

Plan: tmp/vaulted-chats-plan.md (rulings 2026-08-15: synchronous flip
migration, plain names, one shared vault key). A vaulted chat's message
rows, settings, and tool images live on disk as '@enc1:' AES-256-GCM
blobs under the chat DATA key (random, wrapped inside the vault frame —
rekey never touches chat rows). vaulted=0 + private_chat=1 = pre-Phase-2
legacy, swept at unlock. Tests pin `prompt_vault.chat_data_key` to a
fixed key (real AESGCM runs) or to None (sealed).
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

TEST_DEFAULTS = {"prompt": "default"}
_KEY = bytes(range(32))


@pytest.fixture
def sm(tmp_path, monkeypatch):
    """Hermetic ChatSessionManager: 'pub' (public, 2 msgs + 1 tool image)."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.append_messages_to_chat("pub", [
            {"role": "user", "content": "the gravy secret <<IMG::tool:img1>>"},
            {"role": "assistant", "content": "simmer quietly"},
        ])
        m.save_tool_image("img1", b"\x89PNG-fake-bytes", "image/png", chat_name="pub")
        yield m


def _key_on(monkeypatch):
    from core import prompt_vault as pv
    monkeypatch.setattr(pv, "chat_data_key", lambda: _KEY)


def _key_off(monkeypatch):
    from core import prompt_vault as pv
    monkeypatch.setattr(pv, "chat_data_key", lambda: None)


def _seal(monkeypatch, sealed=True):
    import core.chat.history as hist
    monkeypatch.setattr(hist, "_vault_sealed", lambda: sealed)


def raw(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _rows_enc(tmp_path, name):
    return [str(r["message_json"]).startswith("@enc1:")
            for r in raw(tmp_path, "SELECT message_json FROM chat_messages "
                                   "WHERE chat_name = ? ORDER BY seq", (name,))]


class TestCryptoPrimitives:
    def test_round_trip_and_marker(self, monkeypatch):
        from core import prompt_vault as pv
        _key_on(monkeypatch)
        blob = pv.encrypt_chat_blob(b"hello vault")
        assert pv.is_chat_encrypted(blob)
        assert not pv.is_chat_encrypted('{"role": "user"}')
        assert pv.decrypt_chat_blob(blob) == b"hello vault"

    def test_no_key_no_service(self, monkeypatch):
        from core import prompt_vault as pv
        _key_on(monkeypatch)
        blob = pv.encrypt_chat_blob(b"x")
        _key_off(monkeypatch)
        assert pv.encrypt_chat_blob(b"x") is None
        assert pv.decrypt_chat_blob(blob) is None

    def test_tamper_returns_none(self, monkeypatch):
        from core import prompt_vault as pv
        _key_on(monkeypatch)
        blob = pv.encrypt_chat_blob(b"x")
        assert pv.decrypt_chat_blob(blob[:-4] + "AAAA") is None


class TestVaultChatMigration:
    def test_encrypts_everything_on_disk(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert ok, err
        assert all(_rows_enc(tmp_path, "pub"))
        row = raw(tmp_path, "SELECT settings, messages, vaulted FROM chats "
                            "WHERE name = 'pub'")[0]
        assert str(row["settings"]).startswith("@enc1:")
        assert row["messages"] == "[]"      # frozen blob scrubbed
        assert row["vaulted"] == 1
        img = raw(tmp_path, "SELECT data FROM tool_images WHERE id = 'img1'")[0]
        assert str(img["data"]).startswith("@enc1:")

    def test_reads_decrypt_with_key(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        msgs = sm.read_chat_messages("pub")
        assert any("gravy" in str(m.get("content")) for m in msgs)
        s = sm.read_chat_settings("pub")
        assert s is not None
        exp = sm.export_chat("pub")
        assert exp and len(exp["messages"]) == 2
        data, mt = sm.get_tool_image("img1")
        assert data == b"\x89PNG-fake-bytes" and mt == "image/png"

    def test_unvault_restores_plaintext(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        ok, err = sm.unvault_chat("pub")
        assert ok, err
        assert not any(_rows_enc(tmp_path, "pub"))
        row = raw(tmp_path, "SELECT settings, vaulted FROM chats WHERE name = 'pub'")[0]
        json.loads(row["settings"])          # parses = plaintext
        assert row["vaulted"] == 0
        img = raw(tmp_path, "SELECT data FROM tool_images WHERE id = 'img1'")[0]
        assert img["data"] == b"\x89PNG-fake-bytes"

    def test_locked_refuses_both_ways(self, sm, monkeypatch):
        _key_off(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert not ok and "lock" in err
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        _key_off(monkeypatch)
        ok, err = sm.unvault_chat("pub")
        assert not ok and "lock" in err

    def test_blob_chat_converts_then_encrypts(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute("DELETE FROM chat_messages WHERE chat_name = 'pub'")
        conn.execute("UPDATE chats SET storage_format = 'blob', messages = ? "
                     "WHERE name = 'pub'",
                     (json.dumps([{"role": "user", "content": "old blob"}]),))
        conn.commit(); conn.close()
        ok, err = sm.vault_chat("pub")
        assert ok, err
        assert all(_rows_enc(tmp_path, "pub")) and _rows_enc(tmp_path, "pub")
        assert raw(tmp_path, "SELECT storage_format FROM chats "
                             "WHERE name = 'pub'")[0][0] == "rows"

    def test_latched_blob_refuses(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute("UPDATE chats SET storage_format = 'blob', "
                     "conversion_failed = 1 WHERE name = 'pub'")
        conn.commit(); conn.close()
        ok, err = sm.vault_chat("pub")
        assert not ok and "latched" in err


class TestOngoingWrites:
    def test_background_append_encrypts(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        assert sm.append_messages_to_chat("pub", [
            {"role": "user", "content": "new secret"}]) is True
        assert all(_rows_enc(tmp_path, "pub"))
        assert len(_rows_enc(tmp_path, "pub")) == 3

    def test_active_chat_save_encrypts(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        assert sm.set_active_chat("pub")
        sm.current_chat.messages.append(
            {"role": "user", "content": "typed live"})
        sm._save_current_chat()
        flags = _rows_enc(tmp_path, "pub")
        assert len(flags) == 3 and all(flags)
        # And the read round-trips through the decrypt funnel.
        assert "typed live" in str(sm.read_chat_messages("pub")[-1].get("content"))

    def test_settings_patch_stays_encrypted(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        assert sm.set_named_chat_settings("pub", {"voice": "af_x"}) is True
        row = raw(tmp_path, "SELECT settings FROM chats WHERE name = 'pub'")[0]
        assert str(row["settings"]).startswith("@enc1:")
        assert sm.get_settings_for("pub").get("voice") == "af_x"


class TestFlipChokepoints:
    def test_set_named_flip_migrates(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        assert sm.set_named_chat_settings("pub", {"private_chat": True}) is True
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 1
        assert all(_rows_enc(tmp_path, "pub"))

    def test_set_named_unflip_decrypts_first(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.set_named_chat_settings("pub", {"private_chat": True})
        assert sm.set_named_chat_settings("pub", {"private_chat": False}) is True
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 0
        assert not any(_rows_enc(tmp_path, "pub"))
        s = sm.get_settings_for("pub")
        assert s.get("private_chat") is False

    def test_unflip_refused_without_key(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.set_named_chat_settings("pub", {"private_chat": True})
        _key_off(monkeypatch)
        # Sealed decrypt impossible → the settings write itself refuses.
        assert sm.set_named_chat_settings("pub", {"private_chat": False}) is False
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 1

    def test_active_flip_via_update_settings(self, sm, tmp_path, monkeypatch):
        """The talk-stamp path: update_chat_settings on the ACTIVE chat."""
        _key_on(monkeypatch)
        assert sm.set_active_chat("pub")
        assert sm.update_chat_settings({"private_chat": True}) is True
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 1
        assert all(_rows_enc(tmp_path, "pub"))

    def test_deferred_encrypt_heals_at_unlock(self, sm, tmp_path, monkeypatch):
        """Flip while sealed-keyless (legacy path): flag lands, encryption
        defers, vault_pending_private() sweeps it when the key returns."""
        _key_off(monkeypatch)
        assert sm.set_named_chat_settings("pub", {"private_chat": True}) is True
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 0
        _key_on(monkeypatch)
        assert sm.vault_pending_private() == 1
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 1
        assert all(_rows_enc(tmp_path, "pub"))


class TestSealedBehavior:
    def test_sealed_hides_vaulted_from_list_and_search(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        assert "pub" not in {c["name"] for c in sm.list_chat_files()}
        assert sm.search_chat_content("gravy") == {}
        assert sm.read_chat_settings("pub") is None
        assert sm.read_chat_messages("pub") == []
        assert sm.get_tool_image("img1") is None

    def test_unlocked_search_decrypt_scan_finds(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        hits = sm.search_chat_content("gravy")
        assert hits.get("pub") == 1

    def test_include_hidden_stub_for_eviction(self, sm, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        entries = {c["name"]: c for c in sm.list_chat_files(include_hidden=True)}
        assert entries["pub"]["private_chat"] is True   # stub keeps eviction sane


class TestPruneSafety:
    def test_prune_never_deletes_unverifiable(self, sm, tmp_path, monkeypatch):
        """Encrypted rows hide <<IMG>> markers — keyless prune must skip,
        not orphan-collect live images (three-laws)."""
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        _key_off(monkeypatch)
        assert sm._prune_orphaned_tool_images("pub") == 0
        assert raw(tmp_path, "SELECT 1 FROM tool_images WHERE id='img1'")

    def test_prune_with_key_keeps_live_reaps_orphans(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.save_tool_image("orphan1", b"junk", "image/png", chat_name="pub")
        sm.vault_chat("pub")
        assert sm._prune_orphaned_tool_images("pub") == 1
        assert raw(tmp_path, "SELECT 1 FROM tool_images WHERE id='img1'")
        assert not raw(tmp_path, "SELECT 1 FROM tool_images WHERE id='orphan1'")


class TestSnapshotRetirement:
    def test_boot_deletes_snapshot_and_latches(self, tmp_path):
        (tmp_path / "pre_rowify_20260709.db").write_bytes(b"old plaintext copy")
        with patch("core.chat.history.get_system_defaults",
                   side_effect=lambda: dict(TEST_DEFAULTS)), \
             patch("core.chat.history.get_user_defaults",
                   side_effect=lambda: dict(TEST_DEFAULTS)):
            from core.chat.history import ChatSessionManager
            ChatSessionManager(history_dir=str(tmp_path))
        assert not list(tmp_path.glob("pre_rowify_*.db"))
        assert (tmp_path / ".pre_rowify_snapshot_done").exists()


class TestCrossFeatureRoundTrips:
    """Chat surgery on ENCRYPTED chats — trim/clear/rename/delete all run
    through patched paths, but only a round-trip proves the seams hold."""

    def _grow(self, sm, turns=3):
        for i in range(turns):
            sm.append_messages_to_chat("pub", [
                {"role": "user", "content": f"question {i}"},
                {"role": "assistant", "content": f"answer {i}"},
            ])

    def test_trim_on_vaulted_round_trip(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        self._grow(sm)
        sm.vault_chat("pub")
        ok, report = sm.trim_chat("pub", 1, 1)
        assert ok, report
        assert report["deleted_messages"] > 0
        flags = _rows_enc(tmp_path, "pub")
        assert flags and all(flags)          # surgery result re-encrypted
        msgs = sm.read_chat_messages("pub")  # and still readable
        assert any("answer 2" in str(m.get("content")) for m in msgs)

    def test_clear_wipes_future_appends_still_encrypt(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        assert sm.clear_named_chat_messages("pub") is True
        assert _rows_enc(tmp_path, "pub") == []
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'")[0][0] == 1
        sm.append_messages_to_chat("pub", [{"role": "user", "content": "fresh"}])
        assert all(_rows_enc(tmp_path, "pub"))

    def test_rename_vaulted_carries_everything(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        ok, new_name = sm.rename_chat("pub", "moved")
        assert ok, new_name
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='moved'")[0][0] == 1
        assert all(_rows_enc(tmp_path, "moved"))
        msgs = sm.read_chat_messages("moved")
        assert any("gravy" in str(m.get("content")) for m in msgs)
        img = raw(tmp_path, "SELECT chat_name FROM tool_images WHERE id='img1'")[0]
        assert img["chat_name"] == "moved"

    def test_delete_vaulted_cascades(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.vault_chat("pub")
        assert sm.delete_chat("pub") is True
        assert not raw(tmp_path, "SELECT 1 FROM chats WHERE name='pub'")
        assert not raw(tmp_path, "SELECT 1 FROM chat_messages WHERE chat_name='pub'")
        assert not raw(tmp_path, "SELECT 1 FROM tool_images WHERE chat_name='pub'")


class TestSealedSaveCalm:
    """Lock-time switch-away flush on a vaulted chat refuses (key already
    dropped — by design; rows are at rest encrypted). The surfaced event
    must be CALM, not a data-loss scare, and the resync latch must still
    set. Krem live-hit the scary version 2026-08-15. The branch keys on
    _enc_value's 'vault sealed' wording — this test breaks if either side
    is reworded alone."""

    def test_sealed_flush_publishes_calm_event(self, sm, monkeypatch):
        _key_on(monkeypatch)
        _seal(monkeypatch, False)
        assert sm.set_active_chat("pub")
        ok, err = sm.vault_chat("pub")
        assert ok, err
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        events = []
        import core.chat.history as hist
        monkeypatch.setattr(hist, "publish",
                            lambda ev, data=None: events.append((ev, data)))
        sm.current_chat.messages.append({"role": "user", "content": "pending"})
        sm._save_current_chat()   # must not raise
        saves = [d for _, d in events if d and d.get("task") == "Chat Save"]
        assert saves, "expected a Chat Save event"
        msg = saves[-1]["error"]
        assert "re-syncs" in msg and "lost" not in msg
        assert getattr(sm.current_chat, "_needs_full_resync", False)
