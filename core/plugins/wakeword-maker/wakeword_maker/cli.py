# cli.py - the engine from a terminal, and the entry every job process runs.
#   python -m wakeword_maker.cli job <job dir>                       (what Sapphire spawns)
#   python -m wakeword_maker.cli --root <data dir> projects
#   python -m wakeword_maker.cli --root <data dir> new "hey sapphire"
#   python -m wakeword_maker.cli --root <data dir> run <kind> [--project slug] [--arg key=value ...]
#   python -m wakeword_maker.cli road [--live https://localhost:8073] [--esp32] [--keep] [--only step,step] [--json]
# `run` does the same work as a job started from the page, with progress printed, so a box without the UI
# can download, synthesize, rate and train. Run it under the plugin's conda env. `road` walks every door of the
# running Sapphire start to finish on a scratch wake word (wakeword_maker/road.py); under the sapphire env it
# also grabs the PC microphone for the level check.
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


def _road(a, ap):
    from . import road
    base = a.live
    if not base:
        s = {}
        try:
            s = json.loads((paths.SAPPHIRE_ROOT / 'user' / 'settings.json').read_text(encoding='utf-8'))
        except Exception:
            pass
        base = f"{'https' if s.get('WEB_UI_SSL_ADHOC', True) else 'http'}://localhost:{int(s.get('WEB_UI_PORT') or 8073)}"
    token = a.token or os.environ.get('SAPPHIRE_API_TOKEN') or ''
    tf = Path(token).expanduser() if token else paths.SAPPHIRE_ROOT / 'user' / 'sapphire-health.token'
    if tf.is_file():
        token = tf.read_text(encoding='utf-8').strip()
    if not token:
        ap.error('an API token is needed: Settings > System > API keys, or `bash user/sapphire-health.sh --init`; pass --token or set SAPPHIRE_API_TOKEN')
    say = (lambda s: None) if a.json else (lambda s: print(s, flush=True))
    say(f"the road on {base} · phrase '{a.phrase}'" + (' · esp32' if a.esp32 else ''))
    r = road.Road(road.Live(base, token), phrase=a.phrase, esp32=a.esp32, install=not a.no_install, mic=not a.no_mic,
                  satellites=not a.no_satellites, keep=a.keep, sampler='live', slow=a.slow, say=say)
    rep = r.walk(only=[x.strip() for x in a.only.split(',') if x.strip()] if a.only else None)
    print(json.dumps(rep.rows, indent=1) if a.json else '\n' + rep.table(), flush=True)
    return len(rep.failed)


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
    rd = sub.add_parser('road', help='every door start to finish on a scratch wake word, against the running Sapphire')
    rd.add_argument('--live', metavar='BASE', help='Sapphire, default https://localhost:<WEB_UI_PORT> from user/settings.json')
    rd.add_argument('--token', help='an API token or a file holding one; default $SAPPHIRE_API_TOKEN, else user/sapphire-health.token')
    rd.add_argument('--phrase', default='hey road test', help='the scratch wake word (made, walked, deleted)')
    rd.add_argument('--esp32', action='store_true', help='train the ESP32 family too (minutes more)')
    rd.add_argument('--no-install', action='store_true', help='leave this computer\'s wake word alone')
    rd.add_argument('--no-mic', action='store_true', help='no level check through the PC microphone')
    rd.add_argument('--no-satellites', action='store_true', help='no satellite listens, no satellite room day')
    rd.add_argument('--keep', action='store_true', help='leave the scratch wake word and its pieces for a look')
    rd.add_argument('--only', help='comma-separated steps to walk on an existing scratch wake word')
    rd.add_argument('--slow', type=float, default=1.0, help='timeout multiplier (a CPU box: 4)')
    rd.add_argument('--json', action='store_true', help='the report as JSON rows')
    a = ap.parse_args(argv)

    if a.cmd == 'job':
        return run_job(a.job_dir)
    if a.cmd == 'road':
        return _road(a, ap)
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
