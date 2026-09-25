"""Presence (2026-09-25): awake/away is the Chat task's Active hours; a random
status line while awake, the away line while not; off = hands off."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

from plugins.discord.models.settings import PresenceSettings
from plugins.discord.presence import DEFAULT_STATUSES, PresenceClock, status_lines


class FakeLoader:
    def __init__(self, tasks):
        self.tasks = tasks

    def tasks_for_source(self, source):
        return [t for t in self.tasks if t['trigger_config']['source'] == source]


class FakeTransport:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def change_presence_async(self, account, *, status='online', activity=''):
        if self.fail:
            raise RuntimeError('no client')
        self.calls.append((account, status, activity))
        return {'status': 'ok'}


def _task(account='alpha', start=None, end=None, source='discord_message', enabled=True):
    return {'id': f'{account}-{source}', 'enabled': enabled, 'type': 'daemon',
            'trigger_config': {'source': source, 'account': account},
            'active_hours_start': start, 'active_hours_end': end}


def _clock(tasks=(), cfg=None, transport=None):
    cfg = cfg or PresenceSettings(enabled=True)
    store = SimpleNamespace(resolve=lambda: SimpleNamespace(presence=cfg))
    return PresenceClock(plugin_loader=FakeLoader(list(tasks)), transport=transport or FakeTransport(), settings_store=store)


def _at(hour):
    return datetime(2026, 9, 25, hour, 30)


def test_status_lines_parse_text_or_list():
    assert status_lines('a\n\n b \n') == ['a', 'b']
    assert status_lines(['x', ' ', 'y']) == ['x', 'y']
    assert status_lines('') == []


def test_awake_follows_the_chat_tasks_window():
    assert _clock([]).awake('alpha', _at(3))                                   # no Chat task = always awake
    assert _clock([_task()]).awake('alpha', _at(3))                            # no window = always awake
    day = _clock([_task(start=8, end=22)])
    assert day.awake('alpha', _at(12)) and not day.awake('alpha', _at(23)) and not day.awake('alpha', _at(22))
    night = _clock([_task(start=20, end=4)])
    assert night.awake('alpha', _at(2)) and not night.awake('alpha', _at(12))
    two = _clock([_task(start=8, end=12), _task(start=18, end=22)])
    assert two.awake('alpha', _at(19)) and not two.awake('alpha', _at(15))    # any task inside = awake
    other = _clock([_task(account='beta', start=8, end=9)])
    assert other.awake('alpha', _at(15))                                       # another bot's task is not mine
    voice = _clock([_task(start=8, end=9, source='discord_voice')])
    assert voice.awake('alpha', _at(15))                                       # only Chat tasks count


def test_off_never_touches_presence_and_clears_once():
    cfg = PresenceSettings(enabled=False)
    c = _clock([], cfg)
    assert asyncio.run(c.tick_async('alpha', _at(12))) is None and c.transport.calls == []
    c2 = _clock([], PresenceSettings(enabled=True))
    asyncio.run(c2.tick_async('alpha', _at(12)))
    c2.settings_store = SimpleNamespace(resolve=lambda: SimpleNamespace(presence=cfg))
    assert asyncio.run(c2.tick_async('alpha', _at(12))) == {'status': 'online', 'activity': '', 'clear': True}
    assert c2.transport.calls[-1] == ('alpha', 'online', '') and 'alpha' not in c2._last
    assert asyncio.run(c2.tick_async('alpha', _at(12))) is None              # cleared once, then hands off


def test_awake_sets_a_pool_line_and_holds_it_for_the_cycle():
    c = _clock([_task(start=8, end=22)])
    got = asyncio.run(c.tick_async('alpha', _at(12)))
    assert got['status'] == 'online' and got['activity'] in DEFAULT_STATUSES
    assert asyncio.run(c.tick_async('alpha', _at(12))) is None              # inside the cycle: nothing
    c._last['alpha']['at'] -= 31 * 60
    again = asyncio.run(c.tick_async('alpha', _at(12)))
    assert again['activity'] in DEFAULT_STATUSES and again['activity'] != got['activity']
    assert len(c.transport.calls) == 2


def test_away_sets_idle_plus_the_away_line_once_and_wakes_immediately():
    c = _clock([_task(start=8, end=22)], PresenceSettings(enabled=True, away_line='sleeping'))
    asyncio.run(c.tick_async('alpha', _at(12)))
    assert asyncio.run(c.tick_async('alpha', _at(23))) == {'status': 'idle', 'activity': 'sleeping', 'away': True, 'index': -1}
    assert asyncio.run(c.tick_async('alpha', _at(23))) is None              # already away
    woke = asyncio.run(c.tick_async('alpha', _at(8)))                        # no cycle wait after the night
    assert woke['status'] == 'online' and woke['activity'] in DEFAULT_STATUSES
    assert [s for _, s, _ in c.transport.calls] == ['online', 'idle', 'online']


def test_blank_lines_mean_online_with_no_activity():
    c = _clock([], PresenceSettings(enabled=True, statuses=''))
    assert asyncio.run(c.tick_async('alpha', _at(12))) == {'status': 'online', 'activity': '', 'away': False, 'index': -1}
    assert asyncio.run(c.tick_async('alpha', _at(12))) is None


def test_transport_failure_leaves_no_state_and_forget_reapplies():
    c = _clock([], transport=FakeTransport(fail=True))
    assert asyncio.run(c.tick_async('alpha', _at(12))) is None and 'alpha' not in c._last
    c = _clock([])
    asyncio.run(c.tick_async('alpha', _at(12)))
    c.forget('alpha')
    assert asyncio.run(c.tick_async('alpha', _at(12))) is not None          # reconnect → set again
