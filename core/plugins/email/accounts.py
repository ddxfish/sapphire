# core/plugins/email/accounts.py - who logs in, and how
"""Email accounts: credentials per scope, OAuth refresh, IMAP and SMTP login.

The ONE place that reads credentials or refreshes a token. The tools and the
daemon both come through here, so they share the per-scope refresh lock and
the same token scope (the daemon used to refresh with an IMAP-only scope,
which left the next send without SMTP rights).
"""
import base64
import hashlib
import imaplib
import logging
import re
import smtplib
import ssl
import threading
import time

logger = logging.getLogger(__name__)

TIMEOUT = 30  # seconds, IMAP and SMTP sockets
_O365_SCOPE = ('https://outlook.office365.com/IMAP.AccessAsUser.All '
               'https://outlook.office365.com/SMTP.Send offline_access')

_locks_guard = threading.Lock()
_locks: dict = {}


def lock_for(scope: str) -> threading.Lock:
    """Per-scope refresh lock. Microsoft rotates the refresh token, so two
    refreshes of one scope at once lose one of the rotated tokens for good."""
    with _locks_guard:
        lock = _locks.get(scope)
        if lock is None:
            lock = _locks[scope] = threading.Lock()
        return lock


def get_creds(scope):
    """(creds, None) when the account can log in, else (None, why).

    `why` is a sentence for whoever asked. A failed OAuth refresh says so
    apart from "never set up": re-running OAuth setup after a passing network
    fault would overwrite a refresh token that still works."""
    from core.credentials_manager import credentials
    if scope is None:
        return None, "Email is disabled for this chat."
    creds = credentials.get_email_account(scope)
    if not creds.get('address'):
        return None, "Email not configured. Set up credentials in Settings > Plugins > Email."
    if creds.get('auth_type') == 'oauth2':
        if not creds.get('oauth_refresh_token'):
            return None, "OAuth refresh token missing. Re-authorize in Settings > Plugins > Email."
        if creds.get('oauth_expires_at', 0) < time.time() + 300:
            creds = refresh_oauth(scope, creds)
            if not creds:
                return None, (
                    "OAuth token refresh FAILED. Your existing credentials MAY still be valid - "
                    "do NOT re-run OAuth setup yet (it would overwrite the existing refresh token). "
                    "Check the network or the provider's status and try again in a moment.")
        return creds, None
    if not creds.get('app_password'):
        return None, "App password not set. Configure it in Settings > Plugins > Email."
    return creds, None


def refresh_oauth(scope, creds):
    """Refresh an OAuth2 access token. Returns updated creds or None."""
    from core import net
    from core.credentials_manager import credentials

    tenant = creds.get('oauth_tenant_id', 'common')
    token_url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'

    with lock_for(scope):
        # Another thread may have refreshed while we waited on the lock.
        try:
            fresh = credentials.get_email_account(scope)
            if fresh and fresh.get('oauth_expires_at', 0) > time.time() + 60:
                return fresh
        except Exception:
            pass
        try:
            resp = net.post(token_url, data={
                'client_id': creds['oauth_client_id'],
                'client_secret': creds.get('oauth_client_secret', ''),
                'refresh_token': creds['oauth_refresh_token'],
                'grant_type': 'refresh_token',
                'scope': _O365_SCOPE,
            }, timeout=15)
            if resp.status_code != 200:
                logger.error(f"[EMAIL] OAuth token refresh failed: {resp.status_code} {resp.text[:200]}")
                return None
            tokens = resp.json()
            access_token = tokens['access_token']
            expires_at = time.time() + tokens.get('expires_in', 3600)
            new_refresh = tokens.get('refresh_token', '')
            credentials.update_email_oauth_tokens(scope, access_token, expires_at, new_refresh)
            creds = dict(creds, oauth_access_token=access_token, oauth_expires_at=expires_at)
            if new_refresh:
                creds['oauth_refresh_token'] = new_refresh
            logger.info(f"[EMAIL] OAuth token refreshed for scope '{scope}'")
            return creds
        except Exception as e:
            logger.error(f"[EMAIL] OAuth token refresh error: {e}")
            return None


