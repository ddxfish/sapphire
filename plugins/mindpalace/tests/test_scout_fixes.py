"""Regression net for the 2026-07-18 scout-sweep fixes:

- watch-refresh stale doc_vectors (rowid reuse served the OLD file's vectors)
- watch scan settle guard (mid-copy files wait one scan; future mtimes serve)
- cross-filed migrated duplicates survive the boot merge
- explicit knowledge search caps (note teaser + total budget)
- delete_scope cascades into the library
- [doc N]/[N] id-namespace guard on update/delete
- memory recall matrix engine (no 10k recency cliff, gen-bump invalidation,
  corrupt-blob tolerance)
- merge rewrite gate (fabrication surface, default OFF)
- fresh librarian session chats
- transfer import strips carried librarian stamps (pre-drained corpus trap)

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import json
import os
import re
import time
import zlib

import numpy as np
import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import librarian
from plugins.mindpalace.tools import librarian_tools as lt
from plugins.mindpalace.tools import library as lib
from plugins.mindpalace.routes import transfer


class _VecEmbedder:
    """Deterministic 64-dim unit vectors seeded from the text — AVAILABLE."""
    provider_id = 'fake:vec'
    available = True

    def embed(self, texts, prefix='search_document'):
        out = []
        for t in texts:
            rng = np.random.default_rng(zlib.crc32(t.encode('utf-8', 'replace')))
            v = rng.standard_normal(64).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return out


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _VecEmbedder(), raising=False)
    monkeypatch.setattr(pt, "_mem_matrix_cache", {}, raising=False)
    return pt


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "_db_path", tmp_path / "library.db", raising=False)
    monkeypatch.setattr(lib, "_db_initialized", False, raising=False)
    monkeypatch.setattr(lib, "_embedder", lambda: _VecEmbedder())
    monkeypatch.setattr(lib, "ensure_worker", lambda: None)
    monkeypatch.setattr(lib, "_matrix_cache", {}, raising=False)
    return lib


def _drain(library, cap=500):
    for _ in range(cap):
        if not library.work_once():
            return


def _no_threads(monkeypatch):
    import threading as _t
    monkeypatch.setattr(_t, 'Thread',
                        lambda *a, **k: type('N', (), {'start': lambda s: None})())


def _save(content, scope='default', **kw):
    msg, ok = pt._save_memory(content, scope, **kw)
    assert ok, msg
    return int(re.search(r'ID: (\d+)', msg).group(1))


# ─── watch refresh: vectors follow the chunks ────────────────────────────────

def test_watch_refresh_replaces_vectors(library, tmp_path, monkeypatch):
    _no_threads(monkeypatch)
    folder = tmp_path / 'vault'
    folder.mkdir()
    f = folder / 'note.md'
    f.write_text('zebra painting original text', encoding='utf-8')
    old = time.time() - 60
    os.utime(f, (old, old))
    fid, err = library.add_watch_folder('default', str(folder))
    assert fid and not err
    library.scan_folder(fid)
    _drain(library)
    with library.get_connection() as conn:
        doc_id = conn.execute('SELECT id FROM documents').fetchone()[0]
        before = {r[0]: r[1] for r in conn.execute(
            'SELECT v.chunk_id, v.q FROM doc_vectors v JOIN doc_chunks c '
            'ON c.id = v.chunk_id WHERE c.doc_id = ?', (doc_id,)).fetchall()}
    assert before
    # Edit the file (backdated past the settle window) → rescan → re-embed.
    f.write_text('quantum harp replacement text', encoding='utf-8')
    os.utime(f, (old + 1, old + 1))
    rep = library.scan_folder(fid)
    assert rep['changed'] == 1
    _drain(library)
    with library.get_connection() as conn:
        chunks = conn.execute('SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ?',
                              (doc_id,)).fetchone()[0]
        after = {r[0]: r[1] for r in conn.execute(
            'SELECT v.chunk_id, v.q FROM doc_vectors v JOIN doc_chunks c '
            'ON c.id = v.chunk_id WHERE c.doc_id = ?', (doc_id,)).fetchall()}
        orphans = conn.execute(
            'SELECT COUNT(*) FROM doc_vectors v LEFT JOIN doc_chunks c '
            'ON c.id = v.chunk_id WHERE c.id IS NULL').fetchone()[0]
    assert orphans == 0
    assert len(after) == chunks
    assert list(after.values())[0] != list(before.values())[0]
    # The served vector must be the NEW content's — not the old file's ghost.
    hits = library._vector_hits('default', 'quantum harp replacement text', 5)
    assert hits and hits[0][1] > 0.9
    old_hits = library._vector_hits('default', 'zebra painting original text', 5)
    assert not old_hits or old_hits[0][1] < 0.9


def test_watch_settle_guard_waits_then_ingests(library, tmp_path, monkeypatch):
    _no_threads(monkeypatch)
    folder = tmp_path / 'vault'
    folder.mkdir()
    f = folder / 'fresh.md'
    f.write_text('still copying in', encoding='utf-8')   # mtime = now
    fid, _ = library.add_watch_folder('default', str(folder))
    rep = library.scan_folder(fid)
    assert rep['added'] == 0                              # settling → waits
    old = time.time() - 60
    os.utime(f, (old, old))
    rep = library.scan_folder(fid)
    assert rep['added'] == 1                              # settled → ingests
    # Future mtime (clock-skewed share) counts as settled, not stuck forever.
    g = folder / 'future.md'
    g.write_text('from the future', encoding='utf-8')
    os.utime(g, (time.time() + 9000, time.time() + 9000))
    rep = library.scan_folder(fid)
    assert rep['added'] == 1


# ─── migrated-duplicate merge: cross-filing is a choice, not a dupe ──────────

def _mint_migrated(library, scope, text, collection_id):
    doc_id, err = library.import_note(scope, 'copy', text,
                                      collection_id=collection_id)
    assert doc_id and not err
    with library.get_connection() as conn:
        meta = json.loads(conn.execute(
            'SELECT meta FROM documents WHERE id = ?', (doc_id,)
        ).fetchone()[0] or '{}')
        meta['migrated_from'] = 'v2'
        conn.execute('UPDATE documents SET meta = ? WHERE id = ?',
                     (json.dumps(meta), doc_id))
        conn.commit()
    return doc_id


def test_merge_keeps_cross_filed_copies(library):
    ca, _ = library.create_collection('default', 'Recipes')
    cb, _ = library.create_collection('default', 'Gifts')
    a = _mint_migrated(library, 'default', 'the same exact body text', ca)
    b = _mint_migrated(library, 'default', 'the same exact body text', cb)
    assert library.merge_migrated_duplicates() == 0       # both survive
    with library.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 2
    # An UNFILED copy of the same text is the classic v1/v2 double — folds.
    c = _mint_migrated(library, 'default', 'the same exact body text', None)
    assert library.merge_migrated_duplicates() == 1
    with library.get_connection() as conn:
        left = {r[0] for r in conn.execute('SELECT id FROM documents').fetchall()}
    assert c not in left and a in left and b in left


# ─── explicit knowledge search: teaser + budget ──────────────────────────────

def test_explicit_note_teaser_and_budget(library):
    big = 'aardvark lantern ' + ('filler words drift onward ' * 200)
    doc_id, err = library.import_note('default', 'Big note', big)
    assert doc_id and not err
    text, found = library.search_library('default', 'aardvark lantern',
                                         mixed=False)
    assert found
    assert f'read_document({doc_id})' in text             # teased, with handle
    assert len(text) < lib.NOTE_FULL_MAX + 400            # not dumped whole


# ─── delete_scope: the library side goes too ─────────────────────────────────

def test_delete_scope_cascades_library(palace, library):
    pt._save_memory('a memory living in scr', 'scr')
    doc_id, err = library.import_note('scr', 'Scr note', 'library text in scr')
    assert doc_id and not err
    out = pt.delete_scope('scr')
    assert out.get('library_docs_deleted') == 1
    with library.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM documents WHERE scope = ?',
                            ('scr',)).fetchone()[0] == 0


# ─── id-namespace guard: [doc N] never edits memory [N] ──────────────────────

def test_update_memory_refuses_doc_ids(palace, monkeypatch):
    monkeypatch.setattr(pt, "_get_current_scope", lambda: "default")
    for raw in ('doc 5', 'Doc 5', '[doc 5]'):
        msg, ok = pt.execute('update_memory',
                             {'memory_id': raw, 'content': 'x'}, None)
        assert not ok and 'DOCUMENT' in msg
        msg, ok = pt.execute('delete_memory', {'memory_id': raw}, None)
        assert not ok and 'DOCUMENT' in msg


def test_update_memory_not_found_hints_at_doc(palace, library, monkeypatch):
    monkeypatch.setattr(pt, "_get_current_scope", lambda: "default")
    doc_id, err = library.import_note('default', 'Tron still',
                                      'sapphire in the movie tron')
    assert doc_id and not err
    with pt._get_connection() as conn:   # no chunk with this id exists
        assert conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 0
    msg, ok = pt._update_memory(doc_id, 'default', content='new caption')
    assert not ok and f'[doc {doc_id}]' in msg
    msg, ok = pt._delete_memory(doc_id, 'default')
    assert not ok and f'[doc {doc_id}]' in msg


# ─── memory recall: the matrix engine ────────────────────────────────────────

def test_vector_search_finds_and_ranks(palace):
    a = _save('the zebra painting hangs in the workshop')
    _save('coffee mug on the workbench')
    _save('rain on the gravel driveway')
    hits = pt._vector_search('the zebra painting hangs in the workshop',
                             'default', [], 5)
    assert hits and hits[0][0] == a
    assert hits[0][6] > 0.9                               # self-similarity


def test_vector_search_sees_in_place_updates(palace, monkeypatch):
    monkeypatch.setattr(pt, "_get_current_scope", lambda: "default")
    a = _save('the zebra painting hangs in the workshop')
    hits = pt._vector_search('the zebra painting hangs in the workshop',
                             'default', [], 5)
    assert hits and hits[0][0] == a
    msg, ok = pt._update_memory(a, 'default',
                                content='the quantum harp sits by the door')
    assert ok, msg
    # The stamp (COUNT, MAX id) is identical — only the gen bump saves us.
    hits = pt._vector_search('the quantum harp sits by the door',
                             'default', [], 5)
    assert hits and hits[0][0] == a and hits[0][6] > 0.9
    hits = pt._vector_search('the zebra painting hangs in the workshop',
                             'default', [], 5)
    assert not hits or hits[0][6] < 0.9                   # old vector is gone


def test_vector_search_respects_filters_and_privacy(palace):
    text = 'the boat launch plan and tide chart'
    a = _save(text, private_key='tide')
    b = _save(text)                                       # same text, public
    hits = pt._vector_search(text, 'default', [], 5)
    assert a not in [h[0] for h in hits]                  # private gated out
    assert b in [h[0] for h in hits]
    hits = pt._vector_search(text, 'default', [], 5, private_key='tide')
    assert a in [h[0] for h in hits]


def test_mem_matrix_skips_corrupt_blob(palace):
    good = _save('the lighthouse keeper waved')
    with pt._get_connection() as conn:
        conn.execute(
            "INSERT INTO chunks (layer, scope, content, created, updated, "
            "embedding, embedding_provider, embedding_dim) "
            "VALUES ('events', 'default', 'corrupt row', ?, ?, ?, 'fake:vec', 64)",
            (pt._now(), pt._now(), b'\x00\x01\x02'))       # wrong-size blob
        conn.commit()
    pt.bump_matrix_gen()
    hits = pt._vector_search('the lighthouse keeper waved', 'default', [], 5)
    assert hits and hits[0][0] == good                    # no crash, good row serves


# ─── merge rewrite gate ──────────────────────────────────────────────────────

def _set_vec(cid, vec):
    v = np.asarray(vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET embedding = ?, embedding_provider = ?, '
                     'embedding_dim = ? WHERE id = ?',
                     (v.tobytes(), 'fake', v.shape[0], cid))
        conn.commit()


def test_merge_rewrite_gate_default_off(palace, monkeypatch):
    a = _save('Zebra visited the house today')
    b = _save('Zebra came by the house for a visit')
    _set_vec(a, [1, 0, 0])
    _set_vec(b, [1, 0, 0])
    lt.open_pass('default', [a, b], kind='dedup')
    try:
        msg, ok = lt.execute('merge_memories',
                             {'memory_ids': [a, b],
                              'content': 'FABRICATED replacement text'}, None)
        assert ok, msg
        assert 'NOT used' in msg
        new_id = int(re.search(r'→ \[(\d+)\]', msg).group(1))
        with pt._get_connection() as conn:
            content = conn.execute('SELECT content FROM chunks WHERE id = ?',
                                   (new_id,)).fetchone()[0]
        assert content == 'Zebra came by the house for a visit'   # longest original
    finally:
        lt.close_pass()


def test_merge_rewrite_honored_when_enabled(palace, monkeypatch):
    monkeypatch.setattr(lt, "_merge_rewrite_enabled", lambda: True)
    a = _save('Zebra visited the house today')
    b = _save('Zebra came by the house for a visit')
    _set_vec(a, [1, 0, 0])
    _set_vec(b, [1, 0, 0])
    lt.open_pass('default', [a, b], kind='dedup')
    try:
        msg, ok = lt.execute('merge_memories',
                             {'memory_ids': [a, b],
                              'content': 'Zebra visited the house'}, None)
        assert ok, msg
        assert 'NOT used' not in msg
        new_id = int(re.search(r'→ \[(\d+)\]', msg).group(1))
        with pt._get_connection() as conn:
            content = conn.execute('SELECT content FROM chunks WHERE id = ?',
                                   (new_id,)).fetchone()[0]
        assert content == 'Zebra visited the house'
    finally:
        lt.close_pass()


# ─── fresh librarian session chats ───────────────────────────────────────────

def test_mint_session_chat(monkeypatch):
    monkeypatch.setattr(librarian, "_fresh_chat_enabled", lambda: True)
    name = librarian.mint_session_chat()
    assert re.fullmatch(r'librarian-\d{8}-\d{4}', name)
    monkeypatch.setattr(librarian, "_fresh_chat_enabled", lambda: False)
    assert librarian.mint_session_chat() == 'librarian'


# ─── transfer import: carried stamps reset (pre-drained corpus trap) ─────────

def _export_with_stamps(scope='src'):
    return {
        'format': 'mindpalace-export', 'version': 1, 'layer': 'events',
        'scope': scope, 'exported': pt._now(),
        'counts': {'chunks': 1, 'entities': 0}, 'entities': [],
        'chunks': [{
            'content': 'stamped memory from the other install',
            'created': '2026-01-01T00:00:00+00:00',
            'meta': {'librarian_at': 'x', 'temporal_at': 'x', 'link_at': 'x',
                     'dedup_at': 'x', 'event_date': '2026-02-02',
                     'refers_to_time': True},
        }],
    }


def test_import_strips_librarian_stamps(palace):
    out = transfer.import_data(body={'scope': 'dst',
                                     'data': _export_with_stamps()})
    assert out['imported'] == 1
    with pt._get_connection() as conn:
        meta = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE scope = 'dst'").fetchone()[0])
    for k in ('librarian_at', 'temporal_at', 'link_at', 'dedup_at'):
        assert k not in meta                              # re-reviewable here
    assert meta['event_date'] == '2026-02-02'             # the DATA still travels
    assert meta['refers_to_time'] is True


def test_import_keeps_stamps_on_request(palace):
    out = transfer.import_data(body={'scope': 'dst2',
                                     'data': _export_with_stamps(),
                                     'keep_librarian_stamps': True})
    assert out['imported'] == 1
    with pt._get_connection() as conn:
        meta = json.loads(conn.execute(
            "SELECT meta FROM chunks WHERE scope = 'dst2'").fetchone()[0])
    assert meta['librarian_at'] == 'x'
