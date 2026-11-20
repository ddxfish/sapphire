"""The Email page's doors (routes/mail.py) on the in-memory mail server."""
import email
import email.policy
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

SEEN, FLAGGED, ANSWERED = b'\\Seen', b'\\Flagged', b'\\Answered'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 40


@pytest.fixture
def page(mail):
    import plugins.email.routes.mail as routes
    mail.routes = routes
    return mail


def _ok(result):
    """A door's answer when it worked (a refusal is a (dict, status) tuple)."""
    assert not isinstance(result, tuple), result
    return result


def _refusal(result, status=400):
    assert isinstance(result, tuple), result
    body, code = result
    assert code == status and body['success'] is False and body['detail']
    assert 'Traceback' not in body['detail']
    return body['detail']


# -- the folder view -------------------------------------------------------------

def test_folder_view_rows_counts_and_her_mark_untouched(page):
    page.imap.add('INBOX', page.raw(subject='Oldest'), flags=[SEEN])
    page.imap.add('INBOX', page.raw(frm='Bob <bob@y.org>', subject='Middle', files=[('a.txt', 'text/plain', b'hi')]))
    page.imap.add('INBOX', page.raw(subject='Newest'), flags=[FLAGGED, ANSWERED])
    page.imap.add('Junk', page.raw(subject='Win a prize'))

    got = _ok(page.routes.folder_view(query={'scope': 'work'}))
    assert got['folder'] == 'inbox' and got['total'] == 3 and got['pages'] == 1
    assert got['account'] == {'scope': 'work', 'address': 'me@x.com'}
    assert [r['subject'] for r in got['rows']] == ['Newest', 'Middle', 'Oldest']
    newest, middle, oldest = got['rows']
    assert newest['id'] == 'inbox:3' and newest['starred'] and newest['answered'] and newest['unread']
    assert middle['from'] == [{'name': 'Bob', 'addr': 'bob@y.org'}] and middle['attach']
    assert middle['to'] == [{'name': 'me', 'addr': 'me@x.com'}]
    assert not oldest['unread'] and isinstance(oldest['ts'], int)

    folders = {f['key']: f for f in got['folders']}
    assert [f['key'] for f in got['folders']] == ['inbox', 'sent', 'drafts', 'spam', 'trash', 'Receipts']
    assert folders['inbox']['unread'] == 2 and folders['inbox']['total'] == 3
    assert folders['spam'] == {'key': 'spam', 'label': 'Spam', 'role': 'spam', 'total': 1, 'unread': 1}
    assert folders['Receipts']['role'] == '' and folders['Receipts']['label'] == 'Receipts'
    assert got['validity'] == 1
    assert page.state.data == {}          # a person browsing never moves her validity mark


def test_folder_view_pages_by_position(page):
    for n in range(1, 121):
        page.imap.add('INBOX', page.raw(subject=f'Mail {n}'))
    first = _ok(page.routes.folder_view(query={'scope': 'work', 'folder': 'inbox'}))
    assert first['total'] == 120 and first['pages'] == 3 and len(first['rows']) == 50
    assert first['rows'][0]['subject'] == 'Mail 120' and first['rows'][-1]['subject'] == 'Mail 71'
    assert ('search', ['71:120'], None) in page.imap.calls       # never every uid of the folder

    last = _ok(page.routes.folder_view(query={'scope': 'work', 'page': '3', 'counts': '0'}))
    assert [r['subject'] for r in last['rows']][0] == 'Mail 20' and len(last['rows']) == 20
    assert all(f['total'] is None for f in last['folders'])      # counts=0 skips the STATUS round

    past = _ok(page.routes.folder_view(query={'scope': 'work', 'page': '9'}))
    assert past['rows'] == [] and past['total'] == 120


