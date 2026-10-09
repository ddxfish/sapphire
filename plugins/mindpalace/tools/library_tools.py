# plugins/mindpalace/tools/library_tools.py
# Library (Knowledge v3) — her tool surface (P3b, 2026-07-17).
# Five tools: `library` (the card catalog — drawers WITH descriptions, that's
# how she finds where to look), `read_document` (sequential reading + range
# digs), `memory_view_image` (library pictures by query → the one match itself, a
# numbered sheet of several, or an honest 'none look like that' with the
# nearest listed and no pixels spent — gated relatively, see library.py;
# one doc by id; one picture from this chat by img: handle — the combined
# image_view retired 2026-09-10, Krem's vote A: one door per source),
# `local_view_images` (files/folders on this machine → a numbered sheet,
# paged) and `memory_save_image` (any image → the library). Search
# itself lives inside search_memory: layer='knowledge' reroutes to the
# library engine, mixed searches get a library append (palace_tools).
# save_memory layer='knowledge' becomes a library note — her muscle memory
# keeps working, the storage underneath got a building.
# Image tools follow core/images.py's one lane (image upgrade 2026-09-10):
# the sheet is the only image in the return; individuals are stashed img:
# handles named in the text; the GALLERY marker v3 gives the user the same
# numbered set (library tiles ride the thumb route — no copies).

import json
import logging
import math
import threading
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🏛'
TOOL_CATEGORY = 'knowledge'
GROUP = 'Mind Palace'

AVAILABLE_FUNCTIONS = ['library', 'read_document', 'memory_view_image',
                       'local_view_images', 'memory_save_image']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "library",
            "description": ("Browse the library's catalog: every category, topic and document. search_memory(layer='knowledge') "
                            "searches inside the documents."),
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_document",
            "description": ("Read a library document ([doc N]). page=N reads in order, 5 sections a page; around=N gives section "
                            "N with its neighbors (follow a search hit); start/end give a range."),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "integer"},
                    "page": {"type": "integer", "description": "From 1"},
                    "around": {"type": "integer"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "private_key": {"type": "string", "description": "For a private document"}
                },
                "required": ["document_id"]
            }
        }
    },
    {
        "type": "function",
        "category": "memory",
        "is_local": True,
        "function": {
            "name": "memory_view_image",
            "description": ("Look at a remembered picture; the user sees it too. query = the library's best matches by what is IN"
                            " the pictures, as a numbered contact sheet with [doc N] ids. document_id = one library image. "
                            "image_id = a picture from this chat."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for: scene, people, place, caption words"},
                    "count": {"type": "integer", "description": "1-12 (default 6)"},
                    "document_id": {"type": "integer", "description": "A [doc N] id"},
                    "image_id": {"type": "string", "description": "An img:<id> handle from this chat"},
                    "private_key": {"type": "string", "description": "For keyed images"}
                }
            }
        }
    },
    {
        "type": "function",
        "category": "files",
        "is_local": True,
        "function": {
            "name": "local_view_images",
            "description": ("Look at image files on this computer; the user sees numbered tiles. Several paths make a contact "
                            "sheet. A folder pages through its images and lists subfolders. Each picture gets an img: handle; "
                            "memory_save_image(the path) keeps the full file."),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute"},
                    "folder": {"type": "string", "description": "Absolute directory"},
                    "page": {"type": "integer", "description": "Folder page, from 1"},
                    "count": {"type": "integer", "description": "1-12 per page (default 6)"}
                }
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "category": "memory",
        "network": True,
        "is_local": False,
        "function": {
            "name": "memory_save_image",
            "description": ("Keep an image in the library under a topic, searchable by pixels and caption. View it later with "
                            "memory_view_image(document_id=N)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "img:<id>, doc:<N>, an absolute path or an image URL"},
                    "topic": {"type": "string", "description": "Existing or new topic to file it under"},
                    "caption": {"type": "string", "description": "What it is; becomes the title and searchable caption"},
                    "private_key": {"type": "string", "description": "Gate word to keep it private"}
                },
                "required": ["source", "topic"]
            }
        }
    },
]


