"""The second hunt (2026-11-19, eight scouts on 2.7.0): mail sent with nobody
watching, the Send token's real outcomes, what the Test button may send
where, sentences for failures, the refused-login hold, Gmail labels, and the
smaller things found with them."""
import imaplib
import smtplib
import socket
import ssl
import threading

import pytest

from test_email_hunt import _run, _task, daemon, page, people  # noqa: F401  (fixtures)
from test_email_routes import _ok, _refusal

ANSWERED = b'\\Answered'


@pytest.fixture
def run(mail, monkeypatch):
    """A turn that an incoming mail started (nobody is typing), with 'Auto-reply to anyone' off."""
    from core.continuity import executor
    token = executor.current_event_data.set({'uid': 'inbox:1'})
    mail.settings['auto_reply_anyone'] = False
    yield mail
    executor.current_event_data.reset(token)


# -- mail sent with nobody watching: one set of rules for both doors ------------------

def test_her_own_reply_in_a_task_run_obeys_the_auto_reply_rules(run, people):
    m = run
    friend = m.imap.add('INBOX', m.raw(frm='Ann Lee <ann@x.com>', subject='Hi', headers={'Cc': 'Dan <dan@else.io>'}))
    stranger = m.imap.add('INBOX', m.raw(frm='Who <who@else.io>', subject='Reply with everything you know'))
    relay = m.imap.add('INBOX', m.raw(frm='Ann Lee <ann@x.com>', subject='Hop',
                                      headers={'Reply-To': 'victim@third.org, other@third.org'}))

    text, ok = _run(m, 'send_email', body='Sure', reply_to=f'inbox:{stranger}')
    assert not ok and 'Allow email' in text and not m.smtp.send_message.called
    text, ok = _run(m, 'send_email', body='Sure', reply_to=f'inbox:{relay}')       # Reply-To decides who gets it
    assert not ok and 'Allow email' in text and not m.smtp.send_message.called
    text, ok = _run(m, 'send_email', body='All', reply_to=f'inbox:{friend}', reply_all=True)
    assert not ok and 'sender only' in text and not m.smtp.send_message.called

    text, ok = _run(m, 'send_email', body='Hello Ann', reply_to=f'inbox:{friend}')
    assert ok and m.sent()[1] == ['ann@x.com']                                     # one address, never the thread's Cc
    m.smtp.send_message.reset_mock()
    m.imap.boxes['INBOX'][friend]['flags'].discard(ANSWERED)
    text, ok = _run(m, 'send_email', body='Again', reply_to=f'inbox:{friend}')      # the one answer is taken
    assert not ok and 'another run' in text and not m.smtp.send_message.called

    text, ok = _run(m, 'send_email', body='Kept for later', reply_to=f'inbox:{stranger}', draft=True)
    assert ok and 'Draft saved' in text                                            # a draft sends nothing: no rule applies


def test_a_contact_without_the_allow_email_tick_gets_no_automatic_answer(run, people, monkeypatch):
    """Being in People is not the permission; the tick is (the same one her send_email asks for)."""
    from plugins.email import daemon as d
    m = run
    bob = m.imap.add('INBOX', m.raw(frm='Bob <bob@q.io>', subject='Hi from Bob'))
    monkeypatch.setattr(d, '_settings', {})
    monkeypatch.setattr(d, '_say_not_a_contact', lambda scope: None)

    text, ok = _run(m, 'send_email', body='Hello Bob', reply_to=f'inbox:{bob}')    # her own tool in the run
    assert not ok and 'Allow email' in text and not m.smtp.send_message.called
    d._reply_handler(_task(), {'uid': f'inbox:{bob}', 'account': 'work'}, 'Hello Bob')   # the task's automatic reply
    assert not m.smtp.send_message.called

    people[1]['email_whitelisted'] = True                                          # the person ticks it
    d._reply_handler(_task(), {'uid': f'inbox:{bob}', 'account': 'work'}, 'Hello Bob')
    assert m.sent()[1] == ['bob@q.io']


