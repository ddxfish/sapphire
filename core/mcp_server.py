# core/mcp_server.py - what Sapphire offers over MCP (tmp/device-manager-upgrade-plan.md §9)
#
# The Model Context Protocol, served: a client with one of her API tokens can
# list and call the tools the user ticked under Settings > MCP Server, plus
# the tools of this door's own - `ask` and `tell`, a conversation in a named
# chat, and `room_invite` while the user takes chat-room invitations
# (core/room_guest.py). Another Sapphire is the first client (the "Another
# Sapphire" device); Claude Code is the second. A token that speaks as a persona also gets that
# persona's voice and memory here (core/mcp_persona.py).
#
# The shape is the smallest the protocol allows: Streamable HTTP, one POST,
# JSON answers (no event stream - nothing here needs to push), no session.
# Tools only: no resources, no prompts. The route is core/routes/mcp.py; this
# file is the protocol and the tools, so a test needs no HTTP.
import json
import logging
from contextvars import ContextVar
from pathlib import Path

import config

logger = logging.getLogger(__name__)

PROTOCOL = '2025-06-18'        # the version this door speaks; a client's older date is answered with ours
ASK_WAIT = 300                 # seconds an ask waits in the chat's inbox for its turn and her answer
TEXT_MAX = 8000                # characters of one ask or tell
OWN = ('ask', 'tell')          # this door's own tools, always offered
INVITE = 'room_invite'         # the third, offered while Settings > MCP Server takes room invitations
RESET = 'room_reset'           # the fourth, with it: the host's word that a room she sits in was cleared or closed
UPDATE = 'room_update'         # the fifth, with them: the host's word that a room she sits in changed (purpose, people, her brief)
SEAT = ('room_read', 'room_wait', 'room_post')   # a persona key's seat AT a chat room's door here (core/rooms.py DOOR): offered to every persona key

# True while a turn that came in through `ask` or `tell` runs: a tool that
# would ask another Sapphire checks it and says "answer in your message"
# instead, so two Sapphires can never ask each other round and round.
answering = ContextVar('mcp_answering', default=False)

# Who runs a ticked tool from this door: the persona its token speaks as, or
# 'client' for a plain token. None means her own turn. A tool that keeps state
# per caller (the browser plugin's lanes) reads it.
client = ContextVar('mcp_client', default=None)

_TOOLS = {
    'ask': {
        'name': 'ask',
        'description': "Ask Sapphire something in one of her chats and get her answer. The chat's own "
                       "persona answers, so a chat set to another persona is how you reach that one. "
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
    INVITE: {
        'name': INVITE,
        'description': "Invite this Sapphire into a chat room on your side (core/rooms.py). She decides in one "
                       "turn of her own and, if she joins, names the chat here to cue her in with `ask` on her "
                       "turns. Returns JSON: {joined, chat, name, said}. Takes minutes.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'room': {'type': 'string', 'description': "The room's name over there."},
                'host': {'type': 'string', 'description': 'Who invites: the host Sapphire, as she should see it.'},
                'purpose': {'type': 'string', 'description': 'What the room is for (every bot there reads it).'},
                'people': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Who is in the room.'},
                'instructions': {'type': 'string', 'description': 'What the host asks of her in that room.'},
                'rules': {'type': 'string', 'description': "The room's rules in words (turns, caps, mentions, passing)."},
                'private': {'type': 'boolean', 'description': 'Whether the host marked the room private.'},
                'name': {'type': 'string', 'description': 'What the host would call her there.'},
            },
            'required': ['room'],
        },
    },
    RESET: {
        'name': RESET,
        'description': "The host's word that a chat room she sits in was cleared (closed=false) or closed (true). "
                       "Her chat for that room here is kept whole under a dated, archived name; unless closed, a "
                       "fresh chat of the same name starts for the next cue. Nothing is wiped, no turn runs.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'room': {'type': 'string', 'description': "The room's name over there."},
                'host': {'type': 'string', 'description': 'The host Sapphire, as she sees it (the invitation\'s host).'},
                'closed': {'type': 'boolean', 'description': 'True when the room was deleted, not cleared.'},
            },
            'required': ['room', 'host'],
        },
    },
    UPDATE: {
        'name': UPDATE,
        'description': "The host's word that a chat room she sits in changed: its purpose, who is there, or what the "
                       "host asks of her. The room block in her chat for that room is swapped for the new words; "
                       "nothing else of hers changes, no turn runs.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'room': {'type': 'string', 'description': "The room's name over there."},
                'host': {'type': 'string', 'description': 'The host Sapphire, as she sees it (the invitation\'s host).'},
                'purpose': {'type': 'string', 'description': 'What the room is for now.'},
                'people': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Who is in the room now.'},
                'instructions': {'type': 'string', 'description': 'What the host asks of her there now.'},
                'rules': {'type': 'string', 'description': "The room's rules in words, as they stand now."},
            },
            'required': ['room', 'host'],
        },
    },
    'room_read': {
        'name': 'room_read',
        'description': "A chat room here where your persona has a seat at the door: the room, who is there, what the "
                       "host asks of you, and its newest lines (words only). No room named: the rooms where you have "
                       "such a seat.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'room': {'type': 'string', 'description': "The room's name."},
                'limit': {'type': 'integer', 'description': 'How many of the newest lines (default 40).'},
            },
        },
    },
    'room_wait': {
        'name': 'room_wait',
        'description': "Wait at the door of a chat room here for your turn (up to 120 s). Your turn: the lines since "
                       "your last one, the pictures on them, who is there; answer with room_post within 120 s or you "
                       "are skipped. No turn in that time: {turn: false}, so wait again. The room's bots take turns "
                       "after each line of the person's; you are one seat among them.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'room': {'type': 'string', 'description': "The room's name."},
                'seconds': {'type': 'integer', 'description': 'How long to wait, 1-120 (default 120).'},
            },
            'required': ['room'],
        },
    },
    'room_post': {
        'name': 'room_post',
        'description': "Your line for the turn room_wait handed you: as yourself, in your own words, never a line for "
                       "anyone else, never your own name first. Empty, or with <<PASS>> in it, you pass this turn. "
                       "Out of turn, use `tell` with the room as the chat: it lands as you too.",
        'inputSchema': {
            'type': 'object',
            'properties': {
                'room': {'type': 'string', 'description': "The room's name."},
                'text': {'type': 'string', 'description': 'Your line.'},
            },
            'required': ['room', 'text'],
        },
    },
}


