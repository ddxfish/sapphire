"""Wakeword Maker, the light side: the clip store, the job protocol, the dataset catalog and the routes.
Hermetic: a tmp data folder, no GPU, no network, no plugin env. The heavy jobs (synth, qa, the trainers)
are proven by hand in tmp/wakeword-maker-plan.md; here they are only asked for through the routes."""
import asyncio
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

PLUGIN = Path(__file__).resolve().parents[1]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from wakeword_maker import catalog, jobs, paths, store   # noqa: E402
import importlib.util   # noqa: E402
import threading   # noqa: E402

_spec = importlib.util.spec_from_file_location('wwm_routes_api', PLUGIN / 'routes' / 'api.py')
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


def tone(seconds=1.0, sr=16000, freq=440.0, level=0.5, lead=0.3, tail=0.3):
    """A beep with silence either side, like a sample."""
    t = np.arange(int(sr * seconds)) / sr
    a = (np.sin(2 * np.pi * freq * t) * level).astype(np.float32)
    return np.concatenate([np.zeros(int(sr * lead), np.float32), a, np.zeros(int(sr * tail), np.float32)])


def wav_bytes(audio, sr=16000):
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format='WAV', subtype='PCM_16')
    return buf.getvalue()


@pytest.fixture
def root(tmp_path):
    return tmp_path / 'data'


@pytest.fixture
def settings(root):
    root.mkdir()
    return {'data_dir': str(root), 'device': 'cpu', 'qa_whisper_model': 'base.en'}


# --- store -----------------------------------------------------------------------------------------------

def test_slug_and_collections():
    assert paths.slug_of('Hey, Sapphire!') == 'hey_sapphire'
    assert paths.slug_of('') == 'wakeword'
    assert 'positive/uploaded' in paths.COLLECTIONS
    with pytest.raises(ValueError):
        paths.collection_dir('/x', 'positive/stolen')


def test_to_16k_and_trim():
    a48 = tone(sr=48000)
    a = store.to_16k(a48, 48000)
    assert abs(len(a) / 16000 - len(a48) / 48000) < 0.01
    cut = store.trim(a)
    assert 0.9 < len(cut) / 16000 < 1.35          # the pads stay, the half-second of silence goes


def test_project_and_clip_round_trip(root):
    root.mkdir()
    p = store.create_project(root, '  hey   sapphire ')
    assert p['phrase'] == 'hey sapphire' and p['slug'] == 'hey_sapphire'
    assert store.create_project(root, 'hey_anita')['phrase'] == 'hey anita'
    assert store.delete_project(root, 'hey_anita')
    with pytest.raises(ValueError):
        store.create_project(root, 'hey sapphire')
    pdir = paths.project_dir(root, p['slug'])
    side = store.add_clip(pdir, 'positive/recorded', tone(), {'device': 'desk', 'style': 'normal'})
    assert side['source'] == 'recorded' and side['device'] == 'desk' and side['seconds'] == 1.6
    assert (pdir / 'positive/recorded' / f"{side['id']}.wav").exists()
    rows, total = store.list_clips(pdir, 'positive/recorded')
    assert total == 1 and rows[0]['id'] == side['id']
    assert store.counts(pdir)['positive/recorded'] == 1
    store.update_side(pdir, 'positive/recorded', side['id'], verdict='drop')
    assert store.read_side(pdir / 'positive/recorded' / f"{side['id']}.json")['verdict'] == 'drop'
    assert store.delete_clip(pdir, 'positive/recorded', side['id'])
    assert store.counts(pdir)['positive/recorded'] == 0
    assert store.list_projects(root)[0]['slug'] == 'hey_sapphire'
    assert store.delete_project(root, 'hey_sapphire') and store.list_projects(root) == []


def test_take_recording_trims_and_refuses_silence(root):
    root.mkdir()
    store.create_project(root, 'hey sapphire')
    pdir = paths.project_dir(root, 'hey_sapphire')
    side = store.take_recording(pdir, 'positive/recorded', wav_bytes(tone(sr=48000), 48000), {'device': 'phone'})
    assert side['source'] == 'recorded' and 0.9 < side['seconds'] < 1.4
    with pytest.raises(ValueError):
        store.take_recording(pdir, 'positive/recorded', wav_bytes(np.zeros(16000, np.float32)), {})
    amb = store.take_recording(pdir, 'ambient/recorded', wav_bytes(tone(seconds=2.0)), {}, do_trim=True)
    assert amb['seconds'] == 2.6                      # ambient keeps its silence


def test_take_upload_zip_and_bad_files(root):
    root.mkdir()
    store.create_project(root, 'hey sapphire')
    pdir = paths.project_dir(root, 'hey_sapphire')
    z = io.BytesIO()
    with zipfile.ZipFile(z, 'w') as zf:
        zf.writestr('a.wav', wav_bytes(tone()))
        zf.writestr('sub/b.wav', wav_bytes(tone(freq=600)))
        zf.writestr('notes.txt', 'hello')
        zf.writestr('.hidden.wav', wav_bytes(tone()))
    added, skipped = store.take_upload(pdir, 'positive/uploaded', 'batch.zip', z.getvalue())
    assert len(added) == 2 and all(a['source'] == 'uploaded' for a in added) and skipped == []
    added, skipped = store.take_upload(pdir, 'positive/uploaded', 'readme.md', b'# no')
    assert added == [] and skipped[0][0] == 'readme.md'
    added, skipped = store.take_upload(pdir, 'positive/uploaded', 'broken.wav', b'RIFFjunk')
    assert added == [] and len(skipped) == 1
    assert store.counts(pdir)['positive/uploaded'] == 2


# --- jobs --------------------------------------------------------------------------------------------------

def test_progress_and_read(root):
    root.mkdir()
    jdir = paths.jobs_dir(root) / 'j1'
    jdir.mkdir(parents=True)
    (jdir / 'job.json').write_text(json.dumps({'kind': 'synth', 'args': {}, 'project': 'p', 'pid': os.getpid()}))
    pr = jobs.Progress(jdir, every=0)
    pr.step('a', 'start')
    pr.update(42, 'half', clips=3)
    j = jobs.read(jdir)
    assert j['state'] == 'running' and j['pct'] == 42 and j['metrics'] == {'clips': 3}
    pr.done({'made': 3})
    j = jobs.read(jdir)
    assert j['state'] == 'done' and j['result'] == {'made': 3}     # the epitaph stands even while the process winds down
    job = json.loads((jdir / 'job.json').read_text()); job['pid'] = 2 ** 22 + 12345
    (jdir / 'job.json').write_text(json.dumps(job))
    assert jobs.read(jdir)['state'] == 'done'


def test_recycled_pid_is_not_alive(root):
    """A finished job whose pid number the kernel handed to another process must not read as running."""
    root.mkdir()
    jdir = paths.jobs_dir(root) / 'j5'
    jdir.mkdir(parents=True)
    me = os.getpid()
    (jdir / 'job.json').write_text(json.dumps({'kind': 'train', 'args': {}, 'project': 'p', 'pid': me, 'born': 'not-my-birth'}))
    j = jobs.read(jdir)
    assert j['state'] == 'failed' and 'gone' in j['error']
    born = jobs._born(me)
    if born:                                  # with the real birth stamp the same pid is alive
        (jdir / 'job.json').write_text(json.dumps({'kind': 'train', 'args': {}, 'project': 'p', 'pid': me, 'born': born}))
        assert jobs.read(jdir)['state'] == 'running'


