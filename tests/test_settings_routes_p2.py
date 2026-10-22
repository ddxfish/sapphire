"""Surface 2 P2 — settings single-key CRUD + credentials route coverage.

Covers single-PUT /api/settings/{key} (vs. batch PUT covered in P0):
  - Bullet value short-circuits (same secret-preserve as batch)
  - Locked keys return 403 (single-PUT path, not batch-filter)
  - SOCKS keys trigger clear_session_cache
  - delete_setting restores default

Plus credentials CRUD (set/delete LLM + SOCKS creds).

See tmp/coverage-test-plan.md Surface 2 stubs 2.41-2.49.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def settings_client(client, mock_system, monkeypatch):
    """TestClient + settings_manager with disk-write stubbed + snapshot/restore.

    Same setup as test_settings_routes_p0's fixture but tailored for
    single-key routes: get_system().toggle_* / switch_* stubs.

    Disk-safety note: stubbing `settings.save` only protects the `set()` write
    path. Other production paths bypass save() and write the user settings
    file directly — `_remove_key_from_file()` (DELETE handler), `_migrate_
    providers()`, and `reset_to_defaults()`. Tests in this module hit DELETE
    /api/settings/{key}, so we additionally snapshot the actual file bytes
    on entry and restore them on teardown. Without this, every pytest run
    silently nuked the developer's `DEFAULT_USERNAME` override on disk —
    name reverted to "Human Protagonist" on next Sapphire restart. The
    fixture's existing in-memory `_user`/`_runtime`/`_config` snapshot
    only covered the singleton state, not the file. 2026-05-11.
    """
    c, csrf = client

    from core import settings_manager as sm_mod
    monkeypatch.setattr(sm_mod.settings, 'save', lambda: True)
    orig_user = dict(sm_mod.settings._user)
    orig_runtime = dict(sm_mod.settings._runtime)
    orig_config = dict(sm_mod.settings._config)

    # Snapshot the live user/settings.json bytes before any test touches it.
    # Routes that call _remove_key_from_file() / reset_to_defaults() bypass
    # the save() stub and write to disk directly.
    user_settings_path = sm_mod.settings.BASE_DIR / 'user' / 'settings.json'
    file_snapshot = user_settings_path.read_bytes() if user_settings_path.exists() else None

    from core import credentials_manager as cred_mod
    fresh_cred = MagicMock()
    fresh_cred.set_llm_api_key = MagicMock(return_value=True)
    fresh_cred.clear_llm_api_key = MagicMock(return_value=True)
    fresh_cred.has_socks_credentials = MagicMock(return_value=False)
    fresh_cred.set_socks_credentials = MagicMock(return_value=True)
    fresh_cred.set_service_api_key = MagicMock(return_value=True)
    fresh_cred.get_masked_summary = MagicMock(return_value={'llm': {}, 'socks': False})
    monkeypatch.setattr(cred_mod, 'credentials', fresh_cred)

    for attr in ('toggle_wakeword', 'switch_stt_provider', 'toggle_stt',
                 'switch_tts_provider', 'toggle_tts', 'switch_embedding_provider'):
        setattr(mock_system, attr, MagicMock(return_value=None))

    # The switch job (core/provider_switch.py, 2026-10-22) warms Silero on STT
    # none→X and warms the embedder on an embedding switch: never in tests.
    from core import provider_switch as _ps
    monkeypatch.setattr(_ps, '_warm_vad', lambda: None)
    monkeypatch.setattr(_ps, '_embedding_ready', lambda system, value: (True, ''))

    mock_system.llm_chat.session_manager.get_chat_settings.return_value = {}

    try:
        yield c, csrf, sm_mod.settings, fresh_cred, mock_system
    finally:
        # Restore in-memory singleton state
        with sm_mod.settings._lock:
            sm_mod.settings._user = orig_user
            sm_mod.settings._runtime = orig_runtime
            sm_mod.settings._config = orig_config
        # Restore the file too — tests may have written via the bypass paths
        # above. If the file didn't exist at entry and a test created it,
        # delete it to leave the filesystem in its original state.
        if file_snapshot is not None:
            user_settings_path.write_bytes(file_snapshot)
        elif user_settings_path.exists():
            user_settings_path.unlink()


# ─── GET /api/settings/{key} ─────────────────────────────────────────────────

def test_get_single_setting_masks_sensitive(settings_client):
    """[REGRESSION_GUARD] GET /api/settings/{key} for a sensitive key returns
    bullets, same as the bulk endpoint."""
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('ANTHROPIC_API_KEY', 'sk-real', persist=False)
    r = c.get('/api/settings/ANTHROPIC_API_KEY')
    assert r.status_code == 200
    assert r.json()['value'] == '••••••••'


def test_get_single_setting_unknown_returns_404(settings_client):
    c, csrf, settings, cred, sys_mock = settings_client
    r = c.get('/api/settings/DEFINITELY_NOT_A_REAL_KEY_XYZ')
    assert r.status_code == 404


def test_get_single_setting_user_override_flag(settings_client):
    """Response exposes user_override=True when the key was user-set."""
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('DEFAULT_USERNAME', 'TestOverride', persist=True)
    r = c.get('/api/settings/DEFAULT_USERNAME')
    assert r.status_code == 200
    body = r.json()
    assert body['value'] == 'TestOverride'
    # Should be marked as override since we used persist=True
    assert body['user_override'] is True


# ─── PUT /api/settings/{key} ─────────────────────────────────────────────────

def test_put_single_setting_bullet_value_short_circuits(settings_client):
    """[REGRESSION_GUARD] Sending '••••••••' as a value must NOT overwrite
    the real secret. Response still 200 but persisted=False."""
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('ANTHROPIC_API_KEY', 'sk-real-keep-me', persist=False)

    r = c.put(
        '/api/settings/ANTHROPIC_API_KEY',
        headers={'X-CSRF-Token': csrf},
        json={'value': '••••••••'},
    )
    assert r.status_code == 200
    assert r.json()['persisted'] is False
    assert settings.get('ANTHROPIC_API_KEY') == 'sk-real-keep-me'


def test_put_single_setting_locked_key_returns_403(settings_client, monkeypatch):
    """[REGRESSION_GUARD] Locked key in managed mode returns 403 via single-
    PUT (not just filtered like batch)."""
    c, csrf, settings, cred, sys_mock = settings_client
    monkeypatch.setattr(settings, 'is_locked',
                        lambda k: k == 'ANTHROPIC_API_KEY')

    r = c.put(
        '/api/settings/ANTHROPIC_API_KEY',
        headers={'X-CSRF-Token': csrf},
        json={'value': 'sk-attempted'},
    )
    assert r.status_code == 403


def test_put_single_setting_missing_value_returns_400(settings_client):
    c, csrf, settings, cred, sys_mock = settings_client
    r = c.put(
        '/api/settings/DEFAULT_USERNAME',
        headers={'X-CSRF-Token': csrf},
        json={},
    )
    assert r.status_code == 400


def test_put_single_setting_socks_key_clears_session_cache(settings_client, monkeypatch):
    """[PROACTIVE] SOCKS_ENABLED / SOCKS_HOST / SOCKS_PORT / SOCKS_TIMEOUT
    changes must invalidate the cached requests session — otherwise the old
    proxy config keeps being used until next restart."""
    from core import socks_proxy
    c, csrf, settings, cred, sys_mock = settings_client
    clear_mock = MagicMock()
    monkeypatch.setattr(socks_proxy, 'clear_session_cache', clear_mock)

    r = c.put(
        '/api/settings/SOCKS_ENABLED',
        headers={'X-CSRF-Token': csrf},
        json={'value': True},
    )
    assert r.status_code == 200
    clear_mock.assert_called_once()


def test_put_single_setting_non_socks_key_does_not_clear_session_cache(settings_client, monkeypatch):
    """Inverse: non-SOCKS key doesn't clear session cache."""
    from core import socks_proxy
    c, csrf, settings, cred, sys_mock = settings_client
    clear_mock = MagicMock()
    monkeypatch.setattr(socks_proxy, 'clear_session_cache', clear_mock)

    r = c.put(
        '/api/settings/DEFAULT_USERNAME',
        headers={'X-CSRF-Token': csrf},
        json={'value': 'Newname'},
    )
    assert r.status_code == 200
    clear_mock.assert_not_called()


