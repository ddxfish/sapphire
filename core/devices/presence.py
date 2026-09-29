# core/devices/presence.py - things that come and go (tmp/device-manager-plan.md, section 14)
#
# A keyboard is plugged in, a board is unplugged, a Bluetooth thing walks out
# of range. A driver that declares "presence": true is kept told, so its heavy
# part (a synth, a connection) runs only while it has something to run for.
# The driver's side is three optional functions:
#
#   discover(config)                                   what it can see right now
#   watch(changed, stopped)                            call changed() when that changes
#   tend(device, config, secrets, present, leaving)    make the device match
#
# tend is LEVEL triggered: it is handed the whole truth every time, never
# "this one arrived". A missed event is repaired by the next heartbeat, and so
# is a heavy part that fell over. tend must be safe to repeat.
#
# ONE keeper thread. It wakes on a poke or on the heartbeat, keeps one watcher
# per driver that has a device, and hands each device to a small pool. It
# holds no retry policy, and nothing about a device beyond the last thing its
# driver was told. Keep it that way.
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

logger = logging.getLogger(__name__)

HEARTBEAT = 15             # seconds between looks when nothing pokes
SETTLE = 5                 # seconds after start before the first look
GATHER = 0.3               # a burst of pokes is one look
LAST_WORD = 8              # seconds drivers get to let go when they are leaving
SLOW = 30                  # a tend that has not come back by now is logged
WATCH_RETRY = (5, 15, 60)  # seconds before a watcher that ended by itself starts again
EVERY = '*'

_lock = threading.Lock()
_wake = threading.Event()
_halt = threading.Event()
_keeper = None
_pending = set()           # driver ids to look at next; EVERY = all of them
_watchers = {}             # driver id -> {'thread', 'stopped', 'plugin', 'idle'}
_told = {}                 # (device id, driver id) -> the last thing tend was told
_busy = {}                 # (device id, driver id) -> when its tend began
_again = set()             # asked for again while busy
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='device-tend')


def _engine():
    from core.devices import engine
    return engine


def poke(driver_id=None):
    """Something changed: look again. Never waits, never raises."""
    with _lock:
        _pending.add(str(driver_id) if driver_id else EVERY)
    _wake.set()


def start():
    """Start keeping. Safe to call twice. False on an install without devices."""
    global _keeper
    if _engine().refusal():
        return False
    with _lock:
        if _keeper and _keeper.is_alive():
            return True
        _halt.clear()
        _keeper = threading.Thread(target=_keep, daemon=True, name='device-presence')
        _keeper.start()
    return True


def here():
    """{device id: names of what is here}, as the drivers were last told."""
    with _lock:
        out = {}
        for (device_id, _), last in _told.items():
            out.setdefault(device_id, []).extend(t['name'] for t in last['present'])
        return out


# --- the keeper ------------------------------------------------------------------

def _keep():
    _halt.wait(SETTLE)
    poked = False
    while not _halt.is_set():
        if poked:
            _halt.wait(GATHER)
        _wake.clear()
        with _lock:
            which = set(_pending) if poked else {EVERY}
            _pending.clear()
        try:
            _look(which or {EVERY})
        except Exception as e:
            logger.error(f"[DEVICES] presence: a look failed: {e}", exc_info=True)
        poked = _wake.wait(HEARTBEAT)


def _wanted():
    """{(device id, driver id): (row, part)}: every part of an enabled device
    whose driver keeps presence and is loaded."""
    e = _engine()
    reg, out = e._registry(), {}
    for device_id, row in e.rows().items():
        for part in row.get('parts', []) if row.get('enabled', True) else []:
            spec = reg.get_driver(part.get('driver'))
            if spec and spec.get('presence'):
                out[(device_id, part['driver'])] = (row, part)
    return out


def _look(which):
    wanted = _wanted()
    _watch({driver for _, driver in wanted})
    now = time.monotonic()
    with _lock:
        gone = [k for k in _told if k not in wanted]
        slow = [k for k, began in _busy.items() if 0 <= now - began - SLOW < HEARTBEAT]
    for device_id, driver_id in slow:
        logger.warning(f"[DEVICES] {device_id}: the {driver_id} driver has been tending for "
                       f"over {SLOW}s. Nothing else of this device is tended until it returns.")
    for key in gone:
        _hand(key, leaving=True)
    for key, (row, part) in wanted.items():
        if EVERY in which or key[1] in which:
            _hand(key, row, part)


def _hand(key, row=None, part=None, leaving=False):
    """Give one device to the pool. One that is still being tended is asked
    for again when it is done, never twice at once. Returns the job, or None."""
    with _lock:
        if key in _busy:
            _again.add(key)
            return None
        _busy[key] = time.monotonic()
    try:
        return _pool.submit(_tend, key, row, part, leaving)
    except RuntimeError:                         # the pool is shut: the app is going down
        with _lock:
            _busy.pop(key, None)
        return None


