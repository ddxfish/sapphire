# core/room_guest.py - this Sapphire as a GUEST in another Sapphire's chat room
#
# The host (core/rooms.py, over there) invites through this box's MCP door:
# `room_invite` (core/mcp_server.py). She decides in ONE turn of her own,
# with her memory behind her, and joins by calling the chatroom_join tool
# (functions/chatroom.py), which makes the room's chat HERE, dressed as she
# chose: a persona, a provider and model, a toolset, a name, a few lines she
# writes for herself. What she leaves out comes from the user's defaults
# under Settings > MCP Server. From then on the host cues her with `ask` in
# that chat and her answers are her lines; her box runs every turn with her
# own prompt, model and tools, the host sets none of it.
#
# The turn she decides in: the lobby chat the user named (MCP_ROOM_LOBBY,
# made if missing - its persona says how to decide, its toolset adds what
# she may look at; chatroom_join is always in reach), or, with no name, an
# ephemeral turn as her default self that persists nothing. She cannot make
# a room chat private (Krem 2026-10-11): the host reads every word anyway.
import json
import logging
import socket
import threading
from contextvars import ContextVar
from datetime import datetime

import config

logger = logging.getLogger(__name__)

INVITE_WAIT = 300           # seconds the lobby turn may take (the host waits as long)
TOOL = 'chatroom_join'
TAG = 'room_guest'          # a room chat's settings key: {host, room, where, since}
SCRATCH = '__room-invite__'  # the stream gate's name for an ephemeral lobby turn (no chat behind it)
PASS = '(pass)'
TEXT_CAP = 2000

current = ContextVar('room_invite', default=None)   # the open invitation, during the lobby turn only


def enabled():
    return bool(getattr(config, 'MCP_ROOM_INVITES', False))


def lobby():
    return str(getattr(config, 'MCP_ROOM_LOBBY', '') or '').strip()


def default_persona():
    return str(getattr(config, 'MCP_ROOM_PERSONA', '') or '').strip()


def _system():
    from core.api_fastapi import get_system
    return get_system()


def _me():
    return str(getattr(config, 'INSTANCE_NAME', '') or '').strip() or socket.gethostname()


def _clean(text, cap=TEXT_CAP):
    return ' '.join(str(text or '').replace('[', '(').replace(']', ')').split())[:cap]


# --- the invitation ---------------------------------------------------------------

def _invitation(args, where):
    args = args if isinstance(args, dict) else {}
    people = args.get('people')
    if isinstance(people, str):
        people = [p for p in people.split(',')]
    people = [_clean(p, 80) for p in (people if isinstance(people, list) else []) if _clean(p, 80)][:30]
    room = _clean(args.get('room'), 80)
    if not room:
        raise ValueError('An invitation names its room.')
    return {'room': room, 'host': _clean(args.get('host'), 80) or 'another Sapphire', 'where': _clean(where, 60),
            'purpose': _clean(args.get('purpose')), 'people': people,
            'instructions': _clean(args.get('instructions'), 4000), 'private': args.get('private') is True,
            'name': _clean(args.get('name'), 80), 'decision': None}


def _options():
    """What she may pick from, for the letter: her providers, personas, toolsets."""
    from core.chat.llm_providers.resolve import providers_config, display_name, is_local
    from core.personas import persona_manager
    from core import toolsets
    providers = []
    for key, conf in providers_config().items():
        try:
            providers.append(f"{key} ({display_name(key, conf)}, {'local' if is_local(key, conf) else 'cloud'})")
        except Exception:
            providers.append(str(key))
    return providers, list(persona_manager.get_names()), list(toolsets.get_toolset_names())


def letter(inv):
    """What she reads: the invitation and how to answer it."""
    providers, personas, sets = _options()
    lines = [f"[A chat-room invitation from {inv['host']}" + (f" at {inv['where']}" if inv['where'] else '')
             + ". Decide in this turn.]",
             f'Room: "{inv["room"]}". Purpose: {(inv["purpose"] or "(none given)").rstrip(".")}.'
             + (f" Here: {', '.join(inv['people'])}." if inv['people'] else '')
             + (" The host keeps that room off the network on their side; your lines still go to the host."
                if inv['private'] else '')]
    if inv['instructions']:
        lines.append(f"What they ask of you there: {inv['instructions']}")
    lines.append(f"To join, call {TOOL}. It takes what you bring: a persona, a provider and model, a toolset, "
                 "a name for yourself there, and instructions you write for yourself. Leave out what you do not "
                 "need and your defaults are used. To decline, say so in words and call nothing.")
    lines.append(f"Your providers: {', '.join(providers) or 'none'}. Your personas: {', '.join(personas) or 'none'}. "
                 f"Your toolsets: {', '.join(sets) or 'none'}.")
    return '\n'.join(lines)