def _lib():
    from plugins.mindpalace.tools import library
    return library


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _view_one(source, private_key=None):
    """Any core.images handle → one resized image on the rail (the single-
    image tail every viewing tool shares). The doc: lane resolves scope
    itself; img:/path need none."""
    from core import images as ci
    source = (source or '').strip()
    try:
        r = ci.resolve(source, private_key=private_key)
        w, h = r.size
        shaped = ci.for_chat(r.data)
    except ci.ImageError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"[LIBRARY] view {source!r}: {type(e).__name__}: {e}")
        return f"Couldn't open that image: {e}", False
    origin = {'chat': 'from this chat', 'library': 'from the library',
              'file': 'from disk', 'web': 'from the web'}[r.origin]
    return ci.result(f"{r.label} — {w}x{h} — {origin}. You're looking at it now.", [shaped]), True


_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif', '.heic'}
_ROUTE = '/api/plugin/mindpalace/library/documents/{did}/{what}?scope={scope}'


def _clamp(v, default, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi)) if n else default


def _memory_view_image(arguments):
    """image_id → a picture from this chat (no memory scope needed);
    document_id → one library image; query → the library's best pixel
    matches as one numbered sheet (tiles from the thumb route, [doc N] per
    line)."""
    from core import images as ci
    pk = (arguments.get('private_key') or '').strip()
    image_id = (arguments.get('image_id') or '').strip()
    if image_id:
        if not image_id.startswith('img:'):
            return f"image_id takes an img:<id> handle from this chat, not {image_id!r}.", False
        return _view_one(image_id)
    did = arguments.get('document_id')
    query = (arguments.get('query') or '').strip()
    if not did and not query:
        return "Give me a query (what to look for), a document_id, or an image_id.", False
    scope = _pt()._get_current_scope()
    if scope is None:
        return "The library is unavailable when memory is disabled for this chat.", False
    lib = _lib()
    if did:
        return _view_one(f'doc:{did}', pk or None)
    count = _clamp(arguments.get('count'), 6, 1, 12)
    ranked, status, total = lib._photo_rank(scope, query, count * 2)
    # An empty answer names its cause — the old one blamed the embedder for
    # all four (down / no pictures / swapped space / nothing scored).
    if status == 'no_embedder':
        return "Can't search pictures right now: the text embedder is unavailable (Settings → Embedding).", False
    if status == 'space_mismatch':
        return ("Picture search is off: the active text embedder isn't in the vision model's space "
                "(the log has the dims). Captions still search via search_memory."), False
    if not total:
        return ("This chat's library has no pictures yet (or none have been embedded) — "
                "memory_save_image puts one in."), True
    rows = []       # (doc row, hit) best-first, behind the private + missing walls
    with lib.get_connection() as conn:
        for doc_id, _score, hit in ranked:
            r = conn.execute("SELECT id, title, meta, description, private_key, status FROM documents "
                             "WHERE id = ? AND scope = ? AND kind = 'image'", (doc_id, scope)).fetchone()
            if not r or r[5] == 'missing' or (r[4] and r[4] != pk):
                continue
            rows.append((r, hit))
            if len(rows) >= count:
                break
    if not rows:
        return f"No viewable pictures for '{query}' in this chat's library ({total} exist — keyed or missing).", True
    line = lambda r: lib._photo_line(r[0], r[1], r[2], r[3]).strip()
    strong = [r for r, hit in rows if hit]
    weak = [r for r, hit in rows if not hit]
    if not strong:
        # Nothing cleared the gate: say so, list the nearest, spend no pixels.
        closest = "\n".join(f"{i}. {line(r)}" for i, r in enumerate(weak, 1))
        return (f"None of the {total} picture(s) in this chat's library look like '{query}'. "
                f"Closest, all weak (not shown — memory_view_image(document_id=N) opens one):\n{closest}"), True
    entries = []
    for r in strong:
        src, thumb, render, _ext = lib.image_paths(scope, r[0])
        pick = thumb or render or src
        e = {'raw': pick.read_bytes() if pick else None, 'stash': False,      # already stored: tiles off the route
             'thumb': _ROUTE.format(did=r[0], what='thumb', scope=quote(scope)),
             'full': _ROUTE.format(did=r[0], what='file', scope=quote(scope)),
             'title': r[1] or ''}
        if len(strong) == 1 and (render or src):      # one match → the picture itself, not its thumb
            e['show'] = (render or src).read_bytes()
        entries.append(e)
    lines = [f"{i}. {line(r)}" for i, r in enumerate(strong, 1)]
    if weak:
        lines.append("Weaker, not shown: " + "; ".join(line(r) for r in weak))
    images, tail = ci.gallery(f'library: {query}', entries)
    head = (f"1 library picture matches '{query}'; " if len(strong) == 1 else
            f"{len(strong)} library pictures match '{query}', best first — numbered like the sheet; ")
    text = head + "memory_view_image(document_id=N) for a close-up:\n" + "\n".join(lines) + "\n" + tail
    return (ci.result(text, images) if images else text), True


