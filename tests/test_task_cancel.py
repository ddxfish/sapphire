"""Stop a running task — 2026-09-08 (Krem: a daemon ran forever with no off
switch; the toggle only stopped the drain loop BETWEEN runs).

Per-run cancel Event armed by the scheduler, passed through
ContinuityExecutor.run(cancel_event=) into ExecutionContext(cancel_check=).
The context polls it at the top of every tool round and again between the
LLM's reply and its tool batch. Set by cancel_task (⏹ route), by toggle-off
(update_task enabled=False while running), and by delete_task.
"""
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.continuity.scheduler import ContinuityScheduler


# ─── ExecutionContext honors cancel_check ────────────────────────────────────

def _build_ctx(cancel_check, llm_responses):
    from core.continuity.execution_context import ExecutionContext
    fm = MagicMock()
    fm.all_possible_tools = []
    fm._apply_mode_filter = lambda x: x
    te = MagicMock()
    te.call_llm_with_metrics.side_effect = list(llm_responses)
    te.execute_tool_calls.return_value = (1, [])
    te.extract_function_call_from_text.return_value = None
    with patch.object(ExecutionContext, "_build_prompt", return_value="sys"), \
         patch.object(ExecutionContext, "_resolve_provider",
                      return_value=("test", MagicMock(model="m", supports_images=False), "")), \
         patch.object(ExecutionContext, "_build_gen_params", return_value={}), \
         patch.object(ExecutionContext, "_resolve_tools", return_value=None), \
         patch.object(ExecutionContext, "_build_scopes", return_value={}):
        ctx = ExecutionContext(fm, te, {"max_tool_rounds": 20, "context_limit": 0},
                               cancel_check=cancel_check)
    return ctx, te


def _tool_call_response():
    r = MagicMock()
    r.has_tool_calls = True
    r.content = ""
    r.thinking = None
    r.get_tool_calls_as_dicts.return_value = [
        {"id": "tc1", "type": "function", "function": {"name": "noop", "arguments": "{}"}}]
    return r


def test_ctx_stops_between_rounds_and_executes_no_more_tools():
    """An endless tool loop: cancel after round 2 → no round 3 LLM call, no
    3rd tool batch, empty reply, degraded_reason says cancelled."""
    calls = {"n": 0}
    def cancel_check():
        return calls["n"] >= 2
    ctx, te = _build_ctx(cancel_check, [_tool_call_response() for _ in range(20)])
    orig = te.call_llm_with_metrics.side_effect
    def counting(*a, **kw):
        calls["n"] += 1
        return next(orig)
    te.call_llm_with_metrics.side_effect = counting

    with patch("core.continuity.execution_context.config") as cfg:
        cfg.MAX_TOOL_ITERATIONS = 20
        cfg.MAX_PARALLEL_TOOLS = 4
        cfg.CONTEXT_LIMIT = 0
        out = ctx.run("go")

    assert out == ""
    assert ctx.cancelled is True
    assert "Cancelled" in (ctx.degraded_reason or "")
    assert calls["n"] == 2
    assert te.execute_tool_calls.call_count == 1, "the reply that arrived after the stop must not run its tools"
    # History pair stays intact: user + empty assistant
    assert ctx.new_messages[-1] == {"role": "assistant", "content": ""}


def test_ctx_without_cancel_check_runs_normally():
    r = MagicMock(); r.has_tool_calls = False; r.content = "hi"; r.thinking = None
    r.finish_reason = "stop"
    ctx, _ = _build_ctx(None, [r])
    with patch("core.continuity.execution_context.config") as cfg:
        cfg.MAX_TOOL_ITERATIONS = 5
        cfg.MAX_PARALLEL_TOOLS = 4
        cfg.CONTEXT_LIMIT = 0
        out = ctx.run("go")
    assert out == "hi"
    assert ctx.cancelled is False


# ─── scheduler: arm / cancel / toggle-off / delete ───────────────────────────

def _make_scheduler(tmp_path, tasks):
    base_dir = tmp_path / "user" / "continuity"
    base_dir.mkdir(parents=True)
    (base_dir / "tasks.json").write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    sched = ContinuityScheduler.__new__(ContinuityScheduler)
    sched.system = MagicMock()
    sched.executor = MagicMock()
    sched._running = False
    sched._thread = None
    sched._lock = threading.Lock()
    sched._base_dir = base_dir
    sched._tasks_path = base_dir / "tasks.json"
    sched._activity_path = base_dir / "activity.json"
    sched._tasks = {}
    sched._activity = []
    sched._task_running = {}
    sched._task_pending = {}
    sched._task_last_matched = {}
    sched._task_progress = {}
    sched._event_threads = []
    sched._load_tasks()
    sched._load_activity()
    return sched


_DAEMON = {"id": "d1", "name": "Listener", "type": "daemon", "enabled": True,
           "schedule": "0 0 31 2 *", "trigger_config": {"source": "discord_message"},
           "chat_target": ""}


