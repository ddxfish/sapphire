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
