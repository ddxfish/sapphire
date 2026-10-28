"""The Idle Heartbeat (tmp/idle-heartbeat-plan.md): a heartbeat that waits
for the person's quiet, app-wide, with an optional 1x/2x/4x backoff."""
import threading
import time
from pathlib import Path

import pytest

from core import idle
from core.continuity import scheduler as sched
from core.continuity.scheduler import ContinuityScheduler, idle_state, is_idle, _idle_fields

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def quiet(monkeypatch):
    """Set how long the person has been quiet (minutes; None = never spoke)."""
    def set_quiet(mins):
        since = None if mins is None else time.time() - mins * 60
        monkeypatch.setattr(idle, 'since', lambda chat=None, person=False: since)
        return since
    return set_quiet


def beat(**kw):
    return {'id': 'b', 'name': 'b', 'type': 'heartbeat', 'enabled': True, 'idle_minutes': 60,
            'idle_backoff': True, **kw}


def test_only_a_heartbeat_with_minutes_is_idle():
    assert is_idle(beat()) and not is_idle(beat(idle_minutes=0)) and not is_idle(beat(type='task'))
    assert _idle_fields({'idle_minutes': '60'}, 'heartbeat') == (60, True)
    assert _idle_fields({'idle_minutes': 60}, 'task') == (0, True)
    with pytest.raises(ValueError, match='between 5 minutes'):
        _idle_fields({'idle_minutes': 2}, 'heartbeat')


def test_it_waits_for_the_quiet_then_backs_off(quiet):
    since = quiet(30)
    assert not idle_state(beat())[0]
    quiet(61)
    due, stretch, fires, q = idle_state(beat())
    assert due and fires == 0 and 60 < q < 62
    stamped = beat(idle_stretch=stretch, idle_fires=1)          # it fired once this quiet
    assert not idle_state(stamped)[0]                           # next at 2x = 120 min
    assert idle_state(stamped, now=time.time() + 60 * 60)[0]
    spent = beat(idle_stretch=stretch, idle_fires=3)
    assert not idle_state(spent, now=time.time() + 10 ** 6)[0]   # backoff spent: waits for the person


def test_the_person_speaking_rearms_it(quiet):
    old = int(quiet(500))
    spent = beat(idle_stretch=old, idle_fires=3)
    quiet(61)                                                   # a new quiet stretch since they spoke
    assert idle_state(spent)[0]


def test_without_backoff_it_beats_on_schedule_while_quiet(quiet):
    since = quiet(200)
    b = beat(idle_backoff=False, idle_stretch=int(since), idle_fires=9)
    assert idle_state(b)[0]


def test_never_spoke_never_fires(quiet):
    quiet(None)
    assert idle_state(beat()) == (False, None, 0, None)


def scheduler_with(tasks, monkeypatch):
    s = ContinuityScheduler.__new__(ContinuityScheduler)
    s._lock, s._tasks = threading.RLock(), {t['id']: t for t in tasks}
    s._task_running, s._task_pending, s._task_last_matched, s._task_progress = {}, {}, {}, {}
    fired, saved = [], []
    monkeypatch.setattr(s, '_save_tasks', lambda: saved.append(1), raising=False)
    monkeypatch.setattr(s, '_arm_cancel', lambda tid: None, raising=False)
    monkeypatch.setattr(s, '_cron_matches_now', lambda sched_, at: True, raising=False)
    monkeypatch.setattr(s, '_in_active_hours', lambda t, h=None: True, raising=False)
    monkeypatch.setattr(s, '_execute_task', lambda t: fired.append(t['id']), raising=False)
    monkeypatch.setattr(s, '_log_activity', lambda *a, **k: None, raising=False)

    class T:
        def __init__(self, target, args, **kw):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(sched.threading, 'Thread', T)
    return s, fired, saved


