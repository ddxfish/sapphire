# core/plugins/email/accounts.py - who logs in, and how
"""Email accounts: credentials per scope, OAuth refresh, IMAP and SMTP login.

The ONE place that reads credentials or refreshes a token. The tools and the
daemon both come through here, so they share the per-scope refresh lock and
the same token scope (the daemon used to refresh with an IMAP-only scope,
which left the next send without SMTP rights).
"""
import base64
import errno
import hashlib
import imaplib
import logging
import re
import smtplib
import socket
import ssl
import threading
import time

logger = logging.getLogger(__name__)

TIMEOUT = 30  # seconds, IMAP and SMTP sockets
_CONTROL_HOST = re.compile(r'[\x00-\x20\x7f]')     # nothing like this belongs in a server name
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
            return None, ("This Microsoft account's sign-in is gone and this version has no screen to renew it. "
                          "Remove the account in Settings > Plugins > Email.")
        if creds.get('oauth_expires_at', 0) < time.time() + 300:
            creds = refresh_oauth(scope, creds)
            if not creds:
                return None, (
                    "OAuth token refresh FAILED. Your existing credentials MAY still be valid - "
                    "do NOT re-run OAuth setup yet (it would overwrite the existing refresh token). "
                    "Check the network or the provider's status and try again in a moment.")
    elif not creds.get('app_password'):
        return None, "App password not set. Configure it in Settings > Plugins > Email."
    if not str(creds.get('imap_server') or '').strip():
        return None, "This email account has no mail server set. Add it in Settings > Plugins > Email."
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
                try:
                    code = str((resp.json() or {}).get('error') or '')[:60]
                except Exception:
                    code = ''
                logger.error(f"[EMAIL] OAuth token refresh failed: HTTP {resp.status_code} {code}")
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
_refused: dict = {}    # who -> (which credentials, when, the sentence it got)
# What a mail server says when it is NOT judging the password: a busy server, a
# dropped line, a server going down. The mail library reports all of these as a
# failed login too.
_TRANSIENT = re.compile(r'unavailable|too many|try again|temporar|rate.?limit|limit exceeded|busy|timed? ?out|'
                        r'connection|inuse|quota|throttl|socket error|\beof\b|\bbye\b|unexpected|shutting down|'
                        r'system error|internal error', re.I)


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
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return getattr(error, 'smtp_code', 535) // 100 != 4          # 454: come back later, not "wrong password"
    if type(error).__name__ != 'LoginError' or isinstance(error, imaplib.IMAP4.abort):
        return False
    return not _TRANSIENT.search(str(error))


