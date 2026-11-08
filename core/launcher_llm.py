"""The Sapphire Launcher's LLM: find it, add it as a provider once.

The launcher (its own repo) runs llama-server and writes the contract file
`llm-run.json` ({base_url, alias, pid}) in the per-user config folder. A boot
finding it, or finding a llama.cpp server on the launcher's port, adds the
custom provider `sapphire_launcher` at the TOP of the Auto order, so a fresh
install talks with no LLM setup at all.

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


def _from_file():
    """The file's base_url, trusted only while its server answers: the file
    exists only while the launcher's LLM runs, but a crash or a reboot can
    leave it behind."""
    f = _run_file()
    try:
        url = json.loads(f.read_text(encoding='utf-8')).get('base_url')
    except (OSError, ValueError, AttributeError):
        return None
    if not (isinstance(url, str) and re.match(r'https?://', url.strip())):
        logger.warning(f"[LAUNCHER] {f} has no usable base_url; ignoring it")
        return None
    url = _v1(url.strip())
    if not _alive(url):
        logger.info(f"[LAUNCHER] {f} names {url}, which is not answering (left behind?); ignoring it")
        return None
    return url


def _v1(url):
    url = url.rstrip('/')
    return url if url.endswith('/v1') else url + '/v1'


# Loopback probes never ride a proxy (SOCKS_ROUTE_LLM / *_PROXY env would
# send 127.0.0.1 to the proxy box and the launcher would never be seen).
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, timeout=1.0):
    try:
        with _opener.open(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8')) if r.status == 200 else None
    except Exception:
        return None


def _alive(base_url, timeout=1.0):
    """Anything answering {root}/health: llama-server says 503 while it
    loads the model, and that is still the launcher's server."""
    try:
        with _opener.open(re.sub(r'/v1$', '', base_url) + '/health', timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _models(base_url):
    """The llama.cpp server's model ids, or None when nothing llama.cpp answers."""
    data = _get(f"{base_url}/models")
    rows = data.get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    if not any(isinstance(m, dict) and m.get('owned_by') == 'llamacpp' for m in rows):
        return None
    return [str(m.get('id')) for m in rows if isinstance(m, dict) and m.get('id')]


def find():
    """The launcher's base URL: the contract file first, else a llama.cpp
    server answering on the launcher's port. None = no launcher LLM here."""
    url = _from_file()
    if url:
        return url
    return DEFAULT_URL if _models(DEFAULT_URL) is not None else None


def same_url(a, b):
    """One server, however it was typed: case, trailing slash, /v1, and the
    loopback spellings."""
    def norm(u):
        u = re.sub(r'/v1/?$', '', (u or '').strip().lower().rstrip('/'))
        return re.sub(r'//(localhost|0\.0\.0\.0)(?=[:/]|$)', '//127.0.0.1', u)
    return bool(a and b) and norm(a) == norm(b)


def adopt():
    """Add the launcher's provider if it is here and never was added. Returns
    the key when it was added now, else None. Safe to call again (boot, the
    wizard's check)."""
    from core.settings_manager import settings
    if settings.is_managed() or settings.is_docker():
        return None
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
        }
        order = [k for k in (settings.get('LLM_FALLBACK_ORDER', []) or []) if k != KEY]
        settings.set('LLM_CUSTOM_PROVIDERS', custom, persist=True)
        settings.set('LLM_FALLBACK_ORDER', [KEY] + order, persist=True)
        settings.set(MARKER, True, persist=True)
    logger.info(f"[LAUNCHER] added '{NAME}' ({url}) at the top of the Auto order")
    try:
        from core.event_bus import publish, Events
        publish(Events.SETTINGS_CHANGED, {"key": "LLM_CUSTOM_PROVIDERS", "value": KEY})
    except Exception as e:
        logger.debug(f"[LAUNCHER] settings event not published: {e}")
    return KEY


def status():
    """What the wizard's banner says: is the launcher's provider set up, and
    is its server answering (model id, context window)."""
    from core.settings_manager import settings
    conf = (settings.get('LLM_CUSTOM_PROVIDERS', {}) or {}).get(KEY)
    if not isinstance(conf, dict) or not conf.get('enabled'):
        return {'provider': None, 'running': False}
    url = conf.get('base_url') or DEFAULT_URL
    models = _models(url)
    props = _get(re.sub(r'/v1/?$', '', url.rstrip('/')) + '/props') if models else None
    n_ctx = None
    if isinstance(props, dict):
        n_ctx = (props.get('default_generation_settings') or {}).get('n_ctx') or props.get('n_ctx')
    return {
        'provider': KEY,
        'name': conf.get('display_name') or NAME,
        'running': models is not None,
        'model': (models or [None])[0],
        'n_ctx': n_ctx if isinstance(n_ctx, int) else None,
        'first_in_auto': (settings.get('LLM_FALLBACK_ORDER', []) or [None])[0] == KEY,
    }


def adopt_in_background():
    """Boot: probing the network must never hold up the server starting."""
    def run():
        try:
            adopt()
        except Exception as e:
            logger.warning(f"[LAUNCHER] adopt failed: {e}")
    threading.Thread(target=run, name='launcher-llm', daemon=True).start()
