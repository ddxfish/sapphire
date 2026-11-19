"""Tests for credentials_manager Google Calendar methods."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def creds_manager(tmp_path):
    """Create a CredentialsManager with temp file paths."""
    creds_file = tmp_path / 'credentials.json'
    salt_file = tmp_path / '.scramble_salt'

    with patch('core.credentials_manager.CREDENTIALS_FILE', creds_file), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', salt_file), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        from core.credentials_manager import CredentialsManager
        mgr = CredentialsManager()
        yield mgr


class TestGcalAccountCRUD:
    """CRUD operations for gcal accounts."""

    def test_set_and_get_account(self, creds_manager):
        result = creds_manager.set_gcal_account(
            'sawyer',
            client_id='test-client-id',
            client_secret='test-secret',
            calendar_id='sawyer@group.calendar.google.com',
            label='Sawyer Cal'
        )
        assert result is True

        acct = creds_manager.get_gcal_account('sawyer')
        assert acct['client_id'] == 'test-client-id'
        assert acct['client_secret'] == 'test-secret'
        assert acct['calendar_id'] == 'sawyer@group.calendar.google.com'
        assert acct['label'] == 'Sawyer Cal'

    def test_get_nonexistent_returns_defaults(self, creds_manager):
        acct = creds_manager.get_gcal_account('nonexistent')
        assert acct['client_id'] == ''
        assert acct['client_secret'] == ''
        assert acct['refresh_token'] == ''
        assert acct['calendar_id'] == 'primary'

    def test_delete_account(self, creds_manager):
        creds_manager.set_gcal_account('temp', client_id='x', client_secret='y')
        assert creds_manager.delete_gcal_account('temp') is True
        assert creds_manager.delete_gcal_account('temp') is False

    def test_delete_nonexistent(self, creds_manager):
        assert creds_manager.delete_gcal_account('nope') is False

    def test_list_accounts(self, creds_manager):
        creds_manager.set_gcal_account('default', client_id='id1', client_secret='s1')
        creds_manager.set_gcal_account('sawyer', client_id='id2', client_secret='s2',
                                        calendar_id='sawyer@cal', label='Sawyer')

        accounts = creds_manager.list_gcal_accounts()
        assert len(accounts) == 2
        scopes = {a['scope'] for a in accounts}
        assert scopes == {'default', 'sawyer'}

    def test_list_accounts_no_secrets_leaked(self, creds_manager):
        """list_gcal_accounts must not include client_secret or refresh_token."""
        creds_manager.set_gcal_account('test', client_id='pub', client_secret='SUPERSECRET',
                                        calendar_id='cal')
        creds_manager.update_gcal_tokens('test', refresh_token='SECRETTOKEN')

        for acct in creds_manager.list_gcal_accounts():
            assert 'client_secret' not in acct
            assert 'refresh_token' not in acct
            assert 'has_token' in acct


class TestGcalTokens:
    """OAuth token update flow."""

    def test_update_tokens(self, creds_manager):
        creds_manager.set_gcal_account('default', client_id='id', client_secret='secret')
        result = creds_manager.update_gcal_tokens('default', refresh_token='new_refresh')
        assert result is True

        acct = creds_manager.get_gcal_account('default')
        assert acct['refresh_token'] == 'new_refresh'

    def test_update_tokens_nonexistent_scope(self, creds_manager):
        result = creds_manager.update_gcal_tokens('nope', refresh_token='x')
        assert result is False

    def test_has_account_with_token(self, creds_manager):
        creds_manager.set_gcal_account('default', client_id='id', client_secret='s')
        assert creds_manager.has_gcal_account('default') is False  # no refresh token yet

        creds_manager.update_gcal_tokens('default', refresh_token='tok')
        assert creds_manager.has_gcal_account('default') is True

    def test_has_account_nonexistent(self, creds_manager):
        assert creds_manager.has_gcal_account('nope') is False


class TestGcalScopeIsolation:
    """Multiple gcal accounts must be fully isolated."""

    def test_different_scopes_different_data(self, creds_manager):
        creds_manager.set_gcal_account('default', client_id='id_default',
                                        client_secret='secret_default',
                                        calendar_id='primary')
        creds_manager.set_gcal_account('sawyer', client_id='id_sawyer',
                                        client_secret='secret_sawyer',
                                        calendar_id='sawyer@group.calendar.google.com')

        default = creds_manager.get_gcal_account('default')
        sawyer = creds_manager.get_gcal_account('sawyer')

        assert default['client_id'] != sawyer['client_id']
        assert default['calendar_id'] != sawyer['calendar_id']
        assert default['calendar_id'] == 'primary'
        assert sawyer['calendar_id'] == 'sawyer@group.calendar.google.com'

    def test_delete_one_doesnt_affect_other(self, creds_manager):
        creds_manager.set_gcal_account('a', client_id='id_a', client_secret='s_a')
        creds_manager.set_gcal_account('b', client_id='id_b', client_secret='s_b')

        creds_manager.delete_gcal_account('a')

        assert creds_manager.get_gcal_account('a')['client_id'] == ''  # gone
        assert creds_manager.get_gcal_account('b')['client_id'] == 'id_b'  # still here


# -- Easy Connect (relay) accounts: no local client id or secret ----------------
# Moved here from the Google Calendar plugin's tests when the Calendar plugin
# took that plugin over (2026-11-18); they test this file's subject.

def test_relay_account_counts_as_connected(creds_manager):
    import time
    creds_manager.set_gcal_account('easy', '', '', 'primary', '', 'easy',
                                   auth_mode='relay', relay_url='https://relay.example')
    assert creds_manager.has_gcal_account('easy') is False  # no refresh token yet
    creds_manager.update_gcal_tokens('easy', 'rt-1', 'at-1', time.time() + 3600)
    assert creds_manager.has_gcal_account('easy') is True   # despite the empty client_id

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
    """[REGRESSION_GUARD] A relay account needs no client id; a byo account
    with none must not read as connected."""
    creds_manager.set_gcal_account('shell', '', '', refresh_token='rt')
    assert creds_manager.has_gcal_account('shell') is False
