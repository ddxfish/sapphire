"""SQLite bootstrap: one locked connection, schema v2, v1 adoption (S7)."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from plugins.discord.storage.schema import DDL, SCHEMA_VERSION

logger = logging.getLogger(__name__)
_SIDECARS = ('', '-wal', '-shm')
_adopt_lock = threading.Lock()


class _LockedConnection:
    """The one plugin connection, shared by the daemon loop, the voice worker
    pool, tool threads and API threads (check_same_thread=False). Every call
    that touches the handle takes one re-entrant lock (M1, hunt 2026-09-12):
    SQLite serialises at the C level, but Python-side statement/cursor
    interleaving across threads is not guaranteed safe. Reads are cheap under
    the lock; WAL keeps readers off the writer's toes at the file level."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._lock:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def rollback(self):
        with self._lock:
            return self._conn.rollback()

    def cursor(self, *args, **kwargs):
        with self._lock:
            return self._conn.cursor(*args, **kwargs)

    def close(self):
        with self._lock:
            return self._conn.close()

    def __enter__(self):
        self._lock.acquire()
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._conn.__exit__(*exc)
        finally:
            self._lock.release()

    def __getattr__(self, name):
        return getattr(self._conn, name)


class SQLiteService:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._connection: _LockedConnection | None = None

    def start(self) -> None:
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        adopt_v1(self.path)
        raw = sqlite3.connect(str(self.path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        # WAL: readers never block the writer and vice versa; NORMAL is the
        # documented durable-enough pairing for WAL; busy_timeout instead of
        # an instant "database is locked" when the Settings UI's own
        # connection (api/storage_access) overlaps a daemon write.
        try:
            raw.execute('PRAGMA journal_mode=WAL')
            raw.execute('PRAGMA synchronous=NORMAL')
            raw.execute('PRAGMA busy_timeout=5000')
        except sqlite3.DatabaseError as exc:
            logger.warning('[DISCORD] SQLite pragmas not applied (%s) — continuing with defaults', exc)
        self._connection = _LockedConnection(raw)
        ensure_schema(self._connection)

    def stop(self) -> None:
        if self._connection is not None:
            self.checkpoint()
            self._connection.close()
            self._connection = None

    def connection(self) -> _LockedConnection:
        if self._connection is None:
            raise RuntimeError('SQLite service not started')
        return self._connection

    def checkpoint(self) -> None:
        """Fold the WAL back into the main file (retention purge, shutdown)."""
        if self._connection is None:
            return
        try:
            self._connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.DatabaseError as exc:
            logger.debug('[DISCORD] wal_checkpoint skipped: %s', exc)


def ensure_schema(conn) -> None:
    """Idempotent: every table IF NOT EXISTS, the version row stamped once."""
    conn.executescript(DDL)
    if conn.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0] == 0:
        conn.execute('INSERT INTO schema_version(version) VALUES (?)', (SCHEMA_VERSION,))
    conn.commit()


def stored_version(path: Path) -> int | None:
    """The schema_version row a database file carries; None for a missing file
    or one with no version table (treated as fresh — ensure_schema fills it)."""
    return _read_only(path, 'SELECT version FROM schema_version LIMIT 1')


def is_v1(path: Path) -> bool:
    """A v1 file = the stacked-migrations schema. Its migration counter (1–13)
    overlaps the v2 number, so the marker is a table only v1 ever created:
    `observations` (v1 migration 1) — v2 never makes one."""
    return path.exists() and bool(_read_only(
        path, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='observations'"))


def _read_only(path: Path, sql: str):
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            row = conn.execute(sql).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None
    return int(row[0]) if row and row[0] is not None else None


def adopt_v1(path: Path) -> dict | None:
    """A v1 database (13 stacked migrations, 31 tables) is moved aside to
    `<name>.pre-2.0` and a fresh v2 file takes its place with the accounts
    rows copied — the tokens are the one thing worth carrying (§9). If the
    copy does not land, the move is undone and the daemon refuses to boot
    rather than coming up with no bots."""
    with _adopt_lock:
        if not is_v1(path):
            return None
        aside = path.with_name(path.name + '.pre-2.0')
        if aside.exists():
            aside = path.with_name(f'{path.name}.pre-2.0.{int(time.time())}')
        _checkpoint_file(path)
        _move(path, aside)
        new = None
        try:
            new = sqlite3.connect(str(path))
            ensure_schema(new)
            new.execute('ATTACH DATABASE ? AS old', (str(aside),))
            expected = int(new.execute('SELECT COUNT(*) FROM old.accounts').fetchone()[0])
            new.execute(
                'INSERT INTO accounts (name, token, bot_name, bot_id, state, last_error, created_at, updated_at) '
                'SELECT name, token, bot_name, bot_id, state, last_error, created_at, updated_at FROM old.accounts'
            )
            new.commit()
            new.execute('DETACH DATABASE old')
            copied = int(new.execute('SELECT COUNT(*) FROM accounts').fetchone()[0])
            new.close()
            new = None
            if copied != expected:
                raise RuntimeError(f'copied {copied} of {expected} accounts')
        except Exception as exc:
            if new is not None:
                try:
                    new.close()
                except Exception:
                    pass
            for suffix in _SIDECARS:
                Path(str(path) + suffix).unlink(missing_ok=True)
            _move(aside, path)
            raise RuntimeError(f'Discord database upgrade to schema v2 failed ({exc}); the v1 file was restored') from exc
        logger.warning('[DISCORD] database upgraded to schema v2: %d account(s) copied; the old file is kept at %s',
                       copied, aside)
        return {'copied': copied, 'aside': str(aside)}


def _checkpoint_file(path: Path) -> None:
    try:
        conn = sqlite3.connect(str(path))
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        logger.debug('[DISCORD] pre-upgrade checkpoint skipped: %s', exc)


def _move(src: Path, dst: Path) -> None:
    for suffix in _SIDECARS:
        s = Path(str(src) + suffix)
        if s.exists():
            shutil.move(str(s), str(dst) + suffix)


def resolve_default_db_path(plugin_name: str = 'discord') -> Path:
    # Walk up with .absolute() (never .resolve(): symlinked plugin dirs would
    # escape the project) until the repo root (the dir holding core/) — a fixed
    # parent count breaks when the plugin moves between plugins/ and user/plugins/.
    root = Path(__file__).absolute().parent
    while root != root.parent and not (root / 'core').is_dir():
        root = root.parent
    root = root / 'user' / 'plugin_state'
    base = root / plugin_name
    default_path = base / 'discord.sqlite3'
    if plugin_name == 'discord':
        _adopt_legacy_db(root / 'discord_cognitive' / 'discord.sqlite3', default_path)
    return default_path


def _adopt_legacy_db(legacy_path: Path, default_path: Path) -> None:
    """Move the pre-rename database into place ONCE (M31, hunt 2026-09-12).

    Legacy present + new absent → the file (with its -wal/-shm sidecars) moves
    to the new home and stays there; both present → the new one wins and the
    leftover is named in a warning.
    """
    if not legacy_path.exists():
        return
    if default_path.exists():
        logger.warning(
            '[DISCORD] both %s and the legacy %s exist — using the new one; the legacy file is not read',
            default_path, legacy_path,
        )
        return
    try:
        default_path.parent.mkdir(parents=True, exist_ok=True)
        _move(legacy_path, default_path)
        logger.info('[DISCORD] adopted legacy database %s → %s', legacy_path, default_path)
    except OSError as exc:
        logger.error('[DISCORD] could not move legacy database %s: %s', legacy_path, exc)
