"""Search: the words (search.py), what a standard IMAP server and Gmail are
asked, her search_emails(query=...) and the page's search box."""
from datetime import date

import pytest

SEEN, FLAGGED = b'\\Seen', b'\\Flagged'
TODAY = date(2026, 11, 18)


@pytest.fixture
def page(mail):
    import plugins.email.routes.mail as routes
    mail.routes = routes
    return mail


@pytest.fixture
def words():
    from plugins.email import search
    return search


@pytest.fixture
def box(mail):
    """A small mailbox: three in the inbox, one sent, one in a folder of its own, one in spam."""
    m = mail
    ids = {
        'invoice': m.imap.add('INBOX', m.raw(frm='Ann Lee <ann@x.com>', to='me@x.com', subject='Invoice 42',
                                             body='Please pay by Friday', headers={'Date': 'Mon, 02 Nov 2026 10:00:00 +0000'},
                                             files=[('inv.pdf', 'application/pdf', b'%PDF-1.4')]), flags=(SEEN,)),
        'news': m.imap.add('INBOX', m.raw(frm='Newsletter <news@shop.com>', to='me@x.com', subject='Deals on invoices',
                                          body='Big sale', headers={'Date': 'Tue, 10 Nov 2026 10:00:00 +0000'})),
        'lunch': m.imap.add('INBOX', m.raw(frm='Dan <dan@q.io>', to='me@x.com', cc='Ann Lee <ann@x.com>', subject='Lunch?',
                                           body='Friday at noon', headers={'Date': 'Mon, 16 Nov 2026 10:00:00 +0000'}),
                            flags=(SEEN, FLAGGED)),
        'sent': m.imap.add('Sent', m.raw(frm='me@x.com', to='Krem <krem@fish.com>', subject='Re: Invoice 42',
                                         body='Paid, thanks', headers={'Date': 'Tue, 03 Nov 2026 10:00:00 +0000'}),
                           flags=(SEEN,)),
        'old': m.imap.add('Receipts', m.raw(frm='Ann Lee <ann@x.com>', to='me@x.com', subject='Old invoice',
                                           body='From last year', headers={'Date': 'Thu, 05 Nov 2025 10:00:00 +0000'}),
                          flags=(SEEN,)),
        'junk': m.imap.add('Junk', m.raw(frm='Scam <win@lottery.biz>', to='me@x.com', subject='Your invoice is overdue',
                                         body='Click here', headers={'Date': 'Tue, 17 Nov 2026 10:00:00 +0000'})),
    }
    return ids


def _find(mail, text, folder='inbox', everywhere=False):
    from plugins.email import search
    terms, place = search.where(search.parse(text))
    with mail.mailbox.Mailbox('work', mail.creds) as mb:
        rows, total = mb.find(terms, folder, everywhere or place == 'all', 50)
    return [r['subject'] for r in rows]


# -- the words --------------------------------------------------------------------

def test_the_words_are_read(words):
    terms = words.parse('to:ddxfish subject:"quarterly numbers" is:unread -from:news has:attachment hello "two words" label:work')
    assert terms == [(False, 'to', 'ddxfish'), (False, 'subject', 'quarterly numbers'), (False, 'is', 'unread'),
                     (True, 'from', 'news'), (False, 'has', 'attachment'), (False, 'text', 'hello'),
                     (False, 'text', 'two words'), (False, 'raw', 'label:work')]
    assert words.parse('') == [] and words.parse('   ') == []
    assert words.parse('http://x.com/a 10:30') == [(False, 'raw', 'http://x.com/a'), (False, 'text', '10:30')]
    assert words.where(words.parse('in:sent x in:ALL')) == ([(False, 'text', 'x')], 'all')
    assert words.where(words.parse('x in:Receipts')) == ([(False, 'text', 'x')], 'Receipts')
    with pytest.raises(ValueError, match="'from:' needs something"):
        words.parse('from:')


def test_what_a_standard_server_is_asked(words):
    crit, charset, attach = words.imap(words.parse(
        'to:ddxfish subject:"quarterly numbers" is:unread -from:news after:2026-11-01 before:2026/12/01 hello'), TODAY)
    assert crit == ['TO', 'ddxfish', 'SUBJECT', 'quarterly numbers', 'UNSEEN', 'NOT', ['FROM', 'news'],
                    'SINCE', date(2026, 11, 1), 'BEFORE', date(2026, 12, 1), 'TEXT', 'hello']
    assert charset is None and attach is None
    assert words.imap(words.parse('café'), TODAY)[1] == 'UTF-8'
    assert words.imap([], TODAY) == (['ALL'], None, None)
    crit, _, attach = words.imap(words.parse('on:today newer_than:7d older_than:2w -is:starred -has:attachment body:x cc:y'), TODAY)
    assert crit == ['ON', TODAY, 'SINCE', date(2026, 11, 11), 'BEFORE', date(2026, 11, 4), 'NOT', ['FLAGGED'],
                    'BODY', 'x', 'CC', 'y'] and attach is False
    for bad, why in (('after:soon', 'not a date'), ('on:2026-02-31', 'not a real date'), ('is:purple', 'is:purple is not known'),
                     ('has:wings', 'has:wings is not known'), ('newer_than:soon', 'not a span of time'),
                     ('has:attachment -has:attachment', 'cannot both')):
        with pytest.raises(ValueError, match=why):
            words.imap(words.parse(bad), TODAY)


