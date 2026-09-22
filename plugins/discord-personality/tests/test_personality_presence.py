"""Presence cycling module (S1): rotate on a timer, night status in the window."""
import time
from datetime import datetime

from discord_personality.modules import presence


class FakeApi:
    def __init__(self):
        self.calls = []

    def set_presence(self, account, status='online', activity=''):
        self.calls.append((account, status, activity))
        return {'status': 'updated'}


CFG = {'presence.statuses': 'listening: chat\nwatching: the server\n', 'presence.cycle_minutes': 30,
       'presence.status': 'online', 'presence.night_start': '22:00', 'presence.night_end': '07:00',
       'presence.night_status': 'idle', 'presence.night_activity': 'custom: sleeping'}


def setup_function(_):
    presence._last.clear()


def test_first_tick_sets_then_holds_until_the_cycle_elapses():
    api = FakeApi()
    day = datetime(2026, 9, 22, 12, 0)
    assert presence.apply('alpha', api=api, cfg=CFG, now=day)['activity'] == 'listening: chat'
    assert presence.apply('alpha', api=api, cfg=CFG, now=day) is None
    presence._last['alpha']['at'] = time.time() - 31 * 60
    assert presence.apply('alpha', api=api, cfg=CFG, now=day)['activity'] == 'watching: the server'
    presence._last['alpha']['at'] = time.time() - 31 * 60
    assert presence.apply('alpha', api=api, cfg=CFG, now=day)['activity'] == 'listening: chat'      # wraps
    assert [c[2] for c in api.calls] == ['listening: chat', 'watching: the server', 'listening: chat']


def test_night_window_sets_the_night_status_once_and_day_resumes():
    api = FakeApi()
    night = datetime(2026, 9, 22, 23, 0)
    assert presence.apply('alpha', api=api, cfg=CFG, now=night) == {'status': 'idle', 'activity': 'custom: sleeping', 'night': True, 'index': -1}
    assert presence.apply('alpha', api=api, cfg=CFG, now=night) is None
    morning = datetime(2026, 9, 23, 8, 0)
    assert presence.apply('alpha', api=api, cfg=CFG, now=morning)['activity'] == 'listening: chat'   # immediately, no cycle wait
    assert api.calls[0][1] == 'idle' and api.calls[1][1] == 'online'


def test_defaults_when_nothing_is_configured():
    api = FakeApi()
    assert presence.apply('alpha', api=api, cfg={}, now=datetime(2026, 9, 22, 12, 0))['activity'] == presence.DEFAULT_STATUSES[0]
    assert presence.statuses({'presence.statuses': ['a', ' ', 'b']}) == ['a', 'b']
    assert presence.apply('', api=api, cfg={}) is None
