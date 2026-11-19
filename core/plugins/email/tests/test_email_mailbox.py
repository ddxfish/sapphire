"""mailbox.py: folders by role, stable ids, moves that never lie."""
import pytest


def _box(mail, fake=None):
    return mail.mailbox.Mailbox('work', mail.creds)


def test_roles_found_by_flag_on_gmail(mail):
    mail.use(mail.FakeIMAP(mail.GMAIL))
    with _box(mail) as mb:
        roles, names = mb.folders()
    assert roles == {'inbox': 'INBOX', 'sent': '[Gmail]/Sent Mail', 'drafts': '[Gmail]/Drafts',
                     'archive': '[Gmail]/All Mail', 'spam': '[Gmail]/Spam', 'trash': '[Gmail]/Trash'}
    assert '[Gmail]' not in names            # \Noselect is not a folder to open


def test_roles_found_by_name_when_the_server_sets_no_flags(mail):
    mail.use(mail.FakeIMAP([((), b'/', 'INBOX'), ((), b'/', 'Mail/Junk E-mail'), ((), b'/', 'Deleted Items')]))
    with _box(mail) as mb:
        roles, _ = mb.folders()
    assert roles['spam'] == 'Mail/Junk E-mail'
    assert roles['trash'] == 'Deleted Items'
    assert 'archive' not in roles


def test_missing_role_and_unknown_folder_are_refused(mail):
    mail.use(mail.FakeIMAP([((), b'/', 'INBOX')]))
    with _box(mail) as mb:
        with pytest.raises(mail.mailbox.MailError, match="no spam folder"):
            mb.resolve('spam')
        with pytest.raises(mail.mailbox.MailError, match="No folder named"):
            mb.resolve('Nope')


def test_custom_folder_by_name_and_folder_keys(mail):
    with _box(mail) as mb:
        assert mb.resolve('receipts') == 'Receipts'
        assert mb.folder_keys() == ['inbox', 'sent', 'drafts', 'spam', 'trash', 'Receipts']


@pytest.mark.parametrize("handle,want", [("inbox:42", ("inbox", 42)), ("[spam:7]", ("spam", 7)),
                                         ("[Gmail]/Custom:9", ("[Gmail]/Custom", 9))])
def test_split_id(mail, handle, want):
    assert mail.mailbox.split_id(handle) == want


@pytest.mark.parametrize("bad", ["3", "inbox", "inbox:abc", "", None, ":5"])
def test_split_id_refuses_anything_else(mail, bad):
    with pytest.raises(mail.mailbox.MailError):
        mail.mailbox.split_id(bad)


def test_latest_is_newest_first_with_ids_names_and_marks(mail):
    mail.imap.add('INBOX', mail.raw(subject='old'), flags={b'\\Seen'})
    mail.imap.add('INBOX', mail.raw(frm='bob@corp.com', subject='new', files=[('a.pdf', 'application/pdf', b'%PDF')]),
                  flags={b'\\Flagged'})
    with _box(mail) as mb:
        key, rows = mb.latest('inbox', 20)
    assert key == 'inbox'
    assert [r['id'] for r in rows] == ['inbox:2', 'inbox:1']
    assert rows[0]['name'] == 'bob' and rows[1]['name'] == 'Alice'     # never an address
    assert rows[0]['unread'] and rows[0]['starred'] and rows[0]['attach']
    assert not rows[1]['unread'] and not rows[1]['attach']


def test_sent_folder_lists_who_it_went_to(mail):
    mail.imap.add('Sent', mail.raw(frm='me@x.com', to='Carol <carol@x.com>'))
    with _box(mail) as mb:
        key, rows = mb.latest('sent', 20)
    assert key == 'sent' and rows[0]['name'] == 'Carol'


def test_move_uses_the_servers_move(mail):
    uid = mail.imap.add('INBOX', mail.raw(subject='junk mail'))
    with _box(mail) as mb:
        moved, skipped = mb.move([f'inbox:{uid}'], 'spam')
    assert moved == [(f'inbox:{uid}', 'junk mail')] and skipped == []
    assert mail.imap.subjects('Junk') == ['junk mail'] and not mail.imap.boxes['INBOX']


def test_move_without_move_support_copies_then_expunges_only_those(mail):
    fake = mail.use(mail.FakeIMAP(caps=('UIDPLUS',)))
    keep = fake.add('INBOX', mail.raw(subject='keep'), flags={b'\\Deleted'})   # someone else's pending delete
    uid = fake.add('INBOX', mail.raw(subject='bye'))
    with _box(mail) as mb:
        moved, _ = mb.move([f'inbox:{uid}'], 'trash')
    assert moved == [(f'inbox:{uid}', 'bye')]
    assert fake.subjects('Trash') == ['bye']
    assert keep in fake.boxes['INBOX']                # a plain EXPUNGE would have destroyed it
    assert ('uid_expunge', [uid]) in fake.calls


