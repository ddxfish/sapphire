"""email_tool.py: the door she uses. Whitelist, target modes, ids, results."""
import pytest

_PEOPLE = [
    {'id': 1, 'name': 'Alice', 'email': 'alice@x.com', 'email_whitelisted': True},
    {'id': 2, 'name': 'Bob', 'email': 'bob@x.com', 'email_whitelisted': True},
    {'id': 3, 'name': 'Carol', 'email': 'carol@x.com', 'email_whitelisted': False},
]


@pytest.fixture
def people(monkeypatch):
    monkeypatch.setattr('core.contacts.get_people', lambda scope='default': _PEOPLE)


def test_every_tool_is_refused_while_email_is_off_for_the_chat(mail, monkeypatch):
    monkeypatch.setattr(mail.tool, '_get_current_email_scope', lambda: None)
    for fn in mail.tool.AVAILABLE_FUNCTIONS:
        text, ok = mail.tool.execute(fn, {'id': 'inbox:1', 'ids': ['inbox:1'], 'to': 'trash', 'body': 'x'}, None)
        assert ok is False and 'disabled' in text.lower(), fn


def test_tool_list_matches_the_schemas(mail):
    assert [t['function']['name'] for t in mail.tool.TOOLS] == mail.tool.AVAILABLE_FUNCTIONS


def test_old_index_arguments_get_a_pointer_to_ids(mail):
    text, ok = mail.tool.execute('read_email', {'index': 3}, None)
    assert not ok and 'inbox:4821' in text


def test_inbox_lists_ids_names_and_folders_but_no_addresses(mail):
    mail.imap.add('INBOX', mail.raw(frm='Alice <alice@x.com>', subject='Hello'))
    text, ok = mail.tool.execute('get_inbox', {}, None)
    assert ok and '[inbox:1]' in text and 'Alice: Hello *NEW*' in text
    assert 'alice@x.com' not in text
    assert 'Folders: inbox, sent, drafts, spam, trash, Receipts' in text


def test_spam_folder_is_listable(mail):
    mail.imap.add('Junk', mail.raw(subject='You won'))
    text, ok = mail.tool.execute('get_inbox', {'folder': 'spam'}, None)
    assert ok and '[spam:1]' in text and 'You won' in text


def test_read_shows_recipients_attachments_and_marks_read(mail):
    uid = mail.imap.add('INBOX', mail.raw(to='me@x.com, Bob <bob@x.com>', body='line\n' * 2000,
                                         files=[('notes.txt', 'text/plain', b'the notes')]))
    text, ok = mail.tool.execute('read_email', {'id': f'inbox:{uid}'}, None)
    assert ok and 'From: Alice' in text and 'To: me, Bob' in text
    assert '[1] notes.txt (text/plain, 1 KB)' in text
    assert 'page 1 of 3' in text and 'alice@x.com' not in text
    assert b'\\Seen' in mail.imap.boxes['INBOX'][uid]['flags']
    page3, _ = mail.tool.execute('read_email', {'id': f'inbox:{uid}', 'page': 3}, None)
    assert 'page 3' not in page3                       # the last page offers no "more"
    att, ok = mail.tool.execute('read_email', {'id': f'inbox:{uid}', 'attachment': 1}, None)
    assert ok and 'the notes' in att


def test_read_of_a_missing_mail_says_so(mail):
    text, ok = mail.tool.execute('read_email', {'id': 'inbox:404'}, None)
    assert not ok and 'moved or deleted' in text


def test_send_needs_exactly_one_target(mail, people):
    both, ok1 = mail.tool.execute('send_email', {'body': 'x', 'to': ['1'], 'reply_to': 'inbox:1'}, None)
    none, ok2 = mail.tool.execute('send_email', {'body': 'x'}, None)
    assert not ok1 and 'EITHER' in both
    assert not ok2 and 'No recipient' in none
    mail.smtp.send_message.assert_not_called()