def test_folder_view_search_and_unread_only(page):
    page.imap.add('INBOX', page.raw(subject='Invoice March', body='please pay'), flags=[SEEN])
    page.imap.add('INBOX', page.raw(subject='Lunch', body='the invoice is attached'))
    page.imap.add('INBOX', page.raw(subject='Hello', body='nothing here'))

    found = _ok(page.routes.folder_view(query={'scope': 'work', 'q': 'invoice'}))
    assert [r['subject'] for r in found['rows']] == ['Lunch', 'Invoice March'] and found['total'] == 2
    both = _ok(page.routes.folder_view(query={'scope': 'work', 'q': 'invoice', 'unread': '1'}))
    assert [r['subject'] for r in both['rows']] == ['Lunch']
    unread = _ok(page.routes.folder_view(query={'scope': 'work', 'unread': 'true'}))
    assert [r['subject'] for r in unread['rows']] == ['Hello', 'Lunch']
    accent = _ok(page.routes.folder_view(query={'scope': 'work', 'q': 'caf\u00e9'}))
    assert accent['rows'] == [] and page.imap.calls[-1][2] == 'UTF-8'


def test_folder_view_other_folders_and_sent(page):
    page.imap.add('Sent', page.raw(frm='me@x.com', to='Carol <carol@z.net>', subject='Out'), flags=[SEEN])
    page.imap.add('Receipts', page.raw(subject='Receipt 1'))
    sent = _ok(page.routes.folder_view(query={'scope': 'work', 'folder': 'sent'}))
    assert sent['folder'] == 'sent' and sent['rows'][0]['to'] == [{'name': 'Carol', 'addr': 'carol@z.net'}]
    custom = _ok(page.routes.folder_view(query={'scope': 'work', 'folder': 'Receipts'}))
    assert custom['folder'] == 'Receipts' and custom['rows'][0]['id'] == 'Receipts:2'


def test_folder_view_refusals_are_words(page, monkeypatch):
    assert 'account' in _refusal(page.routes.folder_view(query={}))
    assert 'No folder named' in _refusal(page.routes.folder_view(query={'scope': 'work', 'folder': 'Nope'}))

    page.mailbox.close_kept()                  # the page keeps its login: drop it so the next request must connect

    def boom(creds):
        raise RuntimeError("socket fell over")
    monkeypatch.setattr(page.accounts, 'imap_connect', boom)
    assert 'socket fell over' in _refusal(page.routes.folder_view(query={'scope': 'work'}), 500)

    class LoginError(Exception):
        pass

    def refused(creds):
        raise LoginError("bad password")
    monkeypatch.setattr(page.accounts, 'imap_connect', refused)
    assert 'refused the login' in _refusal(page.routes.folder_view(query={'scope': 'work'}))


# -- reading ---------------------------------------------------------------------

def test_message_marks_read_only_when_asked(page):
    uid = page.imap.add('INBOX', page.raw(frm='Alice <alice@x.com>', to='me@x.com, Dan <dan@q.io>',
                                          cc='Eve <eve@q.io>', subject='Plans', body='See you at 5'))
    peek = _ok(page.routes.get_message(query={'scope': 'work', 'id': f'inbox:{uid}', 'validity': '1'}))
    assert peek['unread'] and SEEN not in page.imap.boxes['INBOX'][uid]['flags']

    got = _ok(page.routes.get_message(query={'scope': 'work', 'id': f'inbox:{uid}', 'validity': '1', 'read': '1'}))
    assert SEEN in page.imap.boxes['INBOX'][uid]['flags'] and got['unread'] is False
    assert got['subject'] == 'Plans' and got['text'] == 'See you at 5' and got['has_plain']
    assert got['from'] == [{'name': 'Alice', 'addr': 'alice@x.com'}]
    assert {'name': 'Dan', 'addr': 'dan@q.io'} in got['to'] and got['cc'] == [{'name': 'Eve', 'addr': 'eve@q.io'}]
    assert got['html'] is None and got['attachments'] == [] and isinstance(got['ts'], int)
    assert got['reply']['to'] == [{'name': 'Alice', 'addr': 'alice@x.com'}]
    assert [p['addr'] for p in got['reply']['cc_all']] == ['dan@q.io', 'eve@q.io']     # never ourselves
    assert page.state.data == {}


