"""The Sapphire Launcher's LLM: find it, add it as a provider once.

The launcher (its own repo) runs llama-server and writes the contract file
`llm-run.json` ({base_url, alias, pid}) in the per-user config folder. A boot
finding it, or finding a llama.cpp server on the launcher's port, adds the
custom provider `sapphire_launcher` to the Auto order (first on a fresh
install, so it talks with no LLM setup at all; last on one that finished
setup, so nobody's brain changes under them).

Once only: LAUNCHER_LLM_ADOPTED is set when it is added, so a provider the
user deletes stays deleted. Never added when any provider already points at
that URL, nor when the key is taken, nor in managed or Docker installs (the
launcher's 127.0.0.1 is not the container's).
"""
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

KEY = 'sapphire_launcher'
NAME = 'Sapphire Launcher'
DEFAULT_URL = 'http://127.0.0.1:8642/v1'
MARKER = 'LAUNCHER_LLM_ADOPTED'

_lock = threading.Lock()


def _run_file(windows=os.name == 'nt'):
    """Where the launcher writes llm-run.json (its src/platform.rs): Windows
    %APPDATA%\\Sapphire (else %USERPROFILE%\\AppData\\Roaming\\Sapphire);
    elsewhere $XDG_CONFIG_HOME/sapphire (else ~/.config/sapphire), where the
    lowercase name matters."""
    if windows:
        base = os.environ.get('APPDATA') or str(Path(os.environ.get('USERPROFILE') or Path.home()) / 'AppData' / 'Roaming')
        return Path(base) / 'Sapphire' / 'llm-run.json'
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'sapphire' / 'llm-run.json'


_live = {'key': None, 'url': None}


def live_url(fallback):
    """Where the launcher's LLM is NOW: its port can be changed, so the
    provider follows the contract file whenever it is there (loopback only),
    else keeps its stored URL. No network: a stat per call, a read only when
    the file changed. core/chat/llm_providers reads it for KEY at build time."""
    f = _run_file()
    try:
        st = f.stat()
    except OSError:
        return fallback
    key = (str(f), st.st_mtime_ns, st.st_size)
    if _live['key'] != key:
        try:
            url = json.loads(f.read_text(encoding='utf-8')).get('base_url')
        except (OSError, ValueError, AttributeError):
            url = None
        _live.update(key=key, url=_v1(url.strip()) if isinstance(url, str) and _loopback(url.strip()) else None)
    return _live['url'] or fallback


def _from_file():
    """The file's base_url, trusted only when it is loopback (the contract:
    always http://127.0.0.1:<port>/v1; the provider is marked local, so a
    remote URL here would open private chats to another box) and a llama.cpp
    server answers there: the file exists only while the launcher's LLM
    runs, but a crash or a reboot can leave it behind, and another service
    may own that port by then."""
    f = _run_file()
    try:
        url = json.loads(f.read_text(encoding='utf-8')).get('base_url')
    except (OSError, ValueError, AttributeError):
        return None
    if not (isinstance(url, str) and _loopback(url.strip())):
        logger.warning(f"[LAUNCHER] {f} has no usable loopback base_url; ignoring it")
        return None
    url = _v1(url.strip())
    if not probe(url):
        logger.info(f"[LAUNCHER] {f} names {url}, where no llama.cpp server answers (left behind?); ignoring it")
        return None
    return url


def _v1(url):
    url = url.rstrip('/')
    return url if url.endswith('/v1') else url + '/v1'


def _loopback(url):
    return bool(re.match(r'https?://(127(\.\d{1,3}){0,3}|localhost|\[::1\])(:\d+)?(/|$)', url.lower()))