def _local_view_images(arguments):
    """paths → one image or a numbered sheet; folder → a page of its images.
    Individuals are stashed as 512px thumbs (img: handles for the row and for
    a save); the absolute paths stay in the text for full-resolution saves."""
    from core import images as ci
    paths = arguments.get('paths') or []
    if isinstance(paths, str):
        paths = [paths]
    folder = (arguments.get('folder') or '').strip()
    count = _clamp(arguments.get('count'), 6, 1, 12)
    page = _clamp(arguments.get('page'), 1, 1, 100000)
    head, title = '', 'local images'
    if folder and not paths:
        d = Path(folder)
        if not d.is_absolute() or not d.is_dir():
            return f"{folder} is not an absolute directory on this machine.", False
        try:
            entries = sorted(d.iterdir(), key=lambda q: q.name.lower())
        except OSError as e:
            return f"Couldn't read {folder}: {e}", False
        files = [q for q in entries if q.is_file() and q.suffix.lower() in _IMAGE_EXTS]
        subs = [q.name for q in entries if q.is_dir() and not q.name.startswith('.')]
        pages = max(1, math.ceil(len(files) / count))
        page = min(page, pages)
        paths = [str(q) for q in files[(page - 1) * count: page * count]]
        head = f"{d}: {len(files)} image(s), page {page} of {pages}"
        if subs:
            head += f"; folders: {', '.join(subs[:20])}" + (' …' if len(subs) > 20 else '')
        head += "\n"
        title = f"{d.name or d}: page {page} of {pages}"
        if not files:
            return head + "No images in this folder.", True
    if not paths:
        return "Give me paths (absolute file paths) or a folder (absolute directory).", False
    paths = [str(q).strip() for q in paths if str(q).strip()][:12]
    # This tool is is_local (allowed in private chats) and ci.resolve() takes a
    # URL before it takes a path — a `paths=["https://…"]` entry was real egress
    # from a private chat (broadsword H2). Files only, by construction.
    bad = [q for q in paths if not Path(q).is_absolute()]
    if bad:
        return f"Only absolute file paths here (not {bad[0]!r}) — for a URL use web_view_images.", False
    if len(paths) == 1:
        out, ok = _view_one(paths[0])
        if ok and head and isinstance(out, dict):
            out['text'] = head + out['text']
        return out, ok
    entries, meta = [], []
    for q in paths:
        try:
            r = ci.resolve(q)
            w, h = r.size
            entries.append({'raw': ci.for_chat(r.data, max_px=512), 'title': r.label})
            meta.append((r.label, f"{w}x{h}", q))
        except ci.ImageError as e:
            entries.append({'raw': None})
            meta.append((q, str(e), None))
    if not any(e['raw'] for e in entries):
        return head + "None of those opened as images:\n" + "\n".join(
            f"{i}. {m[0]} — {m[1]}" for i, m in enumerate(meta, 1)), False
    images, tail = ci.gallery(title, entries)
    lines = [f"{i}. {label} — {info}" + (f" — {e['handle']}" if e['handle'] else '') + (f"\n   {q}" if q else '')
             for i, (e, (label, info, q)) in enumerate(zip(entries, meta), 1)]
    text = (head + f"{len(paths)} image(s) — numbered like the sheet; img: = a thumbnail kept in this chat "
            f"(memory_view_image(image_id=...) to look again); memory_save_image(the path) keeps the original:\n"
            + "\n".join(lines) + "\n" + tail)
    return ci.result(text, images), True


