# plugins/mindpalace/tools/library.py
# Knowledge Layer v3 — "the Library" foundation (P1, 2026-07-17).
# Spec: tmp/knowledge-layer.md. Sources are canonical; chunks + vectors are
# rebuildable cache. Separate library.db beside mind.db (bulk text never
# rides the mind's nightly diff). No tool registration yet (P3/P5) — this
# module is the storage + import engine only.
#
# Shape:
#   collections  Category → Topic (2 levels, parent_id), name + DESCRIPTION
#   documents    kind note/article/book/reference, importance human-set,
#                source_path (original, byte-exact) + working_path (living md)
#   doc_chunks   doc_id + seq + chapter breadcrumb, book-grade sizes
#   doc_vectors  int8-quantized nomic embeddings (scale per vector)
#   jobs         resumable background import (batch-atomic cursor)
#
# Import contract: notes are synchronous (small); files enqueue and a daemon
# worker drains — every batch commits chunks+vectors+cursor together, so a
# restart mid-book resumes exactly where it died.

import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

IMPORTANCE = {'low': 0.3, 'med': 0.6, 'high': 0.9}
# kind → (chunk_size, overlap). note = whole (split only past NOTE_SPLIT).
PROFILES = {'article': (1200, 150), 'book': (1800, 200), 'reference': (1200, 300)}
NOTE_SPLIT = 4000
EMBED_BATCH = 32
DOC_CHUNK_GUARD = 20_000   # one corrupt/absurd file can't explode the store
TEXT_KINDS = {'.txt', '.md', '.markdown'}
FILE_KINDS = TEXT_KINDS | {'.pdf', '.epub'}
# Auto kind by extension when the modal doesn't say (epubs are books, PDFs
# are usually manuals/notes → reference).
_EXT_KIND = {'.epub': 'book', '.pdf': 'reference'}
# Images (Arc 2) — kind='image' documents: original is canonical, the working
# form is a GENERATED metadata.md rendered from documents.meta (EXIF + people
# + notes). HEIC needs pillow-heif (in requirements; refused honestly if the
# install predates it).
IMAGE_KINDS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif'}
_BROWSER_SAFE = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
THUMB_SIZE = 512
RENDER_SIZE = 1600

_db_path = None            # tests monkeypatch; None = beside mind.db
_db_initialized = False
_worker_lock = threading.Lock()
_worker_running = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _embedder():
    """Seam — tests monkeypatch this."""
    from core.embeddings import get_embedder
    return get_embedder()


def _vision():
    """Seam — tests monkeypatch this. The vision tower shares the text
    model's vector space (nomic v1.5 pair), so text queries rank pixels."""
    from plugins.mindpalace.tools.vision_embed import get_vision_embedder
    return get_vision_embedder()


def get_db_path() -> Path:
    global _db_path
    if _db_path is None:
        _db_path = Path(_pt()._get_db_path()).parent / 'library.db'
    return Path(_db_path)


def sources_dir() -> Path:
    d = get_db_path().parent / 'library'
    d.mkdir(parents=True, exist_ok=True)
    return d


_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY, parent_id INTEGER,
    name TEXT NOT NULL, description TEXT,
    scope TEXT NOT NULL DEFAULT 'default',
    created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, collection_id INTEGER,
    scope TEXT NOT NULL DEFAULT 'default',
    title TEXT NOT NULL, author TEXT, description TEXT,
    kind TEXT NOT NULL DEFAULT 'note',
    importance REAL NOT NULL DEFAULT 0.6,
    source_path TEXT, working_path TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    private_key TEXT, added_by TEXT, meta TEXT,
    created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS doc_chunks (
    id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL,
    seq INTEGER NOT NULL, chapter TEXT,
    content TEXT NOT NULL, created TEXT);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks(doc_id, seq);
CREATE TABLE IF NOT EXISTS doc_vectors (
    chunk_id INTEGER PRIMARY KEY,
    provider TEXT, dim INTEGER, scale REAL, q BLOB);
CREATE TABLE IF NOT EXISTS img_vectors (
    doc_id INTEGER PRIMARY KEY,
    provider TEXT, dim INTEGER, scale REAL, q BLOB);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'chunk',
    cursor INTEGER NOT NULL DEFAULT 0, total INTEGER,
    state TEXT NOT NULL DEFAULT 'pending', error TEXT,
    created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS lib_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watch_folders (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL DEFAULT 'default',
    path TEXT NOT NULL, collection_id INTEGER,
    last_scan TEXT, created TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    content, content='doc_chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS doc_fts_ai AFTER INSERT ON doc_chunks BEGIN
    INSERT INTO doc_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS doc_fts_ad AFTER DELETE ON doc_chunks BEGIN
    INSERT INTO doc_fts(doc_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS doc_fts_au AFTER UPDATE ON doc_chunks BEGIN
    INSERT INTO doc_fts(doc_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO doc_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


@contextmanager
def get_connection():
    """Closes on exit (sqlite3's own ctx manager commits but never closes —
    a drain loop would leak handles). Commits are explicit at call sites."""
    import sqlite3
    global _db_initialized
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        if not _db_initialized:
            conn.executescript(_SCHEMA)
            conn.commit()
            _db_initialized = True
        yield conn
    finally:
        conn.close()


# ─── Text handling ───────────────────────────────────────────────────────────

def decode_bytes(raw: bytes) -> str:
    """utf-8 first, cp1252 second (the Win10 default), replace as last resort."""
    for enc in ('utf-8', 'cp1252'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


# ─── Format extractors (P2) — refusals happen BEFORE any row exists ──────────

def _extract(ext, raw):
    """→ (markdown_text, file_meta, error). error = honest refusal (scanned
    PDF, DRM epub, corrupt file) — the caller creates nothing on refusal."""
    if ext in TEXT_KINDS:
        return decode_bytes(raw), {}, None
    if ext == '.pdf':
        return _extract_pdf(raw)
    if ext == '.epub':
        return _extract_epub(raw)
    return None, {}, f"'{ext}' isn't importable — txt, md, pdf, epub."


def _extract_pdf(raw):
    """pypdf text layer. Scanned PDFs have none — refused honestly, no OCR
    (Krem's ruling: 'yeet those')."""
    import io
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            try:
                reader.decrypt('')
            except Exception:
                return None, {}, "PDF is password-protected — can't read it."
        pages = [(p.extract_text() or '').strip() for p in reader.pages]
        text = '\n\n'.join(p for p in pages if p)
        if not text or len(text) < 40 * max(1, len(pages)):
            return None, {}, ("This PDF has no usable text layer (scanned "
                              "pages?) — OCR isn't supported. Export it as "
                              "text and import that instead.")
        meta = {}
        try:
            info = reader.metadata
            if info and info.title:
                meta['title'] = str(info.title).strip()
            if info and info.author:
                meta['author'] = str(info.author).strip()
        except Exception:
            pass
        return text, meta, None
    except Exception as e:
        return None, {}, f"PDF parse failed: {e}"


class _HTMLText(HTMLParser):
    """EPUB chapter HTML → plain text + first heading (stdlib, no bs4).
    Heading text is captured separately and re-emitted as a markdown # so
    the chunker's chapter breadcrumbs work."""
    def __init__(self):
        super().__init__()
        self.parts, self.title = [], None
        self._skip = 0
        self._in_head = False
        self._head = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip += 1
        elif tag in ('h1', 'h2', 'h3') and self.title is None:
            self._in_head = True
        elif tag in ('p', 'div', 'br', 'li', 'tr'):
            self.parts.append('\n\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = max(0, self._skip - 1)
        elif tag in ('h1', 'h2', 'h3') and self._in_head:
            self._in_head = False
            t = ' '.join(''.join(self._head).split())
            if t and self.title is None:
                self.title = t

    def handle_data(self, data):
        if self._skip:
            return
        (self._head if self._in_head else self.parts).append(data)


def _html_to_text(html):
    p = _HTMLText()
    try:
        p.feed(html)
    except Exception:
        pass
    text = re.sub(r'\n{3,}', '\n\n',
                  '\n'.join(l.strip() for l in ''.join(p.parts).split('\n')))
    return text.strip(), p.title


def _extract_epub(raw):
    """ebooklib needs a real path — staged through a temp file. Each spine
    document becomes a '# chapter' section in the working markdown."""
    import tempfile
    try:
        import ebooklib
        from ebooklib import epub
        with tempfile.NamedTemporaryFile(suffix='.epub') as tf:
            tf.write(raw)
            tf.flush()
            book = epub.read_epub(tf.name, options={'ignore_ncx': True})
        parts = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            text, head = _html_to_text(
                item.get_content().decode('utf-8', 'replace'))
            if not text:
                continue
            parts.append(f"# {head}\n\n{text}" if head else text)
        text = '\n\n'.join(parts)
        if not text.strip():
            return None, {}, ("EPUB contained no readable text — likely "
                              "DRM-protected. Import a DRM-free copy.")
        meta = {}
        try:
            t = book.get_metadata('DC', 'title')
            a = book.get_metadata('DC', 'creator')
            if t:
                meta['title'] = str(t[0][0]).strip()
            if a:
                meta['author'] = str(a[0][0]).strip()
        except Exception:
            pass
        return text, meta, None
    except Exception as e:
        return None, {}, f"EPUB parse failed (DRM or corrupt?): {e}"


# ─── Images (Arc 2, I1) ──────────────────────────────────────────────────────
# One layer, two views (ruled 2026-07-17): images are documents, kind='image'.
# documents.meta is canonical for annotations (people/notes + EXIF facts);
# metadata.md is a RENDERED view of it — regenerated on every change so the
# working form, FTS, vectors, and graph edges never drift apart.

def _heic_ready():
    """pillow-heif registers a Pillow opener for .heic/.heif. In requirements
    since Arc 2; installs that predate it get an honest refusal instead."""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        return True
    except ImportError:
        return False


def _gps_decimal(dms, ref):
    try:
        deg = float(dms[0]) + float(dms[1]) / 60.0 + float(dms[2]) / 3600.0
        return round(-deg if ref in ('S', 'W') else deg, 6)
    except Exception:
        return None


def _exif_data(img):
    """Pillow EXIF → the facts worth keeping. Missing/corrupt EXIF is normal
    (screenshots, stripped web images) — everything here is best-effort."""
    out = {'width': img.width, 'height': img.height}
    try:
        exif = img.getexif()
        model = str(exif.get(272) or '').strip()      # Model
        make = str(exif.get(271) or '').strip()       # Make
        if model:
            out['camera'] = model if (not make or make.lower() in
                                      model.lower()) else f'{make} {model}'
        ifd = exif.get_ifd(0x8769)
        taken = (ifd.get(36867) or exif.get(306) or '')   # DateTimeOriginal
        if taken:
            # EXIF dates are 'YYYY:MM:DD HH:MM:SS' — colons to dashes.
            t = str(taken).strip()
            if len(t) >= 10 and t[4] == ':' and t[7] == ':':
                t = f'{t[:4]}-{t[5:7]}-{t[8:10]}{t[10:]}'
            out['taken'] = t
        gps = exif.get_ifd(0x8825)
        lat = _gps_decimal(gps.get(2), gps.get(1)) if gps.get(2) else None
        lon = _gps_decimal(gps.get(4), gps.get(3)) if gps.get(4) else None
        if lat is not None and lon is not None:
            out['lat'], out['lon'] = lat, lon
    except Exception as e:
        logger.debug(f"[LIBRARY] EXIF read skipped: {e}")
    return out


IMG_MD_VERSION = '2'   # bump when the render below changes → boot regen


def _render_image_md(title, meta, description=None):
    """The image's working form. Every line is retrieval surface — the
    CAPTION (description), place name, people, and notes all land in FTS +
    the text vector. A photo titled 'download (13)' is findable only by
    what's written here."""
    m = meta or {}
    lines = [f'# {title}', '']
    if (description or '').strip():
        lines += [description.strip(), '']
    for key, label in (('taken', 'taken'), ('camera', 'camera'),
                       ('place', 'place')):
        if m.get(key):
            lines.append(f'- {label}: {m[key]}')
    if m.get('lat') is not None and m.get('lon') is not None:
        lines.append(f"- gps: {m['lat']}, {m['lon']}")
    if m.get('width'):
        lines.append(f"- size: {m['width']}×{m['height']}")
    if m.get('people'):
        lines += ['', '## People', '', ', '.join(m['people'])]
    if m.get('notes'):
        lines += ['', '## Notes', '', m['notes']]
    return '\n'.join(lines)


def _write_image_working(scope, doc_id, title, description, meta):
    """(Re)generate metadata.md + its single chunk + text vector + edges.
    Called on import and on every annotation change — one path, no drift."""
    ddir = sources_dir() / str(doc_id)
    ddir.mkdir(parents=True, exist_ok=True)
    md = _render_image_md(title, meta, description)
    working = ddir / 'metadata.md'
    working.write_text(md, encoding='utf-8')
    chunks = chunk_text(md, 'image')
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM doc_vectors WHERE chunk_id IN '
                    '(SELECT id FROM doc_chunks WHERE doc_id = ?)', (doc_id,))
        cur.execute('DELETE FROM doc_chunks WHERE doc_id = ?', (doc_id,))
        ids = _write_chunks(cur, doc_id, chunks)
        _embed_and_store(cur, list(zip(ids, [c for _, c in chunks])))
        cur.execute('UPDATE documents SET working_path = ?, updated = ? '
                    'WHERE id = ?', (str(working), _now(), doc_id))
        conn.commit()
    _matrix_cache.clear()
    _seed_doc_edges(scope, doc_id, f"{title}\n{description or ''}\n{md}")
    return working


def import_image(scope, filename, raw: bytes, title=None, description=None,
                 collection_id=None, importance=None, added_by='user'):
    """Typed add-modal 'Images': decode-verify FIRST (corrupt file creates
    nothing), EXIF → meta, original + thumbnail + metadata.md into sources/.
    Synchronous — the text side of an image is tiny. Vision vectors arrive
    via the queue (I3). Returns (doc_id, error)."""
    import io
    from PIL import Image, ImageOps
    filename = Path(filename or 'image.jpg').name
    ext = Path(filename).suffix.lower()
    if ext not in IMAGE_KINDS:
        return None, f"'{ext}' isn't an importable image — jpg, png, gif, webp, heic."
    if ext in ('.heic', '.heif') and not _heic_ready():
        return None, ("HEIC needs the pillow-heif package — "
                      "`pip install pillow-heif`, then retry.")
    if not raw:
        return None, "Empty file."
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None, f"'{filename}' doesn't decode as an image — corrupt file?"
    meta = _exif_data(img)
    meta['people'] = []
    if meta.get('lat') is not None:
        # GPS → place name, offline (I2). No dataset yet = first-use
        # download kicks off in the background; backfill_places() names
        # this photo once the atlas arrives.
        from plugins.mindpalace.tools import geonames
        place = geonames.reverse(meta['lat'], meta['lon'])
        if place:
            meta['place'] = place
        else:
            geonames.ensure_dataset_async(on_ready=backfill_places)
    title = (title or '').strip() or Path(filename).stem
    with get_connection() as conn:
        cur = conn.cursor()
        doc_id = _insert_document(
            cur, scope, title, 'image', importance or 'med', collection_id,
            (description or '').strip() or None, None, None, None, 'ready',
            added_by, meta=meta)
        conn.commit()
    ddir = sources_dir() / str(doc_id)
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / filename).write_bytes(raw)
    try:
        thumb = ImageOps.exif_transpose(img)
        thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
        thumb.convert('RGB').save(ddir / 'thumb.jpg', 'JPEG', quality=85)
    except Exception as e:
        logger.warning(f"[LIBRARY] thumbnail failed for doc {doc_id}: {e}")
    with get_connection() as conn:
        cur = conn.cursor()
        now = _now()
        cur.execute('UPDATE documents SET source_path = ? WHERE id = ?',
                    (str(ddir / filename), doc_id))
        # Pixels embed in the background (vision model is slow + lazy-
        # downloaded); the metadata text below serves search immediately.
        cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                    'VALUES (?, ?, ?, ?)', (doc_id, 'vision', now, now))
        conn.commit()
    _write_image_working(scope, doc_id, title, description, meta)
    ensure_worker()
    return doc_id, None


def update_image_meta(scope, doc_id, people=None, notes=None):
    """Lightbox annotations (and later, her tool). people = full replacement
    list; notes = replacement text. Regenerates the working form + vectors +
    edges — people names that match entities become mention edges, so tagging
    Krem in a photo wires it into the spider. Returns (ok, error)."""
    with get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT scope, title, description, kind, meta FROM '
                          'documents WHERE id = ?', (int(doc_id),)).fetchone()
        if not row or row[0] != scope:
            return False, f"No document [{doc_id}] in this scope."
        if row[3] != 'image':
            return False, "Not an image document."
        meta = json.loads(row[4] or '{}')
        if people is not None:
            meta['people'] = [str(p).strip() for p in people if str(p).strip()]
        if notes is not None:
            meta['notes'] = str(notes).strip()
        cur.execute('UPDATE documents SET meta = ?, updated = ? WHERE id = ?',
                    (json.dumps(meta, ensure_ascii=False), _now(), int(doc_id)))
        conn.commit()
        title, description = row[1], row[2]
    _write_image_working(scope, int(doc_id), title, description, meta)
    return True, None


def backfill_places():
    """Boot hook + post-download hook: image docs with GPS but no place name
    yet — covers photos imported before the GeoNames dataset finished its
    first-use download. Idempotent (named photos never re-enter the scan)."""
    from plugins.mindpalace.tools import geonames
    if not geonames.available():
        return 0
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, scope, title, description, meta FROM documents "
                "WHERE kind = 'image' "
                "AND json_extract(meta, '$.lat') IS NOT NULL "
                "AND json_extract(meta, '$.place') IS NULL").fetchall()
        made = 0
        for did, scope, title, desc, meta_raw in rows:
            try:
                meta = json.loads(meta_raw or '{}')
                place = geonames.reverse(meta.get('lat'), meta.get('lon'))
                if not place:
                    continue
                meta['place'] = place
                with get_connection() as conn:
                    conn.execute('UPDATE documents SET meta = ?, updated = ? '
                                 'WHERE id = ?',
                                 (json.dumps(meta, ensure_ascii=False), _now(), did))
                    conn.commit()
                _write_image_working(scope, did, title, desc, meta)
                made += 1
            except Exception as e:
                # One malformed row must not strand the rest of the batch.
                logger.warning(f"[LIBRARY] geocode skipped for doc {did}: {e}")
        if made:
            logger.info(f"[LIBRARY] geocoded {made} image(s)")
        return made
    except Exception as e:
        logger.warning(f"[LIBRARY] place backfill skipped: {e}")
        return 0


