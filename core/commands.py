# core/commands.py - slash commands: one table, two doors.
#
# The person types `/remove 2` in the chat composer (handlers/commands.js →
# POST /api/commands/run); she calls slash_command("/remove 2") (functions/
# slash_command.py). Both land in run(). A command never enters the turn engine:
# as a pre_chat hook it would stand in the inbox behind a live turn and fold
# into the text queued with it, and every door would hear it (a stranger on
# Discord typing /remove 5).
#
# A handler takes (chat, args, by) and returns {'ok': bool, 'say': words} and/or
# {'open': name}, a dialog handlers/commands.js knows how to draw, or {'do': name},
# an action only the page can run because the stream lives there. by is 'user' or
# 'ai'; only commands registered with ai=True can be called by 'ai'. A plugin adds
# its own through capabilities.commands, words only and for the person only: her
# tool's description is built before plugins load, and a plugin has tools for her.
#
# The store refuses history edits on a streaming chat, so what her own turn asks
# for waits: defer() holds it with the stream that asked, finish_turn() runs it
# once the chat's streams are done, or drops it if that stream was stopped. What
# she removes is limited by AI_REMOVE_MAX_TURNS, and /undo puts it back.

import logging
import re
import threading

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(r'^/([A-Za-z][\w-]*)(?:\s+(.*))?$', re.DOTALL)
_NAME_RE = re.compile(r'[a-z][\w-]*')
_commands = {}
_pending = {}       # chat name -> (callable, the streams that asked), waiting for the chat's streams to end
_lock = threading.Lock()


def register(name, handler, args='', description='', ai=False, plugin=None):
    """False when a plugin's name isn't usable: first come keeps it, and core's come first."""
    name = str(name or '').lower()
    held = _commands.get(name)
    if plugin and (not _NAME_RE.fullmatch(name) or (held and held['plugin'] != plugin)):
        logger.warning(f"[COMMANDS] {plugin}: can't register /{name}")
        return False
    _commands[name] = {'handler': handler, 'args': args, 'description': description, 'ai': ai, 'plugin': plugin}
    return True


def unregister_plugin(plugin):
    for name in [n for n, c in _commands.items() if c['plugin'] == plugin]:
        del _commands[name]


def all_commands():
    return [{'name': n, 'args': c['args'], 'description': c['description']} for n, c in sorted(_commands.items())]


def ai_commands():
    """{name: {'args', 'description'}} she may call."""
    return {n: {'args': c['args'], 'description': c['description']} for n, c in _commands.items() if c['ai']}


def run(line, chat, by='user'):
    m = _LINE_RE.match((line or '').strip())
    if not m:
        return {'ok': False, 'say': 'A command looks like /remove 2.'}
    name, args = m.group(1).lower(), (m.group(2) or '').strip()
    cmd = _commands.get(name)
    if not cmd or (by == 'ai' and not cmd['ai']):
        return {'ok': False, 'say': f'No command /{name}. To send it as a message, start with //.'}
    if not chat:
        return {'ok': False, 'say': f'/{name} needs a chat, and this turn has none.'}
    if args and not cmd['args']:
        return {'ok': False, 'say': f"/{name} doesn't take anything after it."}
    try:
        out = cmd['handler'](chat, args, by)
        return {'ok': True, **out} if isinstance(out, dict) else {'ok': True}
    except Exception as e:
        logger.error(f"[COMMANDS] /{name} failed: {e}", exc_info=True)
        return {'ok': False, 'say': f'/{name} failed: {e}'}


def _asking_streams(chat):
    """The streams still writing this chat: one of them is the turn asking. A second
    one (a turn stopped but not yet closed) only ever makes this drop, never cut."""
    from core.api_fastapi import get_system
    return [s for s in get_system().llm_chat.streams_for_chat(chat) if not s.llm_done]


def defer(chat, fn):
    owners = _asking_streams(chat)
    with _lock:
        _pending.setdefault(chat, []).append((fn, owners))


