# plugins/mindpalace/tools/ledger.py
# The Ledger — append-only change stream over the mind (Ledger v1, 2026-07-12).
#
# Design contract (Krem's rulings, tmp/v3-memory-boost.md):
#   - APPEND-ONLY. This module has no UPDATE or DELETE path and none may be
#     added — rows are deterministic and hash-chain-ready for the future
#     tamper-proof memory (prev_hash/row_hash retrofit cleanly onto a table
#     nothing ever rewrites). The one destructor lives OUTSIDE this module:
#     delete_scope razes a whole scope, ledger included ("ALL of it goes").
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
TAIL_LINES = 10       # read_self tail: last N qualifying lines
TAIL_CHARS = 768      # ...hard-capped at this many chars
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


def tail_block(cursor, scope):
    """The read_self tail: last TAIL_LINES qualifying changes, newest first,
    hard-capped at TAIL_CHARS with an '…and K more' overflow line. Returns ''
    when nothing qualifies. Degrades to '' on any error — the ledger can
    never break a working read_self."""
    try:
        total = cursor.execute(
            f'SELECT COUNT(*) FROM ledger WHERE scope = ? AND {SHEET_WHERE}',
            (scope,)).fetchone()[0]
        if not total:
            return ''
        rows = cursor.execute(
            f'SELECT ts, actor, summary FROM ledger '
            f'WHERE scope = ? AND {SHEET_WHERE} '
            f'ORDER BY id DESC LIMIT ?', (scope, TAIL_LINES)).fetchall()
        head = "◆ Ledger — recent changes to your memory (newest first)"
        lines, used = [], len(head)
        shown = 0
        for ts, actor, summary in rows:
            line = f"- [{(ts or '')[:10]}] {actor}: {summary}"
            if len(line) > TAIL_LINE_CHARS:
                line = line[:TAIL_LINE_CHARS - 1] + '…'
            if used + len(line) + 1 > TAIL_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
            shown += 1
        if not lines:
            return ''
        if total > shown:
            lines.append(f"…and {total - shown} more — read_ledger has the full stream")
        return "\n".join([head] + lines)
    except Exception as e:
        logger.warning(f"[MINDPALACE] Ledger tail skipped (sheet unaffected): {e}")
        return ''


def read_block(cursor, scope, count=20, since_ts=None):
    """The read_ledger tool body: the full stream, newest first. Unlike the
    tail this shows EVERY actor (her own routine rows included — she may be
    checking her own trail) with uncapped summaries; librarian pass children
    still collapse into their parent pass line. Returns (text, shown)."""
    where = 'scope = ? AND parent_id IS NULL'
    params = [scope]
    if since_ts:
        where += ' AND ts > ?'
        params.append(since_ts)
    total = cursor.execute(f'SELECT COUNT(*) FROM ledger WHERE {where}',
                           params).fetchone()[0]
    if not total:
        return '', 0
    rows = cursor.execute(
        f'SELECT ts, actor, action, layer, target, summary FROM ledger '
        f'WHERE {where} ORDER BY id DESC LIMIT ?', params + [count]).fetchall()
    lines = []
    for ts, actor, action, layer, target, summary in rows:
        when = (ts or '')[:16].replace('T', ' ')
        bits = f"{actor} {action}" + (f" ({layer})" if layer else '')
        lines.append(f"- [{when}] {bits}: {summary}")
    if total > len(rows):
        lines.append(f"…and {total - len(rows)} more")
    return "\n".join(lines), len(rows)
