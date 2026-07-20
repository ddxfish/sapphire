"""Library (Knowledge v3) P1 — schema, chunker profiles, int8 vectors,
note/txt/md import, and the resumable queue. Spec: tmp/knowledge-layer.md.

The queue tests drive work_once() directly — each call is one atomic slice,
so 'restart mid-book' is simulated by just... calling it again. That's the
design working: the jobs table is the only worker state.
"""
import zlib

import numpy as np
import pytest

from plugins.mindpalace.tools import library as lib


class _FakeEmbedder:
    provider_id = "fake:test"
    available = True

    def embed(self, texts, prefix="search_document"):
        out = []
        for t in texts:
            rng = np.random.default_rng(zlib.crc32(t.encode('utf-8', 'replace')))
            v = rng.standard_normal(64).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return out


class _DownEmbedder:
    provider_id = "fake:down"
    available = False

    def embed(self, texts, prefix="search_document"):
        return None


class _FakeVision:
    """Deterministic 64-dim 'pixel' vectors — seeded from the file BYTES, so
    the same image always lands in the same spot. Not aligned with the text
    fake (cross-modal ranking is asserted via metadata routes; alignment
    itself was verified live against the real model pair)."""
    provider_id = "fake:vision"
    available = True

    def embed_paths(self, paths):
        out = []
        for p in paths:
            try:
                rng = np.random.default_rng(zlib.crc32(p.read_bytes()))
                v = rng.standard_normal(64).astype(np.float32)
                out.append(v / np.linalg.norm(v))
            except Exception:
                out.append(None)
        return out


