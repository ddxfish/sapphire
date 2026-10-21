"""The road, start to finish: Settings, Wakewords, Synthetic, Human, Background, Check, Train (Single and Search),
Test, Install, done, cleanup, through the same doors the page uses (wakeword_maker/road.py). Hermetic here: the
heavy jobs are stand-ins that write what the real ones write. WWM_ROAD=1 walks the real jobs under the plugin
env in a scratch data folder with the big sets stood in small (minutes; WWM_ROAD_ESP32=1 adds the ESP32 family)."""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from wakeword_maker import holdout, jobs, judge, paths, road, store, train_oww   # noqa: E402
import importlib.util   # noqa: E402

_spec = importlib.util.spec_from_file_location('wwm_routes_api_for_the_road', PLUGIN / 'routes' / 'api.py')
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / 'data'
    r.mkdir()
    return r


class FakeJobs:
    """Stands in for jobs.enqueue and jobs.start: the job folder is written as a finished job, and the work is a
    sketch of the real one (clips, ratings, a run with a model and a verdict) so every door after it has something
    to read."""

    def __init__(self, root):
        self.root, self.n, self.kinds = Path(root), 0, []

    def __call__(self, root, kind, args, python, plugin_dir, project=None, publish=None):
        self.kinds.append(kind)
        jdir = jobs._new_dir(self.root, kind)
        now = time.time()
        paths.write_json(jdir / 'job.json', {'kind': kind, 'args': args or {}, 'project': project, 'pid': None, 'born': None, 'started': now,
                                             'queued_at': now, 'python': str(python), 'plugin_dir': str(plugin_dir)})
        try:
            jobs._write_status(jdir, 'done', result=self.run(kind, args or {}, project))
        except Exception as e:
            jobs._write_status(jdir, 'failed', error=f"{type(e).__name__}: {e}")
        return jobs.read(jdir)

    def run(self, kind, args, slug):
        pdir = paths.project_dir(self.root, slug) if slug else None
        pr = store.load_project(self.root, slug) if slug else {}
        if kind == 'synth':
            plan = (pr.get('settings') or {}).get('synth') or {}
            n = int(plan.get('target') or 60)
            voice = (plan.get('piper') or ['en_US-amy-medium'])[0]
            for i in range(n):
                store.add_clip(pdir, 'positive/synth', road.tone(freq=300 + 5 * i), {'source': 'synth', 'engine': 'piper', 'voice': voice, 'text': pr['phrase']})
            k = 0
            for t in pr.get('negatives_auto') or []:
                for _ in range(15):
                    store.add_clip(pdir, 'negative/synth', road.tone(freq=900 + 3 * k), {'source': 'synth', 'engine': 'piper', 'voice': voice, 'text': t})
                    k += 1
            return {'made': n + k, 'piper': n + k, 'kokoro': 0, 'failed': 0, 'seconds': 1, 'counts': store.counts(pdir)}
        if kind == 'qa':
            rated = 0
            for c in ('positive/synth', 'positive/recorded', 'negative/synth', 'negative/recorded'):
                for r in store.list_clips(pdir, c, 0, 10 ** 6)[0]:
                    if 'qa' in r and not args.get('rerate'):
                        continue
                    store.update_side(pdir, c, r['id'], qa={'score': 90, 'transcript': r.get('text') or pr['phrase'], 'similarity': 1.0, 'reasons': [], 'when': time.time()},
                                      **({} if r.get('verdict_by') == 'user' else {'verdict': 'keep'}))
                    rated += 1
            (pdir / 'qa').mkdir(exist_ok=True)
            paths.write_json(pdir / 'qa' / 'summary.json', {'when': time.time(), 'bar': args.get('bar', 60), 'rated_now': rated, 'collections': {}, 'voices': {}})
            paths.write_json(pdir / 'qa' / 'worst.json', [])
            return {'rated': rated, 'collections': {}}
        if kind in ('train', 'train_mww', 'tune'):
            return self.train(kind, args, slug, pdir)
        if kind == 'judge':
            done = []
            for d in sorted((pdir / 'runs').iterdir()):
                if (d / 'run.json').is_file():
                    self.grade(pdir, d, json.loads((d / 'run.json').read_text()))
                    done.append(d.name)
            return {'judged': len(done), 'runs': done}
        if kind == 'voices':
            return {'fetched': 0}
        raise ValueError(f"the fake has no {kind}")

    def train(self, kind, args, slug, pdir):
        if not holdout.read(pdir):
            holdout.make(pdir, root=self.root)
        self.n += 1
        fam = 'mww' if kind == 'train_mww' else ('tune' if kind == 'tune' else 'oww')
        rdir = pdir / 'runs' / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.n:02d}-{fam}"
        rdir.mkdir(parents=True)
        cfg = {**(train_oww.DEFAULTS if fam != 'mww' else {'steps': 20000}), **train_oww.PRESETS.get(args.get('preset') or 'standard', {}), **(args.get('cfg') or {})}
        rows = [{'threshold': t, 'recall': 0.9, 'neg_fp_rate': 0.0, 'fp_per_hour': 0.1, 'own_fa_per_hour': 0.0} for t in train_oww.THRESHOLDS]
        run = {'id': rdir.name, 'trainer': 'mww' if fam == 'mww' else 'oww', 'preset': args.get('preset'), 'cfg': cfg, 'recipe': args.get('recipe') or 'natural',
               'sources': args.get('sources') or {}, 'sources_label': 'all', 'holdout': True, 'grid': args.get('grid'), 'label': args.get('label') or '',
               'started': time.time(), 'ended': time.time(), 'state': 'done', 'rows': rows, 'score': 0.9, 'seconds': 3, 'params': 1234}
        if fam == 'tune':
            run.update({'kind': 'tune', 'refine_of': args.get('from_grid'), 'trials': args.get('trials'), 'budget_steps': args.get('budget_steps'),
                        'best_trial': 1, 'best_params': {'layer_size': 32}, 'best_score': 0.8, 'candidates': []})
            (rdir / 'final').mkdir()
            (rdir / 'final' / 'model.onnx').write_bytes(b'\x08\x07' + b'\0' * 6000)
            with open(rdir / 'trials.jsonl', 'w') as fh:
                for i in range(int(args.get('trials') or 2)):
                    fh.write(json.dumps({'trial': i, 'params': {'layer_size': 32}, 'score': 0.7 + 0.1 * i, 'judged': True, 'rows': rows, 'seconds': 1,
                                         'heldout': {'recall': 0.9, 'stress_recall': 0.8, 'near_fires': 0, 'neg_fires': 0, 'threshold': 0.5}}) + '\n')
        elif fam == 'mww':
            (rdir / 'model.tflite').write_bytes(b'\0\0\0\0TFL3' + b'\0' * 6000)
            paths.write_json(rdir / 'model.json', {'type': 'micro', 'micro': {'probability_cutoff': 0.9, 'sliding_window_size': 5, 'feature_step_size': 10}}, indent=2)
            run['cutoff'] = 0.9
        else:
            (rdir / 'model.onnx').write_bytes(b'\x08\x07' + b'\0' * 6000)
        with open(rdir / 'metrics.jsonl', 'w') as fh:
            fh.write(json.dumps({'t': 1, 'step': 100, 'loss': 0.3, 'val_recall': 0.8}) + '\n')
        paths.write_json(rdir / 'run.json', run, indent=1)
        v = self.grade(pdir, rdir, run)
        return {'run': rdir.name, 'score': 0.9, 'heldout': v}

    def grade(self, pdir, rdir, run):
        """The judge's rows for every held-out clip, with its tags, and the verdict in run.json."""
        held = holdout.read(pdir) or {}
        rows = []
        for part, cols, kind in (('positive', ('positive/recorded', 'positive/uploaded'), 'positive'), ('near', ('negative/recorded', 'negative/uploaded'), 'negative'),
                                 ('negative', ('negative/recorded', 'negative/uploaded'), 'negative')):
            for cid in held.get(part) or []:
                for c in cols:
                    sp = pdir / c / f"{cid}.json"
                    if sp.is_file():
                        side = store.read_side(sp)
                        base = {'id': cid, 'collection': c, 'device': side.get('device'), 'how': side.get('how'), 'where': side.get('where'), 'noise': side.get('noise'),
                                'who': side.get('who'), 'near': bool(side.get('near')), 'text': side.get('text')}
                        rows.append({**base, 'kind': kind, 'score': 0.92 if kind == 'positive' else 0.08})
                        if kind == 'positive':
                            rows += [{**base, 'kind': 'stress', 'draw': i, 'score': 0.85} for i in range(4)]
                        break
        paths.write_json(rdir / 'judge.json', {'run': rdir.name, 'trainer': run.get('trainer'), 'rows': rows, 'when': time.time(), 'holdout_when': held.get('when'),
                                               'held_key': holdout.key(pdir), 'stress_key': judge.STRESS_KEY, 'stress': judge.STRESS})
        v = judge.summary(rows, run.get('rows'), run.get('trainer') or 'oww')
        run['heldout'] = v
        if v.get('threshold') is not None and not run.get('threshold_by_user'):
            run['threshold'] = v['threshold']
        paths.write_json(rdir / 'run.json', run, indent=1)
        return v