# Loopback probes never ride a proxy (SOCKS_ROUTE_LLM / *_PROXY env would
# send 127.0.0.1 to the proxy box and the launcher would never be seen).
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, timeout=1.0):
    try:
        with _opener.open(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8')) if r.status == 200 else None
    except Exception:
        return None


def _models(base_url):
    """The llama.cpp server's model ids, or None when nothing llama.cpp answers."""
    data = _get(f"{base_url}/models")
    rows = data.get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    if not any(isinstance(m, dict) and m.get('owned_by') == 'llamacpp' for m in rows):
        return None
    return [str(m.get('id')) for m in rows if isinstance(m, dict) and m.get('id')]


def _loading(base_url, timeout=1.0):
    """llama-server while it loads the model: /health answers 503 'Loading
    model'. Anything else on that port is not proof of llama.cpp."""
    try:
        with _opener.open(re.sub(r'/v1$', '', base_url) + '/health', timeout=timeout):
            return False
    except urllib.error.HTTPError as e:
        try:
            return e.code == 503 and 'loading model' in e.read().decode('utf-8', 'replace').lower()
        except Exception:
            return False
    except Exception:
        return False


def probe(base_url):
    """'running' (llama.cpp serving a model), 'loading', or None."""
    if _models(base_url) is not None:
        return 'running'
    return 'loading' if _loading(base_url) else None


def find():
    """The launcher's base URL: the contract file first, else a llama.cpp
    server answering on the launcher's port. None = no launcher LLM here."""
    return _from_file() or (DEFAULT_URL if probe(DEFAULT_URL) else None)


def same_url(a, b):
    """One server, however it was typed: case, trailing slash, /v1, and the
    loopback spellings (localhost, 0.0.0.0, [::1], 127.1, any 127.x)."""
    def norm(u):
        u = re.sub(r'/v1/?$', '', (u or '').strip().lower().rstrip('/'))
        return re.sub(r'//(localhost|0\.0\.0\.0|\[::1\]|127(\.\d{1,3}){0,3})(?=[:/]|$)', '//127.0.0.1', u)
    return bool(a and b) and norm(a) == norm(b)


def adopt():
    """Add the launcher's provider if it is here and never was added. Returns
    the key when it was added now, else None. Safe to call again (boot, the
    wizard's check). A fresh install gets it FIRST in Auto; an install that
    finished setup already has a brain, so it goes LAST (Krem 2026-11-08):
    there, but no chat changes brain under anyone."""
    from core.settings_manager import settings
    if settings.is_managed() or settings.is_docker():
        return None
    if getattr(settings, 'corrupt_error', None):
        return None           # every save is refused; adding would only live until restart
    if settings.get(MARKER, False):
        return None
    url = find()
    if not url:
        return None
    from core.chat.llm_providers import PROVIDER_MAP_LOCK
    with _lock, PROVIDER_MAP_LOCK:
        if settings.get(MARKER, False):
            return None
        custom = dict(settings.get('LLM_CUSTOM_PROVIDERS', {}) or {})
        core = settings.get('LLM_PROVIDERS', {}) or {}
        clash = next((k for k, c in {**core, **custom}.items()
                      if isinstance(c, dict) and same_url(c.get('base_url'), url)), None)
        if clash:
            logger.info(f"[LAUNCHER] LLM at {url} is already set up as '{clash}'; not adding another")
            return None
        if KEY in custom:
            logger.warning(f"[LAUNCHER] a provider named '{KEY}' already exists at another URL; not adding")
            return None
        custom[KEY] = {
            'template': 'openai',
            'display_name': NAME,
            'base_url': url,
            'model': '',                  # the server names its one model (ensure_model)
            'enabled': True,
            'use_as_fallback': True,
            'is_local': True,
            'auto_discover_models': True,
            'timeout': 0.3,
            # The launcher's small models loop in thinking under a persona
            # prompt: Qwen3.5 4B hit max_tokens with no reply in 38 of 50
            # runs (2026-11-08); thinking off answers at once. One tick on
            # the card turns it back on (Krem: this provider only).
            'disable_thinking': True,
        }
        order = [k for k in (settings.get('LLM_FALLBACK_ORDER', []) or []) if k != KEY]
        first = not settings.get('SETUP_COMPLETE', False)
        # one save: a crash between separate saves left the provider without
        # its place in Auto, or without the marker
        settings.set_many({
            'LLM_CUSTOM_PROVIDERS': custom,
            'LLM_FALLBACK_ORDER': [KEY] + order if first else order + [KEY],
            MARKER: True,
        }, persist=True)
    logger.info(f"[LAUNCHER] added '{NAME}' ({url}) {'at the top' if first else 'at the end'} of the Auto order")
    try:        # the "no LLM yet" row a finished setup left (core/routes/setup.py)
        from core import notify
        notify.forget('setup-no-llm')
    except Exception as e:
        logger.debug(f"[LAUNCHER] setup row not cleared: {e}")
    try:
        from core.event_bus import publish, Events
        publish(Events.SETTINGS_CHANGED, {"key": "LLM_CUSTOM_PROVIDERS", "value": KEY})
    except Exception as e:
        logger.debug(f"[LAUNCHER] settings event not published: {e}")
    return KEY


def _n_ctx(props):
    if not isinstance(props, dict):
        return None
    dgs = props.get('default_generation_settings')
    n = (dgs.get('n_ctx') if isinstance(dgs, dict) else None) or props.get('n_ctx')
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def status():
    """What the wizard's banner says: is the launcher's provider set up, and
    is its server running, loading or stopped (model id, context window)."""
    from core.settings_manager import settings
    conf = (settings.get('LLM_CUSTOM_PROVIDERS', {}) or {}).get(KEY)
    if not isinstance(conf, dict) or not conf.get('enabled'):
        return {'provider': None, 'state': None}
    url = live_url(conf.get('base_url') or DEFAULT_URL)
    models = _models(url)
    state = 'running' if models is not None else ('loading' if _loading(url) else 'stopped')
    root = re.sub(r'/v1/?$', '', url.rstrip('/'))
    return {
        'provider': KEY,
        'name': str(conf.get('display_name') or NAME),
        'state': state,
        'model': (models or [None])[0],
        'n_ctx': _n_ctx(_get(root + '/props')) if models else None,
        'first_in_auto': (settings.get('LLM_FALLBACK_ORDER', []) or [None])[0] == KEY,
    }


# ── asking the launcher itself (2026-11-15) ────────────────────────────────
#
# The port only says running, loading or silent. The launcher knows the rest:
# nothing installed, a model downloading (and how far), installed but stopped.
# `<launcher> --llm-status` prints that as one line of JSON, with no window and
# no launcher running. Core may call it only when llm.json carries the
# `launcher` key: a launcher from before the switch existed would open its
# window on it, so the key is the proof, never a probe.

CLI_TIMEOUT = 2.0       # seconds; 10-20 ms on Linux, unmeasured on real Windows
CLI_TTL = 3.0           # one ask serves this long (the Easy page polls every 4 s)
_cli = {'at': 0.0, 'reply': None}


def _launcher_exe():
    """The launcher's own program from llm.json, when it is one core may run:
    an absolute path to a file that is there. None otherwise."""
    import time
    f = _run_file().with_name('llm.json')
    data = None
    for _ in range(3):              # the launcher writes by temp + rename: a read can land in between
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            break
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.05)
    exe = data.get('launcher') if isinstance(data, dict) else None
    if not isinstance(exe, str) or not exe.strip():
        return None
    p = Path(exe.strip())
    return str(p) if p.is_absolute() and p.is_file() else None


