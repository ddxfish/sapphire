"""Setting an account up, and upgrading from the plugin before 2.0: the
connection errors as words, a certificate that cannot be verified (refused,
then trusted by its fingerprint), the Test button, and the two tool names
old toolsets still carry."""
import hashlib
import smtplib
import socket
import ssl

import pytest

from test_email_routes import _ok, _refusal, page  # noqa: F401  (the fixture)

DER, OTHER = b'the certificate the server shows', b'a different certificate'
PRINT = hashlib.sha256(DER).hexdigest()


@pytest.fixture(autouse=True)
def _fake_sockets(monkeypatch):
    """The servers in this file are plain objects, not TLS sockets (the real check runs against real
    sockets in the live probe)."""
    from plugins.email import accounts
    monkeypatch.setattr(accounts, '_encrypted', lambda conn, opener: conn)


def _cert_error(text='self-signed certificate'):
    e = ssl.SSLCertVerificationError(1, f'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {text}')
    e.verify_message = text
    return e


class Server:
    """A mail server as accounts._secured sees it: it shows `der`, and passes
    the normal check only when `valid`."""

    def __init__(self, der=DER, valid=False):
        self.der, self.valid, self.opened, self.closed = der, valid, [], 0

    def opener(self):
        def open_with(ctx):
            checked = ctx.verify_mode == ssl.CERT_REQUIRED
            self.opened.append('checked' if checked else 'unchecked')
            if checked and not self.valid:
                raise _cert_error()
            return self
        return open_with, (lambda conn: conn), self._close

    def _close(self, conn):
        self.closed += 1

    def getpeercert(self, binary=False):
        return self.der


# -- why a connection failed -----------------------------------------------------

def test_connection_failures_are_sentences(mail):
    explain, creds = mail.accounts.explain, dict(mail.creds, imap_server='mail.example.org')
    assert "no mail server called 'mail.example.org'" in explain(socket.gaierror(-2, 'Name or service not known'), creds)
    assert 'did not answer on port 993' in explain(socket.timeout('timed out'), creds)
    assert 'port 993' in explain(ConnectionRefusedError(111, 'refused'), creds)
    assert '465 or 587' in explain(ConnectionRefusedError(111, 'refused'), dict(creds, smtp_port=25), sending=True)
    assert 'secure connection' in explain(ssl.SSLError(1, '[SSL: WRONG_VERSION_NUMBER]'), creds)
    assert explain(RuntimeError('something else'), creds) == ''          # unknown: the caller says its own


def test_a_refused_login_names_the_providers_rule(mail):
    class LoginError(Exception):
        pass
    explain = mail.accounts.explain
    gmail = explain(LoginError('[AUTHENTICATIONFAILED] Invalid credentials'), dict(mail.creds, imap_server='imap.gmail.com'))
    assert 'refused the login' in gmail and 'app password' in gmail and 'myaccount.google.com' in gmail
    own = explain(LoginError('[AUTHENTICATIONFAILED] nope'), mail.creds)
    assert 'refused the login' in own and 'Settings > Plugins > Email' in own
    busy = explain(LoginError('Too many simultaneous connections'), mail.creds)
    assert 'refused the login' not in busy and 'Try again' in busy
    sending = explain(smtplib.SMTPAuthenticationError(535, b'bad'), dict(mail.creds, smtp_server='smtp.mail.me.com'), sending=True)
    assert 'for sending' in sending and 'app-specific password' in sending


def test_the_page_and_her_tool_say_the_sentence(page, monkeypatch):
    page.mailbox.close_kept()

    def no_such_host(creds, timeout=None):
        raise socket.gaierror(-2, 'Name or service not known')
    monkeypatch.setattr(page.accounts, 'imap_connect', no_such_host)
    assert "no mail server called 'imap.x.com'" in _refusal(page.routes.folder_view(query={'scope': 'work'}))
    text, ok = page.tool.execute('get_inbox', {}, None)
    assert not ok and "no mail server called 'imap.x.com'" in text and 'Errno' not in text


# -- a certificate that cannot be verified ------------------------------------------

