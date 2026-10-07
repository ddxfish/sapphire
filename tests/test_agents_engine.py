# tests/test_agents_engine.py - agents v2: registry, engine, base Agent, the four tools
# (tmp/agents-v2.md). The fixture `agent_world` (conftest) fakes the plugin
# module, the store, the session manager, the inbox and the event bus.
import json
import time

import pytest

from core.agents import registry
from core.agents.base import _map_answers, _pick
from core.agents.engine import AgentError


# --- registry ---------------------------------------------------------------------------

def test_registry_accepts_a_kind_and_defaults_cloud_to_true():
    registry._kinds.clear()
    assert registry.register_kind('k1', {'module': 'kind.py', 'label': 'K'}, 'p')
    spec = registry.get_kind('k1')
    assert spec['cloud'] is True and spec['conversational'] is False and spec['plugin_name'] == 'p'
    assert registry.register_kind('k2', {'module': 'kind.py', 'cloud': False}, 'p')
    assert registry.get_kind('k2')['cloud'] is False
    registry._kinds.clear()


def test_registry_refuses_bad_ids_modules_schemas_and_shadowing():
    registry._kinds.clear()
    assert not registry.register_kind('Bad Id', {'module': 'k.py'}, 'p')
    assert not registry.register_kind('k', {'module': '../k.py'}, 'p')
    assert not registry.register_kind('k', {'module': 'k.txt'}, 'p')
    assert not registry.register_kind('k', {'module': 'k.py', 'spawn_schema': [{'key': 'mission'}]}, 'p')  # common name
    assert not registry.register_kind('k', {'module': 'k.py', 'spawn_schema': 'nope'}, 'p')
    assert registry.register_kind('k', {'module': 'k.py'}, 'p')
    assert not registry.register_kind('k', {'module': 'k.py'}, 'q')          # another plugin may not take it
    assert registry.unregister_plugin('p') == ['k'] and not registry.has('k')
    registry._kinds.clear()


# --- spawn, report, rows ------------------------------------------------------------------

def test_spawn_runs_the_kind_and_the_report_rides_the_inbox(agent_world):
    w = agent_world
    r = w.mgr.spawn('probe', 'count the stars', chat='desk')
    assert 'error' not in r and r['name'] == 'Spark'
    a = w.mgr._agents[r['id']]
    assert a.status == 'running' and a.chat == 'desk' and a.privacy is False
    a.finish_with_result('about a trillion')
    assert w.wait_for(lambda: r['id'] not in w.mgr._agents)
    tell = [t for t in w.tells if t['source'] == 'agent:probe'][-1]
    assert tell['chat'] == 'desk' and tell['text'] == 'about a trillion' and tell['coalesce'] is False   # one return, one reply
    assert tell['header'].startswith('[Agent Spark (probe) — done in') and 'not typed by the user' in tell['header']
    row = [x for x in w.store['rows'] if x['id'] == r['id']][0]
    assert row['status'] == 'done' and 'mission' not in row and 'options' not in row     # metadata only on disk
    assert w.content[('desk', f"agent:{r['id']}")]['mission'] == 'count the stars'      # content under the chat
    assert w.content[('desk', f"agent:{r['id']}")]['last_report'] == 'about a trillion'
    kinds = [e[0] for e in w.events]
    assert 'agent_spawned' in kinds and 'agent_completed' in kinds
    spawned = [e for e in w.events if e[0] == 'agent_spawned'][0][1]
    assert 'mission' not in spawned                                                        # ids and names only


def test_spawn_refuses_unknown_kind_empty_mission_and_the_concurrency_cap(agent_world):
    w = agent_world
    assert 'no agent kind' in w.mgr.spawn('nope', 'm', chat='desk')['error']
    assert 'mission' in w.mgr.spawn('probe', '   ', chat='desk')['error']
    ids = [w.mgr.spawn('probe', f'm{i}', chat='desk')['id'] for i in range(3)]
    assert 'limit' in w.mgr.spawn('probe', 'one more', chat='desk')['error']
    for i in ids:
        w.mgr._agents[i].finish()
    assert w.wait_for(lambda: not w.mgr._agents)


def test_live_names_are_unique_and_a_failed_run_is_failed(agent_world):
    w = agent_world
    a = w.mgr.spawn('probe', 'one', chat='desk')
    b = w.mgr.spawn('probe', 'two', chat='desk')
    c = w.mgr.spawn('probe', 'three', chat='desk')
    assert {a['name'], b['name'], c['name']} == {'Spark', 'Alpha', 'Spark-2'}
    w.mgr._agents[a['id']].finish_with_error(RuntimeError('boom'))
    assert w.wait_for(lambda: a['id'] not in w.mgr._agents)
    row = [x for x in w.store['rows'] if x['id'] == a['id']][0]
    assert row['status'] == 'failed'
    done = [e for e in w.events if e[0] == 'agent_completed' and e[1]['id'] == a['id']][0][1]
    assert done['status'] == 'failed' and 'boom' in done['error'] and 'result' not in done
    for i in (b['id'], c['id']):
        w.mgr._agents[i].finish()