def test_the_loop_fires_it_once_and_counts_it(quiet, monkeypatch):
    since = quiet(61)
    s, fired, saved = scheduler_with([beat(schedule='*/5 * * * *')], monkeypatch)
    s._check_and_run()
    assert fired == ['b'] and s._tasks['b']['idle_fires'] == 1 and s._tasks['b']['idle_stretch'] == int(since)
    s._task_last_matched.clear()
    s._task_running.clear()
    s._check_and_run()
    assert fired == ['b']                                       # 1x spent; 2x not yet


def test_a_busy_idle_beat_never_queues(quiet, monkeypatch):
    quiet(61)
    s, fired, _ = scheduler_with([beat(schedule='*/5 * * * *')], monkeypatch)
    s._task_running['b'] = True
    s._check_and_run()
    assert fired == [] and not s._task_pending.get('b')


def test_quiet_rides_the_card_and_the_message(quiet, monkeypatch):
    quiet(65)
    s, _, _ = scheduler_with([beat(schedule='*/5 * * * *')], monkeypatch)
    monkeypatch.setattr(s, '_cancel_events', lambda: {}, raising=False)
    t = s.list_tasks()[0]
    assert 64 < t['quiet_min'] < 66 and t['idle_fires_now'] == 0 and t['idle_next_min'] == 60
    from core.continuity.executor import ContinuityExecutor
    msg = ContinuityExecutor._message({'initial_message': "They've been quiet for {quiet}. Check in?"})
    assert msg == "They've been quiet for 1 h 5 min. Check in?"
    quiet(None)
    assert ContinuityExecutor._message({'initial_message': 'quiet {quiet}'}) == 'quiet a while'


def test_its_runtime_state_is_never_planted():
    assert {'idle_stretch', 'idle_fires'} <= set(ContinuityScheduler.INTERNAL_FIELDS)


def test_the_heartbeat_tab_has_an_idle_column_and_editor():
    js = (ROOT / 'interfaces' / 'web' / 'static' / 'views' / 'heartbeat.js').read_text()
    assert 'sched-layout trigger-2col' in js and 'id="hb-new-idle"' in js
    assert "TR.openEditor(null, 'heartbeat', refresh, { idle: true })" in js
    assert 'heartbeats.filter(TR.isIdle)' in js and 'heartbeats.filter(h => !TR.isIdle(h))' in js
    cron = (ROOT / 'interfaces' / 'web' / 'static' / 'shared' / 'trigger-editor' / 'trigger-cron.js').read_text()
    assert 'id="ed-idle-minutes"' in cron and 'id="ed-idle-backoff"' in cron and 'idle_backoff: !!' in cron
    common = (ROOT / 'interfaces' / 'web' / 'static' / 'shared' / 'trigger-common.js').read_text()
    assert 'idle: !!opts.idle' in common and 'beats after ${quietWords(hb.idle_next_min)} quiet' in common
    ed = (ROOT / 'interfaces' / 'web' / 'static' / 'shared' / 'trigger-editor' / 'editor.js').read_text()
    assert "const IDLE_CONFIG = { label: 'Idle Heartbeat'" in ed and '{quiet}' in ed


def test_the_card_says_when_it_next_looks(quiet):
    """Krem, 2026-10-27: 2 h 41 min quiet, a 60-min threshold, and no beat -
    its looks were hourly (*/60 = on the hour) with a 50% chance. The card
    and timeline say when the look that will find it due comes."""
    from datetime import datetime
    from core.continuity.scheduler import idle_next_look, _user_now
    since = quiet(161)
    now = _user_now()
    look = idle_next_look(beat(schedule='*/60 * * * *'), since, now)
    assert look.minute == 0 and 0 < (look - now).total_seconds() <= 3600
    soon = idle_next_look(beat(schedule='*/5 * * * *'), since, now)
    assert soon.minute % 5 == 0 and (soon - now).total_seconds() <= 300
    later = quiet(10)
    due_look = idle_next_look(beat(schedule='*/5 * * * *'), later, now)
    assert (due_look - now).total_seconds() >= 49 * 60            # due at 60 min quiet, not before
    common = (ROOT / 'interfaces' / 'web' / 'static' / 'shared' / 'trigger-common.js').read_text()
    assert 'next look ${lookIn(hb.idle_next_look)}' in common
