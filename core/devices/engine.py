# core/devices/engine.py - the device engine (tmp/device-manager-plan.md)
#
# ONE module holds the rows, loads the drivers, and answers every question.
# The three tools (functions/devices.py) and the routes (core/routes/devices.py)
# are thin doors into it: the Test button and device_status are one function,
# the Try button and device_action are one function.
#
# Core since 2026-09-27 (the ruling: satellites make voice a device matter,
# and devices are never meant to be switched off). Drivers are NOT core: a
# plugin that knows a transport declares one in its manifest
# (capabilities.devices) and registry.py holds the list.
#
# A device row (user/plugin_state/devices.json, key "devices"):
#   {"id", "label", "location", "enabled", "created", "locked": [...],
#    "parts": [{"driver", "plugin", "config": {...}}]}
# "locked" names the capabilities SHE may not use on this device. The user's
# own buttons on the Devices page always work. Only the capabilities in
# LOCKABLE carry that switch. A row without the key uses its drivers' defaults.
# "location" is the room or place, in the user's words. She reads it in
# device_list and at the top of anything a device hears.
# A part's config may hold one FILTER (a field of type "found"): which of the
# things its driver can see count as this device. {"all": bool, "only":
# [{"id", "name"}]}. The engine applies it (passing()). Things that come and
# go are kept by presence.py.
# A part may hold "has": the capabilities the DEVICE ITSELF said it has (its
# driver's status() answered 'has'). From then on the device shows only
# those, also while it is offline. A part without the key has everything its
# driver can do: a device that never said.
# The file kept its place when the engine moved into core, so no device list
# ever had to be migrated. Secrets never sit in a row. They live in
# secret_store.py under "<driver>.<field>".

import importlib
import json
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

STORE = 'devices'          # the state file's name: user/plugin_state/devices.json
ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')
MAX_DEVICES = 64
MAX_ROWS = 50            # items in one rows field
MAX_PICTURES = 4         # pictures one action may answer with
MAX_PICTURE = 12 * 1024 * 1024       # characters of base64 in one picture
PICTURE_TYPES = ('image/jpeg', 'image/png', 'image/webp')
LOCKABLE = ('power', 'camera', 'screen', 'mic')    # these carry a "Sapphire may use this" switch
MAX_FOUND = 64           # things one driver may report as found
RESERVED = ('found',)    # names a device may not have: the routes use them
CLEAR = '__CLEAR__'
PAGE = 'Settings > Devices'

_lock = threading.RLock()
_modules = {}            # driver id -> loaded driver module
_modules_gen = None
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='device-status')   # health.py probes on it


class DeviceError(Exception):
    """A message fit to show her or the user as it is."""


# --- doors to core (patched in tests) ----------------------------------------

def _store():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(STORE)


def _secrets():
    from core.devices import secret_store
    return secret_store


def _health():
    from core.devices import health
    return health


def _registry():
    """The registry, with the drivers that ship inside core always present.
    They are registered here, on first use, because nothing at boot may
    import a driver."""
    from core.devices import registry
    for name in registry.CORE_DRIVERS:
        if not registry.has(name):
            try:
                mod = importlib.import_module(f"core.devices.drivers.{name}")
                registry.register_driver(name, mod.SPEC, registry.CORE, builtin=True)
            except Exception as e:
                logger.error(f"[DEVICES] core driver '{name}' failed to load: {e}", exc_info=True)
    return registry


def _managed():
    from core.settings_manager import settings
    return bool(settings.is_managed())


def refusal():
    """Why devices cannot be used on this install, or ''. A hosted Sapphire
    has no home network: its devices would be the hosting company's."""
    try:
        if _managed():
            return "Devices are not available on hosted Sapphire."
    except Exception as e:
        logger.warning(f"[DEVICES] could not tell whether this install is hosted: {e}")
    return ''


def _plugin_info(name):
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_info(name)


def _all_plugin_info():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_all_plugin_info()


def _function_manager():
    from core.api_fastapi import get_system
    return get_system().llm_chat.function_manager


def _changed():
    """A device was added, changed or removed: presence looks again."""
    try:
        from core.devices import presence
        presence.poke()
    except Exception as e:
        logger.warning(f"[DEVICES] presence could not be told of a change: {e}")


# --- rows --------------------------------------------------------------------

def _slug(name):
    return str(name or '').strip().lower()


def _place(text):
    """A location as one short line."""
    return ' '.join(str(text or '').split())[:60]