class FakeSampler:
    """The warm helper's doors, answered from here: a sample, a variation, the recipe's dry run, Try it, a watch's peaks."""

    def ensure(self, *a, **k):
        return 1

    def get(self, port, door, params, timeout=90):
        if door == 'dryrun':
            return json.dumps({'verdict': 'fits this set.', 'n': int(params.get('n') or 0)}).encode(), '{}'
        return road.wav_bytes(road.tone()), '{}'

    def post(self, port, door, params, data, timeout=120):
        runs = [r for r in str(params.get('runs') or '').split(',') if r]
        if door == 'score':
            return json.dumps({'runs': {r: {'score': 0.91, 'threshold': 0.5, 'fires': True, 'trace': [0.1, 0.9], 'trainer': 'oww'} for r in runs}, 'seconds': 1.0}).encode()
        return json.dumps({'runs': {r: {'peaks': [[6.4, 0.93], [14.1, 0.31]], 'threshold': 0.5, 'trainer': 'oww'} for r in runs}}).encode()


class FakeLibrary:
    """The wake word library and this computer's listener: a folder and a name."""

    def __init__(self, folder):
        self.dir, self.model, self.added, self.removed = Path(folder), '', [], []
        self.dir.mkdir(parents=True, exist_ok=True)

    def add(self, name, data, family, meta=None, overwrite=False, made_by='upload', phrase=''):
        (self.dir / f"{name}.{family}").write_bytes(data)
        self.added.append((name, family, meta, made_by))
        return {'name': name}

    def switch(self, listener, name):
        self.model = name
        return {'sent': True, 'model': name, 'warning': ''}

    def remove(self, name, family):
        (self.dir / f"{name}.{family}").unlink(missing_ok=True)
        self.removed.append((name, family))
        return []

    def get(self, name, family):
        return {'source': 'user', 'made_by': 'wakeword-maker'} if (self.dir / f"{name}.{family}").is_file() else None

    def desktop(self, slug):
        ours = self.model == slug and (self.dir / f"{slug}.onnx").is_file()
        return {'model': self.model, 'threshold': 0.5, 'enabled': True, 'file': (self.dir / f"{slug}.onnx").is_file(), 'ours': ours}