# --- ask / answer -----------------------------------------------------------------------------

def test_a_question_rides_the_inbox_and_the_answer_reaches_the_agent(agent_world):
    w = agent_world
    q = {'questions': [{'question': 'Delete or rewrite?', 'header': 'Fix',
                        'options': [{'label': 'delete', 'description': 'drop it'}, {'label': 'rewrite', 'description': 'redo'}],
                        'multiSelect': False}]}
    r = w.mgr.spawn('probe', 'fix the test', chat='desk', options={'ask': q})
    a = w.mgr._agents[r['id']]
    assert w.wait_for(lambda: a.status == 'waiting')
    tell = [t for t in w.tells if 'asks' in t['header']][-1]
    assert '(a) delete' in tell['text'] and '(b) rewrite' in tell['text'] and "agent_action('Spark', 'answer'" in tell['text']
    assert a.pending_question['questions'][0]['question'] == 'Delete or rewrite?'
    assert any(e[0] == 'agent_waiting' and e[2] for e in w.events)              # ephemeral, ids only
    text, ok = w.mgr.action_text('desk', 'spark', 'answer', 'b')                 # case-folded name, a letter
    assert ok and 'Answered' in text
    assert w.wait_for(lambda: any(e['kind'] == 'note' and "answer={'Delete or rewrite?': 'rewrite'}" in str(e['data'])
                                  for e in a.transcript(0)))
    assert a.status == 'running' and a.pending_question is None
    assert w.mgr.action_text('desk', 'Spark', 'answer', 'again')[1] is False     # nothing pending now
    a.finish()


def test_an_unanswered_question_times_out_to_none(agent_world):
    w = agent_world
    r = w.mgr.spawn('probe', 'm', chat='desk', options={'ask': {'text': 'yes or no?'}, 'ask_timeout': 0.2})
    a = w.mgr._agents[r['id']]
    assert w.wait_for(lambda: any(e['kind'] == 'note' and 'answer=None' in str(e['data']) for e in a.transcript(0)))
    a.finish()


def test_stop_wakes_a_blocked_ask_and_voids_the_result(agent_world):
    w = agent_world
    r = w.mgr.spawn('probe', 'm', chat='desk', options={'ask': {'text': 'pick'}, 'ask_timeout': 30})
    a = w.mgr._agents[r['id']]
    assert w.wait_for(lambda: a.status == 'waiting')
    text, ok = w.mgr.action_text('desk', 'Spark', 'stop', '')
    assert ok and 'stopped' in text
    a._queued_result = 'late'
    a.finish()
    assert w.wait_for(lambda: r['id'] not in w.mgr._agents)
    assert a.result is None and a.status == 'stopped'


def test_map_answers_letters_labels_lists_and_free_text():
    q = {'question': 'Which?', 'options': [{'label': 'Alpha'}, {'label': 'Beta'}], 'multiSelect': False}
    assert _pick(q, 'b') == 'Beta' and _pick(q, 'alpha') == 'Alpha' and _pick(q, 'B)') == 'Beta'
    assert _pick(q, 'neither, do both') == 'neither, do both'
    multi = dict(q, multiSelect=True)
    assert _pick(multi, 'a, b') == ['Alpha', 'Beta'] and _pick(multi, 'alpha and beta') == ['Alpha', 'Beta']
    qs = [q, {'question': 'Branch?', 'options': [{'label': 'dev'}, {'label': 'main'}], 'multiSelect': False}]
    assert _map_answers(qs, 'a') == {'Which?': 'Alpha', 'Branch?': 'dev'}
    assert _map_answers(qs, ['b', 'main']) == {'Which?': 'Beta', 'Branch?': 'main'}
    assert _map_answers(qs, {'Branch?': 'b'}) == {'Which?': '', 'Branch?': 'main'}


# --- privacy by SPEC, re-gated on every action ----------------------------------------------

def test_cloud_kinds_are_refused_from_a_private_chat_and_flagless_counts_as_cloud(agent_world):
    w = agent_world
    assert 'private' in w.mgr.spawn('cloudy', 'm', chat='vault')['error']
    assert 'private' in w.mgr.spawn('flagless', 'm', chat='vault')['error']
    r = w.mgr.spawn('probe', 'm', chat='vault')                                   # local kind: allowed, carries privacy
    assert 'error' not in r and w.mgr._agents[r['id']].privacy is True
    w.mgr._agents[r['id']].finish()
    assert w.wait_for(lambda: r['id'] not in w.mgr._agents)
    assert [x for x in w.store['rows'] if x['id'] == r['id']][0]['privacy'] is True


