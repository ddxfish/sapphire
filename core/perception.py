"""core/perception.py — the perception inbox (Game Room F3, 2026-09-09).

What she sees between her own turns: frames of a screen, a line of game
state, a subtitle window from a movie. Sources DEPOSIT into a per-chat
inbox (a room's board over POST /api/perception/{chat}, a daemon watching
mpv, a plugin); the cadence organ TAKES the latest deposit when it runs
her unprompted turn. Latest wins — frames are perishable, a stale screen
is worse than none — and nothing here is persisted: the organ hands the
frames to the model for that turn only (F2's blob lane will keep them).
"""
import base64
import logging
import threading
import time

logger = logging.getLogger(__name__)

MAX_FRAMES = 12
MAX_FRAME_BYTES = 1_500_000     # base64 chars per frame
MAX_TEXT = 4000
TTL_S = 600                     # a deposit older than this is dropped on take
_MEDIA = {'image/jpeg', 'image/png', 'image/webp'}

_lock = threading.Lock()
_inbox = {}                     # chat -> {'frames', 'text', 'source', 'at'}


def clean_frames(frames):
    """[{data, media_type}] with data-URL prefixes stripped, media types
    held to the three the vision lanes accept, sizes capped, count capped."""
    out = []
    for f in (frames or []):
        if not isinstance(f, dict):
            continue
        data = str(f.get('data') or '')
        media = str(f.get('media_type') or 'image/jpeg').lower()
        if data.startswith('data:'):
            head, _, data = data.partition(',')
            if ';base64' not in head:
                continue
            media = head[5:].split(';', 1)[0] or media
        if media not in _MEDIA or not data or len(data) > MAX_FRAME_BYTES:
            continue
        try:
            base64.b64decode(data[:64], validate=True)
        except Exception:
            continue
        out.append({'data': data, 'media_type': media})
    return out[-MAX_FRAMES:]           # latest wins here too


def deposit(chat, frames=None, text=None, source=None):
    """Latest wins. Returns the stored shape's counts."""
    if not chat:
        return None
    fr = clean_frames(frames)
    tx = str(text or '').strip()[:MAX_TEXT]
    if not fr and not tx:
        return None
    entry = {'frames': fr, 'text': tx, 'source': str(source or '')[:40], 'at': time.time()}
    with _lock:
        _inbox[chat] = entry
    logger.debug(f"[PERCEPTION] deposit -> '{chat}': {len(fr)} frame(s), {len(tx)} chars ({entry['source']})")
    return {'frames': len(fr), 'text': len(tx)}


def take(chat):
    """The latest deposit for `chat`, cleared; None when empty or stale."""
    with _lock:
        entry = _inbox.pop(chat, None)
    if not entry:
        return None
    if time.time() - entry['at'] > TTL_S:
        logger.debug(f"[PERCEPTION] stale deposit for '{chat}' dropped")
        return None
    return entry


def peek(chat):
    with _lock:
        e = _inbox.get(chat)
        return dict(e) if e else None


def clear(chat):
    with _lock:
        _inbox.pop(chat, None)