def test_put_single_setting_wake_word_enabled_triggers_toggle(settings_client):
    """WAKE_WORD_ENABLED must invoke system.toggle_wakeword (not just
    persist the value). A job since 2026-10-22: the PUT returns 'started'."""
    from core import provider_switch
    c, csrf, settings, cred, sys_mock = settings_client

    r = c.put(
        '/api/settings/WAKE_WORD_ENABLED',
        headers={'X-CSRF-Token': csrf},
        json={'value': True},
    )
    assert r.status_code == 200
    assert r.json()['status'] == 'started' and r.json()['job']
    provider_switch.wait(timeout=10)
    sys_mock.toggle_wakeword.assert_called_once_with(True)


def test_put_single_setting_tts_provider_triggers_switch(settings_client):
    """TTS_PROVIDER must call switch_tts_provider, in its job."""
    from core import provider_switch
    c, csrf, settings, cred, sys_mock = settings_client

    r = c.put(
        '/api/settings/TTS_PROVIDER',
        headers={'X-CSRF-Token': csrf},
        json={'value': 'kokoro'},
    )
    assert r.status_code == 200
    provider_switch.wait(timeout=10)
    sys_mock.switch_tts_provider.assert_called_once_with('kokoro')


def test_put_single_setting_publishes_settings_changed(settings_client, event_bus_capture):
    """Every successful single-PUT fires SETTINGS_CHANGED with key/value/tier."""
    c, csrf, settings, cred, sys_mock = settings_client

    r = c.put(
        '/api/settings/DEFAULT_USERNAME',
        headers={'X-CSRF-Token': csrf},
        json={'value': 'EventUser'},
    )
    assert r.status_code == 200
    events = [d for ev, d in event_bus_capture.events if ev == 'settings_changed']
    assert events, "SETTINGS_CHANGED not fired"
    last = events[-1]
    assert last['key'] == 'DEFAULT_USERNAME'
    assert last['value'] == 'EventUser'


