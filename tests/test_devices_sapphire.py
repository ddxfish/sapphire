# tests/test_devices_sapphire.py - another Sapphire as a device
# (core/devices/drivers/sapphire.py). The far Sapphire is a fake answering
# /api/health and /mcp; nothing listens on a port.
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from core import mcp_server
from core.devices.drivers import sapphire as sap

DEV = {'id': 'desk-sapph', 'label': 'Desk Sapphire', 'location': 'the office'}
CFG = {'url': 'https://192.168.0.69:8073', 'chat': 'server-sapph', 'me': 'the Sapphire on the server'}
KEY = {'token': 'tok-abcdefghijklmnop'}
TOOLS = [
    {'name': 'ask', 'description': 'Ask', 'inputSchema': {'type': 'object', 'properties': {'text': {}, 'chat': {}}, 'required': ['text', 'chat']}},
    {'name': 'tell', 'description': 'Tell', 'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'device_action', 'description': 'Use a device over there.',
     'inputSchema': {'type': 'object', 'properties': {'device': {'type': 'string'}, 'capability': {'type': 'string'},
                                                       'action': {'type': 'string'}, 'value': {'type': 'string'}},
                     'required': ['device', 'capability', 'action']}},
    {'name': 'get_weather', 'description': 'Weather.', 'inputSchema': {'type': 'object', 'properties': {'place': {'type': 'string'}}, 'required': ['place']}},
]


class Reply:
    def __init__(self, body, status=200):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def far():
    """The far Sapphire. calls = every request; answers = overrides by (method, path)."""
    state = SimpleNamespace(calls=[], answers={}, health={'status': 'ok', 'name': 'desk'}, tools=TOOLS, door=True)

    def request(method, url, **kw):
        path = url.split('8073', 1)[1]
        state.calls.append(SimpleNamespace(method=method, path=path, kw=kw))
        if (method, path) in state.answers:
            out = state.answers[(method, path)]
            return out() if callable(out) else out
        if path == '/api/health':
            return Reply(state.health)
        if path == '/mcp':
            if not state.door:
                return Reply({'detail': 'off'}, 404)
            if kw['headers'].get('Authorization') != 'Bearer ' + KEY['token']:
                return Reply({'detail': 'Unauthorized'}, 401)
            rpc = kw['json']
            if rpc['method'] == 'tools/list':
                return Reply({'jsonrpc': '2.0', 'id': 1, 'result': {'tools': state.tools}})
            if rpc['method'] == 'tools/call':
                name, args = rpc['params']['name'], rpc['params']['arguments']
                text = {'ask': lambda: f"It is 64 degrees. (asked in {args['chat']} by {args['from']})",
                        'tell': lambda: f"Told her, in the chat '{args['chat']}'.",
                        'device_action': lambda: f"ran {json.dumps(args, sort_keys=True)}",
                        'get_weather': lambda: f"weather for {args.get('place')}"}.get(name, lambda: 'no such tool')()
                return Reply({'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': text}],
                                                                   'isError': name not in {t['name'] for t in state.tools}}})
        return Reply({'detail': 'not found'}, 404)
    sap._about.clear()
    with patch.object(sap.net, 'request', request):
        yield state
    sap._about.clear()


def run(cap, action, value=''):
    return sap.run(DEV, cap, action, value, dict(CFG), KEY, None)


# --- the address ---------------------------------------------------------------

def test_validate_wants_a_lan_address_and_a_chat():
    assert sap.validate({'url': ''})[1] == "Her address is needed."
    assert 'own network' in sap.validate({'url': 'https://sapphire.example.com'})[1]
    assert 'Name the chat' in sap.validate({'url': '192.168.0.69:8073'})[1]
    cfg, why = sap.validate({'url': '192.168.0.69:8073/', 'chat': ' server-sapph ', 'me': ''})
    assert why == '' and cfg['url'] == 'https://192.168.0.69:8073' and cfg['chat'] == 'server-sapph'


# --- what she has, as the far side says -------------------------------------------

def test_status_reads_her_health_and_her_shared_tools(far):
    st = sap.status(DEV, CFG, KEY)
    assert st['online'] is True and st['detail'] == 'Sapphire desk at 192.168.0.69:8073'
    assert st['has'] == ['chat', 'tools'] and st['readings'] == {'tools shared': '2'}
    assert [c.path for c in far.calls] == ['/api/health', '/mcp']
    assert far.calls[0].kw['headers'].get('Authorization') is None     # health is an open door
    assert far.calls[1].kw['verify'] is False                           # her own certificate
    told = sap.describe(DEV, CFG)
    assert set(told['tools']['actions']) == {'device_action', 'get_weather'}
    assert told['tools']['actions']['device_action']['values'] == '<device> <capability> <action> <value>'
    assert told['chat']['actions']['ask']['values'] == '<text>'


