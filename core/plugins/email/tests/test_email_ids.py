"""The check code in every email id (`inbox:4821~k7p`): an id says which
account, folder and folder generation it belongs to, and is refused anywhere
else. These tests run with the code ON; the others write plain ids."""
import json
import re

import pytest

ID = re.compile(r'\[((?:[^\]\s]+):\d+~[a-z2-9]{3})\]')


@pytest.fixture
def tagged(mail, monkeypatch):
    monkeypatch.setattr(mail.mailbox, '_TAGS', True)
    import plugins.email.routes.mail as routes
    mail.routes = routes
    return mail


def _run(mail, name, **args):
    return mail.tool.execute(name, args, None)


def _ids(text):
    return ID.findall(text)


def test_listed_ids_carry_a_code_and_work_whole(tagged):
    m = tagged
    a = m.imap.add('INBOX', m.raw(subject='One'))
    m.imap.add('Sent', m.raw(subject='Out'))
    text, ok = _run(m, 'get_inbox')
    (one,) = _ids(text)
    assert ok and one.startswith(f'inbox:{a}~') and len(one.split('~')[1]) == 3
    (out,) = _ids(_run(m, 'get_inbox', folder='sent')[0])
    assert out.split('~')[1] != one.split('~')[1]                       # another folder, another code
    text, ok = _run(m, 'read_email', id=one)
    assert ok and 'Subject: One' in text
    assert _run(m, 'read_email', id=f'[{one}]')[1]                      # copied with the listing's brackets
    assert _run(m, 'mark_emails', ids=[one], mark='starred') == ('Marked 1 email starred.', True)


def test_an_id_without_its_code_or_with_the_wrong_one_is_refused(tagged):
    m = tagged
    a = m.imap.add('INBOX', m.raw(subject='One'))
    (one,) = _ids(_run(m, 'get_inbox')[0])
    text, ok = _run(m, 'read_email', id=f'inbox:{a}')
    assert not ok and 'missing its check code' in text and 'inbox:4821~k7p' in text
    wrong = one[:-1] + ('a' if one[-1] != 'a' else 'b')
    text, ok = _run(m, 'read_email', id=wrong)
    assert not ok and 'not for this mailbox as it is now' in text
    m.imap.add('INBOX', m.raw(subject='Two'))
    two = [i for i in _ids(_run(m, 'get_inbox')[0]) if i != one][0]
    text, ok = _run(m, 'move_emails', ids=[wrong, two], to='trash')     # the good one still moves
    assert ok and 'Two' in text and 'not for this mailbox' in text and m.imap.subjects('Trash') == ['Two']


def test_an_id_from_another_account_does_not_act_on_this_one(tagged, monkeypatch):
    """A chat lists 'work', the person switches it to 'personal', she says
    'trash that one'. The number exists in both inboxes."""
    m = tagged
    uid = m.imap.add('INBOX', m.raw(subject='The work mail'))
    (work_id,) = _ids(_run(m, 'get_inbox')[0])
    other = dict(m.creds, address='me@personal.org')                    # same server shape, same uid, another mailbox
    monkeypatch.setattr(m.accounts, 'get_creds', lambda scope: (dict(other), None))
    for name, args in (('read_email', {'id': work_id}), ('send_email', {'body': 'x', 'reply_to': work_id})):
        text, ok = _run(m, name, **args)
        assert not ok and 'another account' in text, name
    text, ok = _run(m, 'move_emails', ids=[work_id], to='trash')
    assert 'another account' in text and uid in m.imap.boxes['INBOX'] and not m.smtp.send_message.called


def test_an_id_from_before_a_folder_rebuild_is_refused_whoever_listed_since(tagged):
    m = tagged
    m.imap.add('INBOX', m.raw(subject='Before'))
    (old,) = _ids(_run(m, 'get_inbox')[0])
    m.imap.validity['INBOX'] = 777                                      # the server rebuilt the folder
    from plugins.email import daemon
    daemon._check_account('work', dict(m.creds))                        # the daemon polls...
    _run(m, 'search_emails', query='in:all Before')                     # ...another chat searches everything
    text, ok = _run(m, 'read_email', id=old)
    assert not ok and 'rebuilt the folder' in text                      # and the old id still does not pass
    (fresh,) = _ids(_run(m, 'get_inbox')[0])
    assert fresh != old and _run(m, 'read_email', id=fresh)[1]
    assert not (m.state.data.get('validity') or {})                     # nothing is stored for any of this


