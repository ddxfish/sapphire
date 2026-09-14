"""SQLite bootstrap and connection management."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from pathlib import Path

from plugins.discord.storage.migrations import apply_migrations

logger = logging.getLogger(__name__)


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
        self._bootstrap_schema_version()
        apply_migrations(self._connection)

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

    def _bootstrap_schema_version(self) -> None:
        conn = self._connection
        assert conn is not None
        conn.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)')
        row = conn.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0]
        if row == 0:
            conn.execute('INSERT INTO schema_version(version) VALUES (0)')
        conn.commit()


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

    The old rule returned the legacy path only while the new directory did not
    exist — the first stray file in user/plugin_state/discord/ silently flipped
    the plugin to an empty database. Now: legacy present + new absent → the
    file (with its -wal/-shm sidecars) moves to the new home and stays there;
    both present → the new one wins and the leftover is named in a warning.
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
        for suffix in ('', '-wal', '-shm'):
            src = legacy_path.with_name(legacy_path.name + suffix)
            if src.exists():
                shutil.move(str(src), str(default_path.with_name(default_path.name + suffix)))
        logger.info('[DISCORD] adopted legacy database %s → %s', legacy_path, default_path)
    except OSError as exc:
        logger.error('[DISCORD] could not move legacy database %s: %s', legacy_path, exc)