def refresh_image_working():
    """Boot hook, marker-gated on IMG_MD_VERSION: when the metadata.md render
    evolves (e.g. captions joined it, v2), every image's working form
    regenerates ONCE so already-imported photos become findable the new way.
    No-op forever after."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            if _lib_meta(cur, 'img_md_v') == IMG_MD_VERSION:
                return 0
            rows = cur.execute("SELECT id, scope, title, description, meta "
                               "FROM documents WHERE kind = 'image'").fetchall()
        for did, scope, title, desc, meta_raw in rows:
            try:
                meta = json.loads(meta_raw or '{}')
            except Exception:
                meta = {}
            _write_image_working(scope, did, title, desc, meta)
        with get_connection() as conn:
            cur = conn.cursor()
            _lib_meta(cur, 'img_md_v', IMG_MD_VERSION)
            conn.commit()
        if rows:
            logger.info(f"[LIBRARY] regenerated {len(rows)} image working "
                        f"form(s) → v{IMG_MD_VERSION}")
        return len(rows)
    except Exception as e:
        logger.warning(f"[LIBRARY] image working refresh skipped: {e}")
        return 0


def backfill_vision():
    """Boot hook: image docs with no pixel vector and no live vision job get
    one queued — covers images imported before I3 existed and any that rode
    out a model-down window. Idempotent."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT d.id FROM documents d "
                "LEFT JOIN img_vectors v ON v.doc_id = d.id "
                "WHERE d.kind = 'image' AND d.source_path IS NOT NULL "
                "AND v.doc_id IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM jobs j WHERE j.doc_id = d.id "
                " AND j.stage = 'vision' AND j.state = 'pending')").fetchall()
            now = _now()
            for (did,) in rows:
                cur.execute('INSERT INTO jobs (doc_id, stage, created, '
                            'updated) VALUES (?, ?, ?, ?)',
                            (did, 'vision', now, now))
            conn.commit()
        if rows:
            logger.info(f"[LIBRARY] queued vision embed for {len(rows)} image(s)")
            ensure_worker()
        return len(rows)
    except Exception as e:
        logger.warning(f"[LIBRARY] vision backfill skipped: {e}")
        return 0


def image_paths(scope, doc_id):
    """(original, thumb, render_or_None, ext) for serving. render is the
    browser-safe fallback for formats browsers can't show (HEIC) — generated
    lazily, cached beside the source, rebuildable."""
    from PIL import Image, ImageOps
    with get_connection() as conn:
        row = conn.execute('SELECT scope, source_path, kind FROM documents '
                           'WHERE id = ?', (int(doc_id),)).fetchone()
    if not row or row[0] != scope or row[2] != 'image' or not row[1]:
        return None, None, None, None
    src = Path(row[1])
    if not src.exists():
        return None, None, None, None
    ddir = src.parent
    thumb = ddir / 'thumb.jpg'
    ext = src.suffix.lower()
    render = None
    if ext not in _BROWSER_SAFE:
        render = ddir / 'render.jpg'
        if not render.exists():
            try:
                _heic_ready()
                img = ImageOps.exif_transpose(Image.open(src))
                img.thumbnail((RENDER_SIZE, RENDER_SIZE))
                img.convert('RGB').save(render, 'JPEG', quality=90)
            except Exception as e:
                logger.warning(f"[LIBRARY] render failed for doc {doc_id}: {e}")
                render = None
    return src, (thumb if thumb.exists() else None), render, ext


# ─── Watch-folders (Arc 2, I5) — index in place, the folder is canonical ─────
# No copies: source_path points INTO the watched folder; everything the
# library derives (working md, thumbs, chunks, vectors) is rebuildable cache
# under sources/. Two-speed contract: a scan makes files findable INSTANTLY
# by filename/EXIF/metadata (synchronous, cheap); text chunking and vision
# ride the resumable queue. Vanished files go status='missing' (greyed,
# excluded from search, annotations preserved) and REVIVE if the file
# returns — a USB drive unplugging must never destroy her notes.

WATCH_EXTS = FILE_KINDS | IMAGE_KINDS
WATCH_MAX_FILES = 20_000          # per folder, foot-gun guard
SCAN_THROTTLE_S = 30.0
WATCH_SETTLE_S = 3.0              # files younger than this are still copying

_scan_state_lock = threading.Lock()
_scan_running = False
_last_scan_mono = 0.0


def add_watch_folder(scope, path, collection_id=None):
    """Returns (folder_id, error). First scan kicks off in the background —
    the UI's job polling shows arrivals."""
    try:
        p = Path(path).expanduser()
    except Exception:
        return None, "That's not a usable path."
    if not p.is_absolute():
        return None, "Watch folders need an absolute path."
    p = p.absolute()
    if not p.is_dir():
        return None, f"'{p}' isn't a folder on this machine."
    if p == Path(p.anchor):
        return None, "Watching the filesystem root is a foot-gun — pick a folder."
    lib_root = sources_dir().absolute()
    if p == lib_root or lib_root in p.parents or p in lib_root.parents:
        return None, "That's the library's own store — watching it would loop."
    with get_connection() as conn:
        cur = conn.cursor()
        if cur.execute('SELECT 1 FROM watch_folders WHERE scope = ? AND '
                       'path = ?', (scope, str(p))).fetchone():
            return None, "Already watching that folder."
        cur.execute('INSERT INTO watch_folders (scope, path, collection_id, '
                    'created) VALUES (?, ?, ?, ?)',
                    (scope, str(p), collection_id, _now()))
        fid = cur.lastrowid
        conn.commit()
    threading.Thread(target=lambda: scan_folder(fid),
                     name=f'watch-scan-{fid}', daemon=True).start()
    return fid, None


def remove_watch_folder(scope, fid):
    """Folder row + every doc it produced (derived cache only — the actual
    files in the folder are untouched, they were never ours)."""
    with get_connection() as conn:
        row = conn.execute('SELECT scope FROM watch_folders WHERE id = ?',
                           (int(fid),)).fetchone()
        if not row or row[0] != scope:
            return False, "Watch folder not found."
        dids = [r[0] for r in conn.execute(
            "SELECT id FROM documents WHERE json_extract(meta, '$.watch') = ?",
            (int(fid),)).fetchall()]
    for did in dids:
        delete_document(scope, did)
    with get_connection() as conn:
        conn.execute('DELETE FROM watch_folders WHERE id = ?', (int(fid),))
        conn.commit()
    return True, None


def folders_data(scope):
    out = []
    with get_connection() as conn:
        for fid, path, cid, last in conn.execute(
                'SELECT id, path, collection_id, last_scan FROM watch_folders '
                'WHERE scope = ? ORDER BY path', (scope,)).fetchall():
            total, missing = conn.execute(
                "SELECT COUNT(*), SUM(status = 'missing') FROM documents "
                "WHERE json_extract(meta, '$.watch') = ?", (fid,)).fetchone()
            out.append({'id': fid, 'path': path, 'collection_id': cid,
                        'last_scan': last, 'total': total,
                        'missing': missing or 0})
    return out


def _watch_files(root: Path):
    """relpath → (mtime, size) for every supported, non-hidden file."""
    files = {}
    try:
        for f in root.rglob('*'):
            rel = f.relative_to(root)
            if any(part.startswith('.') for part in rel.parts):
                continue
            if not f.is_file() or f.suffix.lower() not in WATCH_EXTS:
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            files[str(rel)] = (st.st_mtime, st.st_size)
            if len(files) >= WATCH_MAX_FILES:
                logger.warning(f"[LIBRARY] watch folder {root} hit the "
                               f"{WATCH_MAX_FILES}-file guard — truncating")
                break
    except OSError as e:
        logger.warning(f"[LIBRARY] watch walk failed for {root}: {e}")
    return files


def _watch_ingest(scope, fid, root, rel, mtime, size, collection_id):
    """One new file → one document, in place. Images land fully findable
    (EXIF + thumb + working form) right now; text queues its heavy lifting."""
    full = root / rel
    ext = full.suffix.lower()
    title = full.stem
    meta = {'watch': fid, 'relpath': rel, 'mtime': mtime, 'size': size}
    if ext in IMAGE_KINDS:
        from PIL import Image, ImageOps
        if ext in ('.heic', '.heif') and not _heic_ready():
            return None
        try:
            img = Image.open(full)
            img.load()
        except Exception:
            return None
        meta.update(_exif_data(img))
        meta['people'] = []
        if meta.get('lat') is not None:
            from plugins.mindpalace.tools import geonames
            place = geonames.reverse(meta['lat'], meta['lon'])
            if place:
                meta['place'] = place
            else:
                geonames.ensure_dataset_async(on_ready=backfill_places)
        with get_connection() as conn:
            cur = conn.cursor()
            doc_id = _insert_document(cur, scope, title, 'image', 'low',
                                      collection_id, None, None, str(full),
                                      None, 'ready', 'watch', meta=meta)
            now = _now()
            cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                        'VALUES (?, ?, ?, ?)', (doc_id, 'vision', now, now))
            conn.commit()
        ddir = sources_dir() / str(doc_id)
        ddir.mkdir(parents=True, exist_ok=True)
        try:
            thumb = ImageOps.exif_transpose(img)
            thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
            thumb.convert('RGB').save(ddir / 'thumb.jpg', 'JPEG', quality=85)
        except Exception as e:
            logger.warning(f"[LIBRARY] watch thumbnail failed for {full}: {e}")
        _write_image_working(scope, doc_id, title, None, meta)
        return doc_id
    try:
        raw = full.read_bytes()
    except OSError:
        return None
    text, fmeta, err = _extract(ext, raw)
    if err:
        logger.info(f"[LIBRARY] watch skipped {full}: {err}")
        return None
    with get_connection() as conn:
        cur = conn.cursor()
        doc_id = _insert_document(
            cur, scope, fmeta.get('title') or title,
            _EXT_KIND.get(ext, 'article'), 'low', collection_id, None,
            fmeta.get('author'), str(full), None, 'queued', 'watch', meta=meta)
        conn.commit()
    ddir = sources_dir() / str(doc_id)
    ddir.mkdir(parents=True, exist_ok=True)
    working = ddir / 'working.md'
    working.write_text(text, encoding='utf-8')
    with get_connection() as conn:
        cur = conn.cursor()
        now = _now()
        cur.execute('UPDATE documents SET working_path = ?, updated = ? '
                    'WHERE id = ?', (str(working), now, doc_id))
        cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                    'VALUES (?, ?, ?, ?)', (doc_id, 'chunk', now, now))
        conn.commit()
    _seed_doc_edges(scope, doc_id, title)
    return doc_id


