"""The ComfyUI panel's doors (Settings > Plugins > ComfyUI): the workflow list, upload,
import of ComfyUI's last run, the fit check against a ComfyUI, and the kept clips."""
import asyncio
import logging
import re
from pathlib import Path

from plugins.comfyui import comfy as cf

logger = logging.getLogger(__name__)
ODD = "ComfyUI sent back something Sapphire couldn't read."


def _row(name, path):
    """One workflow as the panel shows it. A file that can't be read or described is still
    a row (with its Delete button), never a reason for the list to fail."""
    try:
        return {'name': name, **cf.describe(*cf.read(path))}
    except cf.ComfyError as e:
        return {'name': name, 'error': str(e)}
    except Exception as e:
        logger.warning(f"[COMFY] {name} can't be described: {e}")
        return {'name': name, 'error': "This file isn't a workflow ComfyUI could run."}


def _url(body, settings):
    return (body or {}).get('url') or (settings or {}).get('comfy_url')


def _store(name, graph, meta, replace):
    """Keep a workflow under `name`. One of that name is replaced only when asked to; its
    description stays unless the new file brings its own. A picked node does NOT stay:
    node ids belong to a graph, and id 6 in the new one may be the negative prompt."""
    try:
        shown = cf.describe(graph, meta)               # before the write: a file that can't be described is never kept
    except Exception as e:
        logger.warning(f"[COMFY] upload can't be described: {e}")
        return {'error': "That file isn't a workflow ComfyUI could run."}, 400
    with cf.LOCK:
        old_name, old = cf.find(name)
        if old and not replace:
            return {'error': f'A workflow called "{old_name}" is already here.'}, 409
        if old:
            name = old_name
            if not meta:
                try:
                    meta = {'description': cf.read(old)[1].get('description', '')}
                except cf.ComfyError:
                    meta = {}
                shown = cf.describe(graph, meta)
        cf.write(name, graph, meta)
    return {'name': name, 'replaced': bool(old), 'workflow': {'name': name, **shown}}


def _take(data, name, replace):
    try:
        graph, meta = cf.from_upload(data)
    except cf.ComfyError as e:
        return {'error': str(e)}, 400
    return _store(cf.slug(name), graph, meta, replace)


# GET workflows
def list_workflows(**_):
    count, size = cf.saved()
    return {'workflows': [_row(n, p) for n, p in cf.workflows().items()], 'outputs': {'count': count, 'bytes': size}}


# POST workflows   (multipart: file, name?, replace?)
async def upload(request=None, **_):
    if request is None:
        return {'error': 'No file came with that.'}, 400
    try:
        if int(request.headers.get('content-length') or 0) > cf.MAX_UPLOAD + 65536:
            return {'error': 'That file is too big to be a workflow.'}, 413
    except ValueError:
        pass
    form = await request.form()
    file = form.get('file')
    if file is None or not hasattr(file, 'read'):
        return {'error': 'No file came with that.'}, 400
    data = await file.read(cf.MAX_UPLOAD + 1)          # never more than the limit in memory, whatever the header said
    given = form.get('name')
    name = given if isinstance(given, str) and given.strip() else Path(file.filename or '').stem
    # reading a picture and a big JSON is work: off the event loop, like every sync handler
    return await asyncio.to_thread(_take, data, name, bool(form.get('replace')))


# POST import   body: {name, url?, replace?}
def import_last(body=None, settings=None, **_):
    name = str((body or {}).get('name') or '').strip()
    if not name:
        return {'error': 'Give it a name.'}, 400
    try:
        graph, meta = cf.parse(cf.Comfy(_url(body, settings)).last_run())
    except cf.ComfyError as e:
        return {'error': str(e)}, 400
    except Exception as e:
        logger.warning(f"[COMFY] import: {e}")
        return {'error': ODD}, 400
    return _store(cf.slug(name), graph, meta, bool((body or {}).get('replace')))


