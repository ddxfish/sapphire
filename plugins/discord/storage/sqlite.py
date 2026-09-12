"""SQLite bootstrap and connection management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from plugins.discord.storage.migrations import apply_migrations


class SQLiteService:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    def start(self) -> None:
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._bootstrap_schema_version()
        apply_migrations(self._connection)

    def stop(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError('SQLite service not started')
        return self._connection

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
    if plugin_name == 'discord' and not base.exists():
        legacy_path = root / 'discord_cognitive' / 'discord.sqlite3'
        if legacy_path.exists():
            return legacy_path
    return default_path
