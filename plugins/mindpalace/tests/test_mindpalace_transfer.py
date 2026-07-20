"""Per-tab per-scope import/export (2026-07-11). One layer × one scope per
file; entity refs by name; additive + idempotent import; self-sheet imports
never clobber a living sheet.

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import json

import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import self_tools as st
from plugins.mindpalace.routes import browse, transfer


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


def _export(scope, layer):
    out = transfer.export_data(query={'scope': scope, 'layer': layer})
    assert isinstance(out, dict) and out.get('format'), out
    return out


def _import(scope, data, expect=None):
    return transfer.import_data(body={'scope': scope, 'data': data,
                                      'expect_layer': expect})


def test_events_roundtrip_with_edges_and_idempotence(palace):
    with pt._get_connection() as conn:
        ts = pt._now()
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Krem', 'src', ?, ?)", (ts, ts))
        conn.commit()
    pt._save_memory("talked with Krem about the boat", 'src', label='harbor')
    pt._save_memory("quiet day at the bench", 'src', favorite=True)
    data = _export('src', 'events')
    assert data['counts']['chunks'] == 2
    krem_chunk = next(c for c in data['chunks'] if 'Krem' in c['content'])
    assert krem_chunk['mentions'] == ['Krem']       # edge travels as a NAME

    # Target scope has its own Krem — edges re-seed against it.
    with pt._get_connection() as conn:
        ts = pt._now()
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Krem', 'dst', ?, ?)", (ts, ts))
        conn.commit()
    r = _import('dst', data, expect='events')
    assert r['imported'] == 2 and r['edges_seeded'] == 1
    out, ok = pt._search_memory("boat", 'dst')
    assert ok and 'talked with Krem' in out
    with pt._get_connection() as conn:
        label, meta = conn.execute(
            "SELECT label, meta FROM chunks WHERE scope = 'dst' "
            "AND content = 'talked with Krem about the boat'").fetchone()
        fav = conn.execute(
            "SELECT favorite FROM chunks WHERE scope = 'dst' "
            "AND content = 'quiet day at the bench'").fetchone()[0]
    assert label == 'harbor' and fav == 1
    assert json.loads(meta)['import_src'] == 'src'   # provenance
    # Idempotent: same file again → everything skips.
    r2 = _import('dst', data, expect='events')
    assert r2['imported'] == 0 and r2['skipped'] == 2


def test_entities_roundtrip_fields_kind_headline(palace):
    eid = browse.create_entity(body={'name': 'Jane', 'kind': 'thing',
                                     'scope': 'src'})['id']
    browse.update_entity(eid=eid, body={'headline': 'her home server',
                                        'fields': {'location': 'the rack',
                                                   'nicknames': 'the rack'}})
    pt._save_memory("Jane runs the nightly backups", 'src',
                    layer='entities', entity='Jane')
    data = _export('src', 'entities')
    assert data['counts']['entities'] == 1
    r = _import('dst', data, expect='entities')
    assert r['entities_upserted'] == 1 and r['imported'] == 2   # headline + fact
    ents = browse.list_entities(query={'scope': 'dst'})['entities']
    jane = next(e for e in ents if e['name'] == 'Jane')
    assert jane['kind'] == 'thing'
    assert jane['headline'] == 'her home server'
    assert jane['meta']['fields']['location'] == 'the rack'
    assert jane['chunk_count'] == 2


def test_self_import_never_clobbers_living_sheet(palace):
    st.write_section('src', 'identity', "I am the source sheet")
    pt._save_memory("a free self note from src", 'src', layer='self')
    data = _export('src', 'self')
    st.write_section('dst', 'identity', "I am the target sheet")
    r = _import('dst', data, expect='self')
    assert r['imported'] == 2 and r['arrived_as_history'] == 1
    with pt._get_connection() as conn:
        cur = conn.cursor()
        current = st._current_sections(cur, 'dst')
    assert current['identity']['content'] == "I am the target sheet"   # untouched
    # The incoming identity is queryable as history.
    with pt._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE scope = 'dst' AND layer = 'self' "
            "AND json_extract(meta, '$.section') = 'identity' "
            "AND json_extract(meta, '$.superseded_at') IS NOT NULL").fetchone()[0]
    assert n == 1
    # Into an EMPTY scope the same file's identity becomes current.
    r2 = _import('fresh', data, expect='self')
    assert r2['arrived_as_history'] == 0
    with pt._get_connection() as conn:
        current = st._current_sections(conn.cursor(), 'fresh')
    assert current['identity']['content'] == "I am the source sheet"


def test_derived_from_provenance_travels_intra_file(palace):
    with pt._get_connection() as conn:
        ts = pt._now()
        cur = conn.cursor()
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'src', 'the original tangle', ?, ?)", (ts, ts))
        orig = cur.lastrowid
        cur.execute("INSERT INTO chunks (layer, scope, content, created, updated) "
                    "VALUES ('events', 'src', 'the distilled part', ?, ?)", (ts, ts))
        part = cur.lastrowid
        cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'chunk', ?, 'derived_from', 1.0, ?)",
                    (part, orig, ts))
        conn.commit()
    data = _export('src', 'events')
    r = _import('dst', data, expect='events')
    assert r['imported'] == 2
    with pt._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM edges d "
            "JOIN chunks a ON a.id = d.src_id JOIN chunks b ON b.id = d.dst_id "
            "WHERE d.kind = 'derived_from' AND a.scope = 'dst' AND b.scope = 'dst' "
            "AND a.content = 'the distilled part' "
            "AND b.content = 'the original tangle'").fetchone()[0]
    assert n == 1


def test_refusals_wrong_layer_bad_format_bad_version(palace):
    pt._save_memory("an event", 'src')
    data = _export('src', 'events')
    out = _import('dst', data, expect='self')
    assert out[1] == 400 and 'Events tab' in out[0]['error']
    assert _import('dst', {'format': 'nope'})[1] == 400
    assert _import('dst', {**data, 'version': 99})[1] == 400
    assert transfer.export_data(query={'scope': 'src'})[1] == 400   # layer required
