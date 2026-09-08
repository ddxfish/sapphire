# plugins/mindpalace/tools/prompt_audit.py
# Prompt-ledger sink (2026-07-22, buffer-and-flush rework same day) —
# receives core.audit change events and records them into the scope ledgers
# that watch the affected prompt.
#
# North star: she can always answer "was I tampered with?" from read_ledger.
# The RECORD is automatic and unconditional; the REASON is optional
# annotation — "no reason given" is itself information.
#
# Routing: an event lands in every scope whose watched prompt it touches —
# COALESCE(watched_prompt, prompt) per mind_scopes row. Monolith events match
# by name; activation events carry the preset they changed (an anonymous
# 'custom'/'unknown' working preset matches any assembled watch — over-log
# beats under-log); component content-edits check preset containment via
# prompts.get_prompt, also over-logging when unanswerable.
#
# APPEND-ONLY, honored fully (Krem's B+D ruling): the ledger is never
# UPDATEd. Autosave spam is folded by BUFFERING instead — consecutive edits
# to one (scope, target, actor) accumulate in memory and land as ONE INSERT
# when the 30-min window closes. Flush triggers: window expiry (checked
# lazily on every event), any ledger read for that scope (a reader never
# misses an in-flight edit), and process shutdown (atexit). A session whose
# content ends where it started (type-then-revert) flushes to nothing.
# Events run on core.audit's worker thread — never the event loop.

import atexit
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

WINDOW_MINUTES = 30
CONTENT_CHARS = 20_000   # before/after clamp in detail (monoliths are big)

_buf_lock = threading.Lock()
_open = {}   # (scope, target, actor) -> {head, before, after, reason, last}


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _lg():
    from plugins.mindpalace.tools import ledger
    return ledger


def _clip(text):
    text = str(text or '')
    return text if len(text) <= CONTENT_CHARS else text[:CONTENT_CHARS] + '… [truncated]'


def _now():
    return datetime.now(timezone.utc)


def _watchers(cursor):
    """[(scope, watched_prompt_name)] for every scope that watches one.
    A scope whose prompt_ledger toggle is off ('0') watches nothing — the
    flip itself is recorded by put_resident, so a blind window always
    STARTS with a visible row (silent disablement would be the one tamper
    the ledger couldn't witness)."""
    rows = cursor.execute(
        "SELECT name, COALESCE(watched_prompt, prompt) FROM mind_scopes "
        "WHERE COALESCE(prompt_ledger, '1') != '0'"
    ).fetchall()
    return [(n, w) for n, w in rows if w]


def _covers(watched, event):
    """Does this event touch the watched prompt? Unanswerable → True."""
    kind = event.get('kind')
    try:
        if kind == 'monolith':
            return event.get('name') == watched
        from core import prompts
        if kind == 'activation':
            pname = event.get('prompt')
            if pname == watched:
                return True
            # Anonymous working preset ('custom'/'unknown'): her live piece
            # toggles don't carry the watched name even when the running
            # persona derives from it (scout find 2026-07-22). If the watch
            # is an assembled prompt, take the row — over-log beats a silent
            # gap in the tamper trail.
            if pname in (None, '', 'custom', 'unknown'):
                p = prompts.get_prompt(watched)
                return bool(p) and p.get('type') == 'assembled'
            return False
        if kind == 'vault':
            # Names-only vault mutations (core.prompt_vault._audit_row). A
            # whole prompt: hers iff it IS the watched name. A piece: hers
            # iff her preset contains it — same containment as an edit.
            # Before this branch a vault event fell through to the component
            # path below, where a nameless key matched a nameless lookup and
            # 120 of Krem's moves landed in her ledger (2026-09-08).
            item = event.get('item')
            if item in ('monolith', 'preset'):
                return event.get('name') == watched
            if item != 'piece':
                return False
        # Component content edit (or vault piece): does the watched preset
        # contain the piece? An event that names no piece is not
        # "unanswerable" — it's not about her. Never a vacuous None == None.
        if not event.get('comp_type') or not event.get('key'):
            return False
        p = prompts.get_prompt(watched)
        if not p:
            return True                      # unknown prompt → over-log
        if p.get('type') != 'assembled':
            return False                     # a monolith holds no pieces
        v = (p.get('components') or {}).get(event.get('comp_type'))
        if isinstance(v, (list, tuple)):
            return event.get('key') in v
        return v is not None and v == event.get('key')
    except Exception:
        return True                          # over-log beats under-log


