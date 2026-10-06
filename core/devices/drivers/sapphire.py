# core/devices/drivers/sapphire.py - another Sapphire (tmp/device-manager-upgrade-plan.md §9)
#
# A second Sapphire on the network, reached through her MCP door (POST /mcp,
# core/mcp_server.py) with one of her API tokens. Two things it gives this
# one: a conversation - `ask` lands in a named chat over there and her answer
# comes back as the result, `tell` does not wait - and her shared tools,
# whichever her user ticked under Settings > MCP Server there. The far side
# decides what can be done; this side only discovers it.
#
# A turn that itself came in over MCP may not ask a Sapphire back: that is
# how two of them would talk in circles. mcp_server.answering says so.
import json
import logging
import socket
import threading
import time
from urllib.parse import urlsplit

import requests

from core import net

logger = logging.getLogger(__name__)

SPEC = {
    'label': 'Another Sapphire',
    'icon': '\U0001f48e',
    'capabilities': ['chat', 'tools'],
    'config_schema': [
        {'key': 'url', 'type': 'string', 'label': 'Address', 'tab': 'Status', 'setup': True,
         'placeholder': 'https://192.168.1.102:8073',
         'help': 'Her address on your network. Her MCP door has to be on: Settings > MCP Server, over there.'},
        {'key': 'token', 'type': 'string', 'widget': 'password', 'secret': True, 'tab': 'Status', 'setup': True,
         'label': 'Her API token', 'help': 'Made on that Sapphire under System > API Keys. Stored scrambled.'},
        {'key': 'chat', 'type': 'string', 'label': 'Talks in her chat', 'capability': 'chat', 'setup': True,
         'placeholder': 'desk-sapph',
         'help': 'The chat on HER side where what this Sapphire says lands. Made there if it does not exist.'},
        {'key': 'me', 'type': 'string', 'label': 'Call me', 'capability': 'chat', 'default': '',
         'placeholder': 'the Sapphire on the server',
         'help': 'How she is told who is talking. Empty = this machine\'s name.'},
    ],
}

QUICK = 8                     # seconds for health and a tool list
ASK_WAIT = 180                # seconds her answer may take
TOOL_WAIT = 60
ABOUT_FRESH = 60              # seconds the far tool list is taken as true
OWN = ('ask', 'tell')         # the door's own two: the chat capability, not tools
_about = {}                   # device id -> (monotonic, [tools])
_lock = threading.Lock()


class Problem(Exception):
    """A reason fit to show as it is."""


class Shut(Problem):
    """Her MCP door answers 404: it is off over there."""


# --- reaching her -----------------------------------------------------------------

def _base(config):
    return str(config.get('url') or '').rstrip('/')


def _where(config):
    return urlsplit(_base(config)).netloc or _base(config)


def _me(config):
    return str(config.get('me') or '').strip() or socket.gethostname()


def _request(method, path, config, secrets=None, timeout=QUICK, **kw):
    headers = dict(kw.pop('headers', None) or {})
    key = (secrets or {}).get('token') if secrets is not None else None
    if key:
        headers['Authorization'] = 'Bearer ' + key
    try:
        return net.request(method, _base(config) + path, timeout=timeout, headers=headers,
                           verify=False, **kw)       # her certificate is her own, on your own network
    except requests.exceptions.Timeout:
        raise Problem(f"No answer from {_where(config)} within {timeout}s.")
    except requests.exceptions.RequestException:
        raise Problem(f"Could not reach {_where(config)}. Is she running?")


def _rpc(method, params, config, secrets, timeout=QUICK):
    """One JSON-RPC call to her /mcp. Returns the result. Raises Problem."""
    if not (secrets or {}).get('token'):
        raise Problem("No token is stored for her. Enter one in Settings > Devices.")
    body = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    r = _request('POST', '/mcp', config, secrets, timeout=timeout, json=body,
                 headers={'Accept': 'application/json, text/event-stream'})
    if r.status_code in (401, 403):
        raise Problem("She refused the token. Make a new one there (System > API Keys) and enter it.")
    if r.status_code == 404:
        raise Shut("Her MCP door is off. Turn it on over there: Settings > MCP Server.")
    if r.status_code >= 400:
        raise Problem(f"She answered HTTP {r.status_code}.")
    try:
        said = r.json()
    except ValueError:
        raise Problem("Her answer was not JSON.")
    if not isinstance(said, dict):
        raise Problem("Her answer had no shape.")
    if said.get('error'):
        raise Problem(str((said['error'] or {}).get('message') or 'an error without words')[:300])
    return said.get('result') or {}


def _call(name, args, config, secrets, timeout):
    """tools/call: (text, ok)."""
    out = _rpc('tools/call', {'name': name, 'arguments': args}, config, secrets, timeout=timeout)
    text = '\n'.join(str(c.get('text') or '') for c in (out.get('content') or []) if isinstance(c, dict)).strip()
    return text or '(nothing came back)', not out.get('isError')