def _watch_refresh(scope, doc_id, full: Path, mtime, size):
    """File changed on disk: re-derive, KEEP annotations (people/notes/place
    survive an edit — they're hers, not the file's)."""
    with get_connection() as conn:
        row = conn.execute('SELECT kind, title, description, meta FROM '
                           'documents WHERE id = ?', (doc_id,)).fetchone()
    if not row:
        return
    kind, title, desc, meta_raw = row
    try:
        meta = json.loads(meta_raw or '{}')
    except Exception:
        meta = {}
    meta['mtime'], meta['size'] = mtime, size
    if kind == 'image':
        from PIL import Image
        try:
            img = Image.open(full)
            img.load()
            meta.update(_exif_data(img))
        except Exception:
            return
        with get_connection() as conn:
            cur = conn.cursor()
            now = _now()
            cur.execute('UPDATE documents SET meta = ?, status = ?, '
                        'updated = ? WHERE id = ?',
                        (json.dumps(meta, ensure_ascii=False), 'ready', now,
                         doc_id))
            cur.execute('DELETE FROM img_vectors WHERE doc_id = ?', (doc_id,))
            cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                        'VALUES (?, ?, ?, ?)', (doc_id, 'vision', now, now))
            conn.commit()
        _matrix_cache.clear()
        _write_image_working(scope, doc_id, title, desc, meta)
        return
    try:
        raw = full.read_bytes()
    except OSError:
        return
    text, _fm, err = _extract(full.suffix.lower(), raw)
    if err:
        return
    working = sources_dir() / str(doc_id) / 'working.md'
    working.parent.mkdir(parents=True, exist_ok=True)
    working.write_text(text, encoding='utf-8')
    with get_connection() as conn:
        cur = conn.cursor()
        now = _now()
        cur.execute('UPDATE documents SET meta = ?, status = ?, '
                    'working_path = ?, updated = ? WHERE id = ?',
                    (json.dumps(meta, ensure_ascii=False), 'queued',
                     str(working), now, doc_id))
        cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                    'VALUES (?, ?, ?, ?)', (doc_id, 'chunk', now, now))
        conn.commit()


_folder_locks = {}


def scan_folder(fid):
    """One stat-diff pass: new files ingest, changed re-derive, vanished go
    missing, returned revive. stat-only until something actually differs —
    a quiet 5k-file folder costs milliseconds.

    Serialized per folder: the rescan route, add_watch_folder's kick, boot
    scan_all, and the search-time kick can all land at once — two unguarded
    scans of one folder each snapshot `known` and double-ingest every new
    file. The second scan waits, re-reads, and skips them instead."""
    fid = int(fid)
    with _scan_state_lock:
        lock = _folder_locks.setdefault(fid, threading.Lock())
    with lock:
        return _scan_folder_inner(fid)


def _scan_folder_inner(fid):
    import time
    wall = time.time()
    with get_connection() as conn:
        row = conn.execute('SELECT scope, path, collection_id FROM '
                           'watch_folders WHERE id = ?', (int(fid),)).fetchone()
    if not row:
        return None
    scope, path, collection_id = row
    root = Path(path)
    report = {'added': 0, 'changed': 0, 'missing': 0, 'revived': 0, 'total': 0}
    with get_connection() as conn:
        known = {}
        for did, status, meta_raw in conn.execute(
                "SELECT id, status, meta FROM documents "
                "WHERE json_extract(meta, '$.watch') = ?", (int(fid),)).fetchall():
            try:
                m = json.loads(meta_raw or '{}')
            except Exception:
                m = {}
            known[m.get('relpath')] = (did, status, m)
    if not root.is_dir():
        # Whole folder gone (drive unplugged) — everything sleeps, nothing dies.
        with get_connection() as conn:
            for did, status, _m in known.values():
                if status != 'missing':
                    conn.execute("UPDATE documents SET status = 'missing', "
                                 "updated = ? WHERE id = ?", (_now(), did))
                    report['missing'] += 1
            conn.commit()
        return report
    files = _watch_files(root)
    report['total'] = len(files)
    for rel, (mt, sz) in files.items():
        hit = known.get(rel)
        # Mid-copy files (mtime seconds old) wait for the next scan —
        # ingesting a half-written PDF stores half a document. Future
        # mtimes (clock-skewed network shares) count as settled: we can't
        # age them, and skipping forever would be worse than a rare torn read.
        settling = 0 <= (wall - mt) < WATCH_SETTLE_S
        if hit is None:
            if settling:
                continue
            if _watch_ingest(scope, int(fid), root, rel, mt, sz,
                             collection_id) is not None:
                report['added'] += 1
            continue
        did, status, m = hit
        if status == 'missing':
            with get_connection() as conn:
                conn.execute("UPDATE documents SET status = 'ready', "
                             "updated = ? WHERE id = ?", (_now(), did))
                conn.commit()
            report['revived'] += 1
        if not settling and (m.get('mtime'), m.get('size')) != (mt, sz):
            _watch_refresh(scope, did, root / rel, mt, sz)
            report['changed'] += 1
    with get_connection() as conn:
        for rel, (did, status, _m) in known.items():
            if rel not in files and status != 'missing':
                conn.execute("UPDATE documents SET status = 'missing', "
                             "updated = ? WHERE id = ?", (_now(), did))
                report['missing'] += 1
        conn.execute('UPDATE watch_folders SET last_scan = ? WHERE id = ?',
                     (_now(), int(fid)))
        conn.commit()
    if report['added'] or report['changed']:
        ensure_worker()
    if any(report[k] for k in ('added', 'changed', 'missing', 'revived')):
        logger.info(f"[LIBRARY] watch scan folder {fid}: {report}")
    return report


def scan_all():
    with get_connection() as conn:
        fids = [r[0] for r in conn.execute('SELECT id FROM watch_folders'
                                           ).fetchall()]
    for fid in fids:
        try:
            scan_folder(fid)
        except Exception as e:
            logger.warning(f"[LIBRARY] watch scan {fid} failed: {e}")
    return len(fids)


def ensure_scan_async(min_interval=SCAN_THROTTLE_S):
    """Search-time freshness kick: at most one background sweep per
    throttle window. No folders = no thread, ever."""
    global _scan_running, _last_scan_mono
    import time
    with get_connection() as conn:
        if not conn.execute('SELECT 1 FROM watch_folders LIMIT 1').fetchone():
            return False
    with _scan_state_lock:
        if _scan_running:
            return False
        if time.monotonic() - _last_scan_mono < min_interval:
            return False
        _scan_running = True

    def _go():
        global _scan_running, _last_scan_mono
        try:
            scan_all()
        finally:
            with _scan_state_lock:
                _last_scan_mono = __import__('time').monotonic()
                _scan_running = False

    threading.Thread(target=_go, name='watch-scan', daemon=True).start()
    return True


# ─── Annotated export — metadata back into the container, pixels untouched ───
# JPEG: rebuild the EXIF from original + annotations, splice it in as APP1
# (no re-encode — byte-identical pixel stream). PNG: iTXt chunks before IEND.
# Other formats (HEIC/webp/gif): honest JPEG conversion carrying the EXIF.

def _splice_jpeg_app1(raw, exif_payload):
    """Replace/insert the APP1 Exif segment in a JPEG byte stream. Walks the
    segment chain — never touches entropy-coded data."""
    if raw[:2] != b'\xff\xd8':
        return None
    out = [raw[:2]]
    i = 2
    app1 = b'\xff\xe1' + (len(exif_payload) + 2).to_bytes(2, 'big') + exif_payload
    placed = False
    while i + 4 <= len(raw):
        marker, size = raw[i:i + 2], int.from_bytes(raw[i + 2:i + 4], 'big')
        if marker[0] != 0xFF or marker[1] in (0xD8, 0xD9):
            break
        seg = raw[i:i + 2 + size]
        if marker == b'\xff\xe1' and seg[4:10] == b'Exif\x00\x00':
            out.append(app1)          # replace in place
            placed = True
        elif marker == b'\xff\xe0' and not placed:
            out.append(seg)           # keep JFIF first, Exif right after
            out.append(app1)
            placed = True
        else:
            if not placed and marker[1] not in (0xE0,):
                out.append(app1)      # no APP0/APP1 led — insert before this
                placed = True
            out.append(seg)
        i += 2 + size
        if marker == b'\xff\xda':     # start of scan — rest is entropy data
            break
    if not placed:
        out.append(app1)
    out.append(raw[i:])
    return b''.join(out)


def _png_itxt(raw, pairs):
    """Insert iTXt chunks (utf-8 safe) before IEND. pairs = [(keyword, text)].
    Same chunk mechanics as the persona cards."""
    import struct
    import zlib
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    iend = raw.rfind(b'IEND')
    if iend < 8:
        return None
    cut = iend - 4   # back over the length field
    chunks = []
    for kw, text in pairs:
        data = (kw.encode('latin-1', 'replace')[:79] + b'\x00\x00\x00\x00\x00'
                + text.encode('utf-8'))
        chunks.append(struct.pack('>I', len(data)) + b'iTXt' + data
                      + struct.pack('>I', zlib.crc32(b'iTXt' + data) & 0xffffffff))
    return raw[:cut] + b''.join(chunks) + raw[cut:]


def export_annotated(scope, doc_id):
    """The working form travels WITH the image: description + people + notes
    written into the container's metadata. Returns (bytes, filename, mime)
    or (None, None, error)."""
    import io
    from PIL import Image
    with get_connection() as conn:
        row = conn.execute('SELECT scope, title, description, kind, meta, '
                           'source_path FROM documents WHERE id = ?',
                           (int(doc_id),)).fetchone()
    if not row or row[0] != scope or row[3] != 'image':
        return None, None, "Not found."
    if not row[5] or not Path(row[5]).exists():
        return None, None, "Original file is missing."
    title, description = row[1], row[2]
    meta = json.loads(row[4] or '{}')
    raw = Path(row[5]).read_bytes()
    ext = Path(row[5]).suffix.lower()
    safe = ''.join(c if c.isalnum() or c in ' ._-' else '_'
                   for c in title)[:60].strip() or f'doc-{doc_id}'
    people = ', '.join(meta.get('people') or [])
    notes = meta.get('notes') or ''
    if ext == '.png':
        pairs = [('Description', description or title)]
        if people:
            pairs.append(('People', people))
        if notes:
            pairs.append(('Notes', notes))
        out = _png_itxt(raw, pairs)
        if out:
            return out, f'{safe}_annotated.png', 'image/png'
        return None, None, "PNG structure unreadable."
    _heic_ready()
    try:
        img = Image.open(io.BytesIO(raw))
        exif = img.getexif()             # start from the original's EXIF
        exif[270] = description or title             # ImageDescription
        if people:                                   # XPKeywords (UTF-16LE)
            exif[0x9C9E] = people.encode('utf-16-le') + b'\x00\x00'
        if notes:
            exif.get_ifd(0x8769)[0x9286] = notes     # UserComment
        payload = exif.tobytes()
    except Exception as e:
        return None, None, f"EXIF build failed: {e}"
    if ext in ('.jpg', '.jpeg'):
        out = _splice_jpeg_app1(raw, payload)
        if out:
            return out, f'{safe}_annotated.jpg', 'image/jpeg'
        return None, None, "JPEG structure unreadable."
    # HEIC/webp/gif: metadata write-back means re-encoding anyway — convert
    # to a JPEG that carries it, honestly labeled by the filename.
    try:
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=95, exif=payload)
        return (buf.getvalue(), f'{safe}_annotated_converted.jpg',
                'image/jpeg')
    except Exception as e:
        return None, None, f"Conversion failed: {e}"


_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$')


def _sections(text):
    """Markdown-aware: [(heading_or_None, body)] in order. Plain text = one
    section. Heading text becomes the chunk's chapter breadcrumb."""
    out, cur_head, cur = [], None, []
    for line in text.split('\n'):
        m = _HEADING_RE.match(line)
        if m:
            if cur:
                out.append((cur_head, '\n'.join(cur)))
            cur_head, cur = m.group(2).strip(), []
        else:
            cur.append(line)
    if cur:
        out.append((cur_head, '\n'.join(cur)))
    return out or [(None, text)]


def _pack_paragraphs(body, size, overlap):
    """Paragraph-first packing to ≤size with word-boundary fallback, then a
    tail-of-previous overlap prepended so no thought dies at a boundary."""
    paras = [p.strip() for p in body.split('\n\n') if p.strip()]
    pieces, cur = [], ''
    for p in paras:
        candidate = f"{cur}\n\n{p}" if cur else p
        if len(candidate) <= size:
            cur = candidate
            continue
        if cur:
            pieces.append(cur)
            cur = ''
        while len(p) > size:
            cut = p.rfind(' ', size // 2, size)
            cut = cut if cut > 0 else size
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        cur = p
    if cur:
        pieces.append(cur)
    if overlap:
        with_tail = []
        for i, piece in enumerate(pieces):
            if i:
                tail = pieces[i - 1][-overlap:]
                sp = tail.find(' ')
                if 0 <= sp < overlap - 1:
                    tail = tail[sp + 1:]
                piece = f"…{tail}\n\n{piece}"
            with_tail.append(piece)
        pieces = with_tail
    return pieces


def chunk_text(text, kind):
    """→ [(chapter, content)] per the kind's profile (tmp/knowledge-layer.md)."""
    text = (text or '').strip()
    if not text:
        return []
    # Images chunk like notes: the metadata.md is one small whole.
    if kind in ('note', 'image') and len(text) <= NOTE_SPLIT:
        return [(None, text)]
    size, overlap = PROFILES.get(kind if kind != 'note' else 'article',
                                 PROFILES['article'])
    out = []
    for head, body in _sections(text):
        if not body.strip():
            continue
        for piece in _pack_paragraphs(body, size, overlap):
            out.append((head, piece))
    return out[:DOC_CHUNK_GUARD]


# ─── int8 vectors (the in-house engine's storage half; scan lands in P3) ─────

def quantize(vec):
    """float32 → (scale, int8 bytes). Per-vector max-abs symmetric scaling —
    nomic vectors are unit-norm so this is well-conditioned."""
    v = np.asarray(vec, dtype=np.float32)
    scale = float(np.max(np.abs(v))) or 1.0
    q = np.clip(np.round(v / scale * 127.0), -127, 127).astype(np.int8)
    return scale, q.tobytes()


def dequantize(scale, blob):
    return np.frombuffer(blob, dtype=np.int8).astype(np.float32) * (scale / 127.0)


def _embed_and_store(cursor, rows):
    """rows: [(chunk_id, content)]. Returns embedded count; 0 = embedder down
    (vectors stay missing — rebuildable, FTS still serves)."""
    emb = _embedder()
    if not getattr(emb, 'available', False):
        return 0
    vecs = emb.embed([c for _, c in rows], prefix='search_document')
    if vecs is None:
        return 0
    provider = getattr(emb, 'provider_id', 'unknown')
    for (cid, _), v in zip(rows, vecs):
        scale, blob = quantize(v)
        cursor.execute(
            'INSERT OR REPLACE INTO doc_vectors (chunk_id, provider, dim, scale, q) '
            'VALUES (?, ?, ?, ?, ?)', (cid, provider, int(np.asarray(v).shape[0]),
                                       scale, blob))
    _matrix_cache.clear()   # any vector write invalidates the scan cache
    return len(rows)


# ─── Collections ─────────────────────────────────────────────────────────────

def create_collection(scope, name, description=None, parent_id=None):
    """Category (parent_id None) or Topic (parent must be a Category —
    2 levels, hard). Returns (id, error)."""
    name = (name or '').strip()
    if not name:
        return None, "Collection needs a name."
    with get_connection() as conn:
        cur = conn.cursor()
        if parent_id is not None:
            row = cur.execute('SELECT parent_id FROM collections WHERE id = ? '
                              'AND scope = ?', (int(parent_id), scope)).fetchone()
            if not row:
                return None, f"Parent collection [{parent_id}] not found."
            if row[0] is not None:
                return None, "Topics can't contain topics — 2 levels only."
        now = _now()
        cur.execute('INSERT INTO collections (parent_id, name, description, scope, '
                    'created, updated) VALUES (?, ?, ?, ?, ?, ?)',
                    (parent_id, name, (description or '').strip() or None,
                     scope, now, now))
        conn.commit()
        return cur.lastrowid, None


# ─── Import: notes (synchronous) and files (queued) ──────────────────────────

def _insert_document(cur, scope, title, kind, importance_key, collection_id,
                     description, author, source_path, working_path, status,
                     added_by, meta=None):
    now = _now()
    imp = IMPORTANCE.get(importance_key or '', None)
    if imp is None:
        imp = IMPORTANCE['high'] if kind == 'note' else IMPORTANCE['med']
    cur.execute(
        'INSERT INTO documents (collection_id, scope, title, author, description, '
        'kind, importance, source_path, working_path, status, added_by, meta, '
        'created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (collection_id, scope, title, author, description, kind, imp,
         source_path, working_path, status, added_by,
         json.dumps(meta, ensure_ascii=False) if meta else None, now, now))
    return cur.lastrowid


