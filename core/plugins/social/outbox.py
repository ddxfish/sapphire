# core/plugins/social/outbox.py - drafts waiting for a human, and the log of what went out
#
# A connection in `draft` mode never posts from a tool call: the tool queues
# a row here, a notification points the person at the card, Approve posts it.
# `auto` connections post at once and leave their row here as the log (with
# the platform's id and URL, or the error). Image bytes live beside the rows
# as files - a draft must survive the chat that made it losing its images.
# Newest KEEP rows kept; older rows and their files go.

import logging
import time
import uuid

from core.paths import PLUGIN_STATE_DIR

logger = logging.getLogger(__name__)

DIR = PLUGIN_STATE_DIR / 'social-outbox'
KEEP = 200
STATUSES = ('draft', 'posted', 'failed', 'rejected')
_EXT = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}
_MT = {v: k for k, v in _EXT.items()}


def _st():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state('social')


def add(profile, connection, text, images=(), link='', status='draft', result=None):
    """Store one row (and its image bytes). Returns the row."""
    rid = uuid.uuid4().hex[:10]
    names = []
    for n, (raw, mt) in enumerate(images or ()):
        fn = f"{rid}-{n}.{_EXT.get(mt, 'jpg')}"
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / fn).write_bytes(raw)
        names.append(fn)
    row = {'id': rid, 'profile': profile, 'connection': connection, 'text': str(text or ''),
           'link': str(link or ''), 'images': names,
           'status': status if status in STATUSES else 'draft',
           'created': time.time(), 'decided': 0 if status == 'draft' else time.time(),
           'result': dict(result or {})}
    dropped = []

    def fn(cur):
        rows_ = [row] + list(cur or [])
        dropped.extend(rows_[KEEP:])
        return rows_[:KEEP]
    _st().update_with_lock('outbox', fn, default=[])
    for r in dropped:
        _unlink(r)
    return dict(row)


def _unlink(row):
    for fn in row.get('images') or []:
        try:
            (DIR / fn).unlink(missing_ok=True)
        except Exception:
            pass


def rows(status=None, profile=None):
    out = list(_st().get('outbox', []) or [])
    if status:
        out = [r for r in out if r.get('status') == status]
    if profile:
        out = [r for r in out if r.get('profile') == profile]
    return [dict(r) for r in out]


def get(rid):
    return next((r for r in rows() if r.get('id') == rid), None)


def update(rid, **fields):
    """Set fields on one row; the row after, or None when unknown."""
    found = {}

    def fn(cur):
        out = []
        for r in (cur or []):
            r = dict(r)
            if r.get('id') == rid:
                r.update(fields)
                found.update(r)
            out.append(r)
        return out
    _st().update_with_lock('outbox', fn, default=[])
    return found or None


def pending(profile=None):
    """What still needs the person: drafts to approve and failures to retry or dismiss."""
    return [r for r in rows(profile=profile) if r.get('status') in ('draft', 'failed')]


def image_path(row, n):
    """The n-th image file of a row, or None. Names are ours, never the client's."""
    names = row.get('images') or []
    try:
        fn = names[int(n)]
    except (TypeError, ValueError, IndexError):
        return None
    p = DIR / fn
    return p if fn == p.name and p.is_file() else None


def images_of(row):
    """[(bytes, media_type)] still on disk."""
    out = []
    for n in range(len(row.get('images') or [])):
        p = image_path(row, n)
        if p:
            out.append((p.read_bytes(), _MT.get(p.suffix[1:], 'image/jpeg')))
    return out


def remove(rid):
    gone = []

    def fn(cur):
        keep = []
        for r in (cur or []):
            (gone if r.get('id') == rid else keep).append(r)
        return keep
    _st().update_with_lock('outbox', fn, default=[])
    for r in gone:
        _unlink(r)
    return bool(gone)


def clear_done():
    """Drop the history (posted, rejected); drafts and failures stay in the queue."""
    gone = []

    def fn(cur):
        keep = []
        for r in (cur or []):
            (keep if r.get('status') in ('draft', 'failed') else gone).append(r)
        return keep
    _st().update_with_lock('outbox', fn, default=[])
    for r in gone:
        _unlink(r)
    return len(gone)


_GO = {'hash': '#apps/social'}   # the Social page, pinned or not


def notify_pending():
    """One notification row (re-fired, never piled up) while the queue is not empty."""
    n = len(pending())
    if not n:
        return
    try:
        from core import notify
        notify.notify(f"Social: {n} post{'s' if n != 1 else ''} waiting for you",
                      level='warning', body='Apps › Social › Outbox',
                      source='social', go=_GO, key='social-outbox')
    except Exception as e:
        logger.debug(f"[SOCIAL] outbox notification skipped: {e}")


def notify_failed(who, error):
    """A post she tried to send did not go: say so, and where to retry it."""
    try:
        from core import notify
        notify.notify(f"Social: post on {who} failed", level='error',
                      body=f"{error} — Retry or Reject it in Apps › Social › Outbox",
                      source='social', go=_GO, key='social-failed')
    except Exception as e:
        logger.debug(f"[SOCIAL] failure notification skipped: {e}")