def _scratch(root, tmp_path):
    """A data folder with every set the settings step asks for, none of it real."""
    road.standins(root, real_root=tmp_path / 'nowhere')
    api.catalog.dataset_dir(root, 'oww_models').mkdir(parents=True, exist_ok=True)
    api.catalog.write_receipt(root, 'oww_models', [])


def _no_hardware(monkeypatch, lib):
    import core.wakeword as ww
    from core.wakeword import library
    monkeypatch.setattr(api, 'mics', lambda **_: {'mics': []})
    monkeypatch.setattr(library, 'listeners', lambda: [])
    for n in ('add', 'switch', 'remove', 'get'):
        monkeypatch.setattr(library, n, getattr(lib, n))
    monkeypatch.setattr(ww, 'USER_MODELS_DIR', lib.dir)
    monkeypatch.setattr(api, 'USER_MODELS', lib.dir)
    monkeypatch.setattr(api, '_desktop_state', lib.desktop)


def test_the_road_hermetic(root, tmp_path, monkeypatch):
    settings = {'data_dir': str(root), 'device': 'cpu', 'qa_whisper_model': 'base.en'}
    fake = FakeJobs(root)
    monkeypatch.setattr(api, '_python_for', lambda kind: (sys.executable, None))
    monkeypatch.setattr(api.jobs, 'enqueue', fake)
    monkeypatch.setattr(api.jobs, 'start', fake)
    fs = FakeSampler()
    for n in ('ensure', 'get', 'post'):
        monkeypatch.setattr(api.sampler_client, n, getattr(fs, n))
    lib = FakeLibrary(tmp_path / 'models')
    _no_hardware(monkeypatch, lib)
    _scratch(root, tmp_path)
    monkeypatch.setattr(api, '_env', lambda: {'state': 'ready', 'python': sys.executable, 'build': {}})
    monkeypatch.setattr(api, '_gpu_info', lambda: None)

    r = road.Road(road.Local(root, settings, api=api), sampler='fake', say=print)
    rep = r.walk()
    print(rep.table())
    assert not rep.failed, rep.table()
    assert [x['state'] for x in rep.rows] == ['pass'] * 8 + ['skip'] + ['pass'] * 4      # esp32 is the one skip
    assert fake.kinds == ['synth', 'qa', 'train', 'train', 'tune', 'judge']
    assert lib.added and lib.added[0][0] == 'hey_road_test' and lib.added[0][3] == 'wakeword-maker'
    assert lib.removed == [('hey_road_test', 'onnx')]
    assert not (paths.projects_dir(root) / 'hey_road_test').exists()
    assert store.list_room(root) == [] and store.list_room(root, dropped=True) == []       # the PC day's pieces went with the road
    human = next(x for x in rep.rows if x['step'] == 'human')
    assert '36 takes on 3 mics' in human['detail'] and 'judge takes seen' in human['detail']
    test = next(x for x in rep.rows if x['step'] == 'test')
    assert '1 fire(s), sorted positive' in test['detail']