def test_stopped_is_interrupted_unless_cancelled(root):
    root.mkdir()
    jdir = paths.jobs_dir(root) / 'j6'
    jdir.mkdir(parents=True)
    (jdir / 'job.json').write_text(json.dumps({'kind': 'train', 'args': {}, 'project': 'p', 'pid': 2 ** 22 + 1}))
    pr = jobs.Progress(jdir, every=0)
    pr.stopped()
    assert jobs.read(jdir)['state'] == 'interrupted'
    jobs._write_status(jdir, 'cancelled')     # what cancel() writes before it signals
    pr.stopped()
    assert jobs.read(jdir)['state'] == 'cancelled'


def test_queue_runs_one_heavy_job_per_wake_word(root, monkeypatch):
    """enqueue starts the first job, queues the second behind it, and promote claims each queued job once."""
    root.mkdir()
    launched = []

    def fake_launch(job_dir, job, publish, env=None):
        launched.append(job_dir.name)
        job.update({'pid': os.getpid(), 'born': jobs._born(os.getpid()), 'started': time.time()})
        paths.write_json(job_dir / 'job.json', job)
    monkeypatch.setattr(jobs, '_launch', fake_launch)
    a = jobs.enqueue(root, 'train', {'n': 1}, sys.executable, PLUGIN, project='p')
    b = jobs.enqueue(root, 'train', {'n': 2}, sys.executable, PLUGIN, project='p')
    c = jobs.enqueue(root, 'train', {'n': 3}, sys.executable, PLUGIN, project='other')
    assert a['state'] == 'running' and b['state'] == 'queued' and c['state'] == 'running'
    assert launched == [a['id'], c['id']]
    assert jobs.promote(root) == []           # p is busy: nothing more starts
    assert jobs.cancel(paths.jobs_dir(root) / b['id']) is True
    assert jobs.read(paths.jobs_dir(root) / b['id'])['state'] == 'cancelled'
    jobs._write_status(paths.jobs_dir(root) / a['id'], 'done')
    d = jobs.enqueue(root, 'train', {'n': 4}, sys.executable, PLUGIN, project='p')
    assert d['state'] == 'running'            # a's epitaph stands, so p is free again


def test_prune_keeps_live_and_newest(root):
    root.mkdir()
    for i in range(6):
        d = paths.jobs_dir(root) / f"j-{i}"
        d.mkdir(parents=True)
        (d / 'job.json').write_text(json.dumps({'kind': 'train', 'args': {}, 'project': 'p', 'pid': None, 'queued_at': i}))
        (d / 'status.json').write_text(json.dumps({'state': 'queued' if i == 0 else 'done'}))
    assert jobs.prune(root, keep=2) == 3
    left = sorted(e.name for e in os.scandir(paths.jobs_dir(root)))
    assert left == ['j-0', 'j-4', 'j-5']      # the queued one stays whatever its age


def test_only_the_owner_writes_status(root):
    root.mkdir()
    jdir = paths.jobs_dir(root) / 'j3'
    jdir.mkdir(parents=True)
    (jdir / 'job.json').write_text(json.dumps({'kind': 'synth', 'args': {}, 'project': 'p', 'pid': 2 ** 22 + 12345}))
    pr = jobs.Progress(jdir, every=0)
    pr.pid = os.getpid() + 1                # what a forked Pool worker sees: not the owner
    pr.cancelled(); pr.done({}); pr.fail('x')
    assert not (jdir / 'status.json').exists()
    pr.pid = os.getpid()
    pr.done({'ok': 1})
    assert jobs.read(jdir)['state'] == 'done'


def test_list_all_keeps_live_jobs_and_sorts_by_time(root):
    root.mkdir()
    for i, (name, started, state) in enumerate([('x-2', 10, 'done'), ('x-10', 20, 'done'), ('x-11', 30, 'done'), ('x-3', 5, 'queued')]):
        d = paths.jobs_dir(root) / name
        d.mkdir(parents=True)
        job = {'kind': 'train', 'args': {}, 'project': 'p', 'pid': None if state == 'queued' else 2 ** 22 + i, 'started': None if state == 'queued' else started, 'queued_at': started}
        (d / 'job.json').write_text(json.dumps(job))
        (d / 'status.json').write_text(json.dumps({'state': state}))
    got = jobs.list_all(root, limit=1)
    assert [j['id'] for j in got] == ['x-11', 'x-3']     # newest finished one, plus every live job, newest first


def test_dead_process_without_status_reads_as_failed(root):
    root.mkdir()
    jdir = paths.jobs_dir(root) / 'j2'
    jdir.mkdir(parents=True)
    (jdir / 'job.json').write_text(json.dumps({'kind': 'qa', 'args': {}, 'project': 'p', 'pid': 2 ** 22 + 12345}))
    j = jobs.read(jdir)
    assert j['state'] == 'failed' and 'gone' in j['error']


def test_start_runs_a_real_job_and_cancel(root, tmp_path):
    """A job process under this interpreter: the unknown kind fails cleanly through the protocol."""
    root.mkdir()
    events = []
    j = jobs.start(root, 'no_such_kind', {}, sys.executable, PLUGIN, project=None, publish=lambda e, d: events.append(d))
    deadline = time.time() + 20
    while time.time() < deadline and jobs.read(paths.jobs_dir(root) / j['id'])['state'] == 'running':
        time.sleep(0.2)
    j = jobs.read(paths.jobs_dir(root) / j['id'])
    assert j['state'] == 'failed' and 'unknown job kind' in j['error']
    assert jobs.list_all(root)[0]['id'] == j['id']
    assert jobs.cancel(paths.jobs_dir(root) / j['id']) is False     # already over


def test_cancel_a_running_job_keeps_cancelled(root):
    """cancel writes the epitaph, then signals; the child sees it and leaves it alone (a bare SIGTERM would say interrupted)."""
    root.mkdir()
    j = jobs.start(root, 'nap', {'seconds': 20}, sys.executable, PLUGIN, project='p')
    jdir = paths.jobs_dir(root) / j['id']
    deadline = time.time() + 10
    while time.time() < deadline and jobs.read(jdir)['pct'] == 0:
        time.sleep(0.1)
    assert jobs.read(jdir)['state'] == 'running' and jobs.read(jdir)['born']
    assert jobs.cancel(jdir) is True
    deadline = time.time() + 10
    while time.time() < deadline and jobs._alive(j['pid'], j['born']):
        time.sleep(0.1)
    assert not jobs._alive(j['pid'], j['born'])
    assert jobs.read(jdir)['state'] == 'cancelled'
    jobs._write_status(jdir, 'interrupted')
    again = jobs.retry(root, jdir)
    assert again and again['kind'] == 'nap' and again['id'] != j['id'] and again['state'] == 'running'
    jobs.cancel(paths.jobs_dir(root) / again['id'])


# --- catalog -------------------------------------------------------------------------------------------------

def test_catalog_states(root):
    root.mkdir()
    st = {d['id']: d for d in catalog.status(root)}
    assert st['oww_negatives']['state'] == 'missing' and st['oww_negatives']['bytes'] > 17e9
    assert all('licence' in d and d['licence_url'] for d in st.values())
    d = catalog.dataset_dir(root, 'rirs')
    d.mkdir(parents=True)
    (d / 'x.wav').write_bytes(b'0' * 100)
    assert catalog.status(root)[[x['id'] for x in catalog.DATASETS].index('rirs')]['state'] == 'partial'
    catalog.write_receipt(root, 'rirs', [{'name': 'x.wav', 'bytes': 100}])
    assert catalog.receipt(root, 'rirs')['bytes'] == 100
    assert {d['id']: d for d in catalog.status(root)}['rirs']['state'] == 'ready'


# --- routes -----------------------------------------------------------------------------------------------

