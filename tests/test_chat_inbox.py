# tests/test_chat_inbox.py - the per-chat queue (core/chat/inbox.py)
#
# The engine is faked: core.cadence.run_turn is a recorder that can raise
# ChatBusy on demand, there is no system singleton (the inbox then lets
# begin_stream judge), and the organ counts as started.
import threading
import time
from unittest.mock import patch

import pytest

from core.chat import inbox
from core.chat.chat import ChatBusy


class Engine:
    """A stand-in run_turn: records calls, raises ChatBusy `busy` times first."""

    def __init__(self, busy=0, fail=None, reply='ok'):
        self.calls = []
        self.busy = busy
        self.fail = fail
        self.reply = reply
        self.ran = threading.Event()

    def __call__(self, chat, text, images=None, speak=None, source='cadence', on_event=None,
                 stream_speech=False):
        if self.busy > 0:
            self.busy -= 1
            raise ChatBusy(chat)
        if self.fail is not None:
            raise self.fail
        self.calls.append({'chat': chat, 'text': text, 'speak': speak, 'source': source,
                           'images': images, 'stream_speech': stream_speech})
        self.ran.set()
        return self.reply


@pytest.fixture
def fast(monkeypatch):
    inbox._chats.clear()
    monkeypatch.setattr(inbox, 'COALESCE_S', 0.15)
    monkeypatch.setattr(inbox, 'SWEEP_S', 0.03)
    monkeypatch.setattr(inbox, 'BACKOFF_MIN', 0.005)
    monkeypatch.setattr(inbox, 'BACKOFF_MAX', 0.02)
    monkeypatch.setattr(inbox, 'DRAINER_IDLE_EXIT_S', 0.2)
    monkeypatch.setattr(inbox, '_system', lambda: None)
    monkeypatch.setattr(inbox, '_cadence_ready', lambda: True)
    yield
    for name in list(inbox._chats):
        inbox.drop_chat(name, 'test over')      # a drainer must not carry an item into the next test
    inbox._chats.clear()


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_idle_chat_runs_the_item_and_resolves_the_reply(fast):
    eng = Engine(reply='It is noon.')
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='what time is it', source='midi', lane='later'))
        assert item.reply.result(timeout=3) == 'It is noon.'
    assert [c['text'] for c in eng.calls] == ['what time is it']
    assert eng.calls[0]['source'] == 'midi'


def test_a_busy_chat_is_waited_for_never_dropped(fast):
    eng = Engine(busy=2)
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='hello', lane='later'))
        assert item.reply.result(timeout=3) == 'ok'
    assert len(eng.calls) == 1               # ChatBusy twice, then it ran


def test_a_person_goes_before_a_machine(fast, monkeypatch):
    hold = {'idle': False}
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: hold['idle'])
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        inbox.put('desk', inbox.Item(text='report A', source='agent:x', lane='later', coalesce=False))
        inbox.put('desk', inbox.Item(text='Krem spoke', source='device:pi', lane='now'))
        hold['idle'] = True
        inbox.kick('desk')
        assert _wait(lambda: len(eng.calls) == 2)
    assert [c['text'] for c in eng.calls] == ['Krem spoke', 'report A']


def test_foldable_items_land_as_one_turn_with_headers(fast, monkeypatch):
    hold = {'idle': False}
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: hold['idle'])
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        for n in ('Spark', 'Alpha', 'Mason'):
            inbox.put('desk', inbox.Item(text=f'{n} is done', source=f'agent:{n}', lane='later',
                                         coalesce=True, header=inbox.header(f'Agent {n}', 'llm', 'done')))
        hold['idle'] = True
        inbox.kick('desk')
        assert _wait(lambda: len(eng.calls) == 1)
        time.sleep(0.1)
    assert len(eng.calls) == 1
    text = eng.calls[0]['text']
    assert text.index('Spark is done') < text.index('Alpha is done') < text.index('Mason is done')
    assert text.count('not typed by the user') == 3
    assert 'more waiting' not in text


def test_a_machine_that_waits_for_its_answer_runs_alone(fast, monkeypatch):
    hold = {'idle': False}
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: hold['idle'])
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        inbox.put('desk', inbox.Item(text='mcp question', source='mcp:x', lane='later', coalesce=False))
        inbox.put('desk', inbox.Item(text='report', source='agent:y', lane='later', coalesce=True))
        hold['idle'] = True
        inbox.kick('desk')
        assert _wait(lambda: len(eng.calls) == 2)
    assert eng.calls[0]['text'].startswith('mcp question')
    assert '[1 more waiting in the inbox]' in eng.calls[0]['text']   # a machine turn says what else waits
    assert eng.calls[1]['text'] == 'report'