def test_the_road_keeps_its_wake_word_and_resumes_a_step(root, tmp_path, monkeypatch):
    """--keep leaves the scratch wake word; --only walks one step on it, finding the ★ run by itself."""
    settings = {'data_dir': str(root), 'device': 'cpu', 'qa_whisper_model': 'base.en'}
    fake = FakeJobs(root)
    monkeypatch.setattr(api, '_python_for', lambda kind: (sys.executable, None))
    monkeypatch.setattr(api.jobs, 'enqueue', fake)
    monkeypatch.setattr(api.jobs, 'start', fake)
    lib = FakeLibrary(tmp_path / 'models')
    _no_hardware(monkeypatch, lib)
    _scratch(root, tmp_path)
    monkeypatch.setattr(api, '_env', lambda: {'state': 'ready', 'python': sys.executable, 'build': {}})
    door = road.Local(root, settings, api=api)
    rep = road.Road(door, sampler='skip', keep=True).walk(only=['settings', 'wakeword', 'synthetic', 'human', 'background', 'check', 'train', 'cleanup'])
    assert not rep.failed, rep.table()
    assert (paths.projects_dir(root) / 'hey_road_test').is_dir() and len(store.list_room(root)) == 2
    again = road.Road(door, sampler='skip', keep=True)
    rep = again.walk(only=['test', 'install'])
    assert not rep.failed, rep.table()
    assert again.rid and 'need the sampler' in rep.rows[0]['detail']
    # a wake word with the same name that is not the road's is never taken away
    store.save_project(paths.project_dir(root, 'hey_road_test'), {**store.load_project(root, 'hey_road_test'), 'settings': {}})
    rep = road.Road(door, sampler='skip').walk(only=['wakeword'])
    assert rep.failed and 'not the road' in rep.failed[0]['error']