def _write_chunks(cur, doc_id, chunks):
    now = _now()
    ids = []
    for seq, (chapter, content) in enumerate(chunks):
        cur.execute('INSERT INTO doc_chunks (doc_id, seq, chapter, content, created) '
                    'VALUES (?, ?, ?, ?, ?)', (doc_id, seq, chapter, content, now))
        ids.append(cur.lastrowid)
    return ids


def import_note(scope, title, content, description=None, collection_id=None,
                importance=None, added_by='user', private_key=None, meta=None,
                kind='note'):
    """Typed add-modal 'Note': paste, whole-doc bias, importance defaults
    HIGH (the gesture already said it matters). Synchronous — notes are small.
    meta/kind are the migration seam (import_key provenance, article-sized
    reassemblies). Returns (doc_id, error)."""
    title = (title or '').strip()
    content = (content or '').strip()
    if not title or not content:
        return None, "Note needs a title and content."
    chunks = chunk_text(content, kind)
    with get_connection() as conn:
        cur = conn.cursor()
        doc_id = _insert_document(cur, scope, title, kind,
                                  importance or 'high', collection_id,
                                  (description or '').strip() or None, None,
                                  None, None, 'ready', added_by, meta=meta)
        if private_key:
            cur.execute('UPDATE documents SET private_key = ? WHERE id = ?',
                        (private_key.strip(), doc_id))
        ids = _write_chunks(cur, doc_id, chunks)
        _embed_and_store(cur, list(zip(ids, [c for _, c in chunks])))
        conn.commit()
    # Notes are small — the whole text feeds the entity matcher.
    _seed_doc_edges(scope, doc_id,
                    f"{title}\n{(description or '')}\n{content}")
    return doc_id, None


def import_file(scope, filename, raw: bytes, kind=None, title=None,
                description=None, collection_id=None, importance=None,
                added_by='user'):
    """Typed add-modal 'File'/'Bulk': original bytes land in sources/
    (byte-exact redownload forever), extraction produces the working .md,
    the heavy lifting (chunk + embed) goes to the resumable queue.
    Extraction runs FIRST — a refusal (scanned PDF, DRM epub) creates
    nothing. Title/author autofill from file metadata when the modal left
    them blank. Returns (doc_id, error)."""
    filename = Path(filename or 'untitled.txt').name
    ext = Path(filename).suffix.lower()
    if ext not in FILE_KINDS:
        return None, f"'{ext}' isn't importable — txt, md, pdf, epub."
    if not raw:
        return None, "Empty file."
    text, fmeta, err = _extract(ext, raw)
    if err:
        return None, err
    with get_connection() as conn:
        cur = conn.cursor()
        doc_id = _insert_document(
            cur, scope,
            (title or '').strip() or fmeta.get('title') or Path(filename).stem,
            kind or _EXT_KIND.get(ext, 'article'), importance or 'med',
            collection_id, (description or '').strip() or None,
            fmeta.get('author'), None, None, 'queued', added_by)
        conn.commit()
    ddir = sources_dir() / str(doc_id)
    ddir.mkdir(parents=True, exist_ok=True)
    source_path = ddir / filename
    source_path.write_bytes(raw)
    working_path = ddir / 'working.md'
    working_path.write_text(text, encoding='utf-8')
    with get_connection() as conn:
        cur = conn.cursor()
        now = _now()
        cur.execute('UPDATE documents SET source_path = ?, working_path = ?, '
                    'updated = ? WHERE id = ?',
                    (str(source_path), str(working_path), now, doc_id))
        cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                    'VALUES (?, ?, ?, ?)', (doc_id, 'chunk', now, now))
        conn.commit()
    # Files: title + description only (matching a whole book is a scan tax;
    # the doc-level edge is the same either way).
    with get_connection() as conn:
        row = conn.execute('SELECT title, description FROM documents '
                           'WHERE id = ?', (doc_id,)).fetchone()
    if row:
        _seed_doc_edges(scope, doc_id, f"{row[0]}\n{row[1] or ''}")
    ensure_worker()
    return doc_id, None


# ─── The queue (resumable — the piece everything else leans on) ──────────────

def work_once() -> bool:
    """One atomic slice of import work. True = progress was made (call again),
    False = nothing to do / blocked (embedder down). Each slice commits its
    results WITH its cursor — kill -9 between slices loses nothing."""
    with get_connection() as conn:
        cur = conn.cursor()
        job = cur.execute(
            "SELECT id, doc_id, stage, cursor FROM jobs "
            "WHERE state = 'pending' ORDER BY id LIMIT 1").fetchone()
        if not job:
            return False
        if job[2] == 'vision' and not getattr(_vision(), 'available', False):
            # A blocked vision job (model absent/down) must never dam the
            # queue — text imports behind it still flow. Vision jobs stay
            # pending for the next boot resume.
            job = cur.execute(
                "SELECT id, doc_id, stage, cursor FROM jobs "
                "WHERE state = 'pending' AND stage != 'vision' "
                "ORDER BY id LIMIT 1").fetchone()
            if not job:
                return False
        jid, doc_id, stage, cursor_pos = job
        now = _now()
        try:
            if stage == 'chunk':
                doc = cur.execute('SELECT working_path, kind FROM documents '
                                  'WHERE id = ?', (doc_id,)).fetchone()
                if not doc or not doc[0] or not Path(doc[0]).exists():
                    raise FileNotFoundError(f"working copy missing for doc {doc_id}")
                text = Path(doc[0]).read_text(encoding='utf-8')
                chunks = chunk_text(text, doc[1])
                # Re-run safety: a crash after chunk-insert but before the
                # stage flip re-chunks from zero — wipe partials first.
                # Vectors must go WITH the chunks: rowid reuse after the
                # delete would leave new chunks wearing the old file's
                # vectors (embed skips rows that already have one).
                cur.execute('DELETE FROM doc_vectors WHERE chunk_id IN '
                            '(SELECT id FROM doc_chunks WHERE doc_id = ?)',
                            (doc_id,))
                cur.execute('DELETE FROM doc_chunks WHERE doc_id = ?', (doc_id,))
                _write_chunks(cur, doc_id, chunks)
                cur.execute("UPDATE jobs SET stage = 'embed', cursor = 0, "
                            "total = ?, updated = ? WHERE id = ?",
                            (len(chunks), now, jid))
                cur.execute("UPDATE documents SET status = 'importing', "
                            "updated = ? WHERE id = ?", (now, doc_id))
                conn.commit()
                return True
            if stage == 'embed':
                rows = cur.execute(
                    'SELECT c.id, c.content FROM doc_chunks c '
                    'LEFT JOIN doc_vectors v ON v.chunk_id = c.id '
                    'WHERE c.doc_id = ? AND v.chunk_id IS NULL '
                    'ORDER BY c.seq LIMIT ?', (doc_id, EMBED_BATCH)).fetchall()
                if not rows:
                    cur.execute("UPDATE jobs SET state = 'done', updated = ? "
                                "WHERE id = ?", (now, jid))
                    cur.execute("UPDATE documents SET status = 'ready', "
                                "updated = ? WHERE id = ?", (now, doc_id))
                    conn.commit()
                    return True
                n = _embed_and_store(cur, rows)
                if n == 0:
                    # Embedder down: doc serves via FTS now, vectors arrive
                    # when the model is back — honest partial, never a wedge.
                    cur.execute("UPDATE documents SET status = 'ready', "
                                "updated = ? WHERE id = ?", (now, doc_id))
                    conn.commit()
                    return False
                cur.execute('UPDATE jobs SET cursor = cursor + ?, updated = ? '
                            'WHERE id = ?', (n, now, jid))
                conn.commit()
                return True
            if stage == 'vision':
                doc = cur.execute('SELECT source_path, kind FROM documents '
                                  'WHERE id = ?', (doc_id,)).fetchone()
                if (not doc or doc[1] != 'image' or not doc[0]
                        or not Path(doc[0]).exists()):
                    raise FileNotFoundError(f"image missing for doc {doc_id}")
                ve = _vision()
                if not getattr(ve, 'available', False):
                    # Model absent/down: metadata text already serves search.
                    # Job stays pending — boot resume retries when it's back.
                    return False
                vec = ve.embed_paths([Path(doc[0])])[0]
                if vec is None:
                    raise ValueError(f"vision embed failed for doc {doc_id}")
                scale, blob = quantize(vec)
                cur.execute('INSERT OR REPLACE INTO img_vectors (doc_id, '
                            'provider, dim, scale, q) VALUES (?, ?, ?, ?, ?)',
                            (doc_id, getattr(ve, 'provider_id', 'unknown'),
                             int(np.asarray(vec).shape[0]), scale, blob))
                cur.execute("UPDATE jobs SET state = 'done', updated = ? "
                            "WHERE id = ?", (now, jid))
                conn.commit()
                _matrix_cache.clear()
                return True
            raise ValueError(f"unknown stage '{stage}'")
        except Exception as e:
            logger.error(f"[LIBRARY] job {jid} (doc {doc_id}) failed: {e}",
                         exc_info=True)
            cur.execute("UPDATE jobs SET state = 'error', error = ?, updated = ? "
                        "WHERE id = ?", (str(e)[:500], now, jid))
            cur.execute("UPDATE documents SET status = 'error', updated = ? "
                        "WHERE id = ?", (now, doc_id))
            conn.commit()
            return True


def ensure_worker():
    """Spawn the drain thread if it isn't running. Daemon — dies with the app;
    the jobs table IS the durable state, so nothing is lost."""
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True

    def _drain():
        global _worker_running
        try:
            while True:
                try:
                    if not work_once():
                        break
                except Exception:
                    logger.exception("[LIBRARY] worker slice crashed")
                    break
        finally:
            with _worker_lock:
                _worker_running = False

    threading.Thread(target=_drain, name='library-import', daemon=True).start()


def resume_pending():
    """Boot/nightly hook: restart the drain if jobs survived a shutdown."""
    with get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM jobs WHERE state = 'pending'"
                         ).fetchone()[0]
    if n:
        logger.info(f"[LIBRARY] resuming {n} pending import job(s)")
        ensure_worker()
    return n


# ─── Graph (P5b) — documents join the mention graph as leaf nodes ────────────
# Edges live in MIND.DB (src_type='document' → entity, kind='mentions') so
# the spider walks TO documents from people/places — but never expands into
# them (leaf contract: title + description + [doc N] handle, pull the rest).

def _seed_doc_edges(scope, doc_id, text):
    """One mention edge per matched entity per doc. Failure-isolated —
    a matcher hiccup never breaks an import. Returns edges created."""
    try:
        pt = _pt()
        from plugins.mindpalace.tools import metadata as md
        with pt._get_connection() as conn:
            cur = conn.cursor()
            arows = md.entity_aliases(cur, scope)
            if not arows:
                return 0
            amap = md.alias_map(arows)
            hits = md.match_entities(text, [a for _, _, a in arows])
            seen, ids = set(), []
            for h in hits:
                pair = amap.get(h.lower())
                if pair and pair[0] not in seen:
                    seen.add(pair[0])
                    ids.append(pair[0])
            now = _now()
            made = 0
            for eid in ids:
                if cur.execute(
                        "SELECT 1 FROM edges WHERE src_type = 'document' AND "
                        "src_id = ? AND dst_type = 'entity' AND dst_id = ?",
                        (doc_id, eid)).fetchone():
                    continue
                cur.execute(
                    "INSERT INTO edges (src_type, src_id, dst_type, dst_id, "
                    "kind, weight, created) VALUES ('document', ?, 'entity', "
                    "?, 'mentions', 1.0, ?)", (doc_id, eid, now))
                made += 1
            conn.commit()
        return made
    except Exception as e:
        logger.warning(f"[LIBRARY] doc edge seeding skipped: {e}")
        return 0


def backfill_doc_edges():
    """Boot hook: docs with zero edges get a title+description pass — covers
    everything imported before the graph leg existed. Cheap and idempotent
    (linked docs skip)."""
    try:
        pt = _pt()
        with get_connection() as conn:
            docs = conn.execute("SELECT id, scope, title, "
                                "COALESCE(description, '') FROM documents"
                                ).fetchall()
        if not docs:
            return 0
        with pt._get_connection() as conn:
            linked = {r[0] for r in conn.execute(
                "SELECT DISTINCT src_id FROM edges WHERE src_type = 'document'"
            ).fetchall()}
        made = 0
        for did, scope, title, desc in docs:
            if did in linked:
                continue
            made += _seed_doc_edges(scope, did, f"{title}\n{desc}")
        if made:
            logger.info(f"[LIBRARY] backfilled {made} document edge(s)")
        return made
    except Exception as e:
        logger.warning(f"[LIBRARY] edge backfill skipped: {e}")
        return 0


# ─── Migration (P5 — coexistence COPY, never a cutover) ──────────────────────
# Krem's ruling 2026-07-17: v1 (classic knowledge.db) and v2 (mind.db
# knowledge chunks) keep WORKING — both memory systems run side by side for
# months; users may switch back. Migration copies into the library:
#   v1: READ-ONLY source, forever. Tabs → categories, grouped entries
#       (source_filename + chunk_index) reassemble into documents.
#   v2: mind.db knowledge chunks reassemble by (source|label|solo) group;
#       originals gain meta.library_migrated = palace-read dedup flag only
#       (v1 never reads mind.db; nothing is deleted anywhere).
# Idempotent via documents.meta.import_key. Report lives in lib_meta.

