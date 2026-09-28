# core/devices/engine.py - the device engine (tmp/device-manager-plan.md)
#
# ONE module holds the rows, loads the drivers, and answers every question.
# The three tools (functions/devices.py) and the routes (core/routes/devices.py)
# are thin doors into it: the Test button and device_status are one function,
# the Try button and device_action are one function.
#
# Core since 2026-09-27 (Krem's ruling: satellites make voice a device matter,
# and devices are never meant to be switched off). Drivers are NOT core: a
# plugin that knows a transport declares one in its manifest
# (capabilities.devices) and registry.py holds the list.
#
# A device row (user/plugin_state/devices.json, key "devices"):
#   {"id", "label", "enabled", "created",
#    "parts": [{"driver", "plugin", "config": {...}}]}
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
from concurrent.futures import ThreadPoolExecutor, wait

logger = logging.getLogger(__name__)

STORE = 'devices'          # the state file's name: user/plugin_state/devices.json
ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,32}$')
STATUS_TTL = 30          # seconds a status answer stays fresh
LIST_WAIT = 6            # seconds device_list waits for stale devices
MAX_DEVICES = 64
MAX_ROWS = 50            # items in one rows field
CLEAR = '__CLEAR__'
PAGE = 'Settings > Devices'

_lock = threading.RLock()
_modules = {}            # driver id -> loaded driver module
_modules_gen = None
_status = {}             # device id -> {'online', 'parts', 'ts'}
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='device-status')


class DeviceError(Exception):
    """A message fit to show her or the user as it is."""


# --- doors to core (patched in tests) ----------------------------------------

def _store():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(STORE)


def _secrets():
    from core.devices import secret_store
    return secret_store


def _registry():
    from core.devices import registry
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


# --- rows --------------------------------------------------------------------

def _slug(name):
    return str(name or '').strip().lower()


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


def _call_tool_for(plugin):
    """The tool door handed to a driver: it runs one of its OWN plugin's tools
    through the function manager - same executor, same state, same gates."""
    def call_tool(name, args=None):
        fm = _function_manager()
        if fm.tool_plugin(name) != plugin:
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


def _coerce(field, value):
    default = field.get('default')
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


def _build_part(device_id, driver_id, incoming, previous=None):
    mod, spec = _driver(driver_id)
    config, ops = _clean(spec['config_schema'], incoming, previous or {})
    if callable(getattr(mod, 'validate', None)):
        config, error = mod.validate(config)
        if error:
            raise DeviceError(error)
    return {'driver': driver_id, 'plugin': spec['plugin_name'], 'config': config}, ops, spec


# --- add, change, remove (the Devices page only - never a tool) --------------

def add(device_id, label, driver_id, config=None):
    device_id = _slug(device_id)
    if not ID_RE.fullmatch(device_id):
        raise DeviceError("A device name is lowercase letters, digits, dashes and "
                          "underscores, up to 33 characters, starting with a letter or digit.")
    table = rows()
    if device_id in table:
        raise DeviceError(f"A device named '{device_id}' already exists.")
    if len(table) >= MAX_DEVICES:
        raise DeviceError(f"The limit is {MAX_DEVICES} devices.")
    part, ops, spec = _build_part(device_id, _slug(driver_id), config)
    row = {'id': device_id, 'label': str(label or '').strip()[:80] or device_id,
           'enabled': True, 'created': int(time.time()), 'parts': [part]}
    # A name that was used before must not inherit the old device's secrets.
    _secrets().delete(device_id)
    _write(lambda t: t.__setitem__(device_id, row))
    failed = _apply_secrets(device_id, part['driver'], ops)
    _forget_status(device_id)
    return row, failed


def update(device_id, label=None, enabled=None, parts=None, new_id=None):
    """Change a device. parts: {driver: submitted config}. Returns (row, failed
    secret fields)."""
    row = get(device_id)
    device_id = row['id']
    if label is not None:
        row['label'] = str(label).strip()[:80] or device_id
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
        if not ID_RE.fullmatch(target):
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
    _forget_status(device_id)
    _forget_status(target)
    return row, failed