def test_a_grid_takes_a_budget_for_every_cell_and_the_refine(root, monkeypatch):
    settings = {'data_dir': str(root)}
    api.create_project(body={'phrase': 'hey budget'}, settings=settings)
    monkeypatch.setattr(api, '_python_for', lambda kind: (sys.executable, None))
    queued = []
    monkeypatch.setattr(api.jobs, 'enqueue', lambda root, kind, args, py, pdir, project=None, publish=None: queued.append((kind, args)) or {'id': f"j{len(queued)}"})
    body = {'trainers': ['oww', 'mww'], 'recipes': ['natural'], 'sources': ['all'], 'presets': ['quick'], 'knobs': {'oww': ['rnn']},
            'cfg': {'steps': 300, 'layer_size': 8, 'eval_every': 50, 'bogus': 1}, 'refine': {'trials': 2, 'budget_steps': 1000, 'parallel': 1, 'top': 1}}
    g = api.grid_start('hey_budget', body=body, settings=settings)['grid']
    assert g['of'] == 2 and g['cfg'] == {'steps': 300, 'layer_size': 8, 'eval_every': 50}
    by = {k: a for k, a in queued}
    assert by['train']['cfg'] == {'model_type': 'rnn', 'steps': 300, 'layer_size': 8, 'seed': 1}         # the knob, then the budget; no ESP32 key
    assert by['train_mww']['cfg'] == {'steps': 300, 'eval_every': 50, 'seed': 1}
    assert by['tune']['cfg'] == {'steps': 300, 'layer_size': 8} and by['tune']['from_grid'] == g['id']
    g2 = api.grid_start('hey_budget', body={**body, 'cfg': None}, settings=settings)['grid']
    assert 'cfg' not in g2 and queued[-2][1]['cfg'] == {'seed': 1} and queued[-1][1]['cfg'] == {}      # the ESP32 cell, then the refine, without a budget


@pytest.mark.skipif(not os.environ.get('WWM_ROAD'), reason='WWM_ROAD=1 walks the real jobs under the plugin env (minutes)')
def test_the_road_for_real(root, tmp_path, monkeypatch):
    from core import plugin_envs
    py = plugin_envs.env_python('wakeword-maker')
    if not py:
        pytest.skip('the plugin environment is not built')
    esp32 = bool(os.environ.get('WWM_ROAD_ESP32'))
    missing = road.standins(root, esp32=esp32, python=py)
    if missing:
        pytest.skip(f"no stand-in for {', '.join(missing)}: the real data folder lacks it")
    settings = {'data_dir': str(root), 'device': 'auto', 'qa_whisper_model': 'base.en'}
    monkeypatch.setattr(api, '_python_for', lambda kind: (str(py), None))
    lib = FakeLibrary(tmp_path / 'models')
    _no_hardware(monkeypatch, lib)
    r = road.Road(road.Local(root, settings, api=api), esp32=esp32, sampler='skip', slow=float(os.environ.get('WWM_ROAD_SLOW') or 1), say=print)
    rep = r.walk()
    print(rep.table())
    assert not rep.failed, rep.table()
    assert lib.added and lib.removed == [('hey_road_test', 'onnx')]
    assert not (paths.projects_dir(root) / 'hey_road_test').exists()
