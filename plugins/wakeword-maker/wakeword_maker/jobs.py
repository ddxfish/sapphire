# jobs.py - jobs are processes. Sapphire starts `<env python> -m wakeword_maker.cli job <job dir>` in its own
# session and watches a file; the child writes the file. Nothing heavy ever runs in Sapphire's process and GPU
# memory is freed when a job ends.
#   <root>/jobs/<id>/job.json        kind, args, project, pid, born (the process start time), started, queued_at
#   <root>/jobs/<id>/progress.jsonl  one line per update: {t, pct, step, msg, metrics}
#   <root>/jobs/<id>/status.json     {state: queued|done|failed|cancelled|interrupted, result|error, ended}
#   <root>/jobs/<id>/log.txt         the child's stdout and stderr
# Who writes what: the job writes its own epitaph (done, failed, interrupted); Sapphire's side writes queued and
# cancelled, and reports failed when the process is gone without a word. A pid alone is never trusted: job.json
# records when the process was born, and a pid the kernel handed to someone else no longer matches.
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .paths import job_env, jobs_dir, read_json, write_json

logger = logging.getLogger(__name__)

LIGHT = ('download', 'delete_dataset')     # stdlib-only kinds: run under Sapphire's python when the env is not built yet
HEAVY = ('train', 'train_mww', 'tune', 'features', 'features_mww')   # one of these at a time per wake word: the rest queue
TERMINAL = ('done', 'failed', 'cancelled', 'interrupted')
EVENT = 'wakeword_maker.job'               # event bus type the page listens for
KEEP = 100                                 # finished job folders kept per data root; older ones are pruned
_tails = {}                                # job id -> thread
_lock = threading.Lock()                   # tails table
_qlock = threading.RLock()                 # enqueue / promote / cancel: one decision at a time
_stopped = threading.Event()               # set on unload: tails of this module stop without promoting


# --- the child's side --------------------------------------------------------------------------------

class Progress:
    """What a job writes. update() is throttled so a tight loop does not flood the file. A write that fails (full
    disk) is dropped, not raised: progress is a courtesy, the status file is what matters."""

    def __init__(self, job_dir, every=0.5):
        self.dir = Path(job_dir)
        self.pid = os.getpid()          # forked workers inherit this object; only the owner writes status.json
        self.every = every
        self._last = 0.0
        self._step = ''
        self._lock = threading.Lock()
        self._fh = open(self.dir / 'progress.jsonl', 'a', encoding='utf-8')

    def step(self, name, msg=''):
        self._step = name
        self._write(0, msg or name, force=True)

    def update(self, pct, msg='', force=False, **metrics):
        self._write(pct, msg, force=force, **metrics)

    def _write(self, pct, msg, force=False, **metrics):
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last < self.every:
                return
            self._last = now
            line = {'t': time.time(), 'pct': round(float(pct or 0), 2), 'step': self._step, 'msg': str(msg or '')}
            if metrics:
                line['metrics'] = metrics
            try:
                self._fh.write(json.dumps(line) + '\n')
                self._fh.flush()
            except OSError as e:
                print(f"progress line lost: {e}", file=sys.stderr, flush=True)

    def done(self, result=None):
        if os.getpid() != self.pid:
            return
        _write_status(self.dir, 'done', result=result)
        self._write(100, 'done', force=True)

    def fail(self, error):
        if os.getpid() != self.pid:
            return
        _write_status(self.dir, 'failed', error=str(error))       # the epitaph first: it is the one write that must land
        self._write(0, f"failed: {error}", force=True)

    def cancelled(self):
        if os.getpid() == self.pid:
            _write_status(self.dir, 'cancelled')

    def stopped(self):
        """A SIGTERM arrived. The canceller writes `cancelled` before it signals, so anything else (a Sapphire
        restart, a kill from outside) is an interruption the user did not ask for."""
        if os.getpid() != self.pid:
            return
        if (read_json(self.dir / 'status.json') or {}).get('state') == 'cancelled':
            return
        _write_status(self.dir, 'interrupted', error='stopped from outside (a Sapphire restart or a kill); run it again')


def _write_status(job_dir, state, **extra):
    data = {'state': state, 'ended': time.time(), **extra}
    write_json(Path(job_dir) / 'status.json', data)


# --- Sapphire's side -----------------------------------------------------------------------------------

_procs = {}       # pid -> Popen of the jobs this process spawned: polling reaps them (a zombie still answers kill 0)


