# core/mcp_server.py - what Sapphire offers over MCP (tmp/device-manager-upgrade-plan.md §9)
#
# The Model Context Protocol, served: a client with one of her API tokens can
# list and call the tools the user ticked under Settings > MCP Server, plus
# two tools of this door's own - `ask` and `tell`, a conversation in a named
# chat. Another Sapphire is the first client (the "Another Sapphire" device);
# Claude Code is the second.
#
# The shape is the smallest the protocol allows: Streamable HTTP, one POST,
# JSON answers (no event stream - nothing here needs to push), no session.
# Tools only: no resources, no prompts. The route is core/routes/mcp.py; this
# file is the protocol and the tools, so a test needs no HTTP.
import logging
import threading
import time
from contextvars import ContextVar
from pathlib import Path

import config

logger = logging.getLogger(__name__)

PROTOCOL = '2025-06-18'        # the version this door speaks; a client's older date is answered with ours
BUSY_WAIT = 20                 # seconds an ask waits for a chat that is mid-turn
TEXT_MAX = 8000                # characters of one ask or tell
OWN = ('ask', 'tell')          # this door's own tools, always offered

# True while a turn that came in through `ask` or `tell` runs: a tool that
# would ask another Sapphire checks it and says "answer in your message"
# instead, so two Sapphires can never ask each other round and round.
answering = ContextVar('mcp_answering', default=False)

_TOOLS = {
    'ask': {
        'name': 'ask',
        'description': "Ask Sapphire something in one of her chats and get her answer. "
                       "The chat is made if it does not exist yet.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'text': {'type': 'string', 'description': 'What to say to her.'},
                'chat': {'type': 'string', 'description': 'The chat it lands in, for example "desk-sapph".'},
                'from': {'type': 'string', 'description': 'Who is asking, as she should see it. Example: "another Sapphire, desk-sapph".'},
            },
            'required': ['text', 'chat'],
        },
    },
    'tell': {
        'name': 'tell',
        'description': "Tell Sapphire something in one of her chats without waiting for her answer. "
                       "The chat is made if it does not exist yet.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'text': {'type': 'string', 'description': 'What to say to her.'},
                'chat': {'type': 'string', 'description': 'The chat it lands in.'},
                'from': {'type': 'string', 'description': 'Who is talking, as she should see it.'},
            },
            'required': ['text', 'chat'],
        },
    },
}


def _version():
    try:
        return (Path(__file__).resolve().parent.parent / 'VERSION').read_text().strip() or 'dev'
    except OSError:
        return 'dev'


def enabled():
    return bool(getattr(config, 'MCP_SERVER_ENABLED', False))


def ticked():
    """The tool names the user exposed, as saved."""
    names = getattr(config, 'MCP_SERVER_TOOLS', None) or []
    return [str(n) for n in names if isinstance(n, str) and n] if isinstance(names, list) else []


# --- the catalogue -----------------------------------------------------------

def _scoped_plugins():
    """Plugins that registered a scope: their tools stay off this door for now
    (memory over MCP is a subsystem of its own)."""
    from core.chat.function_manager import SCOPE_REGISTRY
    return {e.get('plugin') for e in SCOPE_REGISTRY.values() if e.get('plugin')}


def catalogue(system):
    """Every tool she has, for the settings page: [{name, description, module,
    plugin, emoji, scoped, exposed}], hidden tools left out, this door's own
    two first."""
    fm = getattr(getattr(system, 'llm_chat', None), 'function_manager', None)
    out = [{'name': n, 'description': _TOOLS[n]['description'], 'module': 'mcp', 'plugin': 'core',
            'emoji': '', 'scoped': False, 'exposed': True, 'own': True} for n in OWN]
    if fm is None:
        return out
    scoped, hidden, on = _scoped_plugins(), fm.get_hidden_functions(), set(ticked())
    for module, info in fm.function_modules.items():
        plugin = info.get('_plugin') or 'core'
        for tool in info.get('tools') or []:
            f = tool.get('function') or {}
            name = f.get('name')
            if not name or name in hidden or name in OWN:
                continue
            out.append({'name': name, 'description': str(f.get('description') or '')[:200],
                        'module': module, 'plugin': plugin, 'emoji': info.get('emoji') or '',
                        'scoped': plugin in scoped, 'exposed': name in on and plugin not in scoped, 'own': False})
    return out


def exposed(system):
    """The tools this door offers right now, in MCP's own shape:
    [{name, description, inputSchema}]. Own two first, then the ticked ones
    that are still here and not scoped."""
    tools = [dict(_TOOLS[n]) for n in OWN]
    fm = getattr(getattr(system, 'llm_chat', None), 'function_manager', None)
    if fm is None:
        return tools
    scoped, hidden, on = _scoped_plugins(), fm.get_hidden_functions(), set(ticked())
    seen = set(OWN)
    for info in fm.function_modules.values():
        if (info.get('_plugin') or 'core') in scoped:
            continue
        for tool in info.get('tools') or []:
            f = tool.get('function') or {}
            name = f.get('name')
            if name in on and name not in hidden and name not in seen:
                seen.add(name)
                tools.append({'name': name, 'description': str(f.get('description') or ''),
                              'inputSchema': f.get('parameters') or {'type': 'object', 'properties': {}}})
    return tools


# --- the two of its own --------------------------------------------------------