def own():
    """This door's own tools right now."""
    from core import room_guest
    return OWN + ((INVITE, RESET, UPDATE) if room_guest.enabled() else ())


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
            'emoji': '', 'scoped': False, 'exposed': True, 'own': True} for n in own()]
    if fm is None:
        return out
    scoped, hidden, on = _scoped_plugins(), fm.get_hidden_functions(), set(ticked())
    for module, info in fm.function_modules.items():
        plugin = info.get('_plugin') or 'core'
        for tool in info.get('tools') or []:
            f = tool.get('function') or {}
            name = f.get('name')
            if not name or name in hidden or name in _TOOLS:
                continue
            out.append({'name': name, 'description': str(f.get('description') or '')[:200],
                        'module': module, 'plugin': plugin, 'emoji': info.get('emoji') or '',
                        'scoped': plugin in scoped, 'exposed': name in on and plugin not in scoped, 'own': False})
    return out


def exposed(system, persona=None):
    """The tools this door offers right now, in MCP's own shape:
    [{name, description, inputSchema}]. Own two first, then what the caller's
    persona gives it, then the ticked ones that are still here and not scoped."""
    from core import mcp_persona
    tools = [dict(_TOOLS[n]) for n in own()] + mcp_persona.offered(persona) + seat_tools(persona)
    fm = getattr(getattr(system, 'llm_chat', None), 'function_manager', None)
    if fm is None:
        return tools
    scoped, hidden, on = _scoped_plugins(), fm.get_hidden_functions(), set(ticked())
    seen = set(_TOOLS) | mcp_persona.NAMES
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


def seat_tools(persona):
    """The seat tools, for a key that speaks as a persona: her seat at a room's
    door is found by that name (core/rooms.py door_*)."""
    from core import mcp_persona
    return [dict(_TOOLS[n]) for n in SEAT] if mcp_persona.settings(persona) is not None else []


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


def _private(system, name):
    """True when `name` is a private chat (or unreadable - fail closed). A
    private chat's words stay on this machine: an ask's reply would leave over
    the network to whoever holds the token (Krem's ruling 2026-10-06)."""
    try:
        s = system.llm_chat.session_manager.get_settings_for(name)
    except Exception:
        return True
    return s is None or bool(s.get('private_chat'))