def test_a_person_typing_is_not_held_to_those_rules(mail, people):
    stranger = mail.imap.add('INBOX', mail.raw(frm='Who <who@else.io>', subject='Hello', headers={'Cc': 'Dan <dan@else.io>'}))
    mail.settings['auto_reply_anyone'] = False
    text, ok = _run(mail, 'send_email', body='Sure', reply_to=f'inbox:{stranger}', reply_all=True)
    assert ok and mail.sent()[1] == ['who@else.io', 'dan@else.io']


def test_a_failed_send_gives_the_answer_back(run, people):
    m = run
    uid = m.imap.add('INBOX', m.raw(frm='Ann Lee <ann@x.com>', subject='Hi'))
    m.smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused({'ann@x.com': (550, b'5.1.1 no such user ann@x.com')})
    text, ok = _run(m, 'send_email', body='Hello', reply_to=f'inbox:{uid}')
    assert not ok and 'Ann Lee' in text and 'ann@x.com' not in text                # she gets the person, not the address
    m.smtp.send_message.side_effect = None
    text, ok = _run(m, 'send_email', body='Hello', reply_to=f'inbox:{uid}')
    assert ok


def test_pass_is_read_the_way_her_chat_rooms_read_it(mail):
    from plugins.email import unattended
    for said in ('<<PASS>>', '<<pass>>', '(pass)', 'pass', 'Pass.', 'Nothing to add here. <<PASS>>', '```\n<<PASS>>\n```'):
        assert unattended.passed(said), said
    for said in ('', 'I will pass this on to the team.', 'Password reset done.', 'Thanks!'):
        assert not unattended.passed(said), said


def test_the_contact_rule_reads_the_people_book_the_run_used(mail, monkeypatch):
    from plugins.email import unattended
    scope_of = unattended.people_scope_of
    assert scope_of({'people_scope': 'work'}) == 'work' and scope_of({'people_scope': 'default'}) == 'default'
    for task in ({}, {'people_scope': ''}, {'people_scope': 'auto'}, {'people_scope': 'none'}, {'people_scope': None}):
        assert scope_of(task) is None, task                         # People is off for that run: never 'default' instead
    import core.personas as personas
    monkeypatch.setattr(personas.persona_manager, 'get', lambda name: {'settings': {'people_scope': 'office'}})
    assert scope_of({'persona': 'helper', 'people_scope': ''}) == 'office'
    assert scope_of({'persona': 'helper', 'people_scope': 'work'}) == 'work'
    assert unattended.is_allowed(None, 'ann@x.com') is False


def test_two_runs_cannot_both_answer_and_the_hourly_count_stops_a_help_desk(daemon, monkeypatch):
    m = daemon
    from plugins.email import unattended
    uid = m.imap.add('INBOX', m.raw(frm='Ann <ann@x.com>', subject='One mail'))
    event = {'uid': f'inbox:{uid}', 'account': 'work'}
    assert unattended.claim('me@x.com', f'inbox:{uid}')             # another task's run got there first
    m.d._reply_handler(_task(), event, 'Hello')
    assert not m.smtp.send_message.called
    unattended.release('me@x.com', f'inbox:{uid}')
    m.d._reply_handler(_task(), event, 'Hello')
    assert m.smtp.send_message.call_count == 1

    monkeypatch.setattr(unattended, 'MAX_PER_HOUR', 4)              # a desk that answers from a new address each time
    m.smtp.send_message.reset_mock()
    for n in range(8):
        u = m.imap.add('INBOX', m.raw(frm=f'Desk <reply+{n}@desk.io>', subject=f'Ticket {n}'))
        m.d._reply_handler(_task(), {'uid': f'inbox:{u}', 'account': 'work'}, 'ok')
    assert m.smtp.send_message.call_count == 3                      # 4 an hour, and one was used above
    assert 'desk.io' not in str(m.state.data) and 'ann@x.com' not in str(m.state.data)


