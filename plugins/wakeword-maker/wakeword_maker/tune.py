# tune.py - the sweep: Optuna walks the trainer's knobs, each trial a short run on the same feature memmaps (one
# process, threads, so the data is loaded once), scored by the best recall on your held-out clips at a threshold
# whose false alarms stay under the targets. The best trial then trains in full. Everything a trial does lands in
# <run>/trials/<n>/ with its own metrics, and trials.jsonl is what the page draws.
import json
import threading
import time
from pathlib import Path

from . import features, paths, store, train_oww
from .paths import write_json

SPACE = {   # what the sweep may touch; the page shows the same list with checkboxes
    'layer_size': [32, 64, 128],
    'n_blocks': [1, 2, 3],
    'lr': (3e-5, 3e-4),
    'max_negative_weight': [500, 1000, 1500, 3000],
    'model_type': ['dnn', 'rnn'],
}


def _recipe_of_set(fdir):
    """The recipe the feature set on disk was built with: the truth a run reports."""
    try:
        return json.loads((Path(fdir) / 'meta.json').read_text()).get('recipe') or 'natural'
    except Exception:
        return 'natural'


def run_job(root, args, progress):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    slug = args['project']
    pdir = paths.project_dir(root, slug)
    project = store.load_project(root, slug)
    n_trials = max(2, min(60, int(args.get('trials') or 12)))
    budget = max(1000, int(args.get('budget_steps') or 8000))
    n_jobs = max(1, min(4, int(args.get('parallel') or 2)))
    which = [k for k in (args.get('space') or list(SPACE)) if k in SPACE]
    final_preset = str(args.get('preset') or 'standard')
    base = {**train_oww.DEFAULTS, **train_oww.PRESETS.get(final_preset, {}), **{k: v for k, v in (args.get('cfg') or {}).items() if k in train_oww.DEFAULTS},
            'device': args.get('device', 'auto')}

    progress.step('features', 'building the feature set (reused if nothing changed)')
    key = features.build_oww(root, {'project': slug, 'recipe': args.get('recipe'), 'synth': args.get('synth'), 'exclude_near': args.get('exclude_near'), 'rounds': args.get('rounds', 2), 'holdout': args.get('holdout', True), 'seed': base['seed'],
                                    'device': args.get('device')}, train_oww._Quiet(progress))
    fdir = pdir / 'features' / 'oww' / key
    stamp = time.strftime('%Y%m%d-%H%M%S')
    run_dir = pdir / 'runs' / f"{stamp}-tune"
    (run_dir / 'trials').mkdir(parents=True, exist_ok=True)
    run = {'id': run_dir.name, 'trainer': 'oww', 'kind': 'tune', 'trials': n_trials, 'budget_steps': budget, 'parallel': n_jobs, 'space': which,
           'features': key, 'recipe': _recipe_of_set(fdir), 'synth': args.get('synth') or 'all', 'exclude_near': args.get('exclude_near') or [], 'holdout': args.get('holdout', True),
           'started': time.time(), 'state': 'running', 'preset': final_preset, 'label': args.get('label') or ''}
    write_json(run_dir / 'run.json', run, indent=1)
    data = train_oww.Data(root, fdir)
    tfile = open(run_dir / 'trials.jsonl', 'a', encoding='utf-8')
    lock = threading.Lock()
    done = {'n': 0}

    def objective(trial):
        cfg = dict(base)
        if 'layer_size' in which:
            cfg['layer_size'] = trial.suggest_categorical('layer_size', SPACE['layer_size'])
        if 'n_blocks' in which:
            cfg['n_blocks'] = trial.suggest_categorical('n_blocks', SPACE['n_blocks'])
        if 'lr' in which:
            cfg['lr'] = trial.suggest_float('lr', *SPACE['lr'], log=True)
        if 'max_negative_weight' in which:
            cfg['max_negative_weight'] = trial.suggest_categorical('max_negative_weight', SPACE['max_negative_weight'])
        if 'model_type' in which:
            cfg['model_type'] = trial.suggest_categorical('model_type', SPACE['model_type'])
        cfg['seed'] = base['seed'] + trial.number
        tdir = run_dir / 'trials' / f"{trial.number:02d}"
        t0 = time.time()
        res = train_oww.train(root, fdir, cfg, tdir, progress=None, data=data, budget_steps=budget, label=f"trial {trial.number}")
        line = {'trial': trial.number, 'params': trial.params, 'score': res['score'], 'rows': res['rows'], 'seconds': round(time.time() - t0),
                'best_threshold': max((r for r in res['rows'] if r['recall'] is not None), key=lambda r: (train_oww.score_of([r]), r['recall']), default={}).get('threshold')}
        with lock:
            done['n'] += 1
            tfile.write(json.dumps(line) + '\n'); tfile.flush()
            progress.update(30 + 55.0 * done['n'] / n_trials, f"trial {trial.number} done: score {res['score']:.3f} ({done['n']} of {n_trials}) · {cfg['model_type']} {cfg['layer_size']}×{cfg['n_blocks']} lr {cfg['lr']:.1e}",
                            force=True, trial=trial.number, score=res['score'])
        return res['score']

    progress.step('tune', f"{n_trials} trials × {budget:,} steps, {n_jobs} at a time, over {', '.join(which)}")
    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=base['seed']))
    study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs)
    tfile.close()
    best = study.best_trial
    run.update({'best_trial': best.number, 'best_params': best.params, 'best_score': best.value})
    write_json(run_dir / 'run.json', run, indent=1)

    # the winner, in full
    cfg = {**base, **best.params}
    progress.step('train', f"best trial {best.number} (score {best.value:.3f}) trains in full: {cfg['model_type']} {cfg['layer_size']}×{cfg['n_blocks']} for {cfg['steps']:,} steps")
    final_dir = run_dir / 'final'
    res = train_oww.train(root, fdir, cfg, final_dir, progress=_Shift(progress, 85, 15), data=data, label='final')
    run.update({'state': 'done', 'ended': time.time(), 'score': res['score'], 'rows': res['rows'], 'cfg': cfg, 'seconds': res['seconds'], 'params': res['params']})
    write_json(run_dir / 'run.json', run, indent=1)
    progress.done({'run': run_dir.name, 'best_trial': best.number, 'best_params': best.params, 'score': res['score']})


class _Shift:
    def __init__(self, p, start, span):
        self.p, self.start, self.span = p, start, span
    def update(self, pct, msg='', **m):
        self.p.update(self.start + self.span * pct / 100.0, msg, **m)
    def step(self, name, msg=''):
        self.p.step(name, msg)
