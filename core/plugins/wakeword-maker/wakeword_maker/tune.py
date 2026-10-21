# tune.py - the refine: Optuna walks the desktop trainer's knobs, each trial a short run on the same feature memmaps
# (one process, threads, so the data is loaded once), scored by the streaming judge on your held-out clips, the
# scoreboard's own numbers. After a grid it starts from the grid's best cells: the grid finds the candidates, the
# refine masters them. The best trial then trains in full and is judged. Everything a trial does lands in
# <run>/trials/<n>/ with its own metrics, and trials.jsonl is what the page draws.
import json
import threading
import time
from pathlib import Path

from . import features, paths, store, train_oww
from .paths import write_json

SPACE = {   # what the refine may touch; the page shows the same list with checkboxes
    'layer_size': [32, 64, 128],
    'n_blocks': [1, 2, 3],
    'lr': (3e-5, 3e-4),
    'max_negative_weight': [500, 1000, 1500, 3000],
    'model_type': ['dnn', 'rnn'],
}
TOP = 3         # judged cells a refine starts from


def judge_score(v):
    """One number from a verdict, the way the scoreboard reads it: mostly recall under stress, a tenth off per
    sound-alike that fires (five at most), a third off when any other negative fires. None when nothing was judged."""
    if not v or v.get('recall') is None:
        return None
    stress = v.get('stress_recall') if v.get('stress_recall') is not None else v['recall']
    s = 0.3 * float(v['recall']) + 0.7 * float(stress) - 0.1 * min(5, int(v.get('near_fires') or 0)) - (0.3 if v.get('neg_fires') else 0.0)
    return round(s, 4)


def candidates(root, slug, grid=None, top=TOP):
    """The judged desktop runs, best first by the judge's score (one grid's cells when `grid` is given), the
    `top` of them. Verdicts from the held-out slice as it stands rank before stale ones (a slice reshuffled or a
    judge take recorded since), whatever they scored. A refine or a sweep run is never a candidate itself."""
    from . import holdout, judge as _judge
    pdir = paths.project_dir(root, slug)
    hk, hw = holdout.key(pdir), (holdout.read(pdir) or {}).get('when')
    d = pdir / 'runs'
    out = []
    for sub in (d.iterdir() if d.is_dir() else []):
        try:
            r = json.loads((sub / 'run.json').read_text(encoding='utf-8'))
        except Exception:
            continue
        if r.get('trainer') != 'oww' or r.get('state') != 'done' or r.get('kind') == 'tune' or not r.get('cfg'):
            continue
        if grid and r.get('grid') != grid:
            continue
        s = judge_score(r.get('heldout'))
        if s is None:
            continue
        stale = 1
        try:
            c = json.loads((sub / 'judge.json').read_text(encoding='utf-8'))
            stale = 0 if (c.get('held_key', hk) == hk and c.get('holdout_when') == hw and c.get('stress_key') == _judge.STRESS_KEY) else 1
        except Exception:
            pass                                       # no cached judging: which slice the verdict came from is unknown
        out.append((stale, -s, r.get('id', ''), r))
    out.sort(key=lambda x: x[:3])
    return [x[3] for x in out[:max(1, int(top))]]


def _around(values, winners):
    """The winners' values and their neighbours on the list, in the list's order."""
    idx = {i for i, v in enumerate(values) if v in winners}
    keep = {j for i in idx for j in (i - 1, i, i + 1) if 0 <= j < len(values)}
    return [v for i, v in enumerate(values) if i in keep] or list(values)


