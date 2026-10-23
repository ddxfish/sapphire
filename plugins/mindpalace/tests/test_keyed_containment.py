"""Keyed-memory containment — librarian never sees key-gated rows
(negspace privacy hunt 2026-08-31, Krem's ruling: never tend keyed rows;
the interactive lane with an explicit key stays untouched).

Behavior tests drive the four batch selectors + the self-pass FEAST shelf
against a real sqlite cursor; the inline SQL sites (dedup partner hydrate,
dedup candidate pool, library migration, ledger preview redaction) are
pinned as source contracts.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from plugins.mindpalace.tools import librarian


SCHEMA = """CREATE TABLE chunks (
    id INTEGER PRIMARY KEY, scope TEXT, layer TEXT, content TEXT,
    label TEXT, favorite INTEGER DEFAULT 0, created TEXT, updated TEXT,
    meta TEXT, private_key TEXT, embedding BLOB, embedding_provider TEXT,
    embedding_dim INTEGER, importance REAL, source TEXT, chunk_index INTEGER)"""


@pytest.fixture
def cur(monkeypatch):
    monkeypatch.setattr(librarian, '_optin_layers', lambda: ())
    conn = sqlite3.connect(':memory:')
    conn.execute(SCHEMA)
    yield conn.cursor()
    conn.close()


def _row(cur, content, layer='events', meta=None, private_key=None,
         embedding=None, created='2026-08-01T00:00:00'):
    cur.execute(
        "INSERT INTO chunks (scope, layer, content, created, meta, "
        "private_key, embedding) VALUES ('s', ?, ?, ?, ?, ?, ?)",
        (layer, content, created,
         json.dumps(meta) if meta else None, private_key, embedding))


def test_sort_batch_excludes_keyed(cur):
    _row(cur, 'public note')
    _row(cur, 'keyed secret', private_key='word')
    got = [r[2] for r in librarian.build_batch(cur, 's', 'all', 10)]
    assert got == ['public note']


def test_temporal_batch_excludes_keyed(cur):
    _row(cur, 'public date', meta={'refers_to_time': 'tomorrow'})
    _row(cur, 'keyed date', meta={'refers_to_time': 'tomorrow'},
         private_key='word')
    got = [r[2] for r in librarian.build_temporal_batch(cur, 's', 10)]
    assert got == ['public date']


def test_link_batch_excludes_keyed(cur):
    _row(cur, 'public mentions Ann', meta={'noun_candidates': ['Ann']})
    _row(cur, 'keyed mentions Bob', meta={'noun_candidates': ['Bob']},
         private_key='word')
    got = [r[2] for r in librarian.build_link_batch(cur, 's', 10)]
    assert got == ['public mentions Ann']


def test_dedup_batch_excludes_keyed(cur):
    _row(cur, 'public embedded', embedding=b'\x00' * 8)
    _row(cur, 'keyed embedded', embedding=b'\x00' * 8, private_key='word')
    got = [r[2] for r in librarian.build_dedup_batch(cur, 's', 10)]
    assert got == ['public embedded']


def test_self_shelf_excludes_keyed(cur, monkeypatch):
    _row(cur, 'public self note', meta={'was_self_layer': 1})
    _row(cur, 'keyed self note', meta={'was_self_layer': 1},
         private_key='word')

    class _FakePT:
        def _get_connection(self):
            class _Ctx:
                def __enter__(_s):
                    return cur.connection
                def __exit__(_s, *a):
                    return False
            return _Ctx()
    monkeypatch.setattr(librarian, '_pt', lambda: _FakePT())
    got = [r[2] for r in librarian._self_shelf_rows('s')]
    assert got == ['public self note']


# ─── Source contracts for the inline SQL / expression sites ─────────────────

def _src(rel):
    return (Path(__file__).parent.parent / rel).read_text()


def test_dedup_partner_hydrate_filters_keyed():
    """librarian run loop hydrates duplicate PARTNERS by bare id — the 5th
    selector the original report missed. Must carry the key filter."""
    assert 'WHERE private_key IS NULL AND id IN' in _src('tools/librarian.py')


def test_dedup_candidate_pool_filters_keyed():
    src = _src('tools/librarian.py')
    i = src.index('def find_duplicates')
    j = src.index('def ', i + 10)
    assert 'private_key IS NULL' in src[i:j], \
        "candidate pool SELECT lost its key filter"


def test_library_migration_refuses_keyed():
    """Krem's ruling: keyed chunks never promote to (public) library docs."""
    src = _src('tools/library.py')
    i = src.index('def _migrate_v2_chunks')
    assert 'private_key IS NULL' in src[i:i + 1200], \
        "v2->library migration would publish keyed rows"


def test_ledger_preview_redacts_keyed():
    """K3: the save ledger line rides read_self tails into system prompts —
    keyed saves must log '[keyed]', never a content preview."""
    assert '": [keyed]" if private_key else' in _src('tools/palace_tools.py')
