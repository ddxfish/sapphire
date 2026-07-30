"""[REGRESSION_GUARD] Account/scope mutations must publish SCOPE_CHANGED.

Bug class (2026-06-24 stale-UI campaign): the account-backed scopes
(email/gcal/bitcoin/github/telegram/discord) build their chat-sidebar scope
dropdown options from each plugin's account list. Their CRUD routes never
published SCOPE_CHANGED, so a newly added/removed account didn't show in the
sidebar dropdown until a full page reload. Fix: publish SCOPE_CHANGED on every
account mutation; chat.js already listens (`scope_changed` -> loadSidebar()).

These guard the core-route publishes directly (they're mounted on the app).
The three signed-plugin routes (telegram/discord/gcal-OAuth) aren't loaded in
the TestClient app, so they get a static guard at the bottom.
"""
import pathlib

import pytest


def _scope_events(capture):
    return [d for ev, d in capture.events if ev == "scope_changed"]


# ── email ────────────────────────────────────────────────────────────
def test_set_email_account_publishes_scope_changed(client, event_bus_capture, monkeypatch):
    c, csrf = client
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "get_email_account", lambda scope: {}, raising=False)
    monkeypatch.setattr(credentials, "set_email_account", lambda *a, **k: True, raising=False)

    r = c.put("/api/email/accounts/work", headers={"X-CSRF-Token": csrf},
              json={"address": "a@b.com", "app_password": "x"})
    assert r.status_code == 200, r.text
    assert _scope_events(event_bus_capture), \
        f"no scope_changed; saw {[ev for ev, _ in event_bus_capture.events]}"


def test_delete_email_account_publishes_scope_changed(client, event_bus_capture, monkeypatch):
    c, csrf = client
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "delete_email_account", lambda scope: True, raising=False)

    r = c.delete("/api/email/accounts/work", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert _scope_events(event_bus_capture)


# ── github ───────────────────────────────────────────────────────────
def test_set_github_account_publishes_scope_changed(client, event_bus_capture, monkeypatch):
    c, csrf = client
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "get_github_account", lambda scope: {}, raising=False)
    monkeypatch.setattr(credentials, "set_github_account", lambda *a, **k: True, raising=False)

    r = c.put("/api/github/accounts/work", headers={"X-CSRF-Token": csrf},
              json={"username": "octocat", "pat": "ghp_x"})
    assert r.status_code == 200, r.text
    assert _scope_events(event_bus_capture)


def test_delete_github_account_publishes_scope_changed(client, event_bus_capture, monkeypatch):
    c, csrf = client
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "delete_github_account", lambda scope: True, raising=False)

    r = c.delete("/api/github/accounts/work", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert _scope_events(event_bus_capture)


# ── bitcoin / gcal (delete paths — set paths need WIF/decrypt machinery) ──
def test_delete_bitcoin_wallet_publishes_scope_changed(client, event_bus_capture, monkeypatch):
    c, csrf = client
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "delete_bitcoin_wallet", lambda scope: True, raising=False)

    r = c.delete("/api/bitcoin/wallets/work", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert _scope_events(event_bus_capture)


def test_delete_gcal_account_publishes_scope_changed(client, event_bus_capture, monkeypatch):
    c, csrf = client
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, "delete_gcal_account", lambda scope: True, raising=False)

    r = c.delete("/api/gcal/accounts/work", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert _scope_events(event_bus_capture)


# ── static guard for the signed-plugin routes (not loaded in TestClient) ──
@pytest.mark.parametrize("rel_path", [
    "plugins/google-calendar/routes/oauth.py",
    "plugins/telegram/routes/auth.py",
    "plugins/discord/routes/accounts.py",
    "user/plugins/discord/api/accounts.py",
])
def test_signed_plugin_account_routes_publish_scope_changed(rel_path):
    """These plugin routes add/remove account-backed scopes but aren't mounted
    on the core TestClient app, so guard the publish statically — catches a
    regression where someone removes the SCOPE_CHANGED publish."""
    root = pathlib.Path(__file__).resolve().parent.parent
    if not (root / rel_path).exists():
        pytest.skip(f"{rel_path} not installed on this box (user-band plugin)")
    text = (root / rel_path).read_text(encoding="utf-8")
    assert "Events.SCOPE_CHANGED" in text, \
        f"{rel_path} no longer publishes SCOPE_CHANGED — sidebar scope dropdowns will go stale"
