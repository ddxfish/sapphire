"""daemon.py: which mail fires an event, and which mail gets an auto-reply."""
import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def daemon(mail, monkeypatch):
    from plugins.email import daemon as d
    loader = MagicMock()
    monkeypatch.setattr(d, '_plugin_loader', loader)
    d._stop_event.clear()
    mail.events = lambda: [json.loads(c.args[1]) for c in loader.emit_daemon_event.call_args_list]
    return d


TASK = {'name': 't', 'trigger_config': {'auto_reply': True}}


def test_first_look_fires_nothing_then_only_new_unread_mail_does(mail, daemon):
    mail.imap.add('INBOX', mail.raw(subject='already here'))
    daemon._check_account('work', mail.creds)
    assert mail.events() == []
    mail.imap.add('INBOX', mail.raw(subject='read on the phone'), flags={b'\\Seen'})
    uid = mail.imap.add('INBOX', mail.raw(frm='Alice <alice@x.com>', subject='new one', body='hello   there'))
    daemon._check_account('work', mail.creds)
    (event,) = mail.events()
    assert event == {'account': 'work', 'from_name': 'Alice', 'from_address': 'alice@x.com',
                     'to_address': 'me@x.com', 'subject': 'new one', 'snippet': 'hello there',
                     'uid': f'inbox:{uid}', 'bulk': False}
    daemon._check_account('work', mail.creds)
    assert len(mail.events()) == 1                     # never twice


def test_mail_that_arrived_while_she_was_down_still_fires(mail, daemon):
    daemon._check_account('work', mail.creds)          # the mark is saved in plugin state
    mail.imap.add('INBOX', mail.raw(subject='while down'))
    mail.mailbox._folder_cache.clear()                 # "restart": nothing in memory survives
    daemon._check_account('work', mail.creds)
    assert [e['subject'] for e in mail.events()] == ['while down']


def test_own_mail_is_skipped_and_bulk_mail_is_flagged(mail, daemon):
    daemon._check_account('work', mail.creds)
    mail.imap.add('INBOX', mail.raw(frm='ME@x.com', subject='my own copy'))
    mail.imap.add('INBOX', mail.raw(frm='News <news@shop.com>', subject='Sale', headers={'List-Id': '<n.shop.com>'}))
    daemon._check_account('work', mail.creds)
    assert [(e['subject'], e['bulk']) for e in mail.events()] == [('Sale', True)]


def test_a_rebuilt_inbox_starts_over_without_a_flood(mail, daemon):
    daemon._check_account('work', mail.creds)
    for i in range(3):
        mail.imap.add('INBOX', mail.raw(subject=f'm{i}'))
    mail.imap.validity['INBOX'] = 9
    daemon._check_account('work', mail.creds)
    assert mail.events() == []


def test_a_flood_fires_only_the_newest(mail, daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'MAX_PER_POLL', 2)
    daemon._check_account('work', mail.creds)
    for i in range(5):
        mail.imap.add('INBOX', mail.raw(subject=f'm{i}'))
    daemon._check_account('work', mail.creds)
    assert [e['subject'] for e in mail.events()] == ['m3', 'm4']


def test_auto_reply_is_threaded_quoted_and_marked_automatic(mail, daemon):
    uid = mail.imap.add('INBOX', mail.raw(frm='Alice <alice@x.com>', subject='Hi', body='How are you?',
                                         headers={'Reply-To': 'alice-home@x.com'}))
    daemon._reply_handler(TASK, {'account': 'work', 'uid': f'inbox:{uid}'}, '<think>hm</think>Doing well!')
    msg, rcpts = mail.sent()
    assert rcpts == ['alice-home@x.com']               # Reply-To is honored
    assert str(msg['Subject']) == 'Re: Hi' and msg['In-Reply-To'] and msg['Auto-Submitted'] == 'auto-replied'
    body = msg.get_body(('plain',)).get_content()
    assert body.startswith('Doing well!') and '> How are you?' in body and 'hm' not in body
    assert mail.imap.subjects('Sent') == ['Re: Hi']


@pytest.mark.parametrize("kw", [{'headers': {'Auto-Submitted': 'auto-replied'}},
                                {'headers': {'Precedence': 'bulk'}},
                                {'frm': 'noreply@shop.com'}])
def test_automated_mail_never_gets_an_auto_reply(mail, daemon, kw):
    uid = mail.imap.add('INBOX', mail.raw(**kw))
    daemon._reply_handler(TASK, {'account': 'work', 'uid': f'inbox:{uid}'}, 'Thanks!')
    mail.smtp.send_message.assert_not_called()


def test_no_reply_when_the_task_has_auto_reply_off_or_the_answer_is_empty(mail, daemon):
    uid = mail.imap.add('INBOX', mail.raw())
    daemon._reply_handler({'trigger_config': {}}, {'account': 'work', 'uid': f'inbox:{uid}'}, 'Hello')
    daemon._reply_handler(TASK, {'account': 'work', 'uid': f'inbox:{uid}'}, '<think>only thoughts</think>')
    mail.smtp.send_message.assert_not_called()


def test_an_event_queued_before_the_upgrade_still_replies(mail, daemon):
    uid = mail.imap.add('INBOX', mail.raw())
    daemon._reply_handler(TASK, {'account': 'work', 'uid': str(uid)}, 'Hello')
    assert mail.sent()[1] == ['alice@x.com']