def test_a_private_caller_turn_is_private_even_in_a_public_chat(agent_world):
    from core.chat.function_manager import scope_private
    w = agent_world
    tok = scope_private.set(True)
    try:
        assert 'private' in w.mgr.spawn('cloudy', 'm', chat='desk')['error']
    finally:
        scope_private.reset(tok)
    assert 'error' not in w.mgr.spawn('cloudy', 'm', chat='desk')
    for a in list(w.mgr._agents.values()):
        a.finish()


def test_say_and_answer_are_regated_when_the_chat_turns_private(agent_world):
    w = agent_world
    r = w.mgr.spawn('cloudy', 'm', chat='desk', options={'ask': {'text': 'go?'}, 'ask_timeout': 30})
    a = w.mgr._agents[r['id']]
    assert w.wait_for(lambda: a.status == 'waiting')
    w.chats['desk']['private_chat'] = True                                        # the chat flips private
    text, ok = w.mgr.action_text('desk', 'Spark', 'answer', 'yes')
    assert ok is False and 'private' in text
    text, ok = w.mgr.action_text('desk', 'Spark', 'stop', '')                     # stop is always allowed
    assert ok
    a.finish()


# --- chat-local lookups --------------------------------------------------------------------------

def test_agents_are_found_only_from_their_own_chat(agent_world):
    w = agent_world
    r = w.mgr.spawn('probe', 'secret mission', chat='desk')
    with pytest.raises(AgentError, match='no agent named'):
        w.mgr.peek_text('other', 'Spark')
    text, ok = w.mgr.action_text('other', 'Spark', 'stop', '')
    assert ok is False and 'no agent named' in text
    text, ok = w.mgr.peek_text('desk', 'Spark')
    assert ok and 'secret mission' in text
    assert w.mgr.check_all(chat_name='other') == [] and len(w.mgr.check_all(chat_name='desk')) == 1
    w.mgr._agents[r['id']].finish()


def test_recently_finished_elsewhere_never_names_a_private_chats_agent(agent_world):
    w = agent_world
    pub = w.mgr.spawn('probe', 'public work', chat='other')
    priv = w.mgr.spawn('probe', 'private work', chat='vault')
    w.mgr._agents[pub['id']].finish_with_result('ok')
    w.mgr._agents[priv['id']].finish_with_result('ok')
    assert w.wait_for(lambda: not w.mgr._agents)
    text, _ = w.mgr.list_text('desk')
    assert "Spark (probe) done in 'other'" in text or "Alpha (probe) done in 'other'" in text
    assert 'vault' not in text and 'private work' not in text and 'public work' not in text


# --- say, conversational, rows across a restart ------------------------------------------------

def test_say_is_refused_while_running_and_taken_when_idle(agent_world):
    w = agent_world
    r = w.mgr.spawn('talker', 'm', chat='desk')
    a = w.mgr._agents[r['id']]
    text, ok = w.mgr.action_text('desk', 'Spark', 'say', 'more')
    assert ok is False and 'still working' in text
    a.status = 'idle'
    text, ok = w.mgr.action_text('desk', 'Spark', 'say', 'more')
    assert ok and a.said == ['more']
    assert w.mgr.action_text('desk', 'Spark', 'say', '')[1] is False
    a.finish()


def test_rows_that_were_live_at_restart_rest_or_are_lost(agent_world):
    w = agent_world
    w.store['rows'] = [
        {'id': 'aa', 'name': 'Spark', 'kind': 'talker', 'chat': 'desk', 'status': 'idle', 'started': time.time(),
         'resume_token': 'sess-1', 'privacy': False, 'conversational': True},
        {'id': 'bb', 'name': 'Alpha', 'kind': 'probe', 'chat': 'desk', 'status': 'running', 'started': time.time(),
         'resume_token': None, 'privacy': False},
    ]
    w.mgr._rows = None
    rows = {r['id']: r for r in w.mgr._load_rows()}
    assert rows['aa']['status'] == 'resting' and rows['bb']['status'] == 'lost'
    text, _ = w.mgr.list_text('desk')
    assert 'Spark (talker) — resting' in text
    text, ok = w.mgr.action_text('desk', 'Spark', 'say', 'wake up')              # a resting row wakes with say
    assert ok and 'woke' in text
    a = [x for x in w.mgr._agents.values() if x.name == 'Spark'][0]
    assert a.resume_token == 'sess-1' and a.mission == 'wake up'
    a.finish()


