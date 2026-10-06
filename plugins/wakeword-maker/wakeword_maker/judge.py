# judge.py - a trained run against real audio, inside the warm sampler process (the plugin env has torch and the
# tflite runtime). Two doors:
#   judge(root, slug, run)         every held-out clip scored by that run's model, with its mic, style and phrase,
#                                  so the page can break recall down by microphone and way of speaking
#   score(root, slug, runs, audio) one utterance (the Try it button) scored by several runs at once
import json
import time
from pathlib import Path

import numpy as np

from . import catalog, features, features_mww, holdout, paths, store
from .paths import write_json

_cache = {}


def _run_dir(root, slug, rid):
    d = paths.project_dir(root, slug) / 'runs' / rid
    if not (d / 'run.json').is_file():
        raise ValueError(f"no run {rid}")
    return d


def _model_file(rdir):
    for p in (rdir / 'model.onnx', rdir / 'final' / 'model.onnx', rdir / 'model.tflite'):
        if p.exists():
            return p
    raise ValueError('that run left no model')


def _peaks(scores, dt, floor=0.2, cooldown=1.5):
    """Local maxima of a score track above `floor`, at least `cooldown` seconds apart: (time, score)."""
    out, last_t = [], -1e9
    n = len(scores)
    for i, v in enumerate(scores):
        if v < floor:
            continue
        if (i > 0 and scores[i - 1] > v) or (i + 1 < n and scores[i + 1] >= v):
            continue
        t = i * dt
        if t - last_t < cooldown:
            if out and v > out[-1][1]:
                out[-1] = (round(t, 2), round(float(v), 4))
            continue
        out.append((round(t, 2), round(float(v), 4)))
        last_t = t
    return out


class OwwModel:
    """openWakeWord's runtime around a run's ONNX: scores 80 ms frames, remembers the stream."""

    def __init__(self, root, onnx_path):
        from openwakeword.model import Model
        d = catalog.dataset_dir(root, 'oww_models')
        self.m = Model(wakeword_models=[str(onnx_path)], inference_framework='onnx',
                       melspec_model_path=str(d / 'melspectrogram.onnx'), embedding_model_path=str(d / 'embedding_model.onnx'))
        self.name = list(self.m.models.keys())[0]

    def track(self, audio, lead=16000):
        """One score per 80 ms frame, the stream fed as the device feeds it (after `lead` samples of silence)."""
        self.m.reset()
        pcm = (np.clip(np.concatenate([np.zeros(lead, np.float32), audio, np.zeros(8000, np.float32)]), -1, 1) * 32767).astype(np.int16)
        out = []
        for i in range(0, len(pcm) - 1279, 1280):
            out.append(float(self.m.predict(pcm[i:i + 1280])[self.name]))
        return np.array(out), 0.08, lead / 16000

    def stream_max(self, audio):
        """Max score over a clip fed as the device would feed it."""
        tr, _, _ = self.track(audio)
        return (float(tr.max()) if len(tr) else 0.0), [round(x, 3) for x in tr]

    def peaks(self, audio):
        tr, dt, lead = self.track(audio)
        return [(round(max(0.0, t - lead), 2), v) for t, v in _peaks(tr, dt)]


class MwwModel:
    def __init__(self, root, tflite_path, window=5):
        import sys
        src = features_mww.source_dir(root)
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from . import compat
        compat.apply()
        from .train_mww import Streamer
        self.st = Streamer(tflite_path)
        self.window = window

    def track(self, audio, lead=16000):
        self.st.reset()
        spec = features_mww._spectrogram(np.concatenate([np.zeros(lead, np.float32), audio, np.zeros(8000, np.float32)]))
        probs = self.st.probabilities(spec.astype(np.float32))
        if len(probs) < self.window:
            return np.zeros(0), self.st.stride * 0.01, lead / 16000
        avg = np.convolve(probs, np.ones(self.window) / self.window, mode='valid')
        dt = self.st.stride * 0.01
        settle = 100 // self.st.stride
        tail = avg[settle:] if len(avg) > settle else avg
        return tail, dt, lead / 16000 - (settle * dt if len(avg) > settle else 0.0)     # tail[0] is at settle*dt, not 0

    def stream_max(self, audio):
        tr, _, _ = self.track(audio)
        return (float(tr.max()) if len(tr) else 0.0), [round(float(x), 3) for x in tr]

    ONSET_S = 0.5      # a chunk starts with silence then the room all at once; the board streams and never hears that edge

    def peaks(self, audio):
        tr, dt, lead = self.track(audio)
        return [(round(t - lead, 2), v) for t, v in _peaks(tr, dt) if t - lead >= self.ONSET_S]


