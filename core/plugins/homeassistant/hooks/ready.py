# core/plugins/homeassistant/hooks/ready.py - the proxy bypass, and the one-time carry from 1.x
#
# plugins_ready fires after the boot scan and after any reload or toggle-on.
# Both jobs are safe to repeat.
import fnmatch
import json
import logging
import threading
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

NAME = 'homeassistant'
OLD_URL = 'http://homeassistant.local:8123'       # what 1.x used when no address was saved
_busy = threading.Lock()


def _hosts():
    """The host of every Home Assistant device: these never ride the proxy."""
    from core.devices import engine
    urls = [str((p.get('config') or {}).get('url') or '') for row in engine.rows().values()
            for p in row.get('parts', []) if p.get('driver') == NAME]
    return {h for h in (urlsplit(u).hostname for u in urls) if h}


def plugins_ready(event):
    try:
        from core.socks_proxy import register_direct_hosts
        register_direct_hosts(_hosts, owner=NAME)
    except Exception as e:
        logger.warning(f"[HA] proxy bypass not registered: {e}")
    threading.Thread(target=carry, name='ha-carry', daemon=True).start()     # it asks Home Assistant: never on the boot path


def _old():
    """What 1.x was set up with: {'url', 'blacklist', 'notify_service'}."""
    from core.paths import PROJECT_ROOT
    try:
        with open(PROJECT_ROOT / 'user' / 'webui' / 'plugins' / 'homeassistant.json', encoding='utf-8') as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    return saved if isinstance(saved, dict) else {}


def _token():
    from core.credentials_manager import credentials
    return credentials.get_ha_token()


def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(NAME)


def _blocked(thing, patterns):
    """The 1.x blacklist: an entity id pattern, or area:<room>."""
    for p in patterns if isinstance(patterns, list) else ():
        p = str(p).strip()
        if p.lower().startswith('area:'):
            if thing['room'].lower() == p[5:].strip().lower():
                return True
        elif fnmatch.fnmatch(thing['id'], p):
            return True
    return False


def carry():
    """Once: the Home Assistant that 1.x was set up with becomes a device
    named home. What the old blacklist blocked is unticked on it, and the
    phone it notified is ticked. Nothing old is deleted. When Home Assistant
    does not answer, nothing is kept and the next start tries again: the
    blacklist cannot be carried without the list of things it blocked."""
    if not _busy.acquire(blocking=False):
        return
    try:
        from core.devices import engine
        state = _state()
        if state.get('carried') or engine.refusal():
            return
        table = engine.rows()
        if any(p.get('driver') == NAME for row in table.values() for p in row.get('parts', [])):
            state.save('carried', True)
            return
        old, token = _old(), _token()
        if not token:
            return                      # never set up: nothing to carry
        name = next(n for n in ('home', 'home-assistant', 'home-2', 'home-3') if n not in table)
        engine.add(name, 'Home', NAME, {'url': old.get('url') or OLD_URL, 'token': token})
        try:
            if not engine.status(name, fresh=True)['online']:
                raise engine.DeviceError('Home Assistant did not answer')
            phone = 'notify.' + str(old.get('notify_service') or '').strip().removeprefix('notify.')
            ticks = {}
            for t in engine.things(engine.get(name)):
                if _blocked(t, old.get('blacklist')):
                    ticks[t['id']] = False
                elif t['id'] == phone:
                    ticks[t['id']] = True
            engine.update(name, allow=ticks)
        except Exception:
            engine.remove(name)
            raise
        state.save('carried', True)
        logger.info(f"[HA] the Home Assistant from before is now the device '{name}' (Settings > Devices)")
    except Exception as e:
        logger.warning(f"[HA] the Home Assistant from before was not carried over ({e}). Tried again at the next start.")
    finally:
        _busy.release()
