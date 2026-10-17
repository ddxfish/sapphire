# cli.py - the engine from a terminal, and the entry every job process runs.
#   python -m wakeword_maker.cli job <job dir>                       (what Sapphire spawns)
#   python -m wakeword_maker.cli --root <data dir> projects
#   python -m wakeword_maker.cli --root <data dir> new "hey sapphire"
#   python -m wakeword_maker.cli --root <data dir> run <kind> [--project slug] [--arg key=value ...]
# `run` does the same work as a job started from the page, with progress printed, so a box without the UI
# can download, synthesize, rate and train. Run it under the plugin's conda env.
import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import paths


def _nap(root, args, progress):
    """Sleeps and reports: the tests' stand-in for a long job (no route starts it)."""
    import time
    n = float(args.get('seconds', 5))
    for i in range(int(n * 10)):
        progress.update(100 * i / (n * 10), 'napping', force=True)
        time.sleep(0.1)
    progress.done({'napped': n})


def job_table():
    """kind -> run(root, args, progress). Imported late so the light kinds never load the heavy packages."""
    from . import download
    table = {'download': download.run_download, 'delete_dataset': download.run_delete, 'nap': _nap}
    try:
        from . import synth
        table['synth'] = synth.run
    except Exception as e:  # the heavy side is missing in this interpreter: those kinds fail with a clear word
        table['synth'] = _missing('synth', e)
    try:
        from . import qa
        table['qa'] = qa.run
    except Exception as e:
        table['qa'] = _missing('qa', e)
    from . import voices
    table['voices'] = voices.fetch
    try:
        from . import features, train_oww
        table['features'] = features.build_oww
        table['train'] = train_oww.run_job
    except Exception as e:
        table['features'] = _missing('features', e)
        table['train'] = _missing('train', e)
    try:
        from . import tune
        table['tune'] = tune.run_job
    except Exception as e:
        table['tune'] = _missing('tune', e)
    try:
        from . import judge
        table['judge'] = judge.run_job
    except Exception as e:
        table['judge'] = _missing('judge', e)
    try:
        from . import features_mww, train_mww
        table['features_mww'] = features_mww.build
        table['train_mww'] = train_mww.run_job
    except Exception as e:
        table['features_mww'] = _missing('features_mww', e)
        table['train_mww'] = _missing('train_mww', e)
    return table


def _missing(kind, err):
    def run(root, args, progress):
        raise RuntimeError(f"{kind} needs the plugin's environment (build it in Settings): {err}")
    return run


class _Printer:
    """A Progress look-alike for the terminal."""
    def step(self, name, msg=''):
        print(f"== {msg or name}", flush=True)

    def update(self, pct, msg='', force=False, **metrics):
        extra = ' '.join(f"{k}={v}" for k, v in metrics.items() if not isinstance(v, (dict, list)))
        print(f"\r{pct:5.1f}%  {msg} {extra}".ljust(100), end='', flush=True)

    def done(self, result=None):
        print(f"\ndone {json.dumps(result) if result else ''}", flush=True)

    def fail(self, error):
        print(f"\nfailed: {error}", file=sys.stderr, flush=True)

    def cancelled(self):
        print("\ncancelled", flush=True)


def run_job(job_dir):
    from .jobs import Progress
    job_dir = Path(job_dir)
    job = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
    root = Path(os.environ.get('WWM_DATA_DIR') or job_dir.parents[1])
    progress = Progress(job_dir)

    def on_term(signum, frame):
        progress.stopped()          # cancelled if the canceller said so, interrupted otherwise; a no-op in forked workers
        os._exit(130)
    signal.signal(signal.SIGTERM, on_term)
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, on_term)

    kind = job.get('kind')
    run = job_table().get(kind)
    if not run:
        progress.fail(f"unknown job kind: {kind}")
        return 2
    try:
        run(root, {**job.get('args', {}), 'project': job.get('project')}, progress)
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        progress.fail(f"{type(e).__name__}: {e}")
        return 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog='wakeword_maker')
    ap.add_argument('--root', help='the data folder (or WWM_DATA_DIR)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    j = sub.add_parser('job')
    j.add_argument('job_dir')
    sub.add_parser('projects')
    n = sub.add_parser('new')
    n.add_argument('phrase')
    r = sub.add_parser('run')
    r.add_argument('kind')
    r.add_argument('--project')
    r.add_argument('--arg', action='append', default=[], help='key=value (value parsed as JSON when it is JSON)')
    sub.add_parser('datasets')
    a = ap.parse_args(argv)

    if a.cmd == 'job':
        return run_job(a.job_dir)
    root = paths.data_root({'data_dir': a.root}) if a.root else paths.data_root()
    if not root:
        ap.error('--root (or WWM_DATA_DIR) is needed')
    from . import store, catalog
    if a.cmd == 'projects':
        for p in store.list_projects(root):
            print(f"{p['slug']:30} {p['phrase']!r}  {sum(p['counts'].values())} clips")
        return 0
    if a.cmd == 'new':
        p = store.create_project(root, a.phrase)
        print(f"created {p['slug']} at {paths.project_dir(root, p['slug'])}")
        return 0
    if a.cmd == 'datasets':
        for d in catalog.status(root):
            print(f"{d['id']:16} {d['state']:8} {d['bytes'] / 1e9:7.2f} GB  {d['label']}  [{d['licence']}]")
        return 0
    if a.cmd == 'run':
        args = {}
        for kv in a.arg:
            k, _, v = kv.partition('=')
            try:
                args[k] = json.loads(v)
            except Exception:
                args[k] = v
        args['project'] = a.project
        run = job_table().get(a.kind)
        if not run:
            ap.error(f"unknown kind {a.kind}; kinds: {', '.join(job_table())}")
        pr = _Printer()
        try:
            run(root, args, pr)
        except Exception as e:
            pr.fail(e)
            return 1
        return 0


if __name__ == '__main__':
    sys.exit(main())
