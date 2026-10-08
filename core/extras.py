# core/extras.py - optional pieces of Sapphire, installed into her own
# environment when first needed (tmp/board-flash-web-plan.md)
#
# Some things Sapphire can do need packages most people never use. They live
# in install/requirements-*.txt as sets. This installs one of those sets
# into the interpreter Sapphire runs in, on the person's say-so, from the
# page that needs it: the Devices page's flasher asks for `flash` the moment
# someone picks "the computer Sapphire runs on". TTS, STT and the wake word
# are NOT here: they stay part of the main install.
#
# Only the sets named below can be installed. Nothing from a request reaches
# pip's arguments. One install at a time, on a thread, its words kept for
# the page and in user/logs/extras-<name>.log. Hosted installs refuse.
import importlib
import importlib.util
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / 'user' / 'logs'
PIP_WAIT = 1800                   # seconds: a set is small, but a slow mirror is not

EXTRAS = {
    'flash': {
        'label': 'Board flashing from this computer',
        'file': 'install/requirements-flash.txt',
        'modules': ('esptool', 'serial'),          # what proves it is there, without importing it
        'note': "esptool and six small packages (pyserial, bitstring, intelhex, reedsolo, esp-pylib, rich_click). "
                "Flashing from Chrome or Edge needs none of this.",
        'restart': False,                          # imported when first used: no restart
    },
}

_lock = threading.Lock()
_job = None                       # the install in flight, or the last one


class ExtraError(Exception):
    """A reason fit to show as it is."""


def _managed():
    try:
        from core.settings_manager import settings
        return bool(settings.is_managed())
    except Exception:
        return False


def installed(name):
    """Whether the set's packages can be found, looked up fresh: a package
    pip just put there is not in the import caches yet."""
    spec = EXTRAS.get(name)
    if not spec:
        return False
    importlib.invalidate_caches()
    try:
        return all(importlib.util.find_spec(m) is not None for m in spec['modules'])
    except (ImportError, ValueError):
        return False


def status():
    """Every set: {name: {label, note, installed, restart, job}}. `job` is
    the install in flight or the last one, when it was for that set."""
    with _lock:
        job = dict(_job) if _job else None
    out = {}
    for name, spec in EXTRAS.items():
        out[name] = {'label': spec['label'], 'note': spec['note'], 'restart': spec['restart'],
                     'installed': installed(name),
                     'job': job if job and job['name'] == name else None}
    return out


def state(name):
    with _lock:
        job = dict(_job) if _job and _job['name'] == name else None
    return job or {'name': name, 'state': 'idle', 'lines': [], 'error': '', 'installed': installed(name)}


def start(name):
    """Install one set in the background. Returns its state."""
    global _job
    spec = EXTRAS.get(name)
    if not spec:
        raise ExtraError(f"There is no optional set called '{name}'.")
    if _managed():
        raise ExtraError("This Sapphire is hosted: its environment is not yours to change.")
    with _lock:
        if _job and _job['state'] == 'running':
            raise ExtraError(f"'{_job['name']}' is being installed. Wait for it.")
        if installed(name):
            _job = {'name': name, 'state': 'done', 'lines': ['already installed'], 'error': '',
                    'started': time.time(), 'installed': True}
            return dict(_job)
        _job = {'name': name, 'state': 'running', 'lines': [], 'error': '', 'started': time.time(),
                'installed': False}
        job = _job
    threading.Thread(target=_install, args=(job, spec), name=f'extras-{name}', daemon=True).start()
    return dict(job)


def _install(job, spec):
    req = ROOT / spec['file']
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONUNBUFFERED='1')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"extras-{job['name']}.log"
    try:
        if not req.is_file():
            raise ExtraError(f"{spec['file']} is missing from this Sapphire.")
        cmd = [sys.executable, '-m', 'pip', 'install', '-r', str(req)]
        with open(log_path, 'w', encoding='utf-8') as log:
            log.write(' '.join(cmd) + '\n')
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                                    env=env, bufsize=1)
            deadline = time.time() + PIP_WAIT
            for line in proc.stdout:
                log.write(line)
                line = line.rstrip()
                if line:
                    with _lock:
                        job['lines'].append(line[:200])
                        del job['lines'][:-40]
                if time.time() > deadline:
                    proc.kill()
                    raise ExtraError("pip took too long and was stopped.")
            code = proc.wait(timeout=60)
        if code:
            raise ExtraError(f"pip failed (exit {code}). The log is user/logs/{log_path.name}.")
        if not installed(job['name']):
            raise ExtraError("pip finished, but the packages still cannot be found. The log may say why.")
        with _lock:
            job.update(state='done', installed=True)
        logger.info(f"[EXTRAS] installed '{job['name']}'")
    except Exception as e:
        with _lock:
            job.update(state='failed', error=str(e))
        logger.error(f"[EXTRAS] install of '{job['name']}' failed: {e}")