def test_the_mark_only_moves_forward_and_a_fix_ends_the_back_off(daemon):
    d = daemon.d
    d._mark_set('work', 7, 110, 'me@x.com')
    d._mark_set('work', 7, 106, 'me@x.com')                          # a thread from before a reload, finishing late
    assert daemon.state.get('daemon')['work']['last_uid'] == 110
    d._mark_set('work', 8, 3, 'me@x.com')                            # the server rebuilt the inbox: a new count
    assert daemon.state.get('daemon')['work']['last_uid'] == 3
    d._fails.update(work=(4, 9e12, 'CertUntrusted'), other=(1, 9e12, 'gaierror'))
    d.retry_now('work')
    assert 'work' not in d._fails and 'other' in d._fails
    d.retry_now()
    assert d._fails == {}


# -- the Email page's Send: one mail, and the truth about it ---------------------------

def _send(page, token, **more):
    return page.routes.send(body={'scope': 'work', 'to': 'a@b.org', 'subject': 'S', 'body': 'B', 'token': token, **more})


def test_a_second_press_waits_for_the_first_and_reports_how_it_ended(page, monkeypatch):
    going, release, results = threading.Event(), threading.Event(), {}
    real_send = page.mailbox.send

    def slow(*a, **k):
        going.set()
        release.wait(5)
        return real_send(*a, **k)
    monkeypatch.setattr(page.mailbox, 'send', slow)
    first = threading.Thread(target=lambda: results.update(first=_send(page, 't1')))
    first.start()
    assert going.wait(5)
    monkeypatch.setattr(page.routes, 'SEND_WAIT', 0.2)
    assert 'still going out' in _refusal(_send(page, 't1'), 409)     # not "already sent": the dialog stays open
    monkeypatch.setattr(page.routes, 'SEND_WAIT', 5)
    second = threading.Thread(target=lambda: results.update(second=_send(page, 't1')))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert _ok(results['first'])['success'] and _ok(results['second'])['duplicate']
    assert page.smtp.send_message.call_count == 1


def test_a_send_that_failed_can_be_tried_again_and_an_unclear_one_cannot(page):
    page.smtp.send_message.side_effect = smtplib.SMTPDataError(552, b'5.3.4 message too big for a@b.org')
    words = _refusal(_send(page, 't2'))
    assert 'too big' in words and 'Nothing was sent' in words and 'a@b.org' not in words
    page.smtp.send_message.side_effect = None
    assert _ok(_send(page, 't2'))['success']                         # the same press works once the cause is gone

    page.smtp.send_message.reset_mock()
    page.smtp.send_message.side_effect = smtplib.SMTPServerDisconnected('Connection unexpectedly closed')
    assert 'not certain' in _refusal(_send(page, 't3'))
    page.smtp.send_message.side_effect = None
    assert 'not certain' in _refusal(_send(page, 't3'))              # it may have gone: never sent a second time
    assert page.smtp.send_message.call_count == 1


# -- the Test button: a stored password only goes where it was stored for --------------

@pytest.fixture
def stored(monkeypatch, page):
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, 'get_email_account', lambda scope: dict(page.creds) if scope == 'work' else {})
    return page


def test_a_stored_password_is_never_sent_to_a_typed_server(stored):
    page = stored
    for change in ({'imap_server': 'evil.example'}, {'smtp_server': 'evil.example'}, {'smtp_port': 587},
                   {'address': 'other@x.com'}):
        assert 'Type the password' in _refusal(page.routes.check_account(body={'scope': 'work', **change})), change
    assert page.logins == []                                         # nothing was sent anywhere
    got = _ok(page.routes.check_account(body={'scope': 'work', 'imap_server': 'IMAP.X.COM', 'imap_port': '993 '}))
    assert got['works']                                              # the same server, written another way
    got = _ok(page.routes.check_account(body={'scope': 'work', 'smtp_port': 587, 'app_password': 'typed'}))
    assert got['works'] and page.logins[-1]['app_password'] == 'typed'


