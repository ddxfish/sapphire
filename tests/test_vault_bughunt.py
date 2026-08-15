"""[REGRESSION_GUARD] Vaulted chats — 2026-08-15 bug-hunt fix wave.

Five-scout campaign (tmp/vault-bug-hunt-plan.md, results in
tmp/vault-bug-hunt-results.md). Pins the Tier-1 fixes:

  G1  search survives a vaulted chat's '@enc1:' rows (sealed AND unlocked)
  G2  clear_chat refuses a hidden chat (stale-name bulk-clear)
  G3  chat-key mint guard: never mint over existing sealed rows;
      malformed chat_key refuses the unlock (VaultCorrupt)
  G4  a MISSING vault file hides vaulted rows (no name leak via the stub)
  G5  vault_pending_private survives a mixed row (seed-defect family)
  G6  the unlock sweep heals vaulted=1 + private_chat=False stranding
  R2  end_streaming's 1→0 transition retries a refused eviction
  R4  flip-to-private is refused while the vault is sealed (both chokepoints)
  R5  update_chat_settings(expected_active=...) refuses a retargeted write
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

TEST_DEFAULTS = {"prompt": "default"}
_KEY = bytes(range(32))


@pytest.fixture
def sm(tmp_path, monkeypatch):
    """Hermetic ChatSessionManager: 'pub' (public, 2 msgs) + 'other' (public)."""
    with patch("core.chat.history.get_system_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)), \
         patch("core.chat.history.get_user_defaults",
               side_effect=lambda: dict(TEST_DEFAULTS)):
        from core.chat.history import ChatSessionManager
        m = ChatSessionManager(history_dir=str(tmp_path))
        m.create_chat("pub")
        m.append_messages_to_chat("pub", [
            {"role": "user", "content": "the gravy secret"},
            {"role": "assistant", "content": "simmer quietly"},
        ])
        m.create_chat("other")
        m.append_messages_to_chat("other", [
            {"role": "user", "content": "public gravy talk"},
        ])
        yield m
    # The fixture registered the sealed-rows probe against this temp store —
    # drop it so later tests (and other files) don't probe a dead DB.
    from core import prompt_vault as pv
    pv.set_sealed_rows_probe(None)


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


class TestG1SearchSurvivesVaultedRows:
    def test_sealed_search_still_finds_public_content(self, sm, monkeypatch):
        # One vaulted chat used to abort BOTH SQL passes → {} for everything.
        _key_on(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert ok, err
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        hits = sm.search_chat_content("gravy")
        assert hits.get("other") == 1          # public content findable
        assert "pub" not in hits               # sealed content stays shut

    def test_unlocked_search_finds_both_sides(self, sm, monkeypatch):
        # Unlocked used to INVERT: only vaulted hits, no public rows hits.
        _key_on(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert ok, err
        _seal(monkeypatch, False)
        hits = sm.search_chat_content("gravy")
        assert hits.get("other") == 1          # public rows scan alive
        assert hits.get("pub") == 1            # decrypt-scan covers vaulted


class TestG2ClearChatGate:
    def test_clear_refuses_hidden_chat(self, sm, tmp_path, monkeypatch):
        _key_on(monkeypatch)
        sm.set_named_chat_settings("pub", {"private_chat": True})
        _key_off(monkeypatch)
        _seal(monkeypatch, True)
        assert sm.clear_chat("pub") is False
        n = raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                          "WHERE chat_name = 'pub'")[0]["n"]
        assert n == 2                          # history untouched

    def test_clear_still_works_public(self, sm, tmp_path, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.clear_chat("other") is True
        n = raw(tmp_path, "SELECT COUNT(*) AS n FROM chat_messages "
                          "WHERE chat_name = 'other'")[0]["n"]
        assert n == 0


class TestG3MintGuard:
    def test_no_mint_over_sealed_rows(self, monkeypatch):
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "_key", b"k" * 32)
        monkeypatch.setattr(pv, "_data", {})           # unlocked, no chat_key
        monkeypatch.setattr(pv, "_save_locked", lambda: True)
        monkeypatch.setattr(pv, "_sealed_rows_probe", lambda: True)
        assert pv.chat_data_key() is None              # refused, no mint

    def test_probe_error_refuses(self, monkeypatch):
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "_key", b"k" * 32)
        monkeypatch.setattr(pv, "_data", {})
        monkeypatch.setattr(pv, "_save_locked", lambda: True)
        def boom():
            raise RuntimeError("db gone")
        monkeypatch.setattr(pv, "_sealed_rows_probe", boom)
        assert pv.chat_data_key() is None

    def test_mint_ok_when_no_sealed_rows(self, monkeypatch):
        from core import prompt_vault as pv
        monkeypatch.setattr(pv, "_key", b"k" * 32)
        monkeypatch.setattr(pv, "_data", {})
        monkeypatch.setattr(pv, "_save_locked", lambda: True)
        monkeypatch.setattr(pv, "_sealed_rows_probe", lambda: False)
        assert isinstance(pv.chat_data_key(), bytes)

    def test_normalize_refuses_malformed_chat_key(self):
        from core import prompt_vault as pv
        with pytest.raises(pv.VaultCorrupt):
            pv._normalize({"chat_key": "zz-not-hex"})
        good = "ab" * 32
        assert pv._normalize({"chat_key": good})["chat_key"] == good
        assert "chat_key" not in pv._normalize({})     # absent stays absent


class TestG4MissingVaultFile:
    def test_vaulted_row_hidden_when_vault_absent(self, sm, monkeypatch):
        _key_on(monkeypatch)
        ok, err = sm.vault_chat("pub")
        assert ok, err
        # Vault file gone: not sealed (no vault) but keyless — G4 hides the
        # vaulted row anyway; the legacy/public chats stay visible.
        _key_off(monkeypatch)
        names = [c["name"] for c in sm.list_chat_files()]
        assert "pub" not in names              # no name leak via the {} stub
        assert "other" in names
        assert sm._vault_hidden("pub") is True
        assert sm._vault_hidden("other") is False


class TestG5G6PendingSweep:
    def test_sweep_survives_mixed_row(self, sm, tmp_path, monkeypatch):
        # vaulted=0 with '@enc1:' settings (the mixed state) used to kill
        # the whole sweep via SQL json_extract.
        conn = sqlite3.connect(str(tmp_path / "sapphire_history.db"))
        conn.execute("UPDATE chats SET settings = '@enc1:garbage', vaulted = 0 "
                     "WHERE name = 'other'")
        conn.execute("""UPDATE chats SET settings = ? WHERE name = 'pub'""",
                     (json.dumps({"prompt": "default", "private_chat": True}),))
        conn.commit()
        conn.close()
        _key_on(monkeypatch)
        assert sm.vault_pending_private() == 1     # 'pub' sealed, no blowup

    def test_sweep_heals_inverse_stranding(self, sm, tmp_path, monkeypatch):
        # vaulted=1 whose settings say private_chat is falsy: the raced-flip
        # stranding. The flag is truth → sweep decrypts back to public.
        _key_on(monkeypatch)
        ok, err = sm.vault_chat("pub")             # public chat, sealed rows
        assert ok, err
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'"
                   )[0]["vaulted"] == 1
        sm.vault_pending_private()
        assert raw(tmp_path, "SELECT vaulted FROM chats WHERE name='pub'"
                   )[0]["vaulted"] == 0            # healed


class TestR2StreamEndReEvict:
    def test_end_streaming_retries_eviction(self, sm, monkeypatch):
        assert sm.set_active_chat("pub")
        assert sm.update_chat_settings({"private_chat": True})
        _seal(monkeypatch, True)
        # Mid-stream: eviction refused ("staying"), private chat stays active.
        sm.begin_streaming()
        assert sm.evict_private_active() is None
        assert sm.get_active_chat_name() == "pub"
        # 1→0 boundary: the retry point.
        sm.end_streaming()
        assert sm.get_active_chat_name() != "pub"


class TestR4SealedFlipRefused:
    def test_active_chokepoint_refuses(self, sm, monkeypatch):
        assert sm.set_active_chat("pub")
        _seal(monkeypatch, True)
        assert sm.update_chat_settings({"private_chat": True}) is False
        assert not sm.get_chat_settings().get("private_chat")

    def test_named_chokepoint_refuses(self, sm, monkeypatch):
        _seal(monkeypatch, True)
        assert sm.set_named_chat_settings("other", {"private_chat": True}) is False

    def test_unsealed_flip_still_works(self, sm, monkeypatch):
        _seal(monkeypatch, False)
        _key_on(monkeypatch)
        assert sm.set_active_chat("pub")
        assert sm.update_chat_settings({"private_chat": True}) is True
        assert sm.get_chat_settings().get("private_chat") is True


class TestR5ExpectedActive:
    def test_retargeted_write_refused(self, sm):
        assert sm.set_active_chat("pub")
        assert sm.update_chat_settings({"voice": "x"},
                                       expected_active="other") is False
        assert sm.get_chat_settings().get("voice") != "x"

    def test_matching_active_writes(self, sm):
        assert sm.set_active_chat("pub")
        assert sm.update_chat_settings({"voice": "x"},
                                       expected_active="pub") is True
        assert sm.get_chat_settings().get("voice") == "x"