def rows():
    """Every device row, by id. A copy - callers may change it freely."""
    data = _store().get('devices', {}) or {}
    return json.loads(json.dumps({k: v for k, v in sorted(data.items()) if isinstance(v, dict)}))


def get(device_id):
    row = rows().get(_slug(device_id))
    if not row:
        raise DeviceError(f"There is no device named '{device_id}'.")
    return row


def _write(mutator):
    """Atomic read-change-write of the whole table. mutator(table) edits in place."""
    def step(table):
        table = dict(table or {})
        mutator(table)
        return table
    _store().update_with_lock('devices', step, default={})


# --- drivers -----------------------------------------------------------------

def drivers():
    """Every driver a plugin declares: the usable ones, and the ones whose
    plugin is off (greyed in the UI, with the plugin to enable)."""
    ready = {d['driver']: d for d in _registry().list_drivers()}
    out = [dict(d, available=True, note='') for d in ready.values()]
    try:
        for info in _all_plugin_info():
            manifest = (info or {}).get('manifest') or {}
            for dd in (manifest.get('capabilities', {}).get('devices') or []):
                did = _slug(dd.get('driver')) if isinstance(dd, dict) else ''
                if not did or did in ready or any(o['driver'] == did for o in out):
                    continue
                title = manifest.get('short_display_name') or info['name']
                out.append({'driver': did, 'label': str(dd.get('label') or did),
                            'icon': str(dd.get('icon') or ''), 'module': '',
                            'capabilities': [str(c) for c in (dd.get('capabilities') or [])],
                            'config_schema': [], 'plugin_name': info['name'],
                            'available': False,
                            'note': f"Enable the {title} plugin to use this"})
    except Exception as e:
        logger.warning(f"[DEVICES] could not list drivers of disabled plugins: {e}")
    return sorted(out, key=lambda d: (not d['available'], d['label'].lower()))


def _driver(driver_id, plugin_hint=''):
    """(module, spec) of a driver that may run right now, or DeviceError."""
    global _modules_gen
    spec = _registry().get_driver(driver_id)
    if not spec:
        who = plugin_hint or driver_id
        raise DeviceError(f"The '{driver_id}' driver is not loaded. Enable the "
                          f"{who} plugin in Settings > Plugins.")
    if spec['plugin_name'] == _registry().CORE:       # ships inside core: always there
        try:
            return importlib.import_module(f"core.devices.drivers.{driver_id}"), spec
        except Exception as e:
            logger.error(f"[DEVICES] core driver '{driver_id}' failed to import: {e}", exc_info=True)
            raise DeviceError(f"The '{driver_id}' driver failed to load: {e}")
    # The owning plugin must still be enabled and loaded - checked on every
    # call, not only at first import (game-room finding 4.14).
    info = _plugin_info(spec['plugin_name'])
    if not info or not (info.get('enabled') and info.get('loaded')):
        raise DeviceError(f"The {spec['plugin_name']} plugin is off, so its "
                          f"'{driver_id}' driver cannot run.")
    with _lock:
        gen = _registry().generation()
        if gen != _modules_gen:          # a plugin came, went, or reloaded
            for mod in _modules.values():
                sys.modules.pop(mod.__name__, None)
            _modules.clear()
            _modules_gen = gen
        mod = _modules.get(driver_id)
        if mod is None:
            dotted = spec['module'][:-3].replace('/', '.')
            try:
                mod = importlib.import_module(f"plugins.{spec['plugin_name']}.{dotted}")
            except Exception as e:
                logger.error(f"[DEVICES] driver '{driver_id}' failed to import: {e}", exc_info=True)
                raise DeviceError(f"The '{driver_id}' driver failed to load: {e}")
            missing = [fn for fn in ('describe', 'status', 'run') if not callable(getattr(mod, fn, None))]
            if missing:
                raise DeviceError(f"The '{driver_id}' driver is missing {', '.join(missing)}().")
            _modules[driver_id] = mod
    return mod, spec


def _call_tool_for(plugin, uses=()):
    """The tool door handed to a driver: it runs one of its OWN plugin's tools
    through the function manager - same executor, same state, same gates.
    A driver that ships inside core owns no plugin. It may run the tools it
    names in its SPEC (`uses_tools`), so core holds no second copy of what a
    plugin already does."""
    def call_tool(name, args=None):
        fm = _function_manager()
        owner = fm.tool_plugin(name)
        if plugin == _registry().CORE:
            if name not in uses or not owner:
                return f"'{name}' is not there to be used. Its plugin may be switched off.", False
        elif owner != plugin:
            return f"The {plugin} driver may only run its own plugin's tools, not '{name}'.", False
        return fm.execute_function(name, dict(args or {}), allowed_tools={name}, with_success=True)
    return call_tool