def _derive_title(text, cap=60):
    """First real line of content → a usable title: markdown stripped,
    whitespace collapsed, word-boundary capped. content[:60] raw was the
    old way — it kept newlines and ** and made unreadable titles."""
    for line in (text or '').split('\n'):
        line = re.sub(r'[#*_`>]+', ' ', line)
        line = ' '.join(line.split()).strip()
        if line:
            if len(line) > cap:
                cut = line.rfind(' ', cap // 2, cap)
                line = line[:cut if cut > 0 else cap].rstrip() + '…'
            return line
    return 'Untitled'


def _title_broken(title):
    t = title or ''
    return '\n' in t or '**' in t or t.startswith('#')


def tidy_migrated_titles():
    """One-shot repair (marker-gated): migrated docs whose titles kept raw
    content[:60] artifacts (newlines, markdown) get re-derived from their
    first section. Marker prevents refighting later hand-renames."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            if _lib_meta(cur, 'title_tidy') == 'v1':
                return 0
            rows = cur.execute(
                "SELECT id, title FROM documents "
                "WHERE json_extract(meta, '$.migrated_from') IS NOT NULL"
            ).fetchall()
            fixed = 0
            for did, title in rows:
                if not _title_broken(title):
                    continue
                first = cur.execute('SELECT content FROM doc_chunks WHERE '
                                    'doc_id = ? ORDER BY seq LIMIT 1',
                                    (did,)).fetchone()
                new = _derive_title(first[0] if first else title)
                cur.execute('UPDATE documents SET title = ?, updated = ? '
                            'WHERE id = ?', (new, _now(), did))
                fixed += 1
            _lib_meta(cur, 'title_tidy', 'v1')
            conn.commit()
        if fixed:
            logger.info(f"[LIBRARY] tidied {fixed} migrated title(s)")
        return fixed
    except Exception as e:
        logger.warning(f"[LIBRARY] title tidy skipped: {e}")
        return 0


def merge_migrated_duplicates():
    """v1 and v2 could both hold the same note — different import keys, so
    idempotency correctly let both in. Exact-content duplicates among
    MIGRATED docs only: keep the richer copy (shelved > described > oldest),
    drop the rest. Hand-made and watched docs are never touched. Idempotent
    — clean store = no-op."""
    import hashlib
    try:
        groups = {}
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, scope, collection_id, description FROM documents "
                "WHERE json_extract(meta, '$.migrated_from') IS NOT NULL "
                "AND status = 'ready'").fetchall()
            for did, scope, cid, desc in rows:
                text = '\n'.join(r[0] for r in conn.execute(
                    'SELECT content FROM doc_chunks WHERE doc_id = ? '
                    'ORDER BY seq', (did,)).fetchall())
                if not text.strip():
                    continue
                h = hashlib.sha256(f'{scope}\x00{text}'.encode()).hexdigest()
                groups.setdefault(h, []).append((did, scope, cid, desc))
        merged = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda m: (m[2] is None, m[3] is None, m[0]))
            keeper, _s, keeper_cid, _kd = members[0]
            for did, scope, _c, _d in members[1:]:
                # Same text deliberately filed in TWO drawers is a filing
                # choice, not a duplicate — only fold copies that share a
                # drawer or never had one.
                if _c is not None and keeper_cid is not None and _c != keeper_cid:
                    continue
                ok, _err = delete_document(scope, did)
                if ok:
                    merged += 1
                    logger.info(f"[LIBRARY] merged duplicate migrated doc "
                                f"{did} → kept {keeper}")
        return merged
    except Exception as e:
        logger.warning(f"[LIBRARY] duplicate merge skipped: {e}")
        return 0


def _lib_meta(cur, key, value=None):
    if value is None:
        row = cur.execute('SELECT value FROM lib_meta WHERE key = ?',
                          (key,)).fetchone()
        return row[0] if row else None
    cur.execute('INSERT OR REPLACE INTO lib_meta (key, value) VALUES (?, ?)',
                (key, value))
    return value


def _existing_import_keys():
    with get_connection() as conn:
        return {r[0] for r in conn.execute(
            "SELECT json_extract(meta, '$.import_key') FROM documents "
            "WHERE meta IS NOT NULL").fetchall() if r[0]}


def _enqueue_text_doc(scope, title, text, kind, importance, collection_id,
                      added_by, meta, description=None):
    """Migration's write path: working.md + a queue job — big stores embed in
    the background, resumable, never wedging a route or boot."""
    with get_connection() as conn:
        cur = conn.cursor()
        doc_id = _insert_document(cur, scope, title, kind, importance,
                                  collection_id, description, None, None,
                                  None, 'queued', added_by, meta=meta)
        conn.commit()
    ddir = sources_dir() / str(doc_id)
    ddir.mkdir(parents=True, exist_ok=True)
    working = ddir / 'working.md'
    working.write_text(text, encoding='utf-8')
    with get_connection() as conn:
        cur = conn.cursor()
        now = _now()
        cur.execute('UPDATE documents SET working_path = ?, updated = ? '
                    'WHERE id = ?', (str(working), now, doc_id))
        cur.execute('INSERT INTO jobs (doc_id, stage, created, updated) '
                    'VALUES (?, ?, ?, ?)', (doc_id, 'chunk', now, now))
        conn.commit()
    _seed_doc_edges(scope, doc_id, f"{title}\n{description or ''}")
    return doc_id


def _migrate_v2_chunks(keys, report):
    """mind.db layer='knowledge' → library docs, grouped like the old UI did
    (source, else label, else solo). Originals flagged, not touched further."""
    pt = _pt()
    with pt._get_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT id, scope, content, label, source, chunk_index, meta "
            "FROM chunks WHERE layer = 'knowledge' "
            "AND json_extract(meta, '$.library_migrated') IS NULL "
            "ORDER BY scope, COALESCE(chunk_index, 0), created").fetchall()
    groups = {}
    for cid, scope, content, label, source, cidx, meta_raw in rows:
        gkey = (scope, source or label or f'solo:{cid}')
        groups.setdefault(gkey, []).append(
            (cid, content, label, source, meta_raw))
    for (scope, gname), members in groups.items():
        import_key = f'libmig:v2:{scope}:{gname}'
        if import_key in keys:
            report['v2']['skipped'] += 1
            continue
        try:
            first_meta = {}
            try:
                first_meta = json.loads(members[0][4] or '{}')
            except Exception:
                pass
            label = members[0][2]
            source = members[0][3]
            title = (label or (Path(source).stem if source else '')
                     or _derive_title(members[0][1])).strip()
            text = '\n\n'.join(m[1] for m in members)
            doc_id = _enqueue_text_doc(
                scope, title, text, 'article' if len(members) > 1 else 'note',
                'med', None, first_meta.get('added_by') or 'user',
                {'import_key': import_key, 'migrated_from': 'v2-chunks'})
            keys.add(import_key)
            report['v2']['imported'] += 1
            with pt._get_connection() as conn:
                cur = conn.cursor()
                for m in members:
                    cur.execute(
                        "UPDATE chunks SET meta = json_set(COALESCE(meta, '{}'), "
                        "'$.library_migrated', ?) WHERE id = ?",
                        (doc_id, m[0]))
                conn.commit()
        except Exception as e:
            logger.error(f"[LIBRARY] v2 group '{gname}' migration failed: {e}")
            report['v2']['failed'] += 1


def _migrate_v1_knowledge(keys, report):
    """Classic knowledge.db (READ-ONLY — _connect_ro enforces it in SQLite
    itself). Tabs → categories; entries grouped per (tab, source_filename)
    reassemble; loose entries are one doc each."""
    from plugins.mindpalace.tools import import_tools as it
    import config
    path = it._source_path(config, 'knowledge.db')
    if not path.exists():
        report['v1']['absent'] = True
        return
    import sqlite3 as sq
    with it._connect_ro(path) as src:
        src.row_factory = sq.Row
        scur = src.cursor()
        if not it._columns(scur, 'knowledge_entries'):
            report['v1']['absent'] = True
            return
        tabs = {}
        if it._columns(scur, 'knowledge_tabs'):
            for r in scur.execute('SELECT * FROM knowledge_tabs').fetchall():
                t = {k: r[k] for k in r.keys()}
                tabs[t.get('id')] = t
        entries = [dict((k, r[k]) for k in r.keys())
                   for r in scur.execute('SELECT * FROM knowledge_entries'
                                         ).fetchall()]
    coll_cache = {}

    def _drawer(scope, tab):
        name = (tab.get('name') or 'Imported (v1)').strip()
        ck = (scope, name)
        if ck not in coll_cache:
            with get_connection() as conn:
                row = conn.execute(
                    'SELECT id FROM collections WHERE scope = ? AND name = ? '
                    'AND parent_id IS NULL', (scope, name)).fetchone()
            coll_cache[ck] = row[0] if row else create_collection(
                scope, name, description='migrated from the v1 knowledge base')[0]
        return coll_cache[ck]

    groups = {}
    for e in entries:
        content = (it._get(e, 'content') or '').strip()
        if not content:
            continue
        tab = tabs.get(e.get('tab_id'), {})
        scope = it._get(e, 'scope') or tab.get('scope') or 'default'
        src_file = it._get(e, 'source_filename')
        gkey = (scope, e.get('tab_id'), src_file or f"entry:{e.get('id')}")
        groups.setdefault(gkey, {'tab': tab, 'members': []})
        groups[gkey]['members'].append(e)
    for (scope, tab_id, gname), g in groups.items():
        import_key = f'libmig:v1:{scope}:{tab_id}:{gname}'
        if import_key in keys:
            report['v1']['skipped'] += 1
            continue
        try:
            members = sorted(g['members'],
                             key=lambda e: (it._get(e, 'chunk_index') or 0))
            tab = g['tab']
            src_file = it._get(members[0], 'source_filename')
            title = ((Path(src_file).stem if src_file else '')
                     or _derive_title(it._get(members[0], 'content'))).strip()
            text = '\n\n'.join((it._get(m, 'content') or '').strip()
                               for m in members)
            added_by = 'user' if (tab.get('type') or 'human') == 'human' else 'ai'
            _enqueue_text_doc(
                scope, title, text,
                'article' if len(members) > 1 else 'note', 'med',
                _drawer(scope, tab), added_by,
                {'import_key': import_key, 'migrated_from': 'v1-knowledge',
                 'v1_tab': tab.get('name')})
            keys.add(import_key)
            report['v1']['imported'] += 1
        except Exception as e:
            logger.error(f"[LIBRARY] v1 group '{gname}' migration failed: {e}")
            report['v1']['failed'] += 1


_migrate_lock = threading.Lock()


def migrate_all(force=False):
    """Both sources, idempotent, marker-gated (first palace boot runs it once;
    Admin's re-migrate passes force=True). Returns the report dict.

    Serialized: the boot timer and the Admin re-migrate button can overlap,
    and each snapshots the import-key set before the other commits — both
    would import the same groups. The second caller waits, re-reads keys,
    and skips everything the first landed."""
    with _migrate_lock:
        return _migrate_all_inner(force)


def _migrate_all_inner(force):
    with get_connection() as conn:
        already = _lib_meta(conn.cursor(), 'migration_ran')
    if already and not force:
        return migration_report()
    report = {'ran_at': _now(),
              'v2': {'imported': 0, 'skipped': 0, 'failed': 0},
              'v1': {'imported': 0, 'skipped': 0, 'failed': 0,
                     'absent': False}}
    keys = _existing_import_keys()
    try:
        _migrate_v2_chunks(keys, report)
    except Exception as e:
        logger.error(f"[LIBRARY] v2 migration crashed: {e}", exc_info=True)
        report['v2']['failed'] += 1
    try:
        _migrate_v1_knowledge(keys, report)
    except Exception as e:
        logger.error(f"[LIBRARY] v1 migration crashed: {e}", exc_info=True)
        report['v1']['failed'] += 1
    with get_connection() as conn:
        cur = conn.cursor()
        _lib_meta(cur, 'migration_ran', report['ran_at'])
        _lib_meta(cur, 'migration_report', json.dumps(report))
        conn.commit()
    ensure_worker()   # embed the arrivals in the background
    logger.info(f"[LIBRARY] migration: v2 {report['v2']} · v1 {report['v1']}")
    return report


def migration_report():
    with get_connection() as conn:
        raw = _lib_meta(conn.cursor(), 'migration_report')
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ─── Management (P4 — the UI's windows) ──────────────────────────────────────

def catalog_data(scope):
    """Structured catalog for the Library tab: categories → topics → docs,
    plus unfiled docs and live job states (import progress chips)."""
    with get_connection() as conn:
        cur = conn.cursor()

        def _docs(where, params):
            out = []
            for r in cur.execute(
                    f'SELECT d.id, d.title, d.author, d.kind, d.description, '
                    f'd.importance, d.status, d.created, d.updated, '
                    f'(SELECT COUNT(*) FROM doc_chunks c WHERE c.doc_id = d.id), '
                    f'd.source_path, d.meta FROM documents d WHERE {where} '
                    f'ORDER BY d.updated DESC', params).fetchall():
                d = {'id': r[0], 'title': r[1], 'author': r[2], 'kind': r[3],
                     'description': r[4], 'importance': _imp_word(r[5]),
                     'status': r[6], 'created': r[7], 'updated': r[8],
                     'sections': r[9], 'has_source': bool(r[10])}
                if r[3] == 'image':   # lightbox facts ride the catalog
                    try:
                        m = json.loads(r[11] or '{}')
                    except Exception:
                        m = {}
                    d['image'] = {k: m.get(k) for k in
                                  ('taken', 'camera', 'place', 'lat', 'lon',
                                   'width', 'height', 'notes')}
                    d['image']['people'] = m.get('people') or []
                out.append(d)
            return out

        cats = []
        for cid, name, desc in cur.execute(
                'SELECT id, name, description FROM collections WHERE scope = ? '
                'AND parent_id IS NULL ORDER BY name', (scope,)).fetchall():
            topics = [{'id': tid, 'name': tname, 'description': tdesc,
                       'documents': _docs('d.collection_id = ?', (tid,))}
                      for tid, tname, tdesc in cur.execute(
                          'SELECT id, name, description FROM collections '
                          'WHERE parent_id = ? ORDER BY name', (cid,)).fetchall()]
            cats.append({'id': cid, 'name': name, 'description': desc,
                         'topics': topics,
                         'documents': _docs('d.collection_id = ?', (cid,))})
        unfiled = _docs('d.scope = ? AND d.collection_id IS NULL', (scope,))
        jobs = [{'doc_id': r[0], 'stage': r[1], 'cursor': r[2], 'total': r[3],
                 'state': r[4], 'error': r[5]}
                for r in cur.execute(
                    'SELECT j.doc_id, j.stage, j.cursor, j.total, j.state, '
                    'j.error FROM jobs j JOIN documents d ON d.id = j.doc_id '
                    "WHERE d.scope = ? AND j.state != 'done'", (scope,)).fetchall()]
        total = cur.execute('SELECT COUNT(*) FROM documents WHERE scope = ?',
                            (scope,)).fetchone()[0]
    return {'categories': cats, 'unfiled': unfiled, 'jobs': jobs,
            'total': total}


def document_text(scope, doc_id):
    """The reader's body: working md when the doc has one, else joined
    chunks. Returns (title, text) or (None, None)."""
    with get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT title, working_path, scope FROM documents '
                          'WHERE id = ?', (int(doc_id),)).fetchone()
        if not row or row[2] != scope:
            return None, None
        title, wp = row[0], row[1]
        if wp and Path(wp).exists():
            return title, Path(wp).read_text(encoding='utf-8')
        return title, _whole_note(cur, int(doc_id))


def update_document(scope, doc_id, **fields):
    """UI edit: title, description, author, kind, importance (word),
    collection_id. Returns (ok, error)."""
    allowed = {}
    if 'title' in fields and (fields['title'] or '').strip():
        allowed['title'] = fields['title'].strip()
    for k in ('description', 'author'):
        if k in fields:
            allowed[k] = (fields[k] or '').strip() or None
    if 'kind' in fields and fields['kind'] in ('note', 'article', 'book',
                                               'reference'):
        allowed['kind'] = fields['kind']
    if 'importance' in fields and fields['importance'] in IMPORTANCE:
        allowed['importance'] = IMPORTANCE[fields['importance']]
    if 'collection_id' in fields:
        allowed['collection_id'] = fields['collection_id'] or None
    if not allowed:
        return False, "Nothing to update."
    with get_connection() as conn:
        cur = conn.cursor()
        owner = cur.execute('SELECT scope, kind FROM documents WHERE id = ?',
                            (int(doc_id),)).fetchone()
        if not owner or owner[0] != scope:
            return False, f"No document [{doc_id}] in this scope."
        # An image is an image — kind is intrinsic, not a dropdown choice.
        if owner[1] == 'image':
            allowed.pop('kind', None)
            if not allowed:
                return False, "Nothing to update."
        sets = ', '.join(f'{k} = ?' for k in allowed)
        cur.execute(f'UPDATE documents SET {sets}, updated = ? WHERE id = ?',
                    (*allowed.values(), _now(), int(doc_id)))
        conn.commit()
    if owner[1] == 'image' and ('title' in allowed or 'description' in allowed):
        # The working form quotes title/description — keep it in step.
        with get_connection() as conn:
            row = conn.execute('SELECT title, description, meta FROM documents '
                               'WHERE id = ?', (int(doc_id),)).fetchone()
        try:
            _write_image_working(scope, int(doc_id), row[0], row[1],
                                 json.loads(row[2] or '{}'))
        except Exception as e:
            logger.warning(f"[LIBRARY] image working refresh failed: {e}")
    return True, None


def delete_document(scope, doc_id):
    """Doc + chunks (FTS via trigger) + vectors + jobs + source files.
    Returns (ok, error)."""
    import shutil
    with get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT scope, title FROM documents WHERE id = ?',
                          (int(doc_id),)).fetchone()
        if not row or row[0] != scope:
            return False, f"No document [{doc_id}] in this scope."
        cur.execute('DELETE FROM doc_vectors WHERE chunk_id IN '
                    '(SELECT id FROM doc_chunks WHERE doc_id = ?)', (int(doc_id),))
        cur.execute('DELETE FROM doc_chunks WHERE doc_id = ?', (int(doc_id),))
        cur.execute('DELETE FROM img_vectors WHERE doc_id = ?', (int(doc_id),))
        cur.execute('DELETE FROM jobs WHERE doc_id = ?', (int(doc_id),))
        cur.execute('DELETE FROM documents WHERE id = ?', (int(doc_id),))
        conn.commit()
    _matrix_cache.clear()
    try:   # graph edges live in mind.db — no ghosts left behind
        pt = _pt()
        with pt._get_connection() as conn:
            conn.execute("DELETE FROM edges WHERE src_type = 'document' "
                         "AND src_id = ?", (int(doc_id),))
            conn.commit()
    except Exception as e:
        logger.warning(f"[LIBRARY] doc edge cleanup skipped: {e}")
    ddir = sources_dir() / str(int(doc_id))
    if ddir.exists():
        shutil.rmtree(ddir, ignore_errors=True)
    return True, None


# ─── Scope export / import — the courier zip (2026-07-19) ────────────────────
# One zip per scope: manifest.json + each owned doc's source file. Derived
# data (chunks, vectors, FTS, thumbs, renders, working forms) is EXCLUDED —
# import rebuilds all of it through the normal pipeline, which is the whole
# payoff of source-files-canonical. Watch-folder docs ship as folder
# DEFINITIONS + per-relpath annotations, never their external files.

EXPORT_FORMAT = 'sapphire-library-export'
EXPORT_VERSION = 1


def _doc_export_key(scope, payload: bytes, title='', collection=None) -> str:
    """Idempotency key minted per TARGET scope + content hash. Deliberately
    NOT the doc's original import_key: migrated docs carry libmig:* keys
    that already exist store-wide — reusing them would make every
    cross-scope import skip as 'already imported'.
    Title + collection salt the hash (scout find, 2026-07-19): body-only
    hashing collapsed distinct docs with identical text ("nothing to
    report" dailies, the same photo in two collections) into one silent
    skip. NOTE: zips imported before this change re-import under new keys."""
    import hashlib
    if isinstance(collection, (list, tuple)):
        coll = '/'.join(str(c) for c in collection)
    else:
        coll = str(collection or '')
    salt = f"\x00{title or ''}\x00{coll}".encode('utf-8')
    return f"libx:{scope}:{hashlib.sha256(payload + salt).hexdigest()[:24]}"


def export_scope_zip(scope, out_path):
    """Write the scope's library to a zip at out_path. Returns (report, error)."""
    import zipfile
    report = {'documents': 0, 'notes': 0, 'watch_folders': 0, 'skipped': 0}
    with get_connection() as conn:
        cur = conn.cursor()
        cols = cur.execute(
            'SELECT id, name, description, parent_id FROM collections '
            'WHERE scope = ?', (scope,)).fetchall()
        col_by_id = {c[0]: c for c in cols}
        docs = cur.execute(
            'SELECT id, collection_id, title, author, description, kind, '
            'importance, status, source_path, private_key, meta, created, '
            'updated, added_by FROM documents WHERE scope = ?',
            (scope,)).fetchall()
        folders = cur.execute(
            'SELECT id, path, collection_id FROM watch_folders WHERE scope = ?',
            (scope,)).fetchall()

    def col_path(cid):
        """Collection as a [category, topic?] name path — ids never travel."""
        path = []
        while cid is not None and cid in col_by_id:
            c = col_by_id[cid]
            path.insert(0, c[1])
            cid = c[3]
        return path

    man_docs, man_folders = [], []
    watch_fids = {f[0] for f in folders}
    try:
        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for (did, cid, title, author, desc, kind, imp, status, src,
                 pkey, meta_raw, created, updated, added_by) in docs:
                try:
                    meta = json.loads(meta_raw or '{}')
                except Exception:
                    meta = {}
                rec = {'title': title, 'kind': kind, 'description': desc,
                       'importance': imp, 'author': author,
                       'private_key': pkey, 'created': created,
                       'added_by': added_by, 'collection': col_path(cid)}
                if meta.get('watch') in watch_fids:
                    # Watch docs ride their folder's annotation map instead.
                    continue
                ann = {k: meta[k] for k in ('people', 'notes') if meta.get(k)}
                if ann:
                    rec['annotations'] = ann
                if kind == 'note' or not src or not Path(src).exists():
                    ttl, text = document_text(scope, did)
                    if text is None:
                        report['skipped'] += 1
                        continue
                    rec['content'] = text
                    report['notes'] += 1
                else:
                    fname = Path(src).name
                    arc = f'sources/{did}/{fname}'
                    zf.write(src, arc)
                    rec['file'] = arc
                    rec['filename'] = fname
                    report['documents'] += 1
                man_docs.append(rec)
            for fid, fpath, fcid in folders:
                ann_map = {}
                for (did, cid, title, author, desc, kind, imp, status, src,
                     pkey, meta_raw, created, updated, added_by) in docs:
                    try:
                        m = json.loads(meta_raw or '{}')
                    except Exception:
                        m = {}
                    if m.get('watch') == fid and m.get('relpath'):
                        a = {k: m[k] for k in ('people', 'notes') if m.get(k)}
                        if desc:
                            a['description'] = desc
                        if a:
                            ann_map[m['relpath']] = a
                man_folders.append({'path': fpath,
                                    'collection': col_path(fcid),
                                    'annotations': ann_map})
                report['watch_folders'] += 1
            manifest = {
                'format': EXPORT_FORMAT, 'version': EXPORT_VERSION,
                'scope': scope, 'exported': _now(),
                'collections': [{'name': c[1], 'description': c[2],
                                 'parent': (col_by_id[c[3]][1]
                                            if c[3] in col_by_id else None)}
                                for c in cols],
                'documents': man_docs, 'watch_folders': man_folders,
            }
            zf.writestr('manifest.json',
                        json.dumps(manifest, ensure_ascii=True, indent=1))
    except Exception as e:
        logger.error(f"[LIBRARY] export failed: {e}", exc_info=True)
        return None, f"Export failed: {e}"
    return report, None


def _ensure_collection_path(scope, path):
    """[category, topic?] names → collection id in scope (find or create)."""
    cid = None
    for name in (path or [])[:2]:
        with get_connection() as conn:
            row = conn.execute(
                'SELECT id FROM collections WHERE scope = ? AND name = ? '
                'AND parent_id IS ?', (scope, name, cid)).fetchone()
        if row:
            cid = row[0]
            continue
        new_id, err = create_collection(scope, name, parent_id=cid)
        if err:
            return cid
        cid = new_id
    return cid


def import_scope_zip(scope, zip_path):
    """Import a courier zip into `scope`. Rides import_note/import_file/
    import_image — chunks, embeddings, FTS, thumbs, vision all rebuild
    through the proven pipeline. Deep-copies every file: imported docs own
    their directories, fully independent of the source scope (deleting or
    clearing the source afterward touches nothing here). Idempotent via
    per-scope content keys. Returns (report, error)."""
    import zipfile
    report = {'imported': 0, 'skipped': 0, 'failed': 0,
              'watch_attached': 0, 'watch_missing': [], 'errors': []}
    try:
        zf = zipfile.ZipFile(zip_path)
        manifest = json.loads(zf.read('manifest.json'))
    except Exception as e:
        return None, f"Not a library export zip: {e}"
    if manifest.get('format') != EXPORT_FORMAT:
        return None, "Not a library export file."
    if manifest.get('version') != EXPORT_VERSION:
        return None, f"Unsupported export version {manifest.get('version')}."

    existing = _existing_import_keys()
    for c in manifest.get('collections') or []:
        parent = [c['parent']] if c.get('parent') else []
        _ensure_collection_path(scope, parent + [c['name']])

    for rec in manifest.get('documents') or []:
        try:
            payload = (rec.get('content') or '').encode('utf-8') \
                if rec.get('content') is not None and not rec.get('file') \
                else zf.read(rec['file'])
            key = _doc_export_key(scope, payload, rec.get('title') or '',
                                  rec.get('collection'))
            if key in existing:
                report['skipped'] += 1
                continue
            cid = _ensure_collection_path(scope, rec.get('collection'))
            imp_word = ('high' if (rec.get('importance') or 0) >= 0.75 else
                        ('low' if (rec.get('importance') or 0.6) < 0.45
                         else 'med'))
            kind = rec.get('kind') or 'note'
            if rec.get('file'):
                fname = rec.get('filename') or Path(rec['file']).name
                if Path(fname).suffix.lower() in IMAGE_KINDS:
                    doc_id, err = import_image(
                        scope, fname, payload, title=rec.get('title'),
                        description=rec.get('description'),
                        collection_id=cid, importance=imp_word,
                        added_by=rec.get('added_by') or 'import')
                    if doc_id:
                        ann = rec.get('annotations') or {}
                        if ann.get('people') or ann.get('notes'):
                            update_image_meta(scope, doc_id,
                                              people=ann.get('people'),
                                              notes=ann.get('notes'))
                else:
                    doc_id, err = import_file(
                        scope, fname, payload, kind=kind,
                        title=rec.get('title'),
                        description=rec.get('description'),
                        collection_id=cid, importance=imp_word,
                        added_by=rec.get('added_by') or 'import')
            else:
                doc_id, err = import_note(
                    scope, rec.get('title'), rec.get('content'),
                    description=rec.get('description'), collection_id=cid,
                    importance=imp_word, private_key=rec.get('private_key'),
                    added_by=rec.get('added_by') or 'import', kind=kind)
            if err:
                report['failed'] += 1
                report['errors'].append(f"{rec.get('title')}: {err}")
                continue
            # Restore what the entry points normalize: exact importance,
            # original created stamp, privacy — and stamp the idempotency key.
            with get_connection() as conn:
                cur = conn.cursor()
                row = cur.execute('SELECT meta FROM documents WHERE id = ?',
                                  (doc_id,)).fetchone()
                try:
                    m = json.loads(row[0]) if row and row[0] else {}
                except Exception:
                    m = {}
                m['import_key'] = key
                cur.execute(
                    'UPDATE documents SET meta = ?, importance = ?, '
                    'created = COALESCE(?, created), private_key = '
                    'COALESCE(?, private_key) WHERE id = ?',
                    (json.dumps(m, ensure_ascii=False),
                     rec.get('importance') or IMPORTANCE[imp_word],
                     rec.get('created'), rec.get('private_key'), doc_id))
                conn.commit()
            existing.add(key)
            report['imported'] += 1
        except Exception as e:
            report['failed'] += 1
            report['errors'].append(f"{rec.get('title')}: {e}")

    for wf in manifest.get('watch_folders') or []:
        fpath = wf.get('path')
        # SAFE-BY-DEFAULT (scout find, 2026-07-19): a courier zip is
        # untrusted input — auto-adding + scanning an absolute path from
        # its manifest was an arbitrary-local-dir ingestion primitive
        # (~/, /etc). Watch folders now import as DEFINITIONS ONLY — the
        # report lists them for the human to re-add via the UI. Annotations
        # below still reunite with watch docs that already exist in the
        # scope (folder re-added before importing).
        report.setdefault('watch_skipped', []).append(fpath or '?')
        # Reunite annotations with files by relpath.
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, meta FROM documents WHERE scope = ? AND "
                "json_extract(meta, '$.watch') IS NOT NULL", (scope,)).fetchall()
        by_rel = {}
        for did, meta_raw in rows:
            try:
                m = json.loads(meta_raw or '{}')
            except Exception:
                continue
            if m.get('relpath'):
                by_rel[m['relpath']] = did
        for rel, ann in (wf.get('annotations') or {}).items():
            did = by_rel.get(rel)
            if not did:
                continue
            if ann.get('description'):
                update_document(scope, did, description=ann['description'])
            if ann.get('people') or ann.get('notes'):
                update_image_meta(scope, did, people=ann.get('people'),
                                  notes=ann.get('notes'))
            # Counts DOCS that actually received annotations (was a per-
            # folder counter — it claimed "attached" for skipped folders).
            report['watch_attached'] += 1

    ensure_worker()
    logger.info(f"[LIBRARY] zip import into '{scope}': {report}")
    return report, None


def delete_scope_library(scope):
    """Scope teardown — called by palace delete_scope so the library side
    goes WITH the mind side (docs, chunks, vectors, jobs, source files on
    disk, plus the scope's collections and watch folders). The per-doc path
    is delete_document: one proven cascade, reused."""
    with get_connection() as conn:
        doc_ids = [r[0] for r in conn.execute(
            'SELECT id FROM documents WHERE scope = ?', (scope,)).fetchall()]
    deleted = 0
    for did in doc_ids:
        ok, err = delete_document(scope, did)
        if ok:
            deleted += 1
        else:
            logger.warning(f"[LIBRARY] scope teardown: doc {did}: {err}")
    with get_connection() as conn:
        conn.execute('DELETE FROM collections WHERE scope = ?', (scope,))
        conn.execute('DELETE FROM watch_folders WHERE scope = ?', (scope,))
        conn.commit()
    _matrix_cache.clear()
    if doc_ids:
        logger.info(f"[LIBRARY] deleted scope '{scope}' library: "
                    f"{deleted}/{len(doc_ids)} docs")
    return deleted


def update_collection(scope, cid, name=None, description=None):
    with get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT scope FROM collections WHERE id = ?',
                          (int(cid),)).fetchone()
        if not row or row[0] != scope:
            return False, "Collection not found."
        if name is not None and name.strip():
            cur.execute('UPDATE collections SET name = ?, updated = ? '
                        'WHERE id = ?', (name.strip(), _now(), int(cid)))
        if description is not None:
            cur.execute('UPDATE collections SET description = ?, updated = ? '
                        'WHERE id = ?', ((description or '').strip() or None,
                                         _now(), int(cid)))
        conn.commit()
    return True, None


def delete_collection(scope, cid):
    """Refuses a category that still holds topics; docs go unfiled — never
    deleted by a drawer removal."""
    with get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT scope FROM collections WHERE id = ?',
                          (int(cid),)).fetchone()
        if not row or row[0] != scope:
            return False, "Collection not found."
        topics = cur.execute('SELECT COUNT(*) FROM collections WHERE '
                             'parent_id = ?', (int(cid),)).fetchone()[0]
        if topics:
            return False, "This category still holds topics — remove them first."
        cur.execute('UPDATE documents SET collection_id = NULL WHERE '
                    'collection_id = ?', (int(cid),))
        cur.execute('DELETE FROM collections WHERE id = ?', (int(cid),))
        conn.commit()
    return True, None


