"""The plugin's own SQLite — user/plugin_state/discord-personality/personality.sqlite3.

Two tables, nothing shared with the Discord host's DB or Sapphire's Mind:
  people(account, user_id, display_name, facts_json, birthday_mm_dd, updated_at)
  reminders(id, account, channel_id, user_id, text, due_ts, sent)   (S3)
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    account TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    facts_json TEXT NOT NULL DEFAULT '[]',
    birthday_mm_dd TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account, user_id)
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    text TEXT NOT NULL,
    due_ts REAL NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0
);
"""
_BIRTHDAY_RE = re.compile(r'^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')


def default_db_path() -> Path:
    # .absolute(), never .resolve(): a symlinked plugin dir must stay in the tree.
    root = Path(__file__).absolute().parent
    while root != root.parent and not (root / 'core').is_dir():
        root = root.parent
    return root / 'user' / 'plugin_state' / 'discord-personality' / 'personality.sqlite3'


def normalize_birthday(text) -> str | None:
    """'MM-DD' → 'MM-DD'; '' → '' (clear); anything else → None (invalid).
    Accepts 'M/D', 'MM/DD', 'YYYY-MM-DD' too."""
    raw = str(text or '').strip()
    if not raw:
        return ''
    m = re.fullmatch(r'(?:\d{4}-)?(\d{1,2})[-/](\d{1,2})', raw)
    if not m:
        return None
    mm_dd = f'{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    return mm_dd if _BIRTHDAY_RE.match(mm_dd) else None


class People:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_db_path()
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript(SCHEMA)
            self._conn = conn
        return self._conn

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        try:
            d['facts'] = json.loads(d.pop('facts_json') or '[]')
        except ValueError:
            d['facts'] = []
        return d

    def list(self, account: str) -> list[dict]:
        with self._lock:
            rows = self._db().execute('SELECT * FROM people WHERE account = ? ORDER BY display_name, user_id',
                                      (str(account),)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, account: str, user_id: str) -> dict | None:
        with self._lock:
            r = self._db().execute('SELECT * FROM people WHERE account = ? AND user_id = ?',
                                   (str(account), str(user_id))).fetchone()
        return self._row(r) if r else None

    def upsert(self, account: str, user_id: str, *, display_name: str | None = None,
               birthday: str | None = None, facts: list | None = None) -> dict:
        """Create or update; None = leave that column alone."""
        account, user_id = str(account), str(user_id)
        with self._lock:
            db = self._db()
            cur = db.execute('SELECT * FROM people WHERE account = ? AND user_id = ?', (account, user_id)).fetchone()
            name = display_name if display_name is not None else (cur['display_name'] if cur else '')
            bday = birthday if birthday is not None else (cur['birthday_mm_dd'] if cur else '')
            facts_json = json.dumps(list(facts)) if facts is not None else (cur['facts_json'] if cur else '[]')
            db.execute('INSERT INTO people (account, user_id, display_name, facts_json, birthday_mm_dd, updated_at) '
                       'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(account, user_id) DO UPDATE SET '
                       'display_name = excluded.display_name, facts_json = excluded.facts_json, '
                       'birthday_mm_dd = excluded.birthday_mm_dd, updated_at = excluded.updated_at',
                       (account, user_id, str(name or ''), facts_json, str(bday or ''), time.time()))
            db.commit()
        return self.get(account, user_id)

    def delete(self, account: str, user_id: str) -> bool:
        with self._lock:
            cur = self._db().execute('DELETE FROM people WHERE account = ? AND user_id = ?', (str(account), str(user_id)))
            self._db().commit()
        return cur.rowcount > 0

    def with_birthday(self, account: str, mm_dd: str) -> list[dict]:
        with self._lock:
            rows = self._db().execute('SELECT * FROM people WHERE account = ? AND birthday_mm_dd = ?',
                                      (str(account), str(mm_dd))).fetchall()
        return [self._row(r) for r in rows]


_people: People | None = None


def people() -> People:
    global _people
    if _people is None:
        _people = People()
    return _people
