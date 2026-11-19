"""The Email page's lists-as-last-seen file (index.py) and the routes that
read and fill it. The index is a tmp_path file (conftest.py); the real one is
never opened."""
import sqlite3

import pytest

SEEN = b'\\Seen'


@pytest.fixture
def page(mail):
    import plugins.email.routes.mail as routes
    mail.routes = routes
    return mail


def _rows(*uids, folder='inbox'):
    return [{'id': f'{folder}:{u}', 'subject': f'mail {u}', 'unread': True} for u in uids]


def _uids(view):
    return [int(r['id'].rpartition(':')[2]) for r in view['rows']]


# -- the file itself ---------------------------------------------------------------

def test_a_page_is_kept_and_read_back_newest_first(mail):
    idx = mail.index
    assert idx.view('work', 'inbox') is None and idx.empty()
    assert idx.save_view('work', 'inbox', 7, 3, 1, 50, _rows(12, 11, 9))
    got = idx.view('work', 'inbox')
    assert _uids(got) == [12, 11, 9] and (got['total'], got['validity']) == (3, 7) and got['as_of'] > 0
    assert got['rows'][0] == {'id': 'inbox:12', 'subject': 'mail 12', 'unread': True}
    assert idx.view('work', 'spam') is None and idx.view('other', 'inbox') is None
    assert idx.view('work', 'inbox', page=2) is None             # never kept: not an empty page


def test_what_the_server_no_longer_lists_is_removed(mail):
    idx = mail.index
    idx.save_view('work', 'inbox', 7, 4, 1, 50, _rows(12, 11, 10, 9))
    idx.save_view('work', 'inbox', 7, 4, 1, 50, _rows(13, 12, 10, 9))        # 11 moved away, 13 arrived
    assert _uids(idx.view('work', 'inbox')) == [13, 12, 10, 9]
    changed = _rows(13, 12, 10, 9)
    changed[1]['unread'] = False
    idx.save_view('work', 'inbox', 7, 4, 1, 50, changed)                      # read elsewhere: the row follows
    assert idx.view('work', 'inbox')['rows'][1]['unread'] is False
    idx.save_view('work', 'inbox', 7, 0, 1, 50, [])                           # emptied
    got = idx.view('work', 'inbox')
    assert got['rows'] == [] and got['total'] == 0


def test_a_rebuilt_folder_starts_empty(mail):
    idx = mail.index
    idx.save_view('work', 'inbox', 7, 2, 1, 50, _rows(12, 11))
    idx.save_view('work', 'inbox', 8, 1, 1, 50, _rows(3))                     # new UIDVALIDITY: old numbers mean other mail
    got = idx.view('work', 'inbox')
    assert _uids(got) == [3] and got['validity'] == 8


def test_only_the_first_page_is_kept_so_the_copy_never_has_a_hole(mail):
    idx = mail.index
    assert idx.save_view('work', 'inbox', 1, 9, 1, 3, _rows(9, 8, 7)) is True
    assert idx.save_view('work', 'inbox', 1, 9, 2, 3, _rows(6, 5, 4)) is False       # deeper pages are not kept
    assert idx.view('work', 'inbox', 2, 3) is None
    idx.save_view('work', 'inbox', 1, 200, 1, 3, _rows(130, 129, 128))               # days later: 120 new mails
    assert _uids(idx.view('work', 'inbox', 1, 3)) == [130, 129, 128]                 # the old rows are gone, no gap


def test_a_copy_kept_for_another_address_is_dropped_not_shown(mail):
    """The account name is pointed at a different mailbox: the previous
    mailbox's senders and subjects must not flash up under the new address."""
    idx = mail.index
    idx.save_view('work', 'inbox', 1, 1, 1, 50, _rows(5), address='Old@X.com')
    assert _uids(idx.view('work', 'inbox', address='old@x.com')) == [5]
    assert idx.view('work', 'inbox', address='new@y.org') is None
    assert idx.view('work', 'inbox', address='old@x.com') is None and idx.empty()    # dropped for good


def test_folder_lists_and_forgetting(mail):
    idx = mail.index
    folders = [{'key': 'inbox', 'label': 'Inbox', 'role': 'inbox', 'total': 3, 'unread': 1}]
    assert idx.folders('work') is None
    idx.save_folders('work', folders)
    idx.save_folders('gone', folders)
    idx.save_view('work', 'inbox', 1, 1, 1, 50, _rows(1))
    idx.save_view('gone', 'inbox', 1, 1, 1, 50, _rows(1))
    assert idx.folders('work') == folders
    idx.prune(['work', 'never-synced'])                                     # 'gone' was removed in Settings
    assert idx.folders('gone') is None and idx.view('gone', 'inbox') is None and idx.view('work', 'inbox')
    idx.forget()
    assert idx.empty() and idx.view('work', 'inbox') is None


