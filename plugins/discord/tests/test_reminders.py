"""Reminders (2026-09-25, back in the host): rows in memory mirrored to the plugin
state file, taken before posting, no LLM; the tool is bound to the asker."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

from core.continuity.executor import current_event_data
from plugins.discord.models.settings import RemindersSettings
from plugins.discord.reminders import MAX_PENDING, Reminders, due_in_seconds, parse_at, parse_delay
from plugins.discord.tools import discord_tools as tools


class FakeState:
    def __init__(self, data=None):
        self.d = dict(data or {})

    def get(self, key, default=None):
        return self.d.get(key, default)

    def save(self, key, value):
        self.d[key] = value


class FakeTransport:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
        self._connected = ['alpha']
        self._accounts = {'alpha': {}}

    def list_connected(self):
        return list(self._connected)

    async def send_message_async(self, channel, text, reply_to_message_id=None, account_name=None, guild_id=None):
        if self.fail:
            raise RuntimeError('boom')
        self.sent.append((channel, text, account_name))
        return {'status': 'sent'}

    def channel_reach_sync(self, channel, account_name=None):
        return {'guild_id': 'g1', 'is_dm': False}


def test_parsers():
    assert parse_delay('2h') == 7200 and parse_delay('in 1d 2h') == 93600 and parse_delay('90 minutes') == 5400
    assert parse_delay('soon') is None and parse_delay('') is None and parse_delay('0m') is None
    now = datetime(2026, 9, 25, 17, 0)
    assert parse_at('18:30', now) == datetime(2026, 9, 25, 18, 30)
    assert parse_at('09:00', now) == datetime(2026, 9, 26, 9, 0)          # already past → tomorrow
    assert due_in_seconds(delay='30m') == 1800 and due_in_seconds(at='18:00', now=now) == 3600
    assert due_in_seconds() is None


def test_store_is_bound_per_person_and_mirrors_every_change():
    state = FakeState()
    r = Reminders(state=state)
    a = r.add('alpha', 'c1', 'u1', 'call mom', 100.0)
    b = r.add('alpha', 'c1', 'u2', 'stretch', 50.0)
    r.add('beta', 'c2', 'u1', 'other bot', 10.0)
    assert [x['id'] for x in r.pending('alpha')] == [b['id'], a['id']]      # due order
    assert [x['text'] for x in r.pending('alpha', 'u1')] == ['call mom']
    assert r.cancel('alpha', 'u1', reminder_id=b['id']) == 0                # not theirs
    assert r.cancel('alpha', 'u2', match='STRETCH') == 1
    assert r.cancel('alpha', 'u1') == 0                                     # no selector = nothing
    assert len(state.d['reminders_pending']) == 2
    again = Reminders(state=state)                                          # a restart
    assert [x['text'] for x in again.pending('alpha')] == ['call mom']
    assert again.add('alpha', 'c1', 'u1', 'new', 5.0)['id'] > a['id']       # ids keep counting


def test_delivery_takes_rows_before_posting_and_survives_a_failure():
    r = Reminders(state=FakeState())
    r.add('alpha', 'c1', 'u1', 'call mom', 100.0)
    r.add('alpha', 'c1', 'u1', 'later', 500.0)
    t = FakeTransport()
    rows = asyncio.run(r.deliver_async('alpha', t, now_ts=200.0))
    assert [x['text'] for x in rows] == ['call mom'] and t.sent == [('c1', '<@u1> Reminder: call mom', 'alpha')]
    assert [x['text'] for x in r.pending('alpha')] == ['later']
    r.add('alpha', 'c1', 'u1', 'lost', 1.0)
    rows = asyncio.run(r.deliver_async('alpha', FakeTransport(fail=True), now_ts=200.0))
    assert [x['text'] for x in rows] == ['lost'] and [x['text'] for x in r.pending('alpha')] == ['later']   # never retried


def _runtime(enabled=True):
    store = Reminders(state=FakeState())
    return SimpleNamespace(transport=FakeTransport(), reminders=store,
                           settings_store=SimpleNamespace(resolve=lambda: SimpleNamespace(reminders=RemindersSettings(enabled=enabled))))


def _in_event(monkeypatch, runtime, **ev):
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    token = current_event_data.set({'account': 'alpha', 'channel_id': 'c1', 'author_id': 'u1', 'message_id': '1', **ev})
    return token


def test_tool_inside_a_conversation_is_the_askers_own(monkeypatch):
    runtime = _runtime()
    token = _in_event(monkeypatch, runtime)
    try:
        msg, ok = tools.execute('discord_remind', {'action': 'add', 'text': 'call mom', 'delay': '2h', 'user': 'u9', 'channel': 'c9'})
        assert ok and 'Reminder #1 set' in msg
        row = runtime.reminders.pending('alpha')[0]
        assert row['user_id'] == 'u1' and row['channel_id'] == 'c1'          # user=/channel= ignored inside an event
        msg, ok = tools.execute('discord_remind', {'action': 'list'})
        assert ok and 'call mom' in msg
        msg, ok = tools.execute('discord_remind', {'action': 'add', 'text': 'x', 'delay': 'whenever'})
        assert not ok and 'When?' in msg
        msg, ok = tools.execute('discord_remind', {'action': 'add', 'text': 'x', 'delay': '40d'})
        assert not ok and '30 days' in msg
        msg, ok = tools.execute('discord_remind', {'action': 'cancel', 'text': 'mom'})
        assert ok and 'Cancelled 1' in msg and runtime.reminders.pending('alpha') == []
    finally:
        current_event_data.reset(token)


def test_tool_refuses_authorless_turns_and_the_off_switch(monkeypatch):
    runtime = _runtime()
    token = _in_event(monkeypatch, runtime, author_id='')
    try:
        msg, ok = tools.execute('discord_remind', {'action': 'list'})
        assert not ok and 'no one asking' in msg
    finally:
        current_event_data.reset(token)
    runtime = _runtime(enabled=False)
    token = _in_event(monkeypatch, runtime)
    try:
        msg, ok = tools.execute('discord_remind', {'action': 'list'})
        assert not ok and 'off' in msg.lower()
    finally:
        current_event_data.reset(token)


def test_tool_from_the_operator_chat_needs_user_and_channel(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set(None)
    tools._reply_account.set(None)
    msg, ok = tools.execute('discord_remind', {'action': 'list'})
    assert not ok and 'user=' in msg
    msg, ok = tools.execute('discord_remind', {'action': 'add', 'text': 'stretch', 'delay': '1h', 'user': 'u7'})
    assert not ok and 'channel=' in msg
    msg, ok = tools.execute('discord_remind', {'action': 'add', 'text': 'stretch', 'delay': '1h', 'user': 'u7', 'channel': 'c1'})
    assert ok and runtime.reminders.pending('alpha', 'u7')[0]['channel_id'] == 'c1'


def test_pending_cap(monkeypatch):
    runtime = _runtime()
    token = _in_event(monkeypatch, runtime)
    try:
        for i in range(MAX_PENDING):
            assert tools.execute('discord_remind', {'action': 'add', 'text': f'r{i}', 'delay': '1h'})[1]
        msg, ok = tools.execute('discord_remind', {'action': 'add', 'text': 'one more', 'delay': '1h'})
        assert not ok and 'cancel one first' in msg
    finally:
        current_event_data.reset(token)
