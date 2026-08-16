"""[REGRESSION_GUARD] Vault v1.3 Phase 1 — chat-scoped plugin data.

Plan: tmp/v13-chat-scoped-storage-plan.md. plugin_chat_data rows follow
the chat through vault/rename/delete: sealed in vault_chat, strictly
unsealed in unvault_chat, renamed in rename_chat's transaction, deleted
with delete_chat (ruling (a): no core archive — vault wins). Sealed
contract: reads on a hidden chat come back empty, writes RAISE.

Rig mirrors test_vaulted_chats_p2: prompt_vault.chat_data_key pinned to
a fixed key (real AESGCM runs) or to None (keyless), _vault_sealed
patched per test.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

TEST_DEFAULTS = {"prompt": "default"}
_KEY = bytes(range(32))
PLUGIN = "game-room"


@pytest.fixture
def sm(tmp_path, monkeypatch):
    """Hermetic ChatSessionManager with one public chat 'pub'."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
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


def _open(monkeypatch):
    _key_on(monkeypatch)
    _seal(monkeypatch, False)


def raw(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def rawx(tmp_path, sql, params=()):
    conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


class TestPublicRoundtrip:
    def test_put_get(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", {"hp": 7, "room": "den"})
        assert sm.plugin_data_get(PLUGIN, "pub", "save") == {"hp": 7, "room": "den"}
        assert sm.plugin_data_get(PLUGIN, "pub", "missing", "dflt") == "dflt"

    def test_put_overwrites_seq0(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", 1)
        sm.plugin_data_put(PLUGIN, "pub", "save", 2)
        assert sm.plugin_data_get(PLUGIN, "pub", "save") == 2
        assert len(raw(sm.history_dir, "SELECT * FROM plugin_chat_data")) == 1

    def test_append_and_read_all_ordered(self, sm, monkeypatch):
        _open(monkeypatch)
        assert sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "a"}) == 1
        assert sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "b"}) == 2
        assert sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "c"}) == 3
        assert sm.plugin_data_read_all(PLUGIN, "pub", "journal") == [
            {"e": "a"}, {"e": "b"}, {"e": "c"}]

    def test_replace_renumbers(self, sm, monkeypatch):
        _open(monkeypatch)
        for e in ("a", "b", "c", "d"):
            sm.plugin_data_append(PLUGIN, "pub", "journal", e)
        sm.plugin_data_replace(PLUGIN, "pub", "journal", ["a", "b"])
        assert sm.plugin_data_read_all(PLUGIN, "pub", "journal") == ["a", "b"]
        seqs = [r["seq"] for r in raw(
            sm.history_dir,
            "SELECT seq FROM plugin_chat_data WHERE key='journal' ORDER BY seq")]
        assert seqs == [1, 2]

    def test_delete_key_and_all(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", 1)
        sm.plugin_data_append(PLUGIN, "pub", "journal", "e")
        assert sm.plugin_data_keys(PLUGIN, "pub") == ["journal", "save"]
        sm.plugin_data_delete(PLUGIN, "pub", "journal")
        assert sm.plugin_data_keys(PLUGIN, "pub") == ["save"]
        sm.plugin_data_delete(PLUGIN, "pub")
        assert sm.plugin_data_keys(PLUGIN, "pub") == []

    def test_plugin_isolation(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", "mine")
        sm.plugin_data_put("other-plugin", "pub", "save", "theirs")
        assert sm.plugin_data_get(PLUGIN, "pub", "save") == "mine"
        assert sm.plugin_data_get("other-plugin", "pub", "save") == "theirs"

    def test_write_to_missing_chat_raises(self, sm, monkeypatch):
        _open(monkeypatch)
        with pytest.raises(ValueError):
            sm.plugin_data_put(PLUGIN, "ghost", "save", 1)
        with pytest.raises(ValueError):
            sm.plugin_data_append(PLUGIN, "ghost", "journal", 1)


class TestChatLifecycle:
    def test_rename_carries_rows(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_append(PLUGIN, "pub", "journal", "e1")
        ok, new = sm.rename_chat("pub", "renamed")
        assert ok, new
        assert sm.plugin_data_read_all(PLUGIN, "renamed", "journal") == ["e1"]
        assert sm.plugin_data_read_all(PLUGIN, "pub", "journal") == []

    def test_delete_chat_removes_rows(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_append(PLUGIN, "pub", "journal", "e1")
        assert sm.delete_chat("pub")
        assert raw(sm.history_dir,
                   "SELECT * FROM plugin_chat_data WHERE chat_name='pub'") == []

    def test_clear_chat_preserves_plugin_data(self, sm, monkeypatch):
        # Clearing messages is not deleting the chat — game state survives.
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", {"hp": 7})
        sm.clear_chat("pub")
        assert sm.plugin_data_get(PLUGIN, "pub", "save") == {"hp": 7}


class TestVaultFlip:
    def test_vault_encrypts_rows_at_rest(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", {"hp": 7})
        sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "a"})
        ok, err = sm.vault_chat("pub")
        assert ok, err
        vals = [r["value"] for r in raw(
            sm.history_dir, "SELECT value FROM plugin_chat_data")]
        assert vals and all(v.startswith("@enc1:") for v in vals)
        # Key present: reads decrypt through the funnel
        assert sm.plugin_data_get(PLUGIN, "pub", "save") == {"hp": 7}
        assert sm.plugin_data_read_all(PLUGIN, "pub", "journal") == [{"e": "a"}]

    def test_write_after_vault_encrypted_at_rest(self, sm, monkeypatch):
        _open(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert ok, err
        sm.plugin_data_put(PLUGIN, "pub", "save", {"hp": 9})
        seq = sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "z"})
        assert seq == 1
        vals = [r["value"] for r in raw(
            sm.history_dir, "SELECT value FROM plugin_chat_data")]
        assert vals and all(v.startswith("@enc1:") for v in vals)
        assert sm.plugin_data_get(PLUGIN, "pub", "save") == {"hp": 9}

    def test_unvault_decrypts_rows(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "a"})
        assert sm.vault_chat("pub")[0]
        ok, err = sm.unvault_chat("pub")
        assert ok, err
        vals = [r["value"] for r in raw(
            sm.history_dir, "SELECT value FROM plugin_chat_data")]
        assert vals == [json.dumps({"e": "a"})]

    def test_unvault_aborts_on_tampered_row(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "a"})
        assert sm.vault_chat("pub")[0]
        rawx(sm.history_dir,
             "UPDATE plugin_chat_data SET value='@enc1:AAAAtampered'")
        ok, err = sm.unvault_chat("pub")
        assert not ok
        assert "plugin data" in err