def _part_secrets(device_id, driver_id):
    """This part's secrets, keyed by bare field name. Raises DeviceError when
    a stored secret cannot be read on this machine."""
    mod = _secrets()
    try:
        every = mod.resolve(device_id)
    except Exception as e:
        raise DeviceError(str(e))
    prefix = driver_id + '.'
    return mod.Secrets(device_id, {f[len(prefix):]: every.get(f)
                                   for f in every.fields() if f.startswith(prefix)})


# --- config ------------------------------------------------------------------

def _is_rows(field):
    return field.get('widget') == 'rows' or field.get('type') == 'rows'


def _columns(field):
    return [c if isinstance(c, str) else str(c.get('key') or '')
            for c in (field.get('columns') or []) if c]


def _is_found(field):
    return field.get('widget') == 'found' or field.get('type') == 'found'


def _picks(field, value):
    """A filter as it is stored: {'all': bool, 'only': [{'id', 'name'}]}.
    Never set = everything counts. "only" is kept while "all" is on, so the
    user's ticks are still there when they switch back."""
    value = value if isinstance(value, dict) else {}
    only, seen = [], set()
    for item in (value.get('only') if isinstance(value.get('only'), list) else []):
        ident = str(item.get('id') or '').strip()[:200] if isinstance(item, dict) else ''
        if ident and ident not in seen:
            seen.add(ident)
            only.append({'id': ident, 'name': ' '.join(str(item.get('name') or ident).split())[:80]})
    every = value.get('all', True)
    if isinstance(every, str):
        every = every.strip().lower() in ('1', 'true', 'yes', 'on')
    return {'all': bool(every), 'only': only[:MAX_FOUND if field.get('many', True) else 1]}


def _coerce(field, value):
    default = field.get('default')
    if _is_found(field):
        return _picks(field, value)
    if _is_rows(field):
        cols = _columns(field)
        out = []
        for item in (value if isinstance(value, list) else []):
            if not isinstance(item, dict):
                continue
            row = {c: str(item.get(c) or '').strip()[:1000] for c in cols}
            if any(row.values()):
                out.append(row)
        return out[:MAX_ROWS]
    kind = field.get('type')
    if kind == 'boolean':
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if kind == 'number':
        try:
            n = float(value)
            return int(n) if n == int(n) else n
        except (TypeError, ValueError):
            return default if isinstance(default, (int, float)) else 0
    text = '' if value is None else str(value).strip()[:2000]
    options = [str(o.get('value')) for o in (field.get('options') or []) if isinstance(o, dict)]
    if options and text not in options:
        return str(default) if str(default) in options else options[0]
    return text


def _clean(schema, incoming, previous):
    """(config, secret_ops) from a submitted form. Only schema keys survive.
    secret_ops: {field: str to store | None to forget}; a field that was not
    sent is kept as it is."""
    incoming = incoming if isinstance(incoming, dict) else {}
    config, ops = {}, {}
    for field in schema:
        key = field['key']
        if field.get('secret'):
            if key in incoming:
                v = incoming[key]
                ops[key] = None if v in ('', None, CLEAR) else str(v)[:16000]
            continue
        if key in incoming:
            config[key] = _coerce(field, incoming[key])
        elif key in previous:
            config[key] = previous[key]
        else:
            config[key] = _coerce(field, field.get('default'))
    return config, ops


def _apply_secrets(device_id, driver_id, ops):
    """Store or forget each secret. Returns the fields that failed."""
    failed = []
    for field, value in ops.items():
        name = f"{driver_id}.{field}"
        done = _secrets().clear(device_id, name) if value is None else _secrets().put(device_id, name, value)
        if not done:
            failed.append(field)
    return failed


def _part(row, driver_id):
    return next((p for p in row.get('parts', []) if p.get('driver') == driver_id), None)


# --- what is here right now ----------------------------------------------------