def _item(system, chat, text, who, where):
    """(chat name, her line above the text, the text). ValueError for a name
    that cannot be a chat, and for a private chat. Line and text travel apart
    (2026-10-11): a chat's wire text is the inbox's header + text, as before;
    a room (core/rooms.py) keeps the words alone as a line from `who`."""
    name = _chat_ready(system, chat)
    if _private(system, name):
        raise ValueError(f"The chat '{name}' is private; it does not take messages over MCP.")
    return name, _line(who, where), str(text)[:TEXT_MAX]


def share_tools():
    """Whether an ask's answer also names the tools she used (names only; her
    inputs and results never leave): Settings > MCP Server > Chat rooms. A
    room host shows the names on her line over there."""
    import config
    return bool(getattr(config, 'MCP_ROOM_SHARE_TOOLS', False))


def ask(system, args, where='', persona=None):
    """Her answer, waited for. The turn waits its turn in the chat's inbox
    (core/chat/inbox.py) and runs under the `answering` flag - the inbox runs
    an item in the context it was put from, so the loop-breaker holds on the
    drainer thread. A busy chat answers late instead of erroring."""
    from core.chat import inbox
    # who is asking: what the client says, else the persona its key speaks as
    who = args.get('from') or persona
    name, head, body = _item(system, args.get('chat'), args.get('text'), who, where)
    report = {}                    # the turn's report (cadence.run_turn): why she said nothing, if she did

    def seen(ev):
        if isinstance(ev, dict) and ev.get('type') == 'turn_report':
            report.update(ev)
    token = answering.set(True)
    try:
        try:
            # gate: asked again the moment the turn is about to run - the chat may
            # have turned private while the ask waited in line, and the reply
            # would leave over the network (2026-10-07)
            reply = inbox.ask(name, body, source=f"mcp:{where or 'client'}", header_line=head,
                              who=str(who or ''), timeout=ASK_WAIT, on_event=seen,
                              gate=lambda: f"The chat '{name}' is private now." if _private(system, name) else '')
        except inbox.InboxRefused as e:
            raise ValueError(str(e))
        except TimeoutError:
            raise RuntimeError(f"The chat '{name}' did not get to this question in {ASK_WAIT}s.")
    finally:
        answering.reset(token)
    logger.info(f"[MCP] ask in '{name}' from {where or 'a client'}: {len(reply or '')} chars back")
    tools = [str(t)[:80] for t in (report.get('tools') or []) if t]
    extra = {'tools': tools} if tools and share_tools() else None      # structuredContent, when she shares
    if reply:
        return reply, True, extra
    # no words: say why, so the asker (a room host across the network) is not left
    # guessing between a pass, a think loop and a token ceiling (2026-10-12)
    why = str(report.get('why') or '').strip()
    return (f'(she said nothing: {why})' if why else '(she said nothing)'), True, extra


def tell(system, args, where='', persona=None):
    """Said to her, not waited for: the item joins the chat's inbox and runs
    when the chat is free. Not foldable with other items - it carries the
    `answering` flag in its context."""
    from core.chat import inbox
    who = args.get('from') or persona
    name, head, body = _item(system, args.get('chat'), args.get('text'), who, where)
    token = answering.set(True)
    try:
        inbox.tell(name, body, source=f"mcp:{where or 'client'}", header_line=head,
                   who=str(who or ''), coalesce=False,
                   gate=lambda: f"The chat '{name}' is private now." if _private(system, name) else '')
    except inbox.InboxRefused as e:
        raise ValueError(str(e))
    finally:
        answering.reset(token)
    return f"Told her, in the chat '{name}'.", True


def room_invite(system, args, where='', persona=None):
    """Another Sapphire's invitation into her chat room: this one decides in a
    turn of her own (core/room_guest.py) under the `answering` flag - she
    may not ask a Sapphire back from it. (JSON text, ok); a reason in words
    rides ValueError/RuntimeError like ask's."""
    from core import room_guest
    token = answering.set(True)
    try:
        return room_guest.invite(system, args, where)
    finally:
        answering.reset(token)


def _seat(name, args, persona):
    """A persona key's seat at a room's door (core/rooms.py). JSON text back; a
    cue's pictures ride as the protocol's image blocks (extra['images'])."""
    from core import mcp_persona, rooms
    if mcp_persona.settings(persona) is None:
        return (f"'{name}' needs an API token that speaks as a persona (Settings > System > API Keys): "
                "its seat at a room's door is found by that name."), False
    room = args.get('room')
    try:
        if name == 'room_post':
            return rooms.door_post(persona, room, args.get('text')), True
        if name == 'room_wait':
            out = rooms.door_wait(persona, room, args.get('seconds'))
        else:
            out = rooms.door_read(persona, room, args.get('limit') or 40) if room else {'rooms': rooms.door_rooms(persona)}
    except rooms.RoomError as e:
        raise ValueError(str(e))
    images = out.pop('images', None) or []
    return json.dumps(out, ensure_ascii=False), True, ({'images': images} if images else None)


