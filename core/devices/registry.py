# core/devices/registry.py - Device driver registry (tmp/device-manager-plan.md)
#
# Plugins declare device drivers in their manifest (capabilities.devices); the
# loader registers them here. The engine (core/devices/engine.py) consumes
# this registry. Drivers and their hardware code stay in plugins. The app
# runs fine with nothing registered; this module is a dumb, dependency-free
# holder, cloned from core/games_registry.py.
#
# A driver spec:
#   driver         slug, [a-z0-9][a-z0-9_-]{0,32} - unique across plugins
#   label          display name in the Add Device picker
#   icon           emoji
#   module         plugin-relative path to the driver module (a .py file)
#   capabilities   list of slugs - what a device of this kind can do
#   config_schema  list of field dicts (plugin-settings field shape). A field
#                  with "secret": true is stored by secret_store.py, never
#                  in the device row.
#   presence       true = its things come and go, and the driver has tend()
#                  (core/devices/presence.py keeps it told)

import json
import logging
import re
import threading
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

MAX_FIELDS = 40
MAX_CAPABILITIES = 16
CORE = 'core'                     # the owner name of a driver that ships inside core
CORE_DRIVERS = ('satellite', 'computer')     # their ids (core/devices/drivers/<id>.py): no plugin may claim one
_ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')

_lock = threading.Lock()
_drivers = {}       # driver id -> spec dict + plugin_name
_generation = 0     # bumped on every change - consumers cache-invalidate on it


def _clean_module(path):
    """A plugin-relative .py path, or '' when it could leave the plugin dir."""
    path = str(path or '').strip().replace('\\', '/')
    p = PurePosixPath(path)
    if not path.endswith('.py') or p.is_absolute() or '..' in p.parts:
        return ''
    return str(p)


def _clean_schema(schema):
    """Field dicts with a string key, JSON-safe, capped. None = reject."""
    if schema is None:
        return []
    if not isinstance(schema, list) or len(schema) > MAX_FIELDS:
        return None
    fields = json.loads(json.dumps(schema))   # raises on anything not JSON-safe
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get('key'), str) or not f['key']:
            return None
    return fields


def register_driver(driver_id, spec, plugin_name, builtin=False):
    """Register one device driver. Returns True on accept. Never raises - a
    bad declaration must not break plugin load.

    builtin=True is the engine registering a driver that ships inside core.
    A plugin can neither call itself 'core' nor take a core driver's id."""
    global _generation
    try:
        driver_id = str(driver_id or '').strip().lower()
        if not _ID_RE.fullmatch(driver_id):
            logger.warning(f"[DEVICES] '{plugin_name}': driver id '{driver_id}' invalid - skipped")
            return False
        if builtin:
            plugin_name = CORE
            spec = dict(spec, module=driver_id + '.py')
        elif plugin_name == CORE or driver_id in CORE_DRIVERS:
            logger.warning(f"[DEVICES] '{plugin_name}': driver id '{driver_id}' belongs to core - refused")
            return False
        module = _clean_module(spec.get('module'))
        if not module:
            logger.warning(f"[DEVICES] '{plugin_name}': driver '{driver_id}' needs a "
                           f"plugin-relative .py module - skipped")
            return False
        caps = [str(c).strip().lower() for c in (spec.get('capabilities') or [])]
        if not caps or len(caps) > MAX_CAPABILITIES or any(not _ID_RE.fullmatch(c) for c in caps):
            logger.warning(f"[DEVICES] '{plugin_name}': driver '{driver_id}' has invalid "
                           f"capabilities {caps} - skipped")
            return False
        schema = _clean_schema(spec.get('config_schema'))
        if schema is None:
            logger.warning(f"[DEVICES] '{plugin_name}': driver '{driver_id}' has an invalid "
                           f"config_schema - skipped")
            return False
        locked = spec.get('locked_by_default') or []
        locked = [c for c in caps if c in {str(x).strip().lower() for x in locked}] \
            if isinstance(locked, (list, tuple)) else []
        with _lock:
            owner = _drivers.get(driver_id, {}).get('plugin_name')
            if owner and owner != plugin_name:
                logger.warning(f"[DEVICES] driver '{driver_id}' already registered by "
                               f"'{owner}' - '{plugin_name}' refused")
                return False
            _drivers[driver_id] = {
                'driver': driver_id,
                'label': str(spec.get('label') or driver_id.replace('-', ' ').title())[:80],
                'icon': str(spec.get('icon') or '')[:16],
                'module': module,
                'capabilities': caps,
                'config_schema': schema,
                'locked_by_default': locked,
                'presence': spec.get('presence') is True,
                # tools of OTHER plugins this driver may run. Core's own
                # drivers only: a plugin's driver runs its own tools.
                'uses_tools': [str(t) for t in (spec.get('uses_tools') or [])][:8] if builtin else [],
                'plugin_name': plugin_name,
            }
            _generation += 1
        logger.info(f"[DEVICES] Driver registered: '{driver_id}' from '{plugin_name}'")
        return True
    except Exception as e:
        logger.warning(f"[DEVICES] register_driver('{driver_id}') failed: {e}")
        return False


def unregister_plugin(plugin_name):
    """Drop all drivers a plugin registered (unload/disable path). Device rows
    survive in the engine - they go dark until the plugin comes back."""
    global _generation
    with _lock:
        gone = [d for d, v in _drivers.items() if v.get('plugin_name') == plugin_name]
        for d in gone:
            _drivers.pop(d, None)
        if gone:
            _generation += 1
    if gone:
        logger.info(f"[DEVICES] Unregistered {len(gone)} driver(s) from '{plugin_name}': {gone}")
    return gone


def list_drivers():
    """Snapshot of all registered drivers, stable order (label, id)."""
    with _lock:
        drivers = [json.loads(json.dumps(v)) for v in _drivers.values()]
    drivers.sort(key=lambda d: (d.get('label', ''), d.get('driver', '')))
    return drivers


def has(driver_id):
    with _lock:
        return str(driver_id or '').strip().lower() in _drivers


def get_driver(driver_id):
    """One driver's spec (a copy), or None."""
    with _lock:
        spec = _drivers.get(str(driver_id or '').strip().lower())
        return json.loads(json.dumps(spec)) if spec else None


def generation():
    with _lock:
        return _generation