def test_a_sapphire_with_only_ask_and_tell_has_chat_and_nothing_else(far):
    far.tools = TOOLS[:2]
    st = sap.status(DEV, CFG, KEY)
    assert st['has'] == ['chat'] and st['readings'] == {'tools shared': '0'}
    assert 'tools' not in sap.describe(DEV, CFG)


def test_her_door_being_off_or_the_token_wrong_is_said_not_hidden(far):
    far.door = False
    st = sap.status(DEV, CFG, KEY)
    assert st['online'] is True and st['has'] == [] and 'MCP door is off' in st['detail']
    far.door = True
    st = sap.status(DEV, CFG, {'token': 'wrong'})
    assert st['online'] is True and st['has'] == [] and 'refused the token' in st['detail']
    assert sap.status(DEV, CFG, {})['detail'].endswith('No token is stored for her. Enter one in Settings > Devices.')


def test_a_sapphire_that_is_not_there(far):
    def gone(method, url, **kw):
        raise requests.exceptions.ConnectionError('gone')
    with patch.object(sap.net, 'request', gone):
        st = sap.status(DEV, CFG, KEY)
    assert st == {'online': False, 'detail': 'Could not reach 192.168.0.69:8073. Is she running?'}


# --- talking ---------------------------------------------------------------------

def test_ask_lands_in_her_chat_with_who_is_asking_and_brings_the_answer_back(far):
    text, ok = run('chat', 'ask', 'what is the weather?')
    assert ok and text == 'She answered: "It is 64 degrees. (asked in server-sapph by another Sapphire, the Sapphire on the server)"'
    call = far.calls[-1]
    assert call.kw['json']['params'] == {'name': 'ask', 'arguments': {
        'text': 'what is the weather?', 'chat': 'server-sapph', 'from': 'another Sapphire, the Sapphire on the server'}}
    assert call.kw['timeout'] == sap.ASK_WAIT


def test_tell_does_not_wait_and_an_empty_value_is_explained(far):
    assert run('chat', 'tell', 'dinner is ready') == ("Told her, in the chat 'server-sapph'.", True)
    text, ok = run('chat', 'ask', '')
    assert ok and text.startswith('ask: the value is what to say to her')
    assert all(c.path != '/mcp' or c.kw['json']['params']['name'] != 'ask' for c in far.calls)


def test_me_falls_back_to_this_machine_name(far):
    with patch.object(sap.socket, 'gethostname', return_value='smokey'):
        sap.run(DEV, 'chat', 'tell', 'hi', dict(CFG, me=''), KEY, None)
    assert far.calls[-1].kw['json']['params']['arguments']['from'] == 'another Sapphire, smokey'


def test_a_turn_that_came_in_over_mcp_may_not_ask_back(far):
    token = mcp_server.answering.set(True)
    try:
        text, ok = run('chat', 'ask', 'and what do you think?')
    finally:
        mcp_server.answering.reset(token)
    assert not ok and 'Answer it in your message' in text
    assert far.calls == []                                               # nothing went over the wire


# --- her tools -----------------------------------------------------------------------

def test_her_words_become_the_far_tools_arguments(far):
    text, ok = run('tools', 'device_action', 'computer sound volume 40')
    assert ok and text == 'ran {"action": "volume", "capability": "sound", "device": "computer", "value": "40"}'
    run('tools', 'device_action', 'pi2 light set purple pulse bpm=40')
    args = far.calls[-1].kw['json']['params']['arguments']
    assert args == {'device': 'pi2', 'capability': 'light', 'action': 'set', 'value': 'purple pulse bpm=40'}  # the last takes the rest
    run('tools', 'device_action', 'pi2')
    assert far.calls[-1].kw['json']['params']['arguments'] == {'device': 'pi2'}            # what she leaves out is left out
    run('tools', 'get_weather', 'Buffalo NY')
    assert far.calls[-1].kw['json']['params']['arguments'] == {'place': 'Buffalo NY'}      # one argument: the whole line
    run('tools', 'get_weather', '{"place": "Buffalo", "units": "F"}')
    assert far.calls[-1].kw['json']['params']['arguments'] == {'place': 'Buffalo', 'units': 'F'}   # JSON as it is


def test_a_tool_she_does_not_share_is_refused_here(far):
    text, ok = run('tools', 'rm_rf')
    assert not ok and "does not share a tool named 'rm_rf'" in text


def test_her_errors_come_back_as_errors(far):
    far.answers[('POST', '/mcp')] = Reply({'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32602, 'message': 'needs a name'}})
    text, ok = run('chat', 'ask', 'hi')
    assert not ok and text == 'needs a name'
    far.answers[('POST', '/mcp')] = Reply(ValueError('html'))
    assert run('chat', 'ask', 'hi') == ('Her answer was not JSON.', False)
