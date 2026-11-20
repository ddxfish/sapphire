"""accounts.get_creds: each way an account can be unusable says its own thing.

A failed OAuth refresh must not read as "not configured": re-running OAuth
setup after a passing network fault overwrites a refresh token that works.
"""
import time
from unittest.mock import MagicMock, patch

from plugins.email import accounts


def _creds_mgr(acct):
    mgr = MagicMock()
    mgr.get_email_account.return_value = acct
    return mgr


_OAUTH = {'address': 'me@corp.com', 'auth_type': 'oauth2', 'oauth_refresh_token': 'rt', 'imap_server': 'outlook.office365.com',
          'oauth_client_id': 'cid', 'oauth_client_secret': 'cs', 'oauth_expires_at': 0}


def test_disabled_scope():
    assert accounts.get_creds(None) == (None, "Email is disabled for this chat.")


def test_no_address_means_not_configured():
    with patch('core.credentials_manager.credentials', _creds_mgr({'address': ''})):
        creds, why = accounts.get_creds('work')
    assert creds is None and 'not configured' in why


def test_missing_password_and_missing_refresh_token_are_named():
    with patch('core.credentials_manager.credentials', _creds_mgr({'address': 'a@x.com', 'app_password': '', 'imap_server': 'imap.x.com'})):
        assert 'App password not set' in accounts.get_creds('work')[1]
    with patch('core.credentials_manager.credentials', _creds_mgr(dict(_OAUTH, oauth_refresh_token=''))):
        assert 'sign-in is gone' in accounts.get_creds('work')[1]


def test_refresh_failure_warns_against_redoing_setup():
    with patch('core.credentials_manager.credentials', _creds_mgr(dict(_OAUTH))), \
         patch.object(accounts, 'refresh_oauth', return_value=None):
        creds, why = accounts.get_creds('work')
    assert creds is None and 'refresh FAILED' in why and 'do NOT re-run OAuth setup' in why


def test_fresh_token_and_password_accounts_pass():
    fresh = dict(_OAUTH, oauth_expires_at=time.time() + 3600, oauth_access_token='at')
    with patch('core.credentials_manager.credentials', _creds_mgr(fresh)):
        assert accounts.get_creds('work') == (fresh, None)
    pw = {'address': 'a@x.com', 'app_password': 'pw', 'auth_type': 'password', 'imap_server': 'imap.x.com'}
    with patch('core.credentials_manager.credentials', _creds_mgr(pw)):
        assert accounts.get_creds('work') == (pw, None)


def test_refresh_asks_for_send_rights_too_and_saves_under_its_own_scope():
    mgr = _creds_mgr(dict(_OAUTH))
    resp = MagicMock(status_code=200)
    resp.json.return_value = {'access_token': 'new', 'expires_in': 3600, 'refresh_token': 'rt2'}
    with patch('core.credentials_manager.credentials', mgr), patch('core.net.post', return_value=resp) as post:
        creds = accounts.refresh_oauth('work', dict(_OAUTH))
    assert 'SMTP.Send' in post.call_args.kwargs['data']['scope']      # the daemon used to drop this
    assert creds['oauth_access_token'] == 'new' and creds['oauth_refresh_token'] == 'rt2'
    assert mgr.update_email_oauth_tokens.call_args.args[0] == 'work'
