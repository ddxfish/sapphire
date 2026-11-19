# core/plugins/calendar/store.py - the calendar's own SQLite
"""
One file, user/plugin_state/calendar_events.db: calendars, their links (a
Google calendar or an ICS address feeding one), events, and a few settings.

ONE connection for the life of the process, every use under one lock. The
app has frozen before on helpers that connect and close per call from many
threads (close can wedge behind SQLite's process-wide mutex), and the page
polls, so nothing here opens or closes on a request. Rollback journal, not
WAL: one writer at a time is all a calendar needs.

Events are kept in iCalendar terms so every source maps onto the same row:
uid, start, end, all_day, rrule, exdates, title, location, notes. `source` is
'local' for an event made here, else the id of the link it came from, and
`source_ref` is that event's id at the source. A linked row is a COPY: the
source stays the authority, and sync_rows() makes the copy match it while
keeping each row's own id, so an id she was given keeps meaning that event.

Times are the person's local wall time with no offset ("floating"): start and
end are 'YYYY-MM-DD' for an all-day event and 'YYYY-MM-DDTHH:MM' for a timed
one. An all-day `end` is the LAST day, inclusive (what people say), not the
exclusive DTEND of the wire format: a source adapter converts at its own edge.
`tz` records the zone the event was written in. Only for a linked row whose
zone is not ours does it change anything: api.py turns those occurrences into
local time when it reads them (a 9:00 London meeting is not at 9:00 here).
"""
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from core.paths import PLUGIN_STATE_DIR

logger = logging.getLogger(__name__)