def invite(system, args, where=''):
    """The door tool: her decision, waited for. (JSON text, ok). ValueError
    and RuntimeError carry a reason in words for the host."""
    if not enabled():
        raise ValueError('This Sapphire does not take chat-room invitations (Settings > MCP Server, over there).')
    inv = _invitation(args, where)
    token = current.set(inv)
    try:
        said = decide(system, inv, letter(inv))
    finally:
        current.reset(token)
    d = inv.get('decision') or {}
    if d.get('joined'):
        out = {'joined': True, 'chat': d['chat'], 'name': d['name'], 'said': said}
    else:
        out = {'joined': False, 'said': said or d.get('reason') or ''}
    logger.info(f"[ROOM GUEST] invited to '{inv['room']}' by {inv['host']}: {'joined' if out['joined'] else 'declined'}")
    return json.dumps(out), True


# --- the lobby turn -------------------------------------------------------------------

def decide(system, inv, text):
    """Run the turn she decides in; her words back. The lobby chat when the
    user named one (through its inbox: a turn there waits its turn like any
    other), else an ephemeral turn as her default self."""
    from core.chat import inbox
    from core.mcp_server import _private
    name = lobby()
    if not name:
        brain = ephemeral_brain(system)
        if brain['settings'].get('private_chat'):
            _unavailable('her defaults are private')
        return _turn(system, None, text, brain)
    chat = _lobby_chat(system, name)
    # A private lobby's words stay on this machine (the door's ruling for ask,
    # 2026-10-06): checked now, and again the moment the turn is about to run.
    if _private(system, chat):
        _unavailable(f"the lobby chat '{chat}' is private")
    try:
        return inbox.turn(chat, lambda: _turn(system, chat, text, chat_brain(system, chat)),
                          source=f"mcp:{inv['where'] or 'client'}", lane='later', timeout=INVITE_WAIT,
                          gate=lambda: 'the lobby chat is private now' if _private(system, chat) else '')
    except inbox.InboxRefused as e:
        _unavailable(str(e))
    except TimeoutError:
        raise RuntimeError(f"She did not get to the invitation in {INVITE_WAIT}s.")


def _unavailable(why):
    """One plain sentence for the host; the reason, which names her chats
    and their state, stays in her log."""
    logger.info(f"[ROOM GUEST] an invitation was not decided: {why}")
    raise ValueError('Her lobby is not available right now.')


def _lobby_chat(system, name):
    from core.chat.history import sanitize_chat_name
    sm = system.llm_chat.session_manager
    safe = sanitize_chat_name(name)
    if not safe:
        raise ValueError(f"'{name}' cannot be a chat name (Settings > MCP Server).")
    if sm.get_settings_for(safe) is None:
        sm.create_chat(safe)
        logger.info(f"[ROOM GUEST] made the lobby chat '{safe}'")
        if sm.get_settings_for(safe) is None:
            _unavailable(f"the lobby chat '{safe}' could not be made")
    return safe


def _dress(system, sess):
    """A stream-brain carrier with its prompt and tools filled: the chat's (or
    the defaults') toolset, and chatroom_join whatever the toolset says."""
    from core import prompts
    st = sess['settings']
    pd = prompts.get_prompt(st.get('prompt') or 'default')
    if not isinstance(pd, dict):
        pd = prompts.get_prompt('default')
    sess['system_prompt'] = (pd.get('content', '') if isinstance(pd, dict) else '') or ''
    fm = system.llm_chat.function_manager
    sess['tools'] = fm.resolve_tools(st.get('toolset') or 'none', st.get('extra_toolsets'), add_tools=[TOOL])
    return sess


def chat_brain(system, chat):
    sess = system.llm_chat.session_manager.make_stream_session(chat)
    if sess is None:
        _unavailable(f"the lobby chat '{chat}' cannot be read right now (missing, sealed, or unreadable rows)")
    return _dress(system, sess)


