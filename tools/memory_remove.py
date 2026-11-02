#!/usr/bin/env python3
"""memory_remove.py - prune Memory (legacy) rows that mention a phrase.

Usage:
    python tools/memory_remove.py PHRASE [PHRASE ...] [--count] [--yes] [--user-dir DIR]

Looks through the classic memory engine's stores (user/memory.db,
user/knowledge.db, user/goals.db) for rows whose text contains any PHRASE
(case-insensitive) and asks before deleting each one. Mind Palace
(user/memory/) is not touched.

    --count     only count the matches per store; prints no text
    --yes       delete every match without asking
    --scope     only rows in this memory scope (people, knowledge, goals too)
    --user-dir  another user folder (a restored backup, another install)

At the prompt: y = delete, n = keep, a = delete the rest of this store
without asking, q = stop. Each deletion is committed at once; there is no
undo beyond your backups. Stdlib only; safe with Sapphire running (the same
rows and FTS triggers the engine uses).
"""
import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).absolute().parent.parent


def _window(text, phrases, width=400):
    """The row's text, or the part around the first phrase when it is long."""
    text = ' '.join(str(text or '').split())
    if len(text) <= width:
        return text
    low = text.lower()
    pos = min((low.find(p) for p in phrases if p in low), default=0)
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    return ('...' if start else '') + text[start:end] + ('...' if end < len(text) else '')


def _open(path, opts):
    """--count opens read-only (a file: URI with an empty authority, so a
    Windows drive or UNC path works too); deleting opens the normal way."""
    if not opts.count:
        return sqlite3.connect(path, timeout=10)
    posix = Path(path).absolute().as_posix()
    uri = 'file:' + quote(posix if posix.startswith('/') else '/' + posix, safe='/:') + '?mode=ro'
    return sqlite3.connect(uri, uri=True, timeout=10)


def _hit(phrases, *fields):
    text = ' '.join(str(f) for f in fields if f).lower()
    return any(p in text for p in phrases)


def _ask():
    while True:
        try:
            a = input("delete? [y/n/a/q] ").strip().lower()
        except EOFError:
            return 'q'
        if a in ('y', 'n', 'a', 'q'):
            return a


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,)).fetchone() is not None


def _rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params)]


def _store(conn, label, rows, fields, render, delete, opts, state):
    """One store: match, then ask (or count, or just delete) per row."""
    if state['stop']:
        return
    hits = [r for r in rows if _hit(opts.phrases, *(r.get(f) for f in fields))
            and (not opts.scope or r.get('scope') == opts.scope)]
    state['found'] += len(hits)
    if opts.count:
        print(f"{label:<14} {len(hits)}")
        return
    auto = opts.yes
    for r in hits:
        print()
        print(render(r))
        a = 'y' if auto else _ask()
        if a == 'q':
            state['stop'] = True
            return
        if a == 'a':
            auto = True
            a = 'y'
        if a == 'y':
            delete(conn, r)
            conn.commit()
            state['deleted'] += 1
            print("deleted")


def _memory_db(path, opts, state):
    with _open(path, opts) as conn:
        if not _has(conn, 'memories'):
            return
        P = opts.phrases
        _store(conn, 'memories',
               _rows(conn, 'SELECT id, content, context, keywords, label, scope, timestamp FROM memories ORDER BY id'),
               ('content', 'context', 'keywords', 'label'),
               lambda r: (f"[memory #{r['id']}] scope={r['scope']} label={r['label'] or '-'} {str(r['timestamp'] or '')[:10]}\n"
                          f"  {_window(r['content'], P)}"),
               lambda c, r: c.execute('DELETE FROM memories WHERE id = ?', (r['id'],)),
               opts, state)