def test_message_html_is_made_safe(page):
    msg = EmailMessage(policy=email.policy.SMTP)
    msg['From'], msg['To'], msg['Subject'] = 'News <news@shop.example>', 'me@x.com', 'Sale'
    msg['Date'] = 'Tue, 17 Nov 2026 10:00:00 -0500'
    msg.set_content('Plain words')
    msg.add_alternative(
        '<html><head><title>t</title><script>steal()</script></head><body onload="x()">'
        '<style>.a{background:url(/api/css-one)} .b{background:url("https://pix.example/b.png")}</style>'
        '<p onclick="steal()" style="background:url(\'/api/css-two\')">Hi <b>there</b></p>'
        '<p style="background:u\\72l(/api/css-three)">escaped</p>'
        '<img src="cid:pic1"><img src="https://track.example/p.gif"><img src="/api/secret">'
        '<img src="https://app.example/api/thing"><img src="//cdn.example/x.png">'
        '<a href="javascript:alert(1)">bad</a><a href="https://shop.example/sale">shop</a>'
        '<a href="mailto:help@shop.example">mail</a>'
        '<form action="https://evil.example"><input name="pw"></form>'
        '<iframe src="https://evil.example"></iframe></body></html>', subtype='html')
    msg.get_payload()[1].add_related(PNG, 'image', 'png', cid='<pic1>')
    uid = page.imap.add('INBOX', msg.as_bytes())

    request = SimpleNamespace(url=SimpleNamespace(hostname='app.example'))
    got = _ok(page.routes.get_message(query={'scope': 'work', 'id': f'inbox:{uid}'}, request=request))
    html = got['html']
    for gone in ('script', 'steal', 'onclick', 'onload', '<form', '<input', '<iframe', 'javascript:',
                 '/api/secret', 'app.example', 'cdn.example', '<title', 'css-one', 'css-two', 'css-three'):
        assert gone not in html, gone
    assert 'src="data:image/png;base64,' in html                      # the attached picture is embedded
    assert 'https://track.example/p.gif' in html and got['remote']    # kept, and the page is told
    assert 'url("https://pix.example/b.png")' in html                 # an outside picture in CSS is kept too
    assert 'href="https://shop.example/sale"' in html and 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html and 'mailto:help@shop.example' in html
    assert got['text'] == 'Plain words' and got['has_plain']


def test_message_html_only_and_no_remote(page):
    uid = page.imap.add('INBOX', page.raw(body=None, html='<p>Only <i>formatted</i></p>'))
    got = _ok(page.routes.get_message(query={'scope': 'work', 'id': f'inbox:{uid}'}))
    assert got['has_plain'] is False and 'Only formatted' in got['text']
    assert got['html'] == '<p>Only <i>formatted</i></p>' and got['remote'] is False


def test_message_refusals(page):
    assert 'not an email id' in _refusal(page.routes.get_message(query={'scope': 'work', 'id': 'nonsense'}))
    assert 'No email' in _refusal(page.routes.get_message(query={'scope': 'work', 'id': 'inbox:999'}))


# -- attachments -----------------------------------------------------------------

def test_attachment_is_always_a_download(page):
    uid = page.imap.add('INBOX', page.raw(files=[('re"port/..\\q1.html', 'text/html', b'<script>x</script>'),
                                                 ('caf\u00e9.pdf', 'application/pdf', b'%PDF-1.4')]))
    seen = _ok(page.routes.get_message(query={'scope': 'work', 'id': f'inbox:{uid}'}))
    assert [(a['n'], a['name'], a['size']) for a in seen['attachments']] == [(1, 're_port_.._q1.html', 18),
                                                                           (2, 'caf\u00e9.pdf', 8)]
    res = page.routes.get_attachment(query={'scope': 'work', 'id': f'inbox:{uid}', 'n': '1'})
    assert res.body == b'<script>x</script>'
    assert res.media_type == 'application/octet-stream'           # never a type the browser would open
    assert res.headers['content-disposition'].startswith('attachment; filename="re_port_.._q1.html"')
    assert res.headers['x-content-type-options'] == 'nosniff'
    second = page.routes.get_attachment(query={'scope': 'work', 'id': f'inbox:{uid}', 'n': '2'})
    assert "filename*=UTF-8''caf%C3%A9.pdf" in second.headers['content-disposition']
    assert SEEN not in page.imap.boxes['INBOX'][uid]['flags']     # a download is not a read

    assert 'no attachment 3' in _refusal(page.routes.get_attachment(query={'scope': 'work', 'id': f'inbox:{uid}', 'n': '3'}))
    plain = page.imap.add('INBOX', page.raw())
    assert 'no attachments' in _refusal(page.routes.get_attachment(query={'scope': 'work', 'id': f'inbox:{plain}', 'n': '1'}))