def test_only_headers_are_in_the_file(page):
    page.imap.add('INBOX', page.raw(frm='Alice <alice@x.com>', subject='Quarterly numbers',
                                    body='THE-BODY-TEXT-MUST-NOT-BE-KEPT'))
    page.routes.folder_view(query={'scope': 'work', 'counts': '0'})
    page.routes.get_message(query={'scope': 'work', 'id': 'inbox:1', 'validity': '1', 'read': '1'})
    page.index.close()
    blob = page.index.path.read_bytes()
    assert b'Quarterly numbers' in blob and b'alice@x.com' in blob
    assert b'THE-BODY-TEXT' not in blob and b'pw' not in blob.split(b'Quarterly')[0][-200:]


# -- through the routes ---------------------------------------------------------------

def test_the_page_gets_the_last_seen_list_without_the_server(page, monkeypatch):
    for n in range(3):
        page.imap.add('INBOX', page.raw(subject=f'Mail {n}'), flags=(SEEN,) if n else ())
    assert page.routes.folder_view(query={'scope': 'work', 'cached': '1'}) == {'cached': True, 'miss': True}
    live = page.routes.folder_view(query={'scope': 'work', 'counts': '0'})
    first = page.imap
    page.imap = page.FakeIMAP()                                  # the counts ride a second connection
    page.imap.boxes, page.imap.listing = first.boxes, first.listing
    page.routes.folder_counts(query={'scope': 'work'})

    def no_server(creds):
        raise AssertionError("a cached view must not log in")
    page.mailbox.close_kept()
    monkeypatch.setattr(page.accounts, 'imap_connect', no_server)
    seen = page.routes.folder_view(query={'scope': 'work', 'folder': 'inbox', 'cached': '1'})
    assert seen['cached'] is True and seen['as_of'] > 0
    assert [r['subject'] for r in seen['rows']] == ['Mail 2', 'Mail 1', 'Mail 0'] == [r['subject'] for r in live['rows']]
    assert (seen['total'], seen['validity'], seen['folder'], seen['pages']) == (3, live['validity'], 'inbox', 1)
    inbox = next(f for f in seen['folders'] if f['key'] == 'inbox')
    assert (inbox['total'], inbox['unread']) == (3, 1)                       # the badges come back too
    assert seen['account'] == {'scope': 'work', 'address': 'me@x.com'}
    assert page.routes.folder_view(query={'scope': 'work', 'folder': 'spam', 'cached': '1'}) == {'cached': True, 'miss': True}


def test_a_search_is_never_kept(page):
    page.imap.add('INBOX', page.raw(subject='Invoice', body='please pay'))
    page.imap.add('INBOX', page.raw(subject='Hello'))
    page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'pay'})
    page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'unread': '1'})
    assert page.index.view('work', 'inbox') is None
    assert page.routes.folder_view(query={'scope': 'work', 'cached': '1', 'q': 'pay'}) == {'cached': True, 'miss': True}


def test_turned_off_it_keeps_nothing_and_drops_what_it_had(page):
    page.imap.add('INBOX', page.raw(subject='Hello'))
    page.routes.folder_view(query={'scope': 'work', 'counts': '0'})
    assert page.index.view('work', 'inbox') is not None
    off = {'keep_mail_index': False}
    got = page.routes.folder_view(query={'scope': 'work', 'counts': '0'}, settings=off)
    assert [r['subject'] for r in got['rows']] == ['Hello']                 # mail still works
    assert page.index.empty()
    assert page.routes.folder_view(query={'scope': 'work', 'cached': '1'}, settings=off) == {'cached': True, 'miss': True}


def test_a_broken_index_never_breaks_mail(page, monkeypatch):
    page.imap.add('INBOX', page.raw(subject='Hello'))

    def boom(*a, **k):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(page.index, 'save_view', boom)
    monkeypatch.setattr(page.index, 'view', boom)
    got = page.routes.folder_view(query={'scope': 'work', 'counts': '0'})
    assert [r['subject'] for r in got['rows']] == ['Hello']
    assert page.routes.folder_view(query={'scope': 'work', 'cached': '1'}) == {'cached': True, 'miss': True}