# ═══ Retrieval (P3) — the in-house engine ════════════════════════════════════
# int8 exact scan (numpy matmul = BLAS SIMD) + FTS5, fused by reciprocal rank.
# Own candidate path — no recency window, old books never fall out. The
# per-scope matrix is a cache keyed on (count, max_id): any write rebuilds.

RRF_K = 60
VEC_MIN = 0.30                              # cosine floor, explicit search
# Mixed rides need to EARN the seat (Kermit sip, 2026-07-18): 0.30 let
# "Kermit the Frog" pull every frog-adjacent Alice passage into a search
# that never aimed at the library.
VEC_MIN_MIXED = 0.40
LIB_MIXED_CHAR_CAP = 1800                   # hard budget for the 🏛 append
LIB_CHAR_CAP = 6000                         # hard budget, explicit search
SNIPPET_LEN = 240                           # mixed teaser excerpt
NOTE_MIXED_MAX = 600                        # notes ride whole only under this
NOTE_FULL_MAX = 1500                        # explicit-search whole-note ceiling
# Cross-modal (text query → pixels) cosines live on a MUCH smaller scale
# than text-text — nomic vision measured 0.02-0.08 on this box. Floor at
# 0.05; ranking does the heavy lifting. TUNING DIAL for real photos.
VIS_MIN = 0.05


