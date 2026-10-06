# tests/test_mcp_server.py - the MCP door (core/mcp_server.py, core/routes/mcp.py):
# what it offers, ask and tell, and the JSON-RPC around them. The function
# manager, the chats and the turn engine are faked; nothing listens on a port.
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config
from core import mcp_server as mcp
from core.chat.chat import ChatBusy


def _tool(name, desc='does a thing', params=None):
    return {'type': 'function', 'function': {'name': name, 'description': desc,
                                             'parameters': params or {'type': 'object', 'properties': {'x': {'type': 'string'}}}}}


@pytest.fixture
def world(monkeypatch):
    """A Sapphire with three tool modules: core devices, a plain plugin, and a
    plugin that keeps a memory scope."""
    fm = MagicMock()
    fm.function_modules = {
        'devices': {'tools': [_tool('device_list'), _tool('device_action')], '_plugin': None, 'emoji': '🎛'},
        'weather': {'tools': [_tool('get_weather'), _tool('secret_probe')], '_plugin': 'weather', 'emoji': '⛅'},
        'memory':  {'tools': [_tool('remember')], '_plugin': 'memory', 'emoji': '🧠'},
    }
    fm.get_hidden_functions.return_value = {'secret_probe'}
    fm.execute_function.return_value = ('sunny, 20 C', True)
    sm = MagicMock()
    chats = {'desk-sapph': {'private_chat': False}}
    sm.get_settings_for.side_effect = lambda name: chats.get(name)
    sm.create_chat.side_effect = lambda name, settings=None: chats.setdefault(name, {'private_chat': False}) and True
    system = SimpleNamespace(llm_chat=SimpleNamespace(function_manager=fm, session_manager=sm))
    monkeypatch.setattr(config, 'MCP_SERVER_ENABLED', True, raising=False)
    monkeypatch.setattr(config, 'MCP_SERVER_TOOLS', ['get_weather', 'device_action', 'remember', 'secret_probe', 'gone'], raising=False)
    with patch.object(mcp, '_scoped_plugins', return_value={'memory'}):
        yield SimpleNamespace(system=system, fm=fm, sm=sm, chats=chats)


def rpc(method, ident=1, **params):
    return {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params}


# --- what it offers ------------------------------------------------------------

def test_the_catalogue_names_every_tool_with_its_state(world):
    rows = {r['name']: r for r in mcp.catalogue(world.system)}
    assert [r for r in rows if rows[r]['own']] == ['ask', 'tell']
    assert rows['get_weather']['exposed'] and rows['get_weather']['plugin'] == 'weather'
    assert rows['device_action']['exposed'] and not rows['device_list']['exposed']
    assert rows['remember']['scoped'] and not rows['remember']['exposed']      # ticked, and still not offered
    assert 'secret_probe' not in rows                                             # hidden tools are not even listed


def test_tools_list_is_own_two_then_the_ticked_ones_that_may_go_out(world):
    out = mcp.handle(world.system, rpc('tools/list'))
    names = [t['name'] for t in out['result']['tools']]
    assert names == ['ask', 'tell', 'device_action', 'get_weather']
    weather = next(t for t in out['result']['tools'] if t['name'] == 'get_weather')
    assert weather['inputSchema'] == {'type': 'object', 'properties': {'x': {'type': 'string'}}}


def test_initialize_and_ping(world):
    out = mcp.handle(world.system, rpc('initialize', protocolVersion='2024-11-05', capabilities={}))
    assert out['result']['protocolVersion'] == mcp.PROTOCOL
    assert out['result']['serverInfo']['name'] == 'Sapphire' and out['result']['capabilities'] == {'tools': {'listChanged': False}}
    assert mcp.handle(world.system, rpc('ping', ident=7)) == {'jsonrpc': '2.0', 'id': 7, 'result': {}}


# --- ask and tell ----------------------------------------------------------------

def test_ask_runs_a_turn_in_the_named_chat_with_her_line_and_returns_the_answer(world):
    seen = {}

    def run_turn(chat, text, source=None, **kw):
        seen.update(chat=chat, text=text, source=source, answering=mcp.answering.get())
        return 'It is 64 degrees.'
    with patch('core.cadence.run_turn', side_effect=run_turn):
        out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={
            'text': 'what is the weather?', 'chat': 'desk-sapph', 'from': 'another Sapphire, desk-sapph'}), where='192.168.1.101')
    assert out['result'] == {'content': [{'type': 'text', 'text': 'It is 64 degrees.'}], 'isError': False}
    assert seen['chat'] == 'desk-sapph' and seen['source'] == 'mcp:192.168.1.101'
    assert seen['text'] == ('[This is from another Sapphire, desk-sapph at 192.168.1.101. '
                            'Answer in your message; no tool is needed to reply.]\nwhat is the weather?')
    assert seen['answering'] is True                      # the belt under the braces, on during her turn
    assert mcp.answering.get() is False                   # and off again after
    world.sm.create_chat.assert_not_called()              # the chat was there


def test_ask_makes_the_chat_when_it_is_missing(world):
    with patch('core.cadence.run_turn', return_value='Hello.') as run_turn:
        out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={'text': 'hi', 'chat': 'New Room'}))
    assert out['result']['isError'] is False
    world.sm.create_chat.assert_called_once()
    made = world.sm.create_chat.call_args.args[0]
    assert made in world.chats and run_turn.call_args.args[0] == made


