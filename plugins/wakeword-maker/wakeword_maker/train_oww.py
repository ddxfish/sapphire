# train_oww.py - the openWakeWord trainer: upstream's network and schedule (Model from openwakeword.train), our data
# and our judges. Each step mixes 1,024 windows of the 2,000-hour negative set with 50 positives, 50 sound-alikes
# and 50 windows of your own room. Every validation step writes a line of metrics the page graphs: loss, recall on
# your held-out clips, false alarms per hour on the standard 11.3 h set and on your held-out room takes. At the
# end the best checkpoints are averaged (as upstream), a threshold sweep is written, and the model goes out as ONNX.
import copy
import json
import math
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np

from . import catalog, compat, features, paths, store
from .paths import write_json

VAL_HOURS = 11.3                 # the standard validation features
DEFAULTS = {
    'model_type': 'dnn', 'layer_size': 32, 'n_blocks': 1, 'steps': 50000, 'lr': 1e-4, 'max_negative_weight': 1500,
    'n_acav': 1024, 'n_pos': 50, 'n_adv': 50, 'n_amb': 50, 'target_fp_per_hour': 0.2, 'val_every': 0, 'seed': 1,
    'sequences': 3,             # upstream: full run, then two short polishing runs at lr/10 and lr/100
}
PRESETS = {
    'quick': {'steps': 10000, 'layer_size': 32, 'n_blocks': 1},
    'standard': {'steps': 50000, 'layer_size': 32, 'n_blocks': 1},
    'thorough': {'steps': 100000, 'layer_size': 64, 'n_blocks': 2},
}
THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


class Data:
    """The feature arrays, memmapped once; shared by every trial in a sweep."""

    def __init__(self, root, fdir):
        fdir = Path(fdir)
        self.meta = json.loads((fdir / 'meta.json').read_text())
        self.shape = tuple(self.meta['shape'])
        ld = lambda n: np.load(fdir / f"{n}.npy", mmap_mode='r') if (fdir / f"{n}.npy").exists() else None
        self.pos, self.adv, self.amb = ld('positive'), ld('adversarial'), ld('ambient')
        self.val_pos, self.val_neg, self.val_amb = ld('val_positive'), ld('val_negative'), ld('val_ambient')
        acav = catalog.dataset_dir(root, 'oww_negatives') / 'openwakeword_features_ACAV100M_2000_hrs_16bit.npy'
        if not acav.exists():
            raise RuntimeError('the openWakeWord negative features are not downloaded (Settings → Datasets)')
        self.acav = np.load(acav, mmap_mode='r')
        valf = catalog.dataset_dir(root, 'oww_validation') / 'validation_set_features.npy'
        if not valf.exists():
            raise RuntimeError('the openWakeWord validation features are not downloaded (Settings → Datasets)')
        v = np.load(valf, mmap_mode='r')
        n = self.shape[0]
        # every window of the 11.3 h stream, as a view: no copy of 3 GB
        self.val_fp = np.lib.stride_tricks.as_strided(v, shape=(v.shape[0] - n + 1, n, v.shape[1]),
                                                      strides=(v.strides[0], v.strides[0], v.strides[1]))
        self.val_amb_seconds = float(self.meta.get('counts', {}).get('val_ambient_seconds') or 0)

    def batches(self, cfg, rng):
        """Endless: (x float32 [B, T, 96], y float32 [B])."""
        n_acav, n_pos, n_adv, n_amb = cfg['n_acav'], cfg['n_pos'], cfg['n_adv'], cfg['n_amb'] if self.amb is not None and len(self.amb) else 0
        at = rng.randrange(0, max(1, len(self.acav) - n_acav))
        while True:
            if at + n_acav > len(self.acav):
                at = rng.randrange(0, max(1, len(self.acav) - n_acav))
            xs = [np.asarray(self.acav[at:at + n_acav], dtype=np.float32)]
            at += n_acav
            ys = [np.zeros(n_acav, np.float32)]
            xs.append(self.pos[rng_idx(rng, len(self.pos), n_pos)]); ys.append(np.ones(n_pos, np.float32))
            if self.adv is not None and len(self.adv):
                xs.append(self.adv[rng_idx(rng, len(self.adv), n_adv)]); ys.append(np.zeros(n_adv, np.float32))
            if n_amb:
                xs.append(self.amb[rng_idx(rng, len(self.amb), n_amb)]); ys.append(np.zeros(n_amb, np.float32))
            yield np.concatenate(xs).astype(np.float32, copy=False), np.concatenate(ys)


def rng_idx(rng, n, k):
    return np.array(sorted(rng.sample(range(n), k)) if n >= k else [rng.randrange(n) for _ in range(k)])


