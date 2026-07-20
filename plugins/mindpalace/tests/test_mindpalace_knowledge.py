"""Knowledge unification (2026-07-11): human and AI knowledge are ONE layer —
meta.added_by is the metadata that tells them apart. Covers the added_by
stamp, the boot migration, the browse filter, + Add (entity route + knowledge
splitter).

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import json

import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.routes import browse


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


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
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    return pt


def _meta_of(cid):
    with pt._get_connection() as conn:
        raw = conn.execute('SELECT meta FROM chunks WHERE id = ?', (cid,)).fetchone()[0]
    return json.loads(raw) if raw else {}


def _last_id():
    with pt._get_connection() as conn:
        return conn.execute('SELECT MAX(id) FROM chunks').fetchone()[0]


# ─── added_by stamping (v3: knowledge saves land in the Library) ─────────────

def _lib():
    from plugins.mindpalace.tools import library
    return library


def test_added_by_user_without_tool_context(palace):
    msg, ok = pt._save_memory("a fact from the UI", 'default', layer='knowledge')
    assert ok, msg
    assert 'Saved to the library' in msg
    with _lib().get_connection() as conn:
        assert conn.execute('SELECT added_by FROM documents'
                            ).fetchone()[0] == 'user'


def test_added_by_ai_with_tool_context(palace):
    fm.tool_context.set({'chat': 'trinity', 'persona': 'sapphire'})
    msg, ok = pt._save_memory("a fact she researched", 'default', layer='knowledge')
    assert ok, msg
    with _lib().get_connection() as conn:
        assert conn.execute('SELECT added_by FROM documents'
                            ).fetchone()[0] == 'ai'


# ─── Boot migration: legacy rows gain added_by ───────────────────────────────

def test_migration_infers_added_by_for_legacy_knowledge(palace):
    ts = pt._now()
    with pt._get_connection() as conn:
        cur = conn.cursor()
        rows = [
            ('classic ai tab entry', {'tab_type': 'ai', 'import_key': 'v2:knowledge:1'}),
            ('her own saved note', {'persona': 'sapphire', 'chat': 'main'}),
            ('human kb import', {'tab_type': 'human', 'import_key': 'v2:knowledge:2'}),
            ('bare row no meta', None),
        ]
        ids = []
        for content, meta in rows:
            cur.execute(
                "INSERT INTO chunks (layer, scope, content, meta, created, updated) "
                "VALUES ('knowledge', 'default', ?, ?, ?, ?)",
                (content, json.dumps(meta) if meta else None, ts, ts))
            ids.append(cur.lastrowid)
        # An events row must NOT be touched by the knowledge-only migration.
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'default', 'an old event', ?, ?)", (ts, ts))
        ev = cur.lastrowid
        conn.commit()
    # Re-run init → idempotent migration sweeps the legacy rows.
    pt._db_initialized = False
    assert pt._ensure_db()
    assert _meta_of(ids[0])['added_by'] == 'ai'      # classic AI tab
    assert _meta_of(ids[1])['added_by'] == 'ai'      # chat provenance
    assert _meta_of(ids[2])['added_by'] == 'user'    # human KB
    assert _meta_of(ids[3])['added_by'] == 'user'    # bare
    assert 'added_by' not in _meta_of(ev)            # events untouched


# ─── Browse filter ───────────────────────────────────────────────────────────

def test_knowledge_saves_leave_chunk_feed_empty(palace):
    # v3: both authors' knowledge lands in the Library — the old chunk feed
    # stays empty, and documents carry the added_by provenance instead.
    pt._save_memory("mine from the app", 'default', layer='knowledge')
    fm.tool_context.set({'chat': 'x'})
    pt._save_memory("hers from a chat", 'default', layer='knowledge')
    fm.tool_context.set(None)
    q = {'scope': 'default', 'layer': 'knowledge'}
    assert browse.list_chunks(query=q)['total'] == 0
    with _lib().get_connection() as conn:
        by = sorted(r[0] for r in conn.execute(
            'SELECT added_by FROM documents').fetchall())
    assert by == ['ai', 'user']


def test_list_chunks_csv_layers_count_matches_list(palace):
    """The phantom-count fix (Krem's off-by-one report): a lone entities
    chunk in scope must not inflate the Memories tab's events+self total."""
    pt._save_memory("an event here", 'default')
    pt._save_memory("a fact on someone", 'default', layer='entities', entity='Ann')
    out = browse.list_chunks(query={'scope': 'default', 'layer': 'events,self'})
    assert out['total'] == 1
    assert [c['layer'] for c in out['chunks']] == ['events']
    assert browse.list_chunks(query={'scope': 'default'})['total'] == 2
    assert browse.list_chunks(query={'scope': 'default',
                                     'layer': 'events,bogus'})[1] == 400


# ─── + Add: entity route ─────────────────────────────────────────────────────

def test_create_entity_route(palace):
    out = browse.create_entity(body={'name': 'Lighthouse', 'kind': 'place',
                                     'scope': 'default'})
    assert out.get('success'), out
    with pt._get_connection() as conn:
        row = conn.execute("SELECT name, kind FROM entities WHERE id = ?",
                           (out['id'],)).fetchone()
    assert row == ('Lighthouse', 'place')
    # Unknown kind → clean 400; empty name → 400.
    assert browse.create_entity(body={'name': 'X', 'kind': 'starship',
                                      'scope': 'default'})[1] == 400
    assert browse.create_entity(body={'name': '', 'scope': 'default'})[1] == 400


def test_entity_headline_create_update_clear(palace):
    """The layer-1 short description (Krem: every kind carries one). PUT
    headline creates the tier-1 chunk, edits it in place (no duplicates),
    clears on empty — and list_entities surfaces it on the cards."""
    out = browse.create_entity(body={'name': 'Jane', 'kind': 'thing',
                                     'scope': 'default'})
    eid = out['id']
    assert browse.update_entity(eid=eid, body={'headline': 'her home server'})['success']
    ents = browse.list_entities(query={'scope': 'default'})['entities']
    assert ents[0]['headline'] == 'her home server'
    # Edit in place — still exactly one tier-1 chunk.
    browse.update_entity(eid=eid, body={'headline': 'her home server, the rack one'})
    with pt._get_connection() as conn:
        rows = conn.execute("SELECT content FROM chunks WHERE entity_id = ? "
                            "AND tier = 1", (eid,)).fetchall()
    assert rows == [('her home server, the rack one',)]
    # Clear removes it.
    browse.update_entity(eid=eid, body={'headline': ''})
    with pt._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE entity_id = ? "
                         "AND tier = 1", (eid,)).fetchone()[0]
    assert n == 0