def test_what_gmail_is_asked(words):
    g = lambda text: words.for_gmail(words.parse(text), TODAY)
    assert g('to:ddxfish subject:"quarterly numbers" -from:news has:attachment after:2026-11-01 hello "two words"') == \
        'to:ddxfish subject:"quarterly numbers" -from:news has:attachment after:2026/11/01 hello "two words"'
    assert g('on:2026-11-18 is:unread -is:starred newer_than:2w') == \
        'after:2026/11/18 before:2026/11/19 is:unread -is:starred newer_than:14d'
    assert g('label:work filename:pdf larger:5M') == 'label:work filename:pdf larger:5M'     # Gmail's own words pass through
    assert g('is:unstarred') == '-is:starred' and g('') == ''
    assert g('is:answered from:ann') is None            # Gmail's box cannot say it: the standard search is used


# -- against a mailbox ----------------------------------------------------------------

def test_search_finds_by_who_what_and_when(mail, box):
    assert _find(mail, 'invoice') == ['Deals on invoices', 'Invoice 42']               # newest first
    assert _find(mail, 'from:ann') == ['Invoice 42'] and _find(mail, 'cc:ann') == ['Lunch?']
    assert _find(mail, 'subject:invoice -from:news') == ['Invoice 42']
    assert _find(mail, 'is:unread') == ['Deals on invoices'] and _find(mail, 'is:starred') == ['Lunch?']
    assert _find(mail, 'body:friday') == ['Lunch?', 'Invoice 42'] and _find(mail, 'body:friday subject:lunch') == ['Lunch?']
    assert _find(mail, 'after:2026-11-05') == ['Lunch?', 'Deals on invoices']
    assert _find(mail, 'before:2026-11-05') == ['Invoice 42'] and _find(mail, 'on:2026-11-10') == ['Deals on invoices']
    assert _find(mail, 'has:attachment') == ['Invoice 42']
    assert _find(mail, '-has:attachment after:2026-11-12') == ['Lunch?']
    assert _find(mail, '"pay by friday"') == ['Invoice 42'] and _find(mail, 'nothing-like-this') == []
    assert _find(mail, 'to:krem', folder='sent') == ['Re: Invoice 42']


def test_everywhere_looks_in_every_folder_but_spam_and_trash(mail, box):
    assert _find(mail, 'invoice', everywhere=True) == ['Deals on invoices', 'Re: Invoice 42', 'Invoice 42', 'Old invoice']
    assert _find(mail, 'invoice in:all') == _find(mail, 'invoice', everywhere=True)
    assert _find(mail, 'to:krem', everywhere=True) == ['Re: Invoice 42']
    assert _find(mail, 'invoice', folder='spam') == ['Your invoice is overdue']         # spam only when named
    with pytest.raises(mail.mailbox.MailError, match='No folder named'):
        _find(mail, 'x', folder='Nope')


def test_gmail_is_searched_with_its_own_engine_in_all_mail(mail):
    fake = mail.use(mail.FakeIMAP(mail.GMAIL, caps=('MOVE', 'UIDPLUS', 'X-GM-EXT-1')))
    a = fake.add('[Gmail]/All Mail', mail.raw(subject='One'))
    b = fake.add('[Gmail]/All Mail', mail.raw(subject='Two'))
    fake.gm_hits = {b}
    from plugins.email import search
    with mail.mailbox.Mailbox('work', mail.creds) as mb:
        rows, total = mb.find(search.parse('to:ddxfish has:attachment label:work'), 'inbox', True, 50)
        assert [r['id'] for r in rows] == [f'archive:{b}'] and total == 1
        assert ('gmail_search', 'to:ddxfish has:attachment label:work') in fake.calls
        assert fake.selected == '[Gmail]/All Mail'
        assert rows[0]['where'] == 'archive'                                           # no labels: only in All Mail
        fake.boxes['[Gmail]/All Mail'][a]['labels'] = (b'\\Inbox', b'\\Important')
        fake.boxes['[Gmail]/All Mail'][b]['labels'] = (b'\\Sent',)
        c = fake.add('[Gmail]/All Mail', mail.raw(subject='Three'))
        fake.boxes['[Gmail]/All Mail'][c]['labels'] = (b'\\Important', b'Clients')
        fake.gm_hits = None
        rows, _ = mb.find(search.parse('x'), 'inbox', True, 50)
        assert [(r['subject'], r['where']) for r in rows] == [('Three', 'Clients'), ('Two', 'sent'), ('One', 'inbox')]
        fake.calls.clear()
        mb.find(search.parse('is:answered'), 'inbox', False, 50)                       # Gmail's box cannot say this one
        assert ('search', ['ANSWERED'], None) in fake.calls and not [c for c in fake.calls if c[0] == 'gmail_search']