def test_the_page_acts_on_a_mail_by_its_own_id_whatever_folder_is_showing(tagged):
    m = tagged
    m.imap.add('INBOX', m.raw(subject='In'))
    r = m.imap.add('Receipts', m.raw(subject='Receipt', body='pay invoice'))
    found = m.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'invoice', 'all': '1'})
    (hit,) = found['rows']
    assert hit['id'].startswith(f'Receipts:{r}~') and hit['folder'] == 'Receipts'
    m.routes.folder_view(query={'scope': 'work', 'folder': 'inbox', 'counts': '0'})     # the search is cleared: inbox shows
    got = m.routes.get_message(query={'scope': 'work', 'id': hit['id'], 'read': '1'})   # the open mail still works
    assert got['subject'] == 'Receipt'
    done = m.routes.mark(body={'scope': 'work', 'ids': [hit['id']], 'mark': 'starred'})
    assert done['success'] and done['changed'] == 1
    moved = m.routes.move(body={'scope': 'work', 'ids': [hit['id']], 'to': 'trash'})
    assert moved['success'] and len(moved['moved']) == 1
    stale = m.routes.get_message(query={'scope': 'work', 'id': hit['id'][:-1] + ('a' if hit['id'][-1] != 'a' else 'b')})
    assert stale[1] == 400 and 'not for this mailbox' in stale[0]['detail']


def test_the_index_keeps_whole_ids(tagged):
    m = tagged
    m.imap.add('INBOX', m.raw(subject='One'))
    live = m.routes.folder_view(query={'scope': 'work', 'counts': '0'})
    kept = m.routes.folder_view(query={'scope': 'work', 'cached': '1'})
    assert [r['id'] for r in kept['rows']] == [r['id'] for r in live['rows']] and '~' in kept['rows'][0]['id']


def test_daemon_events_carry_the_code_and_an_old_bare_one_still_answers(tagged, monkeypatch):
    m = tagged
    from plugins.email import daemon as d
    events = []
    monkeypatch.setattr(d, '_plugin_loader', type('L', (), {'emit_daemon_event': lambda s, k, p: events.append(json.loads(p))})())
    d._stop_event.clear()
    m.imap.add('INBOX', m.raw(subject='Old'))
    d._check_account('work', dict(m.creds))
    uid = m.imap.add('INBOX', m.raw(frm='Ann <ann@x.com>', subject='New one'))
    d._check_account('work', dict(m.creds))
    assert re.fullmatch(rf'inbox:{uid}~[a-z2-9]{{3}}', events[0]['uid'])
    task = {'name': 't', 'trigger_config': {'auto_reply': True}}
    d._reply_handler(task, {'uid': events[0]['uid'], 'account': 'work'}, 'Hello back')
    assert m.smtp.send_message.call_count == 1
    again = m.imap.add('INBOX', m.raw(frm='Bob <bob@q.io>', subject='Queued before the upgrade'))
    d._reply_handler(task, {'uid': f'inbox:{again}', 'account': 'work'}, 'Hello')       # no code: the daemon may
    d._reply_handler(task, {'uid': str(again + 0), 'account': 'work'}, 'Hello')         # a bare number from long ago
    assert m.smtp.send_message.call_count == 2                                          # (the second is 'answered already')


def test_gmail_search_hits_say_where_they_are_with_coded_ids(tagged):
    m = tagged
    fake = m.use(m.FakeIMAP(m.GMAIL, caps=('MOVE', 'UIDPLUS', 'X-GM-EXT-1')))
    a = fake.add('[Gmail]/All Mail', m.raw(frm='Ann Lee <ann@x.com>', subject='Hello'))
    fake.boxes['[Gmail]/All Mail'][a]['labels'] = (b'\\Inbox',)
    found = m.routes.folder_view(query={'scope': 'work', 'counts': '0', 'q': 'hello', 'all': '1'})
    (hit,) = found['rows']
    assert hit['where'] == 'inbox' and hit['id'].startswith(f'archive:{a}~')
    text, ok = _run(m, 'search_emails', query='hello')
    assert ok and '(in inbox)' in text
    moved = m.routes.move(body={'scope': 'work', 'ids': [hit['id']], 'to': 'archive'})        # Archive from the search
    assert len(moved['moved']) == 1 and fake.boxes['[Gmail]/All Mail'][a]['labels'] == ()
