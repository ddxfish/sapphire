"""message.py: what a reply looks like on the wire, and who it goes to."""
import email
import email.policy


def _parse(mail, **kw):
    return mail.message.parse(mail.raw(**kw))


def _parts(msg):
    wire = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    text = lambda kind: wire.get_body((kind,)).get_content().replace('\r\n', '\n')
    return wire, text('plain'), text('html')


def test_reply_has_a_plain_quote_and_a_real_html_blockquote(mail):
    original = _parse(mail, subject='Lunch?', body='Are you free\nat noon?')
    msg = mail.message.build('me@x.com', [('Alice', 'alice@x.com')], (), 'Re: Lunch?', 'Yes!', original, 'reply')
    wire, plain, html = _parts(msg)
    assert wire.get_content_type() == 'multipart/alternative'
    assert plain.startswith('Yes!')
    assert 'Alice wrote:' in plain and '> Are you free\n> at noon?' in plain
    # The part mail clients fold away: a gmail_quote blockquote, not '>' lines.
    assert '<blockquote class="gmail_quote"' in html and 'Are you free<br>' in html
    assert '&gt;' not in html
    assert wire['In-Reply-To'] == original['Message-ID']
    assert str(original['Message-ID']) in str(wire['References'])


def test_reply_to_html_only_mail_quotes_text_never_raw_tags(mail):
    original = _parse(mail, body=None, html='<html><head><style>p{color:red}</style></head>'
                                           '<body><p>Big <b>sale</b></p><script>x()</script></body></html>')
    msg = mail.message.build('me@x.com', [('Alice', 'alice@x.com')], (), 'Re: x', 'ok', original, 'reply')
    _, plain, html = _parts(msg)
    assert '> Big sale' in plain and '<p>' not in plain
    assert '<p>Big <b>sale</b></p>' in html              # their HTML, inside the blockquote
    assert '<script' not in html and 'x()' not in html


def test_references_chain_is_extended(mail):
    original = _parse(mail, headers={'References': '<a@x.com> <b@x.com>'})
    msg = mail.message.build('me@x.com', [('A', 'alice@x.com')], (), 'Re: x', 'ok', original, 'reply')
    assert str(msg['References']).split() == ['<a@x.com>', '<b@x.com>', str(original['Message-ID'])]


def test_reply_to_header_wins_over_from(mail):
    original = _parse(mail, headers={'Reply-To': 'Help Desk <help@x.com>'})
    to, cc = mail.message.reply_targets(original, 'me@x.com')
    assert to == [('Help Desk', 'help@x.com')] and cc == []


def test_reply_to_our_own_sent_mail_goes_to_its_recipients(mail):
    original = _parse(mail, frm='me@x.com', to='Carol <carol@x.com>')
    to, _ = mail.message.reply_targets(original, 'ME@x.com')
    assert to == [('Carol', 'carol@x.com')]


def test_reply_all_adds_everyone_once_and_never_ourselves(mail):
    original = _parse(mail, frm='Alice <alice@x.com>', to='me@x.com, Bob <bob@x.com>, alice@x.com',
                      cc='Carol <carol@x.com>, BOB@x.com')
    to, cc = mail.message.reply_targets(original, 'me@x.com', reply_all=True)
    assert to == [('Alice', 'alice@x.com')]
    assert cc == [('Bob', 'bob@x.com'), ('Carol', 'carol@x.com')]


def test_forward_shows_names_not_addresses_and_carries_files(mail):
    original = _parse(mail, frm='Secret Person <secret@hidden.com>', subject='Report',
                      body='see attached', files=[('r.pdf', 'application/pdf', b'%PDF-1.4')])
    files = mail.message.attachments(original)
    msg = mail.message.build('me@x.com', [('Bob', 'bob@x.com')], (), 'Fwd: Report', 'FYI', original, 'forward', files)
    wire = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    body = wire.get_body(('plain',)).get_content() + wire.get_body(('html',)).get_content()
    assert 'Forwarded message' in body and 'From: Secret Person' in body and 'see attached' in body
    assert 'secret@hidden.com' not in body
    assert [(a.get_filename(), a.get_content()) for a in wire.iter_attachments()] == [('r.pdf', b'%PDF-1.4')]