def test_new_mail_to_cc_bcc_and_the_sent_copy(mail, people):
    text, ok = mail.tool.execute('send_email', {'body': 'yo', 'subject': 'Hi', 'to': [1], 'cc': ['2'], 'bcc': ['1', 2]}, None)
    assert ok, text
    msg, rcpts = mail.sent()
    assert str(msg['To']) == 'Alice <alice@x.com>' and str(msg['Cc']) == 'Bob <bob@x.com>'
    assert msg['Bcc'] is None
    assert rcpts == ['alice@x.com', 'bob@x.com']       # bcc repeats of to/cc are dropped
    assert 'Email sent to Alice (cc Bob)' in text
    assert mail.imap.subjects('Sent') == ['Hi']        # this server does not file it itself


def test_bcc_reaches_smtp_but_never_the_headers(mail, people):
    mail.tool.execute('send_email', {'body': 'yo', 'subject': 'Hi', 'to': ['1'], 'bcc': ['2']}, None)
    msg, rcpts = mail.sent()
    assert rcpts == ['alice@x.com', 'bob@x.com']
    assert 'bob@x.com' not in msg.as_string()


def test_gmail_keeps_its_own_sent_copy(mail, people):
    fake = mail.use(mail.FakeIMAP(mail.GMAIL, caps=('MOVE', 'UIDPLUS', 'X-GM-EXT-1')))
    _, ok = mail.tool.execute('send_email', {'body': 'yo', 'subject': 'Hi', 'to': ['1']}, None)
    assert ok and fake.boxes['[Gmail]/Sent Mail'] == {}


def test_unwhitelisted_contact_fails_loud_and_nothing_is_sent(mail, people):
    for args in ({'to': ['3']}, {'to': ['1'], 'cc': ['3']}, {'to': ['1'], 'bcc': ['3']}, {'to': ['99']}):
        text, ok = mail.tool.execute('send_email', dict(args, body='x', subject='s'), None)
        assert not ok and ('not whitelisted' in text or 'not found' in text)
    mail.smtp.send_message.assert_not_called()


def test_raw_address_needs_allow_all(mail, people):
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['evil@y.com']}, None)
    assert not ok and 'not allowed' in text
    mail.settings['allow_all_recipients'] = True
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['pal@y.com']}, None)
    assert ok and mail.sent()[1] == ['pal@y.com']
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['not an address@']}, None)
    assert not ok and 'Invalid' in text


def test_people_scope_off_refuses_contact_ids(mail, people, monkeypatch):
    monkeypatch.setattr(mail.tool, '_get_current_people_scope', lambda: None)
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['1']}, None)
    assert not ok and 'disabled' in text


def test_reply_goes_to_the_sender_threaded_quoted_and_flags_answered(mail):
    uid = mail.imap.add('INBOX', mail.raw(frm='Stranger <stranger@far.com>', subject='Question', body='Why?'))
    text, ok = mail.tool.execute('send_email', {'body': 'Because.', 'reply_to': f'inbox:{uid}'}, None)
    assert ok and 'Email sent to Stranger: "Re: Question"' in text
    msg, rcpts = mail.sent()
    assert rcpts == ['stranger@far.com'] and msg['In-Reply-To']
    assert 'gmail_quote' in msg.as_string()
    assert b'\\Answered' in mail.imap.boxes['INBOX'][uid]['flags']


def test_reply_all_reaches_the_thread_and_can_be_switched_off(mail):
    uid = mail.imap.add('INBOX', mail.raw(frm='Alice <alice@x.com>', to='me@x.com, Dan <dan@far.com>'))
    mail.settings['allow_reply_all'] = False
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'reply_to': f'inbox:{uid}', 'reply_all': True}, None)
    assert not ok and 'turned off' in text
    mail.smtp.send_message.assert_not_called()
    mail.settings['allow_reply_all'] = True
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'reply_to': f'inbox:{uid}', 'reply_all': True}, None)
    assert ok and mail.sent()[1] == ['alice@x.com', 'dan@far.com'] and '(cc Dan)' in text


def test_draft_is_saved_and_never_sent(mail, people):
    text, ok = mail.tool.execute('send_email', {'body': 'later', 'subject': 'Idea', 'to': ['1'], 'draft': True}, None)
    assert ok and 'Draft saved' in text and 'not sent' in text
    mail.smtp.send_message.assert_not_called()
    assert mail.imap.subjects('Drafts') == ['Idea']


