"""Surface 2 P0 — /api/settings and /api/settings/batch route guards.

HTTP-level coverage for `core/routes/settings.py`:
  - GET  /api/settings                — masks sensitive keys
  - PUT  /api/settings/batch          — skips masked values, routes service API keys
                                         to credentials manager, filters locked keys
                                         in managed mode, dedupes STT_ENABLED+STT_PROVIDER

Each test uses the real settings_manager + credentials_manager but operates on
a tmp_path-backed settings.json via monkeypatch. No secrets written to disk.

See tmp/coverage-test-plan.md Surface 2.
"""
import json
from unittest.mock import MagicMock

import pytest


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def settings_client(client, mock_system, monkeypatch):
    """TestClient + settings_manager with disk writes stubbed.

    Uses the existing `settings` singleton (it already has sane defaults
    loaded) but neuters `.save()` so tests can't corrupt the real
    settings.json. `set(key, value, persist=False)` drops values into the
    in-memory config without touching disk. Teardown: monkeypatch restores.

    Credentials manager is mocked so service API keys don't hit real files.
    Provider-switch side effects on mock_system are stubbed to MagicMocks.
    """
    c, csrf = client

    from core import settings_manager as sm_mod
    # Disk-write guard — no test should persist anything
    monkeypatch.setattr(sm_mod.settings, 'save', lambda: True)
    # Snapshot + restore to prevent cross-test pollution of planted values
    orig_user = dict(sm_mod.settings._user)
    orig_runtime = dict(sm_mod.settings._runtime)
    orig_config = dict(sm_mod.settings._config)

    from core import credentials_manager as cred_mod
    fresh_cred = MagicMock()
    fresh_cred.set_service_api_key = MagicMock(return_value=True)
    monkeypatch.setattr(cred_mod, 'credentials', fresh_cred)

    for attr in ('toggle_wakeword', 'switch_stt_provider', 'toggle_stt',
                 'switch_tts_provider', 'toggle_tts', 'switch_embedding_provider'):
        setattr(mock_system, attr, MagicMock(return_value=None))

    mock_system.llm_chat.session_manager.get_chat_settings.return_value = {}

    # The switch job (core/provider_switch.py, 2026-10-22) warms Silero on STT
    # none→X and warms the embedder on an embedding switch: never in tests.
    from core import provider_switch as _ps
    monkeypatch.setattr(_ps, '_warm_vad', lambda: None)
    monkeypatch.setattr(_ps, '_embedding_ready', lambda system, value: (True, ''))

    try:
        yield c, csrf, sm_mod.settings, fresh_cred, mock_system
    finally:
        with sm_mod.settings._lock:
            sm_mod.settings._user = orig_user
            sm_mod.settings._runtime = orig_runtime
            sm_mod.settings._config = orig_config


# ─── 2.10 GET /api/settings masks sensitive keys ─────────────────────────────

def test_get_settings_masks_sensitive_keys(settings_client):
    """[REGRESSION_GUARD] Sensitive-suffixed keys (_API_KEY, _SECRET, _PASSWORD,
    _TOKEN) and explicit sensitive keys (SAPPHIRE_ROUTER_URL) must be returned
    as bullets, never as plaintext."""
    c, csrf, settings, cred, sys_mock = settings_client

    # Plant real values
    settings.set('ANTHROPIC_API_KEY', 'sk-real-key-1234', persist=False)
    settings.set('SOMETHING_SECRET', 'super-secret-value', persist=False)
    settings.set('SOMETHING_TOKEN', 'tok-abc-123', persist=False)
    settings.set('SAPPHIRE_ROUTER_URL', 'https://router.example/tenant', persist=False)
    settings.set('SOME_REGULAR_KEY', 'visible', persist=False)

    r = c.get('/api/settings')
    assert r.status_code == 200, r.text
    body = r.json()
    s = body['settings']

    # Sensitive: bulleted
    assert s['ANTHROPIC_API_KEY'] == '••••••••'
    assert s['SOMETHING_SECRET'] == '••••••••'
    assert s['SOMETHING_TOKEN'] == '••••••••'
    assert s['SAPPHIRE_ROUTER_URL'] == '••••••••'

    # Non-sensitive: visible
    assert s['SOME_REGULAR_KEY'] == 'visible'


