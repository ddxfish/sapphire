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
                 stream_speech=False, **kw):
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
    from core import cadence
    eng = Engine(fail=cadence.Unreachable("chat 'desk' isn't reachable (missing, sealed or unreadable)"))
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='hello', lane='later'))
        assert _wait(lambda: item.reply.done())
        with pytest.raises(RuntimeError, match='dropped'):
            item.reply.result()
        time.sleep(0.1)
    assert inbox.depth('desk') == 0


def test_a_provider_error_that_sounds_unreachable_is_still_a_failed_turn(fast):
    """'model x not found' matched the old UNREACHABLE substrings and was
    dropped silently (chaos/day-ruiner scouts, 2026-10-07). The drop is typed now."""
    eng = Engine(fail=RuntimeError("model 'llama-x' not found - does not exist on this host"))
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='hello', lane='later'))
        assert _wait(lambda: item.reply.done())
        with pytest.raises(RuntimeError, match='not found'):
            item.reply.result()
    assert inbox.depth('desk') == 0


def test_a_person_is_never_refused_by_the_depth_cap(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    monkeypatch.setattr(inbox, 'DEPTH_MAX', 2)
    inbox.put('desk', inbox.Item(text='1', lane='later'))
    inbox.put('desk', inbox.Item(text='2', lane='later'))
    with pytest.raises(inbox.InboxRefused, match='full'):
        inbox.put('desk', inbox.Item(text='3', lane='later'))         # a machine waits its turn elsewhere
    for i in range(5):
        inbox.put('desk', inbox.Item(run=lambda: None, lane='now', source='web'))   # people always get in line
    assert inbox.depth('desk') == 7


def test_an_asker_that_gives_up_takes_its_item_back(fast, monkeypatch):
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    with pytest.raises(Exception):
        inbox.ask('desk', 'quick question', source='mcp:test', timeout=0.05)
    assert inbox.depth('desk') == 0, 'the timed-out ask would have run later as a ghost turn'


def test_a_chat_that_cannot_be_read_right_now_holds_its_items(fast, monkeypatch):
    """read_chat_settings answers None for missing, sealed AND unreadable; only
    a chat that is GONE is refused (a backup's lock dropped everything)."""
    from types import SimpleNamespace
    sm = SimpleNamespace(is_chat_hidden=lambda c: False, read_chat_settings=lambda c: None,
                         chat_exists=lambda c: c == 'desk')
    monkeypatch.setattr(inbox, '_system', lambda: SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm)))
    assert inbox._refusal('desk') == ''
    assert 'no chat named' in inbox._refusal('ghost')


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


def test_a_doors_own_gate_is_asked_again_when_the_item_is_about_to_run(fast, monkeypatch):
    """An MCP ask checked 'is this chat private?' at put time only: a chat that
    turned private while the ask waited ran with the private history and the
    reply left over the network (privacy scout, 2026-10-07)."""
    private = {'now': False}
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)          # hold the line
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='quick question', lane='later', source='mcp:test',
                                            gate=lambda: 'The chat is private now.' if private['now'] else ''))
        private['now'] = True                                           # the eyeball toggles while it waits
        monkeypatch.setattr(inbox, '_idle_hint', lambda c: True)
        inbox.kick('desk')
        assert _wait(lambda: item.reply.done())
        with pytest.raises(RuntimeError, match='private now'):
            item.reply.result()
    assert eng.calls == [], 'the turn must not run'
    # and a gate that says go lets it through
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='fine', lane='later', source='mcp:test', gate=lambda: ''))
        assert _wait(lambda: item.reply.done()) and eng.calls


def test_the_mcp_door_regates_privacy_at_run_time():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'core' / 'mcp_server.py').read_text(encoding='utf-8')
    assert src.count("gate=lambda: f\"The chat '{name}' is private now.\" if _private(system, name) else ''") == 2


