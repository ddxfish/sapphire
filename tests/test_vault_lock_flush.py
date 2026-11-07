"""[REGRESSION_GUARD] Vault lock order (2026-11-07): lock() flushes the
private active chat WHILE the key is present, then drops the key, then evicts
with flush=False. The old order (key first, then the eviction's switch-away
flush) refused on every lock — a red "Chat Save failed" toast — and dropped
any pending delta while promising it would re-sync. Real vault crypto, real
store, tmp paths.
"""
import types
from unittest.mock import patch

import pytest

import core.prompt_vault as pv

DEFAULTS = {"prompt": "default"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_N', 2 ** 10)
    monkeypatch.setattr(pv, '_publish', lambda action: None)
    monkeypatch.setattr(pv, '_handoff_active', lambda gone: None)
    monkeypatch.setattr(pv, '_audit_row', lambda event: None)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False

    import core.chat.history as hist
    # the real seal rule against the tmp vault (conftest pins it False)
    monkeypatch.setattr(hist, '_vault_sealed', lambda: pv.vault_exists() and not pv.vault_unlocked())
    errors = []
    real_publish = hist.publish
    monkeypatch.setattr(hist, 'publish', lambda ev, data=None: (
        errors.append(data) if data and data.get('task') == 'Chat Save' else None))
    with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(DEFAULTS)), \
         patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(DEFAULTS)):
        sm = hist.ChatSessionManager(history_dir=str(tmp_path / 'hist'))
        shim = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
        import core.api_fastapi as af
        monkeypatch.setattr(af, 'get_system', lambda: shim)
        assert pv.setup("changeme")[0]
        sm.create_chat("secret")
        sm.set_active_chat("secret")
        assert sm.update_chat_settings({'private_chat': True}, expected_active="secret")
        yield types.SimpleNamespace(sm=sm, errors=errors, hist=hist)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False


def test_lock_flushes_pending_delta_and_raises_no_toast(world):
    sm = world.sm
    sm.current_chat.messages.append({"role": "user", "content": "pending line"})
    assert pv.lock("test")
    assert sm.get_active_chat_name() != "secret"        # evicted
    assert world.errors == []                            # no red Chat Save toast
    assert pv.unlock("changeme")[0]
    contents = [m.get("content") for m in sm.read_chat_messages("secret")]
    assert "pending line" in contents                    # flushed, encrypted, kept


def test_eviction_switch_skips_keyless_flush(world):
    """Boot-style eviction: already sealed, active chat private. The switch
    must not attempt a save (it could only refuse)."""
    sm = world.sm
    with pv._lock:
        pv._key = pv._salt = pv._data = None             # seal without lock()
    assert sm.evict_private_active() is not None
    assert world.errors == []


def test_public_active_lock_untouched(world):
    sm = world.sm
    sm.create_chat("pub")
    sm.set_active_chat("pub")
    assert pv.lock("test")
    assert sm.get_active_chat_name() == "pub"
    assert world.errors == []
