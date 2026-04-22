"""Sapphire-killer regression guards for _repair_db (A + B fixes 2026-04-22).

Pre-rewrite:
  1. Renamed the live DB to .corrupted BEFORE verifying salvage worked.
     Power-cut / salvage-failure = user boots to empty Sapphire, data only
     recoverable via shell from .corrupted sibling.
  2. Salvage dropped embedding, embedding_provider, embedding_dim, private_key
     columns. Private memories became PUBLIC after recovery (cross-persona
     leak). All vector search went blind until re-embed.
  3. Second corruption event silently overwrote first .corrupted backup via
     Path.rename (Linux overwrites).

Post-rewrite:
  - Builds fresh DB at .db.new, verifies row count + integrity, THEN swaps.
  - SELECT tiers include all 2.6 columns. CREATE + INSERT preserve them.
  - Prior .corrupted backups get timestamp-suffixed.
"""
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch
import pytest


@pytest.fixture
def corrupt_db_with_data(tmp_path):
    """Create a DB that has real 2.6-schema data + is readable (not actually
    corrupt — we'll call _repair_db manually and verify preservation behavior)."""
    db = tmp_path / "test_memory.db"
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute('''
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            importance INTEGER DEFAULT 5,
            keywords TEXT,
            context TEXT,
            scope TEXT NOT NULL DEFAULT 'default',
            label TEXT,
            embedding BLOB,
            embedding_provider TEXT,
            embedding_dim INTEGER,
            private_key TEXT
        )
    ''')
    cur.execute(
        "INSERT INTO memories (content, scope, label, embedding, "
        "embedding_provider, embedding_dim, private_key) VALUES (?,?,?,?,?,?,?)",
        ("public memory", "default", "personal", b'fake_vec', "test-provider", 384, None)
    )
    cur.execute(
        "INSERT INTO memories (content, scope, label, embedding, "
        "embedding_provider, embedding_dim, private_key) VALUES (?,?,?,?,?,?,?)",
        ("private intimate memory", "default", "private", b'fake_vec2', "test-provider", 384, "secret-word")
    )
    conn.commit()
    conn.close()
    return db


def test_repair_preserves_private_key_column(corrupt_db_with_data):
    """B fix — private_key must survive salvage. Pre-rewrite it was dropped,
    making private memories public after recovery."""
    from plugins.memory.tools.memory_tools import _repair_db

    _repair_db(corrupt_db_with_data)

    # After repair, the DB should still exist at the same path with private_key intact.
    assert corrupt_db_with_data.exists(), "Fresh DB should exist at original path"
    conn = sqlite3.connect(corrupt_db_with_data)
    cur = conn.cursor()
    cur.execute("SELECT content, private_key FROM memories WHERE content = ?",
                ("private intimate memory",))
    row = cur.fetchone()
    conn.close()
    assert row is not None, "Private memory row must survive salvage"
    assert row[1] == "secret-word", \
        f"private_key must be preserved, got {row[1]!r} (cross-persona leak if NULL)"


def test_repair_preserves_embedding_blob(corrupt_db_with_data):
    """B fix — embedding BLOB must survive salvage. Pre-rewrite it was dropped."""
    from plugins.memory.tools.memory_tools import _repair_db

    _repair_db(corrupt_db_with_data)

    conn = sqlite3.connect(corrupt_db_with_data)
    cur = conn.cursor()
    cur.execute("SELECT embedding, embedding_provider, embedding_dim FROM memories "
                "WHERE content = ?", ("public memory",))
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == b'fake_vec', "embedding BLOB must be preserved"
    assert row[1] == "test-provider", "embedding_provider stamp must be preserved"
    assert row[2] == 384, "embedding_dim must be preserved"