def _vis_min():
    """Vision match floor — Settings → Embedding (VISION_MATCH_THRESHOLD).
    Cross-modal cosines run lower than text-text in the shared space, so
    the shipped default stays permissive; raise it when photo hits flood a
    search (Krem, 2026-07-27). Falls back to VIS_MIN."""
    try:
        import config
        return float(getattr(config, 'VISION_MATCH_THRESHOLD', VIS_MIN))
    except Exception:
        return VIS_MIN
PHOTO_CAP = 5                                # photos shown, explicit search
PHOTO_CAP_MIXED = 3
DOC_CAPS = {'high': 5, 'med': 4, 'low': 3}   # explicit layer=knowledge search
MIXED_CAPS = {'high': 3, 'med': 2, 'low': 1}  # riding along in a mixed search
STITCH_EDGE = 300
PAGE_CHUNKS = 5
RANGE_MAX = 12

_matrix_cache = {}


def _imp_word(imp):
    return 'high' if imp >= 0.75 else ('low' if imp < 0.45 else 'med')


def _scope_matrix(scope, provider, dim):
    """(chunk_ids, scales, int8 matrix) for one scope — RAM proportional to
    what's actually in there (the median user pays ~nothing)."""
    key = (scope, provider, dim)
    with get_connection() as conn:
        cur = conn.cursor()
        base = ('FROM doc_vectors v JOIN doc_chunks c ON c.id = v.chunk_id '
                'JOIN documents d ON d.id = c.doc_id '
                'WHERE d.scope = ? AND v.provider = ? AND v.dim = ?')
        stamp = cur.execute(f'SELECT COUNT(*), COALESCE(MAX(v.chunk_id), 0) '
                            f'{base}', (scope, provider, dim)).fetchone()
        cached = _matrix_cache.get(key)
        if cached and cached[0] == stamp:
            return cached[1], cached[2], cached[3]
        rows = cur.execute(f'SELECT v.chunk_id, v.scale, v.q {base} '
                           f'ORDER BY v.chunk_id', (scope, provider, dim)).fetchall()
    if not rows:
        empty = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32),
                 np.empty((0, dim), dtype=np.int8))
        _matrix_cache[key] = (stamp, *empty)
        return empty
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    scales = np.array([r[1] for r in rows], dtype=np.float32)
    matrix = np.frombuffer(b''.join(r[2] for r in rows),
                           dtype=np.int8).reshape(len(rows), dim)
    _matrix_cache[key] = (stamp, ids, scales, matrix)
    return ids, scales, matrix


def _vector_hits(scope, query, limit, floor=VEC_MIN):
    """[(chunk_id, cosine)] best-first. dequant folded into the score:
    cos = (M_int8 · q) × scale/127 — one matmul, no decompressed copy kept."""
    emb = _embedder()
    if not getattr(emb, 'available', False):
        return []
    qv = emb.embed([query], prefix='search_query')
    if qv is None:
        return []
    q = np.asarray(qv[0], dtype=np.float32)
    ids, scales, matrix = _scope_matrix(
        scope, getattr(emb, 'provider_id', 'unknown'), int(q.shape[0]))
    if not len(ids):
        return []
    scores = (matrix.astype(np.float32) @ q) * (scales / 127.0)
    order = np.argsort(-scores)[:limit]
    return [(int(ids[i]), float(scores[i])) for i in order
            if scores[i] >= floor]


def _fts_hits(scope, query, limit):
    """[(chunk_id, bm25)] best-first — implicit-AND on word terms."""
    terms = re.findall(r'\w+', query)
    if not terms:
        return []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT f.rowid, bm25(doc_fts) FROM doc_fts f "
                "JOIN doc_chunks c ON c.id = f.rowid "
                "JOIN documents d ON d.id = c.doc_id "
                "WHERE doc_fts MATCH ? AND d.scope = ? "
                "AND d.status != 'missing' "
                "ORDER BY bm25(doc_fts) LIMIT ?",
                (' '.join(terms), scope, limit)).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        logger.warning(f"[LIBRARY] FTS query failed: {e}")
        return []


def _img_matrix(scope, dim):
    """(doc_ids, scales, int8 matrix) over img_vectors for one scope. Keyed
    on dim only — every core text provider is the nomic-v1.5 768 space, and
    the vision tower is aligned to exactly that (the shared-space gate)."""
    key = ('img', scope, dim)
    with get_connection() as conn:
        cur = conn.cursor()
        base = ('FROM img_vectors v JOIN documents d ON d.id = v.doc_id '
                'WHERE d.scope = ? AND v.dim = ?')
        stamp = cur.execute(f'SELECT COUNT(*), COALESCE(MAX(v.doc_id), 0) '
                            f'{base}', (scope, dim)).fetchone()
        cached = _matrix_cache.get(key)
        if cached and cached[0] == stamp:
            return cached[1], cached[2], cached[3]
        rows = cur.execute(f'SELECT v.doc_id, v.scale, v.q {base} '
                           f'ORDER BY v.doc_id', (scope, dim)).fetchall()
    if not rows:
        empty = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32),
                 np.empty((0, dim), dtype=np.int8))
        _matrix_cache[key] = (stamp, *empty)
        return empty
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    scales = np.array([r[1] for r in rows], dtype=np.float32)
    matrix = np.frombuffer(b''.join(r[2] for r in rows),
                           dtype=np.int8).reshape(len(rows), dim)
    _matrix_cache[key] = (stamp, ids, scales, matrix)
    return ids, scales, matrix


_vis_space_warned = False


def _photo_hits(scope, query, limit):
    """[(doc_id, cosine)] — the text query against PIXELS (shared vector
    space). This is the half of the fusion EXIF can't do: scene content.

    The shared-space gate is an ASSUMPTION about the text embedder (nomic
    v1.5's 768 space, which the vision tower is aligned to). A swapped text
    embedder silently kills or scrambles photo search — so mismatches log
    LOUDLY once instead of degrading in the dark."""
    global _vis_space_warned
    emb = _embedder()
    if not getattr(emb, 'available', False):
        return []
    qv = emb.embed([query], prefix='search_query')
    if qv is None:
        return []
    q = np.asarray(qv[0], dtype=np.float32)
    qdim = int(q.shape[0])
    prov = str(getattr(emb, 'provider_id', '') or '')
    ids, scales, matrix = _img_matrix(scope, qdim)
    if not len(ids):
        # Empty at THIS dim while image vectors exist at another → the text
        # embedder was swapped out of the vision model's space. Without this,
        # photos just vanish from every search with zero telemetry.
        if not _vis_space_warned:
            with get_connection() as conn:
                other = conn.execute(
                    'SELECT v.dim FROM img_vectors v JOIN documents d '
                    'ON d.id = v.doc_id WHERE d.scope = ? AND v.dim != ? '
                    'LIMIT 1', (scope, qdim)).fetchone()
            if other:
                _vis_space_warned = True
                logger.warning(
                    f"[LIBRARY] photo search OFF: text embedder '{prov}' is "
                    f"{qdim}-dim, image vectors are {other[0]}-dim. Photos "
                    f"return to search when a vision-space-compatible text "
                    f"embedder is active.")
        return []
    with get_connection() as conn:
        vprov_row = conn.execute(
            'SELECT v.provider FROM img_vectors v JOIN documents d '
            'ON d.id = v.doc_id WHERE d.scope = ? AND v.dim = ? LIMIT 1',
            (scope, qdim)).fetchone()
    vprov = str(vprov_row[0] if vprov_row else '')
    if ('nomic' in vprov.lower() and 'nomic' not in prov.lower()
            and not _vis_space_warned):
        _vis_space_warned = True
        logger.warning(
            f"[LIBRARY] photo search may be unreliable: image vectors live "
            f"in '{vprov}' (nomic space) but the text embedder is '{prov}' "
            f"— same dimension, different space, cosines are noise.")
    scores = (matrix.astype(np.float32) @ q) * (scales / 127.0)
    order = np.argsort(-scores)[:limit]
    floor = _vis_min()
    return [(int(ids[i]), float(scores[i])) for i in order
            if scores[i] >= floor]


PHOTO_DESC_MAX = 256   # caption cap on the result LINE (full text stays put)


def _photo_line(doc_id, title, meta_raw, description=None):
    """One photo, metadata only — caption · date · place · people. Pixels
    cost context; view_image(id) spends it deliberately. The caption is
    load-bearing: filenames like 'download (13)' say nothing."""
    try:
        m = json.loads(meta_raw or '{}')
    except Exception:
        m = {}
    desc = (description or '').strip()
    if len(desc) > PHOTO_DESC_MAX:
        desc = desc[:PHOTO_DESC_MAX - 1].rstrip() + '…'
    bits = []
    if m.get('taken'):
        bits.append(str(m['taken'])[:10])
    if m.get('place'):
        bits.append(m['place'])
    if m.get('people'):
        bits.append(', '.join(m['people']))
    cap = f' — "{desc}"' if desc else ''
    tail = f" — {' · '.join(bits)}" if bits else ''
    return f"  [doc {doc_id}] {title}{cap}{tail}"


def _fuse(vec_hits, fts_hits):
    """Reciprocal-rank fusion: present in both lists beats top of one."""
    fused = {}
    for hits in (vec_hits, fts_hits):
        for rank, (cid, _) in enumerate(hits):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(fused.items(), key=lambda x: -x[1])


def _doc_row(cur, doc_id):
    r = cur.execute(
        'SELECT id, title, author, description, kind, importance, private_key, '
        'collection_id, status FROM documents WHERE id = ?', (doc_id,)).fetchone()
    if not r:
        return None
    return {'id': r[0], 'title': r[1], 'author': r[2], 'description': r[3],
            'kind': r[4], 'importance': r[5], 'private_key': r[6],
            'collection_id': r[7], 'status': r[8]}


def _breadcrumb(cur, doc):
    parts = []
    cid = doc.get('collection_id')
    while cid:
        row = cur.execute('SELECT name, parent_id FROM collections WHERE id = ?',
                          (cid,)).fetchone()
        if not row:
            break
        parts.append(row[0])
        cid = row[1]
    parts.reverse()
    return ' › '.join(parts + [doc['title']])