def test_privacy_rolls_downhill_a_message_queued_while_private_never_runs_public(fast, monkeypatch):
    """Krem, 2026-10-07: 'she is typing, I queue, then ... my queued message goes to
    the provider at THAT moment it fires?' A turn reads the chat's privacy when it
    runs, so public → private goes local; private → public must NOT go cloud."""
    from types import SimpleNamespace
    privacy = {'desk': True}
    sm = SimpleNamespace(is_chat_hidden=lambda c: False,
                         read_chat_settings=lambda c: {'private_chat': privacy.get(c, False)},
                         chat_exists=lambda c: True)
    monkeypatch.setattr(inbox, '_system', lambda: SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='my private words', lane='later', source='web'))
        assert item.private_at_put is True
        privacy['desk'] = False                       # the eyeball flips the chat public while it waits
        monkeypatch.setattr(inbox, '_idle_hint', lambda c: True)
        inbox.kick('desk')
        assert _wait(lambda: item.reply.done())
        with pytest.raises(RuntimeError, match='was private'):
            item.reply.result()
        assert eng.calls == []
        # public at put, private at run: runs (the turn goes local by itself)
        item2 = inbox.put('desk', inbox.Item(text='public words', lane='later', source='web'))
        assert item2.private_at_put is False
        privacy['desk'] = True
        inbox.kick('desk')
        assert _wait(lambda: item2.reply.done()) and eng.calls


def test_a_privacy_reading_that_failed_at_put_never_arms_the_drop(fast, monkeypatch):
    """A sqlite hiccup at put() read as 'private' (fail closed) and the ratchet
    then DROPPED a typed turn as 'queued while private' once the chat read
    public again - fail-closed plus rolls-downhill failed by dropping (seam
    scout, 2026-10-07). The stamp has three states now; only a known-private
    reading arms the drop."""
    from types import SimpleNamespace
    state = {'raise': True}

    def read(c):
        if state['raise']:
            raise RuntimeError('database is locked')
        return {'private_chat': False}
    sm = SimpleNamespace(is_chat_hidden=lambda c: False, read_chat_settings=read, chat_exists=lambda c: True)
    monkeypatch.setattr(inbox, '_system', lambda: SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='typed during a hiccup', lane='later', source='web'))
        assert item.private_at_put is None                 # unknown, not private
        state['raise'] = False                             # the database is back, the chat is public
        monkeypatch.setattr(inbox, '_idle_hint', lambda c: True)
        inbox.kick('desk')
        assert _wait(lambda: item.reply.done()) and eng.calls, 'the typed turn was dropped by a false ratchet'
        item.reply.result()
        # and a known-private item whose chat is UNREADABLE at run time is not
        # read as "public now" either: it holds (the gate judges), it is not dropped
        state['raise'] = False
        sm.read_chat_settings = lambda c: {'private_chat': True}
        item2 = inbox.put('desk', inbox.Item(text='private words', lane='later', source='web'))
        assert item2.private_at_put is True
        assert inbox._chat_private('desk') is True


def test_a_database_that_cannot_be_read_at_run_time_holds_the_item(fast, monkeypatch):
    """The put-time hold (chat_exists raises → hold) was undone at run time:
    run_turn read None for 'can't read right now' and 'gone' alike, raised
    Unreachable, and the inbox dropped the report (seam scout, 2026-10-07).
    Unreadable is its own error now and the item is pushed back and retried."""
    from core import cadence
    from types import SimpleNamespace
    monkeypatch.setattr(inbox, 'BACKOFF_MIN', 0.05)
    monkeypatch.setattr(inbox, 'BACKOFF_MAX', 0.05)
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: True)
    calls = []

    def run_turn(chat, text, **kw):
        calls.append(text)
        if len(calls) < 3:
            raise cadence.Unreadable("chat 'desk' can't be read right now")
        return 'ran'
    with patch('core.cadence.run_turn', run_turn):
        item = inbox.put('desk', inbox.Item(text='agent report', lane='later', source='agent:llm'))
        assert _wait(lambda: item.reply.done(), 5)
        assert item.reply.result() == 'ran' and len(calls) == 3        # held and retried, never dropped
    # the organ tells the two apart: a read error is Unreadable, gone/sealed is Unreachable
    sm = SimpleNamespace(chat_exists=lambda c: (_ for _ in ()).throw(RuntimeError('locked')),
                         get_settings_for=lambda c: None)
    monkeypatch.setattr(cadence, '_system', SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm)))
    with pytest.raises(cadence.Unreadable):
        cadence.run_turn('desk', 'x')
    sm.chat_exists = lambda c: False
    with pytest.raises(cadence.Unreachable):
        cadence.run_turn('desk', 'x')


