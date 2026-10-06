"""Tests for the API tokens system added 2026-05-21.

Covers:
- create / verify / revoke round trip
- atomic write integrity (corrupted load doesn't crash)
- last_used_at updates on verify
- list_safe never leaks full token
- duplicate name rejected
- empty / oversized name rejected
- non-matching token returns None (no info leak)
- require_login accepts Authorization: Bearer with a valid token
- require_login rejects Authorization: Bearer with a wrong/missing token
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_tokens_file(tmp_path, monkeypatch):
    """Redirect api_tokens.json into a temp dir and reset the singleton."""
    # Patch CONFIG_DIR + API_TOKENS_FILE before the module reloads
    fake_dir = tmp_path / "sapphire-config"
    fake_dir.mkdir()
    fake_file = fake_dir / "api_tokens.json"

    import core.api_tokens as mod
    monkeypatch.setattr(mod, "API_TOKENS_FILE", fake_file)
    # Recreate the singleton against the new path
    mod.api_tokens = mod.ApiTokensManager()
    return fake_file


def test_create_and_verify_roundtrip(temp_tokens_file):
    from core.api_tokens import api_tokens

    entry = api_tokens.create("valheim-mod")
    assert entry["name"] == "valheim-mod"
    assert entry["token"].startswith("sk_")
    assert len(entry["token"]) > 30

    # Verify the same token comes back as the same entry
    found = api_tokens.verify(entry["token"])
    assert found is not None
    assert found["id"] == entry["id"]


def test_verify_unknown_token_returns_none(temp_tokens_file):
    from core.api_tokens import api_tokens

    api_tokens.create("real")
    assert api_tokens.verify("sk_definitely_not_a_real_token_value_zzz") is None
    assert api_tokens.verify("") is None
    assert api_tokens.verify(None) is None  # type: ignore[arg-type]


def test_revoke_removes_token(temp_tokens_file):
    from core.api_tokens import api_tokens

    entry = api_tokens.create("temp")
    assert api_tokens.verify(entry["token"]) is not None
    assert api_tokens.revoke(entry["id"]) is True
    # Now verify should fail
    assert api_tokens.verify(entry["token"]) is None
    # Re-revoke returns False
    assert api_tokens.revoke(entry["id"]) is False


def test_list_safe_never_exposes_full_token(temp_tokens_file):
    from core.api_tokens import api_tokens

    entry = api_tokens.create("client-x")
    listed = api_tokens.list_safe()
    assert len(listed) == 1
    safe = listed[0]
    # Required fields present
    assert safe["name"] == "client-x"
    assert safe["id"] == entry["id"]
    assert "last4" in safe
    # Full token field must be absent
    assert "token" not in safe
    # last4 only shows the tail
    assert safe["last4"] == entry["token"][-4:]


def test_duplicate_name_rejected(temp_tokens_file):
    from core.api_tokens import api_tokens

    api_tokens.create("dup-name")
    with pytest.raises(ValueError, match="already in use"):
        api_tokens.create("dup-name")


def test_empty_name_rejected(temp_tokens_file):
    from core.api_tokens import api_tokens

    with pytest.raises(ValueError, match="required"):
        api_tokens.create("")
    with pytest.raises(ValueError, match="required"):
        api_tokens.create("   ")


def test_oversized_name_rejected(temp_tokens_file):
    from core.api_tokens import api_tokens

    with pytest.raises(ValueError, match="too long"):
        api_tokens.create("x" * 200)


def test_last_used_at_updates_on_verify(temp_tokens_file):
    from core.api_tokens import api_tokens

    entry = api_tokens.create("track-me")
    # Initial last_used_at is None
    assert entry["last_used_at"] is None

    # Verify → updates
    api_tokens.verify(entry["token"])

    safe = api_tokens.list_safe()[0]
    assert safe["last_used_at"] is not None


def test_token_persistence_across_manager_recreate(temp_tokens_file):
    """File on disk survives manager recreation (loaded fresh from JSON)."""
    from core.api_tokens import api_tokens, ApiTokensManager
    import core.api_tokens as mod

    entry = api_tokens.create("persistent")
    full_token = entry["token"]

    # Recreate the manager (simulates a fresh process)
    mod.api_tokens = ApiTokensManager()
    found = mod.api_tokens.verify(full_token)
    assert found is not None
    assert found["name"] == "persistent"


def test_atomic_write_uses_tmp_then_rename(temp_tokens_file):
    """Save path writes via .tmp + rename so a crash mid-write can't leave
    api_tokens.json half-written."""
    from core.api_tokens import api_tokens

    api_tokens.create("atomic-test")
    assert temp_tokens_file.exists()
    # No .tmp file left behind after a clean save
    tmp_leftover = temp_tokens_file.with_suffix(".tmp")
    assert not tmp_leftover.exists()


def test_corrupt_file_backed_up_and_ignored_on_load(temp_tokens_file, monkeypatch, caplog):
    """If api_tokens.json is corrupt at load time, a .corrupt.<ts> backup is
    written and the manager starts empty (rather than crashing)."""
    # Pre-pollute the file with garbage
    temp_tokens_file.write_text("this is not valid json", encoding="utf-8")

    from core.api_tokens import ApiTokensManager
    mgr = ApiTokensManager()

    # Manager loaded empty
    assert mgr.count() == 0
    # Backup exists
    backups = list(temp_tokens_file.parent.glob("api_tokens.json.corrupt.*"))
    assert len(backups) == 1


def test_revoke_unknown_id_returns_false(temp_tokens_file):
    from core.api_tokens import api_tokens

    api_tokens.create("real")
    assert api_tokens.revoke("00000000-0000-0000-0000-000000000000") is False


def test_concurrent_create_doesnt_lose_tokens(temp_tokens_file):
    """RLock around the create/save path ensures concurrent inserts don't
    clobber each other in memory or on disk."""
    from core.api_tokens import api_tokens

    N = 20
    errors = []

    def worker(i):
        try:
            api_tokens.create(f"concurrent-{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent create errors: {errors}"
    assert api_tokens.count() == N

    # Verify the disk reflects the same N (no clobber)
    loaded = json.loads(temp_tokens_file.read_text(encoding="utf-8"))
    assert len(loaded["tokens"]) == N


# ─── require_login integration ──────────────────────────────────────────────

@pytest.fixture
def mock_request_factory():
    """Build a stand-in Request with the headers + session we want."""
    from unittest.mock import MagicMock

    def _make(headers=None, session=None, path="/api/test"):
        req = MagicMock()
        req.headers = headers or {}
        req.session = session or {}
        # url.path used by require_login for redirect-or-401 decision
        req.url.path = path
        return req

    return _make


def test_require_login_accepts_valid_bearer(temp_tokens_file, mock_request_factory):
    from core.api_tokens import api_tokens
    from core.auth import require_login

    entry = api_tokens.create("auth-test")
    req = mock_request_factory(headers={"Authorization": f"Bearer {entry['token']}"})

    # Need is_setup_complete to return True
    with patch("core.setup.is_setup_complete", return_value=True):
        result = asyncio.run(require_login(req))
    assert result is True


def test_require_login_rejects_wrong_bearer(temp_tokens_file, mock_request_factory):
    from core.auth import require_login
    from fastapi import HTTPException

    req = mock_request_factory(
        headers={"Authorization": "Bearer sk_wrong_token_value_no_match"}
    )
    with patch("core.setup.is_setup_complete", return_value=True), \
         patch("core.setup.get_password_hash", return_value=None):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(require_login(req))
    assert ei.value.status_code == 401


def test_require_login_rejects_missing_bearer_value(temp_tokens_file, mock_request_factory):
    from core.auth import require_login
    from fastapi import HTTPException

    # "Bearer" alone with no token value
    req = mock_request_factory(headers={"Authorization": "Bearer "})
    with patch("core.setup.is_setup_complete", return_value=True), \
         patch("core.setup.get_password_hash", return_value=None):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(require_login(req))
    assert ei.value.status_code == 401


def test_require_login_revoked_token_no_longer_works(temp_tokens_file, mock_request_factory):
    from core.api_tokens import api_tokens
    from core.auth import require_login
    from fastapi import HTTPException

    entry = api_tokens.create("revoke-test")
    # Works initially
    req = mock_request_factory(headers={"Authorization": f"Bearer {entry['token']}"})
    with patch("core.setup.is_setup_complete", return_value=True):
        result = asyncio.run(require_login(req))
    assert result is True

    # Revoke
    api_tokens.revoke(entry["id"])

    # Same header, now fails
    with patch("core.setup.is_setup_complete", return_value=True), \
         patch("core.setup.get_password_hash", return_value=None):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(require_login(req))
    assert ei.value.status_code == 401


# ─── X-API-Key retired (2026-10-03) ─────────────────────────────────────────
# The bcrypt password hash used to double as an API key. It is a login
# secret only now: the header is not a credential and exempts nothing.

_FAKE_HASH = "$2b$12$" + "a" * 53


def test_require_login_ignores_x_api_key(temp_tokens_file, mock_request_factory):
    """Even the exact stored hash in X-API-Key does not authenticate."""
    from core.auth import require_login
    from fastapi import HTTPException

    req = mock_request_factory(headers={"X-API-Key": _FAKE_HASH})
    with patch("core.setup.is_setup_complete", return_value=True), \
         patch("core.setup.get_password_hash", return_value=_FAKE_HASH):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(require_login(req))
    assert ei.value.status_code == 401


def _csrf_run(mock_request_factory, headers, session):
    """Drive the real csrf_protection middleware; returns (response, reached)."""
    from core.api_fastapi import csrf_protection

    req = mock_request_factory(headers=headers, session=session, path="/api/settings/X")
    req.method = "PUT"
    reached = []

    async def call_next(_req):
        reached.append(True)
        return "handler-response"

    return asyncio.run(csrf_protection(req, call_next)), bool(reached)


def test_csrf_not_skipped_by_x_api_key(mock_request_factory):
    """A logged-in cookie with an X-API-Key header and no token is refused —
    the header used to switch the whole check off."""
    resp, reached = _csrf_run(
        mock_request_factory,
        headers={"X-API-Key": "junk"},
        session={"logged_in": True, "csrf_token": "real-token"},
    )
    assert reached is False
    assert resp.status_code == 403


def test_csrf_leaves_cookieless_callers_alone(mock_request_factory):
    """A Bearer caller has no session cookie, so the check never applies."""
    resp, reached = _csrf_run(
        mock_request_factory,
        headers={"Authorization": "Bearer sk_anything"},
        session={},
    )
    assert reached is True
    assert resp == "handler-response"


# ─── A token that speaks as a persona (the MCP door, core/mcp_persona.py) ────

def test_a_token_keeps_the_persona_it_was_made_with(temp_tokens_file):
    from core.api_tokens import api_tokens

    bound = api_tokens.create("claude-code", persona="claude")
    plain = api_tokens.create("valheim-mod")
    assert bound["persona"] == "claude" and plain["persona"] is None
    assert api_tokens.persona_of(bound["token"]) == "claude"
    assert api_tokens.persona_of(plain["token"]) is None
    assert api_tokens.persona_of("sk_not_a_token") is None
    assert api_tokens.persona_of("") is None

    listed = {t["name"]: t for t in api_tokens.list_safe()}
    assert listed["claude-code"]["persona"] == "claude" and listed["valheim-mod"]["persona"] is None
    assert "token" not in listed["claude-code"]


def test_persona_survives_a_reload_and_old_records_read_as_plain(temp_tokens_file):
    import core.api_tokens as mod

    bound = mod.api_tokens.create("claude-code", persona="  claude  ")
    data = json.loads(temp_tokens_file.read_text())
    data["tokens"].append({"id": "old", "name": "from-before", "token": "sk_old_shape",
                           "created_at": None, "last_used_at": None})      # no persona key at all
    temp_tokens_file.write_text(json.dumps(data))

    fresh = mod.ApiTokensManager()
    assert fresh.persona_of(bound["token"]) == "claude"          # trimmed at create
    assert fresh.persona_of("sk_old_shape") is None
    assert {t["name"]: t["persona"] for t in fresh.list_safe()} == {"claude-code": "claude", "from-before": None}


def test_asking_for_the_persona_does_not_count_as_a_use(temp_tokens_file):
    from core.api_tokens import api_tokens

    bound = api_tokens.create("claude-code", persona="claude")
    before = temp_tokens_file.read_text()
    api_tokens.persona_of(bound["token"])
    assert temp_tokens_file.read_text() == before
    assert api_tokens.list_safe()[0]["last_used_at"] is None


def _create_through_the_route(body):
    from core.routes.system import create_api_token

    class _Req:
        async def json(self):
            return body
    return asyncio.run(create_api_token(_Req(), True))


def test_the_route_binds_a_real_persona_and_refuses_one_that_is_not(temp_tokens_file, monkeypatch):
    from fastapi import HTTPException
    from core.api_tokens import api_tokens
    from core.personas import persona_manager
    monkeypatch.setattr(persona_manager, "_personas", {"claude": {"settings": {}}})

    made = _create_through_the_route({"name": "claude-code", "persona": "claude"})
    assert made["persona"] == "claude" and api_tokens.persona_of(made["token"]) == "claude"
    assert _create_through_the_route({"name": "plain", "persona": ""})["persona"] is None

    with pytest.raises(HTTPException) as refused:
        _create_through_the_route({"name": "typo", "persona": "cluade"})
    assert refused.value.status_code == 400
    assert [t["name"] for t in api_tokens.list_safe()] == ["claude-code", "plain"]     # the typo made no token