def test_a_folded_turn_speaks_only_when_every_member_asked(fast, monkeypatch):
    hold = {'idle': False}
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: hold['idle'])
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        inbox.put('desk', inbox.Item(text='take 1', source='midi', lane='later', coalesce=True, speak='speakers'))
        inbox.put('desk', inbox.Item(text='take 2', source='midi', lane='later', coalesce=True, speak='speakers'))
        hold['idle'] = True
        inbox.kick('desk')
        assert _wait(lambda: len(eng.calls) == 1)
        assert eng.calls[0]['speak'] == 'speakers'
        hold['idle'] = False
        inbox.put('desk', inbox.Item(text='take 3', source='midi', lane='later', coalesce=True, speak='speakers'))
        inbox.put('desk', inbox.Item(text='take 4', source='midi', lane='later', coalesce=True))
        hold['idle'] = True
        inbox.kick('desk')
        assert _wait(lambda: len(eng.calls) == 2)
    assert eng.calls[1]['speak'] is None


def test_folding_is_same_kind_only(fast, monkeypatch):
    """Krem 2026-10-06: only combine types of returns together - a MIDI take
    never rides an agent's report, a person's turns never ride a machine's."""
    hold = {'idle': False}
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: hold['idle'])
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        inbox.put('desk', inbox.Item(text='report A', source='agent:a', lane='later', coalesce=True))
        inbox.put('desk', inbox.Item(text='take', source='midi', lane='later', coalesce=True))
        inbox.put('desk', inbox.Item(text='report B', source='agent:b', lane='later', coalesce=True))
        hold['idle'] = True
        inbox.kick('desk')
        assert _wait(lambda: len(eng.calls) == 3)
        time.sleep(0.1)
    assert [c['text'].split('\n')[0] for c in eng.calls] == ['report A', 'take', 'report B']   # order kept, nothing crossed


def test_typed_turns_standing_together_fold_through_run_folded(fast):
    """Three thoughts typed while she talks become one turn: the head item's
    run_folded gets the others; every item's reply is the one result."""
    seen = {}

    def head_run(others=()):
        seen['others'] = [o.payload['text'] for o in others]
        return 'one reply'
    gate = threading.Event()
    inbox.put('desk', inbox.Item(run=lambda: gate.wait(2) and 'first', source='web', lane='now'))   # the live one
    a = inbox.put('desk', inbox.Item(run=head_run, run_folded=head_run, fold_key='web', source='web', lane='now',
                                     payload={'text': 'dinner?'}))
    b = inbox.put('desk', inbox.Item(run=lambda: 'b', run_folded=lambda o: 'b', fold_key='web', source='web', lane='now',
                                     payload={'text': 'maybe pasta'}))
    c = inbox.put('desk', inbox.Item(run=lambda: 'sat', source='device:pi', lane='now'))             # a satellite: not folded
    d = inbox.put('desk', inbox.Item(run=lambda: 'd', run_folded=lambda o: 'd', fold_key='web', source='web', lane='now',
                                     payload={'text': 'and bread'}))
    gate.set()
    assert a.reply.result(timeout=3) == 'one reply' and b.reply.result(timeout=3) == 'one reply'
    assert seen['others'] == ['maybe pasta']                  # a + b folded; the satellite kept its place
    assert c.reply.result(timeout=3) == 'sat' and d.reply.result(timeout=3) == 'd'


def test_a_stale_item_is_dropped_and_its_source_told(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    told = []
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='old question', lane='now', ttl=0.02,
                                            on_drop=lambda why: told.append(why)))
        assert _wait(lambda: item.reply.done())
        with pytest.raises(RuntimeError, match='dropped'):
            item.reply.result()
    assert told and 'stale' in told[0]
    assert eng.calls == []


