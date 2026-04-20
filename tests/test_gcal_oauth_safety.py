"""Google Calendar OAuth safety fixes (2026-04-19).

Covers:
  #1 refresh_token rotation is preserved on renewal (was dropped in favor of
      the old stored value — next refresh would fail invalid_grant)
  #2 Per-scope refresh lock serializes the check/POST/save dance
  #3 401 on API call forces one refresh + retry before giving up
  #4 Read path uses get_gcal_tokens_snapshot (locked) instead of raw dict
  #5 Error discrimination: invalid_grant → reconnect, 5xx → transient,
      other → generic "try reconnecting"
"""
import importlib.util
import inspect
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_gcal_calendar():
    """Load plugins/google-calendar/tools/calendar.py by path — the hyphen
    in the dir name prevents a normal import."""
    if 'gcal_calendar_test' in sys.modules:
        return sys.modules['gcal_calendar_test']
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / 'plugins' / 'google-calendar' / 'tools' / 'calendar.py'
    spec = importlib.util.spec_from_file_location('gcal_calendar_test', module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['gcal_calendar_test'] = mod
    spec.loader.exec_module(mod)
    return mod


# ─── #4 snapshot helper exists & is locked ───────────────────────────────

def test_credentials_has_gcal_tokens_snapshot():
    """[REGRESSION_GUARD] credentials_manager exposes a locked snapshot of
    the short-lived gcal access_token + expires_at. The refresh path reads
    through this instead of touching _credentials directly, so reads can't
    see intermediate state from a concurrent write."""
    from core.credentials_manager import credentials
    assert hasattr(credentials, 'get_gcal_tokens_snapshot')
    src = inspect.getsource(credentials.get_gcal_tokens_snapshot)
    assert 'self._lock' in src, "snapshot reader must hold credentials._lock"


# ─── calendar.py refactor ────────────────────────────────────────────────

@pytest.fixture
def mock_gcal(monkeypatch):
    """Patch the gcal module's auth dependencies so we can drive refresh
    behavior deterministically. Returns the calendar module + a Mock that
    represents the credentials store."""
    cal = _load_gcal_calendar()

    # Fake credentials store with tokens_snapshot + get_gcal_account.
    state = {
        'access_token': '',
        'expires_at': 0,
        'refresh_token': 'rt-current',
        'client_id': 'cid',
        'client_secret': 'csec',
        'calendar_id': 'primary',
    }

    fake_creds = MagicMock()
    fake_creds.get_gcal_tokens_snapshot = MagicMock(
        side_effect=lambda scope: {
            'access_token': state['access_token'],
            'expires_at': state['expires_at'],
        }
    )
    fake_creds.get_gcal_account = MagicMock(
        side_effect=lambda scope: {
            'refresh_token': state['refresh_token'],
            'client_id': state['client_id'],
            'client_secret': state['client_secret'],
            'calendar_id': state['calendar_id'],
            'label': scope,
        }
    )

    def _update_tokens(scope, refresh_token, access_token, expires_at):
        if refresh_token:
            state['refresh_token'] = refresh_token
        state['access_token'] = access_token
        state['expires_at'] = expires_at
        return True
    fake_creds.update_gcal_tokens = MagicMock(side_effect=_update_tokens)

    monkeypatch.setattr('core.credentials_manager.credentials', fake_creds)
    monkeypatch.setattr(cal, '_get_gcal_scope', lambda: 'testscope')
    # Clear per-test: each test gets a fresh lock dict
    cal._refresh_locks.clear()
    return cal, state


# ─── #1 rotation preserved ───────────────────────────────────────────────

def test_refresh_saves_rotated_refresh_token(mock_gcal):
    """[REGRESSION_GUARD] If Google returns a new refresh_token, the new one
    must be saved — not the old one. Scout #1. Before this fix, renewal
    always wrote the old refresh_token back, so a rotation event killed
    auth permanently."""
    cal, state = mock_gcal

    def fake_post(url, data=None, timeout=None):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            'access_token': 'new-access',
            'expires_in': 3600,
            'refresh_token': 'rt-ROTATED',  # Google rotated
        }
        return r

    with patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        token, cid, err = cal._get_access_token()
    assert err is None
    assert token == 'new-access'
    assert state['refresh_token'] == 'rt-ROTATED', \
        "rotated refresh_token must be persisted; old value was kept"


def test_refresh_keeps_original_when_no_rotation(mock_gcal):
    """Inverse: when Google doesn't rotate, we keep the existing token."""
    cal, state = mock_gcal

    def fake_post(url, data=None, timeout=None):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {'access_token': 'new-access', 'expires_in': 3600}
        return r

    with patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        cal._get_access_token()
    assert state['refresh_token'] == 'rt-current'


# ─── #2 concurrent refresh serialization ─────────────────────────────────

def test_concurrent_refreshes_serialize(mock_gcal):
    """[REGRESSION_GUARD] Two threads both seeing an expired token must
    result in ONE POST to Google, not two. The second thread blocks on the
    per-scope lock, re-reads the cache after acquiring, and returns the
    fresh token. Scout #2."""
    cal, state = mock_gcal

    post_count = {'n': 0}
    release = threading.Event()

    def fake_post(url, data=None, timeout=None):
        post_count['n'] += 1
        release.wait(timeout=2)  # hold the first call so the second queues
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {'access_token': 'fresh', 'expires_in': 3600}
        return r

    tokens = []
    def _worker():
        with patch('gcal_calendar_test.requests.post', side_effect=fake_post):
            t, _, _ = cal._get_access_token()
            tokens.append(t)

    t1 = threading.Thread(target=_worker, daemon=True)
    t2 = threading.Thread(target=_worker, daemon=True)
    t1.start()
    time.sleep(0.05)
    t2.start()
    release.set()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert post_count['n'] == 1, f"expected 1 POST (serialized), got {post_count['n']}"
    assert tokens == ['fresh', 'fresh'], f"both threads should get fresh token; got {tokens}"