def test_dismiss_and_recall_work_on_live_agents_and_on_rows(agent_world):
    w = agent_world
    r = w.mgr.spawn('probe', 'm', chat='desk')
    assert w.mgr.recall(r['id'])['result'] == 'Agent is still running.'
    w.mgr._agents[r['id']].finish_with_result('the answer')
    assert w.wait_for(lambda: r['id'] not in w.mgr._agents)
    assert w.mgr.recall(r['id'])['result'] == 'the answer'                        # from the row's content
    assert w.mgr.dismiss(r['id'])['status'] == 'dismissed'
    assert 'error' in w.mgr.dismiss('nope')
    assert 'error' in w.mgr.recall('nope')


def test_shutdown_stops_live_agents(agent_world):
    w = agent_world
    ids = [w.mgr.spawn('probe', f'm{i}', chat='desk')['id'] for i in range(2)]
    w.mgr.shutdown(timeout=2)
    assert not w.mgr._agents
    for i in ids:
        assert [x for x in w.store['rows'] if x['id'] == i][0]['status'] == 'stopped'


# --- the tools ----------------------------------------------------------------------------------------

def test_tool_descriptions_carry_kinds_and_never_live_agents(agent_world):
    import functions.agents as fa
    w = agent_world
    r = w.mgr.spawn('probe', 'a very private mission', chat='desk')
    tools = fa.get_tools()
    spawn = [t for t in tools if t['function']['name'] == 'agent_spawn'][0]['function']
    assert 'probe' in spawn['description'] and set(spawn['parameters']['properties']['kind']['enum']) >= {'probe', 'cloudy'}
    blob = json.dumps(tools)
    assert 'Spark' not in blob and 'private mission' not in blob
    assert all(t['is_local'] is True for t in tools)
    w.mgr._agents[r['id']].finish()


def test_spawn_screen_and_option_validation(agent_world):
    w = agent_world
    text, ok = w.mgr.spawn_text('desk', 'probe', '', None)
    assert ok and 'Its own options' in text and 'ask' in text
    text, ok = w.mgr.spawn_text('desk', 'probe', 'go', {'bogus': 1})
    assert ok is False and 'bogus' in text
    text, ok = w.mgr.spawn_text('desk', 'probe', 'go', '{"ask_timeout": 1}')      # a JSON string is accepted
    assert ok and 'dispatched' in text
    text, ok = w.mgr.spawn_text('desk', 'nope', 'go', None)
    assert ok is False and 'no agent kind' in text
    for a in list(w.mgr._agents.values()):
        a.finish()


def test_the_four_tools_dispatch_and_the_mcp_loop_breaker(agent_world, monkeypatch):
    import functions.agents as fa
    from core import mcp_server
    w = agent_world
    monkeypatch.setattr(fa, '_manager', lambda: w.mgr)
    monkeypatch.setattr(fa, '_chat', lambda: 'desk')
    text, ok = fa.execute('agent_list', {}, None)
    assert ok and 'probe' in text
    text, ok = fa.execute('agent_spawn', {'kind': 'probe', 'mission': 'go'}, None)
    assert ok and 'Spark dispatched' in text
    text, ok = fa.execute('agent_peek', {'agent': 'Spark'}, None)
    assert ok and 'running' in text
    text, ok = fa.execute('agent_action', {'agent': 'Spark'}, None)
    assert ok and 'stop' in text
    tok = mcp_server.answering.set(True)
    try:
        text, ok = fa.execute('agent_spawn', {'kind': 'probe', 'mission': 'again'}, None)
        assert ok is False and 'MCP' in text
        text, ok = fa.execute('agent_action', {'agent': 'Spark', 'action': 'say', 'value': 'x'}, None)
        assert ok is False and 'MCP' in text
        assert fa.execute('agent_list', {}, None)[1]                              # looking is fine
    finally:
        mcp_server.answering.reset(tok)
    text, ok = fa.execute('agent_action', {'agent': 'Spark', 'action': 'stop'}, None)
    assert ok


def test_register_type_is_a_logged_no_op_for_old_plugins(agent_world):
    w = agent_world
    w.mgr.register_type('old', 'Old', lambda **k: None)                           # no raise
    assert 'old' not in w.mgr.get_types() and 'probe' in w.mgr.get_types()


def test_rows_and_live_agents_follow_a_chat_rename_and_die_with_a_delete(agent_world):
    w = agent_world
    live = w.mgr.spawn('probe', 'm', chat='desk')
    w.chats['desk-2'] = {'private_chat': False}
    w.mgr.chat_renamed('desk', 'desk-2')
    assert w.mgr._agents[live['id']].chat == 'desk-2'
    assert all(r['chat'] == 'desk-2' for r in w.store['rows'] if r['id'] == live['id'])
    text, ok = w.mgr.peek_text('desk-2', 'Spark')              # found under the new name only
    assert ok
    w.mgr.chat_deleted('desk-2')
    assert w.wait_for(lambda: live['id'] not in w.mgr._agents)
    assert not [r for r in w.store['rows'] if r['id'] == live['id']]
