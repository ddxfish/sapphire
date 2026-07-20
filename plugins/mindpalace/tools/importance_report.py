#!/usr/bin/env python3
# plugins/mindpalace/tools/importance_report.py
# Krem-facing CLI: importance-dynamics observation report. Pure stdlib —
# runnable without the app:
#
#   python user/plugins/mindpalace/tools/importance_report.py [--scope X] [--db path]
#
# Prints deltas since the LAST run of this report (movers, decay drift,
# recall activity), then refreshes its snapshot. First run records the
# baseline. Snapshot lives in mind.db (importance_report_snapshot) — the
# report only ever writes its own tables.

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# .absolute(), NOT .resolve() — a symlinked plugin dir would make resolve()
# walk up the wrong tree (standing house rule). Walk to the repo root (the
# dir holding core/) instead of a fixed parent count — the count breaks when
# the plugin moves between user/plugins/ and plugins/ (2026-07-19).
def _repo_root():
    r = Path(__file__).absolute().parent
    while r != r.parent and not (r / 'core').is_dir():
        r = r.parent
    return r

DEFAULT_DB = _repo_root() / 'user' / 'memory' / 'mind.db'


def _connect(db):
    conn = sqlite3.connect(db)
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('''CREATE TABLE IF NOT EXISTS importance_report_snapshot (
        chunk_id INTEGER PRIMARY KEY, importance REAL, recall_count INTEGER)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS importance_report_meta (
        key TEXT PRIMARY KEY, value TEXT)''')
    return conn


def main():
    ap = argparse.ArgumentParser(description='Importance dynamics report')
    ap.add_argument('--scope', help='limit to one scope (default: all)')
    ap.add_argument('--db', default=str(DEFAULT_DB), help='mind.db path')
    args = ap.parse_args()

    if not Path(args.db).exists():
        sys.exit(f"No database at {args.db}")
    conn = _connect(args.db)
    cur = conn.cursor()

    last_run = cur.execute(
        "SELECT value FROM importance_report_meta WHERE key = 'taken_at'").fetchone()
    snap = dict()
    for cid, imp, rc in cur.execute(
            'SELECT chunk_id, importance, recall_count FROM importance_report_snapshot'):
        snap[cid] = (imp, rc or 0)

    where, params = ('WHERE scope = ?', [args.scope]) if args.scope else ('', [])
    rows = cur.execute(
        f'SELECT id, scope, importance, COALESCE(recall_count, 0), last_recalled, '
        f'favorite, content FROM chunks {where}', params).fetchall()

    print(f"◆ Importance dynamics report — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Baseline: {last_run[0][:16] if last_run else 'none (first run — recording one now)'}")
    print()

    # Per-scope summary
    scopes = {}
    for cid, scope, imp, rc, lr, fav, _content in rows:
        s = scopes.setdefault(scope, {'total': 0, 'rated': 0, 'sum': 0.0,
                                      'recalls': 0, 'fav': 0})
        s['total'] += 1
        s['recalls'] += rc - (snap.get(cid, (None, 0))[1])
        if fav:
            s['fav'] += 1
        if imp is not None:
            s['rated'] += 1
            s['sum'] += imp
    for scope in sorted(scopes):
        s = scopes[scope]
        avg = s['sum'] / s['rated'] if s['rated'] else 0
        print(f"  [{scope}] {s['total']} chunks — {s['rated']} rated "
              f"(avg {avg:.3f}), {s['total'] - s['rated']} unrated, "
              f"{s['fav']} favorites, {s['recalls']} recalls since last report")

    # Movers since snapshot
    movers, newly_rated = [], 0
    for cid, scope, imp, rc, lr, fav, content in rows:
        old = snap.get(cid)
        if old is None or old[0] is None:
            if imp is not None and old is not None:
                newly_rated += 1
            continue
        if imp is None:
            continue
        delta = imp - old[0]
        if abs(delta) > 1e-9:
            movers.append((abs(delta), delta, cid, scope, old[0], imp,
                           rc - old[1], content))
    movers.sort(reverse=True)
    print()
    if last_run:
        print(f"  Movers since last report: {len(movers)} "
              f"({sum(1 for m in movers if m[1] > 0)} up, "
              f"{sum(1 for m in movers if m[1] < 0)} down), "
              f"{newly_rated} newly rated")
        for _a, delta, cid, scope, old, new, drc, content in movers[:15]:
            arrow = '↑' if delta > 0 else '↓'
            preview = ' '.join(content.split())[:50]
            print(f"    {arrow} [{cid}] {old:.3f} → {new:.3f} "
                  f"({delta:+.3f}, +{max(drc, 0)} recalls) {preview}")
        if len(movers) > 15:
            print(f"    …and {len(movers) - 15} more")

    # Refresh snapshot
    cur.execute('DELETE FROM importance_report_snapshot')
    cur.executemany(
        'INSERT INTO importance_report_snapshot (chunk_id, importance, recall_count) '
        'VALUES (?, ?, ?)',
        [(cid, imp, rc) for cid, _s, imp, rc, _lr, _f, _c in rows])
    cur.execute("INSERT OR REPLACE INTO importance_report_meta (key, value) VALUES "
                "('taken_at', ?)",
                (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
    conn.commit()
    conn.close()
    print()
    print(f"  Snapshot refreshed ({len(rows)} chunks) — next run reports deltas from now.")


if __name__ == '__main__':
    main()