class Prefetch:
    """Batches made on a thread so the GPU never waits on the disk."""

    def __init__(self, gen, depth=24):
        self.q = queue.Queue(depth)
        self.stop = False
        self.t = threading.Thread(target=self._run, args=(gen,), daemon=True)
        self.t.start()

    def _run(self, gen):
        for item in gen:
            if self.stop:
                return
            self.q.put(item)

    def __iter__(self):
        return self

    def __next__(self):
        return self.q.get()

    def close(self):
        self.stop = True
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass


def _device(cfg):
    import torch
    want = str(cfg.get('device') or 'auto')
    if want != 'cpu' and torch.cuda.is_available():
        return torch.device('cuda:0')
    return torch.device('cpu')


def _scores(model, arr, device, chunk=16384):
    import torch
    out = []
    with torch.no_grad():
        for i in range(0, len(arr), chunk):
            x = torch.from_numpy(np.ascontiguousarray(arr[i:i + chunk], dtype=np.float32)).to(device)
            out.append(model(x).squeeze(-1).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def _fp_per_hour(scores, thr, hours):
    """False alarms per hour on a stream scored per 80 ms window: one alarm per run of windows over the line."""
    if hours <= 0 or not len(scores):
        return None
    hot = scores >= thr
    fires = int(np.count_nonzero(hot[1:] & ~hot[:-1]) + (1 if hot[0] else 0))
    return fires / hours


def evaluate(model, data, device, thresholds=THRESHOLDS):
    """Recall on your held-out clips, false alarms per hour on the standard set and on your room, per threshold."""
    sp = _scores(model, data.val_pos, device) if data.val_pos is not None and len(data.val_pos) else np.zeros(0)
    sn = _scores(model, data.val_neg, device) if data.val_neg is not None and len(data.val_neg) else np.zeros(0)
    sf = _scores(model, data.val_fp, device)
    sa = _scores(model, data.val_amb, device) if data.val_amb is not None and len(data.val_amb) else np.zeros(0)
    amb_hours = data.val_amb_seconds / 3600.0
    rows = []
    for t in thresholds:
        rows.append({'threshold': t,
                     'recall': float((sp >= t).mean()) if len(sp) else None,
                     'neg_fp_rate': float((sn >= t).mean()) if len(sn) else None,
                     'fp_per_hour': _fp_per_hour(sf, t, VAL_HOURS),
                     'own_fa_per_hour': (float((sa >= t).sum()) / amb_hours) if len(sa) and amb_hours > 0 else None})
    return rows


def score_of(rows, target_fp=0.5, target_own=1.0):
    """One number for a sweep: the best recall at a threshold whose false alarms stay under the targets."""
    best = 0.0
    for r in rows:
        if r['recall'] is None:
            continue
        if (r['fp_per_hour'] or 0) <= target_fp and (r['own_fa_per_hour'] or 0) <= target_own:
            best = max(best, r['recall'])
    return best


def train(root, fdir, cfg, out_dir, progress=None, log=None, data=None, budget_steps=None, label='run'):
    """One training run. Returns the eval rows of the final model and writes run.json, metrics.jsonl, model.onnx."""
    compat.apply()
    import torch
    from openwakeword.train import Model
    cfg = {**DEFAULTS, **(cfg or {})}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = data or Data(root, fdir)
    device = _device(cfg)
    rng = np.random.default_rng(cfg['seed'])
    import random as _r
    prng = _r.Random(cfg['seed'])
    torch.manual_seed(cfg['seed'])
    model = Model(n_classes=1, input_shape=data.shape, model_type=cfg['model_type'], layer_dim=cfg['layer_size'],
                  n_blocks=cfg['n_blocks'], seconds_per_example=features.WINDOW_S)
    model.device = device
    model.to(device)
    model.model.to(device)
    opt = torch.optim.Adam(model.model.parameters(), lr=cfg['lr'])
    mfile = open(out_dir / 'metrics.jsonl', 'a', encoding='utf-8')
    t0 = time.time()
    best_models, best_scores, history = [], [], {'val_recall': [], 'val_n_fp': [], 'val_accuracy': [], 'val_fp_per_hr': []}
    global_step = 0
    total_budget = budget_steps or int(cfg['steps'] * (1 + 0.2 * (cfg['sequences'] - 1)))

    def say(pct, msg, **m):
        if progress:
            progress.update(pct, msg, **m)

    def sequence(steps, lr, neg_weight_max, seq_no):
        nonlocal global_step
        steps = int(steps)
        warm, hold = steps // 5, steps // 3
        weights = np.linspace(1, neg_weight_max, steps)
        val_every = int(cfg['val_every'] or max(250, steps // 40))
        feed = Prefetch(data.batches(cfg, prng))
        acc_pred, acc_y, acc_n, acc_steps = [], [], 0, 1
        for step in range(steps):
            x, y = next(feed)
            x = torch.from_numpy(x).to(device)
            y = torch.from_numpy(y).to(device)
            lr_now = float(model.lr_warmup_cosine_decay(step, warmup_steps=warm, hold=hold, total_steps=steps, target_lr=lr))
            for g in opt.param_groups:
                g['lr'] = lr_now
            opt.zero_grad()
            pred = model.model(x).squeeze(-1)
            keep = ((y == 0) & (pred >= 0.001)) | ((y == 1) & (pred < 0.999))   # upstream: train on what is not yet easy
            pred, yk = pred[keep], y[keep]
            if pred.shape[0] == 0:
                continue
            w = torch.where(yk == 1, torch.ones_like(yk), torch.full_like(yk, float(weights[step])))
            loss = torch.nn.functional.binary_cross_entropy(pred, yk, w) / acc_steps
            acc_n += pred.shape[0]
            acc_pred.append(pred.detach()); acc_y.append(yk.detach())
            if acc_n < 128:
                acc_steps += 1
                continue
            loss.backward()
            opt.step()
            P, Y = torch.cat(acc_pred), torch.cat(acc_y)
            tr_recall = float(((P >= 0.5) & (Y == 1)).sum() / max(1, (Y == 1).sum()))
            acc_pred, acc_y, acc_n, acc_steps = [], [], 0, 1
            global_step += 1
            if step % 50 == 0:
                say(100.0 * global_step / total_budget, f"{label}: seq {seq_no} step {step:,}/{steps:,} · loss {float(loss):.4f} · {time.time() - t0:.0f}s",
                    loss=round(float(loss), 5), step=global_step)
            if (step > 1 and step % val_every == 0) or step == steps - 1:
                rows = evaluate(model.model, data, device, thresholds=[0.5])
                r = rows[0]
                hist_fp = r['fp_per_hour'] if r['fp_per_hour'] is not None else 0.0
                history['val_recall'].append(r['recall'] if r['recall'] is not None else 0.0)
                history['val_n_fp'].append(r['neg_fp_rate'] if r['neg_fp_rate'] is not None else 0.0)
                history['val_fp_per_hr'].append(hist_fp)
                line = {'t': round(time.time() - t0, 1), 'seq': seq_no, 'step': global_step, 'loss': round(float(loss), 5), 'lr': lr_now,
                        'train_recall': round(tr_recall, 4), 'val_recall': r['recall'], 'val_neg_fp_rate': r['neg_fp_rate'],
                        'fp_per_hour': r['fp_per_hour'], 'own_fa_per_hour': r['own_fa_per_hour']}
                mfile.write(json.dumps(line) + '\n'); mfile.flush()
                if log:
                    log(line)
                # keep checkpoints that are good on both sides so far (upstream's rule)
                if history['val_n_fp'][-1] <= np.percentile(history['val_n_fp'], 50) and history['val_recall'][-1] >= np.percentile(history['val_recall'], 5):
                    best_models.append(copy.deepcopy(model.model))
                    best_scores.append({'step': global_step, 'val_recall': history['val_recall'][-1], 'val_n_fp': history['val_n_fp'][-1], 'fp_per_hour': hist_fp})
        feed.close()
        return min(history['val_fp_per_hr'][-5:]) if history['val_fp_per_hr'] else 0.0

    neg_w = cfg['max_negative_weight']
    last_fp = sequence(cfg['steps'] if not budget_steps else budget_steps, cfg['lr'], neg_w, 1)
    if not budget_steps and cfg['sequences'] >= 2:
        if last_fp > cfg['target_fp_per_hour']:
            neg_w *= 2
        last_fp = sequence(cfg['steps'] // 10, cfg['lr'] / 10, neg_w, 2)
    if not budget_steps and cfg['sequences'] >= 3:
        if last_fp > cfg['target_fp_per_hour']:
            neg_w *= 2
        sequence(cfg['steps'] // 10, cfg['lr'] / 100, neg_w, 3)
    mfile.close()

    # the final model: average of the checkpoints in the top tenth (upstream), else the last weights
    final = model.model
    if len(best_models) >= 3:
        rec_p = np.percentile([s['val_recall'] for s in best_scores], 90)
        fp_p = np.percentile([s['val_n_fp'] for s in best_scores], 10)
        picked = [m for m, s in zip(best_models, best_scores) if s['val_recall'] >= rec_p and s['val_n_fp'] <= fp_p]
        if len(picked) >= 1:
            final = model.average_models(models=picked)
    final.to(device)
    rows = evaluate(final, data, device)
    model.model = final
    try:
        export_onnx(final, data.shape, out_dir / 'model.onnx')
    except Exception as e:
        (out_dir / 'export_error.txt').write_text(f"{type(e).__name__}: {e}")
        if log:
            log({'warning': f"onnx export: {e}"})
    import torch as _t
    _t.save(final.state_dict(), out_dir / 'model.pt')
    result = {'rows': rows, 'score': score_of(rows), 'steps': global_step, 'seconds': round(time.time() - t0),
              'checkpoints_kept': len(best_models), 'cfg': cfg, 'features': str(fdir), 'params': sum(p.numel() for p in final.parameters())}
    write_json(out_dir / 'eval.json', result, indent=1)
    return result


def export_onnx(net, shape, path):
    """The classic exporter (the new one wants onnxscript and renames nothing). openWakeWord's runtime names the
    model after the file, so the output name is free."""
    import torch
    net = copy.deepcopy(net).cpu().eval()
    torch.onnx.export(net, torch.rand(1, *shape), str(path), dynamo=False, input_names=['input'], output_names=['output'], opset_version=13)


def _recipe_of_set(fdir):
    """The recipe the feature set on disk was built with: the truth a run reports."""
    try:
        return json.loads((Path(fdir) / 'meta.json').read_text()).get('recipe') or 'natural'
    except Exception:
        return 'natural'


def run_job(root, args, progress):
    """The train job: features (reused when the key matches), then one run with the config in args."""
    slug = args['project']
    pdir = paths.project_dir(root, slug)
    project = store.load_project(root, slug)
    preset = PRESETS.get(str(args.get('preset') or 'standard'), PRESETS['standard'])
    cfg = {**DEFAULTS, **preset, **{k: v for k, v in (args.get('cfg') or {}).items() if k in DEFAULTS}, 'device': args.get('device', 'auto')}
    progress.step('features', 'building the feature set (reused if nothing changed)')
    key = features.build_oww(root, {'project': slug, 'recipe': args.get('recipe'), 'synth': args.get('synth'), 'exclude_near': args.get('exclude_near'), 'rounds': args.get('rounds', 2), 'holdout': args.get('holdout', True), 'seed': cfg['seed'],
                                    'device': args.get('device')}, _Quiet(progress))
    fdir = pdir / 'features' / 'oww' / key
    stamp = time.strftime('%Y%m%d-%H%M%S')
    run_dir = pdir / 'runs' / f"{stamp}-oww"
    run_dir.mkdir(parents=True, exist_ok=True)
    run = {'id': run_dir.name, 'trainer': 'oww', 'preset': args.get('preset', 'standard'), 'cfg': cfg, 'features': key,
           'recipe': _recipe_of_set(fdir), 'synth': args.get('synth') or 'all', 'exclude_near': args.get('exclude_near') or [], 'holdout': args.get('holdout', True),
           'started': time.time(), 'state': 'running', 'label': args.get('label') or ''}
    write_json(run_dir / 'run.json', run, indent=1)
    progress.step('train', f"training {cfg['model_type']} {cfg['layer_size']}×{cfg['n_blocks']} for {cfg['steps']:,} steps")
    try:
        result = train(root, fdir, cfg, run_dir, progress=progress)
        run.update({'state': 'done', 'ended': time.time(), 'score': result['score'], 'rows': result['rows'], 'seconds': result['seconds'], 'params': result['params']})
    except Exception as e:
        run.update({'state': 'failed', 'ended': time.time(), 'error': f"{type(e).__name__}: {e}"})
        write_json(run_dir / 'run.json', run, indent=1)
        raise
    write_json(run_dir / 'run.json', run, indent=1)
    best = max((r for r in result['rows'] if r['recall'] is not None), key=lambda r: (score_of([r]), r['recall']), default=None)
    verdict = _grade(root, slug, run_dir.name, result['rows'], progress)
    progress.done({'run': run_dir.name, 'score': result['score'], 'best': best, 'seconds': result['seconds'], 'heldout': verdict})


def _grade(root, slug, rid, rows, progress):
    """The streaming judge on the held-out clips, the same one Test uses; its verdict goes into run.json."""
    progress.step('judge', 'judging the finished model on your held-out clips')
    try:
        from . import judge
        v = judge.grade(root, slug, rid, rows)
        progress.step('judge', f"held-out: {100 * (v.get('recall') or 0):.0f}% heard · {v.get('near_fires', 0)} of {v.get('n_near', 0)} sound-alikes fired · threshold {v.get('threshold')}")
        return v
    except Exception as e:
        progress.step('judge', f"could not judge: {type(e).__name__}: {e}")
        return None


class _Quiet:
    """Progress for the feature step inside a train job: same bar, shifted words."""
    def __init__(self, p):
        self.p = p
    def step(self, name, msg=''):
        self.p.step(name, msg)
    def update(self, pct, msg='', **m):
        self.p.update(pct * 0.3, 'features: ' + msg, **m)
    def done(self, result=None):
        self.p.step('features', f"feature set ready: {result.get('counts') if result else ''}")
    def fail(self, e):
        raise RuntimeError(e)
