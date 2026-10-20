# core/notify.py - the notification center's engine (tmp/notification-center-plan.md)
#
# A toast is what you did; a notification is what happened to you. Rows live
# here, in user/notifications.json (a ring of RING rows; active rows are never
# pruned), and reach every tab over the bus as `notification`. The browser
# half, shared/notify.js, paints toasts FROM rows; flash() is the passthrough,
# a toast with no row. Levels: info is the log (never toasts, never counts),
# success toasts, warning counts yellow, error and critical count red,
# critical sticks until clicked. A progress row starts 'active', sorts first
# everywhere and cannot be cleared until it ends; its ticks ride the bus only
# (`notification_progress`), the file sees state changes.
#
# Never a private chat name in a row: there is no chat field, and a caller in
# a private context passes private=True, which turns the row into a flash.
#
# Publishers of the old events keep publishing them: the routing table in
# init() turns plugin_notice, plugin_load_error, plugin_reloaded,
# continuity_task_error, sapphire_health_alert and stt_error into rows or
# flashes here, so every plugin written against those lands in the center
# unchanged. A row born on the server carries no origin (every tab paints
# it); a row posted by a tab carries that tab's origin and the bus client
# skips its own echo (it painted the toast before the POST).
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

LEVELS = ('info', 'success', 'warning', 'error', 'critical')
STATES = ('open', 'active', 'done', 'failed')
RING = 500
TITLE_MAX, BODY_MAX, KEY_MAX = 200, 1000, 128
TICK_S = 0.1                      # progress ticks on the bus: at most this often per row
PROJECT_ROOT = Path(__file__).absolute().parent.parent
STORE = PROJECT_ROOT / 'user' / 'notifications.json'

_lock = threading.RLock()
_rows: dict = {}                  # id -> row; insertion order = creation, or the last re-fire
_last_tick: dict = {}             # id -> time of the last tick that went out
_loaded = False
_hooked = False


def _publish(event, data):
    try:
        from core.event_bus import publish
        publish(event, data, ephemeral=True)     # tabs fetch the list on connect; no replay
    except Exception as e:
        logger.debug(f"[NOTIFY] publish {event} failed: {e}")


def _text(s, cap):
    return str(s if s is not None else '')[:cap]


def _clean_go(go):
    """A click target: {view, tab?, key?} or {hash}. Anything else is None."""
    if not isinstance(go, dict):
        return None
    if isinstance(go.get('hash'), str) and go['hash'].startswith('#'):
        return {'hash': go['hash'][:200]}
    if isinstance(go.get('view'), str) and go['view']:
        out = {'view': go['view'][:64]}
        for k in ('tab', 'key'):
            if isinstance(go.get(k), str) and go[k]:
                out[k] = go[k][:64]
        return out
    return None


