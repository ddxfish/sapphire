"""Library courier zip — per-scope export/import + per-tab clear (2026-07-19).

The contract under test:
- export = manifest.json + owned source files; derived data excluded
- import = deep copy into the TARGET scope via the normal entry points;
  chunks/embeddings/thumbs rebuild through the pipeline
- idempotent re-import skips everything
- THE question (Krem): content in two scopes shares NO files — deleting or
  clearing the source scope leaves the imported copy fully intact
- clear_layer wipes ONE tab in ONE scope, knowledge incl. the Library side

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import io
import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import library as lib
from plugins.mindpalace.routes import browse

import zlib


class _VecEmbedder:
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


def _jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (32, 32), (40, 80, 160)).save(buf, format='JPEG')
    return buf.getvalue()


def _build_src_scope(library):
    cat, _ = library.create_collection('src', 'Recipes', 'food things')
    top, _ = library.create_collection('src', 'Pies', parent_id=cat)
    nid, err = library.import_note('src', 'Apple pie',
                                   'Peel six apples. Cinnamon, not nutmeg.',
                                   collection_id=top, private_key='tide')
    assert nid, err
    fid, err = library.import_file('src', 'guide.txt',
                                   b'zebra painting guide text ' * 20,
                                   collection_id=cat)
    assert fid, err
    iid, err = library.import_image('src', 'photo.jpg', _jpeg(),
                                    title='Beach day',
                                    description='sapphire at the beach')
    assert iid, err
    library.update_image_meta('src', iid, people=['Krem'], notes='tron night')
    _drain(library)
    return nid, fid, iid


def test_export_import_round_trip_and_source_delete(palace, library, tmp_path):
    _build_src_scope(library)
    zpath = tmp_path / 'exp.zip'
    report, err = lib.export_scope_zip('src', str(zpath))
    assert not err, err
    assert report['documents'] == 2 and report['notes'] == 1

    with zipfile.ZipFile(zpath) as z:
        man = json.loads(z.read('manifest.json'))
    assert man['format'] == 'sapphire-library-export' and man['scope'] == 'src'
    assert len(man['documents']) == 3

    rep, err = lib.import_scope_zip('sapphire', str(zpath))
    assert not err, err
    assert rep['imported'] == 3 and rep['failed'] == 0, rep
    _drain(library)

    with lib.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, kind, private_key, meta, source_path "
            "FROM documents WHERE scope='sapphire'").fetchall()
    assert len(rows) == 3
    by_title = {r[1]: r for r in rows}
    assert by_title['Apple pie'][3] == 'tide'          # privacy travels
    img_meta = json.loads(by_title['Beach day'][4])
    assert img_meta.get('people') == ['Krem']          # annotations travel
    assert img_meta.get('notes') == 'tron night'
    with lib.get_connection() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM collections WHERE scope='sapphire'").fetchall()}
    assert {'Recipes', 'Pies'} <= names                # drawers recreated
    text, found = lib.search_library('sapphire', 'zebra painting guide')
    assert found                                       # pipeline rebuilt search

    rep2, err = lib.import_scope_zip('sapphire', str(zpath))
    assert rep2['imported'] == 0 and rep2['skipped'] == 3   # idempotent

    # ── THE question: delete the SOURCE scope; the copies must not care ──
    target_files = [r[5] for r in rows if r[5]]
    assert target_files                                # file + image have sources
    out = pt.delete_scope('src')
    assert out.get('library_docs_deleted') == 3
    for sp in target_files:
        assert Path(sp).exists(), f"imported copy lost its file: {sp}"
    title, text = lib.document_text('sapphire', by_title['Apple pie'][0])
    assert 'apples' in text.lower()
    text, found = lib.search_library('sapphire', 'zebra painting guide')
    assert found                                       # still fully alive


def test_watch_folder_definitions_round_trip(palace, library, tmp_path,
                                             monkeypatch):
    import os
    import time
    _no_threads(monkeypatch)
    vault = tmp_path / 'vault'
    vault.mkdir()
    (vault / 'sunset.jpg').write_bytes(_jpeg())
    old = time.time() - 60
    os.utime(vault / 'sunset.jpg', (old, old))
    fid, err = lib.add_watch_folder('src', str(vault))
    assert fid, err
    lib.scan_folder(fid)
    with lib.get_connection() as conn:
        did = conn.execute("SELECT id FROM documents WHERE scope='src'"
                           ).fetchone()[0]
    lib.update_image_meta('src', did, people=['Sapphire'], notes='dusk')
    lib.update_document('src', did, description='the good sunset')

    zpath = tmp_path / 'exp.zip'
    report, err = lib.export_scope_zip('src', str(zpath))
    assert not err and report['watch_folders'] == 1
    assert report['documents'] == 0            # watch files never enter the zip
    with zipfile.ZipFile(zpath) as z:
        man = json.loads(z.read('manifest.json'))
    assert man['watch_folders'][0]['annotations']['sunset.jpg']['people'] == ['Sapphire']

    # SAFE-BY-DEFAULT (2026-07-19): the manifest's absolute watch path is
    # untrusted — import never auto-adds/scans it. Definitions land in the
    # report for the human to re-add.
    rep, err = lib.import_scope_zip('sapphire', str(zpath))
    assert not err and rep['watch_attached'] == 0, rep
    assert rep.get('watch_skipped') == [str(vault)]
    with lib.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE "
                            "scope='sapphire'").fetchone()[0] == 0

    # Human re-adds the folder first → a second import reunites annotations.
    fid2, err = lib.add_watch_folder('sapphire', str(vault))
    assert fid2, err
    lib.scan_folder(fid2)
    rep, err = lib.import_scope_zip('sapphire', str(zpath))
    assert not err and rep['watch_attached'] == 1, rep
    with lib.get_connection() as conn:
        row = conn.execute(
            "SELECT description, meta FROM documents WHERE scope='sapphire'"
            ).fetchone()
    assert row and row[0] == 'the good sunset'
    m = json.loads(row[1])
    assert m.get('people') == ['Sapphire'] and m.get('notes') == 'dusk'


def test_import_missing_watch_path_reports(palace, library, tmp_path,
                                           monkeypatch):
    import os
    import time
    _no_threads(monkeypatch)
    vault = tmp_path / 'gone-later'
    vault.mkdir()
    (vault / 'a.jpg').write_bytes(_jpeg())
    old = time.time() - 60
    os.utime(vault / 'a.jpg', (old, old))
    fid, _ = lib.add_watch_folder('src', str(vault))
    lib.scan_folder(fid)
    zpath = tmp_path / 'exp.zip'
    lib.export_scope_zip('src', str(zpath))
    import shutil
    shutil.rmtree(vault)
    rep, err = lib.import_scope_zip('sapphire', str(zpath))
    assert not err
    assert rep['watch_attached'] == 0
    # Post safe-by-default: the path is never touched at all — it lands in
    # watch_skipped (missing or not); watch_missing stays empty.
    assert rep.get('watch_skipped') == [str(vault)]
    assert rep['watch_missing'] == []


# ─── clear_layer: one tab, one scope ─────────────────────────────────────────

def _mem(content, scope='scr', layer=None):
    msg, ok = pt._save_memory(content, scope, layer=layer)
    assert ok, msg
    return int(re.search(r'ID: (\d+)', msg).group(1))


def test_clear_layer_knowledge_clears_both_stores(palace, library):
    _mem('an event that must survive')
    # layer='knowledge' saves reroute to the Library; mind.db knowledge
    # chunks exist via import_v1 — mimic one directly.
    with pt._get_connection() as conn:
        conn.execute("INSERT INTO chunks (layer, scope, content, created, "
                     "updated) VALUES ('knowledge', 'scr', "
                     "'imported knowledge', ?, ?)", (pt._now(), pt._now()))
        conn.commit()
    did, err = lib.import_note('scr', 'Doomed note', 'library content here')
    assert did, err
    out = browse.maintenance(body={'action': 'clear_layer', 'scope': 'scr',
                                   'layer': 'knowledge', 'confirm': 'scr'})
    assert out['deleted_chunks'] >= 1
    assert out['library_docs_deleted'] == 1
    with pt._get_connection() as conn:
        layers = {r[0]: r[1] for r in conn.execute(
            "SELECT layer, COUNT(*) FROM chunks WHERE scope='scr' "
            "GROUP BY layer").fetchall()}
    assert layers.get('events') == 1               # the rest of the scope stays
    assert 'knowledge' not in layers
    with lib.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE scope='scr'"
                            ).fetchone()[0] == 0


def test_clear_layer_gates_and_scope_isolation(palace, library):
    _mem('other scope memory', scope='other')
    _mem('scr memory')
    out = browse.maintenance(body={'action': 'clear_layer', 'scope': 'scr',
                                   'layer': 'events', 'confirm': 'WRONG'})
    assert isinstance(out, tuple) and out[1] == 400    # typed gate holds
    out = browse.maintenance(body={'action': 'clear_layer', 'scope': 'scr',
                                   'layer': 'nonsense', 'confirm': 'scr'})
    assert isinstance(out, tuple) and out[1] == 400
    out = browse.maintenance(body={'action': 'clear_layer', 'scope': 'scr',
                                   'layer': 'events', 'confirm': 'scr'})
    assert out['deleted_chunks'] == 1
    with pt._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE scope='other'"
                         ).fetchone()[0]
    assert n == 1                                      # other scopes untouched


def test_wipe_scope_cascades_library(palace, library):
    """Krem's live find 2026-07-19: 'Delete ALL memories in scope' predated
    the Library and left the scope's docs alive in the Knowledge tab."""
    _mem('a memory in scr')
    did, err = lib.import_note('scr', 'Doomed', 'library content')
    assert did, err
    out = browse.maintenance(body={'action': 'wipe_scope', 'scope': 'scr',
                                   'confirm': 'scr'})
    assert out['library_docs_deleted'] == 1
    with lib.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE scope='scr'"
                            ).fetchone()[0] == 0


def test_clear_layer_entities_clears_entity_rows(palace, library):
    pt._save_memory('a fact', 'scr', layer='entities', entity='Zebra')
    out = browse.maintenance(body={'action': 'clear_layer', 'scope': 'scr',
                                   'layer': 'entities', 'confirm': 'scr'})
    assert out['deleted_entities'] >= 1
    with pt._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE scope='scr'"
                            ).fetchone()[0] == 0