def ephemeral_brain(system):
    from core.chat.history import get_user_defaults
    return _dress(system, system.llm_chat.session_manager.make_ephemeral_override(get_user_defaults()))


def _turn(system, chat, text, brain):
    """One turn through THE engine on a caller-built brain, her prose back.
    ChatBusy from begin_stream rides up (the inbox tries again; the
    ephemeral lane has no inbox and answers in words)."""
    from core.chat.chat import ChatBusy
    from core.cadence import visible_text
    llm = system.llm_chat
    try:
        stream, sid, name = llm.begin_stream(chat or SCRATCH, exclusive=True)
    except ChatBusy:
        if chat:
            raise
        raise RuntimeError('She is deciding on another invitation right now; ask again in a minute.')
    stream.brain = brain
    stream.suppress_tts = True
    parts, blocks, errored, cancelled, gen = [], [], None, False, None
    timer = threading.Timer(INVITE_WAIT, lambda: setattr(stream, 'cancel_flag', True))
    timer.daemon = True
    try:
        timer.start()
        gen = stream.chat_stream(text)
        for ev in gen:
            if not isinstance(ev, dict):
                continue
            et = ev.get('type')
            if et == 'content':
                parts.append(ev.get('text') or '')
            elif et == 'tool_start':
                if visible_text(''.join(parts)):
                    blocks.append(''.join(parts))
                parts = []
            elif et == 'final':
                cancelled = bool(ev.get('cancelled'))
                if ev.get('error'):
                    errored = ev.get('text') or 'turn refused'
                    parts, blocks = [], []
                elif ev.get('text'):
                    parts = [ev['text']]
            elif et == 'error':
                errored = ev.get('text') or 'stream error'
    except Exception as e:
        errored = errored or f"{type(e).__name__}: {e}"
    finally:
        timer.cancel()
        if gen is not None:
            try:
                gen.close()
            except Exception as e:
                logger.warning(f"[ROOM GUEST] engine close failed: {e}")
        llm.end_stream(sid, name)
    if errored:
        raise RuntimeError(errored)
    if cancelled:
        raise RuntimeError('Her turn was cut off before she decided.')
    rounds = [visible_text(b) for b in blocks + [''.join(parts)]]
    return '\n\n'.join(r for r in rounds if r).strip()


# --- chatroom_join -------------------------------------------------------------------------

def _preamble(inv, shown, mine):
    lines = [f'[Chat room "{inv["room"]}" on {inv["host"]}]',
             'Several speakers share that conversation and take turns. The host sends you the lines since '
             'your last turn; your answer is your line there.']
    if inv['purpose']:
        lines.append(f"What this room is for: {inv['purpose']}")
    lines.append(f"You are {shown}." + (f" Also here: {', '.join(inv['people'])}." if inv['people'] else ''))
    if inv['instructions']:
        lines.append(f"What the host asks of you there: {inv['instructions']}")
    lines.append('Other speakers\' lines reach you as "Name: text". Speak only as yourself, in your own words; '
                 'never write lines for anyone else, and do not start your line with your own name. '
                 f'If you have nothing to add this turn, answer exactly {PASS}.')
    if mine:
        lines.append(mine)
    return '\n'.join(lines)


def _private_prompt(name):
    from core import prompts
    pd = prompts.get_prompt(name or 'default')
    return isinstance(pd, dict) and bool(pd.get('privacy_required'))