def _knowledge_db(path, opts, state):
    with _open(path, opts) as conn:
        P = opts.phrases
        if _has(conn, 'people'):
            _store(conn, 'people',
                   _rows(conn, 'SELECT id, name, relationship, phone, email, address, notes, scope FROM people ORDER BY id'),
                   ('name', 'relationship', 'phone', 'email', 'address', 'notes'),
                   lambda r: (f"[person #{r['id']}] scope={r['scope']} {r['name']} ({r['relationship'] or '-'})"
                              f" {r['phone'] or ''} {r['email'] or ''} {r['address'] or ''}\n"
                              f"  {_window(r['notes'], P)}"),
                   lambda c, r: c.execute('DELETE FROM people WHERE id = ?', (r['id'],)),
                   opts, state)
        if _has(conn, 'knowledge_tabs') and _has(conn, 'knowledge_entries'):
            # A matching tab goes whole (name/description are its text); then
            # the entries left standing are matched one by one.
            _store(conn, 'knowledge tabs',
                   _rows(conn, 'SELECT t.id, t.name, t.description, t.scope, '
                               '(SELECT COUNT(*) FROM knowledge_entries e WHERE e.tab_id = t.id) AS n '
                               'FROM knowledge_tabs t ORDER BY t.id'),
                   ('name', 'description'),
                   lambda r: (f"[knowledge tab #{r['id']}] scope={r['scope']} {r['name']} "
                              f"({r['n']} entries go with it)\n  {_window(r['description'], P)}"),
                   lambda c, r: (c.execute('DELETE FROM knowledge_entries WHERE tab_id = ?', (r['id'],)),
                                 c.execute('DELETE FROM knowledge_tabs WHERE id = ?', (r['id'],))),
                   opts, state)
            _store(conn, 'knowledge',
                   _rows(conn, 'SELECT e.id, e.content, e.source_filename, e.chunk_index, t.name AS tab, t.scope '
                               'FROM knowledge_entries e LEFT JOIN knowledge_tabs t ON t.id = e.tab_id ORDER BY e.id'),
                   ('content', 'source_filename'),
                   lambda r: (f"[knowledge #{r['id']}] scope={r['scope']} tab={r['tab']} "
                              f"file={r['source_filename'] or '-'} chunk={r['chunk_index']}\n  {_window(r['content'], P)}"),
                   lambda c, r: c.execute('DELETE FROM knowledge_entries WHERE id = ?', (r['id'],)),
                   opts, state)


def _goals_db(path, opts, state):
    with _open(path, opts) as conn:
        if not _has(conn, 'goals'):
            return
        P = opts.phrases
        notes = _has(conn, 'goal_progress')

        def drop_goal(c, r):
            if notes:
                c.execute('DELETE FROM goal_progress WHERE goal_id = ? OR goal_id IN '
                          '(SELECT id FROM goals WHERE parent_id = ?)', (r['id'], r['id']))
            c.execute('DELETE FROM goals WHERE parent_id = ?', (r['id'],))
            c.execute('DELETE FROM goals WHERE id = ?', (r['id'],))

        def show_goal(r):
            subs = f" (+{r['subs']} subtasks go with it)" if r['subs'] else ''
            return (f"[goal #{r['id']}] scope={r['scope']} {r['status']} {r['title']}{subs}\n"
                    f"  {_window(r['description'], P)}")

        _store(conn, 'goals',
               _rows(conn, 'SELECT id, title, description, status, scope, '
                           '(SELECT COUNT(*) FROM goals s WHERE s.parent_id = goals.id) AS subs FROM goals ORDER BY id'),
               ('title', 'description'), show_goal, drop_goal, opts, state)
        if notes:
            _store(conn, 'goal notes',
                   _rows(conn, 'SELECT p.id, p.note, p.created_at, g.title, g.scope FROM goal_progress p '
                               'LEFT JOIN goals g ON g.id = p.goal_id ORDER BY p.id'),
                   ('note',),
                   lambda r: f"[goal note #{r['id']}] goal={r['title']} {str(r['created_at'] or '')[:10]}\n  {_window(r['note'], P)}",
                   lambda c, r: c.execute('DELETE FROM goal_progress WHERE id = ?', (r['id'],)),
                   opts, state)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Delete Memory (legacy) rows that mention a phrase, one yes/no at a time.")
    ap.add_argument('phrases', nargs='+', metavar='PHRASE')
    ap.add_argument('--count', action='store_true', help='count matches per store, print no text')
    ap.add_argument('--yes', action='store_true', help='delete every match without asking')
    ap.add_argument('--scope', help='only rows in this memory scope')
    ap.add_argument('--user-dir', default=str(ROOT / 'user'), help='user folder (default: this install)')
    opts = ap.parse_args(argv)
    opts.phrases = [p.lower() for p in opts.phrases if p.strip()]
    if not opts.phrases:
        ap.error('an empty phrase matches everything')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')     # Windows consoles, emoji in rows

    user = Path(opts.user_dir)
    state = {'found': 0, 'deleted': 0, 'stop': False}
    for name, run in (('memory.db', _memory_db), ('knowledge.db', _knowledge_db), ('goals.db', _goals_db)):
        path = user / name
        if not path.exists():
            print(f"{name}: not here")
            continue
        try:
            run(path, opts, state)
        except sqlite3.Error as e:
            print(f"{name}: {e}")
            return 1
    if opts.count:
        print(f"total          {state['found']}")
    else:
        print(f"\n{state['found']} matched, {state['deleted']} deleted"
              + (" (stopped early)" if state['stop'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