class _DownVision:
    provider_id = "fake:vision-down"
    available = False

    def embed_paths(self, paths):
        return [None] * len(paths)


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "_db_path", tmp_path / "library.db", raising=False)
    monkeypatch.setattr(lib, "_db_initialized", False, raising=False)
    monkeypatch.setattr(lib, "_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(lib, "_vision", lambda: _FakeVision())
    # Tests drive work_once() by hand — the real daemon thread would race
    # every queue assertion.
    monkeypatch.setattr(lib, "ensure_worker", lambda: None)
    return lib


def _doc(library, doc_id):
    with library.get_connection() as conn:
        row = conn.execute(
            'SELECT status, kind, importance FROM documents WHERE id = ?',
            (doc_id,)).fetchone()
    return {'status': row[0], 'kind': row[1], 'importance': row[2]}


def _counts(library, doc_id):
    with library.get_connection() as conn:
        chunks = conn.execute('SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ?',
                              (doc_id,)).fetchone()[0]
        vecs = conn.execute(
            'SELECT COUNT(*) FROM doc_vectors v JOIN doc_chunks c '
            'ON v.chunk_id = c.id WHERE c.doc_id = ?', (doc_id,)).fetchone()[0]
    return chunks, vecs


def _drain(library, cap=500):
    for _ in range(cap):
        if not library.work_once():
            return


# ─── Collections ─────────────────────────────────────────────────────────────

def test_collections_two_levels_hard(library):
    cat, err = library.create_collection('default', 'Tech', 'hardware and code')
    assert cat and not err
    top, err = library.create_collection('default', 'ESP32', 'micro projects',
                                         parent_id=cat)
    assert top and not err
    bad, err = library.create_collection('default', 'Deeper', parent_id=top)
    assert bad is None and '2 levels' in err


# ─── Notes (synchronous) ─────────────────────────────────────────────────────

def test_note_imports_whole_and_high(library):
    doc_id, err = library.import_note('default', "Apple pie recipe",
                                      "Peel six apples.\n\nCinnamon, not nutmeg.")
    assert doc_id and not err
    d = _doc(library, doc_id)
    assert d['status'] == 'ready' and d['kind'] == 'note'
    assert d['importance'] == lib.IMPORTANCE['high']   # gesture implies value
    chunks, vecs = _counts(library, doc_id)
    assert chunks == 1 and vecs == 1                   # notes never fragment


# ─── Files through the queue ─────────────────────────────────────────────────

def test_txt_import_via_queue(library):
    text = "\n\n".join(f"Paragraph {i}: " + "words " * 40 for i in range(30))
    doc_id, err = library.import_file('default', 'shatner_01.txt',
                                      text.encode(), kind='book')
    assert doc_id and not err
    assert _doc(library, doc_id)['status'] == 'queued'
    assert _doc(library, doc_id)['importance'] == lib.IMPORTANCE['med']
    _drain(library)
    d = _doc(library, doc_id)
    assert d['status'] == 'ready'
    chunks, vecs = _counts(library, doc_id)
    assert chunks > 1 and vecs == chunks
    # Source is byte-exact; working copy exists.
    with library.get_connection() as conn:
        src, work = conn.execute(
            'SELECT source_path, working_path FROM documents WHERE id = ?',
            (doc_id,)).fetchone()
    from pathlib import Path
    assert Path(src).read_bytes() == text.encode()
    assert Path(work).exists()


def test_md_headings_become_chapters(library):
    md = ("# Chapter One\n\nAlice was beginning to get very tired.\n\n"
          "# Chapter Two\n\nOff with her head!\n")
    doc_id, _ = library.import_file('default', 'alice.md', md.encode(),
                                    kind='article')
    _drain(library)
    with library.get_connection() as conn:
        chapters = [r[0] for r in conn.execute(
            'SELECT chapter FROM doc_chunks WHERE doc_id = ? ORDER BY seq',
            (doc_id,)).fetchall()]
    assert 'Chapter One' in chapters and 'Chapter Two' in chapters


def test_book_profile_sizes_and_overlap(library):
    size, overlap = lib.PROFILES['book']
    text = "\n\n".join("sentence %d " % i + "lorem ipsum " * 30
                       for i in range(60))
    chunks = lib.chunk_text(text, 'book')
    assert len(chunks) > 3
    # Overlap slack rides on top of size; nothing wildly oversized.
    assert all(len(c) <= size + overlap + 8 for _, c in chunks)
    # Every non-first chunk carries the tail of its predecessor.
    assert all(c.startswith('…') for _, c in chunks[1:])


def test_reject_unknown_extension(library):
    doc_id, err = library.import_file('default', 'alice.docx', b'nope')
    assert doc_id is None and 'txt, md, pdf, epub' in err


# ─── P2: PDF / EPUB extractors ───────────────────────────────────────────────

def _mini_pdf(text):
    """Smallest valid text PDF — computed xref so pypdf reads it strict."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def test_pdf_text_layer_imports(library):
    text = "The cenote swim was warmer than the pool said the guide " * 3
    doc_id, err = library.import_file('default', 'trip.pdf',
                                      _mini_pdf(text.strip()))
    assert doc_id and not err, err
    assert _doc(library, doc_id)['kind'] == 'reference'   # pdf auto-kind
    _drain(library)
    assert _doc(library, doc_id)['status'] == 'ready'
    with library.get_connection() as conn:
        content = conn.execute(
            'SELECT content FROM doc_chunks WHERE doc_id = ? ORDER BY seq',
            (doc_id,)).fetchone()[0]
    assert 'cenote swim' in content


def test_scanned_pdf_yeeted(library):
    from pypdf import PdfWriter
    import io
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)      # a "scan": no text layer
    buf = io.BytesIO()
    w.write(buf)
    doc_id, err = library.import_file('default', 'scan.pdf', buf.getvalue())
    assert doc_id is None and 'no usable text layer' in err
    with library.get_connection() as conn:       # refusal creates NOTHING
        assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 0


def _mini_epub(tmp_path):
    from ebooklib import epub
    book = epub.EpubBook()
    book.set_identifier('test-alice')
    book.set_title('Alice in Wonderland')
    book.add_author('Lewis Carroll')
    chapters = []
    for i, (head, body) in enumerate([
            ('Down the Rabbit-Hole', 'Alice was beginning to get very tired.'),
            ('The Queen', 'Off with her head! shouted the Queen.')], 1):
        c = epub.EpubHtml(title=head, file_name=f'ch{i}.xhtml', lang='en')
        c.content = f'<html><body><h1>{head}</h1><p>{body}</p></body></html>'
        book.add_item(c)
        chapters.append(c)
    book.toc = chapters
    book.spine = ['nav'] + chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    path = tmp_path / 'alice.epub'
    epub.write_epub(str(path), book)
    return path.read_bytes()


def test_epub_chapters_and_metadata(library, tmp_path):
    doc_id, err = library.import_file('default', 'alice.epub',
                                      _mini_epub(tmp_path))
    assert doc_id and not err, err
    with library.get_connection() as conn:
        title, author, kind = conn.execute(
            'SELECT title, author, kind FROM documents WHERE id = ?',
            (doc_id,)).fetchone()
    assert title == 'Alice in Wonderland'        # DC metadata autofill
    assert author == 'Lewis Carroll'
    assert kind == 'book'                        # epub auto-kind
    _drain(library)
    with library.get_connection() as conn:
        chapters = {r[0] for r in conn.execute(
            'SELECT chapter FROM doc_chunks WHERE doc_id = ?',
            (doc_id,)).fetchall()}
        beheading = conn.execute(
            "SELECT c.chapter FROM doc_fts f JOIN doc_chunks c ON c.id = f.rowid "
            "WHERE doc_fts MATCH 'head' AND c.doc_id = ?", (doc_id,)).fetchone()
    assert 'The Queen' in chapters               # h1 → breadcrumb
    assert beheading and beheading[0] == 'The Queen'


def test_corrupt_epub_refused(library):
    doc_id, err = library.import_file('default', 'drm.epub',
                                      b'PK\x03\x04 not really an epub')
    assert doc_id is None and 'EPUB' in err


def test_chunk_guard_caps_explosion(library, monkeypatch):
    monkeypatch.setattr(lib, 'DOC_CHUNK_GUARD', 5)
    text = "\n\n".join("para " + "x " * 900 for _ in range(50))
    assert len(lib.chunk_text(text, 'book')) == 5


# ─── Resume: the whole point of the queue ────────────────────────────────────

def test_resume_mid_embed_no_loss_no_dupes(library, monkeypatch):
    monkeypatch.setattr(lib, 'EMBED_BATCH', 2)
    text = "\n\n".join(f"Block {i}: " + "words " * 60 for i in range(12))
    doc_id, _ = library.import_file('default', 'big.txt', text.encode(),
                                    kind='book')
    library.work_once()                      # chunk stage
    library.work_once()                      # first embed batch (2 vectors)
    chunks, vecs = _counts(library, doc_id)
    assert vecs == 2 and chunks > vecs
    with library.get_connection() as conn:
        cursor_pos = conn.execute('SELECT cursor FROM jobs WHERE doc_id = ?',
                                  (doc_id,)).fetchone()[0]
    assert cursor_pos == 2                   # durable progress, not in-RAM
    # "Restart": nothing held in memory — just keep calling work_once.
    _drain(library)
    chunks, vecs = _counts(library, doc_id)
    assert vecs == chunks
    assert _doc(library, doc_id)['status'] == 'ready'
    with library.get_connection() as conn:   # exactly one vector per chunk
        dupes = conn.execute(
            'SELECT COUNT(*) - COUNT(DISTINCT chunk_id) FROM doc_vectors'
        ).fetchone()[0]
    assert dupes == 0
    assert library.resume_pending() == 0     # queue is drained


def test_embedder_down_doc_still_serves_fts(library, monkeypatch):
    monkeypatch.setattr(lib, "_embedder", lambda: _DownEmbedder())
    doc_id, _ = library.import_file('default', 'notes.txt',
                                    b"the cenote swim was warmer than the pool")
    _drain(library)
    d = _doc(library, doc_id)
    assert d['status'] == 'ready'            # honest partial, never a wedge
    chunks, vecs = _counts(library, doc_id)
    assert chunks >= 1 and vecs == 0
    with library.get_connection() as conn:   # FTS serves without vectors
        hits = conn.execute(
            "SELECT rowid FROM doc_fts WHERE doc_fts MATCH 'cenote'").fetchall()
    assert hits


# ─── int8 engine (storage half) ──────────────────────────────────────────────

def test_quantize_roundtrip_cosine(library):
    rng = np.random.default_rng(7)
    v = rng.standard_normal(768).astype(np.float32)
    v /= np.linalg.norm(v)
    scale, blob = lib.quantize(v)
    back = lib.dequantize(scale, blob)
    cos = float(np.dot(v, back) / (np.linalg.norm(v) * np.linalg.norm(back)))
    assert cos > 0.999                       # ~1% is the promised loss ceiling


def test_decode_cp1252_smart_quotes(library):
    raw = b"Krem\x92s \x93boat\x94"        # cp1252 curly quotes, invalid utf-8
    text = lib.decode_bytes(raw)
    assert '’' in text and '“' in text   # curly quotes survive


# ─── P3: retrieval — the Cancun contract, executable ─────────────────────────

def _seed_book(library, title, paras, kind='book', **kw):
    text = "\n\n".join(paras)
    doc_id, err = library.import_file('default', f"{title}.txt",
                                      text.encode(), kind=kind, title=title, **kw)
    assert doc_id, err
    _drain(library)
    return doc_id


def test_vector_only_retrieval(library, monkeypatch):
    library.import_note('default', "Pie", "Cinnamon, never nutmeg, in the pie.")
    library.import_note('default', "Boat", "The hull needs sanding in spring.")
    monkeypatch.setattr(lib, '_fts_hits', lambda *a: [])
    out, found = library.search_library(
        'default', "Cinnamon, never nutmeg, in the pie.")
    assert found and 'Pie' in out and 'sanding' not in out


def test_fts_carries_when_embedder_down(library, monkeypatch):
    library.import_note('default', "Pie", "Cinnamon, never nutmeg, in the pie.")
    monkeypatch.setattr(lib, "_embedder", lambda: _DownEmbedder())
    out, found = library.search_library('default', "nutmeg cinnamon")
    assert found and 'Pie' in out


def test_notes_return_whole_docs_addressed(library):
    library.import_note('default', "Salt on the Lens",
                        "a poem about the sea.\n\nAnd the second stanza too.")
    out, found = library.search_library('default', "poem about the sea")
    assert found
    assert 'second stanza' in out            # whole, not fragment
    assert '[doc ' in out                    # addressed for follow-up
    assert '📝' in out and '(note, high)' in out


def test_stitching_neighbors_in_one_block(library):
    paras = [f"Filler paragraph {i} " + "lorem " * 150 for i in range(8)]
    paras[4] = "The zebra migration clustered at the river. " + "lorem " * 140
    doc_id = _seed_book(library, "Serengeti", paras)
    out, found = library.search_library('default', "zebra migration clustered")
    assert found and 'zebra migration' in out
    # The stitch carries neighbors: the hit's block contains adjacent filler.
    assert 'Filler paragraph 3' in out or 'Filler paragraph 5' in out
    assert f'read_document({doc_id}' in out  # the dig handle is offered


def test_doc_scoped_search(library):
    a = _seed_book(library, "Alpha", ["the shared keyword walrus appears here"])
    _seed_book(library, "Beta", ["the shared keyword walrus appears again"])
    out, found = library.search_library('default', "shared keyword walrus", doc=a)
    assert found and 'Alpha' in out and 'Beta' not in out


def test_private_docs_gated(library):
    library.import_note('default', "Secret tide tables",
                        "the tide turns at moonrise", private_key='tide')
    out, found = library.search_library('default', "tide turns at moonrise")
    assert 'Secret' not in out               # no key, no doc
    out, found = library.search_library('default', "tide turns at moonrise",
                                        private_key='tide')
    assert found and 'Secret tide tables' in out


def test_low_importance_caps_sections(library):
    paras = [f"walrus sighting number {i} " + "words " * 120 for i in range(10)]
    doc_id = _seed_book(library, "Walrus Log", paras, importance='low')
    out, found = library.search_library('default', "walrus sighting number")
    assert found
    assert out.count('§') <= lib.DOC_CAPS['low'] + 1   # capped, not a dump


def test_read_document_pages_and_around(library):
    paras = [f"Chapter text piece {i} " + "prose " * 100 for i in range(14)]
    doc_id = _seed_book(library, "Longbook", paras)
    p1, ok = library.read_document_text('default', doc_id, page=1)
    assert ok and '§1' in p1 and 'page=2' in p1          # continuation offered
    p2, ok = library.read_document_text('default', doc_id, page=2)
    assert ok and p2 != p1
    around, ok = library.read_document_text('default', doc_id, around=5)
    assert ok and '§5' in around
    off, ok = library.read_document_text('default', doc_id, page=99)
    assert not ok and 'pages' in off                     # honest bounds
    other, ok = library.read_document_text('work', doc_id)
    assert not ok                                        # scope wall holds


# ─── P3b: the reroutes — her existing tools reach the Library ────────────────

from plugins.mindpalace.tools import palace_tools as pt_mod
from plugins.mindpalace.tools import library_tools as lt_mod


@pytest.fixture
def palace_lib(tmp_path, monkeypatch, library):
    monkeypatch.setattr(pt_mod, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt_mod, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt_mod, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt_mod, "_get_embedder", lambda: _FakeEmbedder(),
                        raising=False)
    return pt_mod


def test_search_layer_knowledge_reroutes(palace_lib, library):
    library.import_note('default', "Pie", "Cinnamon, never nutmeg, in the pie.")
    out, ok = palace_lib._search_memory("nutmeg cinnamon", 'default',
                                        layer='knowledge')
    assert ok and 'Library matches' in out and '📝' in out


def test_save_memory_knowledge_becomes_library_note(palace_lib, library):
    long_fact = "The VHS tracking knob compensates for head drift. " * 20
    msg, ok = palace_lib._save_memory(long_fact, 'default', layer='knowledge',
                                      label='vhs lore')
    assert ok, msg
    assert 'Saved to the library' in msg and '[doc ' in msg
    with library.get_connection() as conn:      # landed as a library doc
        title = conn.execute('SELECT title FROM documents').fetchone()[0]
    assert title == 'vhs lore'
    with palace_lib._get_connection() as conn:  # and NOT as a mind.db chunk
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE layer = 'knowledge'"
                         ).fetchone()[0]
    assert n == 0


def test_mixed_search_appends_library(palace_lib, library):
    msg, ok = palace_lib._save_memory("Saw a zebra at the fence today",
                                      'default')
    assert ok, msg
    library.import_note('default', "Zebra facts",
                        "A zebra migration can cross the whole plain.")
    out, ok = palace_lib._search_memory("zebra", 'default')
    assert ok
    assert 'Found 1 memories' in out            # the lived memory leads
    assert '🏛 From the library:' in out        # the shelf rides along
    assert 'Zebra facts' in out


def test_no_memory_hits_still_finds_library(palace_lib, library):
    library.import_note('default', "Quantum notes",
                        "Entanglement is not faster-than-light signalling.")
    out, ok = palace_lib._search_memory("entanglement signalling", 'default')
    assert ok and 'library has matches' in out and 'Quantum notes' in out


def test_library_tools_execute_paths(palace_lib, library, monkeypatch):
    monkeypatch.setattr(pt_mod, "_get_current_scope", lambda: 'default')
    doc_id, _ = library.import_note('default', "Shelf card", "hello from L3")
    out, ok = lt_mod.execute('library', {}, None)
    assert ok and 'Shelf card' in out
    out, ok = lt_mod.execute('read_document', {'document_id': doc_id}, None)
    assert ok and 'hello from L3' in out
    out, ok = lt_mod.execute('read_document', {'document_id': 99999}, None)
    assert not ok


def test_search_document_id_implies_knowledge(palace_lib, library):
    a, _ = library.import_note('default', "Alpha", "the walrus keyword here")
    library.import_note('default', "Beta", "the walrus keyword again")
    out, ok = palace_lib._search_memory("walrus keyword", 'default', doc=a)
    assert ok and 'Alpha' in out and 'Beta' not in out


# ─── P4: the Library tab's routes ────────────────────────────────────────────

from plugins.mindpalace.routes import library_routes as lr


def test_routes_full_lifecycle(library):
    out = lr.create_collection(body={'scope': 'default', 'name': 'Tech',
                                     'description': 'hardware'})
    assert out.get('success'), out
    cat_id = out['id']
    out = lr.create_note(body={'scope': 'default', 'title': 'ESP32 pinout',
                               'content': 'GPIO34 is input-only',
                               'collection_id': cat_id, 'importance': 'high'})
    assert out.get('success'), out
    did = out['id']
    cat = lr.catalog(query={'scope': 'default'})
    assert cat['total'] == 1
    assert cat['categories'][0]['documents'][0]['title'] == 'ESP32 pinout'
    assert cat['categories'][0]['description'] == 'hardware'
    doc = lr.get_document(did=did, query={'scope': 'default'})
    assert 'GPIO34' in doc['text']
    assert lr.update_document(did=did, body={'scope': 'default',
                                             'importance': 'low'}).get('success')
    cat = lr.catalog(query={'scope': 'default'})
    assert cat['categories'][0]['documents'][0]['importance'] == 'low'
    # Drawer rules: category holding a topic refuses deletion; deleting the
    # doc's drawer unfiles the doc, never deletes it.
    t = lr.create_collection(body={'scope': 'default', 'name': 'ESP32',
                                   'parent_id': cat_id})
    assert t.get('success')
    assert lr.update_collection(cid=t['id'],
                                body={'scope': 'default',
                                      'description': 'micro stuff'}).get('success')
    out = lr.delete_collection(cid=cat_id, query={'scope': 'default'})
    assert isinstance(out, tuple) and out[1] == 400
    assert lr.delete_collection(cid=t['id'],
                                query={'scope': 'default'}).get('success')
    assert lr.delete_collection(cid=cat_id,
                                query={'scope': 'default'}).get('success')
    cat = lr.catalog(query={'scope': 'default'})
    assert cat['total'] == 1 and cat['unfiled'][0]['id'] == did
    assert lr.delete_document(did=did, query={'scope': 'default'}).get('success')
    assert lr.catalog(query={'scope': 'default'})['total'] == 0


def test_route_upload_multipart_and_refusals(library):
    import asyncio

    class _File:
        def __init__(self, name, data):
            self.filename, self._d = name, data

        async def read(self):
            return self._d

    class _Form:
        def __init__(self, fields, files):
            self._f, self._files = fields, files

        def get(self, k):
            return self._f.get(k)

        def getlist(self, k):
            return self._files if k == 'file' else []

    class _Req:
        def __init__(self, form):
            self._form = form

        async def form(self):
            return self._form

    form = _Form({'scope': 'default', 'importance': 'low'},
                 [_File('shatner_01.txt', b'Chapter one. ' * 50),
                  _File('bad.docx', b'nope')])
    res = asyncio.run(lr.upload(request=_Req(form)))
    assert res['success']
    assert len(res['imported']) == 1 and len(res['refused']) == 1
    assert 'txt, md, pdf, epub' in res['refused'][0]['error']
    _drain(library)
    cat = lr.catalog(query={'scope': 'default'})
    assert cat['total'] == 1 and cat['unfiled'][0]['status'] == 'ready'
    assert cat['unfiled'][0]['importance'] == 'low'   # bulk default honored


def test_route_download_note_fallback(library):
    out = lr.create_note(body={'scope': 'default', 'title': 'Card',
                               'content': 'hello from the shelf'})
    r = lr.download(did=out['id'], query={'scope': 'default',
                                          'which': 'original'})
    assert getattr(r, 'status_code', None) == 200
    assert b'hello from the shelf' in r.body


# ─── P5: coexistence migration (COPY — v1 read-only, v2 rows stay) ───────────

def _legacy_v2_chunk(pt, content, label=None, source=None, idx=None):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        ts = pt._now()
        cur.execute(
            "INSERT INTO chunks (layer, scope, content, label, source, "
            "chunk_index, created, updated) VALUES ('knowledge', 'default', "
            "?, ?, ?, ?, ?, ?)", (content, label, source, idx, ts, ts))
        conn.commit()
        return cur.lastrowid


def test_migrate_v2_chunks_grouped_flagged_deduped(palace_lib, library):
    for i in range(3):
        _legacy_v2_chunk(palace_lib, f"alice fragment {i} of the old shoebox",
                         label='alice', idx=i)
    solo = _legacy_v2_chunk(palace_lib, "a lone legacy fact about sextants")
    report = library.migrate_all()
    assert report['v2'] == {'imported': 2, 'skipped': 0, 'failed': 0}
    _drain(library)
    with library.get_connection() as conn:
        titles = {r[0] for r in conn.execute('SELECT title FROM documents')}
    assert 'alice' in titles                      # 3 fragments → ONE doc
    # Reassembly kept every fragment, in order.
    with library.get_connection() as conn:
        did = conn.execute("SELECT id FROM documents WHERE title='alice'"
                           ).fetchone()[0]
    _, text = library.document_text('default', did)
    assert all(f"alice fragment {i}" in text for i in range(3))
    # Originals flagged, NOT deleted — and palace reads now exclude them.
    with palace_lib._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE layer='knowledge'"
                         ).fetchone()[0]
        flagged = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE layer='knowledge' AND "
            "json_extract(meta, '$.library_migrated') IS NOT NULL").fetchone()[0]
    assert n == 4 and flagged == 4
    out, ok = palace_lib._search_memory("sextants", 'default')
    assert ok and '[knowledge]' not in out        # no double-serving
    assert 'library has matches' in out or '🏛' in out
    # Idempotent: force re-run copies nothing — flagged rows never even
    # re-enter the candidate scan (import_key is the second net).
    report = library.migrate_all(force=True)
    assert report['v2'] == {'imported': 0, 'skipped': 0, 'failed': 0}
    # Marker: unforced call is a cached no-op.
    assert library.migrate_all()['ran_at'] == report['ran_at']


def test_migrate_v1_knowledge_readonly(palace_lib, library, tmp_path,
                                       monkeypatch):
    import sqlite3
    src = tmp_path / 'knowledge.db'
    conn = sqlite3.connect(src)
    conn.executescript("""
        CREATE TABLE knowledge_tabs (id INTEGER PRIMARY KEY, name TEXT,
            type TEXT, scope TEXT);
        CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY, tab_id INTEGER,
            content TEXT, chunk_index INTEGER, source_filename TEXT,
            created_at TEXT, scope TEXT);
        INSERT INTO knowledge_tabs VALUES
            (1, 'Boats', 'human', 'default'), (2, 'Research', 'ai', 'default');
        INSERT INTO knowledge_entries VALUES
            (1, 1, 'hull care part one', 0, 'hull.txt', '2025-01-01', NULL),
            (2, 1, 'hull care part two', 1, 'hull.txt', '2025-01-01', NULL),
            (3, 2, 'she researched tides herself', NULL, NULL, '2025-01-02', NULL);
    """)
    conn.commit()
    conn.close()
    before = src.read_bytes()
    from plugins.mindpalace.tools import import_tools as it
    monkeypatch.setattr(it, '_source_path', lambda config, name: tmp_path / name)
    report = library.migrate_all()
    assert report['v1'] == {'imported': 2, 'skipped': 0, 'failed': 0,
                            'absent': False}
    assert src.read_bytes() == before             # v1 byte-identical: READ-ONLY
    _drain(library)
    with library.get_connection() as conn:
        cats = {r[0] for r in conn.execute(
            'SELECT name FROM collections WHERE parent_id IS NULL')}
        docs = {r[0]: (r[1], r[2]) for r in conn.execute(
            'SELECT title, added_by, collection_id FROM documents')}
    assert {'Boats', 'Research'} <= cats          # tabs became drawers
    assert docs['hull'][0] == 'user'              # human tab → user
    assert docs['she researched tides herself'][0] == 'ai'
    with library.get_connection() as conn:
        did = conn.execute("SELECT id FROM documents WHERE title='hull'"
                           ).fetchone()[0]
    _, text = library.document_text('default', did)
    assert 'part one' in text and 'part two' in text


def test_migration_routes(palace_lib, library):
    _legacy_v2_chunk(palace_lib, "one legacy fact")
    out = lr.migrate()
    assert out.get('success') and out['report']['v2']['imported'] == 1
    status = lr.migration_status()
    assert status['report']['v2']['imported'] == 1


# ─── Tool console: read-only peek, zero fingerprints ─────────────────────────

def test_console_peek_readonly_no_fingerprints(palace_lib, library):
    msg, ok = palace_lib._save_memory("The zebra crossed at dawn", 'default')
    assert ok, msg
    library.import_note('default', "Zebra notes", "zebra migration facts")
    # Peek search returns her real output — with recall instrumentation OFF.
    r = lr.peek(body={'scope': 'default', 'tool': 'search_memory',
                      'args': {'query': 'zebra'}})
    assert r['ok'] and 'zebra' in r['output'].lower()
    with palace_lib._get_connection() as conn:
        boosted = conn.execute(
            'SELECT COALESCE(SUM(recall_count), 0) FROM chunks').fetchone()[0]
    assert boosted == 0                          # boost=False held
    # A REAL search still instruments — the seam only opens for peeks.
    palace_lib._search_memory("zebra", 'default')
    with palace_lib._get_connection() as conn:
        boosted = conn.execute(
            'SELECT COALESCE(SUM(recall_count), 0) FROM chunks').fetchone()[0]
    assert boosted > 0
    # Peek read_self leaves her "while you were away" watermark alone.
    r = lr.peek(body={'scope': 'default', 'tool': 'read_self',
                      'args': {'depth': 1}})
    assert r['ok'] and 'Self sheet' in r['output']
    from plugins.mindpalace.tools import ledger
    with palace_lib._get_connection() as conn:
        wm = ledger.last_read_ts(conn.cursor(), 'default')
    assert wm is None                            # stamp=False held
    # Library + doc peeks work; write verbs hit the whitelist wall.
    r = lr.peek(body={'scope': 'default', 'tool': 'library', 'args': {}})
    assert r['ok'] and 'Zebra notes' in r['output']
    out = lr.peek(body={'scope': 'default', 'tool': 'save_memory', 'args': {}})
    assert isinstance(out, tuple) and out[1] == 400


# ─── P5b: the graph — documents as spider leaves ─────────────────────────────

def _mk_entity(pt, name):
    with pt._get_connection() as conn:
        cur = conn.cursor()
        ts = pt._now()
        cur.execute("INSERT INTO entities (name, scope, created, updated) "
                    "VALUES (?, 'default', ?, ?)", (name, ts, ts))
        conn.commit()
        return cur.lastrowid


def test_import_seeds_doc_edges(palace_lib, library):
    eid = _mk_entity(palace_lib, 'Krem')
    doc_id, _ = library.import_note('default', "Krem's boat manual",
                                    "hull care notes for the boat")
    with palace_lib._get_connection() as conn:
        row = conn.execute("SELECT dst_id FROM edges WHERE src_type = "
                           "'document' AND src_id = ?", (doc_id,)).fetchone()
    assert row and row[0] == eid


def test_spider_walks_to_doc_leaf_priced_by_importance(palace_lib, library):
    from plugins.mindpalace.tools import spider
    eid = _mk_entity(palace_lib, 'Krem')
    hi, _ = library.import_note('default', "Krem's boat manual", "hull care",
                                importance='high')
    lo, _ = library.import_note('default', "Krem's tax archive", "receipts",
                                importance='low')
    with palace_lib._get_connection() as conn:
        cur = conn.cursor()
        chunks, ents, docs = spider.traverse(palace_lib, cur, set(), {eid},
                                             4.0, 'default', None)
        assert hi in docs and lo in docs
        # Human rating prices the step — ALWAYS, no librarian alpha needed.
        assert docs[hi] < docs[lo]
        block = spider._format_block(palace_lib, cur, chunks, ents, 2, docs)
    assert '[doc ' in block and 'boat manual' in block and 'library' in block


def test_doc_leaf_scope_and_private_walls(palace_lib, library):
    from plugins.mindpalace.tools import spider
    eid = _mk_entity(palace_lib, 'Krem')
    # Cross-scope ghost edge: a work-scope doc pointing at default's Krem.
    wdoc, _ = library.import_note('work', "Krem work doc", "other scope")
    with palace_lib._get_connection() as conn:
        conn.execute(
            "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, "
            "weight, created) VALUES ('document', ?, 'entity', ?, 'mentions', "
            "1.0, ?)", (wdoc, eid, palace_lib._now()))
        conn.commit()
    pdoc, _ = library.import_note('default', "Krem's tide tables", "secret",
                                  private_key='tide')
    with palace_lib._get_connection() as conn:
        _c, _e, docs = spider.traverse(palace_lib, conn.cursor(), set(),
                                       {eid}, 8.0, 'default', None)
    assert wdoc not in docs                  # scope wall holds
    assert pdoc not in docs                  # private gate holds
    with palace_lib._get_connection() as conn:
        _c, _e, docs = spider.traverse(palace_lib, conn.cursor(), set(),
                                       {eid}, 8.0, 'default', 'tide')
    assert pdoc in docs                      # the key opens it


def test_delete_document_removes_graph_edges(palace_lib, library):
    _mk_entity(palace_lib, 'Krem')
    doc_id, _ = library.import_note('default', "Krem note", "temp thing")
    library.delete_document('default', doc_id)
    with palace_lib._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM edges WHERE src_type = "
                         "'document'").fetchone()[0]
    assert n == 0


def test_backfill_covers_pre_graph_docs(palace_lib, library):
    _mk_entity(palace_lib, 'Krem')
    with library.get_connection() as conn:   # a doc from before edge seeding
        cur = conn.cursor()
        did = library._insert_document(cur, 'default', "Krem's old doc",
                                       'note', 'med', None, None, None, None,
                                       None, 'ready', 'user')
        conn.commit()
    assert library.backfill_doc_edges() == 1
    assert library.backfill_doc_edges() == 0    # linked docs skip — idempotent
    with palace_lib._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM edges WHERE src_type = "
                         "'document' AND src_id = ?", (did,)).fetchone()[0]
    assert n == 1


def test_catalog_shows_descriptions(library):
    cat, _ = library.create_collection('default', 'Travel', 'trips and plans')
    top, _ = library.create_collection('default', 'Cancun 2026',
                                       'the March trip', parent_id=cat)
    library.import_note('default', "Packing list", "sunscreen and the good hat",
                        description="what to bring", collection_id=top)
    library.import_note('default', "Loose thought", "unfiled note")
    out, ok = library.catalog_text('default')
    assert ok
    assert 'Travel — "trips and plans"' in out
    assert 'Cancun 2026 — "the March trip"' in out
    assert '"what to bring"' in out          # descriptions ARE the surface
    assert '(unfiled)' in out


# --- Arc 2 I1: images -- EXIF, working form, annotations, annotated export ---

def _jpeg_with_exif():
    import io
    from PIL import Image
    img = Image.new('RGB', (48, 32), (200, 30, 60))
    exif = Image.Exif()
    exif[306] = '2024:08:14 17:03:00'
    exif[271] = 'Test'
    exif[272] = 'TestCam 9000'
    gps = exif.get_ifd(0x8825)
    gps[1] = 'N'; gps[2] = (21.0, 9.0, 43.2)
    gps[3] = 'W'; gps[4] = (86.0, 51.0, 5.0)
    exif.get_ifd(0x8769)[36867] = '2024:08:14 17:03:00'
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _png_plain():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (20, 20), (10, 120, 40)).save(buf, 'PNG')
    return buf.getvalue()


def test_image_import_exif_thumb_working(library):
    import json
    did, err = library.import_image('default', 'beach.jpg', _jpeg_with_exif())
    assert err is None
    d = _doc(library, did)
    assert d['kind'] == 'image' and d['status'] == 'ready'
    assert d['importance'] == library.IMPORTANCE['med']   # image default
    with library.get_connection() as conn:
        meta = json.loads(conn.execute(
            'SELECT meta FROM documents WHERE id = ?', (did,)).fetchone()[0])
    assert abs(meta['lat'] - 21.162) < 0.001
    assert abs(meta['lon'] - (-86.8514)) < 0.001
    assert meta['taken'].startswith('2024-08-14')          # colons de-EXIF'd
    assert meta['camera'] == 'TestCam 9000'
    ddir = library.sources_dir() / str(did)
    assert (ddir / 'beach.jpg').exists()
    assert (ddir / 'thumb.jpg').exists()
    md = (ddir / 'metadata.md').read_text(encoding='utf-8')
    assert '# beach' in md and 'taken: 2024-08-14' in md and 'gps: 21.162' in md
    chunks, _ = _counts(library, did)
    assert chunks == 1                                      # whole, like a note
    text, found = library.search_library('default', 'TestCam')
    assert found and '\U0001F5BC' in text and f'[doc {did}]' in text


def test_image_refusals_create_nothing(library):
    did, err = library.import_image('default', 'junk.jpg', b'notanimage')
    assert did is None and 'decode' in err
    did, err = library.import_image('default', 'scan.tiff', b'II*\x00')
    assert did is None and "isn't an importable image" in err
    with library.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 0


def test_image_heic_honest_without_dep(library, monkeypatch):
    monkeypatch.setattr(library, '_heic_ready', lambda: False)
    did, err = library.import_image('default', 'iphone.heic', b'\x00\x00')
    assert did is None and 'pillow-heif' in err


def test_image_annotations_regenerate_working(library):
    import json
    did, _ = library.import_image('default', 'porch.jpg', _jpeg_with_exif(),
                                  title='Porch night')
    ok, err = library.update_image_meta('default', did,
                                        people=['Krem', 'Sapphire'],
                                        notes='green glowstick on the rail')
    assert ok, err
    md = (library.sources_dir() / str(did) / 'metadata.md').read_text('utf-8')
    assert 'Krem, Sapphire' in md and 'green glowstick' in md
    text, found = library.search_library('default', 'glowstick')
    assert found and 'Porch night' in text
    cat = library.catalog_data('default')
    img = cat['unfiled'][0]['image']
    assert img['people'] == ['Krem', 'Sapphire']
    assert img['taken'].startswith('2024-08-14')
    # scope wall
    ok, err = library.update_image_meta('other', did, people=['x'])
    assert not ok
    # text docs refuse
    nid, _ = library.import_note('default', 'Note', 'plain text')
    ok, err = library.update_image_meta('default', nid, notes='nope')
    assert not ok and 'Not an image' in err


def test_image_annotated_export_jpeg_pixels_untouched(library):
    import io
    from PIL import Image
    raw = _jpeg_with_exif()
    did, _ = library.import_image('default', 'beach.jpg', raw,
                                  description='Cancun beach walk')
    library.update_image_meta('default', did, people=['Krem'], notes='sunset')
    data, fname, mime = library.export_annotated('default', did)
    assert data and fname.endswith('_annotated.jpg') and mime == 'image/jpeg'
    assert data[:2] == b'\xff\xd8'
    out = Image.open(io.BytesIO(data))
    exif = out.getexif()
    assert exif[270] == 'Cancun beach walk'                 # ImageDescription
    assert 'Krem' in exif[0x9C9E].decode('utf-16-le')       # XPKeywords
    assert dict(exif.get_ifd(0x8825))                       # GPS survived
    # the pixel stream is byte-identical -- splice never re-encodes
    assert out.tobytes() == Image.open(io.BytesIO(raw)).tobytes()


def test_image_annotated_export_png_itxt(library):
    import io
    from PIL import Image
    raw = _png_plain()
    did, _ = library.import_image('default', 'diagram.png', raw,
                                  description='ESP32 wiring')
    library.update_image_meta('default', did, notes='pin 7 is the trap')
    data, fname, mime = library.export_annotated('default', did)
    assert data and fname.endswith('_annotated.png') and mime == 'image/png'
    assert b'iTXt' in data and b'pin 7 is the trap' in data
    out = Image.open(io.BytesIO(data))
    assert out.tobytes() == Image.open(io.BytesIO(raw)).tobytes()


def test_image_kind_is_intrinsic(library):
    did, _ = library.import_image('default', 'pic.jpg', _jpeg_with_exif())
    ok, err = library.update_document('default', did, kind='note')
    assert not ok                                           # nothing to update
    ok, _ = library.update_document('default', did, kind='note', title='Pic!')
    assert ok
    assert _doc(library, did)['kind'] == 'image'            # kind held


def test_image_upload_route_dispatch(library):
    import asyncio

    class _File:
        def __init__(self, name, data):
            self.filename, self._d = name, data

        async def read(self):
            return self._d

    class _Form:
        def __init__(self, fields, files):
            self._f, self._files = fields, files

        def get(self, k):
            return self._f.get(k)

        def getlist(self, k):
            return self._files if k == 'file' else []

    class _Req:
        def __init__(self, form):
            self._form = form

        async def form(self):
            return self._form

    form = _Form({'scope': 'default', 'importance': 'med',
                  'description': 'trip pics'},
                 [_File('beach.jpg', _jpeg_with_exif()),
                  _File('notes.txt', b'itinerary day one ' * 10)])
    res = asyncio.run(lr.upload(request=_Req(form)))
    assert res['success'] and len(res['imported']) == 2 and not res['refused']
    _drain(library)
    cat = lr.catalog(query={'scope': 'default'})
    kinds = {d['title']: d['kind'] for d in cat['unfiled']}
    assert kinds['beach'] == 'image' and kinds['notes'] != 'image'


def test_image_paths_and_delete_cleanup(library):
    did, _ = library.import_image('default', 'pic.jpg', _jpeg_with_exif())
    src, thumb, render, ext = library.image_paths('default', did)
    assert src and src.name == 'pic.jpg' and thumb and render is None
    assert library.image_paths('other', did) == (None, None, None, None)
    ddir = library.sources_dir() / str(did)
    ok, _ = library.delete_document('default', did)
    assert ok and not ddir.exists()


# --- Arc 2 I3: vision vectors + the photo block (Cancun contract) ------------

class _AlignedVision(_FakeVision):
    """Every image lands exactly on the text fake's vector for 'PIXELS' --
    querying that string gives cosine 1.0, deterministically."""

    def embed_paths(self, paths):
        v = _FakeEmbedder().embed(['PIXELS'])[0]
        return [v for _ in paths]


def test_vision_job_photo_block_and_cleanup(library, monkeypatch):
    monkeypatch.setattr(library, '_vision', lambda: _AlignedVision())
    did, err = library.import_image('default', 'beach.jpg', _jpeg_with_exif())
    assert err is None
    with library.get_connection() as conn:
        stage, state = conn.execute(
            'SELECT stage, state FROM jobs WHERE doc_id = ?', (did,)).fetchone()
    assert stage == 'vision' and state == 'pending'
    _drain(library)
    with library.get_connection() as conn:
        row = conn.execute('SELECT provider, dim FROM img_vectors WHERE '
                           'doc_id = ?', (did,)).fetchone()
        state = conn.execute('SELECT state FROM jobs WHERE doc_id = ?',
                             (did,)).fetchone()[0]
    assert row == ('fake:vision', 64) and state == 'done'
    text, found = library.search_library('default', 'PIXELS')
    assert found and '\U0001F5BC Photos — 1 of 1 matched' in text
    assert 'image RAG' in text                 # she's told HOW these matched
    assert f'[doc {did}] beach' in text and 'view_image' in text
    assert '§' not in text                     # never rendered as sections
    ok, _ = library.delete_document('default', did)
    assert ok
    with library.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM img_vectors').fetchone()[0] == 0


def test_vision_down_never_dams_the_queue(library, monkeypatch):
    monkeypatch.setattr(library, '_vision', lambda: _DownVision())
    did, _ = library.import_image('default', 'pic.jpg', _jpeg_with_exif())
    fid, _ = library.import_file('default', 'notes.txt',
                                 b'chapter text ' * 40)
    _drain(library)
    assert _doc(library, fid)['status'] == 'ready'   # text flowed PAST the dam
    with library.get_connection() as conn:
        assert conn.execute("SELECT state FROM jobs WHERE doc_id = ? AND "
                            "stage = 'vision'", (did,)).fetchone()[0] == 'pending'
    monkeypatch.setattr(library, '_vision', lambda: _FakeVision())
    _drain(library)                                  # model came back
    with library.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM img_vectors WHERE doc_id = ?',
                            (did,)).fetchone()[0] == 1
    assert library.backfill_vision() == 0            # nothing left to queue


def test_photo_metadata_routes_to_pool_not_notes(library):
    did, _ = library.import_image('default', 'porch.jpg', _jpeg_with_exif(),
                                  title='Porch night')
    library.update_image_meta('default', did, people=['Krem'],
                              notes='the green glowstick night')
    text, found = library.search_library('default', 'glowstick')
    assert found and '\U0001F5BC Photos' in text
    assert 'Krem' in text and '2024-08-14' in text   # metadata line, dated
    assert 'the green glowstick night' not in text   # notes stay behind the id


def test_photo_private_wall(library, monkeypatch):
    monkeypatch.setattr(library, '_vision', lambda: _AlignedVision())
    did, _ = library.import_image('default', 'secret.jpg', _jpeg_with_exif())
    _drain(library)
    with library.get_connection() as conn:
        conn.execute("UPDATE documents SET private_key = 'sapphire' "
                     "WHERE id = ?", (did,))
        conn.commit()
    text, found = library.search_library('default', 'PIXELS')
    assert 'secret' not in text
    text, found = library.search_library('default', 'PIXELS',
                                         private_key='sapphire')
    assert found and 'secret' in text


def test_photo_caps_and_counts_mixed(library, monkeypatch):
    monkeypatch.setattr(library, '_vision', lambda: _AlignedVision())
    for i in range(4):
        library.import_image('default', f'trip_{i}.jpg', _jpeg_with_exif(),
                             title=f'Trip {i}')
    _drain(library)
    text, found = library.search_library('default', 'PIXELS', mixed=True)
    assert found and '3 of 4 matched' in text
    assert '1 more behind this search' in text


def test_backfill_vision_queues_the_missing(library):
    did, _ = library.import_image('default', 'old.jpg', _jpeg_with_exif())
    with library.get_connection() as conn:
        conn.execute('DELETE FROM jobs WHERE doc_id = ?', (did,))
        conn.commit()
    assert library.backfill_vision() == 1
    with library.get_connection() as conn:
        assert conn.execute("SELECT stage, state FROM jobs WHERE doc_id = ?",
                            (did,)).fetchone() == ('vision', 'pending')
    assert library.backfill_vision() == 0            # pending job = no double


# --- Arc 2 I4: view_image -- pixels on demand via the image rail -------------

def test_view_image_rides_the_rail(library):
    import base64
    did, _ = library.import_image('default', 'porch.jpg', _jpeg_with_exif(),
                                  title='Porch night')
    library.update_image_meta('default', did, notes='green night')
    out, ok = library.view_image_data('default', did)
    assert ok and isinstance(out, dict)
    assert 'Porch night' in out['text'] and 'green night' in out['text']
    img = out['images'][0]
    assert img['media_type'] == 'image/jpeg'
    assert base64.b64decode(img['data'])[:2] == b'\xff\xd8'
    # walls: wrong scope, private key, non-image docs
    out, ok = library.view_image_data('other', did)
    assert not ok
    with library.get_connection() as conn:
        conn.execute("UPDATE documents SET private_key = 'k' WHERE id = ?",
                     (did,))
        conn.commit()
    assert not library.view_image_data('default', did)[1]
    assert library.view_image_data('default', did, private_key='k')[1]
    nid, _ = library.import_note('default', 'Note', 'text')
    assert not library.view_image_data('default', nid)[1]


def test_view_image_in_toolset_schema():
    from plugins.mindpalace.tools import library_tools as lt
    assert 'view_image' in lt.AVAILABLE_FUNCTIONS
    names = [t['function']['name'] for t in lt.TOOLS]
    assert 'view_image' in names


# --- Captions are retrieval + presentation surface (Krem's tron report) ------

def test_caption_searchable_and_on_the_photo_line(library):
    did, _ = library.import_image('default', 'download (13).jpg',
                                  _jpeg_with_exif(),
                                  description='sapphire in the movie tron')
    md = (library.sources_dir() / str(did) / 'metadata.md').read_text('utf-8')
    assert 'sapphire in the movie tron' in md          # caption is FTS'd
    text, found = library.search_library('default', 'tron')
    assert found and '"sapphire in the movie tron"' in text
    assert f'[doc {did}] download (13)' in text        # title AND caption
    out, ok = library.view_image_data('default', did)
    assert ok and 'sapphire in the movie tron' in out['text']


def test_caption_capped_on_line_full_in_md(library):
    long_desc = 'tron legacy grid ' * 30               # ~510 chars
    did, _ = library.import_image('default', 'x.jpg', _jpeg_with_exif(),
                                  description=long_desc)
    md = (library.sources_dir() / str(did) / 'metadata.md').read_text('utf-8')
    assert long_desc.strip() in md                     # full caption kept
    text, found = library.search_library('default', 'tron')
    line = next(l for l in text.split('\n') if f'[doc {did}]' in l)
    assert '…' in line
    assert len(line) < library.PHOTO_DESC_MAX + 80     # capped render


def test_refresh_image_working_once(library):
    did, _ = library.import_image('default', 'old.jpg', _jpeg_with_exif(),
                                  description='the lost caption')
    # simulate a pre-v2 install: caption-less md + no version marker
    ddir = library.sources_dir() / str(did)
    (ddir / 'metadata.md').write_text('# old\n', encoding='utf-8')
    with library.get_connection() as conn:
        conn.execute("DELETE FROM lib_meta WHERE key = 'img_md_v'")
        conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (did,))
        conn.commit()
    assert library.refresh_image_working() == 1
    md = (ddir / 'metadata.md').read_text('utf-8')
    assert 'the lost caption' in md
    text, found = library.search_library('default', 'lost caption')
    assert found
    assert library.refresh_image_working() == 0        # marker holds


# --- Kermit sip fixes: mixed teasers (A), char cap (C), stitch dedup (D), ----
# --- mixed vector floor (B) --------------------------------------------------

def _import_book(library, name='alice.txt', paras=40):
    # Unique tokens per paragraph — a repetitive corpus would make ANY
    # substring recur and poison duplication asserts.
    text = "\n\n".join(f"Paragraph {i}: the gardeners painted the roses "
                       + " ".join(f"tok{i}_{j}" for j in range(30))
                       for i in range(paras))
    did, err = library.import_file('default', name, text.encode(), kind='book')
    assert not err
    _drain(library)
    return did


def test_mixed_teaser_contract(library):
    did = _import_book(library)
    text, found = library.search_library('default', 'gardeners painted roses',
                                         mixed=True)
    assert found
    block = text.split('\n\U0001F4C4')[1]          # the doc block
    assert 'for the full passage' in block          # handle present
    assert f'read_document({did}, around=' in block
    assert len(block) < 600                         # a taste, not the shelf
    assert block.count('§') == 1               # exactly one section ref
    # explicit search keeps the full stitched treatment
    text, found = library.search_library('default', 'gardeners painted roses')
    assert found and 'page=1 reads from the start' in text


def test_mixed_char_cap_states_the_drop(library, monkeypatch):
    for i in range(4):
        library.import_note('default', f'Long note {i}',
                            f'note {i}: unique riverbank daisies ' + 'x ' * 200)
    monkeypatch.setattr(library, 'LIB_MIXED_CHAR_CAP', 700)
    text, found = library.search_library('default', 'riverbank daisies',
                                         mixed=True, limit=4)
    assert found and 'more document' in text
    assert "search_memory(layer='knowledge') digs deeper" in text
    assert len(text) < 700 + 600 + 200      # first block + one more + notice


def test_mixed_note_teaser_vs_explicit_whole(library):
    long_note = 'The complete saga of the boat. ' * 40      # ~1240 chars
    did, _ = library.import_note('default', 'Boat saga', long_note)
    text, _ = library.search_library('default', 'boat saga', mixed=True)
    assert f'read_document({did}) reads it all' in text
    assert len(text) < len(long_note)                        # truncated
    text, _ = library.search_library('default', 'boat saga')
    assert long_note.strip() in text                         # explicit = whole


def test_stitch_never_duplicates_overlap(library):
    did = _import_book(library)
    with library.get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT seq, content FROM doc_chunks WHERE "
                          "doc_id = ? AND seq > 0 AND content LIKE '…%' "
                          "LIMIT 1", (did,)).fetchone()
        assert row, 'chunker should pack overlaps on a book'
        seq, content = row
        stitched = library._stitch(cur, did, seq, content)
        # packed overlap IS the prev context — no second copy prepended
        assert stitched.startswith(content[:80])
        tail = content[1:120].split('\n')[0]
        assert stitched.count(tail) == 1


def test_mixed_uses_raised_vector_floor(library, monkeypatch):
    seen = []

    def spy(scope, query, limit, floor=library.VEC_MIN):
        seen.append(floor)
        return []

    monkeypatch.setattr(library, '_vector_hits', spy)
    library.search_library('default', 'anything')
    library.search_library('default', 'anything', mixed=True)
    assert seen == [library.VEC_MIN, library.VEC_MIN_MIXED]
    assert library.VEC_MIN_MIXED == 0.40


# --- Arc 2 I5: watch-folders -- index in place, the folder is canonical ------

@pytest.fixture
def farm(tmp_path):
    """A folder with one image, one markdown note, one unsupported file.
    mtimes backdated past the settle window — fresh files deliberately wait
    one scan (mid-copy guard), which is its own test."""
    import os as _os
    import time as _time
    d = tmp_path / 'vault'
    d.mkdir()
    (d / 'beach.jpg').write_bytes(_jpeg_with_exif())
    (d / 'boat-notes.md').write_text('# The Boat\n\nrobot body ' * 30)
    (d / 'skipme.docx').write_bytes(b'nope')
    (d / '.hidden.md').write_text('secret')
    old = _time.time() - 60
    for f in d.iterdir():
        _os.utime(f, (old, old))
    return d


def _watch_docs(library, fid):
    with library.get_connection() as conn:
        return {r[1]: (r[0], r[2]) for r in conn.execute(
            "SELECT id, title, status FROM documents "
            "WHERE json_extract(meta, '$.watch') = ?", (fid,)).fetchall()}


def test_watch_add_refusals(library, farm):
    assert library.add_watch_folder('default', 'relative/path')[0] is None
    assert library.add_watch_folder('default', str(farm / 'nope'))[0] is None
    assert library.add_watch_folder('default', '/')[0] is None
    fid, err = library.add_watch_folder('default', str(library.sources_dir()))
    assert fid is None and 'loop' in err


def test_watch_scan_two_speed(library, farm, monkeypatch):
    # add_watch_folder spawns a scan thread -- neutralize, drive by hand
    import threading as _t
    monkeypatch.setattr(_t, 'Thread',
                        lambda *a, **k: type('N', (), {'start': lambda s: None})())
    fid, err = library.add_watch_folder('default', str(farm))
    assert fid and not err
    rep = library.scan_folder(fid)
    assert rep['added'] == 2 and rep['total'] == 2      # docx + dotfile skipped
    docs = _watch_docs(library, fid)
    assert set(docs) == {'beach', 'boat-notes'}
    # instant leg: image findable by camera metadata BEFORE any queue work
    text, found = library.search_library('default', 'TestCam')
    assert found
    # source stayed in place -- nothing copied into the library store
    with library.get_connection() as conn:
        src = conn.execute('SELECT source_path FROM documents WHERE id = ?',
                           (docs['beach'][0],)).fetchone()[0]
    assert src == str(farm / 'beach.jpg')
    # slow leg: text doc chunks arrive via the queue
    _drain(library)
    assert _doc(library, docs['boat-notes'][0])['status'] == 'ready'
    text, found = library.search_library('default', 'robot body')
    assert found
    rep2 = library.scan_folder(fid)                    # quiet rescan = no-op
    assert rep2['added'] == 0 and rep2['changed'] == 0


def test_watch_missing_revive_and_change(library, farm, monkeypatch):
    import os
    import threading as _t
    monkeypatch.setattr(_t, 'Thread',
                        lambda *a, **k: type('N', (), {'start': lambda s: None})())
    fid, _ = library.add_watch_folder('default', str(farm))
    library.scan_folder(fid)
    _drain(library)
    docs = _watch_docs(library, fid)
    mid = docs['boat-notes'][0]
    # vanish -> missing, excluded from search, chunks preserved
    hidden = farm / 'boat-notes.md.bak'
    (farm / 'boat-notes.md').rename(hidden)
    rep = library.scan_folder(fid)
    assert rep['missing'] == 1
    assert _doc(library, mid)['status'] == 'missing'
    text, found = library.search_library('default', 'robot body')
    assert 'boat-notes' not in text
    txt, ok = library.read_document_text('default', mid)
    assert not ok and 'missing' in txt
    # return -> revive
    hidden.rename(farm / 'boat-notes.md')
    rep = library.scan_folder(fid)
    assert rep['revived'] == 1
    assert _doc(library, mid)['status'] == 'ready'
    text, found = library.search_library('default', 'robot body')
    assert found
    # edit -> re-derive (mtime/size change)
    (farm / 'boat-notes.md').write_text('# The Boat\n\nsolar sails ' * 40)
    os.utime(farm / 'boat-notes.md', (1e9, 2e9))
    rep = library.scan_folder(fid)
    assert rep['changed'] == 1
    _drain(library)
    text, found = library.search_library('default', 'solar sails')
    assert found


def test_watch_remove_keeps_real_files(library, farm, monkeypatch):
    import threading as _t
    monkeypatch.setattr(_t, 'Thread',
                        lambda *a, **k: type('N', (), {'start': lambda s: None})())
    fid, _ = library.add_watch_folder('default', str(farm))
    library.scan_folder(fid)
    assert library.remove_watch_folder('other', fid) == (False, "Watch folder not found.")
    ok, _ = library.remove_watch_folder('default', fid)
    assert ok
    with library.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 0
    assert (farm / 'beach.jpg').exists()               # HER files, untouched
    assert library.folders_data('default') == []


# --- C polish: migrated-title tidy + exact-duplicate merge -------------------

def test_derive_title_cleans_markdown():
    assert lib._derive_title('**Deep-Sea Fish With Hybrid Vision (Feb 2026)**'
                             '\n\nScientists discovered') == \
        'Deep-Sea Fish With Hybrid Vision (Feb 2026)'
    assert lib._derive_title('# Heading\nbody') == 'Heading'
    assert lib._derive_title('\n\n  plain line  \n') == 'plain line'
    long = 'word ' * 30
    t = lib._derive_title(long)
    assert len(t) <= 61 and t.endswith('…')
    assert lib._derive_title('') == 'Untitled'


def test_tidy_migrated_titles_once(library):
    bad = '**Deep-Sea Fish**\n\nScientists'
    did = lib._enqueue_text_doc('default', bad,
                                '**Deep-Sea Fish**\n\nScientists found things.',
                                'note', 'med', None, 'user',
                                {'import_key': 'k1', 'migrated_from': 'v2-chunks'})
    hand, _ = library.import_note('default', 'x#y', 'hand-made, not migrated')
    _drain(library)
    assert library.tidy_migrated_titles() == 1
    with library.get_connection() as conn:
        t = conn.execute('SELECT title FROM documents WHERE id = ?',
                         (did,)).fetchone()[0]
        h = conn.execute('SELECT title FROM documents WHERE id = ?',
                         (hand,)).fetchone()[0]
    assert t == 'Deep-Sea Fish' and '\n' not in t
    assert h == 'x#y'                                  # hand-made untouched
    assert library.tidy_migrated_titles() == 0         # marker holds


def test_merge_migrated_duplicates_keeps_richer(library):
    content = 'The exact same migrated note body, twice.'
    cat, _ = library.create_collection('default', 'Shelf')
    a = lib._enqueue_text_doc('default', 'Copy A', content, 'note', 'med',
                              None, 'user',
                              {'import_key': 'ka', 'migrated_from': 'v1-knowledge'})
    b = lib._enqueue_text_doc('default', 'Copy B', content, 'note', 'med',
                              cat, 'user',
                              {'import_key': 'kb', 'migrated_from': 'v2-chunks'})
    hand, _ = library.import_note('default', 'Hand copy', content)
    _drain(library)
    assert library.merge_migrated_duplicates() == 1
    with library.get_connection() as conn:
        alive = {r[0] for r in conn.execute('SELECT id FROM documents').fetchall()}
    assert b in alive and hand in alive                # shelved copy + hand-made kept
    assert a not in alive                              # bare duplicate merged away
    assert library.merge_migrated_duplicates() == 0    # idempotent