def test_ask_waits_for_a_busy_chat_and_gives_up_in_time(world):
    with patch('core.cadence.run_turn', side_effect=[ChatBusy('desk-sapph'), 'Now.']), \
         patch.object(mcp.time, 'sleep'):
        out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={'text': 'hi', 'chat': 'desk-sapph'}))
    assert out['result']['content'][0]['text'] == 'Now.'
    clock = iter([0, 0, 100, 100])
    with patch('core.cadence.run_turn', side_effect=ChatBusy('desk-sapph')), \
         patch.object(mcp.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(mcp.time, 'sleep'):
        out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={'text': 'hi', 'chat': 'desk-sapph'}))
    assert out['result']['isError'] is True and 'mid-turn' in out['result']['content'][0]['text']


def test_ask_refuses_what_it_should(world):
    out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={'text': 'hi'}))
    assert out['result']['isError'] is True and 'name' in out['result']['content'][0]['text']
    with patch('core.cadence.run_turn', side_effect=RuntimeError('provider down')):
        out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={'text': 'hi', 'chat': 'desk-sapph'}))
    assert out['result']['isError'] is True and out['result']['content'][0]['text'] == 'provider down'
    with patch('core.cadence.run_turn', side_effect=KeyError('odd')):
        out = mcp.handle(world.system, rpc('tools/call', name='ask', arguments={'text': 'hi', 'chat': 'desk-sapph'}))
    assert out['result']['isError'] is True and out['result']['content'][0]['text'] == 'ask failed: KeyError'


def test_tell_answers_at_once_and_the_turn_runs_behind(world):
    ran = []
    with patch('core.cadence.run_turn', side_effect=lambda chat, text, **kw: ran.append((chat, text)) or 'ok'):
        out = mcp.handle(world.system, rpc('tools/call', name='tell', arguments={
            'text': 'dinner is ready', 'chat': 'desk-sapph', 'from': 'Krem'}))
        assert out['result'] == {'content': [{'type': 'text', 'text': "Told her, in the chat 'desk-sapph'."}], 'isError': False}
        for _ in range(100):
            if ran:
                break
            time.sleep(0.02)
    assert ran and ran[0][0] == 'desk-sapph' and ran[0][1].startswith('[This is from Krem')


# --- her other tools -------------------------------------------------------------

def test_a_ticked_tool_runs_through_the_function_manager(world):
    out = mcp.handle(world.system, rpc('tools/call', name='get_weather', arguments={'x': 'Buffalo'}))
    assert out['result'] == {'content': [{'type': 'text', 'text': 'sunny, 20 C'}], 'isError': False}
    name, args = world.fm.execute_function.call_args.args
    kw = world.fm.execute_function.call_args.kwargs
    assert (name, args) == ('get_weather', {'x': 'Buffalo'})
    assert kw['with_success'] is True and set(kw['allowed_tools']) == {'ask', 'tell', 'device_action', 'get_weather'}


def test_a_tool_that_is_not_offered_is_not_run(world):
    for name in ('device_list', 'remember', 'secret_probe', 'gone', 'rm_rf'):
        out = mcp.handle(world.system, rpc('tools/call', name=name, arguments={}))
        assert out['result']['isError'] is True and 'no tool named' in out['result']['content'][0]['text']
    world.fm.execute_function.assert_not_called()


def test_a_picture_taking_tool_sends_its_words_only(world):
    world.fm.execute_function.return_value = ({'text': 'A picture.', 'images': [{'data': 'x'}]}, True)
    out = mcp.handle(world.system, rpc('tools/call', name='device_action', arguments={}))
    assert out['result']['content'] == [{'type': 'text', 'text': 'A picture.'}]


# --- the protocol around it ----------------------------------------------------------

def test_notifications_get_no_answer_and_batches_get_a_list(world):
    assert mcp.handle(world.system, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert mcp.handle_body(world.system, [{'jsonrpc': '2.0', 'method': 'notifications/initialized'}]) is None
    out = mcp.handle_body(world.system, [rpc('ping', ident=1), {'jsonrpc': '2.0', 'method': 'notifications/x'}, rpc('ping', ident=2)])
    assert [a['id'] for a in out] == [1, 2]


def test_bad_requests_are_named(world):
    assert mcp.handle(world.system, {'id': 1, 'method': 'ping'})['error']['code'] == -32600
    assert mcp.handle(world.system, rpc('resources/list'))['error']['code'] == -32601
    assert mcp.handle(world.system, rpc('tools/call', arguments={}))['error']['code'] == -32602
    assert mcp.handle(world.system, 'nonsense')['error']['code'] == -32600


# --- the route ----------------------------------------------------------------------

class _Request:
    def __init__(self, body, ip='192.168.1.101'):
        self._body, self.headers, self.client = body, {}, SimpleNamespace(host=ip)

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_the_door_is_shut_until_turned_on(world, monkeypatch):
    from fastapi import HTTPException
    from core.routes import mcp as route
    monkeypatch.setattr(config, 'MCP_SERVER_ENABLED', False, raising=False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(route.mcp_post(_Request(rpc('ping')), None, world.system))
    assert e.value.status_code == 404 and 'Settings > MCP Server' in e.value.detail


def test_the_door_answers_json_and_takes_notifications_quietly(world):
    import json
    from core.routes import mcp as route
    res = asyncio.run(route.mcp_post(_Request(rpc('ping', ident=3)), None, world.system))
    assert res.status_code == 200 and res.headers['mcp-protocol-version'] == mcp.PROTOCOL
    assert json.loads(res.body) == {'jsonrpc': '2.0', 'id': 3, 'result': {}}
    res = asyncio.run(route.mcp_post(_Request({'jsonrpc': '2.0', 'method': 'notifications/initialized'}), None, world.system))
    assert res.status_code == 202
    res = asyncio.run(route.mcp_post(_Request(ValueError('not json')), None, world.system))
    assert res.status_code == 400 and json.loads(res.body)['error']['code'] == -32700
