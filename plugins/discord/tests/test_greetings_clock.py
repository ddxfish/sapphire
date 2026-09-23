"""The greetings clock (S1, 2026-09-22): times on the daemon task, one post per
channel per kind per day, a five-minute window, a durable latch, no canned text."""
from datetime import datetime
from types import SimpleNamespace

from plugins.discord.conversation.ignored_channels import parse_target
from plugins.discord.greetings import GreetingsClock, INSTRUCTIONS, WINDOW_SECONDS, parse_channels, parse_hhmm


class FakeLoader:
    def __init__(self, tasks):
        self.tasks = tasks
        self.fired = []
        self.refuse = False

    def tasks_for_source(self, source):
        return [t for t in self.tasks if t['trigger_config']['source'] == source]

    def fire_task(self, task_id, payload, *, plugin=None):
        self.fired.append((task_id, payload, plugin))
        return {'success': not self.refuse, 'error': 'refused' if self.refuse else None}


class FakeState:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def save(self, key, value):
        self.d[key] = value


def _task(tid, source, account='alpha', channels='111, 222', greeting='08:00', goodnight='22:30', name='Greet'):
    return {'id': tid, 'name': name, 'enabled': True, 'type': 'daemon',
            'trigger_config': {'source': source, 'account': account, 'channels': channels,
                               'greeting_time': greeting, 'goodnight_time': goodnight}}


def _clock(tasks, connected=('alpha',), state=None):
    loader = FakeLoader(tasks)
    transport = SimpleNamespace(list_connected=lambda: list(connected))
    return loader, GreetingsClock(plugin_loader=loader, transport=transport, state=state or FakeState())


def test_parsers():
    assert parse_hhmm('08:05') == (8, 5) and parse_hhmm(' 23:59 ') == (23, 59)
    assert parse_hhmm('24:00') is None and parse_hhmm('') is None and parse_hhmm('9') is None
    assert parse_channels('111, 222\n333 alpha:444') == ['111', '222', '333', '444']
    assert parse_channels(['111', '111']) == ['111'] and parse_channels(None) == []
    assert parse_target('remmi:123:456') == ('remmi', '456')       # moved in from proactive/targets.py


def test_greeting_fires_once_per_channel_inside_the_window():
    loader, clock = _clock([_task('t1', 'discord_greetings')])
    at = datetime(2026, 9, 22, 8, 1, 0)

    fired = clock.tick(at)

    assert [(f['kind'], f['channel_id']) for f in fired] == [('greeting', '111'), ('greeting', '222')]
    task_id, payload, plugin = loader.fired[0]
    assert task_id == 't1' and plugin == 'discord'
    assert payload['proactive_kind'] == 'greeting' and payload['account'] == 'alpha'
    assert payload['channel_id'] == '111' and payload['content'] == INSTRUCTIONS['greeting']
    assert payload['message_id'].startswith('proactive-greeting-111-')
    # the same tick again, and a later tick inside the window: latched
    assert clock.tick(at) == []
    assert clock.tick(datetime(2026, 9, 22, 8, 4, 59)) == []
    assert len(loader.fired) == 2


def test_late_boot_never_greets_and_tomorrow_is_new():
    loader, clock = _clock([_task('t1', 'discord_greetings')])
    assert clock.tick(datetime(2026, 9, 22, 13, 0)) == []              # 1pm: the old bug class
    assert clock.tick(datetime(2026, 9, 22, 7, 59, 59)) == []          # not yet
    assert len(clock.tick(datetime(2026, 9, 22, 8, 0, 0))) == 2
    assert len(clock.tick(datetime(2026, 9, 23, 8, 0, 30))) == 2       # next day fires again
    assert len(loader.fired) == 4


def test_goodnight_and_blank_times():
    loader, clock = _clock([_task('t1', 'discord_all', channels='111', greeting='', goodnight='22:30')])
    assert clock.tick(datetime(2026, 9, 22, 8, 0)) == []
    fired = clock.tick(datetime(2026, 9, 22, 22, 31))
    assert [f['kind'] for f in fired] == ['goodnight']
    assert loader.fired[0][1]['content'] == INSTRUCTIONS['goodnight']


def test_disconnected_account_and_missing_channels_are_skipped():
    loader, clock = _clock([_task('t1', 'discord_greetings', account='beta'),
                            _task('t2', 'discord_greetings', channels='')], connected=('alpha',))
    assert clock.tick(datetime(2026, 9, 22, 8, 0)) == []
    assert loader.fired == []


def test_refused_fire_is_not_latched_so_it_retries_inside_the_window():
    loader, clock = _clock([_task('t1', 'discord_greetings', channels='111')])
    loader.refuse = True
    assert clock.tick(datetime(2026, 9, 22, 8, 0)) == []
    loader.refuse = False
    assert len(clock.tick(datetime(2026, 9, 22, 8, 1))) == 1


def test_latch_is_durable_across_a_restart():
    state = FakeState()
    _, clock = _clock([_task('t1', 'discord_greetings', channels='111')], state=state)
    assert len(clock.tick(datetime(2026, 9, 22, 8, 0))) == 1
    _, reborn = _clock([_task('t1', 'discord_greetings', channels='111')], state=state)
    assert reborn.tick(datetime(2026, 9, 22, 8, 2)) == []
    assert state.d['greetings_fired'] == {'t1:greeting:111': '2026-09-22'}


def test_chat_only_source_never_fires_greetings():
    loader, clock = _clock([_task('t1', 'discord_message')])
    assert clock.tick(datetime(2026, 9, 22, 8, 0)) == []


def test_loader_without_the_doors_is_a_quiet_no_op():
    clock = GreetingsClock(plugin_loader=SimpleNamespace())
    assert clock.tick(datetime(2026, 9, 22, 8, 0)) == []


def test_recent_history_labels_her_own_lines_you():
    rows = [{'message_id': '1', 'author': 'alice', 'content': 'hi', 'created_at': 1.0},
            {'message_id': '2', 'author': 'sapph', 'content': 'hello!', 'created_at': 2.0}]
    transport = SimpleNamespace(list_connected=lambda: ['alpha'],
                                account_health=lambda name: {'bot_id': '9', 'bot_name': 'sapph'},
                                recent_messages=lambda a, c, limit=20: rows,
                                describe_channel=lambda a, c: {'guild_id': 'g1', 'guild_name': 'G', 'channel_id': c, 'channel_name': 'lounge'})
    clock = GreetingsClock(plugin_loader=SimpleNamespace(), transport=transport)
    payload = clock.build_payload('alpha', '111', 'greeting')
    lines = payload['recent_history']
    assert len(lines) == 2 and lines[1].startswith('You:') and not lines[0].startswith('You:')
    assert payload['guild_name'] == 'G' and payload['channel_name'] == 'lounge'


def test_now_user_follows_config_timezone(monkeypatch):
    """hunt 2.13.0 row 74: one clock — Sapphire's configured timezone, else OS-local."""
    import config
    from datetime import datetime
    from plugins.discord.greetings import now_user
    monkeypatch.setattr(config, 'USER_TIMEZONE', 'UTC', raising=False)
    assert abs((now_user() - datetime.utcnow()).total_seconds()) < 5
    monkeypatch.setattr(config, 'USER_TIMEZONE', '', raising=False)
    assert abs((now_user() - datetime.now()).total_seconds()) < 5
