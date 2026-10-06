# core/backup_targets.py — where backups GO besides the local folder.
#
# A target is anything that can hold a few sealed archives: a satellite's SD
# card, a folder on this machine (USB stick, NAS mount), later an SCP host.
# Core owns the gate and the rotation; providers (core/devices/storage.py)
# hand over Target objects. Remembrance is NOT a target — its vault rotates
# server-side on its own cron — it just calls backup_manager.export_encrypted.
#
# The never-plaintext-off-box gates, in order:
#   1. backup_crypto.seal — encrypt + verify as one step (no unverified blob)
#   2. Target.check before every put — a `.sapphirebak` must carry the magic;
#      a `.tar.gz` is accepted only by a non-remote target with encryption off
#   3. remote drivers run Target.check again inside their own put
#   4. the Pi / ESP32 doors 400 on a backup that lacks the magic
#   5. tests/test_backup_targets.py pins 1-3 with a fake target
#
# 2026-10-06.
import logging
import tempfile
import threading
import time
from pathlib import Path

from core import backup_crypto
from core import backup_openers
from core.backup import backup_manager, select_doomed, tier_of, BackupRefused, TIER_RANK

logger = logging.getLogger(__name__)

SHIP_WAIT = 30 * 60     # a 70 MB blob at 0.5 MB/s is ~2.5 min; cards vary
KEEP_DEFAULT = {"daily": 7, "weekly": 4, "monthly": 3, "manual": 3}


class Target:
    """One place backups go. Subclasses set `kind` and `remote` (a CLASS
    constant: a remote target is encrypted no matter what any setting says)
    and implement put / names / delete (get is for restore, optional)."""
    kind = "target"
    remote = True
    label = ""
    encrypt = True              # honoured only when remote is False
    keep = KEEP_DEFAULT

    @property
    def wants_encryption(self) -> bool:
        return True if self.remote else bool(self.encrypt)

    def check(self, path, name: str):
        """GATE 2/3. Raises BackupRefused unless `name` is an opener file, a
        `.sapphirebak` that really carries the magic, or (non-remote target
        with encryption off) a `.tar.gz`."""
        if name in backup_openers.NAMES:
            return
        if name.endswith(".sapphirebak"):
            if not backup_crypto.is_encrypted_backup(path):
                raise BackupRefused(f"{name} is not ciphertext — refusing to send it to {self.label}")
            return
        if name.endswith(".tar.gz") and not self.wants_encryption:
            return
        raise BackupRefused(f"refusing to send {name} to {self.label}: "
                            f"plaintext never leaves for a {self.kind}")

    def put(self, path: Path, name: str):
        raise NotImplementedError

    def names(self):
        raise NotImplementedError

    def delete(self, name: str):
        raise NotImplementedError

    def get(self, name: str, dst: Path):
        raise NotImplementedError

    def put_openers(self):
        """Drop README + open-backup.sh/.bat + decrypt_backup.py beside the
        archives so a card or stick restores on its own. Default: write them
        to a temp dir and put() each; drivers may override."""
        tmp = Path(tempfile.mkdtemp(prefix="openers_"))
        try:
            backup_openers.write_all(tmp, backup_manager.base_dir / "tools" / "decrypt_backup.py")
            have = set(self.names())
            for f in sorted(tmp.iterdir()):
                if f.name in have:
                    continue
                self.put(f, f.name)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Providers: callables returning a list of Target. Registered by the device
# layer at boot; none registered = nothing ships, quietly.
# ---------------------------------------------------------------------------
_providers = []
_lock = threading.Lock()
last_ship = None      # {ts, filename, results} for health


def register_provider(fn):
    with _lock:
        if fn not in _providers:
            _providers.append(fn)


def targets():
    out = []
    with _lock:
        provs = list(_providers)
    for fn in provs:
        try:
            out.extend(fn() or [])
        except Exception as e:
            logger.error(f"Backup target provider {getattr(fn, '__name__', fn)} failed: {e}")
    return out


# ---------------------------------------------------------------------------
# Shipping
# ---------------------------------------------------------------------------

