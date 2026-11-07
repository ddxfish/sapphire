"""[REGRESSION_GUARD] Delete vault (2026-11-07): the forgotten-passphrase exit
in Settings › Privacy. Its key is the only key to every vaulted chat, so
destroy() purges those chats, then removes the vault file and refs sidecar,
and lands the pointer on a public chat (default comes back, public, when it
was the vaulted one). Real vault crypto, real store, tmp paths.
"""
import types
from unittest.mock import patch

import pytest

import core.prompt_vault as pv

DEFAULTS = {"prompt": "default"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    vdir = tmp_path / 'prompts'
    vdir.mkdir()
    monkeypatch.setattr(pv, 'VAULT_PATH', vdir / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', vdir / 'vault_refs.json')
    monkeypatch.setattr(pv, '_N', 2 ** 10)
    monkeypatch.setattr(pv, '_publish', lambda action: None)
    monkeypatch.setattr(pv, '_handoff_active', lambda gone: None)
    monkeypatch.setattr(pv, '_audit_row', lambda event: None)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False

    import core.chat.history as hist
    monkeypatch.setattr(hist, '_vault_sealed', lambda: pv.vault_exists() and not pv.vault_unlocked())
    events = []
    monkeypatch.setattr(hist, 'publish', lambda ev, data=None: events.append((ev, data)))
    with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(DEFAULTS)), \
         patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(DEFAULTS)):
        sm = hist.ChatSessionManager(history_dir=str(tmp_path / 'hist'))
        shim = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
        import core.api_fastapi as af
        monkeypatch.setattr(af, 'get_system', lambda: shim)
        assert pv.setup("changeme")[0]
        yield types.SimpleNamespace(sm=sm, vdir=vdir, events=events)
    pv.stop()
    pv._key = pv._salt = pv._data = None
    pv._refs, pv._refs_loaded = {}, False


def _make_private(sm, name):
    if name != 'default':
        sm.create_chat(name)
    sm.set_active_chat(name)
    sm.current_chat.messages.append({"role": "user", "content": f"in {name}"})
    assert sm.update_chat_settings({'private_chat': True}, expected_active=name)


def _names(sm):
    return {c['name'] for c in sm.list_chat_files(include_hidden=True)}


def test_teri_default_vaulted_comes_back_public(world):
    """The reported case: Default is vaulted, passphrase forgotten."""
    sm = world.sm
    _make_private(sm, 'default')
    sm.create_chat('pub')
    pv.lock("test")                       # sealed — the user can't unlock
    ok, code = pv.destroy()
    assert ok, code
    assert not pv.vault_exists()
    assert list(world.vdir.iterdir()) == []
    chats = {c['name']: c for c in sm.list_chat_files(include_hidden=True)}
    assert 'default' in chats and not chats['default'].get('private_chat')
    assert sm.read_chat_messages('default') == []      # the ciphertext is gone
    assert 'pub' in chats
    # and a fresh vault works: no G3 refusal from orphaned sealed rows
    assert pv.setup("again")[0]
    assert pv.chat_data_key() is not None


def test_unlocked_vault_purges_private_keeps_public(world):
    sm = world.sm
    sm.create_chat('pub')
    sm.set_active_chat('pub')
    sm.current_chat.messages.append({"role": "user", "content": "public words"})
    sm._save_current_chat()
    _make_private(sm, 'secret')            # active, vault open
    ok, code = pv.destroy()
    assert ok, code
    names = _names(sm)
    assert 'secret' not in names and 'pub' in names
    assert sm.get_active_chat_name() != 'secret'
    contents = [m.get('content') for m in sm.read_chat_messages('pub')]
    assert 'public words' in contents


def test_backrooms_lands_on_default(world):
    """Every chat private → lock parks in the backrooms; delete brings default back."""
    sm = world.sm
    _make_private(sm, 'default')
    pv.lock("test")
    assert sm.get_active_chat_name() == 'backrooms'
    assert pv.destroy()[0]
    assert sm.get_active_chat_name() == 'default'


def test_purge_failure_keeps_the_vault(world, monkeypatch):
    sm = world.sm
    _make_private(sm, 'secret')
    monkeypatch.setattr(sm, 'purge_vaulted_chats', lambda: None)
    ok, code = pv.destroy()
    assert (ok, code) == (False, 'purge_failed')
    assert pv.vault_exists()
    assert pv.unlock("changeme")[0]
    assert 'secret' in _names(sm)


def test_no_private_names_on_the_bus(world):
    sm = world.sm
    _make_private(sm, 'titanic')
    pv.lock("test")
    world.events.clear()
    assert pv.destroy()[0]
    assert 'titanic' not in repr(world.events)


def test_no_vault(world):
    pv.lock("test")
    assert pv.destroy()[0]
    assert pv.destroy() == (False, 'no_vault')


def test_route_requires_typed_words():
    from fastapi import HTTPException
    import asyncio
    from core.routes import vault as rv

    class Req:
        async def json(self):
            return {'confirm': 'yes'}
    with patch.object(rv, '_managed_guard', lambda: None), \
         patch.object(rv.prompt_vault, 'destroy') as d:
        with pytest.raises(HTTPException) as e:
            asyncio.run(rv.vault_delete(Req()))
        assert e.value.status_code == 400
        d.assert_not_called()


def test_failed_eviction_never_saves_private_rows_into_new_default(world):
    """Scout 2026-11-07: Default private, the lock-time eviction never ran
    (sealed by hand, as when no landing exists), its DECRYPTED rows still in
    the buffer. The purge recreates 'default' under the same name — the
    stale buffer must not save into the new public chat in plaintext."""
    sm = world.sm
    _make_private(sm, 'default')
    sm._save_current_chat()
    with pv._lock:
        pv._key = pv._salt = pv._data = None          # sealed, no eviction
    assert sm.get_active_chat_name() == 'default'
    assert any(m.get('content') == 'in default' for m in sm.current_chat.messages)
    assert pv.destroy()[0]
    sm._save_current_chat()                            # any later save
    assert sm.read_chat_messages('default') == []
    assert not sm.get_chat_settings().get('private_chat')
    assert sm.current_chat.messages == []
