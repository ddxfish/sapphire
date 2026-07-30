"""Google Calendar Easy Connect (relay mode, 2026-07-30).

Covers the relay-mode seams added alongside the untouched BYO path:
  - credentials: relay accounts (no local client creds) round-trip and
    count as connected; legacy accounts default to auth_mode 'byo'
  - oauth routes: empty client_id -> PKCE flow through the relay
    (/start builds challenge from a locally-kept verifier, callback
    claims with that same verifier); BYO with client_id never touches
    the relay
  - calendar tools: relay accounts refresh via {relay}/refresh, byo
    accounts still hit Google's token endpoint directly
"""
import hashlib
import importlib.util
import json
import sys
import time
from base64 import urlsafe_b64encode
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def creds_manager(tmp_path):
    """CredentialsManager on temp files (pattern from test_credentials_gcal)."""
    creds_file = tmp_path / 'credentials.json'
    salt_file = tmp_path / '.scramble_salt'
    with patch('core.credentials_manager.CREDENTIALS_FILE', creds_file), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', salt_file), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        from core.credentials_manager import CredentialsManager
        yield CredentialsManager()


def _load_by_path(rel_path, mod_name):
    """Load a plugin module by path — the hyphenated dir prevents import."""
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    project_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(mod_name, project_root / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_oauth():
    return _load_by_path('plugins/google-calendar/routes/oauth.py', 'gcal_oauth_easy_test')


def _load_calendar():
    return _load_by_path('plugins/google-calendar/tools/calendar.py', 'gcal_calendar_easy_test')


# ─── credentials: relay accounts ─────────────────────────────────────────

def test_relay_account_counts_as_connected(creds_manager):
    creds_manager.set_gcal_account('easy', '', '', 'primary', '', 'easy',
                                   auth_mode='relay', relay_url='https://relay.example')
    assert creds_manager.has_gcal_account('easy') is False  # no refresh token yet
    creds_manager.update_gcal_tokens('easy', 'rt-1', 'at-1', time.time() + 3600)
    assert creds_manager.has_gcal_account('easy') is True   # despite empty client_id

    acct = creds_manager.get_gcal_account('easy')
    assert acct['auth_mode'] == 'relay'
    assert acct['relay_url'] == 'https://relay.example'
    assert acct['client_id'] == ''


def test_legacy_account_defaults_to_byo(creds_manager):
    creds_manager.set_gcal_account('old', 'cid', 'csec', refresh_token='rt')
    acct = creds_manager.get_gcal_account('old')
    assert acct['auth_mode'] == 'byo'
    assert creds_manager.has_gcal_account('old') is True


def test_byo_without_client_id_still_not_connected(creds_manager):
    """[REGRESSION_GUARD] Relaxing has_gcal_account for relay must not let a
    credential-less byo account read as connected."""
    creds_manager.set_gcal_account('shell', '', '', refresh_token='rt')
    assert creds_manager.has_gcal_account('shell') is False


# ─── oauth routes: easy flow ─────────────────────────────────────────────

@pytest.fixture
def oauth_env(tmp_path, monkeypatch, creds_manager):
    """oauth module with CSRF file, credentials, and requests.post isolated."""
    mod = _load_oauth()
    monkeypatch.setattr(mod, '_get_csrf_path', lambda: tmp_path / 'csrf.json')
    monkeypatch.setattr('core.credentials_manager.credentials', creds_manager)
    posts = []

    def fake_post(url, json=None, data=None, timeout=None):
        posts.append({'url': url, 'json': json, 'data': data})
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith('/start'):
            resp.json.return_value = {'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth?fake=1'}
        else:
            resp.json.return_value = {'access_token': 'at-new', 'refresh_token': 'rt-new',
                                      'expires_in': 3600}
        return resp

    monkeypatch.setattr(mod.requests, 'post', fake_post)
    req = SimpleNamespace(base_url='http://192.168.0.5:8073/')
    return SimpleNamespace(mod=mod, posts=posts, req=req, csrf=tmp_path / 'csrf.json')


def test_start_auth_easy_uses_relay_with_pkce(oauth_env):
    resp = oauth_env.mod.start_auth(request=oauth_env.req, query={'scope': 'default'}, settings={})
    assert resp.status_code == 302
    assert 'accounts.google.com' in resp.headers['location']

    assert len(oauth_env.posts) == 1
    start = oauth_env.posts[0]
    assert start['url'] == 'https://oauth.sapphireblue.dev/start'
    assert start['json']['return_url'].startswith(
        'http://192.168.0.5:8073/api/plugin/google-calendar/callback?state=')

    # challenge must be S256 of the verifier persisted in the CSRF entry
    entry = list(json.loads(oauth_env.csrf.read_text()).values())[0]
    assert entry['mode'] == 'relay'
    expect = urlsafe_b64encode(
        hashlib.sha256(entry['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert start['json']['code_challenge'] == expect


def test_start_auth_byo_unchanged(oauth_env, creds_manager):
    """[REGRESSION_GUARD] A pasted client_id must keep the pre-relay flow:
    straight to Google, zero relay involvement."""
    creds_manager.set_gcal_account('default', 'cid', 'csec')
    resp = oauth_env.mod.start_auth(request=oauth_env.req, query={}, settings={})
    assert resp.status_code == 302
    assert 'client_id=cid' in resp.headers['location']
    assert oauth_env.posts == []


def test_callback_ticket_claims_and_stores(oauth_env, creds_manager):
    # seed a relay-mode CSRF entry the way start_auth writes it
    oauth_env.csrf.write_text(json.dumps({'tok1': {
        'scope': 'default', 'created': time.time(), 'mode': 'relay',
        'verifier': 'v-123', 'relay': 'https://oauth.sapphireblue.dev'}}))

    resp = oauth_env.mod.handle_callback(
        query={'state': 'tok1', 'ticket': 'tick-9'})
    assert resp.status_code == 302

    claim = oauth_env.posts[0]
    assert claim['url'] == 'https://oauth.sapphireblue.dev/claim'
    assert claim['json'] == {'ticket': 'tick-9', 'code_verifier': 'v-123'}

    acct = creds_manager.get_gcal_account('default')
    assert acct['auth_mode'] == 'relay'
    assert acct['refresh_token'] == 'rt-new'
    assert creds_manager.has_gcal_account('default') is True


def test_callback_relay_error_redirects(oauth_env):
    resp = oauth_env.mod.handle_callback(query={'relay_error': 'access_denied', 'state': 'x'})
    assert resp.status_code == 302
    assert 'gcal_error=access_denied' in resp.headers['location']


def test_disconnect_clears_relay_account(oauth_env, creds_manager):
    creds_manager.set_gcal_account('default', '', '', auth_mode='relay',
                                   relay_url='https://oauth.sapphireblue.dev')
    creds_manager.update_gcal_tokens('default', 'rt', 'at', time.time() + 3600)
    out = oauth_env.mod.disconnect(query={'scope': 'default'})
    assert out == {"status": "disconnected"}
    assert creds_manager.has_gcal_account('default') is False


# ─── calendar tools: refresh routing ─────────────────────────────────────

@pytest.fixture
def refresh_env(monkeypatch):
    cal = _load_calendar()
    monkeypatch.setattr(cal, '_get_gcal_scope', lambda: 'default')
    state = {'account': {}}
    monkeypatch.setattr(cal, '_get_gcal_creds', lambda: (state['account'], None))

    fake_creds = MagicMock()
    fake_creds.get_gcal_tokens_snapshot.return_value = {'access_token': '', 'expires_at': 0}
    fake_creds.update_gcal_tokens.return_value = True
    import core.credentials_manager as cm
    monkeypatch.setattr(cm, 'credentials', fake_creds)

    posts = []

    def fake_post(url, json=None, data=None, timeout=None):
        posts.append({'url': url, 'json': json, 'data': data})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'access_token': 'at-fresh', 'expires_in': 3600}
        return resp

    monkeypatch.setattr(cal.requests, 'post', fake_post)
    return SimpleNamespace(cal=cal, state=state, posts=posts)


def test_refresh_relay_account_hits_relay(refresh_env):
    refresh_env.state['account'] = {
        'refresh_token': 'rt-1', 'client_id': '', 'client_secret': '',
        'calendar_id': 'primary', 'auth_mode': 'relay',
        'relay_url': 'https://oauth.sapphireblue.dev'}
    token, cal_id, err = refresh_env.cal._get_access_token()
    assert err is None and token == 'at-fresh'
    assert refresh_env.posts[0]['url'] == 'https://oauth.sapphireblue.dev/refresh'
    assert refresh_env.posts[0]['json'] == {'refresh_token': 'rt-1'}


def test_refresh_byo_account_hits_google(refresh_env):
    """[REGRESSION_GUARD] BYO refresh must keep going straight to Google
    with the account's own client creds."""
    refresh_env.state['account'] = {
        'refresh_token': 'rt-2', 'client_id': 'cid', 'client_secret': 'csec',
        'calendar_id': 'primary', 'auth_mode': 'byo', 'relay_url': ''}
    token, cal_id, err = refresh_env.cal._get_access_token()
    assert err is None and token == 'at-fresh'
    assert refresh_env.posts[0]['url'] == refresh_env.cal.GOOGLE_TOKEN_URL
    assert refresh_env.posts[0]['data']['client_id'] == 'cid'


def test_event_id_map_survives_across_tool_calls(refresh_env):
    """[REGRESSION_GUARD] The display-number map must outlive the listing's
    execution context — the old ContextVar storage died between tool calls,
    so calendar_delete sent raw '#3' to Google and 404'd (live, 2026-07-30)."""
    cal = refresh_env.cal
    events = [
        {'id': 'gid-aaa', 'summary': 'one',
         'start': {'date': '2026-07-30'}, 'end': {'date': '2026-07-31'}},
        {'id': 'gid-bbb', 'summary': 'two',
         'start': {'date': '2026-07-30'}, 'end': {'date': '2026-07-31'}},
    ]
    cal._format_events(events, 'Today')  # simulates calendar_today's render

    # Resolution happens in a later, separate call — same module, new context
    assert cal._resolve_event_id('2') == ('gid-bbb', None)
    assert cal._resolve_event_id('#1') == ('gid-aaa', None)


def test_resolve_unknown_number_errors_instead_of_google_404(refresh_env):
    refresh_env.cal._id_maps.clear()
    event_id, err = refresh_env.cal._resolve_event_id('7')
    assert event_id is None
    assert 'calendar_today' in err  # guides a re-list instead of a raw 404


def test_resolve_real_google_id_passes_through(refresh_env):
    event_id, err = refresh_env.cal._resolve_event_id('abc123def456ghi')
    assert (event_id, err) == ('abc123def456ghi', None)


def test_refresh_relay_account_without_creds_no_error(refresh_env):
    """Relay accounts must not trip the 'Client ID/Secret not configured'
    guard — they have no local creds by design."""
    refresh_env.state['account'] = {
        'refresh_token': 'rt-3', 'client_id': '', 'client_secret': '',
        'calendar_id': 'primary', 'auth_mode': 'relay',
        'relay_url': 'https://oauth.sapphireblue.dev'}
    _, _, err = refresh_env.cal._get_access_token()
    assert err is None