def test_an_unverified_server_is_refused_before_anything_is_sent(mail):
    server = Server()
    with pytest.raises(mail.accounts.CertUntrusted) as caught:
        mail.accounts._secured('mail.home', 993, 'imap', server.opener())
    info = caught.value.info
    assert info['fingerprint'] == PRINT and info['reason'] == 'it signed its own certificate'
    assert (info['host'], info['port'], info['which'], info['changed']) == ('mail.home', 993, 'imap', False)
    assert 'Nothing was sent' in str(caught.value) and caught.value.words == str(caught.value)
    assert server.opened == ['checked', 'unchecked']         # the second look only reads the certificate
    assert server.closed == 1                                # and is closed again


def test_a_trusted_certificate_is_accepted_and_only_that_one(mail):
    mail.accounts.trust('Mail.Home', 993, PRINT)
    assert mail.accounts.trusted('mail.home', 993) == PRINT and mail.accounts.trusted('mail.home', 465) is None

    server = Server()
    assert mail.accounts._secured('mail.home', 993, 'imap', server.opener()) is server
    assert server.opened == ['unchecked']

    swapped = Server(der=OTHER)                              # someone else answering at that address
    with pytest.raises(mail.accounts.CertUntrusted) as caught:
        mail.accounts._secured('mail.home', 993, 'imap', swapped.opener())
    assert caught.value.info['changed'] is True and 'different certificate' in str(caught.value)
    assert caught.value.info['fingerprint'] == hashlib.sha256(OTHER).hexdigest()
    assert mail.accounts.trusted('mail.home', 993) == PRINT   # the old trust is not moved to the new one


def test_trust_ends_when_the_server_gets_a_real_certificate(mail):
    mail.accounts.trust('mail.home', 993, PRINT)
    proper = Server(der=OTHER, valid=True)
    assert mail.accounts._secured('mail.home', 993, 'imap', proper.opener()) is proper
    assert mail.accounts.trusted('mail.home', 993) is None and mail.accounts.trusted_list() == []


def test_the_page_hands_over_the_certificate_and_trusts_only_what_was_shown(page, monkeypatch):
    page.mailbox.close_kept()
    info = {'host': 'imap.x.com', 'port': 993, 'which': 'imap', 'reason': 'it signed its own certificate',
            'changed': False, 'fingerprint': PRINT}

    def untrusted(creds, timeout=None):
        raise page.accounts.CertUntrusted('imap.x.com could not prove it is the right mail server.', info)
    monkeypatch.setattr(page.accounts, 'imap_connect', untrusted)
    body, status = page.routes.folder_view(query={'scope': 'work'})
    assert status == 400 and body['cert'] == info and 'could not prove' in body['detail']
    text, ok = page.tool.execute('get_inbox', {}, None)       # her tool gets the words, and no way to trust it
    assert not ok and 'could not prove' in text

    showing = {'der': DER}
    monkeypatch.setattr(page.accounts, 'cert_status', lambda host, port, which, timeout=10: {
        'host': host, 'port': port, 'which': which, 'state': 'untrusted',
        'fingerprint': hashlib.sha256(showing['der']).hexdigest()})
    ask = {'host': 'imap.x.com', 'port': 993, 'which': 'imap', 'fingerprint': PRINT}
    showing['der'] = OTHER                                    # the certificate changed after the person looked
    assert 'different certificate' in _refusal(page.routes.trust_cert(body=ask))
    assert page.accounts.trusted('imap.x.com', 993) is None
    assert 'Which certificate' in _refusal(page.routes.trust_cert(body=dict(ask, fingerprint='abc')))

    showing['der'] = DER
    _ok(page.routes.trust_cert(body=dict(ask, fingerprint=':'.join(PRINT[i:i + 2] for i in range(0, 64, 2)).upper())))
    assert page.accounts.trusted('imap.x.com', 993) == PRINT
    assert [c['host'] for c in _ok(page.routes.certs(query={}))['trusted']] == ['imap.x.com']
    assert _ok(page.routes.untrust_cert(body={'host': 'imap.x.com', 'port': 993}))['trusted'] == []


