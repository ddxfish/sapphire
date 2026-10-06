# core/devices/storage.py - backups onto devices (tmp/device-backup-plan.md)
#
# A device that has `storage` (a satellite with a card, this computer with a
# folder on a stick or a NAS mount, later a host over SCP) is a backup
# target: core/backup_targets ships tonight's newest tier to every one of
# them after the 3am run, seals it first, rotates by the device's keep
# counts and drops the opener files beside the archives.
#
# This module is the bridge: it asks the engine for every part with
# `storage` whose driver offers `storage_target(device, config, secrets)`
# (the shape speaker() has for sound) and hands the Target objects to
# backup_targets. Drivers own the transport; core owns the gate.
import logging
import threading
import time

from core import backup_targets

logger = logging.getLogger(__name__)

KEEP_FIELDS = (
    {'key': 'keep_daily', 'type': 'number', 'label': 'Daily backups to keep', 'default': 7,
     'min': 0, 'max': 60, 'capability': 'storage', 'tab': 'Backup',
     'help': 'How many of each are kept there before the oldest goes. 0 = keep none new, delete none.'},
    {'key': 'keep_weekly', 'type': 'number', 'label': 'Weekly backups to keep', 'default': 4,
     'min': 0, 'max': 60, 'capability': 'storage', 'tab': 'Backup'},
    {'key': 'keep_monthly', 'type': 'number', 'label': 'Monthly backups to keep', 'default': 3,
     'min': 0, 'max': 60, 'capability': 'storage', 'tab': 'Backup'},
)


def keep_from(config):
    """The keep counts a device's page settings ask for (manual stays small)."""
    def n(key, default):
        try:
            return max(0, int(config.get(key, default)))
        except (TypeError, ValueError):
            return default
    return {'daily': n('keep_daily', 7), 'weekly': n('keep_weekly', 4),
            'monthly': n('keep_monthly', 3), 'manual': 3}


def targets(online=True):
    """Every device that can hold backups right now, as backup_targets Targets."""
    from core.devices import engine
    out = []
    for mod, brief, config, secrets in engine.doors('storage', 'storage_target', online=online):
        try:
            t = mod.storage_target(brief, config, secrets)
        except Exception as e:
            logger.warning(f"[DEVICES] {brief['id']}: storage target not built: {e}")
            continue
        if t is not None:
            out.append(t)
    return out


def target_for(device_id):
    """The one device's Target (for its own 'backup' action), or None."""
    from core.devices import engine
    for mod, brief, config, secrets in engine.doors('storage', 'storage_target', online=False):
        if brief['id'] == device_id:
            return mod.storage_target(brief, config, secrets)
    return None


_inflight = {}        # target label -> started (time)
_last = {}            # target label -> {'ts', 'ok', 'msg'}
_lock = threading.Lock()


def backup_now(target):
    """The owner's (or her, when unlocked) 'backup' action on one device: a
    fresh manual backup, sealed, shipped, rotated there — on its own thread.
    Answers at once: a 70 MB copy takes a minute or two over WiFi, longer
    than any button or tool call should wait (2026-10-06). `list` and the
    log carry the outcome."""
    from core.backup import BackupRefused
    with _lock:
        if target.label in _inflight:
            since = int(time.time() - _inflight[target.label])
            return f"A backup to {target.label} is already on its way ({since}s in). Ask 'list' to see it land.", True
        _inflight[target.label] = time.time()

    def _go():
        try:
            fn, results = backup_targets.ship_now(to=[target])
            r = results[0] if results else {'ok': False, 'msg': 'nothing was shipped'}
        except BackupRefused as e:
            r = {'ok': False, 'msg': str(e)}
        except Exception as e:
            logger.error(f"[DEVICES] backup to {target.label} failed: {e}", exc_info=True)
            r = {'ok': False, 'msg': f"{type(e).__name__}: {e}"}
        with _lock:
            _inflight.pop(target.label, None)
            _last[target.label] = {'ts': time.time(), 'ok': bool(r.get('ok')), 'msg': str(r.get('msg'))}

    threading.Thread(target=_go, daemon=True, name=f"backup-{target.label}").start()
    return (f"Backing up to {target.label} now: a fresh backup, sealed, sent. About two minutes; "
            f"the device may not answer meanwhile. Ask 'list' to see it land."), True


def last_result(target):
    """'last backup: ok, 3 min ago — ...' or '' for listing_text."""
    with _lock:
        r = _last.get(target.label)
        going = _inflight.get(target.label)
    if going:
        return f"a backup is on its way ({int(time.time() - going)}s in)"
    if not r:
        return ''
    ago = int(time.time() - r['ts'])
    when = f"{ago}s ago" if ago < 120 else f"{ago // 60} min ago"
    return f"last backup {'ok' if r['ok'] else 'FAILED'}, {when}: {r['msg']}"


def listing_text(target, info=None):
    """'kept 3 · newest ... · free 12 GB of 14 GB' for her `list` action and
    the device's readings. `info` is the target's own summary dict when it
    has one ({free_bytes, total_bytes, path})."""
    from core.backup import tier_of
    try:
        sizes = target.sizes()
        names = sorted((n for n in (sizes or target.names()) if tier_of(n)), reverse=True)
    except Exception as e:
        return f"Could not list backups on {target.label}: {e}"
    info = info or {}
    parts = [f"{len(names)} backup(s) on {target.label}"]
    if names:
        parts.append('newest ' + names[0])
    if isinstance(info.get('free_bytes'), (int, float)):
        total = info.get('total_bytes')
        parts.append(f"free {_gb(info['free_bytes'])}" + (f" of {_gb(total)}" if total else ''))
    if info.get('path'):
        parts.append(f"at {info['path']}")
    last = last_result(target)
    if last:
        parts.append(last)
    head = ' · '.join(parts)
    rows = [f"{n}  {_gb(sizes[n])}" if n in sizes else n for n in names[:12]]
    return head + ('\n  ' + '\n  '.join(rows) if rows else '') \
        + (f"\n  ... {len(names) - 12} more" if len(names) > 12 else '')


def _gb(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return '?'
    return f"{n / (1024 ** 3):.1f} GB" if n >= 1024 ** 3 else f"{n / (1024 ** 2):.0f} MB"


def register():
    """Boot: hand the device targets to core's shipper. Idempotent."""
    backup_targets.register_provider(targets)