def test_get_settings_empty_sensitive_stays_empty_not_bulleted(settings_client):
    """[PROACTIVE] An unset sensitive key returns '' (or falsy), NOT bullets.
    Otherwise the frontend can't tell 'set to empty' from 'set to a secret'."""
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('ANTHROPIC_API_KEY', '', persist=False)
    r = c.get('/api/settings')
    assert r.status_code == 200
    s = r.json()['settings']
    assert s['ANTHROPIC_API_KEY'] == ''  # or falsy, definitely not bullets


# ─── 2.6 Batch PUT skips masked values (preserves real secrets) ──────────────

def test_batch_skips_masked_values_preserves_real_secret(settings_client):
    """[REGRESSION_GUARD] When the frontend sends back the masked '••••••••'
    (e.g. from a modal where the user didn't touch the field), the backend
    must NOT overwrite the real secret with dots.
    """
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('ANTHROPIC_API_KEY', 'sk-real-preserved', persist=False)

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {'ANTHROPIC_API_KEY': '••••••••'}},
    )
    assert r.status_code == 200, r.text
    assert settings.get('ANTHROPIC_API_KEY') == 'sk-real-preserved'


def test_batch_accepts_new_secret_replacing_old(settings_client):
    """Inverse: a real (non-bullet) value MUST overwrite."""
    c, csrf, settings, cred, sys_mock = settings_client
    settings.set('ANTHROPIC_API_KEY', 'sk-old', persist=False)

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {'ANTHROPIC_API_KEY': 'sk-new-value'}},
    )
    assert r.status_code == 200
    assert settings.get('ANTHROPIC_API_KEY') == 'sk-new-value'


# ─── 2.11 Service API keys route to credentials manager, not settings.json ───

def test_batch_service_api_keys_route_to_credentials_not_settings(settings_client):
    """[REGRESSION_GUARD] STT_FIREWORKS_API_KEY / TTS_ELEVENLABS_API_KEY /
    EMBEDDING_API_KEY must land in credentials_manager, not settings.json
    (settings.json is checked into dev repos; credentials is gitignored)."""
    c, csrf, settings, cred, sys_mock = settings_client

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {
            'STT_FIREWORKS_API_KEY': 'fw-secret',
            'TTS_ELEVENLABS_API_KEY': 'eleven-secret',
            'EMBEDDING_API_KEY': 'embed-secret',
        }},
    )
    assert r.status_code == 200, r.text

    # All three routed to credentials, NOT settings
    calls = {call.args[0]: call.args[1] for call in cred.set_service_api_key.call_args_list}
    assert calls == {
        'stt_fireworks': 'fw-secret',
        'tts_elevenlabs': 'eleven-secret',
        'embedding': 'embed-secret',
    }
    # Settings.json was NOT populated with the raw keys
    assert settings.get('STT_FIREWORKS_API_KEY', None) in (None, '')
    assert settings.get('TTS_ELEVENLABS_API_KEY', None) in (None, '')
    assert settings.get('EMBEDDING_API_KEY', None) in (None, '')


# ─── 2.8 STT_ENABLED dedup when STT_PROVIDER in same batch ───────────────────