def test_the_depth_cap_refuses_with_a_reason(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    monkeypatch.setattr(inbox, 'DEPTH_MAX', 2)
    inbox.put('desk', inbox.Item(text='1', lane='later'))
    inbox.put('desk', inbox.Item(text='2', lane='later'))
    with pytest.raises(inbox.InboxRefused, match='full'):
        inbox.put('desk', inbox.Item(text='3', lane='later'))
    assert inbox.depth('desk') == 2


def test_a_door_runs_its_own_turn_when_it_is_its_turn(fast):
    tries = {'n': 0}

    def body():
        tries['n'] += 1
        if tries['n'] == 1:
            raise ChatBusy('desk')          # a typed turn won the race
        return 'spoken reply'

    assert inbox.turn('desk', body, source='voice') == 'spoken reply'
    assert tries['n'] == 2


def test_drop_removes_a_waiting_item(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    item = inbox.put('desk', inbox.Item(text='later', lane='later'))
    assert inbox.peek('desk')[0]['ticket'] == item.ticket
    assert inbox.drop('desk', item.ticket)
    assert inbox.depth('desk') == 0
    with pytest.raises(RuntimeError):
        item.reply.result()
    assert not inbox.drop('desk', item.ticket)


def test_delete_and_rename_carry_the_queue(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    inbox.put('old', inbox.Item(text='a', lane='later'))
    inbox.rename_chat('old', 'new')
    assert inbox.depth('old') == 0 and inbox.depth('new') == 1
    assert inbox.drop_chat('new', 'chat deleted') == 1
    assert inbox.depth('new') == 0


def test_peek_shows_ids_and_sources_never_text(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    inbox.put('desk', inbox.Item(text='the secret report', source='agent:q', lane='later'))
    row = inbox.peek('desk')[0]
    assert row['source'] == 'agent:q' and row['lane'] == 'later'
    assert 'secret' not in str(row)


def test_a_turn_may_not_wait_on_its_own_chat(fast, monkeypatch):
    from core.chat.function_manager import tool_context
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)       # the tell below stays queued (fixture drops it)
    token = tool_context.set({'chat': 'desk'})
    try:
        with pytest.raises(inbox.InboxRefused, match='her own turn'):
            inbox.ask('desk', 'are you there', source='mcp:self')
        with pytest.raises(inbox.InboxRefused):
            inbox.turn('desk', lambda: 'x')
        inbox.tell('desk', 'fine to say without waiting', source='mcp:self')   # a tell is allowed
        assert inbox.depth('desk') == 1
    finally:
        tool_context.reset(token)


def test_an_unreachable_chat_drops_the_item_without_retrying(fast):
    eng = Engine(fail=RuntimeError("chat 'desk' isn't reachable (missing, sealed or unreadable)"))
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='hello', lane='later'))
        assert _wait(lambda: item.reply.done())
        with pytest.raises(RuntimeError, match='dropped'):
            item.reply.result()
        time.sleep(0.1)
    assert inbox.depth('desk') == 0


def test_a_provider_error_reaches_the_waiter_and_is_not_retried(fast):
    eng = Engine(fail=ConnectionError('provider down'))
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='hello', lane='later'))
        with pytest.raises(ConnectionError):
            item.reply.result(timeout=3)
    assert inbox.depth('desk') == 0


def test_the_drainer_goes_home_when_idle_and_comes_back(fast):
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        inbox.put('desk', inbox.Item(text='one', lane='later')).reply.result(timeout=3)
        c = inbox._chats['desk']
        assert _wait(lambda: not c.alive, timeout=2)
        inbox.put('desk', inbox.Item(text='two', lane='later')).reply.result(timeout=3)
    assert [x['text'] for x in eng.calls] == ['one', 'two']


def test_text_items_wait_for_the_organ_but_doors_do_not(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_cadence_ready', lambda: False)
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='held', lane='later'))
        assert inbox.turn('desk', lambda: 'door ran') == 'door ran'
        time.sleep(0.1)
        assert not item.reply.done() and eng.calls == []
        monkeypatch.setattr(inbox, '_cadence_ready', lambda: True)
        assert item.reply.result(timeout=3) == 'ok'


def test_header_names_who_what_and_that_the_user_did_not_type_it():
    h = inbox.header('Agent Spark', 'claude_code', 'done in 4m12s')
    assert h == '[Agent Spark (claude_code) — done in 4m12s; not typed by the user]'
    assert inbox.header('MIDI').startswith('[MIDI;')


def test_put_refuses_an_empty_item_and_a_bad_lane(fast):
    with pytest.raises(inbox.InboxRefused):
        inbox.put('desk', inbox.Item(text='   ', lane='later'))
    with pytest.raises(inbox.InboxRefused):
        inbox.put('desk', inbox.Item(text='x', lane='soon'))
    with pytest.raises(inbox.InboxRefused):
        inbox.put('', inbox.Item(text='x'))


def test_an_item_runs_in_the_context_it_was_put_from(fast):
    import contextvars
    flag = contextvars.ContextVar('inbox_test_flag', default=False)
    seen = {}

    def body():
        seen['flag'] = flag.get()
        return 'done'

    token = flag.set(True)
    try:
        assert inbox.turn('desk', body) == 'done'
    finally:
        flag.reset(token)
    assert seen['flag'] is True