def test_shutdown_drops_every_waiting_item_with_its_on_drop(fast, monkeypatch):
    """A graceful restart used to just die on what waited: twilio's plain-write
    fallback never ran, MCP waiters timed out (day-ruiner scout, 2026-10-07)."""
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)      # nothing runs: her turn never ends
    dropped = []
    a = inbox.put('desk', inbox.Item(text='call transcript', lane='later', source='twilio',
                                    on_drop=lambda why: dropped.append(('desk', why))))
    b = inbox.put('den', inbox.Item(text='a take', lane='later', source='midi',
                                   on_drop=lambda why: dropped.append(('den', why))))
    assert inbox.shutdown() == 2
    assert sorted(dropped) == [('den', 'Sapphire is restarting'), ('desk', 'Sapphire is restarting')]
    assert a.reply.done() and b.reply.done() and inbox.peek('desk') == []


def test_a_sealed_chat_holds_a_machines_item_and_refuses_a_persons_turn(fast, monkeypatch):
    """A Claude Code session in a vaulted chat ran past the idle-lock; its
    report hit `sealed` → InboxRefused → lost, while the log said "kept on the
    row" (chaos scout, 2026-10-07). Rows are metadata-only and the content
    store refuses a hidden chat: the only place the report can live is HERE,
    until the unlock. A person's typed turn is still refused - they are there."""
    from types import SimpleNamespace
    hidden = {'desk': True}
    sm = SimpleNamespace(is_chat_hidden=lambda c: hidden.get(c, False),
                         read_chat_settings=lambda c: None if hidden.get(c) else {'private_chat': False},
                         chat_exists=lambda c: True, get_settings_for=lambda c: None if hidden.get(c) else {})
    monkeypatch.setattr(inbox, '_system', lambda: SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: True)
    monkeypatch.setattr(inbox, 'BACKOFF_MAX', 0.05)
    with pytest.raises(inbox.InboxRefused, match='sealed'):
        inbox.put('desk', inbox.Item(run=lambda: 'typed', source='web', lane='now'))
    eng = Engine()
    with patch('core.cadence.run_turn', eng):
        item = inbox.put('desk', inbox.Item(text='the agent report', lane='later', source='agent:claude_code'))
        time.sleep(0.4)
        assert not item.reply.done() and inbox.peek('desk'), 'the report was dropped instead of held'
        hidden['desk'] = False                           # the vault unlocks
        inbox.kick('desk')
        assert _wait(lambda: item.reply.done(), 5) and eng.calls and 'the agent report' in eng.calls[0]['text']


def test_drops_are_typed_so_a_door_can_tell_privacy_from_stale(fast, monkeypatch):
    """The twilio door wrote a PRIVACY-dropped transcript into the (now public)
    chat by hand - the inbox said no and the door did it anyway (privacy scout,
    2026-10-07). on_drop now sees WHY on the item."""
    kinds = []
    it = inbox.put('desk', inbox.Item(text='x', lane='later', source='twilio',
                                     on_drop=lambda why: kinds.append(it.drop_kind)))
    assert inbox.drop('desk', it.ticket) and kinds == ['removed']
    monkeypatch.setattr(inbox, '_idle_hint', lambda c: False)
    it2 = inbox.put('desk', inbox.Item(text='y', lane='later', source='twilio', ttl=0.01,
                                      on_drop=lambda why: kinds.append(it2.drop_kind)))
    time.sleep(0.05)
    inbox._expire(inbox._chat('desk'))
    assert kinds[-1] == 'stale'
    it3 = inbox.put('desk', inbox.Item(text='z', lane='later', source='twilio',
                                      on_drop=lambda why: kinds.append(it3.drop_kind)))
    inbox.drop_chat('desk', 'Sapphire is restarting', 'restart')
    assert kinds[-1] == 'restart'
    src = (__import__('pathlib').Path(__file__).resolve().parent.parent / 'plugins' / 'twilio-voice' / 'daemon.py').read_text(encoding='utf-8')
    assert "if item.drop_kind in ('privacy', 'sealed', 'gone'):" in src
    assert '_fallback()' not in src.split('def _watch():')[1].split('threading.Thread')[0], 'a failed turn is not re-written'