def found(driver_id, config=None):
    """What a driver can see right now: [{'id', 'name', 'kind'}], by name.
    A driver without discover() sees nothing. Raises DeviceError."""
    mod, _ = _driver(_slug(driver_id))
    look = getattr(mod, 'discover', None)
    if not callable(look):
        return []
    try:
        told = look(dict(config or {})) or []
    except Exception as e:
        logger.error(f"[DEVICES] {driver_id}.discover failed: {e}", exc_info=True)
        raise DeviceError(f"The '{driver_id}' driver could not look ({type(e).__name__}).")
    out, seen = [], set()
    for item in told if isinstance(told, (list, tuple)) else []:
        ident = str(item.get('id') or '').strip()[:200] if isinstance(item, dict) else ''
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append({'id': ident, 'name': ' '.join(str(item.get('name') or ident).split())[:80],
                    'kind': ' '.join(str(item.get('kind') or '').split())[:24]})
        if len(out) >= MAX_FOUND:
            break
    return sorted(out, key=lambda t: (t['name'].lower(), t['id']))


def filter_field(spec):
    """The field of a driver that holds its filter, or None."""
    return next((f for f in (spec or {}).get('config_schema') or [] if _is_found(f)), None)


def passing(spec, config, things):
    """The found things that count as this device: its filter, applied. A
    driver without a filter field takes everything."""
    field = filter_field(spec)
    if not field:
        return list(things)
    picks = _picks(field, (config or {}).get(field['key']))
    if picks['all']:
        kept = list(things)
    else:
        wanted = {p['id'] for p in picks['only']}
        kept = [t for t in things if t['id'] in wanted]
    return kept if field.get('many', True) else kept[:1]


def here_now(row, part):
    """The things that are here and count as this device. Raises DeviceError."""
    _, spec = _driver(part['driver'], part.get('plugin', ''))
    config = dict(part.get('config') or {})
    return passing(spec, config, found(part['driver'], config))


def capabilities(part, spec):
    """What this part can do: what its driver can do, held to what the device
    itself said it has. A device that never said has all of it."""
    said = part.get('has')
    every = list((spec or {}).get('capabilities') or [])
    return [c for c in every if c in said] if isinstance(said, list) else every


def _learn(row, part, spec, said):
    """Keep what the device said it has. A name its driver does not know is
    dropped and logged. Written only when the list changed."""
    if not isinstance(said, (list, tuple)):
        return
    names = {_slug(n) for n in said}
    kept = [c for c in spec['capabilities'] if c in names]
    if kept == part.get('has'):
        return
    odd = sorted(n for n in names - set(kept) if n)
    if odd:
        logger.warning(f"[DEVICES] {row['id']}: it says it has {', '.join(odd)[:200]}, which the "
                       f"{part['driver']} driver does not know. Left out.")
    part['has'] = kept

    def step(table):
        mine = _part(table.get(row['id']) or {}, part['driver'])
        if mine is not None:
            mine['has'] = kept
    _write(step)
    logger.info(f"[DEVICES] {row['id']}: it says it has {', '.join(kept) or 'nothing'}")


def _shut(row):
    names = row.get('locked')
    if not isinstance(names, list):              # never set: what its drivers ask for
        names = []
        for part in row.get('parts', []):
            spec = _registry().get_driver(part.get('driver')) or {}
            names += spec.get('locked_by_default') or []
    return sorted({str(n) for n in names if n in LOCKABLE})


def locked(row):
    """The capabilities she may not use on this device."""
    return _shut(row)


def _build_part(device_id, driver_id, incoming, previous=None):
    mod, spec = _driver(driver_id)
    config, ops = _clean(spec['config_schema'], incoming, previous or {})
    if callable(getattr(mod, 'validate', None)):
        config, error = mod.validate(config)
        if error:
            raise DeviceError(error)
    return {'driver': driver_id, 'plugin': spec['plugin_name'], 'config': config}, ops, spec


def tell(row):
    """After a save: each part's driver may have apply(device, config,
    secrets), which hands the settings to the hardware. Returns what went
    wrong, in words, one line per part. Never raises."""
    problems = []
    for part in row.get('parts', []):
        secrets = None
        try:
            mod, _ = _driver(part['driver'], part.get('plugin', ''))
            apply = getattr(mod, 'apply', None)
            if not callable(apply):
                continue
            secrets = _part_secrets(row['id'], part['driver'])
            apply(_brief(row), dict(part.get('config') or {}), secrets)
        except DeviceError as e:
            problems.append(f"{part['driver']}: {e}")
        except Exception as e:
            logger.error(f"[DEVICES] {part['driver']}.apply failed: {e}", exc_info=True)
            problems.append(f"{part['driver']}: {secrets.scrub(str(e)) if secrets else e}")
    return problems


