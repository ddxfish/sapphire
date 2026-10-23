"""Change the login password in-app — 2026-09-08 (Settings › System › Login
Password, POST /api/system/password). Before this the only "reset" was
deleting the hash file by hand, which sends the app back to /setup.
"""
from unittest.mock import patch

import pytest

import core.auth as auth_mod
from core.setup import save_password_hash, get_password_hash, verify_password

OLD = "oldpassword123"


@pytest.fixture
def secret_file(tmp_path):
    """Real bcrypt round-trip against a temp hash file; rate-limit state clean."""
    path = tmp_path / "secret_key"
    auth_mod._endpoint_limits.clear()
    with patch('core.setup.SECRET_KEY_FILE', path), \
         patch('core.setup.CONFIG_DIR', tmp_path):
        assert save_password_hash(OLD)
        yield path
    auth_mod._endpoint_limits.clear()


def _post(client, body):
    c, csrf = client
    return c.post('/api/system/password', json=body, headers={'X-CSRF-Token': csrf})


def test_change_succeeds_and_new_password_verifies(client, secret_file):
    r = _post(client, {"current": OLD, "new": "brandnewpassword"})
    assert r.status_code == 200, r.text
    assert verify_password("brandnewpassword", get_password_hash())
    assert not verify_password(OLD, get_password_hash())


def test_wrong_current_is_refused_and_hash_untouched(client, secret_file):
    before = secret_file.read_text()
    r = _post(client, {"current": "nope-nope-nope", "new": "brandnewpassword"})
    assert r.status_code == 403
    assert secret_file.read_text() == before


def test_short_new_password_refused(client, secret_file):
    r = _post(client, {"current": OLD, "new": "short"})
    assert r.status_code == 400
    assert verify_password(OLD, get_password_hash())


def test_same_password_refused(client, secret_file):
    r = _post(client, {"current": OLD, "new": OLD})
    assert r.status_code == 400


def test_missing_fields_refused(client, secret_file):
    r = _post(client, {})
    assert r.status_code == 403          # empty current never verifies
    assert verify_password(OLD, get_password_hash())


def test_rate_limited_after_five_attempts(client, secret_file):
    for _ in range(5):
        assert _post(client, {"current": "wrong-wrong-wrong", "new": "brandnewpassword"}).status_code == 403
    r = _post(client, {"current": OLD, "new": "brandnewpassword"})
    assert r.status_code == 429
    assert verify_password(OLD, get_password_hash()), "the throttled attempt must not land"


# ─── pre-push hunt 2026-09-08 (E4#1 atomic write, #4 bcrypt 72, #3 session salt)

import asyncio
import sys
from unittest.mock import MagicMock

from fastapi import HTTPException


def test_hash_write_is_atomic_and_private(client, secret_file):
    """In-place write_text left a ZERO-BYTE file on a crash mid-write — and an
    empty hash reopens /setup to whoever visits first. Now tmp + replace."""
    r = _post(client, {"current": OLD, "new": "brandnewpassword"})
    assert r.status_code == 200, r.text
    assert not secret_file.with_suffix('.tmp').exists(), "tmp must be replaced, never left behind"
    assert get_password_hash().startswith('$2')
    if sys.platform != 'win32':
        assert (secret_file.stat().st_mode & 0o777) == 0o600


def test_over_72_bytes_refused_before_bcrypt_raises(client, secret_file):
    """bcrypt 5.x raises past 72 bytes (4.x truncated silently) — refuse with a
    reason instead of a bare 500, on both the route and the saver."""
    r = _post(client, {"current": OLD, "new": "x" * 73})
    assert r.status_code == 400 and "72" in r.json()["detail"]
    assert verify_password(OLD, get_password_hash())
    assert save_password_hash("y" * 73) is None


def _req(session, path='/api/x'):
    req = MagicMock()
    req.session = session
    req.headers = {}
    req.url.path = path
    return req


def test_session_salt_stamp_evicts_other_cookies_after_a_change(secret_file):
    """Login stamps the hash's salt into the cookie; require_login accepts the
    cookie only while the live hash carries the same salt. A change (or the
    documented delete-secret_key reset) logs every OTHER device out; the
    changing tab re-stamps itself. A pre-stamp cookie is cleared, not bounced
    (/login would otherwise redirect it back to / forever)."""
    from core.auth import require_login
    stale = {'logged_in': True}
    with pytest.raises(HTTPException) as e:
        asyncio.run(require_login(_req(stale)))
    assert e.value.status_code == 401 and stale == {}
    good = {'logged_in': True, 'pw': get_password_hash()[:29]}
    assert asyncio.run(require_login(_req(good))) is True
    assert save_password_hash("brandnewpassword")
    with pytest.raises(HTTPException):
        asyncio.run(require_login(_req(good)))
    fresh = {'logged_in': True, 'pw': get_password_hash()[:29]}
    assert asyncio.run(require_login(_req(fresh))) is True