def _born(pid):
    """When the process started, as a string the kernel will not reuse: a recycled pid has a different one."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(')', 1)[1].split()[19]
    except Exception:
        pass
    try:
        import psutil
        return str(int(psutil.Process(pid).create_time()))
    except Exception:
        return None


def _alive(pid, born=None):
    if not pid:
        return False
    pid = int(pid)
    proc = _procs.get(pid)
    if proc is not None:                        # our own child: poll() reaps it and is exact
        if proc.poll() is None:
            return True
        _procs.pop(pid, None)
        return False
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            return False
    except ImportError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        except Exception:
            return False
        try:                                    # not our child: a zombie of someone else's still answers kill 0
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(')', 1)[1].split()[0] == 'Z':
                    return False
        except Exception:
            pass
    except Exception:
        return False
    if born:
        now = _born(pid)
        if now and now != str(born):
            return False                        # the pid lives on in an unrelated process
    return True


def read(job_dir):
    """Everything the page wants to know about one job."""
    job_dir = Path(job_dir)
    job = read_json(job_dir / 'job.json')
    if not job:
        return None
    status = read_json(job_dir / 'status.json') or {}
    last = {}
    try:
        with open(job_dir / 'progress.jsonl', 'rb') as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            lines = fh.read().decode('utf-8', 'replace').strip().splitlines()
            if lines:
                last = json.loads(lines[-1])
    except Exception:
        pass
    state = status.get('state')
    if job.get('pid'):
        if state in TERMINAL:
            pass                                # the epitaph stands, even while the process is still winding down
        elif _alive(job['pid'], job.get('born')):
            state = 'running'
        else:
            state = 'failed'
            status = {'state': state, 'error': 'the job process is gone without a word (see its log)'}
    elif not state:
        state = 'queued' if (job_dir / 'claim.json').exists() else 'failed'     # claimed: a promoter is starting it now
        if state == 'failed':
            status = {'state': state, 'error': 'the job never started'}
    return {**job, 'id': job_dir.name, 'state': state, 'ended': status.get('ended'), 'result': status.get('result'),
            'error': status.get('error'), 'pct': last.get('pct', 0), 'step': last.get('step', ''),
            'msg': last.get('msg', ''), 'metrics': last.get('metrics')}


def list_all(root, project=None, limit=50):
    d = jobs_dir(root)
    if not d.is_dir():
        return []
    out = []
    for e in os.scandir(d):
        if not e.is_dir():
            continue
        j = read(e.path)
        if j and (project is None or j.get('project') == project):
            out.append(j)
    out.sort(key=lambda j: j.get('started') or j.get('queued_at') or 0, reverse=True)
    live = [j for j in out if j['state'] in ('running', 'queued')]
    over = [j for j in out if j['state'] not in ('running', 'queued')][:limit]
    return sorted(live + over, key=lambda j: j.get('started') or j.get('queued_at') or 0, reverse=True)


def running(root):
    return [j for j in list_all(root, limit=200) if j['state'] == 'running']


def queued(root, project=None):
    return [j for j in list_all(root, project=project, limit=200) if j['state'] == 'queued']


def prune(root, keep=KEEP):
    """Drop the oldest finished job folders beyond `keep`. Live and queued jobs are never touched."""
    import shutil
    d = jobs_dir(root)
    if not d.is_dir():
        return 0
    over = [j for j in list_all(root, limit=10 ** 6) if j['state'] not in ('running', 'queued')]
    gone = 0
    for j in over[keep:]:
        try:
            shutil.rmtree(d / j['id'])
            gone += 1
        except Exception as e:
            logger.debug(f"[WWM] prune {j['id']}: {e}")
    return gone


def _new_dir(root, kind):
    jid = time.strftime('%Y%m%d-%H%M%S') + f"-{kind}"
    job_dir = jobs_dir(root) / jid
    n = 1
    while job_dir.exists():
        n += 1
        job_dir = jobs_dir(root) / f"{jid}-{n}"
    job_dir.mkdir(parents=True)
    return job_dir


def _new_job(root, kind, args, python, plugin_dir, project):
    job_dir = _new_dir(Path(root), kind)
    job = {'kind': kind, 'args': args or {}, 'project': project, 'pid': None, 'born': None, 'started': None,
           'queued_at': time.time(), 'python': str(python), 'plugin_dir': str(plugin_dir)}
    write_json(job_dir / 'job.json', job)
    prune(root)
    return job_dir, job


def enqueue(root, kind, args, python, plugin_dir, project=None, publish=None):
    """A heavy job: written as queued first, then promoted, so it runs now when its wake word is free and waits its
    turn otherwise. The queue is the folder: a job.json with status queued and no pid."""
    with _qlock:
        job_dir, _ = _new_job(root, kind, args, python, plugin_dir, project)
        _write_status(job_dir, 'queued')
        promote(root, publish)
        return read(job_dir)


def retry(root, job_dir, publish=None):
    """The same job again (an interrupted or failed one): a fresh folder, same kind and args."""
    j = read(job_dir)
    if not j or j['state'] in ('running', 'queued'):
        return None
    if j['kind'] in HEAVY:
        return enqueue(root, j['kind'], j.get('args'), j['python'], j['plugin_dir'], project=j.get('project'), publish=publish)
    return start(root, j['kind'], j.get('args'), j['python'], j['plugin_dir'], project=j.get('project'), publish=publish)


def promote(root, publish=None):
    """Start the oldest queued job of every wake word that has nothing heavy running. A queued job is claimed by
    renaming its status file, which only one promoter can win."""
    root = Path(root)
    started = []
    with _qlock:
        live = running(root)
        for j in sorted(queued(root), key=lambda j: j.get('queued_at') or 0):
            proj = j.get('project')
            if any(x['kind'] in HEAVY and x.get('project') == proj for x in live + started):
                continue
            job_dir = jobs_dir(root) / j['id']
            try:
                os.replace(job_dir / 'status.json', job_dir / 'claim.json')
            except FileNotFoundError:
                continue                        # someone else claimed it
            try:
                _launch(job_dir, j, publish)
                started.append(read(job_dir))
            except Exception as e:
                logger.warning(f"[WWM] could not start queued job {j['id']}: {e}")
                _write_status(job_dir, 'failed', error=str(e))
            finally:
                try:
                    (job_dir / 'claim.json').unlink()
                except FileNotFoundError:
                    pass
    return started


def _launch(job_dir, job, publish, env=None):
    root = job_dir.parents[1]
    child_env = {**os.environ, **job_env(root), 'PYTHONUNBUFFERED': '1', 'PYTHONPATH': str(job['plugin_dir']), **(env or {})}
    log = open(job_dir / 'log.txt', 'ab')
    kwargs = {'start_new_session': True} if os.name == 'posix' else {'creationflags': 0x00000200}  # new process group
    try:
        proc = subprocess.Popen([str(job['python']), '-m', 'wakeword_maker.cli', 'job', str(job_dir)],
                                cwd=str(job['plugin_dir']), env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
    finally:
        log.close()
    _procs[proc.pid] = proc
    job = {k: v for k, v in job.items() if k in ('kind', 'args', 'project', 'queued_at', 'python', 'plugin_dir')}
    job.update({'pid': proc.pid, 'born': _born(proc.pid), 'started': time.time()})
    write_json(job_dir / 'job.json', job)
    attach(job_dir, publish)


def start(root, kind, args, python, plugin_dir, project=None, publish=None, env=None):
    """Spawn the job now. `python` is the interpreter to use (the plugin env's); `publish(event)` is called on every
    progress line (the daemon passes event_bus.publish)."""
    job_dir, job = _new_job(root, kind, args, python, plugin_dir, project)
    _launch(job_dir, job, publish, env=env)
    return read(job_dir)


def cancel(job_dir):
    with _qlock:
        j = read(job_dir)
        if j and j['state'] == 'queued':
            _write_status(job_dir, 'cancelled')
            return True
        if not j or j['state'] != 'running':
            return False
        _write_status(job_dir, 'cancelled')     # before the signal: the child reads it and keeps it
        pid = int(j['pid'])
        try:
            if os.name == 'posix':
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception as e:
            logger.warning(f"[WWM] cancel {Path(job_dir).name}: {e}")
        return True


def attach(job_dir, publish):
    """Follow a running job's progress file and publish each line; mark it failed if the process dies silently."""
    job_dir = Path(job_dir)
    jid = job_dir.name
    with _lock:
        if jid in _tails and _tails[jid].is_alive():
            return
        _tails.pop(jid, None)
        t = threading.Thread(target=_tail, args=(job_dir, publish), name=f"wwm-tail-{jid}", daemon=True)
        _tails[jid] = t
        t.start()


def attach_all(root, publish):
    _stopped.clear()
    for j in running(root):
        attach(jobs_dir(root) / j['id'], publish)


def detach_all():
    """On unload: this module's tails stop following and stop promoting. The next load re-attaches with its own."""
    _stopped.set()


def _tail(job_dir, publish):
    pos = 0
    pfile = job_dir / 'progress.jsonl'
    quiet = 0
    lingering = 0
    while not _stopped.is_set():
        try:
            if pfile.exists():
                with open(pfile, 'rb') as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                if chunk:
                    quiet = 0
                    for line in chunk.decode('utf-8', 'replace').splitlines():
                        try:
                            data = json.loads(line)
                        except Exception:
                            continue
                        if publish:
                            publish(EVENT, {'id': job_dir.name, **data})
            j = read(job_dir)
            pid = int(j['pid']) if j and j.get('pid') else 0
            alive = _alive(pid, j.get('born')) if j else False
            if alive and j['state'] in TERMINAL and pid in _procs:
                lingering = lingering or time.time()          # epitaph written, our child still here
                if time.time() - lingering > 30:
                    logger.warning(f"[WWM] {job_dir.name} wrote its status but did not exit; killing it")
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL) if os.name == 'posix' else os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
            if not alive:
                if j and publish:
                    publish(EVENT, {'id': job_dir.name, 'state': j['state'], 'pct': 100 if j['state'] == 'done' else j.get('pct', 0),
                                    'msg': j.get('error') or j.get('msg') or j['state'], 'final': True, 'result': j.get('result')})
                try:
                    promote(job_dir.parents[1], publish)
                except Exception as e:
                    logger.debug(f"[WWM] promote: {e}")
                return
        except Exception as e:
            logger.debug(f"[WWM] tail {job_dir.name}: {e}")
        time.sleep(0.5 if quiet < 60 else 2.0)
        quiet += 1