def test_default_root_is_made_and_a_vanished_choice_is_an_error(tmp_path, monkeypatch):
    """No setting = <sapphire>/wakeword, created on first use. A folder the user chose that is gone is a plain error."""
    monkeypatch.setattr(paths, 'DEFAULT_ROOT', tmp_path / 'wakeword')
    assert not (tmp_path / 'wakeword').exists()
    out = api.list_projects(settings={'data_dir': ''})
    assert out == {'projects': []} and (tmp_path / 'wakeword').is_dir()
    st = api.status(settings={'data_dir': ''})
    assert st['root_ok'] and st['default_dir'] and st['data_dir'] == str(tmp_path / 'wakeword')
    body, code = api.list_projects(settings={'data_dir': '/no/such/dir/anywhere'})
    assert code == 409 and body['code'] == 'no_root'
    st = api.status(settings={'data_dir': '/no/such/dir/anywhere'})
    assert not st['root_ok'] and 'does not exist' in st['root_error']


def test_check_dir(tmp_path):
    r = api.check_dir(body={'path': str(tmp_path / 'new')})
    assert r['exists'] is False and r['can_create'] is True
    r = api.check_dir(body={'path': str(tmp_path / 'new'), 'create': True})
    assert r['ok'] and r['empty']
    f = tmp_path / 'file.txt'
    f.write_text('x')
    assert api.check_dir(body={'path': str(f)})['ok'] is False
    assert api.check_dir(body={})[1] == 400


def test_project_routes(settings, root):
    r = api.create_project(body={'phrase': 'hey sapphire'}, settings=settings)
    assert r['project']['slug'] == 'hey_sapphire' and r['project']['counts']['positive/synth'] == 0
    assert api.create_project(body={'phrase': 'hey sapphire'}, settings=settings)[1] == 400
    r = api.update_project('hey_sapphire', body={'spellings': ['hey sapphire', ' hey  saffire '], 'negatives': ['hey sophia'],
                                                 'settings': {'synth': {'target': 500}}}, settings=settings)
    assert r['project']['spellings'] == ['hey sapphire', 'hey saffire'] and r['project']['settings']['synth']['target'] == 500
    assert api.get_project('nope', settings=settings)[1] == 404
    r = api.get_project('hey_sapphire', settings=settings)
    assert r['project']['jobs'] == []
    pg = r['project']['progress']
    assert pg['pct'] == 10 and pg['step'] == 'voices' and pg['next'] == 'Make the set' and pg['index'] == 1 and pg['of'] == 6
    assert api.list_projects(settings=settings)['projects'][0]['progress']['pct'] == 10
    r = api.update_project('hey_sapphire', body={'steps': {'voices': True, 'bogus': True}}, settings=settings)
    assert r['project']['progress']['step'] == 'record' and r['project']['progress']['pct'] == 25 and 'bogus' not in r['project']['steps']
    r = api.update_project('hey_sapphire', body={'steps': {'voices': False}}, settings=settings)
    assert r['project']['progress']['step'] == 'voices'
    assert api.status(settings=settings)['root_ok'] is True
    assert api.delete_project('hey_sapphire', settings=settings)['deleted'] is True


class _Upload:
    def __init__(self, name, data):
        self.filename, self._data = name, data

    async def read(self):
        return self._data


class _Form:
    def __init__(self, fields, files):
        self._fields, self._files = fields, files

    def get(self, k, default=None):
        return self._fields.get(k, default)

    def multi_items(self):
        return list(self._fields.items()) + self._files


class _Request:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


def test_record_and_upload_routes(settings, root):
    api.create_project(body={'phrase': 'hey sapphire'}, settings=settings)
    req = _Request(_Form({'collection': 'positive/recorded', 'device': 'phone', 'style': 'fast', 'audio': _Upload('s.wav', wav_bytes(tone(sr=44100), 44100))}, []))
    r = asyncio.run(api.record('hey_sapphire', request=req, settings=settings))
    assert r['clip']['device'] == 'phone' and r['clip']['via'] == 'browser' and r['counts']['positive/recorded'] == 1
    bad = _Request(_Form({'collection': 'positive/synth', 'audio': _Upload('s.wav', b'')}, []))
    assert asyncio.run(api.record('hey_sapphire', request=bad, settings=settings))[1] == 400
    silent = _Request(_Form({'collection': 'negative/recorded', 'audio': _Upload('s.wav', wav_bytes(np.zeros(8000, np.float32)))}, []))
    assert asyncio.run(api.record('hey_sapphire', request=silent, settings=settings))[1] == 422
    up = _Request(_Form({'collection': 'negative/uploaded'}, [('files', _Upload('a.wav', wav_bytes(tone()))), ('files', _Upload('b.txt', b'x'))]))
    r = asyncio.run(api.upload('hey_sapphire', request=up, settings=settings))
    assert r['added'] == 1 and r['skipped'][0]['name'] == 'b.txt' and r['counts']['negative/uploaded'] == 1
    r = api.list_clips('hey_sapphire', query={'collection': 'negative/uploaded'}, settings=settings)
    cid = r['clips'][0]['id']
    assert r['total'] == 1 and r['clips'][0]['source'] == 'uploaded'
    resp = api.clip_audio('hey_sapphire', 'negative', 'uploaded', cid, settings=settings)
    assert getattr(resp, 'media_type', '') == 'audio/wav'
    r = api.tag_clip('hey_sapphire', 'negative', 'uploaded', cid, body={'verdict': 'keep'}, settings=settings)
    assert r['clip']['verdict'] == 'keep' and r['clip']['verdict_by'] == 'user'
    assert api.clip_audio('hey_sapphire', 'negative', 'stolen', cid, settings=settings)[1] == 404
    assert api.delete_clip('hey_sapphire', 'negative', 'uploaded', cid, settings=settings)['deleted'] is True
    assert api.list_clips('hey_sapphire', query={'collection': 'nope'}, settings=settings)[1] == 400


def test_job_routes_without_env(settings, root, monkeypatch):
    api.create_project(body={'phrase': 'hey sapphire'}, settings=settings)
    monkeypatch.setattr(api, '_env', lambda: {'state': 'missing', 'python': None})
    body, code = api.start_job(body={'kind': 'synth', 'project': 'hey_sapphire'}, settings=settings)
    assert code == 409 and body['code'] == 'no_env'
    assert api.start_job(body={'kind': 'bogus'}, settings=settings)[1] == 400
    assert api.download_dataset('nope', settings=settings)[1] == 404
    # light kinds fall back to Sapphire's own interpreter
    started = {}
    monkeypatch.setattr(api.jobs, 'start', lambda root, kind, args, py, pdir, project=None, publish=None: started.update(kind=kind, py=py) or {'id': 'x'})
    api.download_dataset('rirs', settings=settings)
    assert started == {'kind': 'download', 'py': sys.executable}
    assert api.list_jobs(settings=settings)['jobs'] == []
    assert api.get_job('nope', settings=settings)[1] == 404
    assert api.cancel_job('../x', settings=settings)[1] == 400


def test_voices_and_qa_report(settings):
    v = api.voice_catalog()
    assert any(p['name'] == 'en_US-libritts_r-medium' and p['speakers'] == 904 for p in v['piper'])
    assert 'af_heart' in v['kokoro']['english'] and v['presets']['standard'] == 20000
    api.create_project(body={'phrase': 'hey sapphire'}, settings=settings)
    assert api.qa_report('hey_sapphire', settings=settings) == {'summary': None, 'worst': []}