def remove(device_id):
    row = get(device_id)
    _write(lambda t: t.pop(row['id'], None))
    _secrets().delete(row['id'])
    _forget_status(row['id'])
    return row


def public(row):
    """A row for the Devices page: every part with its schema and its values.
    A secret field reads 'set' or '' - never the secret."""
    held = _secrets().status(row['id'])
    parts = []
    for part in row.get('parts', []):
        spec = _registry().get_driver(part['driver'])
        schema = spec['config_schema'] if spec else []
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
                      'capabilities': spec['capabilities'] if spec else [],
                      'unreadable': unreadable})
    return {'id': row['id'], 'label': row.get('label', row['id']),
            'enabled': bool(row.get('enabled', True)), 'parts': parts}


# --- describe ----------------------------------------------------------------

def describe(row):
    """What this device can do, in tab order:
    [{'capability', 'label', 'help', 'driver', 'actions': {name: {'help', 'example'}}, 'error'}]"""
    out = []
    for part in row.get('parts', []):
        try:
            mod, spec = _driver(part['driver'], part.get('plugin', ''))
            told = mod.describe(_brief(row), dict(part.get('config') or {})) or {}
        except DeviceError as e:
            out.append({'capability': part['driver'], 'label': part['driver'], 'help': '',
                        'driver': part['driver'], 'actions': {}, 'error': str(e)})
            continue
        except Exception as e:
            logger.error(f"[DEVICES] {part['driver']}.describe failed: {e}", exc_info=True)
            out.append({'capability': part['driver'], 'label': part['driver'], 'help': '',
                        'driver': part['driver'], 'actions': {}, 'error': f"driver error: {e}"})
            continue
        for cap in spec['capabilities']:
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
                        'driver': part['driver'], 'actions': actions, 'error': ''})
    return out


def _brief(row):
    return {'id': row['id'], 'label': row.get('label', row['id'])}


# --- status ------------------------------------------------------------------

def _forget_status(device_id):
    with _lock:
        _status.pop(device_id, None)


def _probe(row):
    """Ask every part how it is. Never raises."""
    parts = []
    for part in row.get('parts', []):
        entry = {'driver': part['driver'], 'online': False, 'detail': '', 'readings': {}}
        try:
            mod, _ = _driver(part['driver'], part.get('plugin', ''))
            secrets = _part_secrets(row['id'], part['driver'])
            told = mod.status(_brief(row), dict(part.get('config') or {}), secrets) or {}
            entry['online'] = bool(told.get('online'))
            entry['detail'] = secrets.scrub(str(told.get('detail') or ''))[:300]
            readings = told.get('readings')
            if isinstance(readings, dict):
                entry['readings'] = {str(k)[:40]: secrets.scrub(str(v))[:80]
                                     for k, v in list(readings.items())[:20]}
        except DeviceError as e:
            entry['detail'] = str(e)
        except Exception as e:
            logger.error(f"[DEVICES] {part['driver']}.status failed: {e}", exc_info=True)
            entry['detail'] = f"driver error: {e}"
        parts.append(entry)
    result = {'online': bool(parts) and all(p['online'] for p in parts),
              'parts': parts, 'ts': time.time()}
    with _lock:
        _status[row['id']] = result
    return result


def _cached(device_id):
    with _lock:
        hit = _status.get(device_id)
    if hit and time.time() - hit['ts'] < STATUS_TTL:
        return hit
    return None


def status(device_id, fresh=True):
    """One device's health. fresh=True always asks the device."""
    row = get(device_id)
    return (None if fresh else _cached(row['id'])) or _probe(row)