def execute(function_name, arguments, config):
    try:
        if function_name == 'local_view_images':      # disk, no memory scope needed
            return _local_view_images(arguments)
        if function_name == 'memory_view_image':      # resolves scope itself (img: needs none)
            return _memory_view_image(arguments)
        pt = _pt()
        scope = pt._get_current_scope()
        if scope is None:
            return "The library is unavailable when memory is disabled for this chat.", False
        lib = _lib()
        if function_name == 'library':
            return lib.catalog_text(scope)
        if function_name == 'read_document':
            return lib.read_document_text(
                scope, arguments.get('document_id'),
                page=arguments.get('page'),
                around=arguments.get('around'),
                start=arguments.get('start'),
                end=arguments.get('end'),
                private_key=arguments.get('private_key'))
        if function_name == 'memory_save_image':
            return lib.save_image(
                scope, arguments.get('source'), arguments.get('topic'),
                caption=arguments.get('caption'),
                private_key=arguments.get('private_key'))
        return f"Unknown library function: {function_name}", False
    except Exception as e:
        logger.error(f"[LIBRARY] {function_name} error: {e}", exc_info=True)
        return f"Library error: {e}", False


# Boot-resume: this module is imported once at plugin load — a short timer
# later (app settled), any import jobs that survived a restart pick back up,
# and the coexistence migration runs ONCE (marker-gated no-op forever after;
# Admin's re-migrate button forces a fresh sweep). Failure-isolated.
def _boot_resume():
    try:
        _lib().resume_pending()
    except Exception as e:
        logger.warning(f"[LIBRARY] boot resume skipped: {e}")
    try:
        _lib().migrate_all()
    except Exception as e:
        logger.warning(f"[LIBRARY] boot migration skipped: {e}")
    try:
        _lib().tidy_migrated_titles()
        _lib().merge_migrated_duplicates()
    except Exception as e:
        logger.warning(f"[LIBRARY] migration polish skipped: {e}")
    try:
        _lib().backfill_doc_edges()
    except Exception as e:
        logger.warning(f"[LIBRARY] edge backfill skipped: {e}")
    try:
        _lib().backfill_places()   # photos that beat the GeoNames download
    except Exception as e:
        logger.warning(f"[LIBRARY] place backfill skipped: {e}")
    try:
        _lib().backfill_vision()   # photos missing pixel vectors get queued
    except Exception as e:
        logger.warning(f"[LIBRARY] vision backfill skipped: {e}")
    try:
        _lib().refresh_image_working()   # md render evolved → regen once
    except Exception as e:
        logger.warning(f"[LIBRARY] image working refresh skipped: {e}")
    try:
        _lib().scan_all()                # watched folders catch up
    except Exception as e:
        logger.warning(f"[LIBRARY] watch scan skipped: {e}")


if 'pytest' not in __import__('sys').modules:   # tests drive the queue by hand
    try:
        threading.Timer(15.0, _boot_resume).start()
    except Exception:
        pass