def _tools(device, config, secrets, fresh=False):
    """Her shared tools beyond ask and tell, as she lists them. Asked again
    after a minute."""
    with _lock:
        when, told = _about.get(device['id'], (0, None))
    if fresh or told is None or time.monotonic() - when > ABOUT_FRESH:
        with _lock:
            _about.pop(device['id'], None)
        out = _rpc('tools/list', {}, config, secrets)
        told = [t for t in (out.get('tools') or []) if isinstance(t, dict) and t.get('name') not in OWN]
        with _lock:
            _about[device['id']] = (time.monotonic(), told)
    return told


# --- her words into a tool's arguments -------------------------------------------

def _slots(schema):
    """The far tool's string arguments, in the order it declares them."""
    props = (schema or {}).get('properties') or {}
    strings = [k for k, p in props.items() if str((p or {}).get('type') or 'string') == 'string']
    return strings or list(props)


def _args(schema, value):
    """What she typed, as the far tool's arguments. A JSON object is taken as
    it is. Otherwise her words fill the tool's string arguments in their
    declared order, the last one taking the rest - `device_action` is `pi2
    light set purple pulse` - and a tool with one argument gets the whole
    line. What she leaves out is left out."""
    value = str(value or '').strip()
    if value.startswith('{'):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    order = _slots(schema)
    if not order or not value:
        return {}
    words = value.split(None, len(order) - 1)
    return {k: w for k, w in zip(order, words)}


# --- the driver's doors -----------------------------------------------------------

def validate(config):
    url = str(config.get('url') or '').strip().rstrip('/')
    if not url:
        return config, "Her address is needed."
    if '://' not in url:
        url = 'https://' + url
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return config, f"'{url}' is not a usable address. Example: https://192.168.1.102:8073"
    if net.classify(parts.hostname) != 'lan':
        return config, "Another Sapphire has to be on your own network for now."
    config['url'] = f"{parts.scheme}://{parts.netloc}"
    config['chat'] = str(config.get('chat') or '').strip()[:64]
    if not config['chat']:
        return config, "Name the chat on her side where this Sapphire's words should land."
    config['me'] = str(config.get('me') or '').strip()[:60]
    return config, ''


def describe(device, config):
    told = {
        'chat': {'label': 'Chat', 'help': f"talk with her, in her chat '{config.get('chat') or '?'}'", 'actions': {
            'ask': {'help': 'ask or tell her something and get her answer back. She sees who asked',
                    'example': 'What is the volume on your desktop right now?', 'values': '<text>'},
            'tell': {'help': 'say something to her without waiting for an answer',
                     'example': 'Dinner is in ten minutes.', 'values': '<text>'},
        }},
    }
    with _lock:
        _, tools = _about.get(device['id'], (0, None))
    actions = {}
    for t in tools or []:
        slots = _slots(t.get('inputSchema'))
        actions[t['name']] = {'help': str(t.get('description') or '')[:160],
                              'example': '', 'values': ' '.join(f'<{k}>' for k in slots[:4]) or '(no value)'}
    if actions:
        told['tools'] = {'label': 'Her tools', 'help': 'the tools her user shares with you', 'actions': actions}
    return told


def status(device, config, secrets):
    try:
        h = _request('GET', '/api/health', config, timeout=QUICK)
    except Problem as e:
        return {'online': False, 'detail': str(e)}
    if h.status_code >= 400:
        return {'online': False, 'detail': f"{_where(config)} answered HTTP {h.status_code} to a health check."}
    try:
        name = (h.json() or {}).get('name') or ''
    except ValueError:
        name = ''
    detail = f"Sapphire{' ' + name if name else ''} at {_where(config)}"
    try:
        tools = _tools(device, config, secrets, fresh=True)
    except Shut as e:
        return {'online': True, 'detail': f"{detail}. {e}", 'has': [], 'readings': {}}
    except Problem as e:
        return {'online': True, 'detail': f"{detail}. {e}", 'has': [], 'readings': {}}
    return {'online': True, 'detail': detail, 'has': ['chat'] + (['tools'] if tools else []),
            'readings': {'tools shared': str(len(tools))}}


def run(device, capability, action, value, config, secrets, call_tool):
    from core import mcp_server
    if mcp_server.answering.get():
        return ("This question came in over MCP. Answer it in your message; she reads that. "
                "Asking another Sapphire from here would go round in circles."), False
    try:
        if capability == 'chat':
            text = str(value or '').strip()
            if not text:
                return f"{action}: the value is what to say to her. Example: What are you up to?", True
            args = {'text': text, 'chat': str(config.get('chat') or ''), 'from': f"another Sapphire, {_me(config)}"}
            if action == 'ask':
                said, ok = _call('ask', args, config, secrets, ASK_WAIT)
                return (f'She answered: "{said}"' if ok else said), ok
            if action == 'tell':
                return _call('tell', args, config, secrets, QUICK)
        if capability == 'tools':
            tool = next((t for t in _tools(device, config, secrets) if t.get('name') == action), None)
            if not tool:
                return f"She does not share a tool named '{action}' right now.", False
            return _call(action, _args(tool.get('inputSchema'), value), config, secrets, TOOL_WAIT)
        return f"'{capability}' has no action '{action}'.", False
    except Problem as e:
        return str(e), False
