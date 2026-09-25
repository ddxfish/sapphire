"""Active hours on event tasks (2026-09-25).

Every task already stored active_hours_start/end and the cron loop honoured
them; the daemon editor wrote null and fire_event_task never looked. Now an
event task answers only inside its window — the Discord bot shows "away"
outside it — while the owner's own clock (skip_filter: greetings) is not a
message and is never gated.
"""
import json
import threading
from unittest.mock import MagicMock, patch

from core.continuity.scheduler import ContinuityScheduler, task_in_active_hours


def _sched(tmp_path, tasks):
    base_dir = tmp_path / "user" / "continuity"
    base_dir.mkdir(parents=True)
    (base_dir / "tasks.json").write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    executor = MagicMock()
    executor.run.return_value = {"success": True, "responses": [{"output": "ok"}], "errors": []}
    s = ContinuityScheduler.__new__(ContinuityScheduler)
    s.system, s.executor = MagicMock(), executor
    s._running, s._thread, s._lock = False, None, threading.Lock()
    s._base_dir, s._tasks_path, s._activity_path = base_dir, base_dir / "tasks.json", base_dir / "activity.json"
    s._tasks, s._activity = {}, []
    s._task_running, s._task_pending, s._task_last_matched, s._task_progress = {}, {}, {}, {}
    s._event_threads = []
    s._load_tasks()
    s._load_activity()
    return s


def _task(start, end):
    return {"id": "t1", "name": "Discord Chat", "type": "daemon", "enabled": True,
            "trigger_config": {"source": "discord_message", "account": "sapph"},
            "active_hours_start": start, "active_hours_end": end}


def test_window_helper_end_exclusive_and_overnight():
    assert task_in_active_hours({}, 3)
    assert task_in_active_hours(_task(8, 22), 8) and task_in_active_hours(_task(8, 22), 21)
    assert not task_in_active_hours(_task(8, 22), 22) and not task_in_active_hours(_task(8, 22), 3)
    assert task_in_active_hours(_task(20, 4), 23) and task_in_active_hours(_task(20, 4), 2)
    assert not task_in_active_hours(_task(20, 4), 4) and not task_in_active_hours(_task(20, 4), 12)


def test_event_outside_the_window_is_refused_inside_it_fires(tmp_path):
    s = _sched(tmp_path, [_task(8, 22)])
    with patch("core.continuity.scheduler._user_now") as now:
        now.return_value.hour = 23
        out = s.fire_event_task("t1", '{"account": "sapph", "content": "hi"}')
        assert out == {"success": False, "error": "Outside active hours"}
        now.return_value.hour = 12
        out = s.fire_event_task("t1", '{"account": "sapph", "content": "hi"}')
        assert out.get("success") is True
    for t in s._event_threads:
        t.join(timeout=5)


def test_the_owners_clock_is_not_gated(tmp_path):
    s = _sched(tmp_path, [_task(8, 22)])
    with patch("core.continuity.scheduler._user_now") as now:
        now.return_value.hour = 23
        out = s.fire_event_task("t1", '{"account": "sapph"}', skip_filter=True)
        assert out.get("success") is True
    for t in s._event_threads:
        t.join(timeout=5)


def test_no_window_means_always(tmp_path):
    s = _sched(tmp_path, [_task(None, None)])
    with patch("core.continuity.scheduler._user_now") as now:
        now.return_value.hour = 3
        assert s.fire_event_task("t1", '{"account": "sapph"}').get("success") is True
    for t in s._event_threads:
        t.join(timeout=5)