# --- a call -----------------------------------------------------------------

def call(system, name, args, where='', persona=None):
    """Run one tool. (text, ok). Never raises for a tool's own failure.
    `persona` is the one the caller's token speaks as, or None."""
    args = args if isinstance(args, dict) else {}
    if name == 'ask':
        return ask(system, args, where, persona)
    if name == 'tell':
        return tell(system, args, where, persona)
    if name in (INVITE, RESET, UPDATE):
        if name not in own():
            return 'This Sapphire does not take chat-room invitations (Settings > MCP Server, over there).', False
        if name == RESET:
            from core import room_guest
            return room_guest.reset(system, args, where)
        if name == UPDATE:
            from core import room_guest
            return room_guest.update(system, args, where)
        return room_invite(system, args, where, persona)
    if name in SEAT:
        return _seat(name, args, persona)
    from core import mcp_persona
    if name in mcp_persona.NAMES:
        return mcp_persona.call(system, persona, name, args)
    allowed = {t['name'] for t in exposed(system)}
    if name not in allowed:
        return f"There is no tool named '{name}' on this door.", False
    fm = system.llm_chat.function_manager
    token = client.set(persona or 'client')
    try:
        result, ok = fm.execute_function(name, args, allowed_tools=allowed, with_success=True)
    finally:
        client.reset(token)
    if isinstance(result, dict):               # a picture-taking tool: its words, its pictures as image blocks
        images = [p for p in result.get('images') or [] if isinstance(p, dict) and p.get('data')]
        return str(result.get('text') or ''), bool(ok), ({'images': images} if images else None)
    return str(result if result is not None else ''), bool(ok)


# --- JSON-RPC ---------------------------------------------------------------

def _error(ident, code, text):
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': text}}


def _result(ident, value):
    return {'jsonrpc': '2.0', 'id': ident, 'result': value}


def _persona_line(persona):
    from core import mcp_persona
    line = mcp_persona.about(persona)
    if line:
        line += (" In a chat room here where this persona has a seat at the door, `room_wait` waits for your turn, "
                 "`room_post` is your line, and `room_read` is the room so far (no room named: your rooms).")
    return line


def handle(system, message, where='', persona=None):
    """One JSON-RPC message in, one out - or None for a notification, which
    gets no answer. `where` is the client's address, for her line; `persona`
    is the one the caller's token speaks as, or None."""
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
                            "answer; `tell` her without waiting. Other tools are the ones her user chose to share."
                            + _persona_line(persona),
        })
    if method == 'ping':
        return _result(ident, {})
    if method == 'tools/list':
        return _result(ident, {'tools': exposed(system, persona)})
    if method == 'tools/call':
        name = params.get('name')
        if not isinstance(name, str) or not name:
            return _error(ident, -32602, 'tools/call needs a name.')
        try:
            out = call(system, name, params.get('arguments') or {}, where, persona)
            text, ok, extra = out[0], out[1], (out[2] if len(out) > 2 else None)
        except (ValueError, RuntimeError) as e:          # a reason in words: a busy chat, a turn that failed
            text, ok, extra = str(e), False, None
        except Exception as e:
            logger.error(f"[MCP] {name} failed: {e}", exc_info=True)
            text, ok, extra = f"{name} failed: {type(e).__name__}", False, None
        value = {'content': [{'type': 'text', 'text': text}], 'isError': not ok}
        if isinstance(extra, dict):
            for pic in extra.pop('images', None) or []:   # a room cue's pictures, as the protocol's image blocks
                value['content'].append({'type': 'image', 'data': pic.get('data'),
                                         'mimeType': pic.get('media_type') or 'image/jpeg'})
        if extra:
            value['structuredContent'] = extra        # an ask's tool names, when she shares them (share_tools)
        return _result(ident, value)
    return _error(ident, -32601, f"Method not found: {method}")


def handle_body(system, body, where='', persona=None):
    """A request body, parsed: one message or a batch. Returns what to send
    back, or None when nothing is owed (notifications only)."""
    if isinstance(body, list):
        answers = [a for a in (handle(system, m, where, persona) for m in body) if a is not None]
        return answers or None
    return handle(system, body, where, persona)