# ─── #5 error discrimination ─────────────────────────────────────────────

def test_invalid_grant_surfaces_reconnect_message(mock_gcal):
    """[REGRESSION_GUARD] When Google returns error='invalid_grant', the
    message must tell the user to reconnect (the refresh_token is dead).
    Generic 'Token refresh failed' was the old message — too vague."""
    cal, _ = mock_gcal

    def fake_post(url, data=None, timeout=None):
        r = MagicMock()
        r.status_code = 400
        r.json.return_value = {'error': 'invalid_grant'}
        r.text = '{"error":"invalid_grant"}'
        return r

    with patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        _, _, err = cal._get_access_token()
    assert err is not None
    assert 'invalid_grant' in err
    assert 'Disconnect and reconnect' in err


def test_500_surfaces_transient_message(mock_gcal):
    """5xx from Google is transient — user should try again, not reconnect."""
    cal, _ = mock_gcal

    def fake_post(url, data=None, timeout=None):
        r = MagicMock()
        r.status_code = 503
        r.json.return_value = {}
        r.text = 'Service Unavailable'
        return r

    with patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        _, _, err = cal._get_access_token()
    assert err is not None
    assert 'temporarily unavailable' in err.lower() or 'try again' in err.lower()


def test_network_error_surfaces_network_message(mock_gcal):
    """Connection error must be distinguishable from Google-returned error."""
    cal, _ = mock_gcal
    import requests as _req

    def fake_post(url, data=None, timeout=None):
        raise _req.ConnectionError("DNS failed")

    with patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        _, _, err = cal._get_access_token()
    assert err is not None
    assert 'reach Google' in err or 'network' in err.lower() or 'ConnectionError' in err


# ─── #3 401 retry ────────────────────────────────────────────────────────

def test_api_call_retries_once_on_401(mock_gcal):
    """[REGRESSION_GUARD] 401 from a GCal API endpoint triggers one forced
    refresh + retry. Only on the SECOND 401 does the user get 'reconnect'.
    Before this, a clock-skew 401 was always a reconnect prompt."""
    cal, state = mock_gcal
    state['access_token'] = 'stale'
    state['expires_at'] = time.time() + 10_000  # cached/valid expiry

    request_calls = []
    def fake_request(method, url, **kwargs):
        request_calls.append(url)
        r = MagicMock()
        if len(request_calls) == 1:
            r.status_code = 401
            r.text = 'Unauthorized'
        else:
            r.status_code = 200
            r.json.return_value = {'items': []}
        return r

    def fake_post(url, data=None, timeout=None):
        # forced refresh after the 401
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {'access_token': 'fresh', 'expires_in': 3600}
        return r

    with patch('gcal_calendar_test.requests.request', side_effect=fake_request), \
         patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        data, err = cal._api_get('/calendars/{calendar_id}/events')

    assert err is None, f"retry should have succeeded; got err={err}"
    assert len(request_calls) == 2, f"expected 2 API attempts (401 + retry), got {len(request_calls)}"
    assert data == {'items': []}


def test_api_call_gives_up_after_second_401(mock_gcal):
    """If the retry also 401s, surface reconnect message (not a third try)."""
    cal, state = mock_gcal
    state['access_token'] = 'stale'
    state['expires_at'] = time.time() + 10_000

    request_calls = []
    def fake_request(method, url, **kwargs):
        request_calls.append(url)
        r = MagicMock()
        r.status_code = 401
        r.text = 'Unauthorized'
        return r

    def fake_post(url, data=None, timeout=None):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {'access_token': 'fresh', 'expires_in': 3600}
        return r

    with patch('gcal_calendar_test.requests.request', side_effect=fake_request), \
         patch('gcal_calendar_test.requests.post', side_effect=fake_post):
        data, err = cal._api_get('/calendars/{calendar_id}/events')

    assert err is not None
    assert 'expired after refresh' in err.lower() or 'reconnect' in err.lower()
    assert len(request_calls) == 2, "must not retry more than once"


# ─── source-level guards ─────────────────────────────────────────────────

def test_refresh_path_uses_per_scope_lock():
    """[REGRESSION_GUARD] _get_access_token acquires the scope lock before
    the POST. Scout #2 fix."""
    cal = _load_gcal_calendar()
    src = inspect.getsource(cal._get_access_token)
    assert '_lock_for(scope)' in src


def test_api_call_uses_template_substitution():
    """[REGRESSION_GUARD] _api_call substitutes {calendar_id} into the
    endpoint template. Callers pass templates, not pre-built URLs — ensures
    every call acquires its own scope context through _get_access_token."""
    cal = _load_gcal_calendar()
    src = inspect.getsource(cal._api_call)
    assert 'endpoint_template.format(calendar_id=calendar_id)' in src


def test_api_helpers_do_not_take_token_param():
    """[REGRESSION_GUARD] After the refactor, _api_get/post/delete no
    longer take a pre-fetched token. They fetch internally so 401-retry
    can force-refresh."""
    cal = _load_gcal_calendar()
    import inspect as _i
    # Should take (endpoint_template, ...) — no 'token' param
    for fn_name in ('_api_get', '_api_post', '_api_delete'):
        fn = getattr(cal, fn_name)
        sig = _i.signature(fn)
        assert 'token' not in sig.parameters, \
            f"{fn_name} still accepts `token` — old signature not removed"
