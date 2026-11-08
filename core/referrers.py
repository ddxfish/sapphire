# core/referrers.py
"""Everyone who holds a persona or prompt NAME, in one place.

Rename and delete upkeep used to live wherever someone remembered it — the
persona manager knew chats and favorites, prompt rename knew chats and
tasks — and every hunt found another stranded referrer (API tokens, room
seats, the new-chat defaults; hunt 2026-11-08). A new place that stores one
of these names gets a line here, nowhere else.

Every step is best-effort and logged: the record itself already moved, and
one unreachable referrer must not undo the others. Callers run OUTSIDE their
own manager locks (several steps re-enter the managers).
"""
import logging

logger = logging.getLogger(__name__)


def _system():
    try:
        from core.api_fastapi import get_system
        return get_system()
    except Exception:
        return None


def _step(what, fn):
    try:
        return fn() or []
    except Exception as e:
        logger.warning(f"[REFS] {what} failed: {e}")
        return []


def _chats(key, old, new):
    sm = getattr(getattr(_system(), 'llm_chat', None), 'session_manager', None)
    if sm is None or not hasattr(sm, 'reset_chat_scope_ref'):
        return []
    return [f"chat {n}" for n in (sm.reset_chat_scope_ref(key, old, reset_to=new) or [])]


def _tasks(key, old, new):
    sched = getattr(_system(), 'continuity_scheduler', None)
    return [f"task {n}" for n in sched.repoint_ref(key, old, new)] if sched else []


def _chat_defaults(key, old, new):
    from core.chat.history import repoint_chat_defaults
    return [f"new-chat default {key}"] if repoint_chat_defaults(key, old, new) else []


def _setting(key, old, new):
    from core.settings_manager import settings
    if settings.get(key) != old:
        return []
    settings.set(key, new, persist=True)
    return [key]


def _favorites(old, new):
    from core.settings_manager import settings
    favs = list(settings.get('PERSONA_FAVORITES') or [])
    if old not in favs:
        return []
    out = [new if n == old else n for n in favs] if new else [n for n in favs if n != old]
    settings.set('PERSONA_FAVORITES', out, persist=True)
    return ['favorites']


def _tokens(old, new):
    from core.api_tokens import api_tokens
    return [f"API key {n}" for n in api_tokens.repoint_persona(old, new)]


def _seats(old, new):
    from core.rooms import repoint_persona
    return [f"room {n}" for n in repoint_persona(old, new)]


def persona_renamed(old: str, new: str) -> list:
    """Every holder of persona `old` now holds `new`. Returns what moved."""
    touched = []
    touched += _step("chats", lambda: _chats('persona', old, new))
    touched += _step("tasks", lambda: _tasks('persona', old, new))
    touched += _step("new-chat defaults", lambda: _chat_defaults('persona', old, new))
    touched += _step("default persona", lambda: _setting('DEFAULT_PERSONA', old, new))
    touched += _step("favorites", lambda: _favorites(old, new))
    touched += _step("API keys", lambda: _tokens(old, new))
    touched += _step("room seats", lambda: _seats(old, new))
    if touched:
        logger.info(f"[REFS] persona '{old}' -> '{new}': {touched}")
    return touched


def persona_deleted(name: str) -> list:
    """Release persona `name` everywhere a dead name would be adopted by
    the NEXT persona to take it (a chat's persona, a key's voice and memory
    scope). Chats reset to '' — 'default' was a dangling name, not a
    sentinel. Tasks keep the name and fail loudly at run time (executor).
    Room seats keep it: clearing a seat's persona drops the seat, and with
    the key cleared nothing can speak through a dead door."""
    touched = []
    touched += _step("chats", lambda: _chats('persona', name, ''))
    touched += _step("new-chat defaults", lambda: _chat_defaults('persona', name, ''))
    touched += _step("default persona", lambda: _setting('DEFAULT_PERSONA', name, ''))
    touched += _step("favorites", lambda: _favorites(name, None))
    touched += _step("API keys", lambda: _tokens(name, None))
    if touched:
        logger.warning(f"[REFS] persona '{name}' deleted — released: {touched}")
    return touched


def prompt_renamed(old: str, new: str) -> list:
    """Every holder of prompt `old` now holds `new`. Returns what moved."""
    def personas():
        from core.personas.persona_manager import persona_manager
        return [f"persona {n}" for n in persona_manager.repoint_prompt(old, new)]
    touched = []
    touched += _step("chats", lambda: _chats('prompt', old, new))
    touched += _step("tasks", lambda: _tasks('prompt', old, new))
    touched += _step("personas", personas)
    touched += _step("new-chat defaults", lambda: _chat_defaults('prompt', old, new))
    if touched:
        logger.info(f"[REFS] prompt '{old}' -> '{new}': {touched}")
    return touched