def test_templates_thing_and_place_gained_fields():
    from plugins.mindpalace.tools import templates as tpl
    t = tpl.get_templates()
    thing_keys = [f['key'] for f in t['thing']['fields']]
    place_keys = [f['key'] for f in t['place']['fields']]
    assert 'location' in thing_keys and 'nicknames' in thing_keys
    assert 'nicknames' in place_keys and 'participants' in place_keys


# ─── + Add: knowledge → one Library document (v3) ────────────────────────────

def test_create_chunk_long_knowledge_one_library_doc(palace):
    long_text = "\n\n".join(f"reference paragraph {i}: " + "detail " * 40
                            for i in range(6))   # ~1700 chars, over the old cap
    out = browse.create_chunk(body={'content': long_text, 'scope': 'default',
                                    'layer': 'knowledge', 'label': 'rig-notes'})
    assert out.get('success'), out
    assert 'library' in out['message'].lower()
    with _lib().get_connection() as conn:
        docs = conn.execute('SELECT title FROM documents').fetchall()
        body = '\n'.join(r[0] for r in conn.execute(
            'SELECT content FROM doc_chunks ORDER BY seq').fetchall())
    assert docs == [('rig-notes',)]              # ONE doc, label = title
    assert body.count('detail') == 240           # nothing lost, no split-notes
    with pt._get_connection() as conn:           # and zero old-style chunks
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE layer = 'knowledge'"
                         ).fetchone()[0]
    assert n == 0
    # Events stay hard-capped — no long-text pass outside knowledge.
    out = browse.create_chunk(body={'content': 'x' * 600, 'scope': 'default',
                                    'layer': 'events'})
    assert isinstance(out, tuple) and out[1] == 400


