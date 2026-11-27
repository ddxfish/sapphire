"""The ComfyUI panel's doors (Settings > Plugins > ComfyUI): the workflow list, upload,
import of ComfyUI's last run, the fit check against a ComfyUI, and the kept clips. And the
Apps page's: run a workflow as a job, ask how it is doing, stop it, list what was made."""
import asyncio
import copy
import json
import logging
import random
import re
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

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
def list_workflows(settings=None, **_):
    count, size = cf.saved()
    return {'workflows': [_row(n, p) for n, p in cf.workflows().items()],
            'outputs': {'count': count, 'bytes': size, 'backed_up': cf.backed_up(settings)}}


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
    # the browser's name for the file, read the same on every system (on Windows 'wan:v2.json' would lose 'wan:')
    name = given if isinstance(given, str) and given.strip() else PurePosixPath((file.filename or '').replace('\\', '/')).stem
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


# PUT workflows/{name}   body: {description?, prompt_node?, image_node?, extras?: {'node:input': {name?, min?, max?, off?}}}
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
        if isinstance((body or {}).get('extras'), dict):   # one control's row at a time; an empty row is "as found"
            here = {c['id'] for c in cf.controls(graph)}   # rows for controls this graph no longer has are dropped
            rows = {**(meta.get('extras') if isinstance(meta.get('extras'), dict) else {}), **body['extras']}
            meta['extras'] = {k: v for k, v in rows.items() if k in here}
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
    if not path or cf.put_away(name):                # a private chat's clip while the vault is locked: the same answer as gone
        return {'error': 'That clip is gone.'}, 404
    media = cf.MEDIA_TYPES.get(path.suffix.lower())
    # a private chat's clip is never kept by the browser: a cached copy plays after the lock without asking
    caching = 'no-store' if Path(str(name)).stem.endswith(cf.PRIVATE_MARK) else 'private, max-age=86400'
    headers = {'Cache-Control': caching, 'X-Content-Type-Options': 'nosniff'}
    if not media:
        headers['Content-Disposition'] = f'attachment; filename="{path.name}"'
    return FileResponse(str(path), media_type=media or 'application/octet-stream', headers=headers)


# DELETE outputs
def clear_outputs(**_):
    deleted, left = cf.clear_saved()
    return {'deleted': deleted, 'left': left}


# -- the Apps page: a person runs a workflow, no model in between -----------------------------

JOBS, JOBS_LOCK, JOBS_KEPT = {}, threading.Lock(), 20      # id -> job; the last few stay for a page that comes back
RUN_FILES = 16
RUN_PICTURES = 8                                 # input pictures one run may bring


def _told(job):
    """A job as the page is told about it."""
    out = {k: job.get(k) for k in ('id', 'state', 'phase', 'workflow', 'seed', 'files', 'notes', 'error')}
    out['seconds'] = int((job.get('ended') or time.monotonic()) - job['started'])
    return out


def _work(job, graph, meta, prompt, shape, extras, pictures, wait, url):
    """The job's own thread: send the pictures, queue, wait, keep every file it made, say the
    truth about the shape. Whatever goes wrong ends the job in words."""
    comfy = job['comfy'] = cf.Comfy(url)

    def queued(pid):
        job['pid'] = pid
        job['phase'] = 'working'
        if job['stop']:                              # Stop was pressed before ComfyUI had the job
            comfy.cancel(pid)
    try:
        run, notes, shaped = cf.plan(comfy, graph, meta, job['workflow'], prompt, job['seed'], shape, extras, pictures)
        if job['stop']:
            raise cf.ComfyError("Stopped before it started.")
        outputs, fixed = comfy.run(run, wait, queued)
        items = cf.outputs_of(outputs)[:RUN_FILES]
        if not items:
            raise cf.ComfyError("ComfyUI finished but handed back no file. The workflow needs a Save Image or Save Video node.")
        files, paths = [], []
        for item in items:
            dest = cf.keep(job['workflow'], Path(item['filename']).suffix.lower())
            comfy.fetch(item, dest)
            paths.append(dest)
            files.append({'url': f'/api/plugin/comfyui/file/{dest.name}', 'name': dest.name, 'kind': item['kind']})
        said = cf.shape_note(*shaped, cf.measure(paths[0])) if shaped else None
        job.update(state='done', files=files, notes=notes + comfy.said(fixed) + ([said] if said else []))
    except cf.ComfyError as e:
        job.update(state='stopped' if job['stop'] else 'failed', error=str(e).replace(' Call again without shape.', ''))
    except Exception as e:
        logger.error(f"[COMFY] app run failed: {e}", exc_info=True)
        job.update(state='failed', error=f"Something went wrong inside the ComfyUI plugin ({type(e).__name__}). Its log has the detail.")
    finally:
        job['ended'] = time.monotonic()