def _load():
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        data = []
        try:
            if STORE.exists():
                data = json.loads(STORE.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning(f"[NOTIFY] {STORE.name} unreadable, starting empty: {e}")
        swept = 0
        for r in (data if isinstance(data, list) else []):
            if not isinstance(r, dict) or not r.get('id'):
                continue
            if r.get('state') == 'active':           # work the last process never finished
                r.update(state='failed', level='warning', body='Interrupted by a restart.',
                         updated=time.time(), read=False)
                swept += 1
            _rows[r['id']] = r
        if swept:
            _save_locked()
            logger.info(f"[NOTIFY] {swept} active row(s) from the last run marked interrupted")


def _save_locked():
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(STORE.parent), prefix='.notifications-', suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(list(_rows.values()), f)
        from core.fs_utils import replace_with_retry
        replace_with_retry(Path(tmp), STORE)
    except Exception as e:
        logger.warning(f"[NOTIFY] could not write {STORE.name}: {e}")


def _prune_locked():
    """Over the ring: the log (info, success) goes first, oldest first, then
    the rest, oldest first. Active rows never."""
    extra = len(_rows) - RING
    if extra <= 0:
        return
    quiet = [r['id'] for r in _rows.values() if r['state'] != 'active' and r['level'] in ('info', 'success')]
    loud = [r['id'] for r in _rows.values() if r['state'] != 'active' and r['level'] not in ('info', 'success')]
    for rid in (quiet + loud)[:extra]:
        _rows.pop(rid, None)


def notify(title, level='info', body='', source='core', go=None, key=None,
           private=False, origin=None, state='open', progress=None):
    """A row, and the toast its level earns. Returns the row id, or None for a
    flash (private=True). A key makes the row re-fire instead of piling up:
    the row with that key is updated, marked unread and moved to the front."""
    level = level if level in LEVELS else 'info'
    title = _text(title, TITLE_MAX).strip() or 'Notification'
    body = _text(body, BODY_MAX)
    if private:
        flash(f"{title}: {body}" if body else title, level, origin=origin)
        return None
    _load()
    now = time.time()
    with _lock:
        row = None
        if key:
            key = _text(key, KEY_MAX)
            row = next((r for r in _rows.values() if r.get('key') == key), None)
        if row is not None:
            _rows.pop(row['id'], None)
            row.update(title=title, body=body, level=level, updated=now, read=False,
                       state=state if state in STATES else 'open', progress=progress,
                       source=_text(source or row.get('source') or 'core', 64))
            if go is not None:
                row['go'] = _clean_go(go)
        else:
            row = {'id': uuid.uuid4().hex[:12], 'ts': now, 'updated': now, 'level': level,
                   'title': title, 'body': body, 'source': _text(source or 'core', 64),
                   'state': state if state in STATES else 'open', 'progress': progress,
                   'go': _clean_go(go), 'key': key or None, 'read': False}
        _rows[row['id']] = row
        _prune_locked()
        _save_locked()
        out = dict(row)
    _publish('notification', dict(out, origin=origin))
    return out['id']


def flash(message, level='info', seconds=None, origin=None):
    """A toast with no row: the passthrough for click feedback and for words
    not worth the log."""
    _publish('notification_flash', {'message': _text(message, 500),
                                    'level': level if level in LEVELS else 'info',
                                    'seconds': seconds, 'origin': origin})


# ── progress rows ──────────────────────────────────────────────────────────

def begin(title, source='core', go=None, key=None, body=''):
    """Start an active row. Returns its id; move it with tick(), end it with finish()."""
    return notify(title, 'info', body, source, go, key, state='active',
                  progress={'done': 0, 'total': 0, 'percent': None, 'indeterminate': True})


def tick(rid, done=None, total=None, percent=None, text=None):
    """Move an active row along. Bus only, never the file; at most one tick per
    TICK_S per row (finish() always lands)."""
    with _lock:
        row = _rows.get(rid)
        if not row or row['state'] != 'active':
            return
        p = dict(row.get('progress') or {})
        if done is not None:
            p['done'] = int(done)
        if total is not None:
            p['total'] = int(total)
        if percent is None and p.get('total'):
            percent = 100.0 * p.get('done', 0) / p['total']
        if percent is not None:
            p['percent'] = max(0, min(100, int(percent)))
            p['indeterminate'] = False
        row['progress'] = p
        if text is not None:
            row['body'] = _text(text, BODY_MAX)
        now = time.time()
        if now - _last_tick.get(rid, 0) < TICK_S:
            return
        _last_tick[rid] = now
        row['updated'] = now
        out = {'id': rid, 'progress': dict(p), 'body': row['body'], 'updated': now}
    _publish('notification_progress', out)


def finish(rid, failed=False, body=None, title=None):
    """End an active row: done (success) or failed (error). Unread again, so
    the end toasts and a failure counts red."""
    with _lock:
        row = _rows.get(rid)
        if not row or row['state'] != 'active':
            return
        _rows.pop(rid, None)
        p = dict(row.get('progress') or {})
        if not failed:
            p.update(percent=100, indeterminate=False)
        row.update(state='failed' if failed else 'done', level='error' if failed else 'success',
                   updated=time.time(), read=False, progress=p)
        if body is not None:
            row['body'] = _text(body, BODY_MAX)
        if title is not None:
            row['title'] = _text(title, TITLE_MAX)
        _rows[rid] = row
        _last_tick.pop(rid, None)
        _save_locked()
        out = dict(row)
    _publish('notification', dict(out, origin=None))


class _Handle:
    """What a producer holds while its row is active. lazy: the row is born on
    the first tick, so a library that finds everything cached makes no row."""

    def __init__(self, rid=None, born=None):
        self.id = rid
        self._born = born            # () -> rid, when lazy

    def _ensure(self):
        if self.id is None and self._born is not None:
            self.id = self._born()
        return self.id

    def update(self, done, total=None, text=None):
        if self._ensure():
            tick(self.id, done=done, total=total, text=text)

    def set(self, percent, text=None):
        if self._ensure():
            tick(self.id, percent=percent, text=text)

    def text(self, body):
        if self._ensure():
            tick(self.id, text=body)


@contextmanager
def progress(title, source='core', go=None, key=None, body='', done_title=None, failed_title=None, lazy=False):
    """with progress("Downloading voice Amy", source="piper") as p: p.update(done, total)
    Leaves done (done_title names the finished thing); an exception leaves
    failed with its words (failed_title names that) and is re-raised. lazy:
    no row until the first tick, for work that is often already done."""
    born = lambda: begin(title, source, go, key, body)
    h = _Handle(None if lazy else born(), born if lazy else None)
    try:
        yield h
    except BaseException as e:
        if h.id is not None:
            finish(h.id, failed=True, body=str(e) or e.__class__.__name__, title=failed_title)
        raise
    else:
        if h.id is not None:
            finish(h.id, title=done_title)


def tqdm_for(handle):
    """A tqdm class bound to a progress handle, for libraries that take
    tqdm_class= (huggingface_hub.hf_hub_download / snapshot_download) or read
    a module-level `tqdm` (openwakeword.utils). Born disabled: it never writes
    to stderr (the Windows [WinError 1] crash core/setup.py works around),
    tolerates the hub's extra kwargs (name=...), and counts bytes itself since
    a disabled tqdm stops counting. Only byte bars (unit B or iB) report;
    snapshot_download's file-count bar passes through silently. That one's
    total grows as files begin, so an early percent can step back. Returns
    None when tqdm cannot be imported: pass nothing then."""
    try:
        from tqdm.auto import tqdm as _base
    except Exception:
        return None

    class _Reporter(_base):
        def __init__(self, *a, **kw):
            self._unit = kw.get('unit')
            self._lk = threading.Lock()
            kw.pop('name', None)
            kw['disable'] = True
            super().__init__(*a, **kw)

        def update(self, n=1):
            with self._lk:
                self.n = (self.n or 0) + (n or 0)
                done, total = self.n, self.total
            if self._unit in ('B', 'iB') and total:
                handle.update(int(done), int(total))

        def close(self):
            pass

    return _Reporter


# ── reading and tending ────────────────────────────────────────────────────

def get(rid):
    _load()
    with _lock:
        r = _rows.get(rid)
        return dict(r) if r else None


def rows(level=None, source=None, unread=None, limit=RING):
    """Active first, then newest first. level: one name, or several comma-joined."""
    _load()
    with _lock:
        out = list(_rows.values())
    if level:
        want = {l.strip() for l in str(level).split(',') if l.strip()}
        out = [r for r in out if r['level'] in want]
    if source:
        out = [r for r in out if r['source'] == source]
    if unread is not None:
        out = [r for r in out if (not r['read']) == bool(unread)]
    out.sort(key=lambda r: (r['state'] != 'active', -r['updated']))
    return [dict(r) for r in out[:max(1, int(limit))]]


def counts():
    """What the sidebar wears: red = unread error + critical, yellow = unread
    warning, active = rows in flight. Info and success never count."""
    _load()
    with _lock:
        red = yellow = active = 0
        for r in _rows.values():
            if r['state'] == 'active':
                active += 1
            elif not r['read']:
                if r['level'] in ('error', 'critical'):
                    red += 1
                elif r['level'] == 'warning':
                    yellow += 1
    return {'red': red, 'yellow': yellow, 'active': active}


def mark_read(rid, origin=None):
    _load()
    with _lock:
        r = _rows.get(rid)
        if not r:
            return False
        if not r['read']:
            r['read'] = True
            _save_locked()
    _publish('notification_read', {'id': rid, 'origin': origin})
    return True


def mark_all_read(origin=None):
    """Opening the Notifications page: everything read, the counts go to zero."""
    _load()
    with _lock:
        changed = [r for r in _rows.values() if not r['read']]
        for r in changed:
            r['read'] = True
        if changed:
            _save_locked()
    _publish('notifications_read', {'origin': origin})
    return len(changed)


def dismiss(rid, origin=None):
    """Remove one row. An active row refuses (ValueError): it goes when its work ends."""
    _load()
    with _lock:
        r = _rows.get(rid)
        if not r:
            return False
        if r['state'] == 'active':
            raise ValueError('still active')
        del _rows[rid]
        _save_locked()
    _publish('notification_gone', {'id': rid, 'origin': origin})
    return True


def clear(origin=None):
    """Remove every row that is not active."""
    _load()
    with _lock:
        gone = [k for k, r in _rows.items() if r['state'] != 'active']
        for k in gone:
            del _rows[k]
        if gone:
            _save_locked()
    _publish('notifications_cleared', {'origin': origin})
    return len(gone)


# ── routing: the old events become rows here ───────────────────────────────

_PLUGINS = {'view': 'settings', 'tab': 'plugins'}


def _on_plugin_notice(event):
    d = event.get('data') or {}
    msg, sev, plugin = d.get('message'), d.get('severity'), d.get('plugin') or 'plugin'
    if not msg:
        return
    if sev in ('warning', 'error', 'critical'):
        notify(msg, sev, source=plugin, go=_PLUGINS)
    else:
        flash(msg, 'success' if sev == 'success' else 'info')


def _on_plugin_load_error(event):
    d = event.get('data') or {}
    plugin = d.get('plugin') or 'plugin'
    deps = d.get('missing_deps') or []
    body = str(d.get('error') or 'failed to load')
    if d.get('hint'):
        body += f" — {d['hint']}"
    notify(f"Plugin '{plugin}' did not load", 'warning' if deps else 'error', body,
           source=plugin, key=f"plugin-load:{plugin}", go=_PLUGINS)


def _on_plugin_reloaded(event):
    plugin = (event.get('data') or {}).get('plugin') or 'plugin'
    notify(f"Plugin '{plugin}' reloaded", 'success', source=plugin,
           key=f"plugin-reload:{plugin}", go=_PLUGINS)


def _on_task_error(event):
    d = event.get('data') or {}
    task = d.get('task') or 'Unknown'
    notify(f'Task "{task}" failed', 'error', d.get('error') or 'failed',
           key=f"task:{task}", go={'view': 'scheduled'})


def _on_health_alert(event):
    d = event.get('data') or {}
    kind = d.get('type') or 'db_integrity'
    dbs = ', '.join(d.get('dbs') or [])
    title, body, level = {
        'backup_password_missing': ('Backup NOT made', 'Encryption is on but no backup password is set.', 'error'),
        'backup_ship_refused': ('Backup not sent to devices', d.get('reason') or 'refused', 'error'),
        'backup_ship_failed': (f"Backup to {d.get('target') or 'a device'} FAILED",
                               d.get('reason') or 'see the log', 'error'),
    }.get(kind) or ('Database integrity check FAILED' + (f" ({dbs})" if dbs else ''),
                    'Backups are halted until the sentinel is cleared.', 'critical')
    notify(title, level, body, key=f"health:{kind}", go={'view': 'settings', 'tab': 'backup'})


def _on_webhook(event):
    """The log: a webhook hit, by path and task. Never the payload."""
    d = event.get('data') or {}
    notify(f"Webhook fired: /{d.get('path') or '?'}", 'info',
           f"{d.get('method') or ''} task {d.get('task_id') or '?'}".strip())


def _on_task_done(event):
    """The log: a scheduled task ended (complete, cancelled, skipped). Never
    its words: `details` may carry what it said."""
    d = event.get('data') or {}
    notify(f'Task "{d.get("task_name") or "Unknown"}" {d.get("status") or "complete"}', 'info',
           go={'view': 'scheduled'})


def _on_stt_error(event):
    msg = (event.get('data') or {}).get('message')
    if msg:
        notify(msg, 'error', key='stt', go={'view': 'settings', 'tab': 'stt'})


def init():
    """Load the store, hook the bus, pick up load errors already on the
    loader's list. Idempotent; api_fastapi calls it at import."""
    global _hooked
    _load()
    with _lock:
        if _hooked:
            return
        _hooked = True
    try:
        from core.event_bus import get_event_bus, Events
        bus = get_event_bus()
        bus.on([Events.PLUGIN_NOTICE], _on_plugin_notice)
        bus.on([Events.PLUGIN_LOAD_ERROR], _on_plugin_load_error)
        bus.on([Events.PLUGIN_RELOADED], _on_plugin_reloaded)
        bus.on([Events.CONTINUITY_TASK_ERROR], _on_task_error)
        bus.on(['sapphire_health_alert'], _on_health_alert)
        bus.on([Events.STT_ERROR], _on_stt_error)
        bus.on([Events.WEBHOOK_FIRED], _on_webhook)
        bus.on([Events.CONTINUITY_TASK_COMPLETE, Events.CONTINUITY_TASK_SKIPPED], _on_task_done)
    except Exception as e:
        logger.error(f"[NOTIFY] bus hooks not installed: {e}")
    try:
        from core.plugin_loader import plugin_loader
        for err in list(plugin_loader.get_load_errors() or []):
            _on_plugin_load_error({'data': err})
    except Exception:
        pass
