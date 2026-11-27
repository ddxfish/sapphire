# tests/test_task_run_context.py - a task's run tells its tools what it is
#
# tool_context carries `task` = {id, type, event, n} for a continuity task's
# run, and nothing for an agent's. A tool that opens to scheduled runs only
# (the browser plugin, for a person's own browser) reads it, so these are
# load-bearing: an event-triggered run must say so, the stamp is made on the
# run's own copy (nothing stored or posted can set it), and it reaches the
# tool context through ExecutionContext.
import contextvars
from unittest.mock import MagicMock, patch

from core.continuity.executor import ContinuityExecutor
from core.continuity.execution_context import ExecutionContext
from core.chat import function_manager as fm_mod


def _runs(task, **kw):
    seen = []
    ex = ContinuityExecutor.__new__(ContinuityExecutor)
    ex._resolve_persona = lambda t: t
    ex._run_background = lambda task, result, *a, **k: seen.append(ex._extract_task_settings(task)) or result
    ex._run_foreground_queued = ex._run_background
    contextvars.copy_context().run(lambda: ex.run(task, **kw))
    return seen[0]['task_run']


def test_a_scheduled_run_and_an_event_run_say_which_they_are():
    plain = _runs({'id': 'abc', 'type': 'task', 'name': 't'})
    assert plain['id'] == 'abc' and plain['type'] == 'task' and plain['event'] is False and plain['n']
    beat = _runs({'id': 'hb', 'heartbeat': True, 'name': 'h'})
    assert beat['type'] == 'heartbeat' and beat['event'] is False
    event = _runs({'id': 'ev', 'type': 'daemon', 'name': 'd'}, event_data='{"text": "hi"}')
    assert event['type'] == 'daemon' and event['event'] is True
    in_chat = _runs({'id': 'fg', 'type': 'task', 'name': 'f', 'chat_target': 'den'})
    assert in_chat['id'] == 'fg' and in_chat['event'] is False


def test_each_run_has_its_own_mark_and_a_stored_one_is_not_believed():
    forged = {'id': 'abc', 'type': 'daemon', 'name': 't',
              '_run': {'id': 'abc', 'type': 'task', 'event': False, 'n': 'x'}}
    first = _runs(forged, event_data='{"text": "hi"}')
    assert first['type'] == 'daemon' and first['event'] is True and first['n'] != 'x'
    assert _runs(forged, event_data='{}')['n'] != first['n']
    assert forged['_run']['n'] == 'x'                       # the run stamps its own copy, never the task


def _tool_context(task_settings):
    fm = MagicMock()

    def build():
        with patch.object(ExecutionContext, '_build_prompt', return_value='sys'), \
             patch.object(ExecutionContext, '_resolve_provider', return_value=('k', MagicMock(), '')), \
             patch.object(ExecutionContext, '_build_gen_params', return_value={}), \
             patch.object(ExecutionContext, '_resolve_tools', return_value=[]):
            ExecutionContext(fm, MagicMock(), task_settings)
        return fm_mod.tool_context.get()
    return contextvars.copy_context().run(build)


def test_the_mark_reaches_the_tools_and_an_agent_has_none():
    run = {'id': 'abc', 'type': 'task', 'event': False, 'n': 'r1'}
    ctx = _tool_context({'prompt': 'default', 'toolset': 'none', 'task_run': run})
    assert ctx['channel'] == 'continuity' and ctx['task'] == run
    agent = _tool_context({'prompt': 'agent', 'toolset': 'all'})        # agents build their own settings
    assert agent['channel'] == 'continuity' and 'task' not in agent