def statuses(wait_s=0):
    """{id: status or None} for every enabled device. Stale ones are asked in
    parallel; wait_s is how long to wait for their answers (0 = do not wait,
    the answers land in the cache for the next call)."""
    table = {k: v for k, v in rows().items() if v.get('enabled', True)}
    out = {k: _cached(k) for k in table}
    jobs = {k: _pool.submit(_probe, table[k]) for k, hit in out.items() if hit is None}
    if jobs and wait_s:
        wait(list(jobs.values()), timeout=wait_s)
        for k, job in jobs.items():
            if job.done() and not job.exception():
                out[k] = job.result()
    return out


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


def _caps(row):
    """What this device can do, by name. A part whose driver is off is said so."""
    return [c['capability'] + (' (driver off)' if c['error'] else '') for c in describe(row)]


def list_text():
    table = {k: v for k, v in rows().items() if v.get('enabled', True)}
    if not table:
        return f"No devices yet. The user adds them in {PAGE}.", True
    found = statuses(wait_s=LIST_WAIT)
    lines = []
    for device_id, row in table.items():
        st = found.get(device_id)
        state = 'checking' if st is None else ('online' if st['online'] else 'offline')
        lines.append((device_id, state, ', '.join(_caps(row)) or '-'))
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
    return f"{name} - {state}"


def status_text(device_id):
    try:
        row = _usable(device_id)
    except DeviceError as e:
        return str(e), False
    st = _probe(row)
    lines = [(p['driver'], 'ok' if p['online'] else 'DOWN', p['detail'] or '-') for p in st['parts']]
    out = [f"{_head(row, st)} (checked just now)", _columns_text(lines)]
    for p in st['parts']:
        for k, v in p['readings'].items():
            out.append(f"  {k}: {v}")
    out.append(f"Can do: {', '.join(_caps(row)) or 'nothing'}")
    out.append(f"Next: {_call(row['id'])}")
    return '\n'.join(x for x in out if x), True


def _down_note(st, driver=None):
    """One line that saves her a wasted call when the device is offline."""
    down = [p for p in st['parts'] if not p['online'] and driver in (None, p['driver'])]
    if not down:
        return ''
    why = down[0]['detail'] or 'no answer'
    return f"Offline: {why}\nCommands will fail until it is back."


def _capability_list(row, caps):
    st = _cached(row['id']) or _probe(row)
    lines, errors = [], []
    for c in caps:
        if c['error']:
            errors.append(f"  {c['capability']}: {c['error']}")
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
    return '\n'.join(x for x in (_head(row, st), body, _down_note(st)) if x)


def _action_list(row, cap):
    lines = []
    for name, a in cap['actions'].items():
        args = [row['id'], cap['capability'], name] + ([a['example']] if a['example'] else [])
        lines.append((name, a['help'] or '-', _call(*args)))
    body = _columns_text(lines) or f"  (no actions yet - the user adds them in {PAGE})"
    st = _cached(row['id']) or _probe(row)
    return '\n'.join(x for x in (f"{row['id']} / {cap['capability']}", body,
                                  _down_note(st, cap['driver'])) if x)


def run(device_id=None, capability=None, action=None, value=None):
    """device_action. Every level of missing or wrong argument answers with
    the list one level up, so a wrong guess costs one call, never a dead end.
    Returns (text, ok)."""
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

    act = _slug(action)
    if not act:
        return _action_list(row, cap), True
    if act not in cap['actions']:
        return f"'{cap_name}' has no action '{action}'.\n{_action_list(row, cap)}", False

    part = _part(row, cap['driver'])
    try:
        mod, spec = _driver(part['driver'], part.get('plugin', ''))
        secrets = _part_secrets(row['id'], part['driver'])
        text, ok = mod.run(_brief(row), cap_name, act, '' if value is None else str(value),
                           dict(part.get('config') or {}), secrets,
                           _call_tool_for(spec['plugin_name']))
        return secrets.scrub(str(text if text is not None else '(no output)')), bool(ok)
    except DeviceError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"[DEVICES] {part['driver']}.run({cap_name}, {act}) failed: {e}", exc_info=True)
        return f"{row['id']} / {cap_name} / {act} failed: {type(e).__name__}", False