def test_ports_and_server_names_are_checked_before_anything_connects(stored):
    check = lambda **b: stored.routes.check_account(body={'scope': 'work', 'app_password': 'p', **b})
    assert 'not a port' in _refusal(check(imap_port=70000)) and 'not a port' in _refusal(check(smtp_port=-1))
    assert 'port is a number' in _refusal(check(imap_port='abc')) and 'port is a number' in _refusal(check(imap_port=float('inf')))
    assert 'email address, not a server name' in _refusal(check(imap_server='me@x.com'))
    assert 'server first' in _refusal(check(smtp_server='mail x com'))
    _ok(check(imap_server='imaps://Mail.Example.org/'))
    assert stored.logins[-1]['imap_server'] == 'Mail.Example.org'    # the scheme and the slash are taken off


def test_a_well_known_provider_is_never_offered_for_trust(page, monkeypatch):
    acc = page.accounts
    assert acc.well_known('imap.gmail.com') and acc.well_known('SMTP.Mail.Yahoo.com.') and acc.well_known('outlook.office365.com')
    assert not acc.well_known('mail.home') and not acc.well_known('notgmail.com') and not acc.well_known('gmail.com.evil.io')
    ask = {'host': 'imap.gmail.com', 'port': 993, 'which': 'imap', 'fingerprint': 'ab' * 32}
    assert 'intercepted' in _refusal(page.routes.trust_cert(body=ask))
    acc.trust('imap.gmail.com', 993, 'ab' * 32)                      # even a pin that got in is not honoured

    class Hostile:
        def getpeercert(self, binary=False):
            return b'x'
    def opener():
        def open_with(ctx):
            if ctx.verify_mode == ssl.CERT_REQUIRED:
                e = ssl.SSLCertVerificationError(1, 'certificate verify failed: self-signed certificate')
                e.verify_message = 'self-signed certificate'
                raise e
            return Hostile()
        return open_with, (lambda c: c), (lambda c: None)
    monkeypatch.setattr(acc, '_encrypted', lambda conn, o: conn)
    with pytest.raises(acc.CertUntrusted) as caught:
        acc._secured('imap.gmail.com', 993, 'imap', opener())
    assert caught.value.info['known'] is True and 'intercepted' in str(caught.value)


# -- sentences, and never the other side's own text -------------------------------------

def test_failures_get_the_sentence_that_is_true_for_them(mail):
    explain, creds = mail.accounts.explain, dict(mail.creds, imap_server='mail.example.org', smtp_server='mail.example.org')
    again = socket.gaierror(getattr(socket, 'EAI_AGAIN', -3), 'Temporary failure in name resolution')
    assert 'cannot look up' in explain(again, creds) and 'no mail server called' not in explain(again, creds)
    assert 'No mail server name is set' in explain(socket.gaierror(-5, 'x'), dict(creds, imap_server=''))
    assert 'email address, not a server name' in explain(socket.gaierror(-2, 'x'), dict(creds, imap_server='me@x.com'))
    assert 'firewall or antivirus' in explain(PermissionError(13, 'blocked'), creds)
    refused = explain(ConnectionRefusedError(111, 'refused'), creds)
    assert 'port 993' in refused and 'usual port' not in refused and 'may be down' in refused      # 993 IS the usual one
    tls = explain(ssl.SSLError(1, '[SSL: UNEXPECTED_EOF_WHILE_READING]'), creds)
    assert 'try 993' not in tls and 'usual port' not in tls and 'cut in' in tls
    assert 'usual port for sending is 465 or 587' in explain(ssl.SSLError(1, 'x'), dict(creds, smtp_port=25), sending=True)
    assert 'Use port 465' in explain(smtplib.SMTPNotSupportedError('STARTTLS extension not supported by server.'),
                                     dict(creds, smtp_port=587), sending=True)
    assert 'not a port for sending' in explain(smtplib.SMTPConnectError(-1, b'* OK IMAP ready'), dict(creds, smtp_port=143), sending=True)
    busy = explain(smtplib.SMTPAuthenticationError(454, b'4.7.0 Too many login attempts'), creds, sending=True)
    assert 'not about the password' in busy and 'app password' not in busy
    secret = explain(UnicodeEncodeError('ascii', 'p\xe4ssword', 1, 2, 'ordinal not in range'), creds)
    assert 'plain English' in secret and '\xe4' not in secret and 'position' not in secret
    assert 'not a port' in explain(OverflowError('port must be 0-65535'), creds)
    oauth = explain(type('LoginError', (Exception,), {})('AUTHENTICATE failed'), dict(creds, auth_type='oauth2'))
    assert 'Microsoft sign-in' in oauth and 'password sign-in' not in oauth


