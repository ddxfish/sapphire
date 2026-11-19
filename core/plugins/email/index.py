# core/plugins/email/index.py - mail lists as last seen, on disk (the Email page)
"""
One file, user/plugin_state/email_index.db: for each account and folder, the
list rows the mail server last gave the Email page, the folder's size, and
the account's folder list with its counts.

What it is for: the page paints a folder at once from here, after a reload or
a restart too, and then asks the server and shows what changed. The server
stays the truth; nothing here is ever used to decide what a mail is, and her
tools do not read it.

What is in it: who a mail is from and to (names and addresses), its subject,
date and flags. Never a body, never an attachment. It is plain SQLite, not
inside any chat's vault. The person can turn it off (the `keep_mail_index`
setting), which also deletes the file's contents.

How much: each folder's first page (the newest mails), replaced whole every
time the server lists it. Deeper pages are not kept: a deeper page next to an
older first page would be a list with a hole in it.

ONE connection for the life of the process, every use under one lock, the
same rule as the calendar's store: nothing opens or closes on a request.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from core.paths import PLUGIN_STATE_DIR

logger = logging.getLogger(__name__)

DB_PATH = PLUGIN_STATE_DIR / 'email_index.db'

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rows (
    scope   TEXT NOT NULL,
    folder  TEXT NOT NULL,
    uid     INTEGER NOT NULL,
    data    TEXT NOT NULL,
    PRIMARY KEY (scope, folder, uid)
);
CREATE TABLE IF NOT EXISTS folders (
    scope     TEXT NOT NULL,
    folder    TEXT NOT NULL,
    validity  INTEGER NOT NULL DEFAULT 0,
    total     INTEGER NOT NULL DEFAULT 0,
    low_uid   INTEGER,
    saved_at  REAL NOT NULL,
    PRIMARY KEY (scope, folder)
);
CREATE TABLE IF NOT EXISTS accounts (
    scope     TEXT PRIMARY KEY,
    folders   TEXT NOT NULL,
    saved_at  REAL NOT NULL
);
"""


def _uid(row):
    try:
        return int(str(row.get('id', '')).rpartition(':')[2].partition('~')[0])
    except (ValueError, AttributeError):
        return None