def _describe(event):
    """(action, target, summary_head) — summary gets ': <reason>' appended."""
    kind = event.get('kind')
    key, ct = event.get('key'), event.get('comp_type')
    if kind == 'monolith':
        name = event.get('name')
        if not (event.get('after') or ''):
            return 'removed', f'monolith/{name}', f'prompt "{name}" deleted'
        if not (event.get('before') or ''):
            return 'saved', f'monolith/{name}', f'prompt "{name}" created'
        return 'edited', f'monolith/{name}', f'prompt "{name}" edited'
    if kind == 'vault':
        # Names only, never content: mind.db is plaintext. 'saved' covers
        # both a move-in and an in-vault edit (the vault can't diff).
        item, act = event.get('item'), event.get('action')
        verb = {'saved': 'saved in the vault', 'deleted': 'deleted from the vault',
                'restored': 'restored in the vault'}.get(act, f'{act or "changed"} (vault)')
        action = 'saved' if act == 'saved' else ('removed' if act == 'deleted' else (act or 'edited'))
        if item == 'piece':
            return action, f'vault/{ct}/{key}', f'prompt piece "{key}" ({ct}) {verb}'
        name = event.get('name')
        return action, f'vault/{item}/{name}', f'prompt "{name}" {verb}'
    if kind == 'activation':
        pname = event.get('prompt') or 'current prompt'
        tgt = f'activation/{ct}/{key}'
        if event.get('active'):
            ttl = event.get('ttl_minutes')
            head = (f'prompt piece "{key}" added to "{pname}"'
                    + (f' for {ttl}m' if ttl else ''))
            return 'saved', tgt, head
        return 'removed', tgt, f'prompt piece "{key}" removed from "{pname}"'
    # component content edit
    tgt = f'component/{ct}/{key}'
    if not (event.get('after') or ''):
        return 'removed', tgt, f'prompt piece "{key}" ({ct}) deleted'
    if not (event.get('before') or ''):
        return 'saved', tgt, f'prompt piece "{key}" ({ct}) created'
    return 'edited', tgt, f'prompt piece "{key}" ({ct}) edited'


def handle(event):
    """The registered sink (runs on the audit worker thread). Must never
    raise into core — evidence, not a gate. One event → buffered edits or
    0..N appended ledger rows (one per watching scope)."""
    try:
        _handle(event)
    except Exception as e:
        logger.warning(f"[MINDPALACE] prompt audit record dropped: {e}")


def _handle(event):
    pt = _pt()
    if not pt._ensure_db():
        return
    flush()   # lazy sweep: close any session whose window expired
    if event.get('kind') == 'reason':
        return _apply_reason(event)
    action, target, head = _describe(event)
    actor = event.get('actor') or 'user'
    reason = (event.get('reason') or '').strip() or None
    with pt._get_connection() as conn:
        cur = conn.cursor()
        for scope, watched in _watchers(cur):
            if not _covers(watched, event):
                continue
            key = (scope, target, actor)
            if action == 'edited':
                with _buf_lock:
                    buf = _open.get(key)
                    if buf is None:
                        _open[key] = {'head': head,
                                      'before': _clip(event.get('before')),
                                      'after': _clip(event.get('after')),
                                      'reason': reason, 'last': _now()}
                    else:
                        buf['after'] = _clip(event.get('after'))
                        buf['head'] = head
                        buf['last'] = _now()
                        if reason:
                            buf['reason'] = reason   # last typed why wins
                continue
            # saved/removed: close any open session on this key first so the
            # rows land in true order, then append directly.
            _flush_key(key, cur)
            detail = {'before': _clip(event.get('before')),
                      'after': _clip(event.get('after'))}
            if reason:
                detail['reason'] = reason
            if event.get('ttl_minutes'):
                detail['ttl_minutes'] = event['ttl_minutes']
            _lg().record(scope, actor, action, layer='prompt', target=target,
                         summary=head + ': ' + (reason or 'no reason given'),
                         detail=detail, cursor=cur)
        conn.commit()