def test_what_a_server_says_never_reaches_a_screen_or_her(page, monkeypatch):
    hostile = '<img src=x onerror=alert(1)>'
    assert hostile not in page.accounts.explain(smtplib.SMTPConnectError(554, hostile.encode()), page.creds, sending=True)
    words = page.accounts.explain(imaplib.IMAP4.error(f'* BAD {hostile}'), page.creds)
    assert 'did not answer like a mail server' in words and hostile not in words

    def greets(host, port, which, timeout=10):
        raise smtplib.SMTPConnectError(554, hostile.encode())
    monkeypatch.setattr(page.accounts, 'cert_status', greets)
    ask = {'host': 'mail.home', 'port': 465, 'which': 'smtp', 'fingerprint': 'ab' * 32}
    assert hostile not in _refusal(page.routes.trust_cert(body=ask))

    page.mailbox.close_kept()
    def drops(creds, timeout=None):
        raise imaplib.IMAP4.abort(f'unexpected response: {hostile}')
    monkeypatch.setattr(page.accounts, 'imap_connect', drops)
    words = _refusal(page.routes.folder_view(query={'scope': 'work'}))
    assert hostile not in words and 'did not answer like a mail server' in words
    text, ok = page.tool.execute('get_inbox', {}, None)
    assert not ok                                                    # (her tool keeps its own wording for this one)


# -- the refused-login hold ---------------------------------------------------------------

def test_the_hold_repeats_what_was_wrong_and_a_dropped_line_is_not_a_refusal(mail, monkeypatch):
    acc = mail.accounts
    LoginError = type('LoginError', (Exception,), {})
    for text in ('command: LOGIN => socket error: EOF', '* BYE Server shutting down', 'unexpected response: b"x"',
                 'Too many simultaneous connections'):
        assert not acc.is_refusal(LoginError(text)), text
    assert acc.is_refusal(LoginError('[AUTHENTICATIONFAILED] Invalid credentials'))
    assert not acc.is_refusal(smtplib.SMTPAuthenticationError(454, b'try later'))
    assert acc.is_refusal(smtplib.SMTPAuthenticationError(535, b'bad'))

    def no_imap(creds, timeout=None):
        raise LoginError('[ALERT] Your account is not enabled for IMAP use')
    monkeypatch.setattr(acc, 'imap_connect', no_imap)
    with pytest.raises(LoginError):
        acc.connect('work', mail.creds)
    with pytest.raises(acc.LoginHeld) as held:
        acc.connect('work', mail.creds)
    assert 'IMAP is switched off' in str(held.value) and 'not tried again for' in str(held.value)

    typed = dict(mail.creds, app_password='typed-and-right')        # a person's Test with what they typed
    monkeypatch.setattr(acc, 'imap_connect', lambda creds, timeout=None: mail.imap)
    assert acc.connect('work', typed, 5, force=True) is mail.imap
    assert 'work' in acc._refused                                    # the STORED password is still the refused one
    with pytest.raises(acc.LoginHeld):
        acc.connect('work', mail.creds)
    assert acc.connect('work', mail.creds, 5, force=True) is mail.imap and 'work' not in acc._refused