# --- add, change, remove (the Devices page only - never a tool) --------------

def add(device_id, label, driver_id, config=None, location=''):
    device_id = _slug(device_id)
    if not ID_RE.fullmatch(device_id):
        raise DeviceError("A device name is lowercase letters, digits, dashes and "
                          "underscores, up to 33 characters, starting with a letter or digit.")
    if device_id in RESERVED:
        raise DeviceError(f"'{device_id}' is a name Sapphire uses herself. Pick another.")
    table = rows()
    if device_id in table:
        raise DeviceError(f"A device named '{device_id}' already exists.")
    if len(table) >= MAX_DEVICES:
        raise DeviceError(f"The limit is {MAX_DEVICES} devices.")
    part, ops, spec = _build_part(device_id, _slug(driver_id), config)
    row = {'id': device_id, 'label': str(label or '').strip()[:80] or device_id,
           'location': _place(location),
           'enabled': True, 'created': int(time.time()), 'parts': [part]}
    row['locked'] = locked(row)
    # A name that was used before must not inherit the old device's secrets.
    _secrets().delete(device_id)
    _write(lambda t: t.__setitem__(device_id, row))
    failed = _apply_secrets(device_id, part['driver'], ops)
    _health().forget(device_id)          # a reused name inherits no belief: offline until it answers
    _health().poke(device_id)
    _changed()
    return row, failed


def update(device_id, label=None, enabled=None, parts=None, new_id=None, location=None,
           locked=None):
    """Change a device. parts: {driver: submitted config}. locked: what she
    may not use. A map {capability: bool} changes only the capabilities it
    names, so a switch the page did not show is never touched (a device that
    was off showed none, and saving it opened every lock, 2026-09-28). A list
    is the whole truth. Returns (row, failed secret fields)."""
    row = get(device_id)
    device_id = row['id']
    if isinstance(locked, dict):
        names = set(_shut(row))
        for name, shut in locked.items():
            if _slug(name) in LOCKABLE:
                (names.add if shut else names.discard)(_slug(name))
        row['locked'] = sorted(names)
    elif locked is not None:
        row['locked'] = sorted({_slug(n) for n in locked if _slug(n) in LOCKABLE}) \
            if isinstance(locked, (list, tuple)) else []
    if label is not None:
        row['label'] = str(label).strip()[:80] or device_id
    if location is not None:
        row['location'] = _place(location)
    if enabled is not None:
        row['enabled'] = bool(enabled)
    pending = []
    for driver_id, incoming in (parts or {}).items():
        old = _part(row, _slug(driver_id))
        if not old:
            raise DeviceError(f"'{device_id}' has no '{driver_id}' part.")
        part, ops, _ = _build_part(device_id, old['driver'], incoming, old.get('config'))
        old.update(part)
        pending.append((old['driver'], ops))

    target = device_id
    if new_id is not None and _slug(new_id) != device_id:
        target = _slug(new_id)
        if not ID_RE.fullmatch(target) or target in RESERVED:
            raise DeviceError("That new name is not a valid device name.")
        if target in rows():
            raise DeviceError(f"A device named '{target}' already exists.")
        if not _secrets().rename(device_id, target):
            raise DeviceError("The device's secrets could not be moved, so it was not renamed.")
        row['id'] = target

    def step(table):
        table.pop(device_id, None)
        table[target] = row
    try:
        _write(step)
    except Exception:
        if target != device_id:
            _secrets().rename(target, device_id)     # put the secrets back
        raise
    failed = []
    for driver_id, ops in pending:
        failed += _apply_secrets(target, driver_id, ops)
    if target != device_id:
        _health().rename(device_id, target)
    else:
        _health().poke(target)           # its settings may have changed: look again soon
    _changed()
    return row, failed


def remove(device_id):
    row = get(device_id)
    _write(lambda t: t.pop(row['id'], None))
    _secrets().delete(row['id'])
    _health().forget(row['id'])
    _changed()
    return row


