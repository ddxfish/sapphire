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
# {'open': name}, a dialog handlers/commands.js knows how to draw, or {'do': name},
# an action only the page can run because the stream lives there. by is 'user' or
# 'ai'; only commands registered with ai=True can be called by 'ai'. A plugin adds
# its own through capabilities.commands, words only and for the person only: her
# tool's description is built before plugins load, and a plugin has tools for her.
#
# The store refuses history edits on a streaming chat, so what her own turn asks
# for waits: defer() holds it, finish_turn() runs it when the turn's stream ends.

import logging
import re
import threading

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(r'^/([A-Za-z][\w-]*)(?:\s+(.*))?$', re.DOTALL)
_NAME_RE = re.compile(r'[a-z][\w-]*')
_commands = {}
_pending = {}       # chat name -> callables waiting for its turn to end
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
        return out if isinstance(out, dict) else {'ok': True}
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
    from core.chat.history import count_message_tokens, turn_spans
    sm = _sm()
    settings, exported = sm.read_chat_settings(chat), sm.export_chat(chat)
    if settings is None or exported is None:
        return {'ok': False, 'say': f"Chat '{chat}' not found."}
    rows = exported['messages']
    stored = sum(count_message_tokens(m.get('content', ''), include_images=False) for m in rows)
    return {'ok': True, 'open': 'status', 'chat': chat, 'persona': settings.get('persona'),
            'prompt': settings.get('prompt'), 'toolset': settings.get('toolset'),
            'provider': settings.get('llm_primary') or 'auto', 'model': settings.get('llm_model') or '',
            'private': bool(settings.get('private_chat')), 'streaming': bool(sm.is_streaming(chat)),
            'messages': len(rows), 'turns': len(turn_spans(rows)), 'context': budget.meter(chat, stored)}


register('compact', _dialog('compact', games=False), description='summarize older history (opens the compress dialog)')
register('continue', _action('continue'), description='keep writing the last reply')
register('help', _help, args='[command]', description='list the slash commands')
register('prompt', _dialog('prompt'), description='show the prompt the next turn would send')
register('repair', _dialog('repair'), description='check for unreadable messages (opens the repair dialog)')
register('replay', _action('replay'), description='read the last reply aloud again')
register('retry', _action('retry'), description='redo the last reply')
register('status', _status, description="this chat's persona, model, toolset and size")
register('trim', _dialog('trim', games=False), description='keep the first and last turns (opens the trim dialog)')
register('remove', _remove, args='N', ai=True,
         description='remove the last N turns (a turn: one user message and every reply to it)')