def test_bcc_is_never_a_header_and_names_survive_non_ascii(mail):
    msg = mail.message.build('me@x.com', [('Zoë Müller', 'zoe@x.com')], [('Bob', 'bob@x.com')], 'Hi', 'héllo')
    wire, plain, _ = _parts(msg)
    assert wire['Bcc'] is None
    assert 'Zoë Müller' in str(wire['To']) and 'zoe@x.com' in str(wire['To'])
    assert str(wire['Cc']) == 'Bob <bob@x.com>'
    assert plain.strip() == 'héllo'


def test_prefixed_never_doubles(mail):
    assert mail.message.prefixed('Lunch', 'Re') == 'Re: Lunch'
    assert mail.message.prefixed('RE: Lunch', 'Re') == 'RE: Lunch'
    assert mail.message.prefixed('Fwd: x', 'Fwd') == 'Fwd: x'


def test_html_to_text_keeps_links_and_drops_markup(mail):
    text = mail.message.html_to_text('<div>Hello<br>there</div><p><a href="https://x.com/reset">Reset</a></p>'
                                     '<style>.a{}</style>')
    assert text == 'Hello\nthere\n\nReset (https://x.com/reset)' or text.split() == \
        ['Hello', 'there', 'Reset', '(https://x.com/reset)']


def test_attachments_lists_files_and_inline_pictures_not_the_body(mail):
    original = _parse(mail, body='text', html='<p>text</p>',
                      files=[('pic.png', 'image/png', b'\x89PNG'), ('notes.txt', 'text/plain', b'hi')])
    assert [(f['name'], f['type']) for f in mail.message.attachments(original)] == \
        [('pic.png', 'image/png'), ('notes.txt', 'text/plain')]


def test_automated_mail_is_recognised(mail):
    auto = mail.message.is_automated
    assert auto(_parse(mail)) == ''
    assert auto(_parse(mail, headers={'Auto-Submitted': 'no'})) == ''
    assert auto(_parse(mail, headers={'Auto-Submitted': 'auto-replied'}))
    assert auto(_parse(mail, headers={'Precedence': 'bulk'}))
    assert auto(_parse(mail, headers={'List-Unsubscribe': '<mailto:u@x.com>'}))
    assert auto(_parse(mail, frm='Shop <no-reply@shop.com>'))
    assert auto(_parse(mail, frm='MAILER-DAEMON@x.com'))
    assert auto(_parse(mail, frm='Noreen <noreen@x.com>')) == ''      # a person, not a no-reply box


def test_machine_reply_is_marked_so_the_other_side_wont_answer_it(mail):
    msg = mail.message.build('me@x.com', [('A', 'a@x.com')], (), 'Re: x', 'ok', auto=True)
    assert msg['Auto-Submitted'] == 'auto-replied'
    assert mail.message.is_automated(mail.message.parse(msg.as_bytes()))


def test_a_calendar_invite_is_listed_as_an_attachment(mail):
    import email as _email
    raw = (b"From: Alice <alice@x.com>\r\nTo: me@x.com\r\nSubject: Lunch\r\nMIME-Version: 1.0\r\n"
           b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
           b"--b\r\nContent-Type: text/plain\r\n\r\nLunch on Friday?\r\n"
           b"--b\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n--b--\r\n")
    msg = mail.message.parse(raw)
    assert mail.message.body_text(msg) == 'Lunch on Friday?'
    (invite,) = mail.message.attachments(msg)
    assert invite['type'] == 'text/calendar' and invite['name'].endswith('.ics')
    assert b'BEGIN:VCALENDAR' in invite['data']