# PUT workflows/{name}   body: {description?, prompt_node?, image_node?}
def update(name=None, body=None, **_):
    with cf.LOCK:                                      # two edits of one row must not write over each other
        name, path = cf.find(name)
        if not path:
            return {'error': 'No such workflow.'}, 404
        try:
            graph, meta = cf.read(path)
        except cf.ComfyError as e:
            return {'error': str(e)}, 400
        for key in ('description', 'prompt_node', 'image_node'):
            if key in (body or {}):
                meta[key] = body[key]
        cf.write(name, graph, meta)
    return {'workflow': _row(name, path)}


# DELETE workflows/{name}
def delete(name=None, **_):
    with cf.LOCK:
        name, path = cf.find(name)
        if not path:
            return {'error': 'No such workflow.'}, 404
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            return {'error': f"Couldn't delete it ({e.strerror or 'the file is in use'})."}, 409
    return {'deleted': name}


# GET workflows/{name}/file
def download(name=None, **_):
    from fastapi.responses import Response
    name, path = cf.find(name)
    if not path:
        return {'error': 'No such workflow.'}, 404
    return Response(path.read_bytes(), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="{cf.slug(name)}.json"'})


# POST workflows/{name}/check   body: {url?}
def check(name=None, body=None, settings=None, **_):
    """Will this workflow run on that ComfyUI? Its nodes and model files against the server's own lists."""
    name, path = cf.find(name)
    if not path:
        return {'error': 'No such workflow.'}, 404
    try:
        graph, meta = cf.read(path)
        comfy = cf.Comfy(_url(body, settings))
        found = cf.check(graph, comfy.info(), meta)
    except cf.ComfyError as e:
        return {'ok': False, 'lines': [str(e)]}
    except Exception as e:
        logger.warning(f"[COMFY] check: {e}")
        return {'ok': False, 'lines': [ODD]}
    lines = [f"Missing custom node: {c}" for c in found['missing_nodes']]
    lines += [f"{m['title']}: {m['words']}" for m in found['missing']]
    lines += [f"'{f['value']}' will be read as '{f['to']}' there." for f in found['fixes']]
    if found['ok']:
        lines.insert(0, f"Ready. ComfyUI at {comfy.shown} has every node and model this workflow names.")
    return {'ok': found['ok'], 'lines': lines}


# POST test   body: {url}
def test(body=None, settings=None, **_):
    try:
        comfy = cf.Comfy(_url(body, settings))
        stats = comfy.stats()
    except cf.ComfyError as e:
        return {'ok': False, 'text': str(e)}
    system = stats['system']
    devices = stats.get('devices') if isinstance(stats.get('devices'), list) else []
    gpu = next((d for d in devices if isinstance(d, dict)), {})
    name = re.sub(r'^\w+:\d+\s+', '', str(gpu.get('name') or '').split(' : ')[0]).strip()[:80]
    vram = gpu.get('vram_total')
    size = f", {vram / 2 ** 30:.0f} GB" if isinstance(vram, (int, float)) and not isinstance(vram, bool) and vram > 0 else ''
    return {'ok': True, 'url': comfy.url,
            'text': f"ComfyUI {str(system.get('comfyui_version') or '')[:20]} on {str(system.get('os') or 'that machine')[:20]}"
                    + (f": {name}{size}" if name else '')}


# GET file/{name}   (media: a clip a run made)
def file(name=None, **_):
    """A kept clip. Only what a player or an <img> shows goes out under its own type; anything
    else is a download, so a file from another machine can never open as a page of this app."""
    from fastapi.responses import FileResponse
    path = cf.kept(name)
    if not path:
        return {'error': 'That clip is gone.'}, 404
    media = cf.MEDIA_TYPES.get(path.suffix.lower())
    headers = {'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'}
    if not media:
        headers['Content-Disposition'] = f'attachment; filename="{path.name}"'
    return FileResponse(str(path), media_type=media or 'application/octet-stream', headers=headers)


# DELETE outputs
def clear_outputs(**_):
    deleted, left = cf.clear_saved()
    return {'deleted': deleted, 'left': left}