def _stitch(cur, doc_id, seq, content):
    """§N returns with the tail of §N-1 and the head of §N+1 — who's losing
    their head arrives in the same block as the sentencing. Chunks that
    carry a packed overlap ('…tail\\n\\n' from the chunker) already HOLD
    prev's ending — prepending prev's tail again printed the same text
    twice (Kermit sip, fix D)."""
    nxt = cur.execute('SELECT content FROM doc_chunks WHERE doc_id = ? '
                      'AND seq = ?', (doc_id, seq + 1)).fetchone()
    parts = []
    if not content.startswith('…'):
        prev = cur.execute('SELECT content FROM doc_chunks WHERE doc_id = ? '
                           'AND seq = ?', (doc_id, seq - 1)).fetchone()
        if prev:
            parts.append('…' + prev[0][-STITCH_EDGE:])
    parts.append(content)
    if nxt:
        parts.append(nxt[0][:STITCH_EDGE] + '…')
    return '\n'.join(parts)


def _snippet(content, n=SNIPPET_LEN):
    """Teaser cut for mixed rides: packed overlap stripped, whitespace
    collapsed, capped with an ellipsis."""
    c = content
    if c.startswith('…'):
        cut = c.find('\n\n')
        if 0 < cut <= STITCH_EDGE + 8:
            c = c[cut + 2:]
    c = ' '.join(c.split())
    return c if len(c) <= n else c[:n - 1].rstrip() + '…'


def _whole_note(cur, doc_id):
    rows = cur.execute('SELECT content FROM doc_chunks WHERE doc_id = ? '
                       'ORDER BY seq', (doc_id,)).fetchall()
    return '\n\n'.join(r[0] for r in rows)


def search_library(scope, query, limit=8, doc=None, private_key=None,
                   mixed=False):
    """The Cancun contract (tmp/knowledge-layer.md): hits grouped by document,
    breadcrumbed and addressed, notes whole, others stitched, per-doc caps by
    importance, counts + narrowing path always stated. Returns (text, found).
    mixed=True = riding inside an all-layer search: tighter caps, no
    'no matches' chatter (absence is silent there)."""
    query = (query or '').strip()
    if not query:
        return "Search query cannot be empty.", False
    ensure_scan_async()   # watched folders freshen in the background
    over = max(limit * 4, 24)
    fused = _fuse(_vector_hits(scope, query, over,
                               VEC_MIN_MIXED if mixed else VEC_MIN),
                  _fts_hits(scope, query, over))
    vis = _photo_hits(scope, query, over)
    if not fused and not vis:
        return ("" if mixed else f"The library has no matches for '{query}'."), False
    caps = MIXED_CAPS if mixed else DOC_CAPS
    pk = (private_key or '').strip()
    with get_connection() as conn:
        cur = conn.cursor()
        docs, order = {}, []
        photos = {}   # doc_id → pool score (metadata-text hits + vision hits)
        for cid, score in fused:
            row = cur.execute('SELECT doc_id, seq, chapter, content FROM '
                              'doc_chunks WHERE id = ?', (cid,)).fetchone()
            if not row:
                continue
            doc_id, seq, chapter, content = row
            if doc is not None and doc_id != int(doc):
                continue
            if doc_id not in docs:
                d = _doc_row(cur, doc_id)
                if (not d or d['status'] == 'missing'
                        or (d['private_key'] and d['private_key'] != pk)):
                    docs[doc_id] = None
                    continue
                if d['kind'] == 'image':
                    # Photos pool separately — EXIF/annotation text narrowed,
                    # vision ranks within; rendered as metadata lines below.
                    docs[doc_id] = None
                    photos[doc_id] = photos.get(doc_id, 0.0) + score
                    continue
                d['hits'] = []
                d['score'] = score * (0.7 + d['importance'] / 2.0)
                docs[doc_id] = d
                order.append(doc_id)
            d = docs[doc_id]
            if d is None:
                continue
            cap = caps.get(_imp_word(d['importance']), 3)
            if len(d['hits']) >= cap:
                continue
            # A hit within ±1 of a kept hit is already inside its stitch.
            if any(abs(seq - s) <= 1 for s, _, _ in d['hits']):
                continue
            d['hits'].append((seq, chapter, content))
        for rank, (did, _cos) in enumerate(vis):
            if doc is not None and did != int(doc):
                continue
            d = _doc_row(cur, did)
            if (not d or d['kind'] != 'image' or d['status'] == 'missing'
                    or (d['private_key'] and d['private_key'] != pk)):
                continue
            photos[did] = photos.get(did, 0.0) + 1.0 / (RRF_K + rank + 1)
        keep = [docs[i] for i in order if docs[i] and docs[i]['hits']]
        keep.sort(key=lambda d: -d['score'])
        keep = keep[:limit]
        if not keep and not photos:
            return ("" if mixed else f"The library has no matches for '{query}'."), False
        blocks = []
        for d in keep:
            total = cur.execute('SELECT COUNT(*) FROM doc_chunks WHERE '
                                'doc_id = ?', (d['id'],)).fetchone()[0]
            crumb = _breadcrumb(cur, d)
            by = f" — {d['author']}" if d['author'] else ""
            imp = _imp_word(d['importance'])
            b = []
            if d['kind'] == 'note':
                b.append(f"\n📝 {crumb}{by} (note, {imp}) [doc {d['id']}]")
                note = _whole_note(cur, d['id'])
                # Teaser contract (fix A): a taste and a handle, never the
                # whole shelf. Explicit searches get a longer leash than
                # mixed rides, but knowledge notes have no save-time cap —
                # a matched 60KB note must not dump whole either way.
                cap_n = NOTE_MIXED_MAX if mixed else NOTE_FULL_MAX
                if len(note) > cap_n:
                    b.append(note[:cap_n - 100].rstrip()
                             + f"… [read_document({d['id']}) reads it all]")
                else:
                    b.append(note)
            elif mixed:
                seq, chapter, content = sorted(d['hits'])[0]
                ch = f", ch. {chapter}" if chapter else ""
                b.append(f"\n📄 {crumb}{by} ({d['kind']}, {imp}) "
                         f"[doc {d['id']}] §{seq + 1}/{total}{ch}:")
                b.append(f'  "{_snippet(content)}"')
                b.append(f"  [read_document({d['id']}, around={seq + 1}) "
                         f"for the full passage]")
            else:
                b.append(f"\n📄 {crumb}{by} ({d['kind']}, {imp}) [doc {d['id']}]")
                for seq, chapter, content in sorted(d['hits']):
                    ch = f", ch. {chapter}" if chapter else ""
                    b.append(f"  §{seq + 1}/{total}{ch}:")
                    b.append('  ' + _stitch(cur, d['id'], seq, content)
                             .replace('\n', '\n  '))
                b.append(f"  [read_document({d['id']}, around=N) digs; "
                         f"page=1 reads from the start]")
            blocks.append('\n'.join(b))
        dropped = 0
        # Hard budget (fix C): mixed rides can never flood a search that
        # didn't aim at the library; explicit searches get a bigger room
        # but still a ceiling.
        budget = LIB_MIXED_CHAR_CAP if mixed else LIB_CHAR_CAP
        kept_blocks, used = [], 0
        for blk in blocks:
            if kept_blocks and used + len(blk) > budget:
                dropped += 1
                continue
            kept_blocks.append(blk)
            used += len(blk)
        blocks = kept_blocks
        lines = []
        if not mixed:
            lines.append(f"Library matches for '{query}' "
                         f"({len(keep)} document{'s' if len(keep) != 1 else ''}"
                         + (f", {len(photos)} photo"
                            f"{'s' if len(photos) != 1 else ''}" if photos
                            else "") + "):")
        lines.extend(blocks)
        if dropped:
            lines.append(f"  …{dropped} more document"
                         f"{'s' if dropped != 1 else ''} matched — "
                         f"search_memory(layer='knowledge') digs deeper.")
        if photos:
            # The Cancun contract: metadata lines only, counts stated,
            # pixels on demand.
            cap = PHOTO_CAP_MIXED if mixed else PHOTO_CAP
            ranked = sorted(photos.items(), key=lambda x: -x[1])[:cap]
            shown = []
            for did, _s in ranked:
                r = cur.execute('SELECT title, meta, description FROM '
                                'documents WHERE id = ?', (did,)).fetchone()
                if r:
                    shown.append(_photo_line(did, r[0], r[1], r[2]))
            if shown:
                lines.append(f"\n🖼 Photos — {len(shown)} of {len(photos)} "
                             f"matched (image RAG: pixels and captions "
                             f"matched against your phrase):")
                lines.extend(shown)
                more = len(photos) - len(shown)
                tail = f"; {more} more behind this search" if more else ""
                lines.append(f"  [view_image(id) shows a photo{tail}]")
    return '\n'.join(lines), True


def read_document_text(scope, doc_id, page=None, around=None, start=None,
                       end=None, private_key=None):
    """Sequential reading + range digs. page counts from 1 ({PAGE_CHUNKS}
    chunks each); around=N gives N±2; start/end an explicit §range (capped).
    Returns (text, ok)."""
    with get_connection() as conn:
        cur = conn.cursor()
        d = _doc_row(cur, int(doc_id))
        if not d:
            return f"No document [{doc_id}] in the library.", False
        owner = cur.execute('SELECT scope FROM documents WHERE id = ?',
                            (int(doc_id),)).fetchone()[0]
        if owner != scope:
            return f"No document [{doc_id}] in this scope.", False
        if d['private_key'] and d['private_key'] != (private_key or '').strip():
            return f"No document [{doc_id}] in the library.", False
        if d['status'] == 'missing':
            return (f"[doc {doc_id}] {d['title']} is currently missing from "
                    f"its watched folder (drive unplugged?).", False)
        total = cur.execute('SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ?',
                            (int(doc_id),)).fetchone()[0]
        if not total:
            return f"[{doc_id}] {d['title']} has no readable text yet.", False
        if around is not None:
            lo, hi = max(0, int(around) - 1 - 2), min(total, int(around) + 2)
        elif start is not None or end is not None:
            lo = max(0, (int(start) if start is not None else 1) - 1)
            hi = min(total, int(end) if end is not None else lo + RANGE_MAX)
            hi = min(hi, lo + RANGE_MAX)
        else:
            p = max(1, int(page or 1))
            lo, hi = (p - 1) * PAGE_CHUNKS, min(total, p * PAGE_CHUNKS)
            if lo >= total:
                pages = (total + PAGE_CHUNKS - 1) // PAGE_CHUNKS
                return (f"[{doc_id}] {d['title']} has {pages} pages "
                        f"(§1-{total}).", False)
        rows = cur.execute('SELECT seq, chapter, content FROM doc_chunks '
                           'WHERE doc_id = ? AND seq >= ? AND seq < ? '
                           'ORDER BY seq', (int(doc_id), lo, hi)).fetchall()
        crumb = _breadcrumb(cur, d)
        by = f" — {d['author']}" if d['author'] else ""
        lines = [f"📖 {crumb}{by} (§{lo + 1}-{hi} of {total})"]
        last_ch = None
        for seq, chapter, content in rows:
            if chapter and chapter != last_ch:
                lines.append(f"\n## {chapter}")
                last_ch = chapter
            lines.append(f"\n§{seq + 1}\n{content}")
        if hi < total:
            nxt = (hi // PAGE_CHUNKS) + 1
            lines.append(f"\n[continues — read_document({doc_id}, "
                         f"page={nxt}) for more]")
    return '\n'.join(lines), True


def view_image_data(scope, doc_id, private_key=None, max_dim=1024):
    """The view_image tool (I4): pixels on demand, resized for the LLM,
    riding the tool-result image rail. Returns ({'text', 'images'}, True)
    or (refusal_text, False)."""
    import base64
    import io
    from PIL import Image, ImageOps
    with get_connection() as conn:
        row = conn.execute('SELECT scope, title, kind, meta, source_path, '
                           'private_key, description FROM documents '
                           'WHERE id = ?', (int(doc_id),)).fetchone()
    if not row or row[0] != scope or row[2] != 'image':
        return f"No image [doc {doc_id}] in the library.", False
    if row[5] and row[5] != (private_key or '').strip():
        return f"No image [doc {doc_id}] in the library.", False
    if not row[4] or not Path(row[4]).exists():
        return f"[doc {doc_id}] {row[1]} has no image file.", False
    _heic_ready()
    try:
        img = ImageOps.exif_transpose(Image.open(row[4]))
        img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=85)
    except Exception as e:
        return f"Couldn't read [doc {doc_id}]: {e}", False
    try:
        m = json.loads(row[3] or '{}')
    except Exception:
        m = {}
    lines = ['🖼 ' + _photo_line(int(doc_id), row[1], row[3], row[6]).strip()]
    if m.get('notes'):
        lines.append(f"notes: {m['notes']}")
    return {'text': '\n'.join(lines),
            'images': [{'data': base64.b64encode(buf.getvalue()).decode('ascii'),
                        'media_type': 'image/jpeg'}]}, True


def catalog_text(scope):
    """The card catalog she browses — every drawer with its description.
    Descriptions are retrieval surface, so they all show."""
    with get_connection() as conn:
        cur = conn.cursor()
        cats = cur.execute('SELECT id, name, description FROM collections '
                           'WHERE scope = ? AND parent_id IS NULL '
                           'ORDER BY name', (scope,)).fetchall()
        loose = cur.execute('SELECT id, title, kind, description, importance '
                            'FROM documents WHERE scope = ? AND collection_id '
                            'IS NULL ORDER BY updated DESC', (scope,)).fetchall()
        n_docs = cur.execute('SELECT COUNT(*) FROM documents WHERE scope = ?',
                             (scope,)).fetchone()[0]
        if not n_docs and not cats:
            return "The library is empty in this scope.", True

        def _doc_line(r, pad):
            desc = f' — "{r[3]}"' if r[3] else ""
            return (f"{pad}[doc {r[0]}] {r[1]} ({r[2]}, "
                    f"{_imp_word(r[4])}){desc}")

        lines = [f"🏛 Library — scope '{scope}' ({n_docs} documents)"]
        for cid, name, desc in cats:
            d = f' — "{desc}"' if desc else ""
            lines.append(f"\n▸ {name}{d}")
            topics = cur.execute('SELECT id, name, description FROM collections '
                                 'WHERE parent_id = ? ORDER BY name',
                                 (cid,)).fetchall()
            for tid, tname, tdesc in topics:
                td = f' — "{tdesc}"' if tdesc else ""
                lines.append(f"  ▹ {tname}{td}")
                for r in cur.execute(
                        'SELECT id, title, kind, description, importance FROM '
                        'documents WHERE collection_id = ? ORDER BY title',
                        (tid,)).fetchall():
                    lines.append(_doc_line(r, '    '))
            for r in cur.execute(
                    'SELECT id, title, kind, description, importance FROM '
                    'documents WHERE collection_id = ? ORDER BY title',
                    (cid,)).fetchall():
                lines.append(_doc_line(r, '  '))
        if loose:
            lines.append("\n▸ (unfiled)")
            for r in loose:
                lines.append(_doc_line(r, '  '))
    return '\n'.join(lines), True