def space_around(cands, which):
    """A search space centred on the candidates: each knob's winning values plus their neighbours; the learning
    rate from a third of the lowest to three times the highest, inside the full range."""
    cfgs = [c.get('cfg') or {} for c in cands]
    out = {}
    for k in which:
        if k not in SPACE:
            continue
        if k == 'lr':
            lrs = [float(c['lr']) for c in cfgs if c.get('lr')]
            lo, hi = SPACE['lr']
            out[k] = (max(lo, min(lrs) / 3), min(hi, max(lrs) * 3)) if lrs else SPACE['lr']
        else:
            out[k] = _around(SPACE[k], {c.get(k) for c in cfgs})
    return out


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
    grid = args.get('from_grid')
    cands = candidates(root, slug, grid=grid, top=int(args.get('top') or TOP)) if (grid or args.get('refine')) else []
    if cands:        # a refine: the best judged cell's recipe, data and knobs are the start; the space is drawn around the winners
        best0 = cands[0]
        for k in ('recipe', 'sources', 'holdout'):
            if k in best0:
                args[k] = best0[k]
        base = {**train_oww.DEFAULTS, **train_oww.PRESETS.get(final_preset, {}),
                **{k: v for k, v in (best0.get('cfg') or {}).items() if k in train_oww.DEFAULTS and k not in ('steps', 'seed', 'device')}, 'device': args.get('device', 'auto')}
        space = space_around(cands, which)
        progress.step('candidates', f"refining around {len(cands)} judged cell(s): " + ', '.join(f"{c.get('label') or c['id']} ({judge_score(c.get('heldout')):.2f})" for c in cands))
    else:
        base = {**train_oww.DEFAULTS, **train_oww.PRESETS.get(final_preset, {}), **{k: v for k, v in (args.get('cfg') or {}).items() if k in train_oww.DEFAULTS},
                'device': args.get('device', 'auto')}
        space = {k: SPACE[k] for k in which}
        if grid or args.get('refine'):
            progress.step('candidates', 'no judged desktop cell to start from yet: a plain sweep over the full space')

    progress.step('features', 'building the feature set (reused if nothing changed)')
    sources = features.sources_of(args)
    key = features.build_oww(root, {'project': slug, 'recipe': args.get('recipe'), 'sources': sources, 'exclude_near': args.get('exclude_near'), 'rounds': args.get('rounds', 2), 'holdout': args.get('holdout', True), 'seed': base['seed'],
                                    'device': args.get('device')}, train_oww._Quiet(progress))
    fdir = pdir / 'features' / 'oww' / key
    stamp = time.strftime('%Y%m%d-%H%M%S')
    run_dir = pdir / 'runs' / f"{stamp}-tune"
    (run_dir / 'trials').mkdir(parents=True, exist_ok=True)
    run = {'id': run_dir.name, 'trainer': 'oww', 'kind': 'tune', 'trials': n_trials, 'budget_steps': budget, 'parallel': n_jobs, 'space': which,
           'space_used': {k: list(v) for k, v in space.items()}, 'refine_of': grid, 'candidates': [c['id'] for c in cands], 'grid': args.get('grid'),
           'features': key, 'recipe': _recipe_of_set(fdir), 'synth': sources['synth'], 'sources': sources, 'sources_label': features.sources_label(sources), 'exclude_near': args.get('exclude_near') or [], 'holdout': args.get('holdout', True),
           'started': time.time(), 'state': 'running', 'preset': final_preset, 'label': args.get('label') or ''}
    write_json(run_dir / 'run.json', run, indent=1)
    data = train_oww.Data(root, fdir)
    tfile = open(run_dir / 'trials.jsonl', 'a', encoding='utf-8')
    lock = threading.Lock()
    done = {'n': 0}

    def grade_trial(tdir, cfg, res):
        """The streaming judge on a trial's model: the scoreboard's numbers, so the refine chases what the page shows.
        One at a time (the caller holds the lock). None when the trial left no model the judge could load."""
        rid = f"{run_dir.name}/trials/{tdir.name}"
        write_json(tdir / 'run.json', {'id': rid, 'trainer': 'oww', 'state': 'done', 'cfg': cfg, 'rows': res['rows']}, indent=1)
        from . import judge
        out = judge.judge(root, slug, rid)
        return judge.summary(out['rows'], res['rows'], 'oww')

    def objective(trial):
        cfg = dict(base)
        for k in ('layer_size', 'n_blocks', 'max_negative_weight', 'model_type'):
            if k in space:
                cfg[k] = trial.suggest_categorical(k, list(space[k]))
        if 'lr' in space:
            cfg['lr'] = trial.suggest_float('lr', *space['lr'], log=True)
        cfg['seed'] = base['seed'] + trial.number
        tdir = run_dir / 'trials' / f"{trial.number:02d}"
        t0 = time.time()
        res = train_oww.train(root, fdir, cfg, tdir, progress=None, data=data, budget_steps=budget, label=f"trial {trial.number}")
        # one scale for the whole study: the judge's number, or (when the judge has no held-out takes to hear, the same for
        # every trial) the trainer's own; a trial the judge could not load is pruned, never compared with the judged ones
        score, verdict, judged = res['score'], None, False
        with lock:
            try:
                verdict = grade_trial(tdir, cfg, res)
                js = judge_score(verdict)
                if js is not None:
                    score, judged = js, True
            except Exception as e:
                verdict = {'error': f"{type(e).__name__}: {e}"}
            line = {'trial': trial.number, 'params': trial.params, 'score': score, 'judged': judged, 'rows': res['rows'], 'seconds': round(time.time() - t0),
                    'best_threshold': max((r for r in res['rows'] if r['recall'] is not None), key=lambda r: (train_oww.score_of([r]), r['recall']), default={}).get('threshold'),
                    'heldout': {k: verdict.get(k) for k in ('recall', 'stress_recall', 'near_fires', 'neg_fires', 'threshold', 'error')} if verdict else None}
            done['n'] += 1
            tfile.write(json.dumps(line) + '\n'); tfile.flush()
            heard = f" · hears {100 * verdict['recall']:.0f}%, stressed {100 * (verdict.get('stress_recall') or 0):.0f}%" if verdict and verdict.get('recall') is not None else (f" · not judged: {verdict['error']}" if verdict and verdict.get('error') else '')
            progress.update(30 + 55.0 * done['n'] / n_trials, f"trial {trial.number} done: score {score:.3f} ({done['n']} of {n_trials}) · {cfg['model_type']} {cfg['layer_size']}×{cfg['n_blocks']} lr {cfg['lr']:.1e}{heard}",
                            force=True, trial=trial.number, score=score)
        if verdict and verdict.get('error'):
            raise optuna.TrialPruned()
        return score

    progress.step('tune', f"{n_trials} trials × {budget:,} steps, {n_jobs} at a time, over {', '.join(space)}")
    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=base['seed']))
    study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs)
    tfile.close()
    if not any(t.state == optuna.trial.TrialState.COMPLETE for t in study.trials):
        run.update({'state': 'failed', 'ended': time.time(), 'error': 'no trial could be judged: see trials.jsonl (did the ONNX export fail?)'})
        write_json(run_dir / 'run.json', run, indent=1)
        raise RuntimeError(run['error'])
    best = study.best_trial
    run.update({'best_trial': best.number, 'best_params': best.params, 'best_score': best.value})
    write_json(run_dir / 'run.json', run, indent=1)

    # the winner, in full, then judged like any run (the verdict lands in run.json)
    cfg = {**base, **best.params}
    progress.step('train', f"best trial {best.number} (score {best.value:.3f}) trains in full: {cfg['model_type']} {cfg['layer_size']}×{cfg['n_blocks']} for {cfg['steps']:,} steps")
    final_dir = run_dir / 'final'
    res = train_oww.train(root, fdir, cfg, final_dir, progress=_Shift(progress, 85, 13), data=data, label='final')
    run.update({'state': 'done', 'ended': time.time(), 'score': res['score'], 'rows': res['rows'], 'cfg': cfg, 'seconds': res['seconds'], 'params': res['params']})
    write_json(run_dir / 'run.json', run, indent=1)
    verdict = train_oww._grade(root, slug, run_dir.name, res['rows'], progress)
    progress.done({'run': run_dir.name, 'best_trial': best.number, 'best_params': best.params, 'score': res['score'], 'heldout': verdict})


class _Shift:
    def __init__(self, p, start, span):
        self.p, self.start, self.span = p, start, span
    def update(self, pct, msg='', **m):
        self.p.update(self.start + self.span * pct / 100.0, msg, **m)
    def step(self, name, msg=''):
        self.p.step(name, msg)
