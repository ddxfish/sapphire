# core/commands.py - slash commands: one table, two doors.
#
# The person types `/remove 2` in the chat composer (handlers/commands.js →
# POST /api/commands/run); she calls self_command("/remove 2") (functions/
# self_command.py). Both land in run(). A command never enters the turn engine:
# as a pre_chat hook it would stand in the inbox behind a live turn and fold
# into the text queued with it, and every door would hear it (a stranger on
# Discord typing /remove 5).
#
# A handler takes (chat, args, by) and returns {'ok': bool, 'say': words} and/or
# {'open': name}, a dialog handlers/commands.js knows how to draw. by is 'user'
# or 'ai'; only commands registered with ai=True can be called by 'ai'.
#
# The store refuses history edits on a streaming chat, so what her own turn asks
# for waits: defer() holds it, finish_turn() runs it when the turn's stream ends.

import logging
import re
import threading

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(r'^/([A-Za-z][\w-]*)(?:\s+(.*))?$', re.DOTALL)
_commands = {}
_pending = {}       # chat name -> callables waiting for its turn to end
_lock = threading.Lock()


def register(name, handler, args='', description='', ai=False):
    _commands[name.lower()] = {'handler': handler, 'args': args, 'description': description, 'ai': ai}


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
    try:
        return cmd['handler'](chat, args, by)
    except Exception as e:
        logger.error(f"[COMMANDS] /{name} failed: {e}", exc_info=True)
        return {'ok': False, 'say': f'/{name} failed: {e}'}


def defer(chat, fn):
    with _lock:
        _pending.setdefault(chat, []).append(fn)


def has_deferred(chat):
    with _lock:
        return bool(_pending.get(chat))


def finish_turn(sm, chat, cancelled=False):
    """A turn's stream ended (chat_streaming's finally). Another stream still on
    this chat holds the work for its own end. Stop takes her waiting work with it."""
    if not chat or sm.is_streaming(chat):
        return
    with _lock:
        fns = _pending.pop(chat, None)
    if not fns:
        return
    if cancelled:
        logger.info(f"[COMMANDS] Turn stopped, dropped {len(fns)} waiting command(s)")
        return
    for fn in fns:
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
        private = bool((_sm().read_chat_settings(chat) or {}).get('private_chat'))
    except Exception:
        private = True
    notify.notify(title, level, source='commands', private=private)


def _remove(chat, args, by):
    from core import rooms
    from core.chat.history import turn_spans
    if rooms.is_room(chat):
        return {'ok': False, 'say': "/remove doesn't work in a chat room yet."}
    sm = _sm()
    if not args:
        if by == 'ai':
            return {'ok': False, 'say': 'Say how many turns: /remove 2.'}
        exported = sm.export_chat(chat)
        if exported is None:
            return {'ok': False, 'say': f"Chat '{chat}' not found."}
        return {'ok': True, 'open': 'remove', 'turns': len(turn_spans(exported['messages']))}
    if not re.fullmatch(r'\d{1,6}', args):
        return {'ok': False, 'say': f"/remove takes a number of turns, not '{args}'."}
    count = int(args)

    if by == 'user':
        ok, plan = sm.plan_turn_cut(chat, count)
        if not ok:
            return {'ok': False, 'say': plan}
        ok, err = sm.apply_turn_cut(chat, plan)
        return {'ok': ok, 'say': f'Removed {count} turn(s).' if ok else err}

    # Her own turn is the newest one and it stays: the person asked, she answers,
    # the turns before that go. Planned now so the answer is honest about the count.
    if has_deferred(chat):
        return {'ok': False, 'say': 'A removal is already waiting for this turn to end. Ask for one per turn.'}
    ok, plan = sm.plan_turn_cut(chat, count, keep_last=1)
    if not ok:
        return {'ok': False, 'say': plan}

    def _apply():
        ok, err = _sm().apply_turn_cut(chat, plan)
        logger.info(f"[COMMANDS] AI /remove {count}: {'done' if ok else err}")
        _tell(chat, f'AI removed {count} turn(s) from a chat' if ok else f'AI could not remove turns: {err}',
              'info' if ok else 'error')

    defer(chat, _apply)
    return {'ok': True, 'say': f'The {count} turn(s) before this one will be removed when this turn ends. '
                               'This turn stays.'}


def _compact(chat, args, by):
    """The dialog does the rest: POST /api/chats/{chat}/compress holds every rule."""
    if _sm().read_chat_settings(chat) is None:
        return {'ok': False, 'say': f"Chat '{chat}' not found."}
    return {'ok': True, 'open': 'compact', 'chat': chat}


register('compact', _compact, description='summarize older history (opens the compress dialog)')
register('remove', _remove, args='N', ai=True,
         description='remove the last N turns (a turn: one user message and every reply to it)')