class Index:
    def __init__(self, path=None):
        self.path = Path(path) if path else DB_PATH
        self._lock = threading.RLock()
        self._conn = None

    def _c(self):
        """The one connection; opened (and the tables made) on first use. Lock held by the caller."""
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # What is deleted is overwritten, not just unlinked: "off deletes
            # what was kept" has to be true of the file, not only of the tables.
            conn.execute("PRAGMA secure_delete = ON")
            conn.executescript(_SCHEMA)
            if 'address' not in {r['name'] for r in conn.execute("PRAGMA table_info(folders)")}:
                conn.execute("ALTER TABLE folders ADD COLUMN address TEXT NOT NULL DEFAULT ''")     # a file from 2.4
            conn.commit()
            try:
                os.chmod(self.path, 0o600)           # names and subjects: the owner's eyes only
            except OSError as e:
                logger.debug(f"[EMAIL] mail index permissions not set: {e}")
            self._conn = conn
        return self._conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.warning(f"[EMAIL] closing the mail index failed: {e}")
                self._conn = None

    # -- a folder's list ------------------------------------------------------

    def save_view(self, scope, folder, validity, total, page, per_page, rows, address=''):
        """Keep a folder's FIRST page as the server just listed it (newest
        first, no search). Returns True when it was kept. Only the first
        page: it is what the page opens on, and a deeper page kept next to an
        older first page would be a list with a hole in it."""
        if max(1, int(page or 1)) != 1:
            return False
        kept = []
        for row in rows or []:
            uid = _uid(row)
            if uid is not None:
                kept.append((scope, folder, uid, json.dumps(row, separators=(',', ':'))))
        with self._lock:
            c = self._c()
            try:
                c.execute("DELETE FROM rows WHERE scope = ? AND folder = ?", (scope, folder))
                c.executemany("INSERT OR REPLACE INTO rows (scope, folder, uid, data) VALUES (?, ?, ?, ?)", kept)
                c.execute("INSERT INTO folders (scope, folder, validity, total, low_uid, saved_at, address) "
                          "VALUES (?, ?, ?, ?, NULL, ?, ?) ON CONFLICT(scope, folder) DO UPDATE SET "
                          "validity = excluded.validity, total = excluded.total, saved_at = excluded.saved_at, "
                          "address = excluded.address",
                          (scope, folder, int(validity or 0), int(total or 0), time.time(), str(address or '').lower()))
                c.commit()
                return True
            except Exception:
                c.rollback()
                raise

    def view(self, scope, folder, page=1, per_page=50, address=''):
        """A folder's first page as last seen: {'rows', 'total', 'validity',
        'as_of'}, or None when there is none. `address`: the account as it
        is set up now. A copy kept for another address (the account name was
        pointed at a different mailbox) is dropped, never shown."""
        if max(1, int(page or 1)) != 1:
            return None
        with self._lock:
            c = self._c()
            meta = c.execute("SELECT * FROM folders WHERE scope = ? AND folder = ?", (scope, folder)).fetchone()
            if meta is None:
                return None
            if address and (meta['address'] or '') and meta['address'] != str(address).lower():
                stale = True
            else:
                stale = False
                found = c.execute("SELECT data FROM rows WHERE scope = ? AND folder = ? ORDER BY uid DESC LIMIT ?",
                                  (scope, folder, per_page)).fetchall()
        if stale:
            self.forget(scope)
            return None
        rows = []
        for r in found:
            try:
                rows.append(json.loads(r['data']))
            except ValueError:
                continue
        return {'rows': rows, 'total': meta['total'], 'validity': meta['validity'], 'as_of': meta['saved_at']}

    # -- an account's folder list ------------------------------------------------

    def save_folders(self, scope, folders):
        with self._lock:
            c = self._c()
            c.execute("INSERT INTO accounts (scope, folders, saved_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(scope) DO UPDATE SET folders = excluded.folders, saved_at = excluded.saved_at",
                      (scope, json.dumps(folders, separators=(',', ':')), time.time()))
            c.commit()

    def folders(self, scope):
        """The account's folder list with its counts as last seen, or None."""
        with self._lock:
            r = self._c().execute("SELECT folders FROM accounts WHERE scope = ?", (scope,)).fetchone()
        if r is None:
            return None
        try:
            got = json.loads(r['folders'])
            return got if isinstance(got, list) else None
        except ValueError:
            return None

    # -- forgetting ---------------------------------------------------------------

    def forget(self, scope=None):
        """Drop one account's rows, or everything."""
        with self._lock:
            c = self._c()
            for table in ('rows', 'folders', 'accounts'):
                if scope is None:
                    c.execute(f"DELETE FROM {table}")
                else:
                    c.execute(f"DELETE FROM {table} WHERE scope = ?", (scope,))
            c.commit()
            if scope is None:
                try:
                    c.execute("VACUUM")                  # give the pages back, so nothing lingers in the file
                except sqlite3.Error as e:
                    logger.debug(f"[EMAIL] mail index not compacted: {e}")

    def prune(self, keep):
        """Drop every account that is not in `keep` (it was removed in Settings)."""
        keep = set(keep)
        with self._lock:
            c = self._c()
            have = {r['scope'] for t in ('rows', 'folders', 'accounts')
                    for r in c.execute(f"SELECT DISTINCT scope FROM {t}")}
        for scope in have - keep:
            logger.info(f"[EMAIL] mail index: forgetting the removed account '{scope}'")
            self.forget(scope)

    def empty(self):
        with self._lock:
            c = self._c()
            return not any(c.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in ('rows', 'folders', 'accounts'))


_index = None
_guard = threading.Lock()


def get():
    """The process's one Index."""
    global _index
    with _guard:
        if _index is None:
            _index = Index()
        return _index


def shutdown():
    """Let go of the file (plugin disabled, reloaded, or Sapphire stopping)."""
    global _index
    with _guard:
        i, _index = _index, None
    if i is not None:
        i.close()