def public(row):
    """A row for the Devices page: every part with its schema and its values.
    A secret field reads 'set' or '' - never the secret."""
    held = _secrets().status(row['id'])
    parts = []
    for part in row.get('parts', []):
        spec = _registry().get_driver(part['driver'])
        has = capabilities(part, spec)
        # a field that belongs to something this device does not have is not shown
        schema = [f for f in (spec['config_schema'] if spec else [])
                  if f.get('capability') in has or f.get('capability') not in spec['capabilities']]
        values = dict(part.get('config') or {})
        unreadable = []
        for field in schema:
            if field.get('secret'):
                state = held.get(f"{part['driver']}.{field['key']}")
                values[field['key']] = 'set' if state == 'set' else ''
                if state == 'undecryptable':
                    unreadable.append(field['key'])
        parts.append({'driver': part['driver'], 'plugin': part.get('plugin', ''),
                      'label': spec['label'] if spec else part['driver'],
                      'available': bool(spec), 'schema': schema, 'values': values,
                      'capabilities': has,
                      'unreadable': unreadable})
    return {'id': row['id'], 'label': row.get('label', row['id']),
            'location': _place(row.get('location')), 'locked': locked(row),
            'enabled': bool(row.get('enabled', True)), 'parts': parts}


# --- describe ----------------------------------------------------------------

def describe(row):
    """What this device can do, in tab order:
    [{'capability', 'label', 'help', 'driver', 'actions': {name: {'help', 'example'}},
      'error', 'lockable', 'locked'}]. locked = she may not use it."""
    out = []
    shut = locked(row)
    for part in row.get('parts', []):
        try:
            mod, spec = _driver(part['driver'], part.get('plugin', ''))
            told = mod.describe(_brief(row), dict(part.get('config') or {})) or {}
        except DeviceError as e:
            out.append({'capability': part['driver'], 'label': part['driver'], 'help': '',
                        'driver': part['driver'], 'actions': {}, 'error': str(e),
                        'lockable': False, 'locked': False})
            continue
        except Exception as e:
            logger.error(f"[DEVICES] {part['driver']}.describe failed: {e}", exc_info=True)
            out.append({'capability': part['driver'], 'label': part['driver'], 'help': '',
                        'driver': part['driver'], 'actions': {}, 'error': f"driver error: {e}",
                        'lockable': False, 'locked': False})
            continue
        for cap in capabilities(part, spec):
            if not isinstance(told.get(cap), dict):
                continue            # this device does not have it (self-describing drivers)
            info = told[cap]
            actions = {}
            for name, a in (info.get('actions') or {}).items():
                a = a if isinstance(a, dict) else {}
                actions[_slug(name)] = {'help': str(a.get('help') or '')[:120],
                                        'example': str(a.get('example') or '')[:200]}
            out.append({'capability': cap, 'label': str(info.get('label') or cap),
                        'help': str(info.get('help') or '')[:120],
                        'driver': part['driver'], 'actions': actions, 'error': '',
                        'lockable': cap in LOCKABLE, 'locked': cap in shut})
    return out


def _brief(row):
    return {'id': row['id'], 'label': row.get('label', row['id']),
            'location': _place(row.get('location'))}


# --- status ------------------------------------------------------------------
# What is BELIEVED about a device lives in health.py (online or offline, with
# hysteresis, kept across restarts). _probe only asks.

def _probe(row):
    """Ask every part how it is, right now. Never raises. The answer is raw:
    health.told() decides what it means."""
    parts = []
    for part in row.get('parts', []):
        entry = {'driver': part['driver'], 'online': False, 'detail': '', 'readings': {}}
        try:
            mod, spec = _driver(part['driver'], part.get('plugin', ''))
            secrets = _part_secrets(row['id'], part['driver'])
            told = mod.status(_brief(row), dict(part.get('config') or {}), secrets) or {}
            entry['online'] = bool(told.get('online'))
            _learn(row, part, spec, told.get('has'))
            entry['detail'] = secrets.scrub(str(told.get('detail') or ''))[:300]
            readings = told.get('readings')
            if isinstance(readings, dict):
                entry['readings'] = {str(k)[:40]: secrets.scrub(str(v))[:80]
                                     for k, v in list(readings.items())[:20]}
            if spec.get('presence'):             # said the same way for every driver
                here = here_now(row, part)
                entry['readings'] = dict({'connected': ', '.join(
                    t['name'] + (f" ({t['kind']})" if t['kind'] else '') for t in here)[:80] or 'nothing'},
                    **entry['readings'])
        except DeviceError as e:
            entry['detail'] = str(e)
        except Exception as e:
            logger.error(f"[DEVICES] {part['driver']}.status failed: {e}", exc_info=True)
            entry['detail'] = f"driver error: {e}"
        parts.append(entry)
    return {'online': bool(parts) and all(p['online'] for p in parts),
            'parts': parts, 'ts': time.time()}