def has_deferred(chat):
    with _lock:
        return bool(_pending.get(chat))


def finish_turn(sm, chat):
    """A turn's stream ended (chat_streaming's finally). Another stream still on
    this chat holds the work for its own end. Stopping the turn that asked takes
    its work with it; stopping some other turn doesn't."""
    if not chat or sm.is_streaming(chat):
        return
    with _lock:
        waiting = _pending.pop(chat, None)
    for fn, owners in waiting or []:
        if any(s.cancel_flag for s in owners):
            logger.info("[COMMANDS] The asking turn was stopped, dropped its waiting command")
            _tell(chat, "Turn stopped, so the AI's removal was dropped")
            continue
        try:
            fn()
        except Exception as e:
            logger.error(f"[COMMANDS] Waiting command failed: {e}", exc_info=True)


def _sm():
    from core.api_fastapi import get_system
    return get_system().llm_chat.session_manager


def _tell(chat, title, level='info'):
    """A row for what she did after her turn. Never the chat's name; a private chat flashes."""
    from core import notify
    try:
        settings = _sm().read_chat_settings(chat)
        private = settings is None or bool(settings.get('private_chat'))   # unreadable: assume sealed
    except Exception:
        private = True
    notify.notify(title, level, source='commands', private=private)


def _unreadable(sm, chat):
    """export_chat gave nothing: a chat that isn't there, or one with a bad row."""
    if sm.read_chat_settings(chat) is None:
        return {'ok': False, 'say': f"Chat '{chat}' not found."}
    return {'ok': False, 'say': "This chat has a message that can't be read. /repair can check it."}


def _call_chats():
    from core.api_fastapi import get_system
    from core.routes.chat import _live_call_chats
    return _live_call_chats(get_system())


def _cut_refused(sm, chat, name):
    """The rules every cut of history keeps: a game's transcript is its tale, and a
    live phone call owns its chat. Chat Manager's routes hold the same two."""
    from core import rooms
    if rooms.is_room(chat):
        return f"/{name} doesn't work in a chat room yet."
    if (sm.read_chat_settings(chat) or {}).get('mode') == 'game':
        return f"/{name} doesn't work on a game's chat."
    if chat in _call_chats():
        return f"This chat has a live phone call. Hang up before using /{name}."
    return None


def _her_cap():
    import config
    try:
        return max(1, int(getattr(config, 'AI_REMOVE_MAX_TURNS', 5)))
    except (TypeError, ValueError):
        return 5


def _remove(chat, args, by):
    from core.chat.history import turn_spans
    sm = _sm()
    refused = _cut_refused(sm, chat, 'remove')
    if refused:
        return {'ok': False, 'say': refused}
    if not args:
        if by == 'ai':
            return {'ok': False, 'say': 'Say how many turns: /remove 2.'}
        exported = sm.export_chat(chat)
        if exported is None:
            return _unreadable(sm, chat)
        return {'ok': True, 'open': 'remove', 'turns': len(turn_spans(exported['messages']))}
    if not re.fullmatch(r'\d{1,6}', args):
        return {'ok': False, 'say': f"/remove takes a number of turns, not '{args}'."}
    count = int(args)

    if sm.export_chat(chat) is None:
        return _unreadable(sm, chat)

    if by == 'user':
        ok, plan = sm.plan_turn_cut(chat, count)
        if not ok:
            return {'ok': False, 'say': plan}
        ok, err = sm.apply_turn_cut(chat, plan)
        return {'ok': ok, 'say': f'Removed {count} turn(s).' if ok else err}

    # What the person means by "remove the last one" is that one and this request:
    # N turns before her own, and her own turn with them when it ends. Planned now
    # so the answer is honest about the count.
    if has_deferred(chat):
        return {'ok': False, 'say': 'A removal is already waiting. Ask for one at a time.'}
    # Neither refusal names a number: a page she read could be steering her.
    if count > _her_cap():
        return {'ok': False, 'say': "That's more turns than you can remove at once. The person can do it with /remove."}
    ok, plan = sm.plan_turn_cut(chat, count, keep_last=1)
    if not ok:
        return {'ok': False, 'say': "This chat doesn't have that many turns to remove."}

    def _apply():
        ok, err = _sm().apply_turn_cut(chat, plan, undo=True, with_own_turn=True)
        logger.info(f"[COMMANDS] AI /remove {count}: {'done' if ok else 'refused'}")
        _tell(chat, f'AI removed {count + 1} turn(s) from a chat, its own included. /undo there brings them back.' if ok
              else 'AI could not remove turns', 'info' if ok else 'error')

    defer(chat, _apply)
    return {'ok': True, 'say': f'When this turn ends, it and the {count} turn(s) before it will be removed.'}


