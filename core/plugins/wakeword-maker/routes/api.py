# routes/api.py - the page's doors, all under /api/plugin/wakeword-maker/. Light work only: files in, files out,
# jobs started and watched. Login, CSRF and the rate limit are the framework's.
import asyncio
import json
import re
import logging

import numpy as np
import math
from collections import Counter
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi.responses import FileResponse, PlainTextResponse, Response

PLUGIN_DIR = Path(__file__).absolute().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.append(str(PLUGIN_DIR))

from wakeword_maker import watch as _watch
from wakeword_maker.paths import read_json, write_json
from wakeword_maker import judge as _judge
from wakeword_maker import augment, catalog, holdout, jobs, paths, sampler_client, store, tags, voices   # noqa: E402

logger = logging.getLogger(__name__)
NAME = 'wakeword-maker'
_gpu = {}


# --- helpers -----------------------------------------------------------------------------------------

def _root(settings):
    root = paths.data_root(settings)
    if not root.is_dir() and paths.is_default(root):
        try:
            root.mkdir(parents=True, exist_ok=True)         # <sapphire>/wakeword: ours to make
        except OSError as e:
            return None, ({'error': f"could not make {root}: {e}", 'code': 'no_root'}, 409)
    if not root.is_dir():
        return None, ({'error': f"The data folder {root} does not exist. Pick another in Settings.", 'code': 'no_root'}, 409)
    return root, None


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')


def _safe(*ids):
    """Route ids that become path segments: letters, digits, _ . - only, never a dot name. No slash of either kind,
    no drive letter, nothing a resolve could carry out of the folder."""
    return all(isinstance(x, str) and _ID.fullmatch(x) and x not in ('.', '..') for x in ids)


def _project(root, slug):
    if not _safe(slug):
        return None, ({'error': 'bad wake word id'}, 400)
    pr = store.load_project(root, slug)
    if not pr:
        return None, ({'error': f"no project '{slug}'"}, 404)
    return pr, None


def _publish(event, data):
    try:
        from core.event_bus import publish
        publish(event, data, ephemeral=True)
    except Exception as e:
        logger.debug(f"[WWM] publish: {e}")


def _manifest():
    try:
        return json.loads((PLUGIN_DIR / 'plugin.json').read_text(encoding='utf-8'))
    except Exception:
        return {}


def _env():
    """{'state': built|missing|stale|building|error, 'python': path|None, ...}"""
    try:
        from core import plugin_envs
        spec = _manifest().get('environment') or {}
        state = plugin_envs.env_status(NAME, spec)
        py = plugin_envs.env_python(NAME)
        build = plugin_envs.build_state(NAME)
        return {'state': state, 'python': str(py) if py else None, 'build': build}
    except Exception as e:
        return {'state': 'unknown', 'python': None, 'error': str(e)}


def _python_for(kind):
    env = _env()
    if env.get('state') in ('ready', 'stale') and env.get('python') and Path(env['python']).exists():
        return env['python'], None             # stale = the pin list changed since the build; the env still runs jobs

    if kind in jobs.LIGHT:
        return sys.executable, None
    return None, ({'error': "The plugin's environment is not built yet. Build it from Settings.", 'code': 'no_env',
                   'env': env}, 409)


def _gpu_info():
    if 'at' in _gpu and _gpu.get('at', 0) > 0:
        return _gpu['info']
    info = None
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            name, total, used = [x.strip() for x in out.stdout.strip().splitlines()[0].split(',')]
            info = {'name': name, 'total_mb': int(float(total)), 'used_mb': int(float(used))}
    except Exception:
        pass
    _gpu['info'], _gpu['at'] = info, 1
    return info


def _disk(path):
    try:
        u = shutil.disk_usage(path)
        return {'free': u.free, 'total': u.total}
    except Exception:
        return None


# --- status and settings -----------------------------------------------------------------------------

def status(settings=None, **_):
    root, err = _root(settings)
    root = root or paths.data_root(settings)
    ok = bool(not err and os.access(root, os.W_OK))
    return {
        'data_dir': str(root),
        'default_dir': paths.is_default(root),
        'root_error': err[0].get('error') if err else None,
        'root_ok': ok,
        'disk': _disk(root) if ok else None,
        'env': _env(),
        'gpu': _gpu_info(),
        'projects': len(store.list_projects(root)) if ok else 0,
        'running': [j['id'] for j in jobs.running(root)] if ok else [],
        'collections': paths.COLLECTIONS,
    }


def check_dir(body=None, **_):
    """Before the user commits to a folder: does it exist, can we write, how much room, is it empty."""
    raw = str((body or {}).get('path') or '').strip()
    if not raw:
        return {'error': 'no path'}, 400
    p = paths.data_root({'data_dir': raw})               # the same reading the plugin gives the saved setting
    create = bool((body or {}).get('create'))
    if not p.exists() and create:
        try:
            p.mkdir(parents=True)
        except Exception as e:
            return {'ok': False, 'exists': False, 'error': f"could not create it: {e}"}
    if not p.exists():
        parent = p.parent
        return {'ok': False, 'exists': False, 'can_create': parent.is_dir() and os.access(parent, os.W_OK), 'path': str(p)}
    if not p.is_dir():
        return {'ok': False, 'exists': True, 'error': 'that is a file, not a folder', 'path': str(p)}
    writable = os.access(p, os.W_OK)
    try:
        entries = [e.name for e in os.scandir(p) if not e.name.startswith('.')]
    except Exception:
        entries = []
    ours = (p / 'projects').is_dir() or (p / 'datasets').is_dir()
    return {'ok': writable, 'exists': True, 'writable': writable, 'empty': not entries, 'ours': ours,
            'disk': _disk(p), 'path': str(p)}


def _du(path):
    total = 0
    try:
        for base, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(base, f))
                except OSError:
                    pass
    except Exception:
        pass
    return total


def storage(settings=None, **_):
    """Where everything goes and how much is there: the shared folder (voices, datasets, jobs) and each wake word."""
    root = paths.data_root(settings)
    if not root or not root.is_dir():
        return {'root': str(root) if root else '', 'ok': False}
    out = {'root': str(root), 'ok': True, 'disk': _disk(root),
           'voices': _du(paths.voices_dir(root)), 'datasets': _du(paths.datasets_dir(root)), 'jobs': _du(paths.jobs_dir(root)),
           'projects': []}
    for pr in store.list_projects(root):
        out['projects'].append({'slug': pr['slug'], 'phrase': pr['phrase'], 'folder': pr['folder'], 'bytes': _du(pr['folder']),
                                'default': pr['folder'] == str(paths.projects_dir(root) / pr['slug'])})
    out['default_projects_dir'] = str(paths.projects_dir(root))
    return out


# --- datasets --------------------------------------------------------------------------------------------

def datasets(settings=None, **_):
    root = paths.data_root(settings)
    return {'datasets': catalog.status(root if root and root.is_dir() else None), 'disk': _disk(root) if root else None}


