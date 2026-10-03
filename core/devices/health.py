# core/devices/health.py - is each device reachable? (tmp/device-manager-plan.md, wave 15)
#
# ONE belief per device: online or offline. There is no third state. A probe
# in flight is a separate flag (checking) that the page shows as a pulse, and
# the words never change because of it. The belief moves on evidence only:
# one answer = online, MISSES misses in a row = offline. A device never heard
# from is offline. After that the last known state is the assumption, also
# across a restart: it is kept in user/plugin_state/device_health.json
# (Krem's ruling, 2026-10-02).
#
# ONE keeper thread, like presence.py: it wakes on a poke or on the clock and
# hands every device that is due to the engine's probe pool. Devices that
# talk to Sapphire by themselves (a satellite posting what it heard, opening
# its light stream) check themselves in with seen(). The keeper holds no
# driver knowledge: engine._probe asks the device, this file only believes.
import logging
import threading
import time

logger = logging.getLogger(__name__)

POLL = 30            # seconds between looks at a device that is online
POLL_DOWN = 90       # ... at one that is offline: a dead host costs a whole timeout
SETTLE = 3           # seconds after start before the first look
MISSES = 2           # misses in a row before online turns to offline
STORE = 'device_health'     # user/plugin_state/device_health.json, key "online"

_lock = threading.Lock()
_wake = threading.Event()
_halt = threading.Event()
_keeper = None
_belief = {}         # device id -> {'online', 'misses', 'checking', 'ts', 'due', 'parts'}
_saved = None        # {device id: online} as the file had it, read once


def _engine():
    from core.devices import engine
    return engine


def _store():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state(STORE)


# --- the table (call with _lock held) ----------------------------------------

def _saved_locked():
    global _saved
    if _saved is None:
        try:
            data = _store().get('online', {}) or {}
            known = set(_engine().rows())             # a device that is gone is not kept
            _saved = {k: bool(v) for k, v in data.items() if k in known} if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"[DEVICES] health: last known states not read: {e}")
            _saved = {}
    return _saved


def _entry_locked(device_id):
    b = _belief.get(device_id)
    if b is None:
        b = _belief[device_id] = {'online': _saved_locked().get(device_id, False), 'misses': 0,
                                  'checking': False, 'ts': 0.0, 'due': 0.0, 'parts': []}
    return b


def _persist_locked():
    try:
        _store().save('online', dict(_saved_locked(), **{k: b['online'] for k, b in _belief.items()}))
    except Exception as e:
        logger.warning(f"[DEVICES] health: last known states not saved: {e}")


def _view_locked(b):
    return {'online': b['online'], 'checking': b['checking'], 'ts': b['ts'],
            'misses': b['misses'], 'parts': list(b['parts'])}


# --- what the engine, the page and her tools read ----------------------------

def view(device_id):
    """The belief about one device. Never None, never blocks."""
    with _lock:
        return _view_locked(_entry_locked(device_id))


def views():
    """{device id: belief} for every enabled device."""
    table = [k for k, v in _engine().rows().items() if v.get('enabled', True)]
    with _lock:
        return {k: _view_locked(_entry_locked(k)) for k in table}


def told(device_id, result):
    """Fold one probe's answer into the belief. Returns the new view."""
    now = time.time()
    with _lock:
        b = _entry_locked(device_id)
        was = b['online']
        if result.get('online'):
            b['online'], b['misses'] = True, 0
        else:
            b['misses'] += 1
            if b['misses'] >= MISSES:
                b['online'] = False
        b['checking'] = False
        b['ts'] = now
        b['parts'] = list(result.get('parts') or [])
        b['due'] = now + (POLL if b['online'] else POLL_DOWN)
        flipped = was != b['online']
        if flipped:
            _persist_locked()
        out = _view_locked(b)
    if flipped:
        logger.info(f"[DEVICES] {device_id}: {'online' if out['online'] else 'offline'}")
        _engine().retell()                   # the tool descriptions say who is online
    return out


def check(row):
    """Ask the device now, on the caller's thread, and believe the answer.
    The Test button, device_status, and a save all come through here."""
    with _lock:
        _entry_locked(row['id'])['checking'] = True
    try:
        result = _engine()._probe(row)
    except Exception as e:                       # _probe never raises; belt and braces
        logger.error(f"[DEVICES] {row['id']}: probe failed: {e}", exc_info=True)
        result = {'online': False, 'parts': []}
    return told(row['id'], result)


def seen(device_id):
    """The device itself just talked to Sapphire with its own key: it is
    online, whatever the last probe said. One that was believed offline is
    looked at soon, so its readings arrive. Never raises."""
    try:
        with _lock:
            b = _entry_locked(device_id)
            was = b['online']
            b['online'], b['misses'], b['ts'] = True, 0, time.time()
            if not was:
                b['due'] = 0.0
                _persist_locked()
        if not was:
            logger.info(f"[DEVICES] {device_id}: online (it checked in)")
            _wake.set()
            _engine().retell()
    except Exception as e:
        logger.warning(f"[DEVICES] health: seen({device_id}) failed: {e}")


def poke(device_id=None):
    """Look at this device (or all of them) at the next chance. Never waits."""
    with _lock:
        for k in ([device_id] if device_id else list(_belief)):
            _entry_locked(k)['due'] = 0.0
    _wake.set()


def forget(device_id):
    """The device is gone, or its name is being reused: no belief carries over."""
    with _lock:
        if _belief.pop(device_id, None) is not None or device_id in _saved_locked():
            _saved_locked().pop(device_id, None)
            _persist_locked()


def rename(old, new):
    """A renamed device keeps what was believed about it."""
    with _lock:
        b = _belief.pop(old, None) or {'online': _saved_locked().pop(old, False), 'misses': 0,
                                       'checking': False, 'ts': 0.0, 'due': 0.0, 'parts': []}
        b['due'] = 0.0
        _belief[new] = b
        _persist_locked()
    _wake.set()


# --- the keeper --------------------------------------------------------------

def start():
    """Start believing. Safe to call twice. False on an install without devices."""
    global _keeper
    if _engine().refusal():
        return False
    with _lock:
        if _keeper and _keeper.is_alive():
            return True
        _halt.clear()
        _keeper = threading.Thread(target=_keep, daemon=True, name='device-health')
        _keeper.start()
    return True


def stop():
    _halt.set()
    _wake.set()


def _keep():
    _halt.wait(SETTLE)
    while not _halt.is_set():
        _wake.clear()
        try:
            soon = _look()
        except Exception as e:
            logger.error(f"[DEVICES] health keeper: {e}", exc_info=True)
            soon = time.time() + POLL
        _wake.wait(timeout=max(0.1, min(POLL, soon - time.time())))


def _look():
    """Hand every enabled device that is due to the pool. Returns when the
    next one is due."""
    e = _engine()
    now = time.time()
    soon = now + POLL
    table = {k: v for k, v in e.rows().items() if v.get('enabled', True)}
    with _lock:
        for k, row in table.items():
            b = _entry_locked(k)
            if b['checking']:
                continue
            if b['due'] <= now:
                b['checking'] = True
                e._pool.submit(_run, row)
            else:
                soon = min(soon, b['due'])
    return soon


def _run(row):
    try:
        told(row['id'], _engine()._probe(row))
    except Exception as e:
        logger.error(f"[DEVICES] {row['id']}: probe failed: {e}", exc_info=True)
        with _lock:
            if row['id'] in _belief:
                _belief[row['id']]['checking'] = False