# -- moving and marking ----------------------------------------------------------

def test_move_to_spam_and_back(page):
    a = page.imap.add('INBOX', page.raw(subject='Junk mail'))
    b = page.imap.add('INBOX', page.raw(subject='Keep me'))
    got = _ok(page.routes.move(body={'scope': 'work', 'ids': [f'inbox:{a}', 'inbox:999'], 'to': 'spam', 'validity': 1}))
    assert got['success'] and got['moved'] == [f'inbox:{a}']
    assert got['skipped'] == [{'id': 'inbox:999', 'why': 'no longer there'}]
    assert page.imap.subjects('Junk') == ['Junk mail'] and page.imap.subjects('INBOX') == ['Keep me']

    back = _ok(page.routes.move(body={'scope': 'work', 'ids': f'spam:{a}', 'to': 'inbox'}))
    assert back['moved'] == [f'spam:{a}'] and sorted(page.imap.subjects('INBOX')) == ['Junk mail', 'Keep me']
    _ok(page.routes.move(body={'scope': 'work', 'ids': [f'inbox:{b}'], 'to': 'Receipts'}))
    assert page.imap.subjects('Receipts') == ['Keep me']


def test_move_archive_is_made_when_missing_and_refusals(page):
    a = page.imap.add('INBOX', page.raw(subject='File me'))
    _ok(page.routes.move(body={'scope': 'work', 'ids': [f'inbox:{a}'], 'to': 'archive'}))
    assert page.imap.subjects('Archive') == ['File me']
    assert 'where to move' in _refusal(page.routes.move(body={'scope': 'work', 'ids': [f'archive:{a}']}))
    assert 'at least one' in _refusal(page.routes.move(body={'scope': 'work', 'to': 'trash'}))
    assert 'No folder named' in _refusal(page.routes.move(body={'scope': 'work', 'ids': [f'archive:{a}'], 'to': 'Nope'}))
    too_many = [f'inbox:{n}' for n in range(1, 300)]
    assert 'At most' in _refusal(page.routes.move(body={'scope': 'work', 'ids': too_many, 'to': 'trash'}))
    nothing = _ok(page.routes.move(body={'scope': 'work', 'ids': ['inbox:999'], 'to': 'trash'}))
    assert nothing['success'] is False and nothing['moved'] == []


def test_mark_read_unread_star(page):
    a = page.imap.add('INBOX', page.raw())
    flags = page.imap.boxes['INBOX'][a]['flags']
    assert _ok(page.routes.mark(body={'scope': 'work', 'ids': [f'inbox:{a}'], 'mark': 'read'}))['changed'] == 1
    assert SEEN in flags
    _ok(page.routes.mark(body={'scope': 'work', 'ids': [f'inbox:{a}'], 'mark': 'starred'}))
    _ok(page.routes.mark(body={'scope': 'work', 'ids': [f'inbox:{a}'], 'mark': 'unread'}))
    assert FLAGGED in flags and SEEN not in flags
    _ok(page.routes.mark(body={'scope': 'work', 'ids': [f'inbox:{a}'], 'mark': 'unstarred'}))
    assert FLAGGED not in flags
    assert 'mark must be' in _refusal(page.routes.mark(body={'scope': 'work', 'ids': [f'inbox:{a}'], 'mark': 'shiny'}))
    assert 'at least one' in _refusal(page.routes.mark(body={'scope': 'work', 'mark': 'read'}))


# -- sending ---------------------------------------------------------------------

