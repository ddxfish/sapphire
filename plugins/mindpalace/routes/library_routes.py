# plugins/mindpalace/routes/library_routes.py
# Library (Knowledge v3) app routes — the Library tab's windows (P4).
# Same trusted-surface stance as browse.py; all writes funnel through
# tools/library.py so chunking, embedding, and the queue behave identically
# for the UI and for her tools. Upload is the one async handler (multipart).

import asyncio
import logging

logger = logging.getLogger(__name__)


def _lib():
    from plugins.mindpalace.tools import library
    return library


def catalog(query=None, **_):
    q = query or {}
    scope = q.get('scope') or 'default'
    return _lib().catalog_data(scope)


def create_collection(body=None, **_):
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    cid, err = _lib().create_collection(
        scope, b.get('name'),
        description=b.get('description'), parent_id=b.get('parent_id'))
    if err:
        return {'error': err}, 400
    return {'success': True, 'id': cid}


def update_collection(cid=None, body=None, **_):
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    ok, err = _lib().update_collection(scope, cid,
                                       name=b.get('name'),
                                       description=b.get('description'))
    return ({'success': True} if ok else ({'error': err}, 400))


def delete_collection(cid=None, query=None, **_):
    q = query or {}
    scope = (q.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    ok, err = _lib().delete_collection(scope, cid)
    return ({'success': True} if ok else ({'error': err}, 400))


def create_note(body=None, **_):
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    doc_id, err = _lib().import_note(
        scope, b.get('title'), b.get('content'),
        description=b.get('description'),
        collection_id=b.get('collection_id'),
        importance=b.get('importance'), added_by='user',
        private_key=b.get('private_key'))
    if err:
        return {'error': err}, 400
    return {'success': True, 'id': doc_id}


async def upload(request=None, **_):
    """Multipart: file(s) + fields (scope, kind, title, description,
    collection_id, importance). Bulk = multiple files, one doc each; a
    refusal (scanned PDF, DRM) reports per file, the rest proceed."""
    form = await request.form()
    scope = (form.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    fields = dict(kind=form.get('kind') or None,
                  description=form.get('description') or None,
                  collection_id=(int(form.get('collection_id'))
                                 if form.get('collection_id') else None),
                  importance=form.get('importance') or None)
    files = form.getlist('file')
    if not files:
        return {'error': 'No files in upload'}, 400
    lib = _lib()
    results, errors = [], []
    single = len(files) == 1
    for f in files:
        raw = await f.read()
        from pathlib import Path as _P
        title = form.get('title') if single else None
        # Imports do real work (PDF extraction, EXIF, thumbnail, embed) —
        # off the event loop, or every SSE stream stalls for the duration.
        if _P(f.filename or '').suffix.lower() in lib.IMAGE_KINDS:
            doc_id, err = await asyncio.to_thread(
                lib.import_image, scope, f.filename, raw, title=title,
                description=fields['description'],
                collection_id=fields['collection_id'],
                importance=fields['importance'])
        else:
            doc_id, err = await asyncio.to_thread(
                lib.import_file, scope, f.filename, raw,
                title=title, **fields)
        if err:
            errors.append({'file': f.filename, 'error': err})
        else:
            results.append({'file': f.filename, 'id': doc_id})
    return {'success': bool(results), 'imported': results, 'refused': errors}


def export_zip(query=None, **_):
    """GET library/export?scope= → the courier zip: manifest.json + every
    owned doc's source file. Derived data excluded — import rebuilds it."""
    import os
    import tempfile
    from datetime import datetime
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    scope = (query or {}).get('scope') or 'default'
    fd, tmp = tempfile.mkstemp(suffix='.zip')
    os.close(fd)
    report, err = _lib().export_scope_zip(scope, tmp)
    if err:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {'error': err}, 400
    fname = f"sapphire-library-{scope}-{datetime.now().strftime('%Y%m%d')}.zip"
    return FileResponse(tmp, media_type='application/zip',
                        headers={'Content-Disposition':
                                 f'attachment; filename="{fname}"'},
                        background=BackgroundTask(os.unlink, tmp))


async def import_zip(request=None, **_):
    """POST library/import-zip (multipart: file + scope) → import the courier
    zip into the chosen scope. Deep-copies files; idempotent re-import skips."""
    import os
    import tempfile
    form = await request.form()
    scope = (form.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    f = form.get('file')
    if not f:
        return {'error': 'No file in upload'}, 400
    raw = await f.read()
    fd, tmp = tempfile.mkstemp(suffix='.zip')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(raw)
        report, err = await asyncio.to_thread(_lib().import_scope_zip,
                                              scope, tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if err:
        return {'error': err}, 400
    return {'success': True, 'report': report}


def peek(body=None, **_):
    """The Admin tool console: run her READ-ONLY tools with explicit params
    and return the raw text she would see. Zero fingerprints by construction:
    search passes boost=False (no recall bumps), read_self passes stamp=False
    (ledger watermark untouched) and extra_tools=False (nothing executes).
    Whitelist only — no write verb is reachable from here."""
    b = body or {}
    scope = b.get('scope') or 'default'
    tool = (b.get('tool') or '').strip()
    a = b.get('args') or {}

    def _i(key, default=None):
        v = a.get(key)
        try:
            return int(v) if v not in (None, '') else default
        except (TypeError, ValueError):
            return default

    try:
        from plugins.mindpalace.tools import palace_tools as pt
        if tool == 'search_memory':
            text, ok = pt._search_memory(
                a.get('query') or '', scope, limit=_i('limit', 10),
                layer=(a.get('layer') or '').strip() or None,
                depth=_i('depth', 0), doc=_i('document_id'), boost=False)
        elif tool == 'get_recent_memories':
            text, ok = pt._get_recent_memories(scope, count=_i('count', 10))
        elif tool == 'read_self':
            from plugins.mindpalace.tools import self_tools
            text, ok = self_tools._read_self(scope, depth=_i('depth', 1),
                                             extra_tools=False, stamp=False)
        elif tool == 'library':
            text, ok = _lib().catalog_text(scope)
        elif tool == 'read_document':
            text, ok = _lib().read_document_text(
                scope, _i('document_id'), page=_i('page'),
                around=_i('around'))
        elif tool == 'list_goals':
            from plugins.mindpalace.tools import goal_tools
            text, ok = goal_tools._list(scope, goal_id=_i('goal_id'),
                                        status=a.get('status') or 'active')
        else:
            return {'error': f"'{tool}' isn't peekable — read-only tools "
                             f"only."}, 400
        return {'ok': bool(ok), 'output': text}
    except Exception as e:
        logger.error(f"[LIBRARY] peek {tool} failed: {e}", exc_info=True)
        return {'error': str(e)}, 500


def migrate(body=None, **_):
    """Admin's re-migrate button. Copies v1 + v2 knowledge into the library
    (idempotent — already-imported groups skip). Embedding continues in the
    background queue after this returns. Polish rides along: broken-title
    tidy + exact-duplicate merge among migrated docs."""
    report = _lib().migrate_all(force=True)
    report['titles_tidied'] = _lib().tidy_migrated_titles()
    report['duplicates_merged'] = _lib().merge_migrated_duplicates()
    return {'success': True, 'report': report}


def migration_status(query=None, **_):
    return {'report': _lib().migration_report()}


def get_document(did=None, query=None, **_):
    q = query or {}
    scope = q.get('scope') or 'default'
    title, text = _lib().document_text(scope, did)
    if title is None:
        return {'error': 'Not found'}, 404
    return {'id': int(did), 'title': title, 'text': text}


def update_document(did=None, body=None, **_):
    b = dict(body or {})
    scope = (b.pop('scope', None) or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    ok, err = _lib().update_document(scope, did, **b)
    return ({'success': True} if ok else ({'error': err}, 400))


def delete_document(did=None, query=None, **_):
    q = query or {}
    scope = (q.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    ok, err = _lib().delete_document(scope, did)
    return ({'success': True} if ok else ({'error': err}, 404))


def list_folders(query=None, **_):
    q = query or {}
    return {'folders': _lib().folders_data(q.get('scope') or 'default')}


def add_folder(body=None, **_):
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    fid, err = _lib().add_watch_folder(
        scope, b.get('path'),
        collection_id=(int(b['collection_id'])
                       if b.get('collection_id') else None))
    if err:
        return {'error': err}, 400
    return {'success': True, 'id': fid}


def delete_folder(fid=None, query=None, **_):
    q = query or {}
    ok, err = _lib().remove_watch_folder(q.get('scope') or 'default', fid)
    return ({'success': True} if ok else ({'error': err}, 404))


def rescan_folder(fid=None, body=None, **_):
    b = body or {}
    scope = b.get('scope') or 'default'
    if not any(f['id'] == int(fid)
               for f in _lib().folders_data(scope)):
        return {'error': 'Watch folder not found'}, 404
    report = _lib().scan_folder(int(fid))
    return {'success': True, 'report': report}


def image_thumb(did=None, query=None, **_):
    """Grid tile. Falls back to the original when the thumb didn't generate
    (rare) — the browser can usually cope."""
    from fastapi.responses import FileResponse
    q = query or {}
    src, thumb, _r, _e = _lib().image_paths(q.get('scope') or 'default', did)
    if thumb:
        return FileResponse(thumb, media_type='image/jpeg')
    if src:
        return FileResponse(src)
    return {'error': 'Not found'}, 404


def image_file(did=None, query=None, **_):
    """Lightbox full view. Browser-safe formats serve the original;
    HEIC serves a cached JPEG render (pixels for browsers, source untouched)."""
    from fastapi.responses import FileResponse
    q = query or {}
    src, _t, render, ext = _lib().image_paths(q.get('scope') or 'default', did)
    if not src:
        return {'error': 'Not found'}, 404
    if ext in _lib()._BROWSER_SAFE:
        return FileResponse(src)
    if render:
        return FileResponse(render, media_type='image/jpeg')
    return FileResponse(src)


def image_meta(did=None, body=None, **_):
    """Lightbox annotations: people tags + notes. Regenerates the working
    form — annotations become searchable the moment they're saved."""
    b = body or {}
    ok, err = _lib().update_image_meta(
        b.get('scope') or 'default', did,
        people=b.get('people'), notes=b.get('notes'))
    return ({'success': True} if ok else ({'error': err}, 400))


def download(did=None, query=None, **_):
    """which=original → the byte-exact upload; which=working → the living
    markdown; which=annotated (images) → original pixels with the current
    description/people/notes spliced into the container metadata."""
    from pathlib import Path
    from fastapi.responses import FileResponse, Response
    q = query or {}
    scope = q.get('scope') or 'default'
    which = q.get('which') or 'original'
    lib = _lib()
    if which == 'annotated':
        data, fname, mime = lib.export_annotated(scope, did)
        if data is None:
            return {'error': mime}, 400
        return Response(content=data, media_type=mime,
                        headers={'Content-Disposition':
                                 f'attachment; filename="{fname}"'})
    with lib.get_connection() as conn:
        row = conn.execute('SELECT scope, title, source_path, working_path '
                           'FROM documents WHERE id = ?', (int(did),)).fetchone()
    if not row or row[0] != scope:
        return {'error': 'Not found'}, 404
    path = lib._local_path(did, row[2] if which == 'original' else row[3])
    if not path:
        if which == 'original':
            # Notes have no source file — the working text IS the note.
            title, text = lib.document_text(scope, did)
            if title is None:
                return {'error': 'Not found'}, 404
            from fastapi.responses import PlainTextResponse
            safe = ''.join(c if c.isalnum() or c in ' ._-' else '_'
                           for c in title)[:60].strip() or f'doc-{did}'
            return PlainTextResponse(
                text, headers={'Content-Disposition':
                               f'attachment; filename="{safe}.md"'})
        return {'error': 'No file for this document'}, 404
    return FileResponse(path, filename=path.name)