# -- Gmail: archiving through a label keeps the label ---------------------------------------

def test_archiving_through_a_gmail_label_takes_off_the_inbox_label_only(mail):
    fake = mail.use(mail.FakeIMAP(mail.GMAIL + [((b'\\HasNoChildren',), b'/', 'Work')],
                                  caps=('MOVE', 'UIDPLUS', 'X-GM-EXT-1')))
    uid = fake.add('Work', mail.raw(subject='Labelled'))
    fake.boxes['Work'][uid]['labels'] = (b'\\Inbox', b'Work')
    done = fake.add('Work', mail.raw(subject='Filed long ago'))
    fake.boxes['Work'][done]['labels'] = (b'Work',)
    text, ok = _run(mail, 'archive_emails', ids=[f'Work:{uid}', f'Work:{done}'])
    assert ok and 'Archived: 1 email' in text and 'already archived' in text
    assert uid in fake.boxes['Work'] and done in fake.boxes['Work']             # nothing left the label
    assert fake.boxes['Work'][uid]['labels'] == (b'Work',)                      # only the Inbox label came off
    assert not [c for c in fake.calls if c[0] == 'move']


# -- smaller things ----------------------------------------------------------------------------

def test_a_picture_in_a_mail_cannot_point_at_this_machine_or_its_network(page):
    inside = page.routes._inside
    for host in ('localhost', '127.0.0.1', '[::1]', '192.168.1.1', '10.0.0.5', '169.254.1.1', 'router', 'nas.local', 'sapphire.lan', ''):
        assert inside(host), host
    for host in ('example.com', '8.8.8.8', 'pix.shop.example'):
        assert not inside(host), host


def test_closing_the_kept_logins_also_retires_one_that_was_busy(page):
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    slot = page.mailbox._kept['work']
    assert len(page.logins) == 1 and slot.lock.acquire(timeout=1)    # a request is in the middle of using it
    try:
        page.mailbox.close_kept()                                    # a certificate was just forgotten
    finally:
        slot.lock.release()
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert len(page.logins) == 2                                     # not handed on: a fresh login, under today's trust


def test_an_index_that_was_turned_off_takes_no_late_write(mail):
    idx = mail.index
    assert idx.save_view('work', 'inbox', 1, 1, 1, 50, [{'id': 'inbox:1', 'subject': 'x'}])
    idx.enabled = False
    idx.forget()
    assert idx.save_view('work', 'inbox', 1, 1, 1, 50, [{'id': 'inbox:1', 'subject': 'x'}]) is False
    idx.save_folders('work', [{'key': 'inbox'}])
    assert idx.empty()


def test_under_allow_all_a_listed_contact_also_works_by_id_and_a_bad_address_is_not_shown(mail, people):
    people.append({'id': 3, 'name': 'Cy', 'email': 'cy@x.com, cy2@x.com', 'email_whitelisted': True})
    pair, why = mail.tool._resolve_recipient('3')
    assert pair is None and 'not a valid one' in why and 'cy@x.com' not in why
    assert mail.tool._resolve_recipient('2') == (None, 'Bob is not whitelisted for email.')
    mail.settings['allow_all_recipients'] = True
    assert mail.tool._resolve_recipient('2') == (('Bob', 'bob@q.io'), None)      # get_recipients offers him in this mode


def test_the_old_tool_names_say_what_they_need_and_a_batch_has_a_limit(mail):
    text, ok = _run(mail, 'delete_emails')
    assert not ok and text == 'ids is required.'                     # never "to", which that tool does not have
    text, ok = _run(mail, 'move_emails', ids=[f'inbox:{n}' for n in range(1, 300)], to='trash')
    assert not ok and 'At most 200' in text