# -- the Test button -------------------------------------------------------------------

@pytest.fixture
def stored(monkeypatch, mail):
    """What the credentials manager holds for 'work'."""
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, 'get_email_account', lambda scope: dict(mail.creds) if scope == 'work' else {})
    return mail


def test_the_test_checks_reading_and_sending_apart(page, stored, monkeypatch):
    page.imap.add('INBOX', page.raw(subject='one'))
    got = _ok(page.routes.check_account(body={'scope': 'work'}))
    assert got['works'] and got['reading'] == {'ok': True, 'count': 1} and got['sending'] == {'ok': True}
    assert page.logins[-1]['app_password'] == 'pw'            # a blank password field means the stored one
    page.smtp.send_message.assert_not_called()                # no mail is sent by a test

    def wrong_port(creds, timeout=None, **kw):
        raise ConnectionRefusedError(111, 'Connection refused')
    monkeypatch.setattr(page.accounts, 'smtp_connect', wrong_port)
    got = _ok(page.routes.check_account(body={'scope': 'work', 'smtp_port': 2525, 'app_password': 'typed'}))
    assert not got['works'] and got['reading']['ok'] and not got['sending']['ok']
    assert 'port 2525' in got['sending']['words'] and page.logins[-1]['app_password'] == 'typed'


def test_the_test_shows_an_unverified_certificate(page, stored, monkeypatch):
    info = {'host': 'imap.x.com', 'port': 993, 'which': 'imap', 'reason': 'it signed its own certificate',
            'changed': False, 'fingerprint': PRINT}

    def untrusted(creds, timeout=None):
        raise page.accounts.CertUntrusted('imap.x.com could not prove it is the right mail server.', info)
    monkeypatch.setattr(page.accounts, 'imap_connect', untrusted)
    got = _ok(page.routes.check_account(body={'scope': 'work'}))
    assert not got['works'] and got['certs'] == [info] and 'could not prove' in got['reading']['words']


def test_a_new_account_is_tested_from_what_is_typed(page, stored):
    assert 'the password' in _refusal(page.routes.check_account(body={
        'scope': 'new', 'address': 'a@b.org', 'imap_server': 'imap.b.org', 'smtp_server': 'smtp.b.org'}))
    assert 'the email address' in _refusal(page.routes.check_account(body={'scope': 'new'}))
    got = _ok(page.routes.check_account(body={'scope': 'new', 'address': 'a@b.org', 'app_password': 'secret',
                                              'imap_server': 'imap.b.org', 'smtp_server': 'smtp.b.org'}))
    assert got['works'] and page.logins[-1]['imap_server'] == 'imap.b.org' and page.logins[-1]['imap_port'] == 993


# -- the tool names a toolset from before 2.0 still lists -----------------------------

def test_archive_and_delete_still_answer_by_their_old_names(mail):
    a = mail.imap.add('INBOX', mail.raw(subject='keep'))
    b = mail.imap.add('INBOX', mail.raw(subject='bin'))
    text, ok = mail.tool.execute('archive_emails', {'ids': [f'inbox:{a}']}, None)
    assert ok and 'rchive' in text
    text, ok = mail.tool.execute('delete_emails', {'ids': [f'inbox:{b}']}, None)
    assert ok and 'Trash' in text
    assert mail.imap.boxes['INBOX'] == {}
    text, ok = mail.tool.execute('delete_emails', {'indices': [1]}, None)       # the old numbers are still refused
    assert not ok and 'by id now' in text


def test_every_tool_listed_is_defined_and_handled(mail):
    names = [t['function']['name'] for t in mail.tool.TOOLS]
    assert sorted(names) == sorted(mail.tool.AVAILABLE_FUNCTIONS) and len(set(names)) == len(names)
    assert {'archive_emails', 'delete_emails', 'move_emails', 'mark_emails'} <= set(names)
    for name in names:
        text, _ = mail.tool.execute(name, {}, None)
        assert 'Unknown email function' not in text