def _apply_reason(event):
    """kind='reason' — a why arriving on its own (the UI's ✓ commit). A
    reason typed AFTER the last content keystroke produces no saver diff, so
    without this it never left the browser (Krem's live find, 2026-07-23).
    Open buffered session → set its reason. Already flushed (she read
    mid-session) → append a 'noted' child on that row, same machinery as the
    pencil — append-only holds. Nothing recent to attach to → drop."""
    pt, lg = _pt(), _lg()
    reason = (event.get('reason') or '').strip()
    if not reason:
        return
    actor = event.get('actor') or 'user'
    if event.get('name'):
        probe = {'kind': 'monolith', 'name': event['name']}
        target = f"monolith/{event['name']}"
    else:
        probe = {'kind': 'component', 'comp_type': event.get('comp_type'),
                 'key': event.get('key')}
        target = f"component/{event.get('comp_type')}/{event.get('key')}"
    cutoff = _now() - timedelta(minutes=WINDOW_MINUTES)
    with pt._get_connection() as conn:
        cur = conn.cursor()
        for scope, watched in _watchers(cur):
            if not _covers(watched, probe):
                continue
            key = (scope, target, actor)
            with _buf_lock:
                buf = _open.get(key)
                if buf is not None:
                    buf['reason'] = reason
                    buf['last'] = _now()
                    continue
            row = cur.execute(
                "SELECT id, ts, detail FROM ledger WHERE scope = ? "
                "AND layer = 'prompt' AND target = ? AND actor = ? "
                "AND parent_id IS NULL ORDER BY id DESC LIMIT 1",
                (scope, target, actor)).fetchone()
            if not row:
                continue
            try:
                if datetime.fromisoformat(row[1]) < cutoff:
                    continue   # too old — a why belongs near its change (✏ exists)
            except Exception:
                continue
            eff = None   # effective reason: newest note wins, else the row's own
            try:
                eff = (json.loads(row[2]) or {}).get('reason') if row[2] else None
            except Exception:
                pass
            note = cur.execute(
                "SELECT detail FROM ledger WHERE parent_id = ? "
                "AND action = 'noted' ORDER BY id DESC LIMIT 1",
                (row[0],)).fetchone()
            if note:
                try:
                    eff = (json.loads(note[0]) or {}).get('reason') if note[0] else None
                except Exception:
                    eff = None
            if eff == reason:
                continue   # already says this — no duplicate note
            lg.record(scope, actor, 'noted', layer='prompt', target=row[0],
                      parent_id=row[0], summary=f'reason: {reason}',
                      detail={'reason': reason}, cursor=cur)
        conn.commit()


def _flush_key(key, cursor):
    """Append one ledger row for a buffered session (caller's cursor).
    A session that ended where it started writes nothing."""
    with _buf_lock:
        buf = _open.pop(key, None)
    if buf is None:
        return
    if buf['before'] == buf['after']:
        return   # type-then-revert: no net change, no evidence to record
    scope, target, actor = key
    detail = {'before': buf['before'], 'after': buf['after']}
    if buf.get('reason'):
        detail['reason'] = buf['reason']
    _lg().record(scope, actor, 'edited', layer='prompt', target=target,
                 summary=buf['head'] + ': ' + (buf.get('reason') or 'no reason given'),
                 detail=detail, cursor=cursor)


def flush(scope=None, force=False):
    """Close buffered editing sessions: expired ones by default, all (or all
    for one scope) when force=True. Called lazily on every event, from the
    ledger read paths (so a reader never misses an in-flight edit), and at
    process exit. Never raises."""
    try:
        cutoff = _now() - timedelta(minutes=WINDOW_MINUTES)
        with _buf_lock:
            due = [k for k, b in _open.items()
                   if (force or b['last'] <= cutoff)
                   and (scope is None or k[0] == scope)]
        if not due:
            return
        pt = _pt()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            for key in due:
                _flush_key(key, cur)
            conn.commit()
    except Exception as e:
        logger.warning(f"[MINDPALACE] prompt audit flush failed "
                       f"(sessions still buffered): {e}")


atexit.register(lambda: flush(force=True))

try:
    from core.audit import register_sink as _register_audit_sink
    _register_audit_sink('mindpalace', handle)
except Exception as _e:
    logger.warning(f"[MINDPALACE] audit sink registration failed: {_e}")
