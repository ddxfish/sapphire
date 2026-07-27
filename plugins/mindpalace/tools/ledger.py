# plugins/mindpalace/tools/ledger.py
# The Ledger — append-only change stream over the mind (Ledger v1, 2026-07-12).
#
# Design contract (Krem's rulings, tmp/v3-memory-boost.md):
#   - APPEND-ONLY. This module has no UPDATE or DELETE path and none may be
#     added — rows are deterministic and hash-chain-ready for the future
#     tamper-proof memory (prev_hash/row_hash retrofit cleanly onto a table
#     nothing ever rewrites). The destructors live OUTSIDE this module, both
#     typed-confirm human-only: delete_scope razes a whole scope, ledger
#     included ("ALL of it goes"); the clear_ledger maintenance action razes
#     one scope's ledger history and records the clearing as the fresh
#     ledger's first row.
#   - FAILURE-ISOLATED. record() never raises — a ledger bug must never block
#     the memory operation it describes.
#   - Rows ride the CALLER'S transaction when a cursor is passed (the row
#     commits/rolls back atomically with the op); standalone mode opens its
#     own connection.
#
# actor:  user | ai | librarian | import | system
# action: saved|edited|removed|deleted|retired|restored|merged|promoted|
#         atomized|linked|imported|pass|favorite|unfavorite
# parent_id: librarian pass children point at their pass row.

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now():
    """Ledger timestamps carry microseconds (unlike pt._now's seconds): the
    unread window is `ts > last_read_ts`, and at seconds precision a change
    landing in the same second as her read would be swallowed. Same ISO-UTC
    format family — lexicographic order stays chronological."""
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')

SUMMARY_MAX = 300
TAIL_LINES = 8        # read_self tail: last N qualifying lines
TAIL_CHARS = 640      # ...hard-capped at this many chars
TAIL_LINE_CHARS = 80  # ...and each line capped too: the tail is an OVERVIEW
                      # (~all 10 lines visible) — read_ledger holds the detail

# The read_self tail and the "while you were away" unread count share one
# exclusion rule: her own routine 'ai' rows stay out (she was there when they
# happened) and librarian pass CHILDREN collapse into their parent line.
SHEET_WHERE = "actor != 'ai' AND parent_id IS NULL"


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def preview(text, n=60):
    text = ' '.join((text or '').split())
    return text[:n] + ('…' if len(text) > n else '')


def record(scope, actor, action, layer=None, target=None, summary='',
           detail=None, parent_id=None, cursor=None):
    """Append one ledger row. Returns the row id, or None on failure —
    logged, never raised."""
    try:
        pt = _pt()
        row = (_now(), scope, actor, action, layer,
               str(target) if target is not None else None,
               (summary or '')[:SUMMARY_MAX],
               json.dumps(detail, ensure_ascii=False) if detail else None,
               parent_id)
        sql = ('INSERT INTO ledger (ts, scope, actor, action, layer, target, '
               'summary, detail, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
        if cursor is not None:
            cursor.execute(sql, row)
            return cursor.lastrowid
        with pt._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, row)
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"[MINDPALACE] Ledger write skipped ({actor}/{action}): {e}")
        return None


def mark_read(cursor, scope):
    """Stamp the watermark: she has read this scope's ledger. Powers the
    'while you were away' unread window. Never raises."""
    try:
        cursor.execute(
            'INSERT INTO ledger_reads (scope, last_read_ts) VALUES (?, ?) '
            'ON CONFLICT(scope) DO UPDATE SET last_read_ts = excluded.last_read_ts',
            (scope, _now()))
    except Exception as e:
        logger.warning(f"[MINDPALACE] Ledger watermark skipped: {e}")