REFUSED_HOLD = 600     # seconds a refused login is not tried again
_refused: dict = {}    # account -> (which credentials, when)
_TRANSIENT = re.compile(r'unavailable|too many|try again|temporar|rate.?limit|limit exceeded|busy|timed? ?out|'
                        r'connection|inuse|quota|throttl', re.I)


class LoginHeld(Exception):
    """This account's login was refused a moment ago and is not sent again yet."""


def creds_mark(creds):
    """Which credentials these are, without keeping them: a changed password is a new mark."""
    secret = creds.get('oauth_access_token') or creds.get('app_password') or ''
    text = '\n'.join(str(x) for x in (creds.get('address'), creds.get('imap_server'), creds.get('imap_port'),
                                      creds.get('auth_type'), secret))
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def is_refusal(error):
    """True when the server said the name or password is wrong. The mail
    library reports EVERY failure during login as a LoginError, a dropped
    line or "too many connections" included, and those must not count."""
    if type(error).__name__ != 'LoginError' or isinstance(error, imaplib.IMAP4.abort):
        return False
    return not _TRANSIENT.search(str(error))


def connect(scope, creds):
    """imap_connect() behind the refused-login hold. Every login goes through
    here: the Email page, her tools and the daemon. A wrong password must not
    be sent again on every click or poll, because mail servers lock an
    address, or ban the whole machine, after a few refusals in a row."""
    mark = creds_mark(creds)
    bad = _refused.get(scope)
    if bad and bad[0] == mark and time.time() - bad[1] < REFUSED_HOLD:
        mins = max(1, int((REFUSED_HOLD - (time.time() - bad[1])) // 60))
        raise LoginHeld("The mail server refused this account's login. Check its password in Settings > "
                        f"Plugins > Email. It is not tried again for {mins} more minute{'s' if mins != 1 else ''}, "
                        "so the server does not lock the address out.")
    try:
        client = imap_connect(creds)
    except Exception as e:
        if is_refusal(e):
            _refused[scope] = (mark, time.time())
        raise
    _refused.pop(scope, None)
    return client


def _tls():
    """Certificate and host name are checked. smtplib's own default checks neither."""
    return ssl.create_default_context()


def imap_connect(creds):
    """A logged-in IMAPClient (password or XOAUTH2)."""
    from imapclient import IMAPClient
    client = IMAPClient(creds['imap_server'], port=int(creds.get('imap_port') or 993),
                        ssl=True, timeout=TIMEOUT)
    try:
        if creds.get('auth_type') == 'oauth2':
            client.oauth2_login(creds['address'], creds['oauth_access_token'])
        else:
            client.login(creds['address'], creds['app_password'])
    except Exception:
        try:
            client.shutdown()
        except Exception:
            pass
        raise
    return client


def smtp_connect(creds):
    """A logged-in SMTP connection (password or XOAUTH2)."""
    port = int(creds.get('smtp_port') or 465)
    if port == 465:
        smtp = smtplib.SMTP_SSL(creds['smtp_server'], port, timeout=TIMEOUT, context=_tls())
    else:
        smtp = smtplib.SMTP(creds['smtp_server'], port, timeout=TIMEOUT)
        smtp.ehlo()
        smtp.starttls(context=_tls())
        smtp.ehlo()
    if creds.get('auth_type') == 'oauth2':
        auth = f"user={creds['address']}\x01auth=Bearer {creds['oauth_access_token']}\x01\x01"
        code, resp = smtp.docmd('AUTH', 'XOAUTH2 ' + base64.b64encode(auth.encode()).decode())
        if code not in (235, 250):
            raise smtplib.SMTPAuthenticationError(code, resp)
    else:
        smtp.login(creds['address'], creds['app_password'])
    return smtp