def test_project_in_its_own_folder(settings, root, tmp_path):
    other = tmp_path / 'elsewhere'
    r = api.create_project(body={'phrase': 'hey nova', 'folder': str(other) + '/'}, settings=settings)
    assert r['project']['folder'] == str(other / 'hey_nova') and paths.projects_index(root) == {'hey_nova': str(other / 'hey_nova')}
    assert (other / 'hey_nova' / 'project.json').is_file()
    assert [p['slug'] for p in store.list_projects(root)] == ['hey_nova']
    r = api.update_project('hey_nova', body={'folder': str(tmp_path / 'moved')}, settings=settings)
    assert r['project']['folder'] == str(tmp_path / 'moved') and (tmp_path / 'moved' / 'project.json').is_file() and not (other / 'hey_nova').exists()
    st = api.storage(settings=settings)
    assert st['ok'] and st['projects'][0]['folder'] == str(tmp_path / 'moved') and st['projects'][0]['default'] is False
    assert api.delete_project('hey_nova', settings=settings)['deleted'] and paths.projects_index(root) == {}
    assert api.create_project(body={'phrase': 'hey nova', 'folder': str(tmp_path / 'full')}, settings=settings)['project']['folder'] == str(tmp_path / 'full')


def test_a_typed_folder_must_be_empty_and_delete_never_eats_a_stranger(settings, root, tmp_path):
    """The folder a user types becomes the project's whole folder, so delete may clear it: only an empty or new one
    qualifies, never the data folder, a folder above it, or one with their files in it."""
    mine = tmp_path / 'Recordings'
    mine.mkdir()
    (mine / 'precious.wav').write_bytes(b'x')
    for bad in (str(mine), str(root), str(root.parent), str(root / 'datasets' / 'x')):
        r = api.create_project(body={'phrase': 'hey nova', 'folder': bad}, settings=settings)
        assert isinstance(r, tuple) and r[1] == 400, bad
    assert (mine / 'precious.wav').exists() and not paths.projects_index(root)
    empty = tmp_path / 'empty'
    empty.mkdir()
    r = api.create_project(body={'phrase': 'hey nova', 'folder': str(empty)}, settings=settings)
    assert r['project']['folder'] == str(empty)
    r = api.update_project('hey_nova', body={'folder': str(mine)}, settings=settings)        # a move into their files: refused
    assert isinstance(r, tuple) and r[1] == 400 and (empty / 'project.json').is_file()
    also_empty = tmp_path / 'also_empty'
    also_empty.mkdir()
    r = api.update_project('hey_nova', body={'folder': str(also_empty)}, settings=settings)  # into an existing empty one: lands there, not inside it
    assert (also_empty / 'project.json').is_file() and not (also_empty / 'hey_nova').exists()
    assert api.delete_project('hey_nova', settings=settings)['deleted'] and not also_empty.exists() and mine.exists()


# --- judge -------------------------------------------------------------------------------------------------

def test_judge_summary_picks_a_clean_high_threshold():
    from wakeword_maker import judge
    rows = [{'kind': 'positive', 'score': s} for s in (1.0, 1.0, 0.95, 0.55)]
    rows += [{'kind': 'negative', 'near': True, 'score': s} for s in (0.45, 0.1, 0.0)]
    rows += [{'kind': 'negative', 'near': False, 'score': s} for s in (0.35, 0.0)]
    v = judge.summary(rows, [{'threshold': 0.5, 'fp_per_hour': 0.1}, {'threshold': 0.4, 'fp_per_hour': 0.9}])
    # 0.3: an other negative fires. 0.4: std FA too high. 0.5: 4/4, one sound-alike under it, clean -> the pick
    assert v['threshold'] == 0.5 and v['recall'] == 1.0 and v['near_fires'] == 0 and v['neg_fires'] == 0 and v['clean']
    assert v['n_pos'] == 4 and v['n_near'] == 3 and v['n_neg'] == 2 and len(v['table']) == len(judge.GRID)
    # nothing clean: the best recall with the fewest sound-alikes still gets picked, flagged not clean
    v2 = judge.summary([{'kind': 'positive', 'score': 0.9}, {'kind': 'negative', 'near': False, 'score': 0.99}])
    assert v2['threshold'] == 0.5 and not v2['clean']      # nothing clean: best recall, nearest the usual point
    # a plateau: the pick sits at the detector's usual point, not at the top of the grid
    flat = [{'kind': 'positive', 'score': 1.0}] * 3 + [{'kind': 'negative', 'near': True, 'score': 0.0}]
    assert judge.summary(flat, trainer='oww')['threshold'] == 0.5 and judge.summary(flat, trainer='mww')['threshold'] == 0.97


# --- install -----------------------------------------------------------------------------------------------

def test_chosen_prefers_the_star_then_the_judge(root):
    root.mkdir()
    pr = store.create_project(root, 'hey marcus')
    pdir = paths.project_dir(root, pr['slug'])
    def run(rid, trainer, star=False, recall=0.9, near=0, model='model.onnx'):
        d = pdir / 'runs' / rid
        d.mkdir(parents=True)
        (d / model).write_bytes(b'x')
        (d / 'run.json').write_text(json.dumps({'id': rid, 'trainer': trainer, 'state': 'done', 'chosen': star, 'heldout': {'recall': recall, 'near_fires': near, 'n_near': 10, 'threshold': 0.5}}))
    run('a-oww', 'oww', recall=0.8); run('b-oww', 'oww', recall=0.95, near=2); run('c-oww', 'oww', recall=0.9, star=True)
    run('d-mww', 'mww', recall=0.7, model='model.tflite'); run('e-mww', 'mww', recall=0.9, model='model.tflite')
    picks = api._chosen(pdir)
    assert picks['oww']['id'] == 'c-oww' and picks['mww']['id'] == 'e-mww'
    st = api.install_status(pr['slug'], settings={'data_dir': str(root)})
    assert st['picks']['oww']['id'] == 'c-oww' and st['picks']['mww']['threshold'] == 0.5 and 'desktop' in st
    r = api.run_file(pr['slug'], 'c-oww', 'manifest', settings={'data_dir': str(root)})
    assert json.loads(r.body)['name'] == pr['slug'] and json.loads(r.body)['framework'] == 'onnx'
    body, code = api.run_file(pr['slug'], 'c-oww', 'model.tflite', settings={'data_dir': str(root)})
    assert code == 404


def test_chosen_follows_the_judge_clean_first_and_skips_stale(root):
    root.mkdir()
    pr = store.create_project(root, 'hey nova')
    pdir = paths.project_dir(root, pr['slug'])
    def run(rid, recall, near=0, clean=True, stale=False):
        d = pdir / 'runs' / rid
        d.mkdir(parents=True)
        (d / 'model.onnx').write_bytes(b'x')
        (d / 'run.json').write_text(json.dumps({'id': rid, 'trainer': 'oww', 'state': 'done', 'heldout': {'recall': recall, 'near_fires': near, 'n_near': 10, 'threshold': 0.5, 'clean': clean, 'neg_fires': 0 if clean else 2, 'stale': stale}}))
    run('flawed-oww', 1.0, clean=False)        # best recall, but no threshold met the rule
    run('clean-oww', 0.9)
    run('fresh-but-old-slice-oww', 0.99, stale=True)
    assert api._chosen(pdir)['oww']['id'] == 'clean-oww'


def test_loads_ours_follows_core_resolution(tmp_path, monkeypatch):
    """Core loads user/wakeword/models first, so our file under a shipped name (hey_sapphire) is the one that listens."""
    import core.wakeword as ww
    user_dir = tmp_path / 'models'
    user_dir.mkdir()
    monkeypatch.setattr(ww, 'USER_MODELS_DIR', user_dir)
    monkeypatch.setattr(api, 'USER_MODELS', user_dir)
    assert api._loads_ours('hey_sapphire') is False        # nothing of ours yet: core's own file
    (user_dir / 'hey_sapphire.onnx').write_bytes(b'x')
    assert api._loads_ours('hey_sapphire') is True
    assert api._loads_ours('hey_jarvis') is False          # a built-in name with no file of ours