def cli_status():
    """The launcher's --llm-status reply (a dict with ok true: phase, job,
    running, last_job), or None when it cannot be asked: no launcher that
    knows the switch, managed or Docker, it failed, or it took too long.
    Never raises. Asked at most once per CLI_TTL."""
    import subprocess
    import time
    from core.settings_manager import settings
    if settings.is_managed() or settings.is_docker():
        return None
    now = time.monotonic()
    with _lock:
        if _cli['at'] and now - _cli['at'] < CLI_TTL:
            return _cli['reply']
    reply = None
    exe = _launcher_exe()
    if exe:
        try:
            r = subprocess.run([exe, '--llm-status'], capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=CLI_TIMEOUT, stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            lines = [ln for ln in (r.stdout or '').splitlines() if ln.strip()]
            data = json.loads(lines[-1]) if lines else None
            if isinstance(data, dict) and data.get('ok') is True and isinstance(data.get('phase'), str):
                reply = data
            else:
                logger.debug(f"[LAUNCHER] --llm-status said no (exit {r.returncode}): {(r.stdout or '')[:200]}")
        except Exception as e:      # not there any more, not runnable, too slow, not JSON
            logger.debug(f"[LAUNCHER] --llm-status could not be asked: {e}")
    with _lock:
        _cli.update(at=time.monotonic(), reply=reply)
    return reply


def adopt_in_background():
    """Boot: probing the network must never hold up the server starting."""
    def run():
        try:
            adopt()
        except Exception as e:
            logger.warning(f"[LAUNCHER] adopt failed: {e}")
    threading.Thread(target=run, name='launcher-llm', daemon=True).start()