def _undo(chat, args, by):
    sm = _sm()
    refused = _cut_refused(sm, chat, 'undo')
    if refused:
        return {'ok': False, 'say': refused}
    turns, err = sm.undo_turn_cut(chat)
    return {'ok': not err, 'say': err or f'Put back {turns} turn(s).'}


def _dialog(name, games=True):
    """The dialog does the rest: its route holds every rule. A game's transcript is
    the tale and its journal points into it, so Chat Manager offers no trim or
    compress there and neither do these."""
    def handler(chat, args, by):
        settings = _sm().read_chat_settings(chat)
        if settings is None:
            return {'ok': False, 'say': f"Chat '{chat}' not found."}
        if not games and settings.get('mode') == 'game':
            return {'ok': False, 'say': f"/{name} doesn't work on a game's chat."}
        return {'ok': True, 'open': name, 'chat': chat}
    return handler


def _action(name):
    return lambda chat, args, by: {'ok': True, 'do': name}


def _help(chat, args, by):
    rows = all_commands()
    if args:
        want = args.lstrip('/').lower()
        rows = [c for c in rows if c['name'] == want]
        if not rows:
            return {'ok': False, 'say': f'No command /{want}.'}
    return {'ok': True, 'open': 'help', 'commands': rows}


def _status(chat, args, by):
    from core.chat import budget
    from core.chat.history import turn_spans
    sm = _sm()
    settings, exported = sm.read_chat_settings(chat), sm.export_chat(chat)
    if settings is None or exported is None:
        return _unreadable(sm, chat)
    rows = exported['messages']
    return {'ok': True, 'open': 'status', 'chat': chat, 'persona': settings.get('persona'),
            'prompt': settings.get('prompt'), 'toolset': settings.get('toolset'),
            'provider': settings.get('llm_primary') or 'auto', 'model': settings.get('llm_model') or '',
            'private': bool(settings.get('private_chat')), 'streaming': bool(sm.is_streaming(chat)),
            'messages': len(rows), 'turns': len(turn_spans(rows)), 'context': budget.report(chat, rows)['meter']}


def _context(chat, args, by):
    from core.chat import budget
    sm = _sm()
    exported = sm.export_chat(chat)
    if exported is None:
        return _unreadable(sm, chat)
    return {'ok': True, 'open': 'context', 'chat': chat, **budget.report(chat, exported['messages'])}


register('compact', _dialog('compact', games=False), description='summarize older history (opens the compress dialog)')
register('context', _context, description="what's taking up this chat's context, biggest first")
register('continue', _action('continue'), description='keep writing the last reply')
register('help', _help, args='[command]', description='list the slash commands')
register('prompt', _dialog('prompt'), description='show the prompt the next turn would send')
register('repair', _dialog('repair'), description='check for unreadable messages (opens the repair dialog)')
register('replay', _action('replay'), description='read the last reply aloud again')
register('retry', _action('retry'), description='redo the last reply')
register('status', _status, description="this chat's persona, model, toolset and size")
register('trim', _dialog('trim', games=False), description='keep the first and last turns (opens the trim dialog)')
register('undo', _undo, description="put back the turns the AI last removed")
register('remove', _remove, args='N', ai=True,
         description='remove the last N turns (a turn: one user message and every reply to it)')