class TestSealedContract:
    def _vaulted_pub(self, sm, monkeypatch):
        _open(monkeypatch)
        sm.plugin_data_put(PLUGIN, "pub", "save", {"hp": 7})
        sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "a"})
        assert sm.vault_chat("pub")[0]

    def test_sealed_reads_empty(self, sm, monkeypatch):
        self._vaulted_pub(sm, monkeypatch)
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        assert sm.plugin_data_get(PLUGIN, "pub", "save", "dflt") == "dflt"
        assert sm.plugin_data_read_all(PLUGIN, "pub", "journal") == []
        assert sm.plugin_data_keys(PLUGIN, "pub") == []

    def test_sealed_writes_raise(self, sm, monkeypatch):
        self._vaulted_pub(sm, monkeypatch)
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        with pytest.raises(RuntimeError):
            sm.plugin_data_put(PLUGIN, "pub", "save", {"hp": 8})
        with pytest.raises(RuntimeError):
            sm.plugin_data_append(PLUGIN, "pub", "journal", {"e": "b"})
        with pytest.raises(RuntimeError):
            sm.plugin_data_delete(PLUGIN, "pub", "journal")
        # Nothing leaked through
        vals = [r["value"] for r in raw(
            sm.history_dir, "SELECT value FROM plugin_chat_data")]
        assert all(v.startswith("@enc1:") for v in vals)

    def test_keyless_not_sealed_hides_vaulted_rows(self, sm, monkeypatch):
        # G4 state: vault file missing reads not-sealed, but a vaulted
        # chat's rows are ciphertext with no key — still as-if-absent.
        self._vaulted_pub(sm, monkeypatch)
        _key_off(monkeypatch)
        _seal(monkeypatch, False)
        assert sm.plugin_data_get(PLUGIN, "pub", "save", "dflt") == "dflt"
        assert sm.plugin_data_read_all(PLUGIN, "pub", "journal") == []
        with pytest.raises(RuntimeError):
            sm.plugin_data_put(PLUGIN, "pub", "save", 1)


class TestLoaderVeneer:
    def test_bound_roundtrip(self, sm, monkeypatch):
        _open(monkeypatch)
        from core.plugin_loader import PluginChatState
        monkeypatch.setattr(PluginChatState, "_sm", staticmethod(lambda: sm))

        cs = PluginChatState(PLUGIN)
        cs.put("pub", "save", {"hp": 7})
        assert cs.get("pub", "save") == {"hp": 7}
        assert cs.append("pub", "journal", "e1") == 1
        assert cs.read_all("pub", "journal") == ["e1"]
        assert cs.keys("pub") == ["journal", "save"]
        cs.delete("pub")
        assert cs.keys("pub") == []

    def test_get_chat_state_cached(self, sm):
        from core.plugin_loader import PluginLoader
        ldr = PluginLoader()
        a = ldr.get_chat_state("some-plugin")
        assert a is ldr.get_chat_state("some-plugin")