def download_dataset(did, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    if not catalog.by_id(did):
        return {'error': f"no such dataset {did}"}, 404
    for j in jobs.running(root):
        if j['kind'] == 'download' and j['args'].get('dataset') == did:
            return {'job': j, 'already': True}
    py, err = _python_for('download')
    if err:
        return err
    return {'job': jobs.start(root, 'download', {'dataset': did}, py, PLUGIN_DIR, publish=_publish)}


def delete_dataset(did, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    if not catalog.by_id(did):
        return {'error': f"no such dataset {did}"}, 404
    py, _e = _python_for('delete_dataset')
    return {'job': jobs.start(root, 'delete_dataset', {'dataset': did}, py, PLUGIN_DIR, publish=_publish)}


# --- projects ------------------------------------------------------------------------------------------

SYNTH_ENOUGH = 2000      # the quick set: past this the Voices step counts as done on its own
RECORDED_ENOUGH = 240    # your own voice, the floor worth training on: three mics, eighty takes each; a quarter is held back to judge
STEPS = [('voices', 'Voices'), ('record', 'Record'), ('check', 'Check'), ('train', 'Train'), ('test', 'Test'), ('install', 'Install')]


def _progress(root, pr):
    """The wizard's view of a project: which steps are done (by the user's Done, or on their own when the work is
    plainly there), the first that is not, and a percentage for the card."""
    c = pr.get('counts') or {}
    synth = c.get('positive/synth', 0)
    rec = c.get('positive/recorded', 0) + c.get('positive/uploaded', 0)
    rated = (paths.project_dir(root, pr['slug']) / 'qa' / 'summary.json').is_file()
    marked = pr.get('steps') or {}
    trained = (paths.project_dir(root, pr['slug']) / 'runs').is_dir() and any((d / 'run.json').is_file() and (d / 'model.onnx').exists() or (d / 'final' / 'model.onnx').exists() or (d / 'model.tflite').exists()
                                                                              for d in (paths.project_dir(root, pr['slug']) / 'runs').iterdir())
    try:
        listening = _desktop_state(pr['slug'])['ours']          # our file, under our name, and the one core would load
    except Exception:
        listening = False
    auto = {'voices': synth >= SYNTH_ENOUGH, 'record': rec >= RECORDED_ENOUGH, 'check': rated and synth > 0, 'train': trained, 'install': listening}
    done = {k: bool(marked.get(k) or auto.get(k)) for k, _ in STEPS}
    pending = next((k for k, _ in STEPS if not done[k]), None)
    labels = dict(STEPS)
    hints = {'voices': 'Make the set' if not synth else f"Make the set ({synth:,} made)",
             'record': f"Record yourself ({rec} of {RECORDED_ENOUGH})", 'check': 'Check the clips',
             'train': 'Train a model', 'test': 'Test the models', 'install': 'Install on your devices'}
    n_done = sum(done.values())
    return {'pct': int(round(10 + 90 * n_done / len(STEPS))), 'done': done, 'step': pending,
            'next': hints.get(pending, 'All done') if pending else 'All done', 'tab': pending or 'install',
            'label': labels.get(pending, ''), 'index': ([k for k, _ in STEPS].index(pending) + 1) if pending else len(STEPS),
            'of': len(STEPS), 'auto': auto, 'synth': synth, 'recorded': rec, 'recorded_floor': RECORDED_ENOUGH, 'rated': rated}


def list_projects(settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    out = store.list_projects(root)
    for pr in out:
        pr['progress'] = _progress(root, pr)
    return {'projects': out}


def create_project(body=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    try:
        pr = store.create_project(root, (body or {}).get('phrase'), (body or {}).get('folder'))
    except ValueError as e:
        return {'error': str(e)}, 400
    except OSError as e:
        return {'error': f"could not make that folder: {e}"}, 400
    pr['counts'] = store.counts(paths.project_dir(root, pr['slug']))
    pr['progress'] = _progress(root, pr)
    return {'project': pr}


def get_project(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pr['counts'] = store.counts(paths.project_dir(root, slug))
    pr['progress'] = _progress(root, pr)
    pr['jobs'] = jobs.list_all(root, project=slug, limit=20)
    return {'project': pr}


def update_project(slug, body=None, settings=None, **_):
    """Fields the page may change: spellings, negatives (the user's own sound-alike phrases), settings (voices, counts...)."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    body = body or {}
    for k in ('spellings', 'negatives', 'negatives_auto'):
        if k in body and isinstance(body[k], list):
            pr[k] = [' '.join(str(x).split()) for x in body[k] if str(x).strip()]
    if isinstance(body.get('settings'), dict):
        pr.setdefault('settings', {}).update(body['settings'])
    if body.get('folder') is not None:
        want = str(body['folder']).strip()
        if not want:
            return {'error': 'folder?'}, 400
        if jobs.running(root) and any(j.get('project') == slug for j in jobs.running(root)):
            return {'error': 'a job is running on this wake word; move it when the job is done'}, 409
        try:
            src = Path(pr['folder'])
            dst = Path(want).expanduser()
            same_fs = dst.exists() and os.stat(dst).st_dev == os.stat(src).st_dev or (not dst.exists() and dst.parent.exists() and os.stat(dst.parent).st_dev == os.stat(src).st_dev)
            if not same_fs and sum(1 for _ in src.rglob('*.wav')) > 2000:
                return {'error': 'that is another drive and the set is large; moving across drives as a job comes with the next wave. Pick a folder on the same drive for now.'}, 409
            store.move_project(root, slug, dst)
            pr = store.load_project(root, slug)
        except ValueError as e:
            return {'error': str(e)}, 400
        except OSError as e:
            return {'error': f"could not move it: {e}"}, 400
    if isinstance(body.get('steps'), dict):       # {'voices': true|false, ...}: the user's Done, or undo
        steps = pr.setdefault('steps', {})
        for k, v in body['steps'].items():
            if k in dict(STEPS):
                if v:
                    steps[k] = time.time()
                else:
                    steps.pop(k, None)
    store.save_project(paths.project_dir(root, slug), pr)
    pr['counts'] = store.counts(paths.project_dir(root, slug))
    pr['progress'] = _progress(root, pr)
    return {'project': pr}


def delete_project(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    busy = [j for j in jobs.list_all(root, project=slug, limit=0) if j['state'] in ('running', 'queued')]
    if busy:
        return {'error': f"{len(busy)} job(s) still run or wait for this wake word; cancel them first", 'code': 'busy'}, 409
    _watch.stop(paths.project_dir(root, slug), slug)
    try:
        return {'deleted': store.delete_project(root, slug)}
    except ValueError as e:
        return {'error': str(e)}, 400


# --- clips ---------------------------------------------------------------------------------------------

def _collection(kind, src):
    c = f"{kind}/{src}"
    return c if c in paths.COLLECTIONS else None


def list_clips(slug, query=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    q = query or {}
    c = q.get('collection')
    if c not in paths.COLLECTIONS:
        return {'error': f"collection? one of {', '.join(paths.COLLECTIONS)}"}, 400
    try:
        offset, limit = int(q.get('offset', 0)), min(200, int(q.get('limit', 50)))
    except ValueError:
        return {'error': 'offset and limit are numbers'}, 400
    rows, total = store.list_clips(paths.project_dir(root, slug), c, offset, limit)
    for r in rows:
        r['flags'] = store.clip_flags(r)
    return {'clips': rows, 'total': total, 'collection': c}


def clip_audio(slug, kind, src, cid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    c = _collection(kind, src)
    if not c and (kind, src) != ('ambient', 'library'):
        return {'error': 'no such collection'}, 404
    try:
        wav = store.room_paths(root, cid)[0] if not c else store.clip_paths(paths.project_dir(root, slug), c, cid)[0]
    except ValueError:
        return {'error': 'bad clip id'}, 400
    if not wav.is_file():
        return {'error': 'no such clip'}, 404
    return FileResponse(str(wav), media_type='audio/wav', filename=f"{cid}.wav")


def delete_clip(slug, kind, src, cid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    c = _collection(kind, src)
    if not c and (kind, src) == ('ambient', 'library'):          # a piece of the shared room library
        try:
            return {'deleted': store.delete_room(root, cid=cid) > 0}
        except ValueError:
            return {'error': 'bad piece id'}, 400
    if not c:
        return {'error': 'no such collection'}, 404
    try:
        _forget(paths.project_dir(root, slug) / c)
        return {'deleted': store.delete_clip(paths.project_dir(root, slug), c, cid)}
    except ValueError:
        return {'error': 'bad clip id'}, 400


def tag_clip(slug, kind, src, cid, body=None, settings=None, **_):
    """The user's verdict on a clip from the Check tab: keep / drop, a note."""
    root, err = _root(settings)
    if err:
        return err
    c = _collection(kind, src)
    if not c and (kind, src) == ('ambient', 'library'):          # a piece of the shared room library: verdict or note
        fields = {k2: body[k2] for k2 in ('verdict', 'note') if k2 in (body or {})}
        try:
            return {'clip': store.update_room(root, cid, **fields)}
        except ValueError:
            return {'error': 'bad piece id'}, 400
    if not c:
        return {'error': 'no such collection'}, 404
    body = body or {}
    fields = {k: body[k] for k in ('verdict', 'note', 'device', 'text', 'near') if k in body}
    fields.update(tags.clean(body))
    if 'note' in fields:
        fields['note'] = str(fields['note'] or '')[:200]
    if 'verdict' in fields:
        fields['verdict_by'] = 'user'
    if 'judge' in body:
        fields['judge'] = body['judge'] not in (False, 'false', 0, '0', '', None)
    pdir = paths.project_dir(root, slug)
    try:
        _forget(pdir / c)
        side = store.update_side(pdir, c, cid, **fields)
    except ValueError:
        return {'error': 'bad clip id'}, 400
    if 'judge' in fields:                                       # for the judge: in the slice now, out of training
        part = holdout.part_of(c, bool(side.get('near')))
        if part:
            holdout.earmark(pdir, part, cid, on=fields['judge'])
    return {'clip': side}


def _dropped(pdir):
    for c in paths.COLLECTIONS:
        if c.startswith(('ambient/', 'previews/')):
            continue
        for r in _rows(pdir / c):
            if r.get('verdict') == 'drop':
                yield c, r


def dropped_report(slug, settings=None, **_):
    """The dropped clips by reason: how many of each, so a whole reason can be deleted or kept back at once."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    by = Counter()
    total = 0
    for c, r in _dropped(pdir):
        total += 1
        reasons = (r.get('qa') or {}).get('reasons') or ['no reason recorded']
        by[reasons[0]] += 1                   # the first reason is the one that cost the most
    return {'total': total, 'reasons': [{'reason': k, 'n': n} for k, n in by.most_common()]}


def delete_dropped(slug, body=None, settings=None, **_):
    """Remove from disk the dropped clips: all of them, or those whose first reason matches `reason`. With
    `keep: true` the clips are kept back (verdict keep, as if you pressed keep on each) instead of deleted."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    body = body or {}
    reason = body.get('reason')
    keep = bool(body.get('keep'))
    pdir = paths.project_dir(root, slug)
    n = 0
    touched = set()
    for c, r in list(_dropped(pdir)):
        reasons = (r.get('qa') or {}).get('reasons') or ['no reason recorded']
        if reason and reasons[0] != reason:
            continue
        if keep:
            store.update_side(pdir, c, r['id'], verdict='keep', verdict_by='user')
        else:
            store.delete_clip(pdir, c, r['id'])
        touched.add(c)
        n += 1
    for c in touched:
        _forget(pdir / c)
    return {'kept' if keep else 'deleted': n, 'counts': store.counts(pdir)}


async def record(slug, request=None, settings=None, **_):
    """Samples from the browser recorder: multipart with one or more `audio` parts (wav at any rate) and the tags.
    Several takes ride one request when the user is quick, which keeps the plugin under the per-minute limit.
    The reply carries counts and the guide so the page needs no further calls."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    form = await request.form()
    takes = [await v.read() for k, v in form.multi_items() if k == 'audio' and hasattr(v, 'read')]
    if not takes:
        return {'error': 'no audio part'}, 400
    fields = {k: str(v) for k, v in form.multi_items() if not hasattr(v, 'read')}
    return await asyncio.to_thread(_record, root, slug, fields, takes, settings)   # decode, trim and VAD off the event loop


def _record(root, slug, form, takes, settings):
    c = str(form.get('collection') or 'positive/recorded')
    if c not in paths.COLLECTIONS or c.split('/')[1] != 'recorded':
        return {'error': 'recordings go to a recorded collection'}, 400
    meta = {k: str(form.get(k))[:200] for k in ('device', 'text', 'note') if form.get(k)}
    meta.update({k: v for k, v in tags.clean(form).items() if v})
    meta['via'] = 'browser'
    if form.get('near'):
        meta['near'] = True                       # a sound-alike in the user's own voice: the hardest negative
    if form.get('judge') and form.get('judge') not in ('0', 'false'):
        meta['judge'] = True                      # recorded for the judge: held out from the start, never trained on
    texts = str(form.get('texts') or '').split('\n') if form.get('texts') else []
    heads = str(form.get('drop_head_ms') or '0').split(',')
    tails = str(form.get('drop_tail_ms') or '0').split(',')
    pdir = paths.project_dir(root, slug)
    clips, refused = [], []
    for i, data in enumerate(takes):
        try:
            head = int(heads[i] if i < len(heads) else heads[-1] or 0)
            tail = int(tails[i] if i < len(tails) else tails[-1] or 0)
        except ValueError:
            head = tail = 0
        m = dict(meta)
        if i < len(texts) and texts[i].strip():
            m['text'] = texts[i].strip()
        try:
            clips.append(store.take_recording(pdir, c, data, m, do_trim=form.get('trim', '1') != '0',
                                              drop_head_ms=head, drop_tail_ms=tail))
        except ValueError as e:
            refused.append(str(e))
        except Exception as e:
            refused.append(f"could not read that audio: {e}")
    for cl in clips:
        cl['flags'] = store.clip_flags(cl)
        if cl.get('judge'):
            holdout.earmark(pdir, holdout.part_of(c, bool(cl.get('near'))), cl['id'])
    out = {'clips': clips, 'refused': refused, 'counts': store.counts(pdir)}
    if clips:
        out['clip'] = clips[-1]
    try:
        out['guide'] = guide(slug, settings=settings)
    except Exception:
        pass
    if not clips:
        return {**out, 'error': refused[0] if refused else 'nothing kept'}, 422
    return out


def near_misses(slug, settings=None, **_):
    """What to read for near misses: the user's own list, the found sound-alikes, and plain swaps of the phrase."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    from wakeword_maker import negatives
    words = pr['phrase'].split()
    plain = []
    if len(words) > 1:
        plain += [' '.join([sw] + words[1:]) for sw in negatives.SWAPS]
        plain += [words[-1], ' '.join(words[:-1])]
    found = [' '.join(str(t).split()) for t in pr.get('negatives_auto') or []]
    close = [t for t in found if any(w in t.split() for w in words)]      # "marcus heyne" yes, "carcinogen" no
    rhymes = []
    try:
        import pronouncing
        for w in words[-1:]:                                              # names that start like the last word: martin, marker, marquis
            phones = pronouncing.phones_for_word(w)
            if phones:
                head = ' '.join(phones[0].split()[:3])
                cands = [x for x in pronouncing.search('^' + head) if x.isalpha() and x != w and 3 <= len(x) <= 9][:12]
                rhymes += [' '.join(words[:-1] + [x]) for x in cands]
    except Exception:
        pass
    seen, out = set(), []
    for t in (pr.get('negatives') or []) + plain + rhymes + close + found:
        t = ' '.join(str(t).split())
        if t and t not in seen and t != pr['phrase']:
            seen.add(t)
            out.append(t)
    return {'phrases': out[:60]}


async def record_test(slug, request=None, settings=None, **_):
    """A second of silence from a mic: how noisy is it? Nothing is kept."""
    root, err = _root(settings)
    if err:
        return err
    device, data = '', None
    if 'json' in (request.headers.get('content-type') or ''):
        device = str(((await request.json()) or {}).get('device') or '')          # a satellite's own mic
        if not device:
            return {'error': 'device?'}, 400
    else:
        form = await request.form()
        up = form.get('audio')
        if up is None:
            return {'error': 'no audio part'}, 400
        data = await up.read()

    def work():
        nonlocal data
        try:
            if device:
                data, _ = _listen(device, 2, vad=False)
            audio, sr = store.decode(data)
            audio = store.to_16k(audio, sr)
        except Exception as e:
            return {'error': f"could not read that audio: {getattr(e, 'args', [e])[0]}"}, 422
        return store.mic_report(audio)
    return await asyncio.to_thread(work)


# (floor, good). The good column is what made the first clean models (2026-10-05: 223 takes over 4 mics and 10 ways of
# speaking, 219 near misses, 175 other words, 48 minutes of room, held-out 25%); the floor is about half of that.
TARGETS = {'positive': (240, 440), 'negative': (80, 320), 'near': (120, 400), 'ambient_minutes': (30, 90)}   # floor, good: twice what made the first clean models, so a quarter can be held back to judge


def guide(slug, settings=None, **_):
    """The Record tab's helper: targets with progress, and quick checks over what has been recorded."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    own = {'positive': [], 'negative': [], 'ambient': []}
    for kind in own:
        for src in ('recorded', 'uploaded'):
            rows, _ = store.list_clips(pdir, f"{kind}/{src}", 0, 100000)
            own[kind] += rows
    pos, neg, amb = own['positive'], own['negative'], own['ambient']
    amb = amb + store.list_room(root, slug=slug)                 # the shared room library counts for every wake word
    amb_min = sum(float(c.get('seconds') or 0) for c in amb) / 60.0
    near = [c for c in neg if c.get('near')]
    progress = {
        'positive': {'have': len(pos), 'min': TARGETS['positive'][0], 'good': TARGETS['positive'][1]},
        'near': {'have': len(near), 'min': TARGETS['near'][0], 'good': TARGETS['near'][1]},
        'negative': {'have': len(neg) - len(near), 'min': TARGETS['negative'][0], 'good': TARGETS['negative'][1]},
        'ambient': {'have': round(amb_min, 1), 'min': TARGETS['ambient_minutes'][0], 'good': TARGETS['ambient_minutes'][1], 'unit': 'min'},
    }
    checks = []     # level: bad = fix before training, warn = should fix, info = would make it better
    def add(level, text):
        checks.append({'level': level, 'ok': level == 'ok', 'text': text})
    by_dev = {}
    for c in pos + neg:
        d = c.get('device') or 'unknown'
        by_dev.setdefault(d, []).append(c)
    for d, cs in sorted(by_dev.items()):
        floors = [c['floor_db'] for c in cs if isinstance(c.get('floor_db'), (int, float))]
        fl = [store.clip_flags(c) for c in cs]
        clipped = sum(1 for f in fl if 'clipped' in f)
        quiet = sum(1 for f in fl if 'faint' in f)
        hissy = sum(1 for f in fl if 'noisy' in f)
        med = sorted(floors)[len(floors) // 2] if floors else None
        if med is not None and med > -28:
            add('warn', f"{d}: noise floor {med:.0f} dB, very noisy. Lower its input volume, or keep it for Room sound.")
        elif med is not None and med > -38:
            add('warn', f"{d}: noise floor {med:.0f} dB, hiss under every clip. Lower its input volume a little.")
        elif med is not None:
            add('ok', f"{d}: clean floor ({med:.0f} dB), {len(cs)} clips.")
        if clipped:
            add('bad', f"{d}: {clipped} clipped clip(s). Marked in the list: delete them, back off the mic or lower its volume.")
        if quiet:
            add('warn', f"{d}: {quiet} faint clip(s), under -30 dB at the loudest. Marked in the list; delete or record again closer.")
        if hissy:
            add('warn', f"{d}: {hissy} clip(s) with the voice less than 20 dB above the mic's noise. Marked in the list.")
    if pos:
        mics = len(set(c.get('device') or '?' for c in pos))
        add('ok' if mics >= 3 else 'info', f"{mics} microphone(s) for the phrase." + ('' if mics >= 3 else ' Three or more teach the phrase, not the mic: a satellite counts, so does a phone or a webcam.'))
        tg = [tags.of(c) for c in pos]
        hows = sorted({t['how'] for t in tg})
        missing = [x for x in ('fast', 'soft', 'loud', 'whisper') if x not in hows]
        add('ok' if len(hows) >= 4 else 'info', f"{len(hows)} way(s) of speaking." + (f" Try: {', '.join(missing[:3])}." if missing else ''))
        far = sum(1 for t in tg if t['where'] != 'close')
        add('ok' if far >= 10 else 'info', f"{far} take(s) from across the room or the next room." + ('' if far >= 10 else ' Ten or more per mic: that is how you really call her.'))
        noisy = sum(1 for t in tg if t['noise'] != 'quiet')
        add('ok' if noisy >= 10 else 'warn', f"{noisy} take(s) with the TV or music on." + ('' if noisy >= 10 else ' Ten or more: they are the only judges of a loud room, and the ones the first model fails.'))
        judge_n = sum(1 for c in pos if c.get('judge'))
        if judge_n:
            add('ok', f"{judge_n} take(s) recorded for the judge: held out from the start, never trained on.")
        odd = sum(1 for c in pos if set(store.clip_flags(c)) & {'short', 'long'})
        if odd:
            add('warn', f"{odd} phrase clip(s) under half a second or over 2.5 s, marked in the list: listen and delete the broken ones.")
        people = sorted({t['who'] for t in tg if t['who']}) or pr.get('settings', {}).get('people')
        add('ok' if people else 'info', 'Someone else saying it is worth more than fifty more of yours: tag them under Who.' if not people
            else (f"{len(people)} people recorded: {', '.join(people[:6])}." if isinstance(people, list) else f"{people} people recorded."))
    if not near:
        add('warn', 'No near misses yet: words that sound like the phrase, in your voice, are the hardest negatives. Pick Near miss and read the prompts.')
    plain = len(neg) - len(near)
    if plain == 0:
        add('warn', 'No ordinary negatives yet: other things you say near her, in your voice.')
    else:
        nstyles = {tags.of(c)['how'] for c in neg}
        if 'whisper' not in nstyles or 'loud' not in nstyles:
            add('info', 'Negatives in a whisper and at a shout too: she should ignore you at every volume.')
    if amb_min < TARGETS['ambient_minutes'][0]:
        add('warn', f"Room sound: {amb_min:.1f} min. Let it run through TV, dishes, music; this is how false alarms get counted.")
    elif len(amb) < 3:
        add('info', 'Room sound from more moments (TV on, TV off, washer, late night) beats one long take.')
    return {'progress': progress, 'checks': checks}


UPLOAD_MAX = 512 * 2 ** 20     # bytes per request: an hour of 48 kHz stereo wav is ~690 MB, so ambient comes in pieces


async def upload(slug, request=None, settings=None, **_):
    """Files into a collection, earmarked uploaded for good. Many files or a zip in one request."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    form = await request.form()
    c = str(form.get('collection') or '')
    if c not in paths.COLLECTIONS or c.split('/')[1] != 'uploaded':
        return {'error': 'uploads go to an uploaded collection'}, 400
    files = [(val.filename, await val.read()) for key, val in form.multi_items() if key == 'files' and hasattr(val, 'read')]
    if sum(len(d) for _, d in files) > UPLOAD_MAX:
        return {'error': f"that is over {UPLOAD_MAX // 2 ** 20} MB in one go; upload in smaller batches"}, 413

    def work():
        added, skipped = [], []
        for name, data in files:
            a, s = store.take_upload(paths.project_dir(root, slug), c, name, data)
            added += a
            skipped += s
        return {'added': len(added), 'skipped': [{'name': n, 'why': w} for n, w in skipped],
                'counts': store.counts(paths.project_dir(root, slug))}
    return await asyncio.to_thread(work)


# --- browsing: an index of sidecars per collection, cached by folder mtime and count ----------------------------

_index = {}        # str(dir) -> (mtime_ns, count, when, rows)
INDEX_TTL = 20     # seconds: sidecars edited in place (notes, ratings) do not touch the folder's mtime


def _rows(cdir):
    cdir = Path(cdir)
    if not cdir.is_dir():
        return []
    try:
        st = cdir.stat()
    except OSError:
        return []
    names = [e.name for e in os.scandir(cdir) if e.name.endswith('.json')]
    key = str(cdir)
    hit = _index.get(key)
    now = time.time()
    if hit and hit[0] == st.st_mtime_ns and hit[1] == len(names) and now - hit[2] < INDEX_TTL:
        return hit[3]
    rows = []
    for n in names:
        side = store.read_side(cdir / n)
        if side:
            side['flags'] = store.clip_flags(side)
            rows.append(side)
    rows.sort(key=lambda r: r.get('id', ''), reverse=True)
    _index[key] = (st.st_mtime_ns, len(names), now, rows)
    return rows


def _forget(cdir):
    _index.pop(str(cdir), None)


KINDS = {   # tab -> (collections, keep)
    'positive': (('positive/synth', 'positive/recorded', 'positive/uploaded'), lambda r: True),
    'near': (('negative/synth', 'negative/recorded', 'negative/uploaded'), lambda r: r.get('near') or r.get('source') == 'synth'),
    'negative': (('negative/recorded', 'negative/uploaded', 'negative/mined'), lambda r: not r.get('near')),
    'ambient': (('ambient/recorded', 'ambient/uploaded'), lambda r: True),
}


def browse(slug, query=None, settings=None, **_):
    """One tab of the clip browser: filtered, grouped (near misses by phrase), paged, with the facets to filter by."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    q = query or {}
    kind = q.get('kind', 'positive')
    if kind not in KINDS:
        return {'error': 'kind?'}, 400
    pdir = paths.project_dir(root, slug)
    cols, keep = KINDS[kind]
    held = holdout.ids(pdir)
    rows = [({**r, 'held': True} if r['id'] in held else r) for c in cols for r in _rows(pdir / c) if keep(r)]
    if kind == 'ambient':
        rows += [{**r, 'collection': 'ambient/library', 'library': True, 'flags': store.clip_flags(r), **({'held': True} if r['id'] in held else {})}
                 for r in store.list_room(root, slug=slug)]
        rows.sort(key=lambda r: r.get('id', ''), reverse=True)
    facets = {'device': Counter(r.get('device') or 'unknown' for r in rows), 'source': Counter(r.get('source') or '?' for r in rows)}
    for k, v in (('device', q.get('device')), ('source', q.get('source'))):
        if v:
            rows = [r for r in rows if (r.get(k) or ('unknown' if k == 'device' else '?')) == v]
    judge_n = 0
    if kind != 'ambient':
        own = [r for r in rows if r.get('source') != 'synth']
        for ax in tags.DEFAULT:
            facets[ax] = Counter(tags.of(r)[ax] or '(nobody tagged)' for r in own)
        judge_n = sum(1 for r in own if r.get('judge'))
        if q.get('judge'):
            rows = [r for r in rows if r.get('judge')]
        for ax in tags.DEFAULT:
            if q.get(ax):
                rows = [r for r in rows if (tags.of(r)[ax] or '(nobody tagged)') == q[ax]]
    if q.get('flag'):
        rows = [r for r in rows if q['flag'] in (r.get('flags') or [])]
    groups = None
    if kind == 'near':
        g = Counter((r.get('text') or '(no phrase)') for r in rows)
        groups = [{'text': t, 'n': n} for t, n in g.most_common()]
        if q.get('text'):
            rows = [r for r in rows if (r.get('text') or '(no phrase)') == q['text']]
    try:
        offset, limit = max(0, int(q.get('offset', 0))), max(1, min(100, int(q.get('limit', 24))))
    except ValueError:
        offset, limit = 0, 24
    page = rows[offset: offset + limit]
    return {'kind': kind, 'total': len(rows), 'offset': offset, 'limit': limit, 'clips': page, 'judge': judge_n,
            'facets': {k: dict(v.most_common(30)) for k, v in facets.items()}, 'groups': groups}


# --- satellites as microphones ----------------------------------------------------------------------------

def mics(**_):
    """Satellites with a microphone, for the Record tab."""
    out = []
    try:
        from core.devices import engine
        for d in engine.fleet():
            # a cap reads "mic", "mic (locked)" (she may not; the user may) or "mic (driver off)"
            caps = {c.partition(' ')[0]: c.partition(' ')[2] for c in (d.get('caps') or [])}
            if 'mic' in caps and caps['mic'] != '(driver off)' and not d.get('every'):
                h = _health_of(d['id'])
                out.append({'id': d['id'], 'location': d.get('location', ''), 'online': d.get('online'), 'format': _wake_format(d['id'], h), 'capture': _pushes(h)})
    except Exception as e:
        logger.debug(f"[WWM] mics: {e}")
    return {'mics': out}


def mic_settings(device, body=None, query=None, settings=None, **_):
    """Read (GET) or set (POST {gain, agc}) a satellite's microphone gain and AGC, through the device engine."""
    try:
        from core.devices import engine
    except Exception as e:
        return {'error': str(e)}, 500
    out = {}
    try:
        if body and body.get('gain') is not None:
            told, ok = engine.run(device, 'mic', 'gain', str(body['gain']), owner=True)
            if not ok:
                return {'error': told}, 400
            out['told'] = told
        if body and body.get('agc') is not None:
            told, ok = engine.run(device, 'mic', 'agc', 'on' if body['agc'] in (True, 'on', 1, '1') else 'off', owner=True)
            if not ok:
                return {'error': told}, 400
            out['told'] = told
        told, ok = engine.run(device, 'mic', 'gain', '', owner=True)
    except Exception as e:
        return {'error': str(e)}, 502
    import re as _re
    m = _re.search(r'([0-9.]+) dB of ([0-9.]+); AGC (on|off)', told or '')
    if not ok or not m:
        return {**out, 'supported': False, 'told': told}
    return {**out, 'supported': True, 'gain_db': float(m.group(1)), 'gain_max_db': float(m.group(2)), 'agc': m.group(3) == 'on'}


def _board_parts(device):
    """(row, config, secrets) of a satellite, what the driver's doors take."""
    from core.devices import engine
    row = engine._usable(device)
    part = engine._part(row, 'satellite')
    if not part:
        raise ValueError(f"{device} is not a satellite")
    return row, dict(part.get('config') or {}), engine._part_secrets(row['id'], 'satellite')


def _health_of(device):
    """What a satellite says about itself (the driver keeps it a minute); {} when it cannot be asked."""
    try:
        from core.devices.drivers import satellite
        row, cfg, secrets = _board_parts(device)
        return satellite._health(row, cfg, secrets) or {}
    except Exception:
        return {}


def _pushes(h):
    """A board with the capture door (an ESP32 from firmware 0.7.6) pushes its room in pieces (`capture` in its health)."""
    return isinstance((h or {}).get('capture'), dict)


def _wake_format(device, h=None):
    """Which model family a satellite runs: 'tflite' (microWakeWord, an ESP32) or 'onnx' (openWakeWord, a Pi). The
    driver's reading (satellite.wake_format): the board's health (`wakeword.format`), else its device row's hints,
    else its board and device names; a Pi is the default."""
    from core.devices.drivers import satellite
    h = _health_of(device) if h is None else h
    row = None
    try:
        from core.devices import engine
        row = engine.get(device)
    except Exception:
        pass
    return satellite.wake_format(device, h or None, row)


def _listen(device, seconds, vad=True):
    """A satellite's own ears for `seconds`: the WAV bytes and the device id. vad=True ends at the end of speech."""
    from core.devices import engine
    from core.devices.drivers import satellite
    row = engine._usable(device)
    part = engine._part(row, 'satellite')
    if not part:
        raise ValueError(f"{device} is not a satellite")
    secrets = engine._part_secrets(row['id'], 'satellite')
    v = 'true' if vad else 'false'
    r = satellite._call('GET', f'/audio/listen?vad={v}&seconds={seconds}&max_seconds={seconds}&lead_in=0&tone=false',
                        dict(part.get('config') or {}), secrets, timeout=seconds + 20)
    return r.content, row['id']


def record_satellite(slug, body=None, settings=None, **_):
    """Ask a satellite to listen and keep what it heard as a sample. Its own mic, its own echo cancelling:
    the audio the model will meet in service."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    body = body or {}
    device = str(body.get('device') or '')
    c = str(body.get('collection') or 'positive/recorded')
    if c not in paths.COLLECTIONS or c.split('/')[1] != 'recorded':
        return {'error': 'recordings go to a recorded collection'}, 400
    try:
        seconds = max(2, min(600, int(body.get('seconds', 6))))
    except ValueError:
        seconds = 6
    if not c.startswith('ambient/'):
        seconds = min(seconds, 60)
    tg = tags.clean(body)
    loud = tg.get('noise', 'quiet') != 'quiet'            # VAD never finds the end of speech over a TV: fixed window instead
    if loud and not c.startswith('ambient/'):
        seconds = 4
    try:
        data, dev_id = _listen(device, seconds, vad=not c.startswith('ambient/') and not loud)
    except Exception as e:
        return {'error': f"{device}: {getattr(e, 'args', [e])[0]}"}, 502
    meta = {'device': dev_id, 'via': 'satellite', **{k: v for k, v in tg.items() if v}}
    for k in ('text', 'note'):
        if body.get(k):
            meta[k] = str(body[k])[:200]
    if body.get('near'):
        meta['near'] = True
    if body.get('judge') not in (None, False, 'false', 0, '0', ''):
        meta['judge'] = True
    pdir = paths.project_dir(root, slug)
    try:
        side = store.take_recording(pdir, c, data, meta, do_trim=body.get('trim', True) not in (False, 'false', 0))
    except ValueError as e:
        return {'error': str(e)}, 422
    if side.get('judge'):
        holdout.earmark(pdir, holdout.part_of(c, bool(side.get('near'))), side['id'])
    return {'clip': side, 'counts': store.counts(pdir)}


def qa_report(slug, settings=None, **_):
    """What the last Check job found: qa/summary.json and the lowest-scoring clips."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    qdir = paths.project_dir(root, slug) / 'qa'
    out = {'summary': None, 'worst': []}
    for key, name in (('summary', 'summary.json'), ('worst', 'worst.json')):
        try:
            out[key] = json.loads((qdir / name).read_text(encoding='utf-8'))
        except Exception:
            pass
    return out


def _hist(values, lo, hi, bins):
    """Counts per bin over [lo, hi]; values outside land in the end bins."""
    counts = [0] * bins
    if not values:
        return counts
    w = (hi - lo) / bins
    for v in values:
        i = int((v - lo) / w) if w else 0
        counts[max(0, min(bins - 1, i))] += 1
    return counts


def check_report(slug, settings=None, **_):
    """The Check tab: what the project holds, what is red, what would make it better, and the numbers behind the charts.
    Works before any rating (from the sidecars) and gets richer after one."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    rows = {c: _rows(pdir / c) for c in paths.COLLECTIONS if not c.startswith('previews/')}
    pos = [r for c, rs in rows.items() if c.startswith('positive/') for r in rs]
    neg = [r for c, rs in rows.items() if c.startswith('negative/') for r in rs]
    amb = [r for c, rs in rows.items() if c.startswith('ambient/') for r in rs]
    near = [r for r in neg if r.get('near') or r.get('source') == 'synth']
    own_pos = [r for r in pos if r.get('source') in ('recorded', 'uploaded')]
    own_neg = [r for r in neg if r.get('source') in ('recorded', 'uploaded')]
    rated = [r for r in pos + neg if isinstance((r.get('qa') or {}).get('score'), (int, float))]
    dropped = [r for r in pos + neg if r.get('verdict') == 'drop']
    db = lambda r: 20 * math.log10(max(float(r.get('peak') or 0), 1e-6))

    counts = {
        'positive': {'synth': len([r for r in pos if r.get('source') == 'synth']), 'recorded': len([r for r in pos if r.get('source') == 'recorded']), 'uploaded': len([r for r in pos if r.get('source') == 'uploaded'])},
        'near': {'synth': len([r for r in near if r.get('source') == 'synth']), 'recorded': len([r for r in near if r.get('source') == 'recorded']), 'uploaded': len([r for r in near if r.get('source') == 'uploaded'])},
        'negative': {'recorded': len([r for r in own_neg if not r.get('near')]), 'uploaded': 0, 'mined': len([r for r in neg if r.get('source') == 'mined'])},
        'ambient': {'takes': len(amb), 'minutes': round(sum(float(r.get('seconds') or 0) for r in amb) / 60, 1)},
        'rated': len(rated), 'dropped': len(dropped), 'total': len(pos) + len(neg),
    }
    charts = {
        'score_pos': _hist([r['qa']['score'] for r in pos if isinstance((r.get('qa') or {}).get('score'), (int, float))], 0, 100, 20),
        'score_neg': _hist([r['qa']['score'] for r in neg if isinstance((r.get('qa') or {}).get('score'), (int, float))], 0, 100, 20),
        'length_own': _hist([float(r.get('seconds') or 0) for r in own_pos], 0, 3.0, 15),
        'length_synth': _hist([float(r.get('seconds') or 0) for r in pos if r.get('source') == 'synth'], 0, 3.0, 15),
        'level_own': _hist([db(r) for r in own_pos], -40, 0, 16),
        'level_synth': _hist([db(r) for r in pos if r.get('source') == 'synth'], -40, 0, 16),
        'bar': int(pr.get('settings', {}).get('qa_bar', 60)),
    }
    # per microphone (your takes) and per voice (synthetic)
    def agg(items, key, label_fn=None):
        out = {}
        for r in items:
            k = label_fn(r) if label_fn else (r.get(key) or '?')
            o = out.setdefault(k, {'n': 0, 'score_sum': 0, 'scored': 0, 'dropped': 0, 'flags': 0, 'level_sum': 0.0})
            o['n'] += 1
            sc = (r.get('qa') or {}).get('score')
            if isinstance(sc, (int, float)):
                o['score_sum'] += sc; o['scored'] += 1
            o['dropped'] += int(r.get('verdict') == 'drop')
            o['flags'] += int(bool([f for f in (r.get('flags') or []) if f != 'dropped']))
            o['level_sum'] += db(r)
        rows_ = []
        for k, o in out.items():
            rows_.append({'name': k, 'n': o['n'], 'score': round(o['score_sum'] / o['scored']) if o['scored'] else None,
                          'dropped': o['dropped'], 'flagged': o['flags'], 'level': round(o['level_sum'] / o['n'], 1)})
        return sorted(rows_, key=lambda x: (x['score'] if x['score'] is not None else 101, -x['n']))
    mics = agg(own_pos + own_neg, 'device')
    voices_ = agg([r for r in pos if r.get('source') == 'synth'], 'voice')
    tg = [tags.of(r) for r in own_pos]
    tag_counts = {ax: dict(Counter(t[ax] or '(nobody tagged)' for t in tg).most_common()) for ax in tags.DEFAULT}
    flag_counts = Counter(f for r in pos + neg for f in (r.get('flags') or []))

    advice = []
    def add(level, text):
        advice.append({'level': level, 'text': text})
    if not rated:
        add('warn', 'Nothing rated yet. Press Rate: the score decides which clips train.')
    bad_voices = [v for v in voices_ if v['score'] is not None and v['score'] < 70 and v['n'] >= 10]
    if bad_voices:
        add('warn', f"{len(bad_voices)} voice(s) score under 70: {', '.join(v['name'] for v in bad_voices[:5])}. Untick them under Voices and make the set again; their clips are mostly not your phrase.")
    bad_mics = [m for m in mics if m['score'] is not None and m['score'] < 70 and m['n'] >= 5]
    if bad_mics:
        add('warn', f"Low scores through {', '.join(m['name'] for m in bad_mics)}: listen to the lowest below; a mic too far away or too noisy reads as 'not the phrase'.")
    if flag_counts.get('clipped'):
        add('bad', f"{flag_counts['clipped']} clipped clip(s). Delete them on the Record tab (filter by flag) before training.")
    if flag_counts.get('says the phrase'):
        pass
    says = [r for r in neg if 'says the phrase' in ' '.join((r.get('qa') or {}).get('reasons', []))]
    if says:
        add('bad', f"{len(says)} negative(s) actually say the phrase. They are dropped; check the lowest list for more.")
    n_own, n_syn = len(own_pos), counts['positive']['synth']
    if n_own < TARGETS['positive'][0]:
        add('warn', f"Only {n_own} of your own positives; {TARGETS['positive'][0]} is the floor, {TARGETS['positive'][1]} is the target.")
    elif n_syn and n_own / max(1, n_syn) < 0.005:
        add('info', f"{n_own} real against {n_syn:,} synthetic: the trainers weight yours up, but another fifty of yours still moves the needle more than anything else.")
    if len([r for r in near if r.get('source') != 'synth']) < 10:
        add('warn', 'Under 10 near misses in your own voice. The most valuable negatives you can add.')
    if counts['ambient']['minutes'] < 3:
        add('warn', f"Room sound: {counts['ambient']['minutes']} min. False alarms cannot be counted without it.")
    if len(mics) < 2:
        add('info', 'One microphone so far. A second one teaches the phrase, not the mic.')
    missing_styles = [x for x in ('fast', 'soft', 'loud') if x not in tag_counts['how']] + (['across the room'] if len(tag_counts['where']) < 2 else [])
    if missing_styles and own_pos:
        add('info', f"Not recorded yet: {', '.join(missing_styles)}.")
    if rated and dropped:
        add('ok', f"{len(dropped)} of {len(rated)} rated clips are out ({100 * len(dropped) / len(rated):.0f}%). Under 10% is normal for a clean set.")
    return {'counts': counts, 'charts': charts, 'mics': mics, 'voices': voices_[:40], 'tags': tag_counts, 'judge': sum(1 for r in own_pos if r.get('judge')),
            'flags': dict(flag_counts), 'advice': advice}


# --- runs: what training produced ---------------------------------------------------------------------

def _run_rows(pdir, root=None):
    d = pdir / 'runs'
    out = []
    if not d.is_dir():
        return out
    hk = holdout.key(pdir)
    hw = (holdout.read(pdir) or {}).get('when')
    slug = (read_json(pdir / 'project.json') or {}).get('slug') or pdir.name
    training = root is None or any(j.get('project') == slug and j['kind'] in ('train', 'train_mww', 'tune') for j in jobs.running(root))
    for sub in sorted(d.iterdir(), reverse=True):
        rj = sub / 'run.json'
        if rj.is_file():
            r = read_json(rj)
            if not r:
                continue
            if r.get('state') == 'running' and not training:
                r['state'] = 'interrupted'                   # its trainer is gone (a restart, a kill): no result will come
                r['error'] = r.get('error') or 'the trainer was stopped before it finished'
                write_json(rj, r, indent=1)
            r['has_model'] = (sub / 'model.onnx').is_file() or (sub / 'final' / 'model.onnx').is_file() or (sub / 'model.tflite').is_file()
            r['judged'] = (sub / 'judge.json').is_file()
            if r['judged'] and r.get('state') == 'done':
                try:
                    cached = json.loads((sub / 'judge.json').read_text(encoding='utf-8'))
                    if 'heldout' not in r or 'by_tag' not in r['heldout']:        # a verdict from before recall-by-tag: filled once from the cached rows
                        r['heldout'] = _judge.summary(cached['rows'], r.get('rows'), r.get('trainer') or 'oww')
                        r.setdefault('threshold', r['heldout'].get('threshold'))
                        write_json(rj, r, indent=1)
                    if (cached.get('holdout_when') != hw or cached.get('held_key', hk) != hk or cached.get('stress_key') != _judge.STRESS_KEY) and r.get('heldout'):
                        r['heldout'] = {**r['heldout'], 'stale': True}        # the judging set changed since: re-judge
                except Exception:
                    pass
            out.append(r)
    return out


def list_runs(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    from wakeword_maker import features as _f
    return {'runs': _run_rows(pdir, root), 'features': _f.latest_oww(pdir),
            'running': [j for j in jobs.running(root) if j.get('project') == slug and j['kind'] in jobs.HEAVY],
            'queued': [j for j in jobs.queued(root, project=slug)]}


def _jsonl(path, limit=5000):
    rows = []
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows[-limit:]


def get_run(slug, rid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    if not _safe(rid):
        return {'error': 'bad run id'}, 400
    rdir = paths.project_dir(root, slug) / 'runs' / rid
    if not (rdir / 'run.json').is_file():
        return {'error': 'no such run'}, 404
    run = json.loads((rdir / 'run.json').read_text(encoding='utf-8'))
    out = {'run': run, 'metrics': _jsonl(rdir / 'metrics.jsonl'), 'trials': _jsonl(rdir / 'trials.jsonl')}
    if (rdir / 'final' / 'metrics.jsonl').exists():
        out['final_metrics'] = _jsonl(rdir / 'final' / 'metrics.jsonl')
    return out


def delete_run(slug, rid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    if not _safe(rid):
        return {'error': 'bad run id'}, 400
    if any(j.get('project') == slug and j['state'] == 'running' and j['kind'] in ('train', 'tune') for j in jobs.running(root)):
        return {'error': 'a training job is running on this wake word'}, 409
    rdir = paths.project_dir(root, slug) / 'runs' / rid
    if rdir.is_dir():
        shutil.rmtree(rdir)
        return {'deleted': True}
    return {'error': 'no such run'}, 404


def judge_run(slug, rid, query=None, settings=None, **_):
    """Every held-out clip scored by this run's model (cached in the run folder): the Test tab's breakdown."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    if not _safe(rid):
        return {'error': 'bad run id'}, 400
    pdir = paths.project_dir(root, slug)
    rdir = pdir / 'runs' / rid
    held_when = (holdout.read(pdir) or {}).get('when')
    cached = read_json(rdir / 'judge.json') if not (query or {}).get('force') else None
    if cached and cached.get('holdout_when') == held_when and cached.get('held_key', holdout.key(pdir)) == holdout.key(pdir):
        return {**cached, 'rows': [{**r, **tags.of(r)} for r in cached.get('rows') or []]}    # a re-drawn or re-rated held-out slice makes the old verdict stale
    py, err = _python_for('synth')
    if err:
        return err
    try:
        port = sampler_client.ensure(py, PLUGIN_DIR, root, (settings or {}).get('device', 'auto'))
        data, _ = sampler_client.get(port, 'judge', {'project': slug, 'run': rid, 'force': '1' if (query or {}).get('force') else ''}, timeout=600)
    except Exception as e:
        return {'error': f"judge: {e}"}, 502
    out = json.loads(data.decode('utf-8'))
    try:                                                      # the verdict rides along in run.json for the tables
        rj = rdir / 'run.json'
        run = json.loads(rj.read_text(encoding='utf-8'))
        run['heldout'] = _judge.summary(out.get('rows') or [], run.get('rows'), run.get('trainer') or 'oww')
        if run['heldout'].get('threshold') is not None and not run.get('threshold_by_user'):
            run['threshold'] = run['heldout']['threshold']
        write_json(rj, run, indent=1)
    except Exception:
        pass
    out['rows'] = [{**r, **tags.of(r)} for r in out.get('rows') or []]
    return out


async def try_runs(slug, request=None, settings=None, **_):
    """One utterance (multipart `audio`, field `runs` = comma-separated run ids) against several runs."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    py, err = _python_for('synth')
    if err:
        return err
    if 'json' in (request.headers.get('content-type') or ''):
        body = await request.json()
        runs = str(body.get('runs') or '')
        if not _safe(*runs.split(',')):          # several ticked models ride as one comma list
            return {'error': 'runs?'}, 400
        device, data, key, via = str(body.get('device') or ''), None, False, 'satellite'
    else:
        form = await request.form()
        up = form.get('audio')
        if up is None:
            return {'error': 'no audio part'}, 400
        data = await up.read()
        runs = str(form.get('runs') or '')
        if not _safe(*runs.split(',')):          # several ticked models ride as one comma list
            return {'error': 'runs?'}, 400
        device, key, via = '', form.get('key') == '1', 'browser'
    return await asyncio.to_thread(_try_runs, root, slug, py, runs, device, data, key, via, settings)   # the satellite wait and the scoring off the loop


def _try_runs(root, slug, py, runs, device, data, key, via, settings):
    if via == 'satellite':
        try:
            data, dev_id = _listen(device, 8, vad=True)
        except Exception as e:
            return {'error': f"{device}: {getattr(e, 'args', [e])[0]}"}, 502
    try:
        audio, sr = store.decode(data)
        audio = store.to_16k(audio, sr)
        if via == 'browser':
            head, tail = (250, 200) if key else (80, 80)
            h, t = int(16000 * head / 1000), int(16000 * tail / 1000)
            if len(audio) > h + t + 4000:
                audio = audio[h: len(audio) - t]
        else:
            audio = store.boost_if_faint(audio, store.mic_report(audio)['floor_db'])[0]
        import io
        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, (np.clip(audio, -1, 1) * 32767).astype(np.int16), 16000, format='WAV', subtype='PCM_16')
        port = sampler_client.ensure(py, PLUGIN_DIR, root, (settings or {}).get('device', 'auto'))
        out = sampler_client.post(port, 'score', {'project': slug, 'runs': runs}, buf.getvalue())
    except Exception as e:
        return {'error': f"try: {e}"}, 502
    rep = json.loads(out.decode('utf-8'))
    rep['mic'] = store.mic_report(audio)
    return rep


def _watch_scorer(root, slug, runs, settings):
    if not runs:                                   # a capture with no models ticked: the room is kept, nothing is scored
        return lambda wav: {}
    py, err = _python_for('synth')
    if err:
        raise RuntimeError(err[0].get('error') if isinstance(err, tuple) else str(err))
    device = (settings or {}).get('device', 'auto')
    sampler_client.ensure(py, PLUGIN_DIR, root, device)

    def listen_fn(wav):
        port = sampler_client.ensure(py, PLUGIN_DIR, root, device)        # the sampler may have been restarted since the last chunk
        out = sampler_client.post(port, 'listen', {'project': slug, 'runs': runs}, wav, timeout=600)
        return json.loads(out.decode('utf-8')).get('runs') or {}
    return listen_fn


def watch_start(slug, body=None, settings=None, **_):
    """Begin a room watch: a satellite listens in chunks for `minutes` (its thread), or the browser will post
    chunks (device 'browser'). Every fire of a chosen model is kept with its audio."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    body = body or {}
    runs = str(body.get('runs') or '')
    keep = body.get('keep') in (True, 'true', 1, '1')          # every piece becomes a room take
    if runs and not _safe(*runs.split(',')):          # several ticked models ride as one comma list
        return {'error': 'bad run id'}, 400
    if not runs and not keep:
        return {'error': 'pick at least one model, or keep the room'}, 400
    try:
        minutes = max(1, min(24 * 60, int(body.get('minutes') or 30)))
    except ValueError:
        minutes = 30
    device = str(body.get('device') or 'browser')[:60]
    test = body.get('test') in (True, 'true', 1, '1')          # a pushing board counts its fires and answers nothing
    pdir = paths.project_dir(root, slug)
    try:
        if device.startswith('browser'):                         # 'browser', or 'browser:<mic label>': one tab per PC mic
            st = _watch.start(pdir, slug, device, minutes, runs, _watch_scorer(root, slug, runs, settings), keep=keep, root=root)
        else:
            from core.devices import engine
            row = engine._usable(device)
            listen_fn, capture_fn, chunk_s, arm, disarm = _watch_fns(root, slug, device, runs, settings, keep=keep, test=test)
            st = _watch.start(pdir, slug, row['id'], minutes, runs, listen_fn, capture_fn, chunk_seconds=chunk_s,
                              keep=keep, test=test and arm is not None, arm=arm, disarm=disarm, root=root)
    except Exception as e:
        return {'error': f"watch: {getattr(e, 'args', [e])[0]}"}, 502
    st['running'] = True
    return {'watch': st}


CAPTURE_CHUNK_S = 30     # a pushing board's piece: two per minute, a megabyte each


def _capture(device, seconds, chunk_s=CAPTURE_CHUNK_S, test=False):
    """Ask a board to push its room for `seconds` (0 = stop), through the driver's door. A board already
    capturing (Sapphire restarted under it) is left alone."""
    from core.devices.drivers import satellite
    row, cfg, secrets = _board_parts(device)
    if seconds > 0:
        try:
            if (satellite.capture_state(row, cfg, secrets) or {}).get('active'):
                return None
        except Exception:
            pass
    return satellite.capture(row, cfg, secrets, seconds, chunk_s, test)


def _watch_fns(root, slug, device, runs, settings, keep=False, test=False):
    """A satellite watch's hands: the scorer and the capture, and for a board that pushes its room (an ESP32 with
    the capture door) the arm and disarm. Built on start and again on resume."""
    h = _health_of(device)
    scorer = _watch_scorer(root, slug, runs, settings)
    if _pushes(h):
        return (scorer, lambda seconds: _watch.take(device, timeout=seconds + 30), CAPTURE_CHUNK_S,
                lambda seconds: _capture(device, seconds, CAPTURE_CHUNK_S, test), lambda: _capture(device, 0))
    chunk_s = 60 if _wake_format(device, h) == 'tflite' else 300     # an ESP32 holds a minute of audio, a Pi five

    def capture_fn(seconds):
        data, _ = _listen(device, int(min(seconds, chunk_s)), vad=False)
        audio, sr = store.decode(data)
        audio = store.to_16k(audio, sr)
        return store.boost_if_faint(audio, store.mic_report(audio)['floor_db'])[0]
    return scorer, capture_fn, chunk_s, None, None


def watch_resume(settings):
    """On plugin load: every wake word's unfinished satellite watch picks up where the reload or restart cut it."""
    root, err = _root(settings)
    if err:
        return []
    out, busy = [], False
    for pr in store.list_projects(root):
        slug = pr['slug']
        try:
            got, held = _watch.resume(paths.project_dir(root, slug), slug,
                                      lambda d, r, w, s=slug: _watch_fns(root, s, d, r, settings, keep=bool(w.get('keep')), test=bool(w.get('test'))), root=root)
            busy = busy or held
            out += [f"{slug}: {st['device']} ({st['id']})" for st in (got or [])]
        except Exception as e:
            logger.warning(f"[WWM] could not resume the watch for {slug}: {e}")
    return out, busy


def watch_get(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    return {'watch': _watch.latest(pdir, slug), 'watches': _watch.watches(pdir, slug)}


def watch_stop(slug, body=None, query=None, settings=None, **_):
    """Stop the watch on one microphone ({device} or {id}); with neither, every watch of the wake word."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    q = {**(query or {}), **(body or {})}
    device = str(q.get('device') or '')[:60] or None
    wid = str(q.get('id') or '') or None
    if wid and not _safe(wid):
        return {'error': 'bad watch id'}, 400
    pdir = paths.project_dir(root, slug)
    last = _watch.stop(pdir, slug, device=device, wid=wid)
    return {'watch': last or _watch.latest(pdir, slug), 'watches': _watch.watches(pdir, slug)}


async def watch_chunk(slug, request=None, settings=None, **_):
    """The browser's chunk of a running watch (multipart `audio`)."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    form = await request.form()
    device = str(form.get('device') or 'browser')[:60]
    st = _watch.live_for(pdir, slug, device)
    if not st or st.get('ended') or not st.get('browser'):
        return {'error': f"no browser watch is running on {device}"}, 409
    up = form.get('audio')
    if up is None:
        return {'error': 'no audio part'}, 400
    data = await up.read()

    def work():
        try:
            audio, sr = store.decode(data)
            audio = store.to_16k(audio, sr)
            listen_fn = _watch_scorer(root, slug, st['runs'], settings)
            out = _watch.chunk(pdir, st, audio, device, listen_fn, root=root)
        except Exception as e:
            return {'error': f"watch: {getattr(e, 'args', [e])[0]}"}, 502
        out['running'] = True
        return {'watch': out}
    return await asyncio.to_thread(work)       # scoring takes seconds (minutes on a cold sampler): never on the loop


def watch_audio(slug, wid, fid, **kw):
    settings = kw.get('settings')
    root, err = _root(settings)
    if err:
        return err
    if not _safe(wid, fid):
        return {'error': 'bad id'}, 400
    f = _watch.wdir(paths.project_dir(root, slug), wid) / f"{fid}.wav"
    if not f.is_file():
        return {'error': 'no audio (sorted already?)'}, 404
    return FileResponse(str(f), media_type='audio/wav')


def watch_fate(slug, wid, fid, body=None, settings=None, **_):
    """What a fire was: negative (keep as a negative), positive (that was me saying it), discard."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    what = str((body or {}).get('as') or '')
    if what not in ('negative', 'positive', 'discard'):
        return {'error': 'as = negative | positive | discard'}, 400
    if not _safe(wid, fid):
        return {'error': 'bad id'}, 400
    pdir = paths.project_dir(root, slug)
    st = _watch.read(pdir, wid) or {}
    try:
        fire = _watch.fate(pdir, wid, fid, what, {'device': st.get('device'), 'text': pr['phrase'] if what == 'positive' else None}, root=root, slug=slug)
    except ValueError as e:
        return {'error': str(e)}, 404
    if what in ('negative', 'positive'):
        _forget(pdir / f"{what}/recorded")
    return {'fire': fire, 'counts': store.counts(pdir)}


def set_run(slug, rid, body=None, settings=None, **_):
    """The threshold the user chose for a run (and per device), and a label; Install reads these."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    if not _safe(rid):
        return {'error': 'bad run id'}, 400
    rdir = paths.project_dir(root, slug) / 'runs' / rid
    if not (rdir / 'run.json').is_file():
        return {'error': 'no such run'}, 404
    run = json.loads((rdir / 'run.json').read_text(encoding='utf-8'))
    body = body or {}
    if body.get('threshold') is not None:
        try:
            run['threshold'] = round(max(0.05, min(0.999, float(body['threshold']))), 3)
        except ValueError:
            return {'error': 'threshold is a number'}, 400
        run['threshold_by_user'] = True          # a re-judge keeps the user's number
        if run.get('trainer') == 'mww':
            run['cutoff'] = run['threshold']
            mj = rdir / 'model.json'
            if mj.is_file():
                try:
                    man = json.loads(mj.read_text(encoding='utf-8'))
                    man['micro']['probability_cutoff'] = run['threshold']
                    write_json(mj, man, indent=2)
                except Exception:
                    pass
    if isinstance(body.get('thresholds'), dict):
        run['thresholds'] = {str(k)[:40]: round(max(0.05, min(0.999, float(v))), 3) for k, v in body['thresholds'].items() if v is not None}
    if 'label' in body:
        run['label'] = str(body['label'])[:80]
    if 'chosen' in body:
        run['chosen'] = bool(body['chosen'])
    write_json(rdir / 'run.json', run, indent=1)
    return {'run': run}


def train_options(**_):
    from wakeword_maker import train_oww, tune, train_mww
    return {'defaults': train_oww.DEFAULTS, 'presets': train_oww.PRESETS, 'space': {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in tune.SPACE.items()},
            'thresholds': train_oww.THRESHOLDS, 'mww': {'defaults': train_mww.DEFAULTS, 'presets': train_mww.PRESETS, 'cutoffs': train_mww.CUTOFFS},
            'grid': {'recipes': list(augment.STOPS), 'presets': ['quick', 'standard', 'thorough'], 'knobs': {t: list(v) for t, v in GRID_KNOBS.items()}, 'max': GRID_MAX}}


def voice_catalog(settings=None, **_):
    root = paths.data_root(settings)
    return voices.catalog(root if root and root.is_dir() else None)


def voice_sample(query=None, settings=None, **_):
    """One voice saying the text, as a WAV: the Play button on the Voices tab. A warm helper process answers;
    the first call starts it (a few seconds), a piper voice downloads on its first use."""
    root, err = _root(settings)
    if err:
        return err
    py, err = _python_for('synth')
    if err:
        return err
    q = query or {}
    engine = q.get('engine', 'kokoro')
    voice = str(q.get('voice') or '')
    text = str(q.get('text') or 'hey sapphire')[:80]
    if engine not in ('piper', 'kokoro') or not voice or '/' in voice or '..' in voice:
        return {'error': 'engine and voice?'}, 400
    try:
        port = sampler_client.ensure(py, PLUGIN_DIR, root, (settings or {}).get('device', 'auto'))
        wav, tag = sampler_client.fetch(port, engine, voice, text, q.get('speaker'), float(q.get('speed', 1.0)), q.get('blend'))
    except Exception as e:
        return {'error': f"sample: {getattr(e, 'reason', None) or e}"}, 502
    return Response(content=wav, media_type='audio/wav', headers={'X-Sample': tag, 'Cache-Control': 'no-store'})


def variation_presets(**_):
    return {'presets': augment.PRESETS, 'default': augment.DEFAULT, 'stops': augment.STOPS, 'renamed': augment.OLD_STOPS}


def get_holdout(slug, settings=None, **_):
    """The held-out slice; made on first ask so Train and Test always have one."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    h = holdout.read(pdir) or holdout.make(pdir)
    return {'holdout': {k: v for k, v in h.items() if k not in ('positive', 'near', 'negative', 'ambient')}}


def reshuffle_holdout(slug, body=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    try:
        frac = float((body or {}).get('fraction') or holdout.FRACTION)
    except ValueError:
        frac = holdout.FRACTION
    h = holdout.make(pdir, frac=max(0.05, min(0.4, frac)), root=root)       # the shared room library is in the draw
    for c in paths.COLLECTIONS:
        _forget(pdir / c)
    return {'holdout': {k: v for k, v in h.items() if k not in ('positive', 'near', 'negative', 'ambient')}}


def dry_run(slug, query=None, settings=None, **_):
    """Three hundred draws of the recipe over the real set, judged: rejections, voice over noise, and whether Whisper
    still hears the phrase in the varied clips against the clean ones. The harshest survivors come back replayable."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    py, err = _python_for('synth')
    if err:
        return err
    q = query or {}
    try:
        port = sampler_client.ensure(py, PLUGIN_DIR, root, (settings or {}).get('device', 'auto'))
        data, _ = sampler_client.get(port, 'dryrun', {'project': slug, 'n': q.get('n', 300), 'whisper': (settings or {}).get('qa_whisper_model', 'base.en')}, timeout=300)
    except Exception as e:
        return {'error': f"dry run: {e}"}, 502
    return json.loads(data.decode('utf-8'))


def variation_sample(slug, query=None, settings=None, **_):
    """One draw of the recipe on one of the project's clips, as a WAV: the Variations tab's ear."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    py, err = _python_for('synth')
    if err:
        return err
    q = query or {}
    clip = str(q.get('clip') or 'random')
    if clip.count('/') not in (0, 2) or not _safe(*clip.split('/')):
        return {'error': 'clip?'}, 400
    try:
        port = sampler_client.ensure(py, PLUGIN_DIR, root, (settings or {}).get('device', 'auto'))
        wav, tag = sampler_client.get(port, 'variation', {'project': slug, 'clip': clip, 'want': q.get('want'), 'seed': q.get('seed')})
    except Exception as e:
        return {'error': f"variation: {e}"}, 502
    return Response(content=wav, media_type='audio/wav', headers={'X-Sample': tag, 'Cache-Control': 'no-store'})


# --- jobs -----------------------------------------------------------------------------------------------

def list_jobs(query=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    q = query or {}
    return {'jobs': jobs.list_all(root, project=q.get('project') or None, limit=int(q.get('limit', 30)))}


def start_job(body=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    body = body or {}
    if body.get('retry'):                      # the same job again: an interrupted or failed one
        jid = str(body['retry'])
        if not _safe(jid):
            return {'error': 'bad job id'}, 400
        j = jobs.retry(root, paths.jobs_dir(root) / jid, publish=_publish)
        return {'job': j} if j else ({'error': 'that job is not over, or is gone'}, 409)
    kind = str(body.get('kind') or '')
    if kind not in ('synth', 'qa', 'voices', 'features', 'train', 'tune', 'features_mww', 'train_mww', 'judge', 'download', 'delete_dataset'):
        return {'error': f"unknown job kind {kind}"}, 400
    slug = body.get('project')
    if kind in ('synth', 'qa', 'features', 'train', 'tune', 'features_mww', 'train_mww', 'judge'):
        pr, err = _project(root, slug)
        if err:
            return err
    py, err = _python_for(kind)
    if err:
        return err
    args = {**(body.get('args') or {}), 'device': (settings or {}).get('device', 'auto'),
            'whisper': (settings or {}).get('qa_whisper_model', 'base.en')}
    if kind in jobs.HEAVY:
        return {'job': jobs.enqueue(root, kind, args, py, PLUGIN_DIR, project=slug, publish=_publish)}
    return {'job': jobs.start(root, kind, args, py, PLUGIN_DIR, project=slug, publish=_publish)}


def get_job(jid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    j = jobs.read(paths.jobs_dir(root) / jid) if '/' not in jid and '..' not in jid else None
    return {'job': j} if j else ({'error': 'no such job'}, 404)


def cancel_job(jid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    if not _safe(jid):
        return {'error': 'bad id'}, 400
    jdir = paths.jobs_dir(root) / jid
    if jobs.cancel(jdir):
        return {'cancelled': True}
    j = jobs.read(jdir)
    if j and j['state'] not in ('running', 'queued'):          # over already: forget it (its folder goes)
        shutil.rmtree(jdir, ignore_errors=True)
        return {'cancelled': False, 'forgotten': True}
    return {'cancelled': False}


def job_log(jid, query=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    if not _safe(jid):
        return {'error': 'bad id'}, 400
    p = paths.jobs_dir(root) / jid / 'log.txt'
    if not p.is_file():
        return PlainTextResponse('')
    size = p.stat().st_size
    with open(p, 'rb') as fh:
        fh.seek(max(0, size - 64000))
        return PlainTextResponse(fh.read().decode('utf-8', 'replace'))


# --- install ---------------------------------------------------------------------------------------------
# Install goes through the wake word library (core/wakeword/library.py, Settings > Wakeword): the ★ model of each
# kind lands under user/wakeword/models/<slug>.<family> with the sidecar, then library.switch() makes the listener
# take it (desktop: the two settings and the live detector; a satellite: its wake_model wish, sent through
# PUT /wakeword/model by the driver, the board restarts on it; one that cannot be reached is sent it when next
# seen). The library page and a board's Wake word tab switch between the same models afterwards. 2026-10-19.

from core.paths import PROJECT_ROOT

SAPPHIRE_ROOT = PROJECT_ROOT
USER_MODELS = SAPPHIRE_ROOT / 'user' / 'wakeword' / 'models'
RUN_FILES = {'model.onnx': 'application/octet-stream', 'model.tflite': 'application/octet-stream', 'model.json': 'application/json'}


def _remember_install(root, slug, **fields):
    """settings.install of the project: where its models went."""
    pr = store.load_project(root, slug)
    inst = {**(((pr.get('settings') or {}).get('install')) or {}), **fields}
    pr.setdefault('settings', {})['install'] = inst
    store.save_project(paths.project_dir(root, slug), pr)
    return inst


def _chosen(pdir):
    """trainer -> the ★ run, else the best fresh verdict; only runs that left a model."""
    out = {}
    rows = [r for r in _run_rows(pdir) if r.get('state') == 'done' and r.get('has_model') and not r['id'].startswith('builtin-')]
    for tr in ('oww', 'mww'):
        mine = [r for r in rows if r.get('trainer') == tr]
        star = [r for r in mine if r.get('chosen')]
        if star:
            out[tr] = star[0]
            continue
        judged = [r for r in mine if r.get('heldout') and not r['heldout'].get('stale') and r['heldout'].get('recall') is not None]
        if judged:                                  # the judge's own order: clean first, recall, fewest sound-alikes, fewest false alarms, threshold nearest the usual
            usual = _judge.USUAL.get(tr, 0.5)
            out[tr] = max(judged, key=lambda r: (bool(r['heldout'].get('clean')), r['heldout'].get('recall') or 0, -(r['heldout'].get('near_fires') or 0),
                                                 r['heldout'].get('stress_recall') or 0, -(r['heldout'].get('fp_per_hour') or 0),
                                                 (r['heldout'].get('margin') or {}).get('gap') or 0, -abs((r['heldout'].get('threshold') or usual) - usual)))
    return out


def _run_brief(r):
    h = r.get('heldout') or {}
    return {'id': r['id'], 'label': r.get('label') or '', 'trainer': r.get('trainer'), 'recipe': r.get('recipe'), 'chosen': bool(r.get('chosen')),
            'threshold': r.get('threshold') or r.get('cutoff') or h.get('threshold') or 0.5,
            'recall': h.get('recall'), 'near_fires': h.get('near_fires'), 'n_near': h.get('n_near'), 'stale': bool(h.get('stale'))}


def _model_path(pdir, rid):
    if not _safe(rid):
        raise ValueError('bad run id')
    rdir = pdir / 'runs' / rid
    for p in (rdir / 'model.onnx', rdir / 'final' / 'model.onnx', rdir / 'model.tflite'):
        if p.is_file():
            return rdir, p
    raise ValueError('that run left no model')


def _loads_ours(slug):
    """True when core would load OUR file for this name. Core resolves user/wakeword/models first (since 2026-10-05),
    so this is the post-condition of an install, and the guard against an older core that still prefers its own."""
    try:
        from core.wakeword import resolve_model_path
        return os.path.abspath(str(resolve_model_path(slug))) == str((USER_MODELS / f"{slug}.onnx").absolute())
    except Exception:
        return (USER_MODELS / f"{slug}.onnx").is_file()


def _desktop_state(slug):
    import config as _cfg
    from core.wakeword import library
    model = str(getattr(_cfg, 'WAKEWORD_MODEL', '') or '')
    mine = library.get(slug, 'onnx') or {}
    ours = model == slug and mine.get('source') == 'user' and mine.get('made_by') == 'wakeword-maker' and _loads_ours(slug)
    return {'model': model, 'threshold': getattr(_cfg, 'WAKEWORD_THRESHOLD', None), 'enabled': bool(getattr(_cfg, 'WAKE_WORD_ENABLED', False)),
            'file': (USER_MODELS / f"{slug}.onnx").is_file(), 'ours': ours}


def install_status(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    picks = {tr: _run_brief(r) for tr, r in _chosen(pdir).items()}
    sats = []
    try:
        from core.wakeword import library
        for l in library.listeners():
            if l.get('kind') != 'device':
                continue
            sats.append({'id': l['id'], 'location': l.get('location', ''), 'online': l.get('online'), 'format': l.get('family'),
                         'listening': l.get('model'), 'ours': l.get('model') == slug,
                         'set': l.get('wish') == slug and l.get('model') != slug})
    except Exception as e:
        logger.debug(f"[WWM] install listeners: {e}")
    inst = (pr.get('settings') or {}).get('install') or {}
    return {'picks': picks, 'desktop': {**_desktop_state(slug), 'previous': inst.get('previous_desktop')}, 'satellites': sats, 'phrase': pr.get('phrase')}


def install_desktop(slug, body=None, settings=None, **_):
    """Put the ★ desktop model on this computer and start listening for it: file, two settings, live swap."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    rid = str((body or {}).get('run') or '') or (_chosen(pdir).get('oww') or {}).get('id') or ''
    if not rid:
        return {'error': 'no desktop model to install yet: train one, then ★ it on Train'}, 400
    try:
        rdir, mf = _model_path(pdir, rid)
    except ValueError as e:
        return {'error': str(e)}, 404
    if mf.suffix != '.onnx':
        return {'error': 'the desktop takes the openWakeWord model (.onnx), not the ESP32 one'}, 400
    run = json.loads((rdir / 'run.json').read_text(encoding='utf-8'))
    thr = float(run.get('threshold') or (run.get('heldout') or {}).get('threshold') or 0.5)
    from core.wakeword import library
    before = _desktop_state(slug)
    try:
        library.add(slug, mf.read_bytes(), 'onnx', {'threshold': thr, 'run': rid, 'label': run.get('label')},
                    overwrite=True, made_by='wakeword-maker', phrase=pr.get('phrase'))
        if not before['ours'] and before['model']:
            _remember_install(root, slug, previous_desktop={'model': before['model'], 'threshold': before['threshold']})
        told = library.switch(library.DESKTOP, slug)              # the two settings, then the live detector
        ok = told.get('sent')
    except library.LibraryError as e:
        return {'error': str(e)}, 502
    except Exception as e:
        return {'error': f"the install failed: {e}"}, 502
    if not _loads_ours(slug):                     # an older core that still prefers its own file under this name
        return {'error': f"the file is in place and the setting is {slug}, but this Sapphire loads its own {slug} model first. "
                         "Update Sapphire (user/ wins since 2.13.4), or install the file under another name.", 'code': 'shadowed',
                'desktop': _desktop_state(slug)}, 409
    return {'desktop': _desktop_state(slug), 'swapped': bool(ok), 'run': rid, 'threshold': thr}


def install_desktop_restore(slug, settings=None, **_):
    """Put the model that was listening before back (the file stays)."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    prev = ((pr.get('settings') or {}).get('install') or {}).get('previous_desktop')
    if not prev or not prev.get('model'):
        return {'error': 'nothing to put back'}, 400
    try:
        from core.settings_manager import settings as _settings
        if prev.get('threshold') is not None:
            _settings.set('WAKEWORD_THRESHOLD', float(prev['threshold']), persist=True)
        _settings.set('WAKEWORD_MODEL', str(prev['model']), persist=True)
        from core.api_fastapi import get_system
        get_system().reload_wakeword_model()
    except Exception as e:
        return {'error': f"could not swap back: {e}"}, 502
    return {'desktop': _desktop_state(slug)}


def install_satellite(slug, body=None, settings=None, **_):
    """Send the ★ model of the right kind to a satellite: PUT /wakeword/model with the bytes, the manifest as query
    fields. A body without the door answers 404 and the page says so."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    device = str((body or {}).get('device') or '')
    picks = _chosen(pdir)
    fmt = _wake_format(device)
    try:
        from core.devices import engine
        from core.devices.drivers import satellite
        row = engine._usable(device)
        part = engine._part(row, 'satellite')
        if not part:
            return {'error': f"{device} is not a satellite"}, 400
        secrets = engine._part_secrets(row['id'], 'satellite')
    except Exception as e:
        return {'error': f"{device}: {getattr(e, 'args', [e])[0]}"}, 502
    want = str((body or {}).get('run') or '') or (picks.get('mww' if fmt == 'tflite' else 'oww') or {}).get('id') or ''
    if not want:
        return {'error': f"no {'ESP32' if fmt == 'tflite' else 'desktop/Pi'} model to send yet: train one, then ★ it on Train"}, 400
    try:
        rdir, mf = _model_path(pdir, want)
    except ValueError as e:
        return {'error': str(e)}, 404
    if mf.suffix.lstrip('.') != fmt:
        return {'error': f"{device} runs {'microWakeWord (.tflite)' if fmt == 'tflite' else 'openWakeWord (.onnx)'}; that run is the other family"}, 400
    run = json.loads((rdir / 'run.json').read_text(encoding='utf-8'))
    thr = float(run.get('threshold') or run.get('cutoff') or (run.get('heldout') or {}).get('threshold') or 0.5)
    extra = {}
    if mf.suffix == '.tflite':
        mj = read_json(rdir / 'model.json')
        man = (mj.get('micro') if isinstance(mj, dict) else None) or {}
        extra = {'sliding_window': man.get('sliding_window_size', 5), 'step_ms': man.get('feature_step_size', 10)}
    from core.wakeword import library
    meta = {'threshold': thr, 'run': want, 'label': run.get('label')}
    if extra:
        meta['micro'] = {'sliding_window': extra['sliding_window'], 'step_ms': extra['step_ms']}
    try:
        library.add(slug, mf.read_bytes(), fmt, meta, overwrite=True, made_by='wakeword-maker', phrase=pr.get('phrase'))
        told = library.switch(row['id'], slug)                    # its wish; the driver sends it and the board restarts
    except library.LibraryError as e:
        return {'error': str(e)}, 400
    except Exception as e:
        return {'error': f"{device}: {getattr(e, 'args', [e])[0]}"}, 502
    if not told.get('sent') and not told.get('had'):
        code = 'no_door' if 'no door' in str(told.get('warning') or '') else 'not_sent'
        return {'error': f"{device} is set to {slug}, but: {told.get('warning')}", 'code': code}, 502
    return {'sent': want, 'threshold': thr, 'told': told}


def run_file(slug, rid, name, settings=None, **_):
    """A run's model files, for copying onto a body by hand: model.onnx, model.tflite, model.json (ESP32 manifest),
    or 'manifest' for the desktop one."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    try:
        rdir, mf = _model_path(pdir, rid)
    except ValueError as e:
        return {'error': str(e)}, 404
    if name == 'manifest':
        run = json.loads((rdir / 'run.json').read_text(encoding='utf-8'))
        thr = run.get('threshold') or run.get('cutoff') or (run.get('heldout') or {}).get('threshold') or 0.5
        return Response(json.dumps({'name': slug, 'phrase': pr.get('phrase'), 'threshold': thr, 'framework': mf.suffix.lstrip('.'), 'run': rid, 'label': run.get('label')}, indent=1),
                        media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{slug}.json"'})
    if name not in RUN_FILES:
        return {'error': 'no such file'}, 404
    f = rdir / name if name != 'model.onnx' else mf
    if not f.is_file():
        return {'error': 'that run has no such file'}, 404
    out_name = f"{slug}{f.suffix}" if name != 'model.json' else f"{slug}_manifest.json"
    return FileResponse(str(f), media_type=RUN_FILES[name], filename=out_name)


# --- the grid: many runs from one click --------------------------------------------------------------------
# A grid is the product of the axes you tick: trainer x recipe stop x source block x preset x knobs x seeds. Every
# cell is one ordinary train job, queued here in Sapphire's process (so the tails and the queue are its own), with
# a label that names its cell and `grid` in its args. <project>/grids/<id>.json keeps the cells; GET joins each
# to its job's state and its run's verdict and folds them by axis: mean recall, stress, sound-alikes and gap over
# everything else, so "which source block helps" is one row, not a scroll through forty runs.
GRID_MAX = 200
GRID_SOURCES = {           # named source blocks (features.sources_of); 'without <mic>' rows are added per project
    'all': {}, 'piper only': {'synth': 'piper'}, 'kokoro only': {'synth': 'kokoro'},
    'synthetic only': {'human': 'none'}, 'own takes only': {'synth': 'none'}, 'own x16': {'own_repeat': 16},
}
GRID_KNOBS = {             # named settings per trainer; 'default' = the preset as it stands
    'oww': {'dnn 32x1': {'model_type': 'dnn', 'layer_size': 32, 'n_blocks': 1},
            'dnn 64x2': {'model_type': 'dnn', 'layer_size': 64, 'n_blocks': 2},
            'rnn': {'model_type': 'rnn'}},
    'mww': {'more room': {'sampling_room': 6.0}, 'heavier negatives': {'negative_class_weight': 40},
            'bigger net': {'pointwise_filters': '96,96,96,96', 'first_conv_filters': 48},
            'lighter adversarial': {'sampling_adversarial': 1.5, 'penalty_adversarial': 1.0}},
}
GRID_AXES = ('trainer', 'recipe', 'sources', 'preset', 'knob', 'seed')


def _own_rows(pdir):
    return [r for c in ('positive/recorded', 'positive/uploaded') for r in store.list_clips(pdir, c, 0, 10 ** 7)[0]]


_grid_root = None      # set by the routes before _grid_sources: where the room library lives


def _grid_sources(pdir, root=None):
    """The named source blocks, plus 'without <mic>' / 'only <mic>' for every microphone in your takes (two or more),
    and 'room only / without <mic>' for every microphone in the shared room library."""
    global _grid_root
    if root is not None:
        _grid_root = root
    out = dict(GRID_SOURCES)
    rows = _own_rows(pdir)
    devices = sorted({r.get('device') or 'unknown' for r in rows})
    if len(devices) > 1:
        for d in devices:
            out[f"without {d}"] = {'devices': [x for x in devices if x != d]}
        for d in devices:
            out[f"only {d}"] = {'devices': [d]}
    for ax, values in _tag_values(rows).items():              # 'does this data help': the same takes minus one tag value
        if len(values) > 1:
            for v in values:
                out[f"without {v}"] = {ax: [x for x in values if x != v]}
    room = sorted({r.get('device') or 'unknown' for r in store.list_room(_grid_root or '', dropped=False)} if _grid_root else set())
    if len(room) > 1:
        for d in room:
            out[f"room only {d}"] = {'room_devices': [d]}
            out[f"room without {d}"] = {'room_devices': [x for x in room if x != d]}
    return out


def _tag_values(rows):
    """The tag values present in your takes, per axis, skipping an empty who."""
    tg = [tags.of(r) for r in rows]
    return {ax: sorted({t[ax] for t in tg if t[ax]}) for ax in tags.DEFAULT}


def sources_options(slug, settings=None, **_):
    """What a run may train on: the mics and tags in your takes, and the named source blocks a grid offers."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    from wakeword_maker import features as _f
    pdir = paths.project_dir(root, slug)
    rows = _own_rows(pdir)
    return {'devices': sorted({r.get('device') or 'unknown' for r in rows}), 'tags': _tag_values(rows),
            'presets': [{'name': k, 'sources': v, 'label': _f.sources_label(_f.sources_of({'sources': v}))} for k, v in _grid_sources(pdir, root).items()]}


def _grids_dir(pdir):
    return pdir / 'grids'


def _pivot(cells):
    """Per axis with more than one value: for each value, the judged cells' mean recall, stress recall, sound-alike
    fires and gap, with how many cells that is and the lowest recall among them."""
    out = {}
    for axis in GRID_AXES:
        values = sorted({str(c.get(axis)) for c in cells})
        if len(values) < 2:
            continue
        table = {}
        for v in values:
            mine = [c for c in cells if str(c.get(axis)) == v and c.get('recall') is not None]
            mean = lambda k: round(sum(float(c[k]) for c in mine if c.get(k) is not None) / max(1, len([c for c in mine if c.get(k) is not None])), 3) if any(c.get(k) is not None for c in mine) else None
            table[v] = {'n': len(mine), 'of': len([c for c in cells if str(c.get(axis)) == v]), 'recall': mean('recall'), 'recall_min': min((c['recall'] for c in mine), default=None),
                        'stress': mean('stress'), 'near': mean('near'), 'gap': mean('gap'), 'fp': mean('fp')}
            if any(c.get('focus') is not None for c in cells):
                table[v]['focus'] = mean('focus')
                table[v]['focus_stress'] = mean('focus_stress')
        out[axis] = table
    return out


def _grid_view(root, pdir, grid):
    """The grid's cells joined to their job state and run verdict, counts, and the pivot."""
    runs = {r['id']: r for r in _run_rows(pdir, root)}
    by_job = {j['id']: j for j in jobs.list_all(root, project=grid.get('project'), limit=10 ** 6)}
    cells = []
    focus = grid.get('focus') or {}
    for c in grid.get('cells') or []:
        j = by_job.get(c.get('job'))
        rid = ((j or {}).get('result') or {}).get('run')
        r = runs.get(rid) or {}
        h = r.get('heldout') or {}
        cell = {**c, 'state': (j or {}).get('state') or 'gone', 'run': rid, 'recall': h.get('recall'), 'stress': h.get('stress_recall'),
                'near': h.get('near_fires'), 'gap': (h.get('margin') or {}).get('gap'), 'fp': h.get('fp_per_hour'), 'threshold': h.get('threshold'),
                'stale': bool(h.get('stale')), 'error': (j or {}).get('error')}
        if focus:                                             # the held-out takes of one tag value: the data the grid asks about
            f = ((h.get('by_tag') or {}).get(focus.get('axis')) or {}).get(focus.get('value')) or {}
            cell['focus'], cell['focus_stress'], cell['focus_n'] = f.get('recall'), f.get('stress'), f.get('n')
        cells.append(cell)
    counts = Counter(c['state'] for c in cells)
    return {**grid, 'cells': cells, 'counts': dict(counts), 'done': counts.get('done', 0), 'of': len(cells), 'pivot': _pivot(cells)}


def grid_start(slug, body=None, settings=None, **_):
    """Queue a grid: every combination of the ticked axes, `seeds` times each (seed 1 of every cell first, so the
    first pass covers the whole grid). Refused above GRID_MAX cells. Answers the grid as GET would."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    body = body or {}
    pdir = paths.project_dir(root, slug)
    named = _grid_sources(pdir, root)
    trainers = [t for t in (body.get('trainers') or ['oww']) if t in ('oww', 'mww')]
    recipes = [r for r in (body.get('recipes') or ['natural']) if r in augment.STOPS]
    srcs = [s for s in (body.get('sources') or ['all']) if s in named]
    presets = [p for p in (body.get('presets') or ['standard']) if p in ('quick', 'standard', 'thorough')]
    knobs = body.get('knobs') if isinstance(body.get('knobs'), dict) else {}
    try:
        seeds = max(1, min(5, int(body.get('seeds') or 1)))
    except ValueError:
        seeds = 1
    if not (trainers and recipes and srcs and presets):
        return {'error': 'pick at least one trainer, recipe, source block and preset'}, 400
    cells = []
    for seed in range(1, seeds + 1):
        for tr in trainers:
            names = [k for k in (knobs.get(tr) or ['default']) if k == 'default' or k in GRID_KNOBS[tr]] or ['default']
            for recipe in recipes:
                for sname in srcs:
                    for preset in presets:
                        for kn in names:
                            cells.append({'trainer': tr, 'recipe': recipe, 'sources': sname, 'preset': preset, 'knob': kn, 'seed': seed})
    if len(cells) > GRID_MAX:
        return {'error': f"{len(cells)} runs is too many for one grid; {GRID_MAX} at most. Fewer axes, or fewer seeds."}, 400
    py, err = _python_for('train')
    if err:
        return err
    gid = time.strftime('%Y%m%d-%H%M%S')
    label = str(body.get('label') or '')[:40].strip()
    holdout_on = body.get('holdout', True) not in (False, 'false', 0, '0')
    try:
        rounds = max(1, min(6, int(body.get('rounds') or 2)))
    except ValueError:
        rounds = 2
    for c in cells:
        cfg = dict(GRID_KNOBS[c['trainer']].get(c['knob'], {}))
        cfg['seed'] = c['seed']
        c['label'] = ' · '.join(x for x in (label, c['recipe'], c['sources'], c['preset'], c['knob'] if c['knob'] != 'default' else '', f"s{c['seed']}") if x)
        args = {'preset': c['preset'], 'recipe': c['recipe'], 'sources': named[c['sources']], 'cfg': cfg, 'holdout': holdout_on, 'rounds': rounds,
                'label': c['label'], 'grid': gid, 'device': (settings or {}).get('device', 'auto'), 'whisper': (settings or {}).get('qa_whisper_model', 'base.en')}
        j = jobs.enqueue(root, 'train' if c['trainer'] == 'oww' else 'train_mww', args, py, PLUGIN_DIR, project=slug, publish=_publish)
        c['job'] = j['id']
    focus = body.get('focus') if isinstance(body.get('focus'), dict) else None
    if focus and (focus.get('axis') not in ('device',) + tuple(tags.DEFAULT) or not focus.get('value')):
        focus = None
    grid = {'id': gid, 'project': slug, 'label': label, 'when': time.time(), 'seeds': seeds, 'rounds': rounds, 'holdout': holdout_on,
            'axes': {'trainers': trainers, 'recipes': recipes, 'sources': srcs, 'presets': presets, 'knobs': {t: [k for k in (knobs.get(t) or ['default'])] for t in trainers}},
            'cells': cells, **({'focus': {'axis': str(focus['axis']), 'value': str(focus['value'])[:60]}} if focus else {})}
    _grids_dir(pdir).mkdir(parents=True, exist_ok=True)
    write_json(_grids_dir(pdir) / f"{gid}.json", grid, indent=1)
    return {'grid': _grid_view(root, pdir, grid)}


def grids_list(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    out = []
    d = _grids_dir(pdir)
    for f in sorted(d.glob('*.json'), reverse=True) if d.is_dir() else []:
        g = read_json(f)
        if g:
            v = _grid_view(root, pdir, g)
            out.append({k: v[k] for k in ('id', 'label', 'when', 'seeds', 'axes', 'counts', 'done', 'of')})
    return {'grids': out}


def grid_get(slug, gid, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    if not _safe(gid):
        return {'error': 'bad grid id'}, 400
    pdir = paths.project_dir(root, slug)
    g = read_json(_grids_dir(pdir) / f"{gid}.json")
    if not g:
        return {'error': 'no such grid'}, 404
    return {'grid': _grid_view(root, pdir, g)}


def grid_delete(slug, gid, settings=None, **_):
    """Forget a grid: its queued and running cells are cancelled; finished runs stay on the scoreboard."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    if not _safe(gid):
        return {'error': 'bad grid id'}, 400
    pdir = paths.project_dir(root, slug)
    f = _grids_dir(pdir) / f"{gid}.json"
    g = read_json(f)
    if not g:
        return {'error': 'no such grid'}, 404
    stopped = 0
    for c in g.get('cells') or []:
        if c.get('job') and _safe(c['job']) and jobs.cancel(paths.jobs_dir(root) / c['job']):
            stopped += 1
    f.unlink()
    return {'deleted': True, 'cancelled': stopped}


# --- the room library and the room day ----------------------------------------------------------------------
# Room sound belongs to no phrase: every kept piece lands once at <root>/room, tagged by microphone, and every
# wake word trains on it and judges false alarms with it (minus the pieces its own phrase was said in). A room
# day is one click: a watch on every microphone you tick, for the hours you set, keeping everything.

def room_library(query=None, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    out = store.room_summary(root)
    slug = str((query or {}).get('project') or '')
    if slug:
        out['dropped_for'] = sum(1 for r in store.list_room(root, dropped=True) if slug in (r.get('drop_for') or []))
    return out


def room_delete(query=None, body=None, settings=None, **_):
    """Delete one piece (?cid=) or a whole watch's pieces (?watch=)."""
    root, err = _root(settings)
    if err:
        return err
    q = {**(query or {}), **(body or {})}
    cid, wid = str(q.get('cid') or ''), str(q.get('watch') or '')
    if not (cid or wid) or (cid and not _safe(cid)) or (wid and not _safe(wid)):
        return {'error': 'cid= or watch='}, 400
    try:
        return {'deleted': store.delete_room(root, cid=cid or None, watch=wid or None)}
    except ValueError:
        return {'error': 'bad id'}, 400


def room_day(slug, body=None, settings=None, **_):
    """Start a keeping watch on every satellite microphone named (or every online one): the room for `minutes`,
    every piece into the library, the wake word's chosen models on watch so a phrase said in the room is flagged.
    PC microphones are the page's: it starts them itself. Answers what started and what refused."""
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    body = body or {}
    try:
        minutes = max(1, min(24 * 60, int(body.get('minutes') or 360)))
    except ValueError:
        minutes = 360
    test = body.get('test', True) not in (False, 'false', 0, '0')
    want = [str(d)[:60] for d in (body.get('devices') or []) if str(d).strip()]
    sats = [m for m in mics()['mics'] if m.get('online') and (not want or m['id'] in want)]
    pdir = paths.project_dir(root, slug)
    picks = _chosen(pdir)
    runs = ','.join(dict.fromkeys([r['id'] for r in picks.values() if r] + [d.name for d in (pdir / 'runs').iterdir() if d.name.startswith('builtin-') and (d / 'run.json').is_file()] if (pdir / 'runs').is_dir() else [r['id'] for r in picks.values() if r]))
    started, refused = [], []
    for m in sats:
        if _watch.running(slug, m['id']):
            refused.append({'device': m['id'], 'error': 'already watching'})
            continue
        try:
            listen_fn, capture_fn, chunk_s, arm, disarm = _watch_fns(root, slug, m['id'], runs, settings, keep=True, test=test)
            st = _watch.start(pdir, slug, m['id'], minutes, runs, listen_fn, capture_fn, chunk_seconds=chunk_s, keep=True,
                              test=test and arm is not None, arm=arm, disarm=disarm, root=root)
            started.append({'device': m['id'], 'id': st['id'], 'pushed': st.get('pushed')})
        except Exception as e:
            refused.append({'device': m['id'], 'error': str(getattr(e, 'args', [e])[0])[:160]})
    return {'started': started, 'refused': refused, 'minutes': minutes, 'runs': runs, 'watches': _watch.watches(pdir, slug)}


def room_day_stop(slug, settings=None, **_):
    root, err = _root(settings)
    if err:
        return err
    pr, err = _project(root, slug)
    if err:
        return err
    pdir = paths.project_dir(root, slug)
    _watch.stop(pdir, slug)
    return {'watches': _watch.watches(pdir, slug)}