def _start(f, pictures, settings):
    """Everything that can be refused without ComfyUI is refused here, at once. Then the job
    exists and the page is told so: sending the pictures is the job's first step, not
    something the page waits through (it would give up, and a job nobody can stop would run)."""
    settings = settings or {}
    try:
        name, path = cf.pick(f.get('workflow'), settings.get('default_workflow'))
        graph, meta = cf.read(path)
        prompt = str(f.get('prompt') or '').strip()
        if not prompt and cf.prompt_targets(graph, meta):
            raise cf.ComfyError("Type a prompt first.")
        shape = str(f.get('shape') or '').strip().lower() or None
        if shape and shape not in cf.SHAPES:
            raise cf.ComfyError(f"The shape is one of: {', '.join(cf.SHAPES)}.")
        try:
            seed = int(str(f.get('seed') or '').strip() or 0)
            extras = json.loads(f['extras']) if str(f.get('extras') or '').strip() else None
        except (ValueError, OverflowError, RecursionError):
            raise cf.ComfyError("The seed is a whole number, and the settings didn't arrive whole. Try again.") from None
        seed = seed if 0 < seed < 2 ** 63 else random.randint(1, 2 ** 31 - 1)
        as_set = copy.deepcopy(graph)             # the workflow as these settings leave it
        if extras:
            cf.set_extras(as_set, meta, extras)
        if shape and not cf.size_plan(as_set)[0]:
            raise cf.ComfyError(f"Workflow '{name}' makes its own shape, which can't be set from here.")
        room = len(cf.image_slots(as_set, meta))
        if pictures and not room:
            raise cf.ComfyError(f"Workflow '{name}' takes no input image" + (" the way it is set here." if cf.image_slots(graph, meta) else "."))
        if len(pictures) > room:
            raise cf.ComfyError(f"Workflow '{name}' takes {'one input image' if room == 1 else f'at most {room} input images'}.")
    except cf.ComfyError as e:
        return {'error': str(e)}, 400
    except Exception as e:
        logger.error(f"[COMFY] app run couldn't start: {e}", exc_info=True)
        return {'error': ODD}, 400
    video = cf.kind(graph) != 'image'
    try:
        wait = max(10.0, min(float(settings.get('video_timeout' if video else 'timeout')), 86400.0))
    except (TypeError, ValueError):
        wait = 1200.0 if video else 300.0
    job = {'id': uuid.uuid4().hex[:12], 'state': 'running', 'phase': 'sending', 'workflow': name, 'seed': seed,
           'started': time.monotonic(), 'pid': None, 'stop': False, 'comfy': None}
    with JOBS_LOCK:
        JOBS[job['id']] = job
        done = [k for k, j in JOBS.items() if j['state'] != 'running']
        for k in done[:max(0, len(JOBS) - JOBS_KEPT)]:
            del JOBS[k]
    threading.Thread(target=_work, args=(job, graph, meta, prompt, shape, extras, pictures, wait, settings.get('comfy_url')),
                     daemon=True, name='comfy-app-run').start()
    return _told(job)


# POST run   (multipart: workflow, prompt, shape?, seed?, extras? (JSON text), image? (a file, or several))
async def run(request=None, settings=None, **_):
    if request is None:
        return {'error': 'Nothing came with that.'}, 400
    try:
        if int(request.headers.get('content-length') or 0) > RUN_PICTURES * cf.MAX_UPLOAD + (1 << 21):
            return {'error': 'Those pictures are too big.'}, 413
    except ValueError:
        pass
    form = await request.form()
    pictures = []                                    # one, or several for a workflow with room for them, first slot first
    for file in (form.getlist('image') if hasattr(form, 'getlist') else [form.get('image')])[:RUN_PICTURES]:
        if file is not None and hasattr(file, 'read'):
            pictures.append(await file.read(cf.MAX_UPLOAD + 1))
            if len(pictures[-1]) > cf.MAX_UPLOAD:
                return {'error': 'That picture is too big.'}, 413
    fields = {k: form.get(k) for k in ('workflow', 'prompt', 'shape', 'seed', 'extras') if isinstance(form.get(k), str)}
    return await asyncio.to_thread(_start, fields, pictures, settings)     # reading a workflow file is work: off the event loop


# GET run/{job}
def run_state(job=None, **_):
    found = JOBS.get(str(job or ''))
    return _told(found) if found else ({'error': 'That run is no longer tracked: the plugin was reloaded, or Sapphire restarted.'}, 404)


# DELETE run/{job}
def run_stop(job=None, **_):
    found = JOBS.get(str(job or ''))
    if not found:
        return {'error': 'That run is gone.'}, 404
    found['stop'] = True
    if found['state'] == 'running' and found['pid'] and found['comfy']:
        found['comfy'].cancel(found['pid'])          # never raises: a ComfyUI that doesn't answer is logged
    return _told(found)


# GET outputs   (the newest things made, for the Apps page's strip)
def outputs(settings=None, **_):
    count, size = cf.saved()
    return {'files': [{**f, 'url': f"/api/plugin/comfyui/file/{f['name']}"} for f in cf.recent()],
            'count': count, 'bytes': size, 'backed_up': cf.backed_up(settings)}