def status(device_id, fresh=True):
    """What is believed about one device: {'online', 'checking', 'ts',
    'misses', 'parts'}. fresh=True asks the device first and believes the
    answer; fresh=False never blocks."""
    row = get(device_id)
    return _health().check(row) if fresh else _health().view(row['id'])


def statuses():
    """{id: belief} for every enabled device. Never blocks: the health
    keeper asks the devices on its own clock."""
    return _health().views()


# --- what she reads ----------------------------------------------------------

def _call(*args):
    return 'device_action(' + ','.join(json.dumps(str(a)) for a in args) + ')'


def _age(ts):
    s = max(0, int(time.time() - ts))
    return f"{s}s ago" if s < 90 else f"{s // 60}m ago"


def _columns_text(lines):
    """Left-aligned columns, two spaces apart, indented two."""
    if not lines:
        return ''
    widths = [max(len(str(r[i])) for r in lines) for i in range(len(lines[0]) - 1)]
    return '\n'.join('  ' + '  '.join(str(c).ljust(w) for c, w in zip(r, widths)) + '  ' + str(r[-1])
                     for r in lines).rstrip()


LOCKED_NOTE = f"the user has not allowed you this. They can change it in {PAGE}"


def _caps(row):
    """What this device can do, by name. A part whose driver is off is said
    so, and so is a capability she may not use."""
    return [c['capability'] + (' (driver off)' if c['error'] else ' (locked)' if c['locked'] else '')
            for c in describe(row)]


def list_text():
    table = {k: v for k, v in rows().items() if v.get('enabled', True)}
    if not table:
        return f"No devices yet. The user adds them in {PAGE}.", True
    found = statuses()
    placed = any(_place(row.get('location')) for row in table.values())
    lines = []
    for device_id, row in table.items():
        state = 'online' if found[device_id]['online'] else 'offline'
        where = (_place(row.get('location')) or '-',) if placed else ()
        lines.append((device_id, state) + where + (', '.join(_caps(row)) or '-',))
    first = next(iter(table))
    return f"Devices ({len(table)}):\n{_columns_text(lines)}\nNext: {_call(first)}", True


def _usable(device_id):
    """The row she named, or DeviceError with the way forward."""
    row = rows().get(_slug(device_id))
    if row and not row.get('enabled', True):
        raise DeviceError(f"'{row['id']}' is turned off in {PAGE}.")
    if not row:
        names = [k for k, v in rows().items() if v.get('enabled', True)]
        known = f" Devices: {', '.join(names)}." if names else f" No devices exist yet. The user adds them in {PAGE}."
        raise DeviceError(f"There is no device named '{device_id}'.{known}")
    return row


def _head(row, st):
    state = 'online' if st['online'] else 'offline'
    label = row.get('label') or row['id']
    name = row['id'] if label == row['id'] else f"{row['id']} - {label}"
    where = _place(row.get('location'))
    return name + (f" ({where})" if where else '') + f" - {state}"


def status_text(device_id):
    try:
        row = _usable(device_id)
    except DeviceError as e:
        return str(e), False
    st = _health().check(row)
    lines = [(p['driver'], 'ok' if p['online'] else 'down', p['detail'] or '-') for p in st['parts']]
    out = [f"{_head(row, st)} (checked just now)", _columns_text(lines)]
    if st['online'] and st['misses']:
        out.append("It did not answer just now. It counts as offline if it misses again.")
    for p in st['parts']:
        for k, v in p['readings'].items():
            out.append(f"  {k}: {v}")
    out.append(f"Can do: {', '.join(_caps(row)) or 'nothing'}")
    out.append(f"Next: {_call(row['id'])}")
    return '\n'.join(x for x in out if x), True


def _down_note(st, driver=None):
    """One line that saves her a wasted call when the device is believed
    offline. A single miss says nothing here."""
    if st['online']:
        return ''
    down = [p for p in st['parts'] if not p['online']]
    mine = [p for p in down if p['driver'] == driver] or down
    why = (mine[0]['detail'] if mine else '') or ('not heard from yet' if not st['ts'] else 'no answer')
    return f"Offline: {why}\nCommands will fail until it is back."