def test_repair_preserves_original_on_salvage_failure(tmp_path, monkeypatch):
    """A fix — if salvage throws during the fresh-DB build, the original must
    be UNTOUCHED on disk. Pre-rewrite, the rename-to-.corrupted happened BEFORE
    salvage attempted, so failure = user boots to empty DB."""
    from plugins.memory.tools import memory_tools

    # Build a real DB with data.
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, "
                "timestamp TEXT, importance INTEGER, keywords TEXT, context TEXT, "
                "scope TEXT NOT NULL DEFAULT 'default', label TEXT, embedding BLOB)")
    cur.execute("INSERT INTO memories (content) VALUES ('important stuff')")
    conn.commit()
    conn.close()
    original_bytes = db.read_bytes()

    # Monkey-patch to simulate INSERT blowing up during salvage rebuild
    real_connect = sqlite3.connect
    calls = {"count": 0}

    def failing_connect(path, *a, **kw):
        calls["count"] += 1
        # First call = read from corrupted original (should work).
        # Second call = write to .db.new — we make the INSERT phase blow up.
        if calls["count"] == 1:
            return real_connect(path, *a, **kw)
        conn2 = real_connect(path, *a, **kw)
        orig_execute = conn2.cursor().execute

        class BoomCursor:
            def __init__(self, c):
                self._c = c
            def execute(self, sql, *args, **kwargs):
                if sql.startswith("INSERT INTO memories"):
                    raise RuntimeError("simulated salvage failure")
                return self._c.execute(sql, *args, **kwargs)
            def fetchone(self):
                return self._c.fetchone()
            def fetchall(self):
                return self._c.fetchall()

        class BoomConn:
            def __init__(self, c):
                self._c = c
            def cursor(self):
                return BoomCursor(self._c.cursor())
            def commit(self):
                return self._c.commit()
            def close(self):
                return self._c.close()

        return BoomConn(conn2)

    monkeypatch.setattr(sqlite3, "connect", failing_connect)
    memory_tools._repair_db(db)

    # Original must still exist, with original bytes, untouched.
    assert db.exists(), "Original DB must NOT have been renamed when salvage failed"
    assert db.read_bytes() == original_bytes, "Original DB bytes must be unchanged"


def test_repair_timestamps_second_corrupted_backup(tmp_path, monkeypatch):
    """A fix (claim 1f) — second corruption must NOT overwrite first .corrupted
    backup. Pre-rewrite, Path.rename silently clobbered on Linux."""
    from plugins.memory.tools import memory_tools

    # Simulate: a prior .corrupted backup already exists from an earlier event
    db = tmp_path / "m.db"
    prior_backup = tmp_path / "m.db.corrupted"
    prior_backup.write_bytes(b"precious prior corrupted data")

    # Build a readable DB for this event
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, "
                "timestamp TEXT, importance INTEGER, keywords TEXT, context TEXT, "
                "scope TEXT NOT NULL DEFAULT 'default', label TEXT, embedding BLOB)")
    cur.execute("INSERT INTO memories (content) VALUES ('new event data')")
    conn.commit()
    conn.close()

    memory_tools._repair_db(db)

    # First .corrupted must still have its original bytes
    assert prior_backup.exists(), "Prior .corrupted backup must survive"
    assert prior_backup.read_bytes() == b"precious prior corrupted data", \
        "Prior .corrupted backup must NOT be overwritten by second corruption event"

    # A timestamped .corrupted.YYYYMMDD_HHMMSS should now exist with the new event's original
    ts_backups = list(tmp_path.glob("m.db.corrupted.*"))
    assert len(ts_backups) == 1, f"Expected 1 timestamped backup, got {ts_backups}"


def test_repair_bails_if_all_selects_fail(tmp_path, monkeypatch):
    """A fix — if no SELECT tier can read the table, original gets renamed
    but NO fresh empty DB is created by _repair_db. _ensure_db handles the
    fresh-empty creation on next connection."""
    from plugins.memory.tools import memory_tools

    # Build a DB where the memories table is missing — forces all SELECTs to fail.
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()

    memory_tools._repair_db(db)

    # Original should have been renamed (all tiers failed, unrecoverable)
    assert not db.exists(), "Original should be renamed when salvage impossible"
    assert (tmp_path / "m.db.corrupted").exists(), ".corrupted sibling should exist"


def test_repair_salvages_full_12col_schema(corrupt_db_with_data):
    """B fix — verify widest SELECT is attempted first (captures 12 cols)
    and all columns land in the fresh DB table."""
    from plugins.memory.tools.memory_tools import _repair_db

    _repair_db(corrupt_db_with_data)

    # Fresh DB should have the full 12-column schema
    conn = sqlite3.connect(corrupt_db_with_data)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(memories)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    expected = {'id', 'content', 'timestamp', 'importance', 'keywords', 'context',
                'scope', 'label', 'embedding', 'embedding_provider',
                'embedding_dim', 'private_key'}
    assert expected.issubset(cols), \
        f"Fresh DB missing columns: {expected - cols}"