def test_move_reports_a_vanished_mail_instead_of_claiming_it(mail):
    uid = mail.imap.add('INBOX', mail.raw(subject='here'))
    with _box(mail) as mb:
        moved, skipped = mb.move([f'inbox:{uid}', 'inbox:999'], 'trash')
    assert moved == [(f'inbox:{uid}', 'here')]
    assert skipped == [('inbox:999', 'no longer there')]


def test_delete_is_refused_when_the_server_has_no_trash(mail):
    fake = mail.use(mail.FakeIMAP([((), b'/', 'INBOX')]))
    uid = fake.add('INBOX', mail.raw())
    with _box(mail) as mb:
        with pytest.raises(mail.mailbox.MailError, match="no trash folder"):
            mb.move([f'inbox:{uid}'], 'trash')
    assert uid in fake.boxes['INBOX']                 # nothing was destroyed


def test_archive_folder_is_created_when_missing(mail):
    uid = mail.imap.add('INBOX', mail.raw(subject='file me'))
    with _box(mail) as mb:
        moved, _ = mb.move([f'inbox:{uid}'], 'archive', create='Archive')
    assert moved == [(f'inbox:{uid}', 'file me')] and mail.imap.subjects('Archive') == ['file me']


def test_not_spam_moves_back_to_the_inbox(mail):
    uid = mail.imap.add('Junk', mail.raw(subject='real mail'))
    with _box(mail) as mb:
        moved, _ = mb.move([f'spam:{uid}'], 'inbox')
    assert moved == [(f'spam:{uid}', 'real mail')] and mail.imap.subjects('INBOX') == ['real mail']


def test_reading_peeks_and_marks_read_only_on_request(mail):
    uid = mail.imap.add('INBOX', mail.raw())
    with _box(mail) as mb:
        mb.message(f'inbox:{uid}')
    assert b'\\Seen' not in mail.imap.boxes['INBOX'][uid]['flags']
    with _box(mail) as mb:
        mb.message(f'inbox:{uid}', mark_read=True)
    assert b'\\Seen' in mail.imap.boxes['INBOX'][uid]['flags']


def test_flag_on_and_off_and_skips_missing(mail):
    uid = mail.imap.add('INBOX', mail.raw())
    with _box(mail) as mb:
        assert mb.flag([f'inbox:{uid}', 'inbox:77'], b'\\Flagged') == (1, [('inbox:77', 'no longer there')])
        assert b'\\Flagged' in mail.imap.boxes['INBOX'][uid]['flags']
        mb.flag([f'inbox:{uid}'], b'\\Flagged', on=False)
    assert b'\\Flagged' not in mail.imap.boxes['INBOX'][uid]['flags']


def test_search_sender_means_recipient_in_sent_and_utf8_when_needed(mail):
    with _box(mail) as mb:
        mb.search('sent', sender='carol')
        mb.search('inbox', content='héllo')
    searches = [c for c in mail.imap.calls if c[0] == 'search']
    assert searches[0][1][:2] == ['TO', 'carol'] and searches[0][2] is None
    assert searches[1][1] == ['OR', 'SUBJECT', 'héllo', 'BODY', 'héllo'] and searches[1][2] == 'UTF-8'


def test_gmails_own_folders_are_shown_by_their_plain_names(mail):
    """'[Gmail]/Starred' is how the server spells it; she and the page see
    'Starred', and either spelling opens it."""
    fake = mail.use(mail.FakeIMAP(mail.GMAIL + [
        ((b'\\HasNoChildren', b'\\Flagged'), b'/', '[Gmail]/Starred'),
        ((b'\\HasNoChildren', b'\\Important'), b'/', '[Gmail]/Important'),
        ((b'\\HasNoChildren',), b'/', '[Gmail]/Inbox'),          # would read as the inbox role: keeps its full name
        ((b'\\HasNoChildren',), b'/', 'Receipts')]))
    uid = fake.add('[Gmail]/Starred', mail.raw(subject='Kept'))
    with mail.mailbox.Mailbox('work', mail.creds) as mb:
        assert mb.folder_keys() == ['inbox', 'sent', 'drafts', 'archive', 'spam', 'trash',
                                    'Starred', 'Important', '[Gmail]/Inbox', 'Receipts']
        for name in ('Starred', 'starred', '[Gmail]/Starred'):
            assert mb.resolve(name) == '[Gmail]/Starred'
        assert mb.key_for('[Gmail]/Starred') == 'Starred' and mb.key_for('[Gmail]/All Mail') == 'archive'
        assert mb.resolve('inbox') == 'INBOX' and mb.resolve('[Gmail]/Inbox') == '[Gmail]/Inbox'
        rows = mb.latest('Starred', 5, False)
    text, ok = mail.tool.execute('get_inbox', {'folder': 'Starred'}, None)
    assert ok and f'[Starred:{uid}]' in text and 'Folders: inbox, sent, drafts, archive, spam, trash, Starred, Important' in text
    text, ok = mail.tool.execute('read_email', {'id': f'Starred:{uid}'}, None)
    assert ok and 'Kept' in text