def test_send_new_mail_to_cc_bcc(page):
    got = _ok(page.routes.send(body={
        'scope': 'work', 'mode': 'new', 'to': 'Ann Lee <ann@a.org>, bob@b.org; ann@a.org',
        'cc': ['"Doe, Carl" <carl@c.org>', 'bob@b.org'], 'bcc': 'dee@d.org',
        'subject': 'Hello\r\nBcc: sneak@e.org', 'body': 'First line\r\nSecond line'}))
    assert got == {'success': True, 'draft': False, 'note': ''}
    msg, rcpts = page.sent()
    assert rcpts == ['ann@a.org', 'bob@b.org', 'carl@c.org', 'dee@d.org']      # each once; bcc to SMTP only
    assert msg['Bcc'] is None and 'dee@d.org' not in msg.as_string()
    assert msg['Subject'] == 'Hello Bcc: sneak@e.org'                          # one header line, no injection
    assert 'Ann Lee <ann@a.org>' in str(msg['To']) and 'carl@c.org' in str(msg['Cc'])
    assert msg['From'] == 'me@x.com'
    assert page.imap.subjects('Sent') == ['Hello Bcc: sneak@e.org']            # the Sent copy


def test_send_reply_threads_quotes_and_flags_answered(page):
    uid = page.imap.add('INBOX', page.raw(frm='Alice <alice@x.com>', to='me@x.com, Dan <dan@q.io>',
                                          subject='Plans', body='See you at 5'))
    original = email.message_from_bytes(page.imap.boxes['INBOX'][uid]['raw'], policy=email.policy.default)
    _ok(page.routes.send(body={'scope': 'work', 'mode': 'reply', 'id': f'inbox:{uid}', 'validity': 1,
                               'to': 'Alice <alice@x.com>', 'cc': 'dan@q.io', 'body': 'Works for me'}))
    msg, rcpts = page.sent()
    assert rcpts == ['alice@x.com', 'dan@q.io']
    assert msg['Subject'] == 'Re: Plans' and msg['In-Reply-To'] == original['Message-ID']
    plain = msg.get_body(preferencelist=('plain',)).get_content()
    assert 'Works for me' in plain and '> See you at 5' in plain
    assert 'gmail_quote' in msg.get_body(preferencelist=('html',)).get_content()
    assert ANSWERED in page.imap.boxes['INBOX'][uid]['flags']
    assert msg['Auto-Submitted'] is None                           # a person wrote it, not the daemon


def test_send_forward_carries_attachments(page):
    uid = page.imap.add('INBOX', page.raw(subject='Report', body='Attached',
                                          files=[('q1.pdf', 'application/pdf', b'%PDF-1.4')]))
    _ok(page.routes.send(body={'scope': 'work', 'mode': 'forward', 'id': f'inbox:{uid}',
                               'to': 'ann@a.org', 'body': ''}))
    msg, rcpts = page.sent()
    assert rcpts == ['ann@a.org'] and msg['Subject'] == 'Fwd: Report'
    assert [p.get_filename() for p in msg.iter_attachments()] == ['q1.pdf']
    assert 'Forwarded message' in msg.get_body(preferencelist=('plain',)).get_content()
    assert ANSWERED not in page.imap.boxes['INBOX'][uid]['flags']


def test_send_draft_saves_and_sends_nothing(page):
    got = _ok(page.routes.send(body={'scope': 'work', 'mode': 'new', 'to': '', 'subject': '',
                                     'body': 'half a thought', 'draft': True}))
    assert got['draft'] is True and not page.smtp.send_message.called
    (draft,) = page.imap.boxes['Drafts'].values()
    assert b'\\Draft' in draft['flags'] and b'half a thought' in draft['raw']


def test_send_refusals(page):
    send = page.routes.send
    assert 'To' in _refusal(send(body={'scope': 'work', 'subject': 'x', 'body': 'y'}))
    assert 'not an email address' in _refusal(send(body={'scope': 'work', 'to': 'bob', 'subject': 'x', 'body': 'y'}))
    assert 'Cc' in _refusal(send(body={'scope': 'work', 'to': 'a@b.org', 'cc': 'x@@y', 'subject': 'x', 'body': 'y'}))
    assert 'subject' in _refusal(send(body={'scope': 'work', 'to': 'a@b.org', 'body': 'y'}))
    assert 'empty' in _refusal(send(body={'scope': 'work', 'to': 'a@b.org', 'subject': 'x', 'body': '  '}))
    assert 'mode' in _refusal(send(body={'scope': 'work', 'mode': 'shout', 'to': 'a@b.org'}))
    assert 'reply to' in _refusal(send(body={'scope': 'work', 'mode': 'reply', 'to': 'a@b.org', 'body': 'y'}))
    assert 'account' in _refusal(send(body={'to': 'a@b.org', 'subject': 'x', 'body': 'y'}))
    # Two addresses with no comma between them: named, never quietly dropped.
    assert 'a@b.org x@y.org' in _refusal(send(body={'scope': 'work', 'to': 'a@b.org x@y.org', 'subject': 'x', 'body': 'y'}))
    assert not page.smtp.send_message.called