# -- her tool ---------------------------------------------------------------------------

def test_her_search_takes_the_same_words_and_looks_everywhere(mail, box):
    text, ok = mail.tool.execute('search_emails', {'query': 'invoice -from:news'}, None)
    assert ok, text
    lines = text.splitlines()
    assert lines[1] == 'Search "invoice -from:news" in all folders: 3 shown, 0 unread'
    assert [l.split('] ')[0].strip() for l in lines[2:5]] == [f"[sent:{box['sent']}", f"[inbox:{box['invoice']}", f"[Receipts:{box['old']}"]
    assert ' - to Krem: Re: Invoice 42' in lines[2] and ' - Ann Lee: Invoice 42 (attachment)' in lines[3]
    assert 'ann@x.com' not in text                                                    # names only, as everywhere in her tools

    text, ok = mail.tool.execute('search_emails', {'query': 'to:krem'}, None)
    assert ok and f"[sent:{box['sent']}]" in text and text.count('\n  [') == 1
    text, ok = mail.tool.execute('search_emails', {'query': 'invoice', 'folder': 'spam'}, None)
    assert ok and 'in spam: 1 shown' in text and 'Your invoice is overdue' in text
    text, ok = mail.tool.execute('search_emails', {'query': 'invoice in:inbox', 'count': 1}, None)
    assert ok and 'in inbox: 1 shown' in text and '(2 matched; these are the newest 1.' in text
    text, ok = mail.tool.execute('search_emails', {'query': 'unicorns'}, None)
    assert ok and 'nothing matched' in text and 'Folders: inbox' in text

    for args, why in (({'query': 'after:soon'}, 'not a date'), ({'query': 'is:purple'}, 'is not known'),
                      ({'query': 'x', 'sender': 'ann'}, 'not both'), ({'query': 'x', 'folder': 'Nope'}, 'No folder named')):
        text, ok = mail.tool.execute('search_emails', args, None)
        assert not ok and why in text, args
    text, ok = mail.tool.execute('search_emails', {'sender': 'ann'}, None)             # the old fields still work
    assert ok and 'Invoice 42' in text


# -- the page's box ---------------------------------------------------------------------

def test_the_search_box_searches_this_folder_or_all(page, box):
    here = page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'invoice'})
    assert [r['subject'] for r in here['rows']] == ['Deals on invoices', 'Invoice 42']
    assert here['everywhere'] is False and here['validity'] and all(r['folder'] == 'inbox' for r in here['rows'])
    everywhere = page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'invoice', 'all': '1'})
    assert [(r['folder'], r['subject']) for r in everywhere['rows']] == [
        ('inbox', 'Deals on invoices'), ('sent', 'Re: Invoice 42'), ('inbox', 'Invoice 42'), ('Receipts', 'Old invoice')]
    assert (everywhere['everywhere'], everywhere['validity'], everywhere['total'], everywhere['folder']) == (True, 0, 4, 'inbox')
    by_words = page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'to:krem in:sent'})
    assert [r['subject'] for r in by_words['rows']] == ['Re: Invoice 42'] and by_words['folder'] == 'sent'
    unread = page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'invoice', 'unread': '1'})
    assert [r['subject'] for r in unread['rows']] == ['Deals on invoices']
    bad = page.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'after:soon'})
    assert bad[1] == 400 and 'not a date' in bad[0]['detail']
    # a result from another folder opens and moves by its own id
    sent_id = by_words['rows'][0]['id']
    got = page.routes.get_message(query={'scope': 'work', 'id': sent_id, 'validity': '0'})
    assert got['subject'] == 'Re: Invoice 42'


def test_her_gmail_results_say_where_the_mail_really_is(mail):
    fake = mail.use(mail.FakeIMAP(mail.GMAIL, caps=('MOVE', 'UIDPLUS', 'X-GM-EXT-1')))
    a = fake.add('[Gmail]/All Mail', mail.raw(frm='Ann Lee <ann@x.com>', subject='Hello'))
    b = fake.add('[Gmail]/All Mail', mail.raw(frm='me@x.com', to='Krem <krem@fish.com>', subject='Re: Hello'))
    fake.boxes['[Gmail]/All Mail'][a]['labels'] = (b'\\Inbox',)
    fake.boxes['[Gmail]/All Mail'][b]['labels'] = (b'\\Sent',)
    text, ok = mail.tool.execute('search_emails', {'query': 'hello'}, None)
    assert ok, text
    assert f"[archive:{b}]" in text and "- to Krem: Re: Hello" in text and "(in sent)" in text
    assert f"[archive:{a}]" in text and "- Ann Lee: Hello" in text and "(in inbox)" in text
    assert "These ids are from Gmail's All Mail" in text and "works as it is for read, move and mark" in text