def test_judge_summary_without_positives_says_so():
    from wakeword_maker import judge
    rows = [{'kind': 'negative', 'near': True, 'score': 0.2}, {'kind': 'negative', 'near': False, 'score': 0.1}]
    v = judge.summary(rows, [], 'oww')
    assert v['recall'] is None and v['threshold'] is None and v['n_pos'] == 0


# --- room watch: pieces a board pushes (capture.c, firmware 0.7.6) ---------------------------------------

def test_watch_keeps_pushed_pieces_and_counts_the_boards_fires(root):
    from wakeword_maker import watch
    root.mkdir()
    p = store.create_project(root, 'hey test')
    pdir = paths.project_dir(root, p['slug'])
    armed = []
    listen_fn = lambda wav: {'r1': {'peaks': [(2.0, 0.3)], 'threshold': 0.5}}      # the candidate on the PC: a weak peak at 2 s
    st = watch.start(pdir, p['slug'], 'board1', 1, 'r1', listen_fn, capture_fn=lambda s: watch.take('board1', timeout=s),
                     chunk_seconds=5, keep=True, test=True, arm=lambda seconds: armed.append(seconds), disarm=lambda: armed.append(0))
    assert st['pushed'] and st['keep'] and st['test'] and armed == [60]
    assert watch.sink('nobody', wav_bytes(tone(3.0)), {})['accepted'] is False         # no watch on that device: the board stops
    rep = watch.sink('board1', wav_bytes(tone(6.0)), {'chunk': 3, 'fires': [[2.1, 0.98]], 'fire_count': 1, 'test': True, 'model': 'hey_test'})
    assert rep['accepted'] is True
    for _ in range(100):
        if store.counts(pdir).get('ambient/recorded'):
            break
        time.sleep(0.1)
    watch.stop(pdir, p['slug'], device='board1')
    watch._live[(p['slug'], 'board1')]['thread'].join(5)
    assert armed[-1] == 0                                                              # the board was told to stop
    st = watch.read(pdir, st['id'])
    assert st['kept'] == 1 and store.counts(pdir)['ambient/recorded'] == 1 and st['ended']
    side = store.list_clips(pdir, 'ambient/recorded', 0, 10)[0][0]
    assert side['via'] == 'capture' and side['watch'] == st['id'] and side['piece'] == 3 and side['board_fires'] == 1
    assert st['board'] == 'hey_test' and st['board_fires'] == 1
    assert len(st['fires']) == 1
    by = st['fires'][0]['runs']
    assert by['board:hey_test'] == {'score': 0.98, 'fires': True}
    assert by['r1']['fires'] is False and by['r1']['score'] == 0.3                    # the same moment, both verdicts side by side
    assert (pdir / 'watch' / st['id'] / f"{st['fires'][0]['id']}.wav").is_file()


def test_watch_sink_refuses_a_bad_piece_and_pause_frees_a_take(root):
    from wakeword_maker import watch
    root.mkdir()
    p = store.create_project(root, 'hey pause')
    pdir = paths.project_dir(root, p['slug'])
    st = watch.start(pdir, p['slug'], 'board2', 1, 'r1', lambda wav: {}, capture_fn=lambda s: watch.take('board2', timeout=s),
                     chunk_seconds=5, arm=lambda seconds: None, disarm=lambda: None)
    assert watch.sink('board2', b'not a wav', {})['ok'] is False
    watch.pause_all()
    watch._live[(p['slug'], 'board2')]['thread'].join(5)
    assert not watch._live[(p['slug'], 'board2')]['thread'].is_alive()
    assert watch.read(pdir, st['id']).get('paused')


# --- the bench: stress rows, margins, the room pool, the ESP32 trainer's onset -----------------------------

def test_judge_summary_counts_stress_and_measures_the_gap():
    from wakeword_maker import judge
    rows = [{'kind': 'positive', 'score': s} for s in (1.0, 0.9, 0.8)]
    rows += [{'kind': 'negative', 'near': True, 'score': 0.4}, {'kind': 'negative', 'near': False, 'score': 0.2}]
    rows += [{'kind': 'stress', 'score': s} for s in (0.95, 0.6, 0.45, 0.1)]
    v = judge.summary(rows, [], 'oww')
    assert v['n_stress'] == 4 and v['threshold'] == 0.5
    assert v['stress_recall'] == 0.5                                   # 0.95 and 0.6 clear 0.5; 0.45 and 0.1 do not
    assert next(x for x in v['table'] if x['threshold'] == 0.3)['stress_recall'] == 0.75
    m = v['margin']
    assert m['min_pos'] == 0.8 and m['max_near'] == 0.4 and m['max_neg'] == 0.2 and m['stress_min'] == 0.1
    assert abs(m['gap'] - 0.4) < 1e-9
    # on a tie in recall and sound-alikes, the better stress recall wins over the usual-point rule
    flat = [{'kind': 'positive', 'score': 1.0}] * 3 + [{'kind': 'negative', 'near': True, 'score': 0.0}]
    flat += [{'kind': 'stress', 'score': 0.35}] * 4
    assert judge.summary(flat, trainer='oww')['threshold'] == 0.3     # 0.3 hears every stressed draw, 0.5 none
    assert judge.summary([{'kind': 'positive', 'score': 1.0}], trainer='oww')['stress_recall'] is None


