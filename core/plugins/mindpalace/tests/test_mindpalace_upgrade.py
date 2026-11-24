"""The update that adds columns to her memory (writer, origin, daily layer)
must leave what was already written exactly as it was. An older database is
made by taking the newer columns away again; every test then lets the palace
open it and compares the rows she had, column by column.

One older step does write into old rows on purpose, and has its own test
(test_migration_infers_added_by_for_legacy_knowledge): a knowledge row with
no `added_by` in its meta is given one. No knowledge row is used here.

Package-path imports on purpose (never re-import a plugin module in a test).
"""
import sqlite3

import pytest

from plugins.mindpalace.tools import palace_tools as pt

NEWER = ('wr_model', 'wr_provider', 'wr_private', 'origin_name', 'origin')
# what a row was before the update: every column the first CREATE TABLE names
CHUNK_COLS = ('id, layer, scope, content, entity_id, tier, chunk_index, source, label, favorite, importance, '
              'mentions, private_key, meta, created, updated, embedding, embedding_provider, embedding_dim')
ENTITY_COLS = 'id, name, scope, kind, mentions, meta, created, updated'


class _NoEmbedder:
    provider_id = "fake"
    available = False


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_name_seen", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _NoEmbedder(), raising=False)
    monkeypatch.setattr(pt, "_instance_name", lambda: 'Sapphire-Purple')
    monkeypatch.setattr(pt, "_instance_id", lambda: 'a' * 32)
    return pt


def _older_db():
    """Her database as the version before left it, with memories in it."""
    assert pt._ensure_db()
    conn = sqlite3.connect(pt._get_db_path())
    conn.execute('DROP INDEX IF EXISTS idx_chunks_wr_private')
    for table in ('chunks', 'entities'):
        for col in NEWER:
            conn.execute(f'ALTER TABLE {table} DROP COLUMN {col}')
    conn.execute('ALTER TABLE ledger DROP COLUMN wr_private')
    conn.execute('DROP TABLE palace_kv')
    conn.execute("INSERT INTO entities (name, scope, kind, mentions, meta, created, updated) "
                 "VALUES ('Ann', 'default', 'person', 3, '{\"email\": \"ann@x.org\"}', '2026-01-02', '2026-01-03')")
    rows = [
        ('events', 'default', 'We walked the pier at dusk.', None, '{"model": "glm-5", "provider": "fw"}', b'\x00\x01\x02\x03'),
        ('people', 'default', 'Ann likes tea — green, never black.', 1, '{}', None),
        ('events', 'work', 'line one\nline two\ttabbed', None, '{not json', b''),
        ('events', 'default', '', None, None, None),
    ]
    for layer, scope, content, entity, meta, emb in rows:
        conn.execute("INSERT INTO chunks (layer, scope, content, entity_id, label, favorite, importance, mentions, meta, "
                     "created, updated, embedding, embedding_provider, embedding_dim) "
                     "VALUES (?, ?, ?, ?, 'tag', 1, 0.5, 2, ?, '2026-01-02T03:04:05', '2026-02-03T04:05:06', ?, 'nomic', 4)",
                     (layer, scope, content, entity, meta, emb))
    conn.commit()
    conn.close()
    pt._db_initialized = False


def _rows():
    conn = sqlite3.connect(pt._get_db_path())
    try:
        return (conn.execute(f'SELECT {CHUNK_COLS} FROM chunks ORDER BY id').fetchall(),
                conn.execute(f'SELECT {ENTITY_COLS} FROM entities ORDER BY id').fetchall())
    finally:
        conn.close()


def _dump():
    """Every row and every table, index and trigger. Sorted: reopening makes the search
    triggers again, which moves them in the listing and changes nothing."""
    conn = sqlite3.connect(pt._get_db_path())
    try:
        return sorted(conn.iterdump())
    finally:
        conn.close()


def _write_locked():
    conn = sqlite3.connect(pt._get_db_path(), timeout=0.2)
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('ROLLBACK')
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        conn.close()


def test_the_update_leaves_every_memory_she_had_exactly_as_it_was(palace):
    _older_db()
    before = _rows()
    assert len(before[0]) == 4 and len(before[1]) == 1
    assert pt._ensure_db()
    assert _rows() == before                        # not one byte of an old row's own columns
    conn = sqlite3.connect(pt._get_db_path())
    cols = {r[1] for r in conn.execute('PRAGMA table_info(chunks)')}
    stamped = conn.execute('SELECT origin, origin_name, wr_private FROM chunks').fetchall()
    conn.close()
    assert set(NEWER) <= cols
    assert stamped == [('a' * 32, 'Sapphire-Purple', 0)] * 4      # hers, and public as they always were


def test_opening_an_updated_database_again_changes_nothing(palace):
    _older_db()
    assert pt._ensure_db()
    once = _dump()
    for _ in range(2):
        pt._db_initialized = False
        assert pt._ensure_db()
    assert _dump() == once


def _die_once(monkeypatch):
    real = pt._follow_instance_name

    def dies(cursor):
        raise RuntimeError('power cut')
    monkeypatch.setattr(pt, '_follow_instance_name', dies)
    assert not pt._ensure_db()                      # it must not claim success
    monkeypatch.setattr(pt, '_follow_instance_name', real)
    pt._db_initialized = False


def test_an_update_that_dies_halfway_is_undone_whole_and_finishes_next_time(palace, monkeypatch):
    _older_db()
    before = _rows()
    _die_once(monkeypatch)
    assert _rows() == before
    conn = sqlite3.connect(pt._get_db_path())
    cols = {r[1] for r in conn.execute('PRAGMA table_info(chunks)')}
    conn.close()
    assert not cols & set(NEWER)                    # no half-made columns: the next try starts clean
    assert pt._ensure_db()
    assert _rows() == before
    conn = sqlite3.connect(pt._get_db_path())
    got = conn.execute('SELECT wr_model, wr_provider, origin_name FROM chunks ORDER BY id').fetchall()
    conn.close()
    assert got[0] == ('glm-5', 'fw', 'Sapphire-Purple')           # the model a row recorded is still carried over
    assert [g[2] for g in got] == ['Sapphire-Purple'] * 4


def test_a_failed_open_does_not_leave_her_memory_locked(palace, monkeypatch):
    # it used to neither roll back nor close: the write lock stayed until Python's garbage collector ran
    _older_db()
    _die_once(monkeypatch)
    assert not _write_locked()


def test_a_database_from_another_sapphire_keeps_its_rows_as_theirs(palace, monkeypatch):
    _older_db()
    assert pt._ensure_db()                          # stamped as 'aaaa...' / Sapphire-Purple
    before = _rows()
    monkeypatch.setattr(pt, '_instance_id', lambda: 'b' * 32)     # the backup is restored on another machine
    monkeypatch.setattr(pt, '_instance_name', lambda: 'Sapphire-Green')
    pt._db_initialized = False
    monkeypatch.setattr(pt, '_name_seen', None, raising=False)
    assert pt._ensure_db()
    assert _rows() == before
    conn = sqlite3.connect(pt._get_db_path())
    marks = set(conn.execute('SELECT origin, origin_name FROM chunks').fetchall())
    conn.close()
    assert marks == {('a' * 32, 'Sapphire-Purple')}               # not claimed, not renamed