class _BlockingExecutor:
    """Stands in for ContinuityExecutor: blocks until its cancel_event is set
    (or a hard timeout), then returns — the way a real run would exit at its
    next cancel point."""
    def __init__(self):
        self.started = threading.Event()
        self.seen_event = None

    def run(self, task, event_data=None, progress_callback=None,
            response_callback=None, cancel_event=None):
        self.seen_event = cancel_event
        self.started.set()
        cancel_event.wait(timeout=5)
        return {"success": True, "responses": [], "errors": [],
                "cancelled": cancel_event.is_set()}


def test_cancel_task_stops_a_live_event_run_and_drops_the_queue(tmp_path):
    sched = _make_scheduler(tmp_path, [_DAEMON])
    ex = _BlockingExecutor()
    sched.executor = ex
    r = sched.fire_event_task("d1", json.dumps({"text": "hi"}))
    assert r == {"success": True, "queued": False}
    assert ex.started.wait(2)
    # A second event queues behind the live run
    assert sched.fire_event_task("d1", json.dumps({"text": "again"})) == {"success": True, "queued": True}
    assert len(sched._task_pending["d1"]) == 1

    out = sched.cancel_task("d1")
    assert out == {"success": True, "was_running": True}
    assert ex.seen_event.is_set()
    for _ in range(50):
        if not sched._task_running.get("d1"):
            break
        time.sleep(0.05)
    assert sched._task_running.get("d1") is False
    assert sched._task_pending["d1"] == [], "queued events die with the cancelled run"
    statuses = [a["status"] for a in sched._activity if a["task_id"] == "d1"]
    assert "cancel_requested" in statuses and "cancelled" in statuses
    assert sched._tasks["d1"]["enabled"] is True, "⏹ stops the run, not the task"


def test_toggle_off_cancels_the_run_in_flight(tmp_path):
    sched = _make_scheduler(tmp_path, [_DAEMON])
    ex = _BlockingExecutor()
    sched.executor = ex
    sched.fire_event_task("d1", json.dumps({"text": "hi"}))
    assert ex.started.wait(2)
    sched.update_task("d1", {"enabled": False})
    assert ex.seen_event.is_set()
    for _ in range(50):
        if not sched._task_running.get("d1"):
            break
        time.sleep(0.05)
    assert sched._task_running.get("d1") is False


def test_toggle_off_when_idle_arms_nothing(tmp_path):
    sched = _make_scheduler(tmp_path, [_DAEMON])
    sched.update_task("d1", {"enabled": False})
    assert sched._tasks["d1"]["enabled"] is False
    assert sched._cancel_events().get("d1") is None


def test_delete_sets_the_live_runs_event(tmp_path):
    sched = _make_scheduler(tmp_path, [_DAEMON])
    ex = _BlockingExecutor()
    sched.executor = ex
    sched.fire_event_task("d1", json.dumps({"text": "hi"}))
    assert ex.started.wait(2)
    assert sched.delete_task("d1") is True
    assert ex.seen_event.is_set()


def test_cancel_unknown_task(tmp_path):
    sched = _make_scheduler(tmp_path, [])
    assert sched.cancel_task("nope")["success"] is False


def test_list_tasks_reports_cancelling(tmp_path):
    sched = _make_scheduler(tmp_path, [_DAEMON])
    ex = _BlockingExecutor()
    sched.executor = ex
    sched.fire_event_task("d1", json.dumps({"text": "hi"}))
    assert ex.started.wait(2)
    assert sched.list_tasks()[0]["cancelling"] is False
    ex.seen_event.set()          # simulate the request without releasing running yet
    # The flag reads set-while-running as "cancelling" until the thread exits
    # (it may already have exited — either answer is consistent).
    t = sched.list_tasks()[0]
    assert t["cancelling"] == bool(t["running"])


def test_run_now_passes_cancel_event(tmp_path):
    sched = _make_scheduler(tmp_path, [_DAEMON])
    seen = {}
    def run(task, **kw):
        seen.update(kw)
        return {"success": True, "responses": [], "errors": []}
    sched.executor.run.side_effect = run
    sched.run_task_now("d1")
    assert isinstance(seen.get("cancel_event"), threading.Event)


# ─── route ───────────────────────────────────────────────────────────────────

def test_cancel_route(client, mock_system):
    c, csrf = client
    mock_system.continuity_scheduler = MagicMock()
    mock_system.continuity_scheduler.cancel_task.return_value = {"success": True, "was_running": True}
    r = c.post('/api/continuity/tasks/abc/cancel', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    assert r.json()["was_running"] is True
    mock_system.continuity_scheduler.cancel_task.assert_called_once_with('abc')

    mock_system.continuity_scheduler.cancel_task.return_value = {"success": False, "error": "Task not found"}
    r = c.post('/api/continuity/tasks/zzz/cancel', headers={'X-CSRF-Token': csrf})
    assert r.status_code == 404
