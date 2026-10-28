# core/idle.py - how long a chat (or the whole app) has been quiet
#
# One clock for everything that asks "how long since anyone talked here":
# her idle line in the ghost message (IDLE_NOTICE_MINUTES), the context
# budgeter's warm-cache hold (core/chat/budget.py), and plugins.
#
#   from core import idle
#   idle.minutes('default')               # since the last message there (anyone's)
#   idle.minutes('default', person=True)  # since the person last wrote there
#   idle.minutes()                        # since the last message in ANY chat
#   idle.since(chat, person=False)        # the same as an epoch time, or None
#
# History stamps the clock as each message lands (touch, from history.py's
# MESSAGE_ADDED sites: a new row, never an edit). After a restart a chat's
# first ask reads its last rows' own timestamps, so a quiet night survives a
# reboot. A sealed (vaulted) chat answers None: its rows are not read.
import logging
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_any = {}           # chat → epoch of its last message (anyone's)
_person = {}        # chat → epoch of the person's last message
_seeded = set()     # chats whose stored rows have been read once
_all_seeded = False


def touch(chat: str, role: str = 'assistant', at: Optional[float] = None):
    """A message landed in `chat`. role 'user' (or 'pair') is the person."""
    if not chat:
        return
    now = at or time.time()
    with _lock:
        _any[chat] = max(now, _any.get(chat, 0))
        if role in ('user', 'pair'):
            _person[chat] = max(now, _person.get(chat, 0))
        _seeded.add(chat)               # what lands now is newer than anything stored


def forget(chat: str, new_name: Optional[str] = None):
    """A chat cleared or deleted (its clock goes), or renamed (it moves)."""
    with _lock:
        a, p = _any.pop(chat, None), _person.pop(chat, None)
        _seeded.discard(chat)
        if new_name:
            if a:
                _any[new_name] = a
            if p:
                _person[new_name] = p
            _seeded.add(new_name)


def _epoch(stamp) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except (TypeError, ValueError):
        return None


def _seed(chat: Optional[str]):
    """Read the stored rows' timestamps once: one chat, or every chat (None)."""
    global _all_seeded
    if (chat is None and _all_seeded) or (chat is not None and chat in _seeded):
        return
    try:
        from core.api_fastapi import get_system
        rows = get_system().llm_chat.session_manager.last_activity(chat)
    except Exception as e:
        logger.debug(f"[IDLE] seed skipped: {e}")
        rows = {}
    with _lock:
        for name, (any_ts, person_ts) in rows.items():
            if name in _seeded:
                continue                # a touch since boot is newer
            a, p = _epoch(any_ts), _epoch(person_ts)
            if a:
                _any[name] = a
            if p:
                _person[name] = p
            _seeded.add(name)
        if chat is None:
            _all_seeded = True
        else:
            _seeded.add(chat)


def since(chat: Optional[str] = None, person: bool = False) -> Optional[float]:
    """Epoch of the last message in `chat` (None = in any chat); person=True:
    the person's last. None when nothing is known."""
    _seed(chat)
    book = _person if person else _any
    with _lock:
        if chat is None:
            return max(book.values(), default=None)
        return book.get(chat)


def minutes(chat: Optional[str] = None, person: bool = False) -> Optional[float]:
    """Minutes since the last message (see since()), or None."""
    t = since(chat, person)
    return None if t is None else max(0.0, (time.time() - t) / 60.0)


def words(mins: float) -> str:
    """'45 min', '2 h 10 min', '3 days 4 h'."""
    m = int(mins)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} day{'s' if d != 1 else ''} {h} h" if h else f"{d} day{'s' if d != 1 else ''}"