def load(root, slug, rid):
    key = (str(root), slug, rid)
    if key in _cache:
        return _cache[key]
    rdir = _run_dir(root, slug, rid)
    mf = _model_file(rdir)
    run = json.loads((rdir / 'run.json').read_text())
    model = MwwModel(root, mf, int((run.get('cfg') or {}).get('sliding_window', 5))) if mf.suffix == '.tflite' else OwwModel(root, mf)
    if len(_cache) > 6:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (model, run, rdir)
    return _cache[key]


GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.97)


USUAL = {'oww': 0.5, 'mww': 0.97}      # where each detector normally runs; among equals the pick stays near it


def summary(rows, eval_rows=None, trainer='oww'):
    """The judge's verdict at every threshold, and the one to install: no ordinary negative fires, at most one
    sound-alike, the standard false-alarm rate under 0.5/h where the trainer measured it, then the best recall,
    and among equals the threshold nearest the detector's usual operating point (a plateau of 100% from 0.3 to
    0.97 says nothing about a faint call from the hallway; the usual point keeps room for it)."""
    pos = [r['score'] for r in rows if r['kind'] == 'positive']
    near = [r['score'] for r in rows if r['kind'] != 'positive' and r.get('near')]
    other = [r['score'] for r in rows if r['kind'] != 'positive' and not r.get('near')]
    std = {round(r['threshold'], 2): r.get('fp_per_hour') for r in (eval_rows or []) if r.get('threshold') is not None}
    table = []
    for t in GRID:
        table.append({'threshold': t, 'recall': (sum(v >= t for v in pos) / len(pos)) if pos else None,
                      'near_fires': sum(v >= t for v in near), 'neg_fires': sum(v >= t for v in other), 'fp_per_hour': std.get(t)})
    scored = [x for x in table if x['recall'] is not None]
    ok = [x for x in scored if x['neg_fires'] == 0 and x['near_fires'] <= 1 and (x['fp_per_hour'] is None or x['fp_per_hour'] <= 0.5)]
    usual = USUAL.get(trainer, 0.5)
    pick = max(ok or scored, key=lambda x: (x['recall'], -x['near_fires'], -abs(x['threshold'] - usual), x['threshold']), default=None)
    return {'n_pos': len(pos), 'n_near': len(near), 'n_neg': len(other), 'table': table, 'clean': bool(ok),
            **({k: pick[k] for k in ('threshold', 'recall', 'near_fires', 'neg_fires', 'fp_per_hour')} if pick else {'threshold': None, 'recall': None})}


def judge(root, slug, rid, force=False):
    """Every held-out clip, scored. Cached in the run folder until the held-out slice is drawn again."""
    model, run, rdir = load(root, slug, rid)
    out_path = rdir / 'judge.json'
    pdir = paths.project_dir(root, slug)
    held_when = (holdout.read(pdir) or {}).get('when')
    held_key = holdout.key(pdir)
    if out_path.exists() and not force:
        cached = json.loads(out_path.read_text())
        if cached.get('holdout_when') == held_when and cached.get('held_key', held_key) == held_key:
            return cached
    held = holdout.ids(pdir)
    sets = features._clips(pdir, held)
    rows = []
    t0 = time.time()
    for kind, items in (('positive', sets['val_positive']), ('negative', sets['val_negative'])):
        for wav, _ in items:
            try:
                audio, sr = store.decode(wav)
                audio = store.to_16k(audio, sr)
                side = store.read_side(Path(wav).with_suffix('.json')) or {}
            except Exception:
                continue
            best, _ = model.stream_max(audio)
            rows.append({'kind': kind, 'id': side.get('id'), 'collection': side.get('collection'), 'device': side.get('device'), 'style': side.get('style') or 'normal',
                         'near': bool(side.get('near')), 'text': side.get('text'), 'score': round(best, 4)})
    out = {'run': rid, 'trainer': run.get('trainer'), 'rows': rows, 'seconds': round(time.time() - t0, 1), 'when': time.time(),
           'holdout_when': held_when, 'held_key': held_key}
    write_json(out_path, out)
    return out