def last_read_ts(cursor, scope):
    try:
        row = cursor.execute('SELECT last_read_ts FROM ledger_reads WHERE scope = ?',
                             (scope,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def tail_block(cursor, scope, depth=2):
    """The read_self tail: newest first, capped by depth — 4 lines at wake
    depth ≤1, TAIL_LINES/TAIL_CHARS at depth 2 (Krem's trim, 2026-07-27:
    the tail is a glance, read_ledger is the stream). Returns '' when
    nothing qualifies. Degrades to '' on any error — the ledger can never
    break a working read_self."""
    try:
        n_lines = TAIL_LINES if depth >= 2 else 4
        n_chars = TAIL_CHARS if depth >= 2 else 360
        total = cursor.execute(
            f'SELECT COUNT(*) FROM ledger WHERE scope = ? AND {SHEET_WHERE}',
            (scope,)).fetchone()[0]
        if not total:
            return ''
        rows = cursor.execute(
            f"SELECT l.ts, l.actor, l.summary, "
            f"  (SELECT r.summary FROM ledger r WHERE r.parent_id = l.id "
            f"   AND r.action = 'report' ORDER BY r.id DESC LIMIT 1) "
            f"FROM ledger l WHERE l.scope = ? AND {SHEET_WHERE} "
            f"ORDER BY l.id DESC LIMIT ?", (scope, n_lines)).fetchall()
        head = "◆ Ledger (newest first)"
        lines, used = [], len(head)
        shown = 0
        for ts, actor, summary, report in rows:
            line = f"- [{(ts or '')[:10]}] {actor}: {report or summary}"
            if len(line) > TAIL_LINE_CHARS:
                line = line[:TAIL_LINE_CHARS - 1] + '…'
            if used + len(line) + 1 > n_chars:
                break
            lines.append(line)
            used += len(line) + 1
            shown += 1
        if not lines:
            return ''
        if total > shown:
            lines.append(f"…and {total - shown} more via read_ledger")
        return "\n".join([head] + lines)
    except Exception as e:
        logger.warning(f"[MINDPALACE] Ledger tail skipped (sheet unaffected): {e}")
        return ''


def read_block(cursor, scope, count=20, since_ts=None):
    """The read_ledger tool body: the full stream, newest first. Unlike the
    tail this shows EVERY actor (her own routine rows included — she may be
    checking her own trail) with uncapped summaries; librarian pass children
    still collapse into their parent pass line. Every line leads with its row
    [id] — the handle read_ledger(id=…) dives on. Returns (text, shown)."""
    where = 'l.scope = ? AND l.parent_id IS NULL'
    params = [scope]
    if since_ts:
        where += ' AND l.ts > ?'
        params.append(since_ts)
    total = cursor.execute(f'SELECT COUNT(*) FROM ledger l WHERE {where}',
                           params).fetchone()[0]
    if not total:
        return '', 0
    # Append-only overlays: the newest 'noted' child (post-hoc human
    # annotation) rides the line; the newest 'report' child (a librarian
    # run's after-action) REPLACES a run row's "running…" summary.
    rows = cursor.execute(
        f"SELECT l.id, l.ts, l.actor, l.action, l.layer, l.target, l.summary, "
        f"  (SELECT n.summary FROM ledger n WHERE n.parent_id = l.id "
        f"   AND n.action = 'noted' ORDER BY n.id DESC LIMIT 1), "
        f"  (SELECT r.summary FROM ledger r WHERE r.parent_id = l.id "
        f"   AND r.action = 'report' ORDER BY r.id DESC LIMIT 1) "
        f"FROM ledger l WHERE {where} ORDER BY l.id DESC LIMIT ?",
        params + [count]).fetchall()
    lines = []
    for rid, ts, actor, action, layer, target, summary, note, report in rows:
        when = (ts or '')[:16].replace('T', ' ')
        bits = f"{actor} {action}" + (f" ({layer})" if layer else '')
        line = f"- [{rid}] {when} · {bits}: {report or summary}"
        if note:
            line += f" ({note})"
        lines.append(line)
    if total > len(rows):
        lines.append(f"…and {total - len(rows)} more")
    return "\n".join(lines), len(rows)


DETAIL_VALUE_CHARS = 4000   # per rendered detail value in the id= deep view
DETAIL_CHILD_LINES = 15     # pass children shown before "…and K more"


def _clip(text, n=DETAIL_VALUE_CHARS):
    text = str(text)
    return text if len(text) <= n else text[:n] + '… [truncated]'


def detail_block(cursor, scope, ids):
    """read_ledger(id=…): the full rows behind the stream lines — every
    column plus the detail JSON rendered readable (field diffs as old → new,
    before/after/reason for prompt rows) and any librarian-pass children.
    Scope-guarded: another scope's ids read as not found. Returns
    (text, shown)."""
    out, shown = [], 0
    for rid in ids:
        row = cursor.execute(
            'SELECT id, ts, actor, action, layer, target, summary, detail '
            'FROM ledger WHERE id = ? AND scope = ?', (rid, scope)).fetchone()
        if not row:
            out.append(f"[{rid}] — no such entry in this scope")
            out.append('')
            continue
        rid, ts, actor, action, layer, target, summary, detail = row
        when = (ts or '')[:16].replace('T', ' ')
        head = f"[{rid}] {when} · {actor} {action}" + (f" ({layer})" if layer else '')
        if target:
            head += f" → {target}"
        out.append(head)
        if summary:
            out.append(f"  {summary}")
        note = cursor.execute(
            "SELECT summary FROM ledger WHERE parent_id = ? AND scope = ? "
            "AND action = 'noted' ORDER BY id DESC LIMIT 1",
            (rid, scope)).fetchone()
        if note:
            out.append(f"  noted later — {note[0]}")
        report = cursor.execute(
            "SELECT summary FROM ledger WHERE parent_id = ? AND scope = ? "
            "AND action = 'report' ORDER BY id DESC LIMIT 1",
            (rid, scope)).fetchone()
        if report:
            out.append(f"  after-action — {report[0]}")
        if detail:
            try:
                d = json.loads(detail)
            except Exception:
                d = None
            if isinstance(d, dict):
                fields = d.pop('fields', None)
                if isinstance(fields, dict):
                    for k in sorted(fields):
                        pair = fields[k]
                        old, new = (pair if isinstance(pair, (list, tuple))
                                    and len(pair) == 2 else ('', pair))
                        out.append(f'  {k}: "{_clip(old)}" → "{_clip(new)}"')
                for key in ('reason', 'before', 'after'):
                    if key in d:
                        out.append(f"  {key}: {_clip(d.pop(key))}")
                for k, v in d.items():
                    out.append(f"  {k}: {_clip(json.dumps(v, ensure_ascii=False))}")
            else:
                out.append(f"  detail: {_clip(detail)}")
        kid_total = cursor.execute(
            'SELECT COUNT(*) FROM ledger WHERE parent_id = ? AND scope = ?',
            (rid, scope)).fetchone()[0]
        if kid_total:
            kids = cursor.execute(
                'SELECT id, action, summary FROM ledger '
                'WHERE parent_id = ? AND scope = ? ORDER BY id LIMIT ?',
                (rid, scope, DETAIL_CHILD_LINES)).fetchall()
            out.append(f"  children ({kid_total}):")
            for kid, kaction, ksummary in kids:
                out.append(f"    [{kid}] {kaction}: {preview(ksummary, 70)}")
            if kid_total > len(kids):
                out.append(f"    …and {kid_total - len(kids)} more")
        shown += 1
        out.append('')
    while out and not out[-1]:
        out.pop()
    return "\n".join(out), shown