def test_send_address_box_forgives_stray_commas(page):
    _ok(page.routes.send(body={'scope': 'work', 'to': ' a@b.org, , <c@d.org>,', 'subject': 'x', 'body': 'y'}))
    assert page.sent()[1] == ['a@b.org', 'c@d.org']


def test_send_smtp_failures_are_words(page):
    import smtplib
    page.smtp.send_message.side_effect = smtplib.SMTPAuthenticationError(535, b'nope')
    assert 'refused the login' in _refusal(page.routes.send(body={'scope': 'work', 'to': 'a@b.org',
                                                                  'subject': 'x', 'body': 'y'}))
    page.smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused({'a@b.org': (550, b'no such user')})
    assert 'a@b.org' in _refusal(page.routes.send(body={'scope': 'work', 'to': 'a@b.org',
                                                        'subject': 'x', 'body': 'y'}))
    assert page.imap.subjects('Sent') == []                        # nothing filed for a mail that did not go


# -- the manifest ----------------------------------------------------------------

def test_manifest_doors_and_page_files_exist(page):
    import json
    from pathlib import Path
    plugin = Path(page.routes.__file__).parent.parent
    manifest = json.loads((plugin / 'plugin.json').read_text(encoding='utf-8'))
    caps = manifest['capabilities']
    assert caps['app']['label'] == 'Email' and caps['app']['nav'] == 'apps'
    for route in caps['routes']:
        file, fn = route['handler'].split(':')
        assert file == 'routes/mail.py' and callable(getattr(page.routes, fn)), route
    assert (plugin / 'app' / 'index.js').is_file() and (plugin / 'app' / 'email.css').is_file()


# -- the page keeps its login ---------------------------------------------------------

def _second_line(page):
    """Another connection to the same mailbox: its own selected folder, the same mail."""
    twin = page.FakeIMAP()
    twin.boxes, twin.listing = page.imap.boxes, page.imap.listing
    return twin


def test_the_page_logs_in_once_and_her_tools_still_log_in_each_time(page):
    uid = page.imap.add('INBOX', page.raw(subject='Hello'))
    for _ in range(3):
        _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    _ok(page.routes.get_message(query={'scope': 'work', 'id': f'inbox:{uid}', 'validity': '1', 'read': '1'}))
    _ok(page.routes.mark(body={'scope': 'work', 'ids': [f'inbox:{uid}'], 'mark': 'starred', 'validity': 1}))
    assert len(page.logins) == 1 and page.imap.logouts == 0            # five requests, one login, still open
    assert ('noop',) not in page.imap.calls                            # used again at once: no check needed

    text, ok = page.tool.execute('get_inbox', {}, None)                # her tool: its own login, closed after
    assert ok and len(page.logins) == 2 and page.imap.logouts == 1

    first = page.imap
    page.imap = _second_line(page)
    _ok(page.routes.folder_counts(query={'scope': 'work'}))            # counts ride a second line of their own
    assert len(page.logins) == 3 and page.imap.selected is None      # it never selects a folder
    assert set(page.mailbox._kept) == {'work', 'work\ncounts'}
    page.mailbox.close_kept()
    assert first.logouts + page.imap.logouts == 3 and all(s.client is None for s in page.mailbox._kept.values())


def test_counts_come_apart_from_the_list(page):
    page.imap.add('INBOX', page.raw(subject='One'))
    page.imap.add('INBOX', page.raw(subject='Two'), flags=(SEEN,))
    fast = _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert [f['unread'] for f in fast['folders']] == [None] * len(fast['folders']) and len(fast['rows']) == 2
    page.imap = _second_line(page)
    counts = _ok(page.routes.folder_counts(query={'scope': 'work'}))
    inbox = next(f for f in counts['folders'] if f['key'] == 'inbox')
    assert (inbox['total'], inbox['unread']) == (2, 1) and counts['scope'] == 'work'
    assert 'account' in _refusal(page.routes.folder_counts(query={}))