def _capability_list(row, caps):
    st = _health().view(row['id'])
    lines, errors = [], []
    for c in caps:
        if c['error']:
            errors.append(f"  {c['capability']}: {c['error']}")
            continue
        if c['locked']:
            lines.append((c['capability'], c['help'] or '-', f"locked: {LOCKED_NOTE}"))
            continue
        first = next(iter(c['actions'].items()), None)
        if first is None:
            lines.append((c['capability'], c['help'] or '-', '(no actions yet)'))
            continue
        name, a = first
        args = [row['id'], c['capability'], name] + ([a['example']] if a['example'] else [])
        lines.append((c['capability'], c['help'] or '-', _call(*args)))
    body = '\n'.join(x for x in (_columns_text(lines), '\n'.join(errors)) if x)
    body = body or '  (nothing it can do yet)'
    # one example per line reads as "that is all it can do" (Sapphire, 2026-09-28)
    most = max((c for c in caps if not c['error'] and not c['locked']),
               key=lambda c: len(c['actions']), default=None)
    more = (f"Each line is one example. {_call(row['id'], most['capability'])} lists all "
            f"{len(most['actions'])} actions of {most['capability']}."
            if most and len(most['actions']) > 1 else '')
    return '\n'.join(x for x in (_head(row, st), body, more, _down_note(st)) if x)


def _action_list(row, cap):
    lines = []
    for name, a in cap['actions'].items():
        args = [row['id'], cap['capability'], name] + ([a['example']] if a['example'] else [])
        lines.append((name, a['help'] or '-', _call(*args)))
    body = _columns_text(lines) or f"  (no actions yet - the user adds them in {PAGE})"
    st = _health().view(row['id'])
    return '\n'.join(x for x in (f"{row['id']} / {cap['capability']}", body,
                                  _down_note(st, cap['driver'])) if x)


def _result(told, secrets):
    """What an action answered: text, or {'text', 'images'} when it took
    pictures. The text is scrubbed of secrets. Pictures are checked, never
    trusted: a known type, a sane size, a few at most."""
    if isinstance(told, dict) and isinstance(told.get('images'), list):
        text = secrets.scrub(str(told.get('text') or '')).strip()
        images = []
        for img in told['images'][:MAX_PICTURES * 8]:
            if len(images) >= MAX_PICTURES:
                break
            if not isinstance(img, dict):
                continue
            data, kind = img.get('data'), str(img.get('media_type') or '').lower()
            if isinstance(data, str) and data and len(data) <= MAX_PICTURE and kind in PICTURE_TYPES:
                images.append({'data': data, 'media_type': kind})
        if images:
            return {'text': text or 'A picture.', 'images': images}
        return text or 'No picture came back.'
    return secrets.scrub(str(told if told is not None else '(no output)'))


def text_of(result):
    """The words of what run() answered, for a caller that shows no pictures."""
    return str(result.get('text') or '') if isinstance(result, dict) else str(result)


def run(device_id=None, capability=None, action=None, value=None, owner=False):
    """device_action. Every level of missing or wrong argument answers with
    the list one level up, so a wrong guess costs one call, never a dead end.
    Returns (result, ok). result is text, or {'text', 'images'} when the
    action took pictures (the shape every picture-taking tool answers with).
    owner=True is the user at the Devices page, or core itself: a capability
    that is locked for her still runs."""
    if not _slug(device_id):
        return list_text()
    try:
        row = _usable(device_id)
    except DeviceError as e:
        return str(e), False
    caps = describe(row)

    cap_name = _slug(capability)
    if not cap_name:
        return _capability_list(row, caps), True
    cap = next((c for c in caps if c['capability'] == cap_name), None)
    if not cap:
        return f"'{row['id']}' has no '{capability}'.\n{_capability_list(row, caps)}", False
    if cap['error']:
        return f"{row['id']} / {cap_name}: {cap['error']}", False
    if cap['locked'] and not owner:
        return f"'{cap_name}' on '{row['id']}' is locked: {LOCKED_NOTE}.", False

    act = _slug(action)
    if not act:
        return _action_list(row, cap), True
    if act not in cap['actions']:
        return f"'{cap_name}' has no action '{action}'.\n{_action_list(row, cap)}", False

    part = _part(row, cap['driver'])
    try:
        mod, spec = _driver(part['driver'], part.get('plugin', ''))
        secrets = _part_secrets(row['id'], part['driver'])
        told, ok = mod.run(_brief(row), cap_name, act, '' if value is None else str(value),
                           dict(part.get('config') or {}), secrets,
                           _call_tool_for(spec['plugin_name'], spec.get('uses_tools') or ()))
        return _result(told, secrets), bool(ok)
    except DeviceError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"[DEVICES] {part['driver']}.run({cap_name}, {act}) failed: {e}", exc_info=True)
        return f"{row['id']} / {cap_name} / {act} failed: {type(e).__name__}", False