def _chat_ready(system, chat):
    """The chat's name as kept, made if it did not exist. ValueError when the
    name cannot be a chat."""
    from core.chat.history import sanitize_chat_name
    sm = system.llm_chat.session_manager
    name = sanitize_chat_name(str(chat or '').strip())
    if not name:
        raise ValueError('The chat needs a name.')
    if sm.get_settings_for(name) is None:
        sm.create_chat(name)
        logger.info(f"[MCP] made the chat '{name}' for a client")
        if sm.get_settings_for(name) is None:
            raise ValueError(f"The chat '{name}' could not be made.")
    return name


def _line(who, where):
    """The one line she reads above an ask or tell: who, from where, and that
    her message IS the answer (Krem's words: no tool is needed to reply)."""
    who = ' '.join(str(who or 'an MCP client').replace('[', '(').replace(']', ')').split())[:80]
    where = ' '.join(str(where or '').split())[:60]
    return (f"[This is from {who}" + (f" at {where}" if where else '')
            + ". Answer in your message; no tool is needed to reply.]")


def _turn(system, chat, text, who, where):
    """One turn in `chat`, on this thread, under the answering flag. Returns
    her reply. Raises on a chat that stays busy or a turn that fails."""
    from core import cadence
    from core.chat.chat import ChatBusy
    name = _chat_ready(system, chat)
    body = f"{_line(who, where)}\n{str(text)[:TEXT_MAX]}"
    give_up = time.monotonic() + BUSY_WAIT
    token = answering.set(True)
    try:
        while True:
            try:
                return name, cadence.run_turn(name, body, source=f"mcp:{where or 'client'}")
            except ChatBusy:
                if time.monotonic() >= give_up:
                    raise RuntimeError(f"The chat '{name}' stayed mid-turn for {BUSY_WAIT}s.")
                time.sleep(1)
    finally:
        answering.reset(token)


def ask(system, args, where=''):
    name, reply = _turn(system, args.get('chat'), args.get('text'), args.get('from'), where)
    logger.info(f"[MCP] ask in '{name}' from {where or 'a client'}: {len(reply or '')} chars back")
    return reply or '(she said nothing)', True


def tell(system, args, where=''):
    name = _chat_ready(system, args.get('chat'))
    text, who = args.get('text'), args.get('from')

    def go():
        try:
            _turn(system, name, text, who, where)
        except Exception as e:
            logger.warning(f"[MCP] tell in '{name}' failed: {e}")
    threading.Thread(target=go, daemon=True, name='mcp-tell').start()
    return f"Told her, in the chat '{name}'.", True


# --- a call -----------------------------------------------------------------

def call(system, name, args, where=''):
    """Run one tool. (text, ok). Never raises for a tool's own failure."""
    args = args if isinstance(args, dict) else {}
    if name == 'ask':
        return ask(system, args, where)
    if name == 'tell':
        return tell(system, args, where)
    allowed = {t['name'] for t in exposed(system)}
    if name not in allowed:
        return f"There is no tool named '{name}' on this door.", False
    fm = system.llm_chat.function_manager
    result, ok = fm.execute_function(name, args, allowed_tools=allowed, with_success=True)
    if isinstance(result, dict):               # a picture-taking tool: its words only
        result = result.get('text') or str(result)
    return str(result if result is not None else ''), bool(ok)


# --- JSON-RPC ---------------------------------------------------------------

def _error(ident, code, text):
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': text}}


def _result(ident, value):
    return {'jsonrpc': '2.0', 'id': ident, 'result': value}


def handle(system, message, where=''):
    """One JSON-RPC message in, one out - or None for a notification, which
    gets no answer. `where` is the client's address, for her line."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return _error(message.get('id') if isinstance(message, dict) else None, -32600, 'Not a JSON-RPC 2.0 request.')
    method, ident, params = message['method'], message.get('id'), message.get('params') or {}
    if not isinstance(params, dict):
        params = {}
    if method.startswith('notifications/'):
        return None
    if ident is None:
        return None                          # a request without an id is a notification too
    if method == 'initialize':
        return _result(ident, {
            'protocolVersion': PROTOCOL,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'Sapphire', 'version': _version()},
            'instructions': "Sapphire, a local AI. `ask` her something in a named chat and get her "
                            "answer; `tell` her without waiting. Other tools are the ones her user chose to share.",
        })
    if method == 'ping':
        return _result(ident, {})
    if method == 'tools/list':
        return _result(ident, {'tools': exposed(system)})
    if method == 'tools/call':
        name = params.get('name')
        if not isinstance(name, str) or not name:
            return _error(ident, -32602, 'tools/call needs a name.')
        try:
            text, ok = call(system, name, params.get('arguments') or {}, where)
        except (ValueError, RuntimeError) as e:          # a reason in words: a busy chat, a turn that failed
            text, ok = str(e), False
        except Exception as e:
            logger.error(f"[MCP] {name} failed: {e}", exc_info=True)
            text, ok = f"{name} failed: {type(e).__name__}", False
        return _result(ident, {'content': [{'type': 'text', 'text': text}], 'isError': not ok})
    return _error(ident, -32601, f"Method not found: {method}")


def handle_body(system, body, where=''):
    """A request body, parsed: one message or a batch. Returns what to send
    back, or None when nothing is owed (notifications only)."""
    if isinstance(body, list):
        answers = [a for a in (handle(system, m, where) for m in body) if a is not None]
        return answers or None
    return handle(system, body, where)
