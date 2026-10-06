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