def test_attach_takes_pictures_only(mail, people, monkeypatch):
    from core import images as ci

    def resolve(src, **kw):
        if src == 'img:abc':
            return ci.Resolved(b'\xff\xd8jpegbytes', 'image/jpeg', 'abc', 'chat')
        raise ci.ImageError("no file at /etc/passwd" if src.startswith('/') else "bad handle")
    monkeypatch.setattr(ci, 'resolve', resolve)

    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['1'], 'attach': ['/etc/passwd']}, None)
    assert not ok and 'attach /etc/passwd' in text
    mail.smtp.send_message.assert_not_called()
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['1'], 'attach': ['img:abc']}, None)
    assert ok and '(1 attached)' in text
    files = list(mail.sent()[0].iter_attachments())
    assert [(f.get_filename(), f.get_content_type()) for f in files] == [('abc.jpg', 'image/jpeg')]


def test_forward_goes_through_the_whitelist_and_carries_files(mail, people):
    uid = mail.imap.add('INBOX', mail.raw(subject='Report', files=[('r.pdf', 'application/pdf', b'%PDF')]))
    text, ok = mail.tool.execute('forward_email', {'id': f'inbox:{uid}', 'to': ['3']}, None)
    assert not ok and 'not whitelisted' in text
    text, ok = mail.tool.execute('forward_email', {'id': f'inbox:{uid}', 'to': ['2'], 'note': 'FYI'}, None)
    assert ok and 'Forwarded to Bob (1 attached): "Fwd: Report"' in text
    assert [f.get_filename() for f in mail.sent()[0].iter_attachments()] == ['r.pdf']


def test_move_reports_in_plain_words(mail):
    a = mail.imap.add('INBOX', mail.raw(subject='promo'))
    text, ok = mail.tool.execute('move_emails', {'ids': [f'inbox:{a}', 'inbox:50'], 'to': 'spam'}, None)
    assert ok and 'Marked as spam: 1 email' in text and f'[inbox:{a}] promo' in text
    assert '[inbox:50] no longer there' in text
    text, ok = mail.tool.execute('move_emails', {'ids': [f'spam:{a}'], 'to': 'inbox'}, None)
    assert ok and 'Moved to Inbox' in text
    text, ok = mail.tool.execute('move_emails', {'ids': ['inbox:50'], 'to': 'trash'}, None)
    assert not ok                                      # nothing moved is not a success


def test_mark_read_unread_starred(mail):
    uid = mail.imap.add('INBOX', mail.raw())
    flags = lambda: mail.imap.boxes['INBOX'][uid]['flags']
    assert mail.tool.execute('mark_emails', {'ids': [f'inbox:{uid}'], 'mark': 'read'}, None)[1]
    assert b'\\Seen' in flags()
    mail.tool.execute('mark_emails', {'ids': f'inbox:{uid}', 'mark': 'unread'}, None)   # a bare id works too
    assert b'\\Seen' not in flags()
    mail.tool.execute('mark_emails', {'ids': [f'inbox:{uid}'], 'mark': 'starred'}, None)
    assert b'\\Flagged' in flags()
    text, ok = mail.tool.execute('mark_emails', {'ids': [f'inbox:{uid}'], 'mark': 'purple'}, None)
    assert not ok and 'one of' in text


def test_search_needs_a_term_and_a_real_date(mail):
    assert mail.tool.execute('search_emails', {}, None) == ("Give query (e.g. to:krem subject:invoice), or at least one of: sender, content, date.", False)
    text, ok = mail.tool.execute('search_emails', {'date': 'last tuesday'}, None)
    assert not ok and 'YYYY-MM-DD' in text


def test_smtp_login_failure_is_named(mail, people, monkeypatch):
    import smtplib
    mail.smtp.send_message.side_effect = smtplib.SMTPAuthenticationError(535, b'bad')
    text, ok = mail.tool.execute('send_email', {'body': 'x', 'subject': 's', 'to': ['1']}, None)
    assert not ok and 'refused the login' in text