def test_batch_stt_enabled_skipped_when_stt_provider_in_same_batch(settings_client):
    """[REGRESSION_GUARD] If a batch contains both STT_PROVIDER and STT_ENABLED,
    the STT_PROVIDER switch handles the runtime state — STT_ENABLED should NOT
    also fire a second switch (would double-init, cause provider-in-progress race).
    The switch is a job since 2026-10-22 (core/provider_switch.py): wait for it.
    """
    from core import provider_switch
    c, csrf, settings, cred, sys_mock = settings_client

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {
            'STT_PROVIDER': 'faster_whisper',
            'STT_ENABLED': True,
        }},
    )
    assert r.status_code == 200
    provider_switch.wait(timeout=10)

    # switch_stt_provider called once (the STT_PROVIDER job)
    assert sys_mock.switch_stt_provider.call_count == 1
    # the legacy toggle never runs — STT_ENABLED in same batch as STT_PROVIDER
    assert sys_mock.toggle_stt.call_count == 0
    statuses = {x['key']: x['status'] for x in r.json()['results']}
    assert statuses == {'STT_PROVIDER': 'started', 'STT_ENABLED': 'skipped'}


def test_batch_stt_enabled_alone_fires_toggle_stt(settings_client):
    """Inverse: STT_ENABLED without STT_PROVIDER → the provider job runs (the legacy
    toggle_stt mapped onto its provider key, the local default when none)."""
    from core import provider_switch
    c, csrf, settings, cred, sys_mock = settings_client

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {'STT_ENABLED': True}},
    )
    assert r.status_code == 200
    provider_switch.wait(timeout=10)
    assert sys_mock.toggle_stt.call_count == 0
    assert sys_mock.switch_stt_provider.call_count == 1
    assert sys_mock.switch_stt_provider.call_args[0][0] != 'none'


# ─── 2.9 Managed mode filters locked keys ────────────────────────────────────

def test_batch_locked_keys_silently_filtered_in_managed_mode(settings_client, monkeypatch):
    """[REGRESSION_GUARD] In managed mode, locked keys must be silently dropped
    from a batch write — not rejected with an error that leaks which keys are
    locked (info the user shouldn't need)."""
    c, csrf, settings, cred, sys_mock = settings_client

    # Plant locked-key value BEFORE enabling managed mode (settings.set is
    # itself blocked under managed mode for locked keys).
    settings.set('ANTHROPIC_API_KEY', 'sk-managed-locked', persist=False)

    monkeypatch.setattr(settings, 'is_managed', lambda: True)
    locked_set = {'ANTHROPIC_API_KEY'}
    monkeypatch.setattr(settings, 'is_locked', lambda k: k in locked_set)

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {
            'ANTHROPIC_API_KEY': 'sk-attempted-override',
            'SOME_REGULAR_KEY': 'wrote-ok',
        }},
    )
    assert r.status_code == 200

    # Locked key unchanged
    assert settings.get('ANTHROPIC_API_KEY') == 'sk-managed-locked'
    # Non-locked key did land
    assert settings.get('SOME_REGULAR_KEY') == 'wrote-ok'


# ─── 2.7 TTS provider switch re-applies chat settings ────────────────────────

def test_batch_tts_provider_switch_reapplies_chat_settings(settings_client):
    """[REGRESSION_GUARD] After TTS_PROVIDER switch, voice/pitch/speed need to
    be re-validated for the new provider — _apply_chat_settings is invoked on
    the active chat. Guards that the re-apply path isn't silently dropped."""
    from core import provider_switch
    c, csrf, settings, cred, sys_mock = settings_client

    called = {'count': 0}
    sys_mock.llm_chat.session_manager.get_chat_settings.side_effect = \
        lambda: (called.__setitem__('count', called['count'] + 1) or {'voice': 'test'})

    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={'settings': {'TTS_PROVIDER': 'kokoro'}},
    )
    assert r.status_code == 200
    provider_switch.wait(timeout=10)
    # Chat settings re-read at least once for the TTS re-apply
    assert called['count'] >= 1, "re-apply of chat settings didn't fire on TTS switch"
    assert sys_mock.switch_tts_provider.call_count == 1


# ─── Batch 400 on missing 'settings' key ─────────────────────────────────────

def test_batch_missing_settings_key_returns_400(settings_client):
    c, csrf, settings, cred, sys_mock = settings_client
    r = c.put(
        '/api/settings/batch',
        headers={'X-CSRF-Token': csrf},
        json={},
    )
    assert r.status_code == 400