# ─── DELETE /api/settings/{key} ──────────────────────────────────────────────

def test_delete_single_setting_restores_default(settings_client):
    """[PROACTIVE] DELETE /api/settings/{key} removes user override and
    returns the default value that's now active."""
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('DEFAULT_USERNAME', 'Override', persist=True)

    r = c.request(
        'DELETE', '/api/settings/DEFAULT_USERNAME',
        headers={'X-CSRF-Token': csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'success'
    assert 'reverted_to' in body
    # User override is gone
    assert 'DEFAULT_USERNAME' not in settings.get_user_overrides()


def test_delete_single_setting_unknown_override_returns_404(settings_client):
    c, csrf, settings, cred, sys_mock = settings_client
    r = c.request(
        'DELETE', '/api/settings/SOME_RANDOM_UNSET_KEY',
        headers={'X-CSRF-Token': csrf},
    )
    assert r.status_code == 404


def test_delete_single_setting_locked_returns_403(settings_client, monkeypatch):
    c, csrf, settings, cred, sys_mock = settings_client
    monkeypatch.setattr(settings, 'is_locked',
                        lambda k: k == 'ANTHROPIC_API_KEY')
    r = c.request(
        'DELETE', '/api/settings/ANTHROPIC_API_KEY',
        headers={'X-CSRF-Token': csrf},
    )
    assert r.status_code == 403


# ─── Credentials routes ──────────────────────────────────────────────────────

def test_get_credentials_returns_masked_summary(settings_client):
    """GET /api/credentials returns masked summary — never actual keys."""
    c, csrf, settings, cred, sys_mock = settings_client
    cred.get_masked_summary.return_value = {
        'llm': {'anthropic': True, 'openai': False},
        'socks': False,
    }
    r = c.get('/api/credentials')
    assert r.status_code == 200
    body = r.json()
    # Must be masked shape, not raw keys
    assert 'llm' in body
    # Masked summary never includes raw keys
    for provider, status in body['llm'].items():
        assert isinstance(status, bool), f"provider {provider} leaked non-bool: {status!r}"


def test_put_llm_credential_routes_to_credentials_manager(settings_client):
    """[REGRESSION_GUARD] PUT /api/credentials/llm/{provider} must land in
    credentials_manager, not in settings.json."""
    c, csrf, settings, cred, sys_mock = settings_client
    r = c.put(
        '/api/credentials/llm/anthropic',
        headers={'X-CSRF-Token': csrf},
        json={'api_key': 'sk-fresh-key'},
    )
    assert r.status_code == 200
    cred.set_llm_api_key.assert_called_once_with('anthropic', 'sk-fresh-key')


def test_delete_llm_credential_success(settings_client):
    c, csrf, settings, cred, sys_mock = settings_client
    r = c.request(
        'DELETE', '/api/credentials/llm/anthropic',
        headers={'X-CSRF-Token': csrf},
    )
    assert r.status_code == 200
    cred.clear_llm_api_key.assert_called_once_with('anthropic')


def test_delete_llm_credential_missing_returns_404(settings_client):
    c, csrf, settings, cred, sys_mock = settings_client
    cred.clear_llm_api_key.return_value = False
    r = c.request(
        'DELETE', '/api/credentials/llm/nonexistent',
        headers={'X-CSRF-Token': csrf},
    )
    assert r.status_code == 404


# ─── Settings reload + reset ─────────────────────────────────────────────────

def test_settings_reload_invokes_manager_reload(settings_client, monkeypatch):
    c, csrf, settings, cred, sys_mock = settings_client
    reload_mock = MagicMock()
    monkeypatch.setattr(settings, 'reload', reload_mock)
    r = c.post('/api/settings/reload', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    reload_mock.assert_called_once()


def test_settings_reset_reinits_providers_to_none(settings_client, monkeypatch):
    """[PROACTIVE] POST /api/settings/reset must tear down all provider state
    back to 'none' — otherwise stale providers keep handling traffic with
    zeroed-out config until next restart."""
    c, csrf, settings, cred, sys_mock = settings_client
    monkeypatch.setattr(settings, 'reset_to_defaults', lambda: True)
    r = c.post('/api/settings/reset', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    sys_mock.toggle_wakeword.assert_called_once_with(False)
    sys_mock.switch_tts_provider.assert_called_once_with('none')
    sys_mock.switch_stt_provider.assert_called_once_with('none')
