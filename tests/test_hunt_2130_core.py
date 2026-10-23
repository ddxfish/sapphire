"""Discord hunt 2.13.0 — core-side wave 1 pins.

Row numbers refer to tmp/zeebie-discord/hunt-2130-results.md.
"""
import contextvars
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── row 52: emit_daemon_event only reports accepted when the task took it ──
def _emit_with(result, monkeypatch):
    from core.plugin_loader import plugin_loader
    sched = SimpleNamespace(
        find_tasks_by_event=lambda source: [{'id': 't1', 'name': 'T'}],
        fire_event_task=lambda tid, ev, reply_callback=None: result,
    )
    monkeypatch.setattr(plugin_loader, '_scheduler', sched)
    monkeypatch.setattr(plugin_loader, '_get_reply_handler', lambda source: None)
    return plugin_loader.emit_daemon_event('probe_source', '{}')


def test_accept_is_only_success_or_queued(monkeypatch):
    assert _emit_with({'success': True}, monkeypatch) is True
    assert _emit_with({'success': True, 'queued': True}, monkeypatch) is True
    for err in ('Event filtered out', 'Account mismatch', 'Event queue full', 'Task is disabled',
                'Task not found', "Task type 'task' is not event-triggered",
                'Event data not JSON-parseable, filter requires JSON'):
        assert _emit_with({'success': False, 'error': err}, monkeypatch) is False, err


# ── row 83: the py-cord mute lifts at DEBUG ────────────────────────────────
def test_noisy_logger_pin_lifts_at_debug():
    from core import sapphire_logging as sl
    root = logging.getLogger()
    prev = root.level
    try:
        root.setLevel(logging.DEBUG)
        sl._quiet_noisy_loggers()
        assert logging.getLogger('discord').level == logging.NOTSET
        assert logging.getLogger('uvicorn.access').level == logging.WARNING   # HTTP spam stays pinned
        root.setLevel(logging.INFO)
        sl._quiet_noisy_loggers()
        assert logging.getLogger('discord').level == logging.WARNING
    finally:
        root.setLevel(prev)
        sl._quiet_noisy_loggers()


# ── row 81: a refused time_setting is loud, the fallback still applies ─────
def test_sched_task_def_warns_on_bad_hour(monkeypatch, caplog):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda name: {'k': 24})
    with caplog.at_level(logging.WARNING):
        d = plugin_loader._sched_task_def('probe', {'name': 'n', 'cron': '0 9 * * *', 'time_setting': 'k'}, '/tmp')
    assert d['schedule'] == '0 9 * * *'
    assert 'not a valid hour' in caplog.text
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda name: {'k': 13})
    caplog.clear()
    d = plugin_loader._sched_task_def('probe', {'name': 'n', 'cron': '0 9 * * *', 'time_setting': 'k'}, '/tmp')
    assert d['schedule'] == '0 13 * * *'
    assert 'not a valid hour' not in caplog.text


# ── row 85: a daemon can drop its own reply handler ────────────────────────
def test_unregister_reply_handler():
    from core.plugin_loader import plugin_loader
    plugin_loader.register_reply_handler('probe_plugin', lambda *a: None)
    assert 'probe_plugin' in plugin_loader._reply_handlers
    plugin_loader.unregister_reply_handler('probe_plugin')
    assert 'probe_plugin' not in plugin_loader._reply_handlers


# ── row 53: agent workers carry the spawning turn's ContextVars ────────────
_PROBE = contextvars.ContextVar('hunt2130_probe', default=None)


def test_base_worker_thread_inherits_context():
    from core.agents.base_worker import BaseWorker
    seen = []

    class W(BaseWorker):
        def _run_wrapper(self):
            seen.append(_PROBE.get())

    _PROBE.set('from-caller')
    w = W('id', 'probe', 'mission')
    w.start()
    w._thread.join(timeout=5)
    assert seen == ['from-caller']


# ── row 64: the driver finishes the sink even when the stream fails ────────
SR = 16000


def _frame(ms, speech):
    return (b"\x00\x00" * int(SR * ms / 1000), speech)


def _driver(events, transcript='hello sapphire'):
    from core.conversation.driver import ConversationDriver
    system = MagicMock()
    fake_stream = MagicMock()
    fake_stream.cancel_flag = False
    fake_stream.chat_stream.return_value = iter(events)
    system.llm_chat.begin_stream.return_value = (fake_stream, 'sid', 'chat')
    sink = MagicMock()
    d = ConversationDriver(system, transcribe_fn=lambda p: transcript, sink_factory=lambda: sink,
                           endpoint_silence_ms=300, min_speech_ms=100, barge_hold_ms=90)
    d._spawn = lambda target, *a: target(*a)
    return d, sink


def _run_turn(d):
    d.push_frame(*_frame(150, True))
    for _ in range(3):
        d.push_frame(*_frame(100, False))


@patch('core.conversation.driver.publish')
def test_error_event_still_finishes_and_drains_the_sink(pub):
    from core.conversation.engine import IDLE
    from core.event_bus import Events
    d, sink = _driver([{'type': 'content', 'text': 'Hi'}, {'type': 'error', 'text': 'boom'}])
    _run_turn(d)
    sink.finish.assert_called_once()
    sink.wait.assert_called_once()
    ends = [c for c in pub.call_args_list if c.args and c.args[0] == Events.VOICE_TURN_END]
    assert len(ends) == 1
    assert d.engine.state == IDLE


@patch('core.conversation.driver.publish')
def test_early_return_never_finishes_an_unstarted_sink(pub):
    d, sink = _driver([{'type': 'done'}], transcript='')
    _run_turn(d)
    sink.start.assert_not_called()
    sink.finish.assert_not_called()