def _tend(key, row, part, leaving):
    """One device and one driver, told the whole truth. Never raises."""
    device_id, driver_id = key
    e = _engine()
    with _lock:
        last = _told.get(key)
    try:
        if leaving:
            # The code that was told last is the code that lets go: its plugin
            # may be half unloaded by now, and no lookup would find it.
            if not last:
                return
            try:
                secrets = e._part_secrets(device_id, driver_id)
            except Exception:
                secrets = e._secrets().Secrets(device_id, {})
            last['tend'](last['device'], last['config'], secrets, [], True)
            return
        device, config = e._brief(row), dict(part.get('config') or {})
        mod, spec = e._driver(driver_id, part.get('plugin', ''))
        tend = getattr(mod, 'tend', None)
        if not callable(tend):
            return
        present = e.passing(spec, config, e.found(driver_id, config))
        tend(device, config, e._part_secrets(device_id, driver_id), present, False)
        names = [t['name'] for t in present]
        if last is None or [t['id'] for t in last['present']] != [t['id'] for t in present]:
            logger.info(f"[DEVICES] {device_id}: connected: {', '.join(names) or 'nothing'}")
        with _lock:
            _told[key] = {'device': device, 'config': config, 'plugin': spec['plugin_name'],
                          'present': present, 'tend': tend}
    except e.DeviceError as err:
        logger.warning(f"[DEVICES] {device_id}: the {driver_id} driver was not tended: {err}")
    except Exception as err:
        logger.error(f"[DEVICES] {device_id}: {driver_id}.tend failed: {err}", exc_info=True)
    finally:
        with _lock:
            if leaving:
                _told.pop(key, None)             # said once, whatever came of it
            _busy.pop(key, None)
            more = key in _again
            _again.discard(key)
        if more and not leaving:
            poke(driver_id)


# --- the watchers ----------------------------------------------------------------

def _watch(drivers):
    """One watcher for every driver that has a device, none for the rest."""
    reg = _engine()._registry()
    with _lock:
        extra = [d for d in _watchers if d not in drivers]
        new = [d for d in drivers if d not in _watchers
               or not (_watchers[d]['idle'] or _watchers[d]['thread'].is_alive())]
    for driver_id in extra:
        _unwatch(driver_id)
    for driver_id in new:
        stopped = threading.Event()
        thread = threading.Thread(target=_watching, args=(driver_id, stopped), daemon=True,
                                  name=f'device-watch-{driver_id}')
        with _lock:
            _watchers[driver_id] = {'thread': thread, 'stopped': stopped, 'idle': False,
                                    'plugin': (reg.get_driver(driver_id) or {}).get('plugin_name', '')}
        thread.start()


def _watching(driver_id, stopped):
    """Run the driver's watch() until it is told to stop. One that ends by
    itself is started again, each time a little later."""
    tries = 0
    while not (stopped.is_set() or _halt.is_set()):
        began = time.monotonic()
        try:
            mod, _ = _engine()._driver(driver_id)
            watch = getattr(mod, 'watch', None)
            if not callable(watch):              # the heartbeat is all this driver gets
                with _lock:
                    if driver_id in _watchers and _watchers[driver_id]['stopped'] is stopped:
                        _watchers[driver_id]['idle'] = True
                return
            watch(lambda: poke(driver_id), stopped)
        except Exception as e:
            logger.warning(f"[DEVICES] the watcher of '{driver_id}' failed: {e}")
        if stopped.is_set() or _halt.is_set():
            return
        if time.monotonic() - began > WATCH_RETRY[-1]:
            tries = 0
        pause = WATCH_RETRY[min(tries, len(WATCH_RETRY) - 1)]
        tries += 1
        # Said after the pause, not before: when the app goes down the system
        # ends the watcher's own program first, and that is no fault.
        if stopped.wait(pause) or _halt.is_set():
            return
        logger.warning(f"[DEVICES] the watcher of '{driver_id}' ended by itself {pause}s ago. "
                       f"Starting it again. The heartbeat covered meanwhile.")


def _unwatch(driver_id):
    with _lock:
        w = _watchers.pop(driver_id, None)
    if w:
        w['stopped'].set()
        if w['thread'].is_alive():
            w['thread'].join(timeout=3)


# --- leaving ---------------------------------------------------------------------

def _let_go(keys, drivers):
    """Tend these one last time, leaving, then end these watchers. Waits a
    short while for the drivers, never long."""
    with _lock:
        for driver_id in drivers:
            if driver_id in _watchers:
                _watchers[driver_id]['stopped'].set()
    end = time.monotonic() + LAST_WORD
    jobs, waiting = [], list(keys)
    while waiting and time.monotonic() < end:
        for key in list(waiting):
            with _lock:
                busy = key in _busy
            if not busy:
                waiting.remove(key)
                job = _hand(key, leaving=True)
                if job:
                    jobs.append(job)
        if waiting:
            time.sleep(0.05)
    if jobs:
        wait(jobs, timeout=max(0.1, end - time.monotonic()))
    with _lock:
        for key in keys:
            if _told.pop(key, None) is not None:
                logger.warning(f"[DEVICES] {key[0]}: the {key[1]} driver did not let go in {LAST_WORD}s")
    for driver_id in drivers:
        _unwatch(driver_id)


def release(plugin_name):
    """A plugin is about to unload. The loader calls this BEFORE it takes the
    plugin's drivers away, while they can still let go. Never raises."""
    try:
        with _lock:
            keys = [k for k, v in _told.items() if v['plugin'] == plugin_name]
            drivers = {d for d, w in _watchers.items() if w['plugin'] == plugin_name}
        if keys or drivers:
            _let_go(keys, drivers | {k[1] for k in keys})
    except Exception as e:
        logger.warning(f"[DEVICES] presence could not release '{plugin_name}': {e}")


def stop():
    """Sapphire is stopping: every driver lets go. Never raises."""
    global _keeper
    _halt.set()
    _wake.set()
    try:
        with _lock:
            keys, drivers = list(_told), set(_watchers)
        _let_go(keys, drivers)
        keeper, _keeper = _keeper, None
        if keeper and keeper.is_alive():
            keeper.join(timeout=2)
    except Exception as e:
        logger.warning(f"[DEVICES] presence did not stop cleanly: {e}")