# ─── Library flood caps (v3, 2026-07-17) ─────────────────────────────────────
# A book is one document. Mixed (all-layer) searches show a tight library
# block (MIXED_CAPS sections per doc); an explicit layer='knowledge' search
# digs deep (DOC_CAPS). FTS-only here — the conftest embedder is down.

def _seed_alice_book():
    lib = _lib()
    paras = [f"alice wonderland rabbit chapter part {i} " + "filler " * 150
             for i in range(16)]
    doc_id, err = lib.import_file('default', 'alice.txt',
                                  "\n\n".join(paras).encode(), kind='book',
                                  title='Alice')
    assert doc_id, err
    while lib.work_once():
        pass
    return doc_id


def test_mixed_search_shows_tight_library_block(palace):
    _seed_alice_book()
    msg, ok = pt._save_memory("talked about alice wonderland with a friend",
                              'default')
    assert ok, msg
    out, ok = pt._search_memory("alice wonderland", 'default', limit=10)
    assert ok, out
    assert '[events]' in out                     # the lived memory leads
    assert '🏛 From the library:' in out         # the shelf rides along
    assert out.count('§') <= 2, out              # MIXED_CAPS med — a taste


def test_layer_knowledge_search_digs_deep(palace):
    doc_id = _seed_alice_book()
    out, ok = pt._search_memory("alice wonderland rabbit", 'default',
                                layer='knowledge')
    assert ok, out
    assert out.count('§') >= 3, out              # DOC_CAPS med — the dig
    assert f'read_document({doc_id}' in out      # and the handle to go deeper


def test_standalone_notes_are_separate_docs(palace):
    for i in range(4):
        msg, ok = pt._save_memory(f"distinct note {i} about quantum sailing",
                                  'default', layer='knowledge')
        assert ok, msg
    out, ok = pt._search_memory("quantum sailing", 'default',
                                layer='knowledge')
    assert ok, out
    assert out.count('📝') == 4, out             # four docs, no cross-capping


# ─── Entity delete (browse route, 2026-07-12) ────────────────────────────────

def test_delete_entity_removes_chunks_edges_keeps_mentions(palace):
    msg, ok = pt._save_memory("Zorblax runs the moon cafe", 'default',
                              layer='entities', entity='Zorblax')
    assert ok, msg
    msg, ok = pt._save_memory("Had coffee with Zorblax today", 'default')
    assert ok, msg
    with pt._get_connection() as conn:
        eid = conn.execute(
            "SELECT id FROM entities WHERE name = 'Zorblax'").fetchone()[0]
        assert conn.execute(
            "SELECT COUNT(*) FROM edges WHERE dst_type='entity' AND dst_id=?",
            (eid,)).fetchone()[0] >= 1

    resp = browse.delete_entity(eid=str(eid))
    body = resp[0] if isinstance(resp, tuple) else resp
    assert body.get('success') is True
    assert body.get('deleted_chunks') == 1

    with pt._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entities WHERE id=?',
                            (eid,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM chunks WHERE entity_id=?',
                            (eid,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM edges WHERE (dst_type='entity' AND dst_id=?) "
            "OR (src_type='entity' AND src_id=?)", (eid, eid)).fetchone()[0] == 0
        # The event that MENTIONED it survives — it just loses the link.
        assert conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE content LIKE '%coffee with Zorblax%'"
        ).fetchone()[0] == 1


def test_delete_entity_missing_404(palace):
    resp = browse.delete_entity(eid='99999')
    assert isinstance(resp, tuple) and resp[1] == 404