DB_PATH = PLUGIN_STATE_DIR / 'calendar_events.db'
DEFAULT_CALENDAR = 'default'

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendars (
    name        TEXT PRIMARY KEY,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    uid         TEXT NOT NULL,
    calendar    TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'local',
    source_ref  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    start       TEXT NOT NULL,
    end         TEXT NOT NULL,
    all_day     INTEGER NOT NULL DEFAULT 0,
    tz          TEXT NOT NULL DEFAULT '',
    rrule       TEXT NOT NULL DEFAULT '',
    exdates     TEXT NOT NULL DEFAULT '[]',
    location    TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT 'user',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_cal_start ON events (calendar, start);
CREATE TABLE IF NOT EXISTS links (
    id          TEXT PRIMARY KEY,
    calendar    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    ref         TEXT NOT NULL,
    color       INTEGER NOT NULL DEFAULT 1,
    writable    INTEGER NOT NULL DEFAULT 0,
    synced_at   REAL NOT NULL DEFAULT 0,
    win_from    TEXT NOT NULL DEFAULT '',
    win_to      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""

# Columns added after 1.0: a database from before gets them on first open.
_ADDED = (('events', 'guests', "TEXT NOT NULL DEFAULT '[]'"),
          ('events', 'series', "TEXT NOT NULL DEFAULT ''"),
          ('events', 'url', "TEXT NOT NULL DEFAULT ''"))

# What a caller may set. id, uid and the stamps are the store's own.
FIELDS = ('calendar', 'source', 'source_ref', 'title', 'start', 'end', 'all_day', 'tz',
          'rrule', 'exdates', 'location', 'notes', 'created_by', 'guests', 'series', 'url')
LINK_FIELDS = ('calendar', 'kind', 'name', 'ref', 'color', 'writable', 'synced_at', 'win_from', 'win_to', 'error')
_SYNCED = tuple(f for f in FIELDS if f not in ('calendar', 'source', 'source_ref', 'created_by'))
_ID_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'      # no l, o, 0, 1
_ID_LETTERS = 'abcdefghijkmnpqrstuvwxyz'


def _new_id():
    """Six characters, always starting with a letter so it never reads as a number."""
    return secrets.choice(_ID_LETTERS) + ''.join(secrets.choice(_ID_ALPHABET) for _ in range(5))


def _row(r):
    d = dict(r)
    d['all_day'] = bool(d['all_day'])
    try:
        d['exdates'] = [str(x) for x in json.loads(d.get('exdates') or '[]')]
    except (ValueError, TypeError):
        d['exdates'] = []
    try:
        d['guests'] = [g for g in json.loads(d.get('guests') or '[]') if isinstance(g, dict)]
    except (ValueError, TypeError):
        d['guests'] = []
    return d


def _link(r):
    d = dict(r)
    d['writable'] = bool(d['writable'])
    return d


def _pack(row):
    """A caller's event fields as the columns hold them."""
    if 'all_day' in row:
        row['all_day'] = 1 if row['all_day'] else 0
    if 'exdates' in row:
        row['exdates'] = json.dumps(sorted(set(row['exdates'] or [])))
    if 'guests' in row:
        row['guests'] = json.dumps(row['guests'] or [])
    return row


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else DB_PATH
        self._lock = threading.RLock()
        self._conn = None

    def _c(self):
        """The one connection; opened (and the schema made) on first use. Lock held by the caller."""
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            for table, col, spec in _ADDED:
                if col not in {r['name'] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {spec}')
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.warning(f"[calendar] closing the database failed: {e}")
                self._conn = None

    # -- calendars ----------------------------------------------------------

    def calendars(self):
        """Every calendar with its event count; `default` always exists."""
        with self._lock:
            c = self._c()
            counts = {r['calendar']: r['n'] for r in
                      c.execute("SELECT calendar, COUNT(*) AS n FROM events GROUP BY calendar")}
            names = {r['name'] for r in c.execute("SELECT name FROM calendars")}
            names |= {r['calendar'] for r in c.execute("SELECT DISTINCT calendar FROM links")}
        names |= set(counts) | {DEFAULT_CALENDAR}
        ordered = [DEFAULT_CALENDAR] + sorted(n for n in names if n != DEFAULT_CALENDAR)
        return [{'name': n, 'events': counts.get(n, 0)} for n in ordered]

    def has_calendar(self, name):
        return any(c['name'] == name for c in self.calendars())

    def add_calendar(self, name):
        with self._lock:
            c = self._c()
            c.execute("INSERT OR IGNORE INTO calendars (name, created_at) VALUES (?, ?)", (name, time.time()))
            c.commit()

    def delete_calendar(self, name):
        """Remove a calendar, its links and every event in it. Returns how many events went."""
        with self._lock:
            c = self._c()
            n = c.execute("DELETE FROM events WHERE calendar = ?", (name,)).rowcount
            c.execute("DELETE FROM links WHERE calendar = ?", (name,))
            c.execute("DELETE FROM meta WHERE key = ?", (f"target:{name}",))
            c.execute("DELETE FROM calendars WHERE name = ?", (name,))
            c.commit()
            return n

    # -- events -------------------------------------------------------------

    def get(self, event_id):
        with self._lock:
            r = self._c().execute("SELECT * FROM events WHERE id = ?", (str(event_id),)).fetchone()
        return _row(r) if r else None

    def insert(self, fields):
        """Add an event; returns the stored row. The id is new and stable."""
        row = {k: fields[k] for k in FIELDS if k in fields}
        row.setdefault('all_day', False)
        row.setdefault('exdates', [])
        with self._lock:
            c = self._c()
            event_id = self._insert(c, _pack(row))
            c.commit()
        return self.get(event_id)

    def _free_id(self, c, table):
        for _ in range(20):
            new = _new_id()
            if not c.execute(f"SELECT 1 FROM {table} WHERE id = ?", (new,)).fetchone():
                return new
        raise RuntimeError("could not make a free id")

    def _insert(self, c, row):
        """One packed row in; lock held and commit left to the caller."""
        now = time.time()
        row = dict(row, id=self._free_id(c, 'events'), created_at=now, updated_at=now)
        row.setdefault('uid', f"{uuid.uuid4()}@sapphire")
        cols = ', '.join(f'"{k}"' for k in row)
        c.execute(f"INSERT INTO events ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
        return row['id']

    def update(self, event_id, fields):
        """Change the given fields of one event; returns the new row, None if there is no such id."""
        row = _pack({k: fields[k] for k in FIELDS if k in fields})
        row['updated_at'] = time.time()
        sets = ', '.join(f'"{k}" = ?' for k in row)
        with self._lock:
            c = self._c()
            n = c.execute(f"UPDATE events SET {sets} WHERE id = ?", list(row.values()) + [str(event_id)]).rowcount
            c.commit()
        return self.get(event_id) if n else None

    def delete(self, event_id):
        with self._lock:
            c = self._c()
            n = c.execute("DELETE FROM events WHERE id = ?", (str(event_id),)).rowcount
            c.commit()
        return bool(n)

    def candidates(self, first_day, last_day, calendar=None):
        """Rows that can have an occurrence on a day in [first_day, last_day]
        ('YYYY-MM-DD'): one-off events that overlap it, and every repeating
        event that has started by last_day (the caller expands those)."""
        sql = ("SELECT * FROM events WHERE substr(start, 1, 10) <= ? "
               "AND (rrule != '' OR substr(end, 1, 10) >= ?)")
        args = [last_day, first_day]
        if calendar is not None:
            sql += " AND calendar = ?"
            args.append(calendar)
        with self._lock:
            rows = self._c().execute(sql + " ORDER BY start, id", args).fetchall()
        return [_row(r) for r in rows]


    # -- links: a Google calendar or an ICS address feeding one calendar -------

    def links(self, calendar=None):
        sql, args = "SELECT * FROM links", []
        if calendar is not None:
            sql, args = sql + " WHERE calendar = ?", [calendar]
        with self._lock:
            rows = self._c().execute(sql + " ORDER BY created_at, id", args).fetchall()
        return [_link(r) for r in rows]

    def get_link(self, link_id):
        with self._lock:
            r = self._c().execute("SELECT * FROM links WHERE id = ?", (str(link_id),)).fetchone()
        return _link(r) if r else None

    def add_link(self, fields):
        row = {k: fields[k] for k in LINK_FIELDS if k in fields}
        row['writable'] = 1 if row.get('writable') else 0
        with self._lock:
            c = self._c()
            row.update(id=self._free_id(c, 'links'), created_at=time.time())
            cols = ', '.join(f'"{k}"' for k in row)
            c.execute(f"INSERT INTO links ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
            c.commit()
        return self.get_link(row['id'])

    def update_link(self, link_id, fields):
        row = {k: fields[k] for k in LINK_FIELDS if k in fields}
        if 'writable' in row:
            row['writable'] = 1 if row['writable'] else 0
        if not row:
            return self.get_link(link_id)
        sets = ', '.join(f'"{k}" = ?' for k in row)
        with self._lock:
            c = self._c()
            c.execute(f"UPDATE links SET {sets} WHERE id = ?", list(row.values()) + [str(link_id)])
            c.commit()
        return self.get_link(link_id)

    def delete_link(self, link_id):
        """Remove a link and the copies of its events. Returns how many events went."""
        with self._lock:
            c = self._c()
            n = c.execute("DELETE FROM events WHERE source = ?", (str(link_id),)).rowcount
            c.execute("DELETE FROM links WHERE id = ?", (str(link_id),))
            c.commit()
            return n

    def sync_rows(self, link, rows, prune=True):
        """Make the copies of one link's events match `rows` (each carries its
        `source_ref`). A row already here keeps its id; one the source no
        longer has is removed (prune=False: only add and change).
        All or nothing. Returns (added, changed, removed)."""
        added = changed = 0
        with self._lock:
            c = self._c()
            have = {r['source_ref']: dict(r) for r in
                    c.execute("SELECT * FROM events WHERE source = ?", (link['id'],))}
            try:
                seen = set()
                for raw in rows:
                    ref = str(raw.get('source_ref') or '')
                    if not ref or ref in seen:
                        continue
                    seen.add(ref)
                    row = _pack({k: raw[k] for k in _SYNCED if k in raw})
                    old = have.get(ref)
                    if old is None:
                        row.setdefault('all_day', 0)
                        row.update(calendar=link['calendar'], source=link['id'], source_ref=ref,
                                   created_by='link')
                        if raw.get('uid'):
                            row['uid'] = str(raw['uid'])
                        self._insert(c, row)
                        added += 1
                    elif any(old.get(k) != v for k, v in row.items()):
                        row['updated_at'] = time.time()
                        sets = ', '.join(f'"{k}" = ?' for k in row)
                        c.execute(f"UPDATE events SET {sets} WHERE id = ?", list(row.values()) + [old['id']])
                        changed += 1
                gone = [old['id'] for ref, old in have.items() if ref not in seen] if prune else []
                c.executemany("DELETE FROM events WHERE id = ?", [(g,) for g in gone])
                c.commit()
            except Exception:
                c.rollback()
                raise
        return added, changed, len(gone)

    def find(self, source, source_ref):
        with self._lock:
            r = self._c().execute("SELECT * FROM events WHERE source = ? AND source_ref = ?",
                                  (str(source), str(source_ref))).fetchone()
        return _row(r) if r else None

    def series(self, source, series):
        """Every stored occurrence of one repeating event of a link, in order."""
        with self._lock:
            rows = self._c().execute("SELECT * FROM events WHERE source = ? AND series = ? ORDER BY start",
                                     (str(source), str(series))).fetchall()
        return [_row(r) for r in rows]

    # -- small settings -----------------------------------------------------

    def meta(self, key, default=''):
        with self._lock:
            r = self._c().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return r['value'] if r else default

    def set_meta(self, key, value):
        with self._lock:
            c = self._c()
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, str(value)))
            c.commit()


_store = None
_store_guard = threading.Lock()


def get():
    """The process's one Store."""
    global _store
    with _store_guard:
        if _store is None:
            _store = Store()
        return _store


def shutdown():
    """Let go of the file (plugin disabled, reloaded, or Sapphire stopping)."""
    global _store
    with _store_guard:
        s, _store = _store, None
    if s is not None:
        s.close()