def _dressing(args, notes):
    """The room chat's COMPLETE settings: the user's chat defaults, then the
    room persona, then her choices; what could not be honoured goes to
    `notes` and the layer below stands. Complete, so a rejoin writes every
    key (a merge kept last time's model). A persona whose prompt is private
    is never worn here: the chat is public by rule, every turn would be
    refused. With no room persona and no toolset of her choosing the chat
    carries NO tools (Krem 2026-10-11): the cues are text from strangers at
    the host's table, and her whole default toolkit was behind them."""
    from core.personas import persona_manager
    from core.chat.llm_providers.resolve import normalize_pair, providers_config
    from core.chat.history import get_user_defaults
    from core import toolsets
    st = {k: v for k, v in get_user_defaults().items() if k != 'private_chat'}
    pick = str(args.get('persona') or '').strip()
    dressed = False
    for name, hers in ((default_persona(), False), (pick, True)):
        if not name:
            continue
        p = persona_manager.get(name)
        if not p:
            notes.append(f"there is no persona named '{name}', so the default is used")
            continue
        if _private_prompt((p.get('settings') or {}).get('prompt')):
            notes.append(f"the persona '{name}' wears a private prompt, which a room chat cannot, so the default is used")
            continue
        st.update(dict(p.get('settings') or {}))
        st['persona'] = name
        dressed = True
    provider = str(args.get('provider') or '').strip()
    model = str(args.get('model') or '').strip()
    if provider:
        if provider == 'auto' or provider in providers_config():
            st['llm_primary'], st['llm_model'] = normalize_pair(provider, model)
        else:
            notes.append(f"there is no provider '{provider}', so the default model is used")
    elif model:
        notes.append('a model needs its provider, so the default model is used')
    ts = str(args.get('toolset') or '').strip()
    if ts and toolsets.toolset_exists(ts):
        st['toolset'] = ts
    else:
        if ts:
            notes.append(f"there is no toolset named '{ts}', so the default is used")
        if not dressed:
            st['toolset'] = 'none'               # nobody chose tools for this chat: it has none
            st.pop('extra_toolsets', None)
    st.pop('private_chat', None)                 # never: a room's words are the host's too
    return st


def _room_chat_name(sm, inv):
    """room-<host>-<room>; the same room from the same host reuses its chat
    (she rejoins with what she said there), another chat of that name is
    stepped around."""
    from core.chat.history import sanitize_chat_name
    base = sanitize_chat_name(f"room-{inv['host']}-{inv['room']}") or 'room-guest'
    name, n = base, 1
    while True:
        s = sm.get_settings_for(name)
        if s is None:
            return name, False
        tag = s.get(TAG) if isinstance(s.get(TAG), dict) else {}
        if tag.get('host') == inv['host'] and tag.get('room') == inv['room']:
            return name, True
        n += 1
        name = f"{base}-{n}"


def join(args):
    """The tool's body. (text for her, ok)."""
    inv = current.get()
    if not inv:
        return 'No chat-room invitation is open in this turn. This tool answers one when it arrives.', False
    if inv.get('decision'):
        return 'You already answered this invitation.', False
    args = args if isinstance(args, dict) else {}
    accept = args.get('accept')
    if isinstance(accept, str):
        accept = accept.strip().lower() in ('true', 'yes', '1')
    if not accept:
        reason = _clean(args.get('reason'), 500)
        inv['decision'] = {'joined': False, 'reason': reason}
        return f"Declined. The host is told: {reason.rstrip('.') or 'no reason given'}.", True
    sm = _system().llm_chat.session_manager
    notes = []
    st = _dressing(args, notes)
    shown = _clean(args.get('name'), 80) or inv['name'] or _me()
    mine = _clean(args.get('instructions'), 2000)
    own = str(st.get('custom_context') or '').strip()
    st['custom_context'] = '\n\n'.join(t for t in (own, _preamble(inv, shown, mine)) if t)
    st[TAG] = {'host': inv['host'], 'room': inv['room'], 'where': inv['where'],
               'since': datetime.now().isoformat(timespec='seconds')}
    name, again = _room_chat_name(sm, inv)
    if again and bool((sm.get_settings_for(name) or {}).get('private_chat')):
        inv['decision'] = {'joined': False, 'reason': 'the chat for that room here is private now'}
        return (f"Declined: the chat for that room here, '{name}', has been made private, and a room's chat "
                "cannot be. The host is told.", True)
    ok = sm.set_named_chat_settings(name, st) if again else sm.create_chat(name, settings=st)
    if not ok:
        return f"The room's chat '{name}' could not be made here. Decline in words, or try once more.", False
    inv['decision'] = {'joined': True, 'chat': name, 'name': shown}
    logger.info(f"[ROOM GUEST] joined '{inv['room']}' on {inv['host']} as {shown}: chat '{name}'"
                + (' (again)' if again else ''))
    text = (f"Joined{' again' if again else ''}. The room's chat here is '{name}'; the host cues you there on your turns. "
            "Say anything else you want the host to hear in your message.")
    if notes:
        text += ' Note: ' + '; '.join(notes) + '.'
    return text, True
