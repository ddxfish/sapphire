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
# Only the sets named below can be installed, plus one set PER PLUGIN built
# from its own manifest (`plugin:<name>`, 2026-10-08): the plugin's hard
# `pip_dependencies` and/or its optional `extra` - {label, pip: [specs],
# modules?: [...], note}. A hard dependency keeps the plugin from loading until
# it is there (core/plugin_loader.py); an optional extra lets the plugin load
# and only the feature that needs it refuses (the Claude Code plugin's Agent
# SDK, 250 MB, which nothing else in Sapphire uses). Both install through
# HERE - the one pip engine - from the plugin card or the moment the plugin
# is enabled, on the person's say-so. Nothing from a request reaches pip's
# arguments: a request names a set, the set names its packages. One install
# at a time, on a thread, its words kept for the page and in
# user/logs/extras-<name>.log. Hosted installs refuse.
import importlib
import importlib.metadata
import importlib.util
import logging
import os
import re
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
PLUGIN_PREFIX = 'plugin:'
# A requirement as pip_dependencies has always allowed it: `name[extras]` with
# version operators, or a PEP 508 direct reference `name[extras] @ git+https://…`
# (the discord plugin's py-cord). Never an option (`-…`), a bare path or URL.
_NAME = r'[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?'
_SPEC_RE = re.compile(
    r'^' + _NAME + r'\s*(([<>=!~]=?\s*[A-Za-z0-9._*+-]+\s*(,\s*[<>=!~]=?\s*[A-Za-z0-9._*+-]+\s*)*)'
    r'|(@\s*(git\+https|git\+ssh|https|http)://\S+))?$')


class ExtraError(Exception):
    """A reason fit to show as it is."""


def _dist_name(spec):
    return re.split(r'[<>=!~\[; ]', str(spec), 1)[0].strip()


def _plugin_spec(plugin_name):
    """The set a plugin declares, from its manifest, or None. Specs are plain
    requirement specifiers only - never a pip option, a URL or a path - so a
    manifest cannot smuggle arguments to pip."""
    try:
        from core.plugin_loader import plugin_loader
        info = plugin_loader.get_plugin_info(plugin_name)
    except Exception:
        info = None
    if not info:
        return None
    manifest = info.get('manifest') or {}
    extra = manifest.get('extra') if isinstance(manifest.get('extra'), dict) else {}
    hard = [str(x) for x in (manifest.get('pip_dependencies') or [])]
    soft = [str(x) for x in (extra.get('pip') or [])]
    specs = [x.strip() for x in hard + soft if x and str(x).strip()]
    bad = [x for x in specs if not _SPEC_RE.match(x)]
    if bad:
        logger.debug(f"[EXTRAS] {plugin_name}: ignoring pip specs that are not requirements: {bad}")
        specs = [x for x in specs if x not in bad]
    if not specs:
        return None
    title = manifest.get('title') or manifest.get('display_name') or plugin_name
    return {
        'label': str(extra.get('label') or f"Packages for {title}"),
        'specs': specs,
        'modules': tuple(str(m) for m in (extra.get('modules') or [])),
        'note': str(extra.get('note') or ('Installs: ' + ', '.join(specs))),
        'restart': bool(extra.get('restart', False)),
        'hard': bool(hard),
        'plugin': plugin_name,
    }


def _spec(name):
    if name in EXTRAS:
        return EXTRAS[name]
    if isinstance(name, str) and name.startswith(PLUGIN_PREFIX):
        return _plugin_spec(name[len(PLUGIN_PREFIX):])
    return None


def known(name):
    return _spec(name) is not None


def plugin_extra(plugin_name):
    """What the plugin card shows: {name, label, note, installed, hard} or None."""
    spec = _plugin_spec(plugin_name)
    if not spec:
        return None
    name = PLUGIN_PREFIX + plugin_name
    return {'name': name, 'label': spec['label'], 'note': spec['note'],
            'installed': _installed(spec), 'hard': spec['hard'], 'restart': spec['restart']}


def _managed():
    try:
        from core.settings_manager import settings
        return bool(settings.is_managed())
    except Exception:
        return False


def installed(name):
    """Whether the set's packages can be found, looked up fresh: a package
    pip just put there is not in the import caches yet."""
    spec = _spec(name)
    return _installed(spec) if spec else False


def _installed(spec):
    importlib.invalidate_caches()
    try:
        if spec.get('modules'):
            return all(importlib.util.find_spec(m) is not None for m in spec['modules'])
        # a plugin set with no module names: the distributions themselves
        for x in spec.get('specs') or ():
            importlib.metadata.version(_dist_name(x))
        return bool(spec.get('specs'))
    except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
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


def describe(name):
    """One set as the page sees it, or None (a plugin's set included)."""
    spec = _spec(name)
    if not spec:
        return None
    return {'label': spec['label'], 'note': spec['note'], 'restart': spec.get('restart', False),
            'installed': _installed(spec)}


def start(name):
    """Install one set in the background. Returns its state."""
    global _job
    spec = _spec(name)
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
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONUNBUFFERED='1')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"extras-{job['name'].replace(':', '-')}.log"
    try:
        if spec.get('specs'):
            cmd = [sys.executable, '-m', 'pip', 'install', *spec['specs']]
        else:
            req = ROOT / spec['file']
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
        if not _installed(spec):
            raise ExtraError("pip finished, but the packages still cannot be found. The log may say why.")
        with _lock:
            job.update(state='done', installed=True)
        logger.info(f"[EXTRAS] installed '{job['name']}'")
        if spec.get('plugin'):
            # a plugin whose hard dependencies were missing sat enabled-but-
            # unloaded; it loads now (the optional case reloads harmlessly)
            try:
                from core.plugin_loader import plugin_loader
                plugin_loader.reload_plugin(spec['plugin'])
            except Exception as e:
                logger.warning(f"[EXTRAS] {spec['plugin']}: reload after install failed: {e}")
    except Exception as e:
        with _lock:
            job.update(state='failed', error=str(e))
        logger.error(f"[EXTRAS] install of '{job['name']}' failed: {e}")