def _held(who, mark):
    """Raise LoginHeld while this login is on hold for these credentials."""
    bad = _refused.get(who)
    if bad and bad[0] == mark and time.time() - bad[1] < REFUSED_HOLD:
        mins = max(1, int((REFUSED_HOLD - (time.time() - bad[1])) // 60))
        why = bad[2] if len(bad) > 2 and bad[2] else "The mail server refused this account's login. Check its password in Settings > Plugins > Email."
        held = LoginHeld(f"{why} (It is not tried again for {mins} more minute{'s' if mins != 1 else ''}, so the "
                         "server does not lock the address out. Test in Settings > Plugins > Email tries at once.)")
        held.words = str(held)
        raise held


def connect(scope, creds, timeout=None, force=False):
    """imap_connect() behind the refused-login hold. Every login goes through
    here: the Email page, her tools, the daemon and the Test button. A wrong
    password must not be sent again on every click or poll, because mail
    servers lock an address, or ban the whole machine, after a few refusals
    in a row. `force` (the Test button, a person asking): try now whatever
    the hold says; a refusal is still recorded for everyone else."""
    mark = creds_mark(creds)
    if not force:
        _held(scope, mark)
    try:
        client = imap_connect(creds, timeout) if timeout else imap_connect(creds)
    except Exception as e:
        _word(e, creds)
        if is_refusal(e):
            _refused[scope] = (mark, time.time(), getattr(e, 'words', ''))
        raise
    # It worked: the hold is over. Except when a person's Test logged in with what they
     # TYPED while the stored password is still the refused one: that hold stays.
    if not force or (_refused.get(scope) or ('',))[0] == mark:
        _refused.pop(scope, None)
    return client


def forgive(scope, creds):
    """These credentials just logged in (the Test button): lift their hold, and only theirs."""
    if (_refused.get(scope) or ('',))[0] == creds_mark(creds):
        _refused.pop(scope, None)


# -- why a connection failed, in words ---------------------------------------------

_APP_PASSWORD = (
    (('gmail.com', 'googlemail.com'),
     "Google wants an app password here, not your normal password (2-Step Verification must be on). "
     "Make one at https://myaccount.google.com/apppasswords"),
    (('yahoo.com', 'aol.com'),
     "This provider wants an app password here, not your normal password. Make one in your account's security settings."),
    (('mail.me.com', 'icloud.com'),
     "Apple wants an app-specific password here, not your Apple Account password. Make one at https://account.apple.com"),
    (('fastmail.com',),
     "Fastmail wants an app password here, not your normal password (Settings > Privacy & Security)."),
    (('office365.com', 'outlook.com', 'hotmail.com', 'live.com'),
     "Microsoft has switched off password sign-in for mail programs on most accounts, app passwords included."),
)
_USUAL = {False: (993,), True: (465, 587)}       # sending? -> the ports that job normally uses


def well_known(host):
    """True for the big mail providers. Their certificates always verify, so
    one that does not is somebody in between, never the provider."""
    host = str(host or '').strip().lower().rstrip('.')
    return any(host == n or host.endswith('.' + n) for names, _words in _APP_PASSWORD for n in names) \
        or any(host == n or host.endswith('.' + n) for n in ('google.com', 'me.com', 'mac.com', 'yahoodns.net',
                                                             'outlook.office.com', 'microsoft.com', 'messagingengine.com'))


def _provider_hint(host):
    host = str(host or '').lower()
    for names, words in _APP_PASSWORD:
        if any(host == n or host.endswith('.' + n) for n in names):
            return words
    return "Check the address and the password in Settings > Plugins > Email."


def _port_words(port, sending):
    usual = _USUAL[sending]
    return '' if port in usual else f" The usual port for {'sending' if sending else 'reading mail'} is {' or '.join(map(str, usual))}."


def _host_trouble(host):
    """What is wrong with a server name as typed, or ''."""
    text = str(host or '').strip()
    if not text:
        return "No mail server name is set for this account. Add it in Settings > Plugins > Email."
    if '@' in text:
        return f"'{text}' is an email address, not a server name. A server name looks like mail.example.com."
    if '://' in text or '/' in text or ' ' in text:
        return f"'{text}' is not a server name: leave out anything like imaps:// and any slash. It looks like mail.example.com."
    return ''


def explain(error, creds=None, sending=False):
    """A failed connection as a sentence a person can act on, or '' when
    the error is not one of the known kinds (the caller then says its own)."""
    creds = creds or {}
    raw_host = creds.get('smtp_server' if sending else 'imap_server')
    host = str(raw_host or '').strip() or 'the mail server'
    try:
        port = int(creds.get('smtp_port' if sending else 'imap_port') or (465 if sending else 993))
    except (TypeError, ValueError):
        port = 465 if sending else 993
    job = 'sending' if sending else 'reading mail'
    if isinstance(error, (CertUntrusted, LoginHeld)):
        return str(error)
    if isinstance(error, UnicodeEncodeError):
        # the library's own text would show a character of the password and where it sits
        return "This mail server cannot take a password with letters outside plain English ones. Use an app password."
    if isinstance(error, OverflowError) or not 1 <= port <= 65535:
        return f"{port} is not a port. Reading mail is 993; sending is 465 or 587."
    if type(error).__name__ == 'LoginError' or isinstance(error, smtplib.SMTPAuthenticationError):
        text = str(error)
        if not is_refusal(error):
            return (f"{host} did not take the login just now (it is busy, or the connection dropped). "
                    "This is not about the password. Try again in a moment.")
        if re.search(r'not enabled for imap|imap access is disabled|imap is disabled', text, re.I):
            return (f"{host} refused the login: IMAP is switched off for this account. It is turned on in the mail "
                    "provider's settings (for a company address, by whoever runs the mail).")
        if creds.get('auth_type') == 'oauth2':
            return f"{host} refused this account's Microsoft sign-in for {job}. It may have run out or been withdrawn."
        return f"The mail server refused the login for {job}. {_provider_hint(host)}"
    if isinstance(error, socket.gaierror):
        trouble = _host_trouble(raw_host)
        if trouble:
            return trouble
        if error.errno == getattr(socket, 'EAI_AGAIN', -3):
            return f"This machine cannot look up {host} right now. Check that it is online."
        return f"There is no mail server called '{host}'. Check the server name in Settings > Plugins > Email."
    if isinstance(error, (socket.timeout, TimeoutError)):
        return (f"{host} did not answer on port {port}. Check the server name and the port, "
                "and that this machine is online.")
    if isinstance(error, ConnectionRefusedError):
        return (f"{host} is not taking connections on port {port}." + (_port_words(port, sending)
                or " The server may be down, or a firewall is in the way."))
    if isinstance(error, PermissionError):
        return f"Something on this machine (a firewall or antivirus) blocked the connection to {host} on port {port}."
    if isinstance(error, ssl.SSLError):
        return (f"{host} did not start a secure connection on port {port}." + (_port_words(port, sending)
                or " The server may be too old for today's encryption, or something on the network "
                   "(a firewall, antivirus) cut in."))
    if isinstance(error, smtplib.SMTPNotSupportedError):
        if 'starttls' in str(error).lower():
            return f"{host} does not offer an encrypted connection on port {port}. Use port 465 for sending."
        return f"{host} does not take a login on port {port}.{_port_words(port, sending)}"
    if isinstance(error, smtplib.SMTPConnectError):
        code = getattr(error, 'smtp_code', 0)
        if code == -1:
            return f"Port {port} on {host} is not a port for sending mail.{_port_words(port, sending)}"
        return f"{host} turned the connection away (code {code}). It may be busy, or it does not accept this machine."
    if isinstance(error, (smtplib.SMTPServerDisconnected, ConnectionError)):
        return f"The connection to {host} dropped. Try again."
    if isinstance(error, OSError) and getattr(error, 'errno', None) in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return f"This machine cannot reach {host}. Check that it is online."
    if isinstance(error, (smtplib.SMTPException, imaplib.IMAP4.error)):
        # What such a server sent is in the exception's text. It is the other
        # side's to choose, so it goes to the log and never to a screen or to her.
        return f"{host} did not answer like a mail server on port {port}.{_port_words(port, sending)}"
    return ''


def _word(error, creds, sending=False):
    """Hang the sentence on the error, for whoever reports it."""
    if getattr(error, 'words', ''):
        return
    try:
        error.words = explain(error, creds, sending)
    except Exception:
        pass


# -- certificates ----------------------------------------------------------------------
#
# Every connection checks the server's certificate and its name. A server
# that cannot pass (a certificate it signed itself, one for another name, a
# home server reached by IP) is refused BEFORE the password is sent, unless
# the person looked at that certificate and trusted it. What is kept is the
# certificate's fingerprint for that server and port, so trust is in that one
# certificate: another one showing up there later is refused again.

class CertUntrusted(Exception):
    """The server's certificate cannot be verified and has not been trusted.
    `info`: host, port, which ('imap' | 'smtp'), reason, changed, and when the
    certificate could be read: fingerprint, subject, issuer, expires."""

    def __init__(self, words, info):
        super().__init__(words)
        self.words, self.info = words, info


def _state():
    from plugins.email import mailbox
    return mailbox._state()


def _where(host, port):
    return f"{str(host or '').strip().lower()}:{int(port)}"


def _pins():
    try:
        return {k: v for k, v in (_state().get('trusted_certs') or {}).items()
                if isinstance(v, dict) and isinstance(v.get('sha256'), str)}
    except Exception as e:
        logger.debug(f"[EMAIL] trusted certificates not readable: {e}")
        return {}


def trusted(host, port):
    """The fingerprint the person trusted for this server and port, or None."""
    return (_pins().get(_where(host, port)) or {}).get('sha256')


def trusted_list():
    out = []
    for where, pin in sorted(_pins().items()):
        host, _, port = where.rpartition(':')
        out.append({'host': host, 'port': int(port), 'fingerprint': pin.get('sha256', ''), 'since': pin.get('since', 0)})
    return out


def trust(host, port, fingerprint):
    key = _where(host, port)
    _state().update_with_lock('trusted_certs', lambda cur: {
        **(cur or {}), key: {'sha256': str(fingerprint).lower(), 'since': time.time()}}, {})
    logger.info(f"[EMAIL] certificate trusted for {key}")


def untrust(host, port, only=None):
    """Forget the trust for this server and port. `only`: forget it only if
    it is still this fingerprint (the caller read it a moment ago, and the
    person may have trusted another certificate since)."""
    key = _where(host, port)
    _state().update_with_lock('trusted_certs', lambda cur: {
        k: v for k, v in (cur or {}).items()
        if k != key or (only is not None and (v or {}).get('sha256') != only)}, {})
    logger.info(f"[EMAIL] certificate no longer trusted for {key}")


def _tls():
    """Certificate and host name are checked. smtplib's own default checks neither."""
    ctx = ssl.create_default_context()
    try:
        # Also the common public authorities, as shipped with Python's certifi.
        # Windows only fetches an authority's root the first time a browser
        # needs it, so on a fresh machine a perfectly good mail server (Let's
        # Encrypt, say) fails the check without this. The system's own store
        # stays in, so a company's or an antivirus's root keeps working.
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception as e:
        logger.debug(f"[EMAIL] certifi roots not added: {e}")
    return ctx


def _unchecked():
    """Encrypted, but the certificate is not judged here: the caller compares its fingerprint."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_REASONS = (
    (r'self.signed certificate in certificate chain',
     "its certificate comes from a private authority this machine does not know "
     "(a company's own, or security software on this machine re-signing connections)"),
    (r'self.signed', "it signed its own certificate"),
    (r'unable to get local issuer|unable to verify the first', "its certificate comes from an authority this machine does not know"),
    (r'expired', "its certificate has expired"),
    (r'not yet valid', "its certificate is not valid yet (check this machine's clock)"),
    (r'mismatch|not valid for|doesn.t match', "its certificate is for a different name"),
)


def _reason(error):
    text = str(getattr(error, 'verify_message', '') or error)
    for pattern, words in _REASONS:
        if re.search(pattern, text, re.I):
            return words
    return text[:160]


def describe(der):
    """What a person can check a certificate by."""
    out = {'fingerprint': hashlib.sha256(der).hexdigest()}
    try:
        from cryptography import x509
        cert = x509.load_der_x509_certificate(der)
        name = lambda n: next((a.value for a in n if a.oid == x509.NameOID.COMMON_NAME), '') or n.rfc4514_string()
        expires = getattr(cert, 'not_valid_after_utc', None) or cert.not_valid_after
        plain = lambda text: re.sub(r'[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]', '', str(text))[:120]
        out.update(subject=plain(name(cert.subject)), issuer=plain(name(cert.issuer)),
                   expires=expires.strftime('%Y-%m-%d'), self_signed=cert.issuer == cert.subject)
    except Exception as e:
        logger.debug(f"[EMAIL] certificate not readable beyond its fingerprint: {e}")
    return out


def _peek(opener):
    """The certificate the server shows, taken without judging it. Nothing is sent but the TLS hello."""
    open_with, sock_of, close = opener
    conn = open_with(_unchecked())
    try:
        return sock_of(conn).getpeercert(True)
    finally:
        try:
            close(conn)
        except Exception:
            pass


def _untrusted(host, port, which, error, opener, changed=False):
    info = {'host': str(host), 'port': int(port), 'which': which, 'reason': _reason(error), 'changed': changed}
    try:
        info.update(describe(_peek(opener)))
    except Exception as e:
        logger.debug(f"[EMAIL] could not read {host}:{port}'s certificate: {e}")
    if well_known(host):
        info['known'] = True
        words = (f"The connection to {host} is being intercepted: what answered could not prove it is {host} "
                 f"({info['reason']}), and that provider always can. Nothing was sent to it, your password "
                 "included. It may be security software on this machine, a company or hotel network, or an attack. "
                 "This cannot be trusted from here: try another network, or switch off 'scan encrypted "
                 "connections' in the security software.")
    elif changed:
        words = (f"{host} now shows a different certificate from the one you trusted, and the new one cannot be "
                 f"verified either ({info['reason']}). Nothing was sent to it, your password included. If you "
                 "renewed the certificate yourself, look at the new one and trust it: the Email page, or Test in "
                 "Settings > Plugins > Email.")
    else:
        words = (f"{host} could not prove it is the right mail server: {info['reason']}. Nothing was sent to it, "
                 "your password included. If this is your own server and you expect that, a person can look at "
                 "its certificate and trust it: the Email page, or Test in Settings > Plugins > Email.")
    return CertUntrusted(words, info)


def _encrypted(conn, opener):
    """The connection, once it is certain its socket is a TLS one. The mail
    libraries raise when encryption does not come up; this does not rest on that."""
    _open, sock_of, close = opener
    if not isinstance(sock_of(conn), ssl.SSLSocket):
        _quiet(close, conn)
        raise ssl.SSLError("the connection is not encrypted")
    return conn


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception as e:
        logger.debug(f"[EMAIL] cleanup: {e}")


def _secured(host, port, which, opener):
    """A connection whose encryption is up and whose server is the right
    one: verified the normal way, or showing exactly the certificate the
    person trusted for it. Raises CertUntrusted otherwise."""
    open_with, sock_of, close = opener
    pin = trusted(host, port)
    if pin and well_known(host):
        pin = None                         # never honoured for a provider whose certificate always verifies
    if pin:
        conn = open_with(_unchecked())
        try:
            same = hashlib.sha256(sock_of(_encrypted(conn, opener)).getpeercert(True)).hexdigest() == pin
        except Exception:
            _quiet(close, conn)
            raise
        if same:
            return conn
        _quiet(close, conn)
        try:
            conn = open_with(_tls())       # not the trusted one: fine only if it is a real one now
        except ssl.SSLCertVerificationError as e:
            raise _untrusted(host, port, which, e, opener, changed=True) from None
        # The server has a proper certificate now: the exception is over. Said
        # out loud, because a trust that was given under pressure leaves no other trace.
        _quiet(untrust, host, port, pin)
        _quiet(_say_trust_ended, host, port)
        return _encrypted(conn, opener)
    try:
        return _encrypted(open_with(_tls()), opener)
    except ssl.SSLCertVerificationError as e:
        raise _untrusted(host, port, which, e, opener) from None


def _say_trust_ended(host, port):
    from core import notify
    notify.notify(f"{host} no longer needs a trusted certificate", level='info', source='email',
                  body=(f"You had trusted a certificate for {host} (port {port}) that could not be verified. The "
                        "server now shows one that can, so that trust was dropped. If you never set up such a "
                        "server yourself, change that account's password: it was sent to whatever showed the "
                        "trusted certificate."),
                  key=f"email-trust-ended-{host}-{port}")


def _imap_opener(host, port, timeout):
    from imapclient import IMAPClient
    return (lambda ctx: IMAPClient(host, port=port, ssl=True, ssl_context=ctx, timeout=timeout),
            lambda c: c.socket(), lambda c: c.shutdown())


HELO_NAME = '[127.0.0.1]'     # what this machine calls itself to a mail server: never its real host name


def _smtp_opener(host, port, timeout):
    # smtplib would announce socket.getfqdn(): the machine's name (often with the
    # user's name in it), in the clear before STARTTLS, and some providers copy
    # it into a Received line every recipient can read.
    def open_with(ctx):
        if port == 465:
            return smtplib.SMTP_SSL(host, port, local_hostname=HELO_NAME, timeout=timeout, context=ctx)
        smtp = smtplib.SMTP(host, port, local_hostname=HELO_NAME, timeout=timeout)
        try:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
        except Exception:
            try:
                smtp.close()
            except Exception:
                pass
            raise
        return smtp
    return open_with, (lambda s: s.sock), (lambda s: s.close())


def cert_status(host, port, which, timeout=10):
    """Where this server stands, without logging in: {'state': 'verified'},
    or its certificate's details with state 'untrusted' | 'trusted' |
    'changed'. Any other failure (no such server, wrong port) raises."""
    port = int(port)
    opener = (_smtp_opener if which == 'smtp' else _imap_opener)(host, port, timeout)
    base = {'host': str(host), 'port': port, 'which': which}
    try:
        conn = opener[0](_tls())
    except ssl.SSLCertVerificationError as e:
        info = dict(base, reason=_reason(e), **describe(_peek(opener)))
        pin = trusted(host, port)
        info['state'] = 'trusted' if pin and pin == info['fingerprint'] else ('changed' if pin else 'untrusted')
        info['known'] = well_known(host)        # a big provider: never offered for trust
        return info
    try:
        opener[2](conn)
    except Exception:
        pass
    return dict(base, state='verified')


def imap_connect(creds, timeout=TIMEOUT):
    """A logged-in IMAPClient (password or XOAUTH2)."""
    host, port = creds['imap_server'], int(creds.get('imap_port') or 993)
    client = _secured(host, port, 'imap', _imap_opener(host, port, timeout))
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


def smtp_connect(creds, timeout=TIMEOUT, force=False):
    """A logged-in SMTP connection (password or XOAUTH2). A refused login is
    held like a reading one (connect()), so a retry loop cannot get the
    address locked; `force` is the Test button."""
    host, port = creds['smtp_server'], int(creds.get('smtp_port') or 465)
    who, mark = f"smtp\n{str(host).lower()}:{port}", creds_mark(dict(creds, imap_server=host, imap_port=port))
    if not force:
        _held(who, mark)
    try:
        smtp = _secured(host, port, 'smtp', _smtp_opener(host, port, timeout))
        try:
            if creds.get('auth_type') == 'oauth2':
                auth = f"user={creds['address']}\x01auth=Bearer {creds['oauth_access_token']}\x01\x01"
                code, resp = smtp.docmd('AUTH', 'XOAUTH2 ' + base64.b64encode(auth.encode()).decode())
                if code not in (235, 250):
                    raise smtplib.SMTPAuthenticationError(code, resp)
            else:
                smtp.login(creds['address'], creds['app_password'])
        except Exception:
            try:
                smtp.close()
            except Exception:
                pass
            raise
    except Exception as e:
        _word(e, creds, sending=True)
        if is_refusal(e):
            _refused[who] = (mark, time.time(), getattr(e, 'words', ''))
        raise
    _refused.pop(who, None)
    return smtp