def test_an_idle_login_is_checked_and_a_dead_one_is_replaced(page, monkeypatch):
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    slot = page.mailbox._kept['work']
    slot.used -= page.mailbox.KEEP_CHECK + 1                           # it sat a while
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert page.imap.calls.count(('noop',)) == 1 and len(page.logins) == 1       # alive: kept

    slot.used -= page.mailbox.KEEP_CHECK + 1
    page.imap.dead = True                                              # the server hung up meanwhile
    fresh = page.FakeIMAP()
    fresh.add('INBOX', page.raw(subject='After the drop'))
    page.imap, old = fresh, page.imap
    got = _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert [r['subject'] for r in got['rows']] == ['After the drop'] and len(page.logins) == 2 and old.logouts == 1

    slot.used -= page.mailbox.KEEP_IDLE + 1                            # past the idle limit: a new login, no NOOP
    noops = fresh.calls.count(('noop',))
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert len(page.logins) == 3 and fresh.calls.count(('noop',)) == noops


def test_a_changed_password_or_a_broken_line_is_not_reused(page, monkeypatch):
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    changed = dict(page.creds, app_password='new-one')
    monkeypatch.setattr(page.accounts, 'get_creds', lambda scope: (dict(changed), None))
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert len(page.logins) == 2 and page.logins[1]['app_password'] == 'new-one'

    # our own refusal leaves the line usable; a failure in the middle of a command does not
    assert 'No folder named' in _refusal(page.routes.folder_view(query={'scope': 'work', 'folder': 'Nope', 'counts': '0'}))
    assert page.mailbox._kept['work'].client is not None and len(page.logins) == 2

    def broken(*a, **k):
        raise OSError('line dropped mid-answer')
    monkeypatch.setattr(page.imap, 'fetch', broken)
    page.imap.add('INBOX', page.raw(subject='x'))
    words = _refusal(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}), 502)
    assert 'stopped answering' in words and 'line dropped' not in words     # the error's own text stays in the log
    assert page.mailbox._kept['work'].client is None                   # thrown away, not handed to the next click


def test_one_request_at_a_time_per_login(page, monkeypatch):
    monkeypatch.setattr(page.routes, 'READ_WAIT', 0.05)
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    slot = page.mailbox._kept['work']
    assert slot.lock.acquire(timeout=1)                                # another request is on the line
    try:
        assert 'still busy' in _refusal(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    finally:
        slot.lock.release()
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))


def test_a_refused_login_is_not_sent_again_on_every_click(page, monkeypatch):
    """Three wrong passwords in a row can get the whole machine banned by the
    mail server, her working account included."""
    class LoginError(Exception):
        pass
    tries = []

    def refused(creds):
        tries.append(creds['app_password'])
        raise LoginError("bad password")
    monkeypatch.setattr(page.accounts, 'imap_connect', refused)
    assert 'refused the login' in _refusal(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    for door in (page.routes.folder_view, page.routes.folder_counts):
        words = _refusal(door(query={'scope': 'work'}))
        assert 'not tried again for' in words and 'Settings' in words
    assert tries == ['pw']                                             # the server heard it once

    fixed = dict(page.creds, app_password='right-one')                 # the person fixes the password: tried at once
    monkeypatch.setattr(page.accounts, 'get_creds', lambda scope: (dict(fixed), None))
    monkeypatch.setattr(page.accounts, 'imap_connect', lambda creds: tries.append(creds['app_password']) or page.imap)
    _ok(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert tries == ['pw', 'right-one'] and page.accounts._refused == {}

    monkeypatch.setattr(page.accounts, 'imap_connect', refused)        # the hold runs out by itself
    page.mailbox.close_kept()
    _refusal(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    mark, when, words = page.accounts._refused['work']
    page.accounts._refused['work'] = (mark, when - page.accounts.REFUSED_HOLD - 1, words)
    _refusal(page.routes.folder_view(query={'scope': 'work', 'counts': '0'}))
    assert tries == ['pw', 'right-one', 'right-one', 'right-one']
