"""Regression guards for the agent system (agents v2 shape, 2026-10-06).
  R9  shutdown() ends every live agent - a kind that holds a process is told to stop
  R10 stop() on a session-backed agent interrupts the session AND voids the result
The claude_code kind holds a Claude Code session on the SDK; its stop() must
reach the running loop (interrupt) and the base must void any result so a
dismissed agent never reports partial output.
"""
import asyncio
import importlib
import threading
from unittest.mock import MagicMock

import pytest


class _Engine:
    def install_carrier(self, a): return None
    def release_carrier(self, t): pass
    def finished(self, a): pass
    def report_out(self, a, t): pass
    def event_out(self, a, k): pass
    def question_out(self, a, q): pass


def _kind():
    return importlib.import_module('plugins.claude-code.agent_kind')


def _agent():
    row = {'id': 'r1', 'name': 'Forge', 'kind': 'claude_code', 'chat': 'trinity',
           'mission': 'm', 'options': {'mode': 'project'}, 'privacy': False}
    return _kind().Agent(row, _Engine())


def test_R10_stop_interrupts_the_session_and_voids_the_result():
    a = _agent()
    # a loop and a client as the running thread would have them
    loop = asyncio.new_event_loop()
    interrupted = threading.Event()

    class _Client:
        async def interrupt(self):
            interrupted.set()
    a._loop, a._client = loop, _Client()
    a.result = 'partial output'
    a.status = 'running'
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        a.stop()
        assert interrupted.wait(2), 'interrupt() never reached the loop'
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)
    assert a.cancelled and a.status == 'stopped' and a.result is None
    assert a._say_q.get_nowait() is None                 # the idle wait is woken to end


def test_R9_shutdown_stops_every_live_agent_and_marks_the_rows(agent_world):
    w = agent_world
    ids = [w.mgr.spawn('probe', f'm{i}', chat='desk')['id'] for i in range(2)]
    assert len(w.mgr.check_all()) == 2
    w.mgr.shutdown(timeout=2)
    assert w.mgr.check_all() == []
    assert all(r['status'] == 'stopped' for r in w.store['rows'] if r['id'] in ids)


def test_stop_wakes_a_pending_question_with_no_answer(agent_world):
    w = agent_world
    r = w.mgr.spawn('probe', 'm', chat='desk', options={'ask': {'text': 'go?'}, 'ask_timeout': 30})
    a = w.mgr._agents[r['id']]
    assert w.wait_for(lambda: a.status == 'waiting')
    a.stop()
    assert w.wait_for(lambda: r['id'] not in w.mgr._agents)
    assert a.result is None and a.pending_question is None


def test_say_is_refused_unless_the_session_is_idle():
    a = _agent()
    assert a.say('more')[1] is False                       # no client yet
    a._client = MagicMock()
    a.status = 'running'
    assert a.say('more')[1] is False
    a.status = 'idle'
    text, ok = a.say('more')
    assert ok and a._say_q.get_nowait() == 'more'