def test_mww_detections_ignore_the_onset():
    from wakeword_maker.train_mww import _detections
    probs = np.zeros(400)
    probs[:6] = 1.0                                   # the silence-to-room edge after a reset: one hot window at the start
    assert _detections(probs, 0.9, 5, 0) == 1         # counted when nothing settles: the phantom every ESP32 run reported
    assert _detections(probs, 0.9, 5, 100 // 3) == 0  # the board ignores its first 100 slices; so does the judge now
    probs[200:210] = 1.0
    assert _detections(probs, 0.9, 5, 100 // 3) == 1


def test_augment_pools_can_be_limited_to_named_room_takes(root):
    from wakeword_maker import augment
    root.mkdir()
    p = store.create_project(root, 'hey pool')
    pdir = paths.project_dir(root, p['slug'])
    a = store.add_clip(pdir, 'ambient/recorded', tone(2.0), {'device': 'x'})
    b = store.add_clip(pdir, 'ambient/recorded', tone(2.0), {'device': 'x'})
    assert len(augment.pools(root, pdir)['ambient']) == 2
    only = augment.pools(root, pdir, {a['id']})['ambient']
    assert len(only) == 1 and only[0].endswith(f"{a['id']}.wav")
    assert augment.pools(root, pdir, set())['ambient'] == []


def test_daemon_finds_the_resume_door_in_a_route_row_of_any_shape():
    spec = importlib.util.spec_from_file_location('wwm_daemon', PLUGIN / 'daemon.py')
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
    calls = []
    ns = {'watch_resume': lambda settings: calls.append(settings) or (['hey_x: board (w1)'], False)}
    handler = eval('lambda: None', ns)                       # a handler whose globals are the routes namespace
    loader = SimpleNamespace(_routes={'wakeword-maker': [('GET', None, [], handler, {'opts': True})]})   # the loader's row: options last
    assert daemon._resume_door(loader) is ns['watch_resume']
    assert daemon._resume_door(SimpleNamespace(_routes={'wakeword-maker': [('GET', None, [], {}, {})]})) is None
    daemon._resume_watches(loader, {'data_dir': 'x'})
    assert calls == [{'data_dir': 'x'}]


# --- sources: what a run trains on, as one block --------------------------------------------------------

def test_sources_block_picks_what_trains_and_never_touches_the_held_out(root):
    from wakeword_maker import features, holdout
    root.mkdir()
    p = store.create_project(root, 'hey src')
    pdir = paths.project_dir(root, p['slug'])
    syn_p = store.add_clip(pdir, 'positive/synth', tone(1.0), {'source': 'synth', 'engine': 'piper', 'voice': 'v1'})
    syn_k = store.add_clip(pdir, 'positive/synth', tone(1.0), {'source': 'synth', 'engine': 'kokoro', 'voice': 'af_x'})
    own_a = store.add_clip(pdir, 'positive/recorded', tone(1.0), {'device': 'hyperx', 'style': 'normal'})
    own_b = store.add_clip(pdir, 'positive/recorded', tone(1.0), {'device': 'esp32', 'style': 'fast'})
    near = store.add_clip(pdir, 'negative/recorded', tone(1.0), {'device': 'hyperx', 'near': True})
    mined = store.add_clip(pdir, 'negative/mined', tone(1.0), {'source': 'mined'})
    held = {own_b['id']}
    ids = lambda items: {Path(w).stem for w, _ in items}
    s = features._clips(pdir, held)                                   # the old call: everything
    assert ids(s['positive']) == {syn_p['id'], syn_k['id'], own_a['id']} and ids(s['val_positive']) == {own_b['id']}
    assert dict((Path(w).stem, n) for w, n in s['positive'])[own_a['id']] == features.OWN_REPEAT
    assert ids(s['adversarial']) == {near['id'], mined['id']}
    s = features._clips(pdir, held, sources={'synth': 'piper', 'human': 'none', 'mined': False})
    assert ids(s['positive']) == {syn_p['id']} and ids(s['val_positive']) == {own_b['id']}    # held out judges regardless
    assert ids(s['adversarial']) == {near['id']}                        # your own negatives are always in
    s = features._clips(pdir, set(), sources={'devices': ['esp32'], 'own_repeat': 3})
    assert ids(s['positive']) == {syn_p['id'], syn_k['id'], own_b['id']}
    assert dict((Path(w).stem, n) for w, n in s['positive'])[own_b['id']] == 3
    s = features._clips(pdir, set(), sources={'styles': ['fast']})
    assert own_a['id'] not in ids(s['positive']) and own_b['id'] in ids(s['positive'])
    src = features.sources_of({'synth': 'kokoro'})                     # the old single knob still rides
    assert src['synth'] == 'kokoro' and src['human'] == 'all' and src['own_repeat'] == features.OWN_REPEAT
    assert features.sources_label(src) == 'kokoro only'
    assert features.sources_label(features.sources_of({'sources': {'human': 'none', 'own_repeat': 16, 'mined': False}})) == 'no own takes · own x16 · no mined'
    assert features.sources_label(features.sources_of({})) == 'all'
    a = features.key_of(p, {'preset': 'natural'}, s, 2, True, features.sources_of({}))
    b = features.key_of(p, {'preset': 'natural'}, s, 2, True, features.sources_of({'sources': {'own_repeat': 16}}))
    assert a != b                                                       # a different weight is a different feature set


# --- the grid ---------------------------------------------------------------------------------------------

def test_grid_expands_the_axes_queues_every_cell_and_pivots(settings, root, monkeypatch):
    api.create_project(body={'phrase': 'hey grid'}, settings=settings)
    pdir = paths.project_dir(root, 'hey_grid')
    store.add_clip(pdir, 'positive/recorded', tone(1.0), {'device': 'hyperx', 'style': 'normal'})
    store.add_clip(pdir, 'positive/recorded', tone(1.0), {'device': 'esp32', 'style': 'fast'})
    opts = api.sources_options('hey_grid', settings=settings)
    assert opts['devices'] == ['esp32', 'hyperx'] and opts['styles'] == ['fast', 'normal']
    names = {p['name']: p for p in opts['presets']}
    assert names['without esp32']['sources'] == {'devices': ['hyperx']} and names['piper only']['label'] == 'piper only'
    monkeypatch.setattr(api, '_python_for', lambda kind: (sys.executable, None))
    queued = []
    monkeypatch.setattr(api.jobs, 'enqueue', lambda root, kind, args, py, pdir, project=None, publish=None: queued.append((kind, args)) or {'id': f"j{len(queued)}"})
    body = {'trainers': ['oww', 'mww'], 'recipes': ['gentle', 'natural'], 'sources': ['all', 'piper only', 'without esp32'], 'presets': ['standard'],
            'knobs': {'oww': ['rnn'], 'mww': ['default', 'more room']}, 'seeds': 2, 'label': 'g'}
    r = api.grid_start('hey_grid', body=body, settings=settings)
    g = r['grid']
    assert g['of'] == 36 and len(queued) == 36                       # 2 seeds x (oww 1x2x3 + mww 2x2x3)
    assert [k for k, _ in queued][:6] == ['train'] * 6               # seed 1, oww first, then the ESP32 cells
    from collections import Counter
    kinds = Counter(k for k, _ in queued)
    assert kinds == {'train': 12, 'train_mww': 24}
    first = queued[0][1]
    assert first['grid'] == g['id'] and first['cfg'] == {'model_type': 'rnn', 'seed': 1} and first['label'] == 'g · gentle · all · standard · rnn · s1'
    assert any(a['sources'] == {'devices': ['hyperx']} for _, a in queued)
    assert any(a['cfg'].get('sampling_room') == 6.0 for k, a in queued if k == 'train_mww')
    assert [c['seed'] for c in g['cells']][:18] == [1] * 18 and g['cells'][0]['state'] == 'gone'   # no real jobs in this test
    lst = api.grids_list('hey_grid', settings=settings)['grids']
    assert lst[0]['id'] == g['id'] and lst[0]['of'] == 36
    assert api.grid_get('hey_grid', g['id'], settings=settings)['grid']['of'] == 36
    assert api.grid_get('hey_grid', 'nope', settings=settings)[1] == 404
    assert api.grid_start('hey_grid', body={**body, 'seeds': 5, 'presets': ['quick', 'standard', 'thorough'], 'recipes': list(api.augment.STOPS)}, settings=settings)[1] == 400
    assert api.grid_start('hey_grid', body={'trainers': ['bogus']}, settings=settings)[1] == 400
    assert api.grid_delete('hey_grid', g['id'], settings=settings)['deleted'] is True
    assert api.grids_list('hey_grid', settings=settings)['grids'] == []
    # the pivot folds the judged cells by every axis that varies
    cells = [{'trainer': 'oww', 'recipe': 'gentle', 'sources': 'all', 'seed': 1, 'recall': 1.0, 'stress': 0.8, 'near': 0, 'gap': 0.4, 'fp': 0.0},
             {'trainer': 'oww', 'recipe': 'rough', 'sources': 'all', 'seed': 1, 'recall': 0.9, 'stress': 0.9, 'near': 2, 'gap': -0.1, 'fp': 0.2},
             {'trainer': 'oww', 'recipe': 'rough', 'sources': 'all', 'seed': 2, 'recall': None}]
    pv = api._pivot(cells)
    assert 'trainer' not in pv and 'sources' not in pv and set(pv) == {'recipe', 'seed'}
    assert pv['recipe']['gentle'] == {'n': 1, 'of': 1, 'recall': 1.0, 'recall_min': 1.0, 'stress': 0.8, 'near': 0.0, 'gap': 0.4, 'fp': 0.0}
    assert pv['recipe']['rough']['n'] == 1 and pv['recipe']['rough']['of'] == 2 and pv['recipe']['rough']['recall'] == 0.9
    assert pv['seed']['2'] == {'n': 0, 'of': 1, 'recall': None, 'recall_min': None, 'stress': None, 'near': None, 'gap': None, 'fp': None}


def test_one_watch_per_microphone_two_at_once_and_each_stops_alone(root):
    from wakeword_maker import watch
    root.mkdir()
    p = store.create_project(root, 'hey two')
    pdir = paths.project_dir(root, p['slug'])
    quiet = lambda wav: {}
    a = watch.start(pdir, p['slug'], 'boardA', 1, 'r1', quiet, capture_fn=lambda s: watch.take('boardA', timeout=s), chunk_seconds=5, keep=True, arm=lambda s: None, disarm=lambda: None)
    b = watch.start(pdir, p['slug'], 'boardB', 1, 'r1', quiet, capture_fn=lambda s: watch.take('boardB', timeout=s), chunk_seconds=5, keep=True, arm=lambda s: None, disarm=lambda: None)
    assert a['id'] != b['id'] and watch.running(p['slug'], 'boardA') and watch.running(p['slug'], 'boardB') and watch.running(p['slug'])
    with pytest.raises(RuntimeError):
        watch.start(pdir, p['slug'], 'boardA', 1, 'r1', quiet, capture_fn=lambda s: None, arm=lambda s: None)   # that mic is taken
    c = watch.start(pdir, p['slug'], 'browser:HyperX', 1, 'r1', quiet, keep=True)                               # a PC mic rides alongside
    assert watch.live_for(pdir, p['slug'], 'browser:HyperX')['id'] == c['id'] and watch.live_for(pdir, p['slug'], 'nobody') is None
    assert watch.sink('boardB', wav_bytes(tone(4.0)), {'chunk': 1})['accepted'] is True
    assert watch.sink('boardA', wav_bytes(tone(4.0)), {'chunk': 1})['accepted'] is True
    for _ in range(100):
        if (store.counts(pdir).get('ambient/recorded') or 0) >= 2:
            break
        time.sleep(0.1)
    sides = store.list_clips(pdir, 'ambient/recorded', 0, 10)[0]
    assert {x['device'] for x in sides} == {'boardA', 'boardB'} and {x['watch'] for x in sides} == {a['id'], b['id']}   # each piece to its own watch
    live = [w['device'] for w in watch.watches(pdir, p['slug']) if w['running']]
    assert sorted(live) == ['boardA', 'boardB', 'browser:HyperX']
    watch.stop(pdir, p['slug'], device='boardA')
    watch._live[(p['slug'], 'boardA')]['thread'].join(5)
    assert not watch.running(p['slug'], 'boardA') and watch.running(p['slug'], 'boardB')
    assert watch.read(pdir, a['id'])['ended'] and not watch.read(pdir, b['id']).get('ended')
    assert watch.sink('boardA', wav_bytes(tone(4.0)), {})['accepted'] is False                                # its board is told to stop
    watch.stop(pdir, p['slug'])                                                                                 # the rest, all at once
    watch._live[(p['slug'], 'boardB')]['thread'].join(5)
    assert not watch.running(p['slug']) and watch.read(pdir, c['id'])['ended']
    assert all(w['ended'] for w in watch.watches(pdir, p['slug']))


def test_a_board_that_refuses_to_arm_leaves_no_open_watch(root):
    from wakeword_maker import watch
    root.mkdir()
    p = store.create_project(root, 'hey refuse')
    pdir = paths.project_dir(root, p['slug'])

    def refuse(seconds):
        raise RuntimeError('409 a capture is already going on')
    with pytest.raises(RuntimeError):
        watch.start(pdir, p['slug'], 'boardX', 5, 'r1', lambda wav: {}, capture_fn=lambda s: None, arm=refuse)
    sts = watch.watches(pdir, p['slug'])
    assert len(sts) == 1 and sts[0]['ended'] and 'already going on' in sts[0]['error'] and not sts[0]['running']
    assert not watch.running(p['slug'])


def test_resume_brings_back_every_mic_and_skips_only_the_busy_one(root):
    from wakeword_maker import watch
    root.mkdir()
    p = store.create_project(root, 'hey resume')
    pdir = paths.project_dir(root, p['slug'])
    quiet = lambda wav: {}
    a = watch.start(pdir, p['slug'], 'boardA', 5, 'r1', quiet, capture_fn=lambda s: watch.take('boardA', timeout=s), chunk_seconds=5, arm=lambda s: None, disarm=lambda: None)
    slow = threading.Event()
    b = watch.start(pdir, p['slug'], 'slowpi', 5, 'r1', quiet, capture_fn=lambda s: (slow.wait(30), None)[1], chunk_seconds=5)   # a pull that holds
    watch.pause_all()
    watch._live[(p['slug'], 'boardA')]['thread'].join(5)
    assert watch.read(pdir, a['id']).get('paused') and not watch.running(p['slug'], 'boardA')
    armed = []
    got, busy = watch.resume(pdir, p['slug'], lambda d, r, w: (quiet, (lambda s: watch.take(d, timeout=s)), 5, (lambda s: armed.append((d, s))), None))
    assert [w['device'] for w in got] == ['boardA'] and busy is True                 # boardA is back; slowpi's old loop still holds its pull
    assert armed and armed[0][0] == 'boardA' and watch.running(p['slug'], 'boardA')
    slow.set()
    watch._live[(p['slug'], 'slowpi')]['thread'].join(5)
    got, busy = watch.resume(pdir, p['slug'], lambda d, r, w: (quiet, (lambda s: None), 5))
    assert [w['device'] for w in got] == ['slowpi'] and busy is False
    watch.stop(pdir, p['slug'])


def test_that_was_me_drops_the_room_piece_the_fire_fell_in(root):
    from wakeword_maker import watch, holdout
    root.mkdir()
    p = store.create_project(root, 'hey piece')
    pdir = paths.project_dir(root, p['slug'])
    st = watch.start(pdir, p['slug'], 'boardP', 1, '', lambda wav: {}, capture_fn=lambda s: watch.take('boardP', timeout=s), chunk_seconds=5, keep=True, arm=lambda s: None, disarm=lambda: None)
    assert st['runs'] == ''                                                                   # a capture with no models: the room only
    watch.sink('boardP', wav_bytes(tone(6.0)), {'chunk': 0, 'fires': [[2.0, 0.99]], 'fire_count': 1, 'model': 'hey_piece'})
    for _ in range(100):
        if store.counts(pdir).get('ambient/recorded'):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    watch.stop(pdir, p['slug'])
    st = watch.read(pdir, st['id'])
    fire = st['fires'][0]
    piece = store.list_clips(pdir, 'ambient/recorded', 0, 10)[0][0]
    assert fire['piece_clip'] == piece['id']
    out = watch.fate(pdir, st['id'], fire['id'], 'positive', {'device': 'boardP', 'text': 'hey piece'})
    assert out['piece_dropped'] is True and out['clip']
    side = store.read_side(store.clip_paths(pdir, 'ambient/recorded', piece['id'])[1])
    assert side['verdict'] == 'drop'                                                          # that piece is no negative any more
    assert holdout.make(pdir)['counts']['ambient'] == [0, 0]                                   # and never judges false alarms


# --- the room library: shared by every wake word ----------------------------------------------------------

def test_room_library_is_shared_filtered_per_phrase_and_held_out_per_mic(root):
    from wakeword_maker import features, holdout, augment, watch
    root.mkdir()
    a = store.create_project(root, 'hey alpha'); b = store.create_project(root, 'hey beta')
    pa, pb = paths.project_dir(root, a['slug']), paths.project_dir(root, b['slug'])
    pieces = [store.add_room(root, tone(3.0), {'device': d, 'watch': w, 'project': 'hey_alpha'}) for d, w in (('esp', 'w1'), ('esp', 'w1'), ('esp', 'w1'), ('pi', 'w2'), ('pi', 'w2'), ('pi', 'w2'))]
    assert len(store.list_room(root)) == 6 and store.room_summary(root)['minutes'] == round(sum(x['seconds'] for x in pieces) / 60, 1)
    own = store.add_clip(pa, 'ambient/recorded', tone(3.0), {'device': 'hyperx'})
    ids = lambda items: {Path(w).stem for w, _ in items}
    s = features._clips(pa, set(), root=root)
    assert ids(s['ambient']) == {p['id'] for p in pieces} | {own['id']}              # own takes plus the whole library
    store.drop_room_for(root, pieces[0]['id'], 'hey_alpha')                          # alpha's phrase was said in it
    assert pieces[0]['id'] not in ids(features._clips(pa, set(), root=root)['ambient'])
    assert pieces[0]['id'] in ids(features._clips(pb, set(), root=root)['ambient'])   # still a fine negative for beta
    s = features._clips(pa, set(), sources={'room_devices': ['pi']}, root=root)
    assert ids(s['ambient']) == {p['id'] for p in pieces[3:]}                        # one microphone's room only
    assert features.sources_label(features.sources_of({'sources': {'room_devices': ['pi']}})) == 'room pi'
    h = holdout.make(pa, frac=0.3, seed=1, root=root)
    held = set(h['ambient'])
    assert len(held) == 2 and len([i for i in held if i in {p['id'] for p in pieces[:3]}]) == 1   # whole takes, from EACH mic: one esp (of two left), one pi (of three)
    assert pieces[0]['id'] not in held
    pools = augment.pools(root, pa, held)
    assert {Path(x).stem for x in pools['ambient']} == held                          # the stress judge draws from the held-out room alone
    pools = augment.pools(root, pb)
    assert len(pools['ambient']) == 6                                                 # beta sees every piece
    assert store.delete_room(root, watch='w2') == 3 and len(store.list_room(root)) == 3
    assert store.delete_room(root, cid=pieces[1]['id']) == 1


def test_a_kept_piece_lands_in_the_library_and_that_was_me_drops_it_for_this_phrase_only(root):
    from wakeword_maker import watch
    root.mkdir()
    p = store.create_project(root, 'hey lib')
    pdir = paths.project_dir(root, p['slug'])
    st = watch.start(pdir, p['slug'], 'boardL', 1, '', lambda wav: {}, capture_fn=lambda s: watch.take('boardL', timeout=s), chunk_seconds=5, keep=True,
                     arm=lambda s: None, disarm=lambda: None, root=root)
    assert st['library'] and st['project'] == 'hey_lib'
    watch.sink('boardL', wav_bytes(tone(6.0)), {'chunk': 4, 'fires': [[2.0, 0.97]], 'fire_count': 1, 'model': 'x'})
    for _ in range(100):
        if store.list_room(root):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    watch.stop(pdir, p['slug'])
    piece = store.list_room(root)[0]
    assert piece['device'] == 'boardL' and piece['watch'] == st['id'] and piece['piece'] == 4 and piece['project'] == 'hey_lib'
    assert store.counts(pdir)['ambient/recorded'] == 0                                # nothing in the wake word's own folder
    st = watch.read(pdir, st['id'])
    fire = st['fires'][0]
    assert fire['piece_clip'] == piece['id'] and fire['piece_in'] == 'room'
    out = watch.fate(pdir, st['id'], fire['id'], 'positive', {'device': 'boardL', 'text': 'hey lib'}, root=root, slug='hey_lib')
    assert out['piece_dropped'] is True
    side = store.list_room(root, dropped=True)[0]
    assert side['drop_for'] == ['hey_lib'] and store.list_room(root, slug='hey_lib') == [] and len(store.list_room(root, slug='other')) == 1


def test_room_day_starts_a_keeping_watch_on_every_online_satellite(settings, root, monkeypatch):
    api.create_project(body={'phrase': 'hey day'}, settings=settings)
    monkeypatch.setattr(api, 'mics', lambda **_: {'mics': [{'id': 'espA', 'online': True, 'capture': True}, {'id': 'piB', 'online': True, 'capture': False}, {'id': 'off', 'online': False}]})
    monkeypatch.setattr(api, '_watch_fns', lambda root, slug, device, runs, settings, keep=False, test=False: ((lambda wav: {}), (lambda s: api._watch.take(device, timeout=s)), 5, (lambda s: None) if device == 'espA' else None, None))
    r = api.room_day('hey_day', body={'minutes': 90}, settings=settings)
    assert [x['device'] for x in r['started']] == ['espA', 'piB'] and r['refused'] == [] and r['minutes'] == 90 and r['runs'] == ''
    assert [w['device'] for w in r['watches'] if w['running']] == ['piB', 'espA'] or sorted(w['device'] for w in r['watches'] if w['running']) == ['espA', 'piB']
    r2 = api.room_day('hey_day', body={'devices': ['espA']}, settings=settings)
    assert r2['started'] == [] and r2['refused'][0]['error'] == 'already watching'
    assert api.room_library(query={'project': 'hey_day'}, settings=settings)['minutes'] == 0
    r3 = api.room_day_stop('hey_day', settings=settings)
    assert not any(w['running'] for w in r3['watches'])


def test_install_satellite_goes_through_the_library(root, monkeypatch):
    """The ★ ESP32 model lands in the wake word library with its manifest, then the device's wish is switched
    (the driver sends it, the board restarts). A board that could not be told is set and said so (2026-10-19)."""
    root.mkdir()
    pr = store.create_project(root, 'hey marcus')
    pdir = paths.project_dir(root, pr['slug'])
    d = pdir / 'runs' / 'e-mww'
    d.mkdir(parents=True)
    (d / 'model.tflite').write_bytes(b'\0\0\0\0TFL3' + b'\0' * 5000)
    (d / 'model.json').write_text(json.dumps({'micro': {'sliding_window_size': 6, 'feature_step_size': 10}}))
    (d / 'run.json').write_text(json.dumps({'id': 'e-mww', 'trainer': 'mww', 'state': 'done', 'threshold': 0.9,
                                            'heldout': {'recall': 0.9, 'near_fires': 0, 'n_near': 10, 'threshold': 0.9}}))
    from core.devices import engine
    from core.wakeword import library
    monkeypatch.setattr(engine, '_usable', lambda dev: {'id': 'esp', 'parts': [{'driver': 'satellite', 'config': {}}]})
    monkeypatch.setattr(engine, '_part', lambda row, drv: row['parts'][0])
    monkeypatch.setattr(engine, '_part_secrets', lambda dev, drv: {})
    monkeypatch.setattr(api, '_wake_format', lambda device, h=None: 'tflite')
    added, switched = [], []
    monkeypatch.setattr(library, 'add', lambda name, data, fam, meta=None, **kw: added.append((name, fam, meta, kw)) or {'name': name})
    monkeypatch.setattr(library, 'switch', lambda l, m: switched.append((l, m)) or {'sent': True, 'warning': '', 'model': m})
    out = api.install_satellite(pr['slug'], body={'device': 'esp'}, settings={'data_dir': str(root)})
    assert out['sent'] == 'e-mww' and out['threshold'] == 0.9 and out['told']['sent'] is True
    name, fam, meta, kw = added[0]
    assert (name, fam) == ('hey_marcus', 'tflite')
    assert meta == {'threshold': 0.9, 'run': 'e-mww', 'label': None, 'micro': {'sliding_window': 6, 'step_ms': 10}}
    assert kw == {'overwrite': True, 'made_by': 'wakeword-maker', 'phrase': 'hey marcus'}
    assert switched == [('esp', 'hey_marcus')]
    monkeypatch.setattr(library, 'switch', lambda l, m: {'sent': False, 'warning': 'satellite: Could not reach it.', 'model': m})
    body, code = api.install_satellite(pr['slug'], body={'device': 'esp'}, settings={'data_dir': str(root)})
    assert code == 502 and 'Could not reach' in body['error'] and body['code'] == 'not_sent'
    st = api.install_status(pr['slug'], settings={'data_dir': str(root)})
    assert 'satellites' in st and 'desktop' in st