def _ship_one(t: Target, plain: Path, sealed):
    path = sealed if t.wants_encryption else plain
    if path is None:
        return {"target": t.label, "ok": False, "msg": "no sealed copy"}
    name = path.name
    try:
        t.check(path, name)                       # GATE 2
        have = set(t.names())
        if name in have:
            verb = "already there"
        else:
            t.put(path, name)
            verb = f"shipped ({path.stat().st_size // (1024 * 1024)} MB)"
        doomed = select_doomed(list(t.names()), t.keep)
        for d in doomed:
            try:
                t.delete(d)
            except Exception as e:
                logger.warning(f"[BACKUP->{t.label}] could not drop {d}: {e}")
        try:
            t.put_openers()
        except Exception as e:
            logger.warning(f"[BACKUP->{t.label}] openers not written: {e}")
        kept = sum(1 for n in t.names() if tier_of(n))
        msg = f"{name} {verb}; kept {kept}, dropped {len(doomed)}"
        logger.info(f"[BACKUP->{t.label}] {msg}")
        return {"target": t.label, "ok": True, "msg": msg}
    except Exception as e:
        logger.error(f"[BACKUP->{t.label}] FAILED: {e}")
        return {"target": t.label, "ok": False, "msg": str(e)}


def ship(filename: str, to=None):
    """Ship ONE existing local backup to every target (or `to`): seal once if
    any target wants encryption (GATE 1), one thread per target, rotation by
    names after. Returns [{target, ok, msg}]. Raises BackupRefused when the
    local file is missing or sealing is impossible (no password)."""
    global last_ship
    tl = list(to) if to is not None else targets()
    if not tl:
        return []
    plain = backup_manager.get_backup_path(filename)
    if not plain:
        raise BackupRefused(f"Backup not found: {filename}")
    sealed, cleanup = None, (lambda: None)
    if any(t.wants_encryption for t in tl):
        sealed, cleanup = backup_manager.sealed(filename)
    results, rlock = [], threading.Lock()

    def _run(t):
        r = _ship_one(t, plain, sealed)
        with rlock:
            results.append(r)

    threads = [threading.Thread(target=_run, args=(t,), daemon=True,
                                name=f"ship-{t.kind}") for t in tl]
    for th in threads:
        th.start()
    deadline = time.monotonic() + SHIP_WAIT
    for th in threads:
        th.join(max(0.0, deadline - time.monotonic()))
    if any(th.is_alive() for th in threads):
        logger.warning(f"Backup ship still running after {SHIP_WAIT}s; temp blob left for the sweep")
    else:
        cleanup()
    last_ship = {"ts": time.time(), "filename": filename, "results": list(results)}
    return results


def pick_newest(files):
    """Of one scheduled run's files, the one to ship: highest tier wins
    (monthly > weekly > daily), newest name breaks ties."""
    files = [f for f in (files or []) if f]
    if not files:
        return None
    return max(files, key=lambda f: (TIER_RANK.get(tier_of(f) or "", 0), f))


def ship_async(files):
    """Scheduler entry: ship the run's newest tier on a thread, never blocking
    the backup loop. Skips the seal entirely when no target exists."""
    fn = pick_newest(files)
    if not fn or not targets():
        return None

    def _go():
        try:
            ship(fn)
        except BackupRefused as e:
            logger.error(f"Backup targets: {e}")
            backup_manager._alert('backup_ship_refused', reason=str(e))
        except Exception as e:
            logger.error(f"Backup targets: ship failed: {e}", exc_info=True)

    th = threading.Thread(target=_go, daemon=True, name="backup-ship")
    th.start()
    return th


def ship_now(to=None):
    """Owner's 'back up to this device now': a fresh manual backup (under the
    backup lock, with rotation), then ship it. Returns (filename, results).
    Raises BackupRefused."""
    if backup_manager._active_corruption_sentinels():
        raise BackupRefused("Corruption sentinel active — backups halted. See Settings > Backup.")
    with backup_manager._backup_op_lock:
        fn = backup_manager.create_backup("manual")
        if fn:
            backup_manager.rotate_backups()
    if not fn:
        raise BackupRefused(backup_manager.last_backup_error or "Backup creation failed")
    return fn, ship(fn, to=to)