def grade(root, slug, rid, eval_rows=None, force=False):
    """Judge the run and write the verdict into run.json (`heldout`, and the threshold to install). What every
    trainer does last, and what the page does for a run judged before this existed."""
    out = judge(root, slug, rid, force=force)
    rdir = _run_dir(root, slug, rid)
    rj = rdir / 'run.json'
    run = json.loads(rj.read_text())
    verdict = summary(out['rows'], eval_rows if eval_rows is not None else run.get('rows'), run.get('trainer') or 'oww')
    run['heldout'] = verdict
    if verdict.get('threshold') is not None and not run.get('threshold_by_user'):
        run['threshold'] = verdict['threshold']
        if run.get('trainer') == 'mww':
            run['cutoff'] = verdict['threshold']
            mj = rdir / 'model.json'
            if mj.exists():
                try:
                    man = json.loads(mj.read_text())
                    man['micro']['probability_cutoff'] = verdict['threshold']
                    write_json(mj, man, indent=2)
                except Exception:
                    pass
    write_json(rj, run, indent=1)
    return verdict


def score(root, slug, rids, audio):
    """One utterance against several runs."""
    out = {}
    for rid in rids:
        try:
            model, run, rdir = load(root, slug, rid)
            best, trace = model.stream_max(audio)
            thr = run.get('threshold') or run.get('cutoff') or 0.5
            out[rid] = {'score': round(best, 4), 'threshold': thr, 'fires': best >= thr, 'trace': trace[-60:], 'trainer': run.get('trainer')}
        except Exception as e:
            out[rid] = {'error': f"{type(e).__name__}: {e}"}
    return out


def listen(root, slug, rids, audio):
    """A long chunk against several runs: every peak along it, so a watch can count fires at any threshold."""
    out = {}
    for rid in rids:
        try:
            model, run, rdir = load(root, slug, rid)
            out[rid] = {'peaks': model.peaks(audio), 'threshold': run.get('threshold') or run.get('cutoff') or 0.5, 'trainer': run.get('trainer')}
        except Exception as e:
            out[rid] = {'error': f"{type(e).__name__}: {e}"}
    return out


def run_job(root, args, progress):
    """The 'judge' job: every finished model of a wake word graded again on the judging set as it stands now.
    Fresh verdicts are kept unless `force`. What the Re-judge button runs after a reshuffle or a dropped clip."""
    slug = args['project']
    pdir = paths.project_dir(root, slug)
    hk = holdout.key(pdir)
    hw = (holdout.read(pdir) or {}).get('when')
    todo = []
    for d in sorted((pdir / 'runs').iterdir()) if (pdir / 'runs').is_dir() else []:
        rj = d / 'run.json'
        if not rj.is_file():
            continue
        try:
            run = json.loads(rj.read_text())
            _model_file(d)
        except Exception:
            continue
        if run.get('state') != 'done':
            continue
        fresh = False
        if (d / 'judge.json').is_file() and not args.get('force'):
            try:
                c = json.loads((d / 'judge.json').read_text())
                fresh = c.get('held_key') == hk and c.get('holdout_when') == hw and 'heldout' in run
            except Exception:
                pass
        if not fresh:
            todo.append(d.name)
    progress.step('judge', f"{len(todo)} model(s) to judge on {len(holdout.ids(pdir))} held-out clips")
    done = []
    for i, rid in enumerate(todo):
        try:
            v = grade(root, slug, rid, force=True)
            done.append({'run': rid, 'recall': v.get('recall'), 'near_fires': v.get('near_fires')})
        except Exception as e:
            done.append({'run': rid, 'error': f"{type(e).__name__}: {e}"})
        progress.update(100.0 * (i + 1) / max(1, len(todo)), f"{i + 1} of {len(todo)} judged · {rid}")
        _cache.clear()
    progress.done({'judged': len(done), 'runs': done})
