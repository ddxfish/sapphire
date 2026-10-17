"""Regression guard for 2026-04-22 day-ruiner H8.

Pre-fix: if OAuth refresh failed transiently (network blip, provider hiccup),
_get_email_creds() returned None and the caller reported 'Email not
configured.' Users reading 'not configured' re-ran OAuth setup, which
overwrote their valid refresh_token with a fresh-consent token — permadeath
of the working credential if the fresh consent also hiccupped.

Fix: _get_email_creds_detailed() returns an explicit error string that
tells user 'DO NOT re-run OAuth setup yet.'
"""
import time
from unittest.mock import patch, MagicMock
import pytest


def test_refresh_failure_emits_distinct_error():
    """Refresh-fail path returns guidance that warns against re-running setup."""
    from plugins.email.tools.email_tool import _get_email_creds_detailed

    fake_creds = {
        'address': 'user@example.com',
        'auth_type': 'oauth2',
        'oauth_refresh_token': 'valid-token',
        'oauth_expires_at': time.time() - 10,  # expired 10s ago
    }

    with patch("plugins.email.tools.email_tool._get_current_email_scope", return_value="default"):
        with patch("core.credentials_manager.credentials") as mock_creds:
            mock_creds.get_email_account.return_value = fake_creds
            with patch("plugins.email.tools.email_tool._refresh_oauth_token", return_value=None):
                creds, err = _get_email_creds_detailed()

    assert creds is None, "Refresh failure should return None creds"
    assert err is not None, "Refresh failure should return an error string"
    assert "FAILED" in err or "refresh" in err.lower(), \
        f"Error should mention refresh failure: {err!r}"
    assert "do not re-run" in err.lower() or "do NOT re-run" in err, \
        f"Error MUST warn against re-running OAuth setup (would overwrite valid token): {err!r}"


def test_missing_refresh_token_distinct_error():
    """OAuth account with no refresh_token yet returns different guidance
    (safe to re-authorize because no valid token to overwrite)."""
    from plugins.email.tools.email_tool import _get_email_creds_detailed

    fake_creds = {
        'address': 'user@example.com',
        'auth_type': 'oauth2',
        'oauth_refresh_token': '',  # never authorized
    }

    with patch("plugins.email.tools.email_tool._get_current_email_scope", return_value="default"):
        with patch("core.credentials_manager.credentials") as mock_creds:
            mock_creds.get_email_account.return_value = fake_creds
            creds, err = _get_email_creds_detailed()

    assert creds is None
    assert err is not None
    assert "Re-authorize" in err or "re-authorize" in err.lower()
    # Critically: should NOT contain the "do not re-run" warning
    assert "do not re-run" not in err.lower(), \
        "Missing token case should tell user to re-authorize, not avoid it"


def test_disabled_scope_distinct_error():
    """scope=None path says 'Email is disabled for this chat.'"""
    from plugins.email.tools.email_tool import _get_email_creds_detailed

    with patch("plugins.email.tools.email_tool._get_current_email_scope", return_value=None):
        creds, err = _get_email_creds_detailed()

    assert creds is None
    assert err is not None
    assert "disabled" in err.lower()


def test_no_address_distinct_error():
    """Not-configured (no address) path says 'set up credentials.'"""
    from plugins.email.tools.email_tool import _get_email_creds_detailed

    with patch("plugins.email.tools.email_tool._get_current_email_scope", return_value="default"):
        with patch("core.credentials_manager.credentials") as mock_creds:
            mock_creds.get_email_account.return_value = {'address': ''}
            creds, err = _get_email_creds_detailed()

    assert creds is None
    assert err is not None
    assert "not configured" in err.lower() or "set up" in err.lower()


def test_happy_path_returns_creds_no_error():
    """Valid OAuth creds with unexpired token returns (creds, None)."""
    from plugins.email.tools.email_tool import _get_email_creds_detailed

    fake_creds = {
        'address': 'user@example.com',
        'auth_type': 'oauth2',
        'oauth_refresh_token': 'valid',
        'oauth_expires_at': time.time() + 3600,  # expires in 1h
        'oauth_access_token': 'current',
    }

    with patch("plugins.email.tools.email_tool._get_current_email_scope", return_value="default"):
        with patch("core.credentials_manager.credentials") as mock_creds:
            mock_creds.get_email_account.return_value = fake_creds
            creds, err = _get_email_creds_detailed()

    assert creds is not None, f"Happy path should return creds, got error: {err}"
    assert err is None
    assert creds['address'] == 'user@example.com'
