import os
import fnmatch
import shutil
import tarfile
import tempfile
import time
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path
import config
from core import backup_crypto
from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

TIER_RANK = {"monthly": 3, "weekly": 2, "daily": 1}


class BackupRefused(Exception):
    """A backup was refused for a reason the user should read (no password,
    corruption sentinel, over the cap, nothing to back up)."""


def tier_of(name: str):
    """Tier encoded in a backup filename (`sapphire_<date>_<time>_<tier>.*`),
    or None. `pre_update` parses to `update`, matching list_backups."""
    parts = str(name).split('.')[0].split('_')
    return parts[-1] if len(parts) >= 4 and parts[0] == 'sapphire' else None


def select_doomed(names, keep):
    """Pure tier rotation, shared by the local folder and every remote target
    (2026-10-06): group `names` by tier, newest-first by name, return the
    names past that tier's keep. keep <= 0 pauses the tier (nothing of it is
    ever purged); unknown tiers and unparsable names are left alone."""
    by_tier = {}
    for n in names:
        tier = tier_of(n)
        if tier:
            by_tier.setdefault(tier, []).append(str(n))
    doomed = []
    for tier, lst in by_tier.items():
        limit = int(keep.get(tier, 0) or 0)
        if limit <= 0:
            continue
        lst.sort(reverse=True)
        doomed.extend(lst[limit:])
    return doomed


# Privacy floor — ALWAYS excluded from backups, regardless of user settings:
#  - `.bad-<ts>` quarantined corrupted-state files (may hold stale OAuth/session
#    strings) — `*.bad-<timestamp>` from PluginState._load.
#  - `.tmp` / `.tmp.<pid>` in-flight atomic-rename files (possibly-truncated JSON).
#  - `*_mcp_key.json` (inbound MCP bearer keys) + `mcp_client.json` (outbound
#    bearer tokens) — plaintext live credentials; backups land on RAID + offsite,
#    and these are re-issuable in the plugin/MCP admin UI. Witch-hunt findings
#    C5 (2026-04-21) + MCP C1 (2026-05-07).
def _privacy_excluded(rel: str) -> bool:
    if '.bad-' in rel:
        return True
    if rel.endswith('.tmp') or '.tmp.' in rel:
        return True
    if rel.endswith('_mcp_key.json') or rel.endswith('mcp_client.json'):
        return True
    # sapphire-health.token — bearer API token minted for the health-check
    # script (user/sapphire-health.sh). Live credential; same class as the
    # MCP keys above, and local backups are plaintext by design.
    if rel.endswith('sapphire-health.token'):
        return True
    return False


# Cache floor — ALWAYS excluded, all platforms. `user/models/` is rebuildable-
# cache territory BY CONTRACT (Krem's ruling 2026-07-18): everything under it
# is re-downloaded on first use (HF models via the Windows HF_HOME redirect,
# dtln, silero VAD, geonames). Anything precious must live elsewhere in user/.
# Without this, Windows installs back up 100s of MB of model blobs.
# `user/game-room/library/` is the same class: game files the player uploaded
# from copies they own (a WAD, a ROM), 10s of MB each and the same bytes in
# every archive. Saves are NOT here: they ride the chat database.
_CACHE_FLOOR = ('models', 'game-room/library')


def _cache_excluded(rel: str) -> bool:
    return any(rel == d or rel.startswith(d + '/') for d in _CACHE_FLOOR)


def _exclude_patterns_setting():
    """User-defined exclude globs from settings (tolerates list OR newline string)."""
    raw = getattr(config, 'BACKUPS_EXCLUDE_PATTERNS', None) or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    return [str(p).strip() for p in raw if str(p).strip()]


def _is_excluded(rel: str, user_patterns=None) -> bool:
    """True if a `user/`-relative path should be excluded — the privacy floor
    (always) plus user-defined fnmatch globs (`*` crosses `/`, e.g. `rag/*`,
    `*.log`). Shared by the tar filter and the size estimator so the preview
    matches the real backup exactly."""
    if _privacy_excluded(rel):
        return True
    if _cache_excluded(rel):
        return True
    for pat in (user_patterns or []):
        if not pat:
            continue
        p = pat.rstrip('/')
        # Bare name → exclude the whole subtree (`piper-voices` skips
        # `piper-voices/...`); plus normal fnmatch globs (`rag/*`, `*.log`).
        if rel == p or rel.startswith(p + '/') or fnmatch.fnmatch(rel, pat):
            return True
    return False


def _backup_filter(tarinfo):
    name = tarinfo.name
    rel = name[len("user/"):] if name.startswith("user/") else name
    # Skip symlinks/hardlinks: tar stores the target PATH (a private-location leak
    # once backups go offsite) and they don't restore cleanly cross-platform
    # (Windows needs admin to recreate a symlink). They're config, not data.
    if tarinfo.issym() or tarinfo.islnk():
        return None
    if _is_excluded(rel, _exclude_patterns_setting()):
        return None
    return tarinfo


class Backup:
    """Backup manager for the user/ directory."""

    def __init__(self):
        self._stop_event = None
        self.base_dir = Path(getattr(config, 'BASE_DIR', Path(__file__).parent.parent))
        self.user_dir = self.base_dir / "user"
        self._backup_dir_override = None
        self.backup_dir_error = None
        self.backup_dir.mkdir(exist_ok=True)
        # Serializes create + rotate as a single critical section. Without
        # this, a manual backup triggered during the scheduled 3am run can
        # race with rotation: both paths sort-by-mtime and delete oldest-
        # past-limit, and an in-flight partial can be counted or a valid
        # older backup deleted to make room for a still-writing new one.
        # Witch-hunt 2026-04-21 finding R5.
        self._backup_op_lock = threading.Lock()
        logger.info(f"Backup initialized - base_dir: {self.base_dir}, backup_dir: {self.backup_dir}")

    @property
    def backup_dir(self):
        """Where local backups live (2026-10-06): `BACKUPS_DIR`, read live.
        Empty = `user_backups/` beside user/; relative = under base_dir;
        `~` expands. A dir inside user/ (backups backing up backups, forever)
        or one that can't be made/written falls back to the default with the
        reason in `backup_dir_error` (health + the Backup page)."""
        override = getattr(self, '_backup_dir_override', None)
        if override is not None:
            return override
        default = self.base_dir / "user_backups"
        raw = str(getattr(config, 'BACKUPS_DIR', '') or '').strip()
        chosen, err = default, None
        if raw:
            p = Path(os.path.expanduser(raw))
            if not p.is_absolute():
                p = self.base_dir / p
            try:
                inside_user = p.resolve().is_relative_to(self.user_dir.resolve())
            except OSError:
                inside_user = False
            if inside_user:
                err = f"BACKUPS_DIR {raw!r} is inside user/ — using the default folder"
            else:
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    if not os.access(p, os.W_OK):
                        raise PermissionError("not writable")
                    chosen = p
                except OSError as e:
                    err = f"BACKUPS_DIR {raw!r} unusable ({e}) — using the default folder"
        if err and err != getattr(self, 'backup_dir_error', None):
            logger.error(err)
        self.backup_dir_error = err
        try:
            chosen.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Backup folder {chosen} cannot be created: {e}")
        return chosen

    @backup_dir.setter
    def backup_dir(self, value):
        self._backup_dir_override = Path(value) if value is not None else None

    def _backup_password(self) -> str:
        """The one backup password (Settings > Backup; stored machine-bound in
        ~/.config/sapphire, never inside user/). '' when unset/unreadable."""
        try:
            from core.credentials_manager import credentials
            return credentials.get_backup_password() or ''
        except Exception as e:
            logger.error(f"Backup password unreadable: {e}")
            return ''

    def _alert(self, kind, **data):
        try:
            from core.event_bus import publish
            publish('sapphire_health_alert', {'type': kind, **data})
        except Exception as e:
            logger.debug(f"sapphire_health_alert publish failed: {e}")

    def run_scheduled(self):
        """Run scheduled backup check - called daily at 3am."""
        if not getattr(config, 'BACKUPS_ENABLED', True):
            logger.info("Backups disabled, skipping scheduled run")
            return "Backups disabled"

        # Sapphire Health gate — if any corruption sentinel is active, HALT
        # the backup cycle entirely. Creating new backups of a corrupt DB
        # and rotating out good ones is exactly the waterfall class this
        # exists to prevent. User clears the sentinel file(s) after fixing.
        # Witch-hunt 2026-04-21 finding R1.
        sentinels = self._active_corruption_sentinels()
        if sentinels:
            logger.critical(
                f"Sapphire Health: {len(sentinels)} corruption sentinel(s) active "
                f"— SKIPPING backup create + rotate to preserve last-known-good. "
                f"Clear {self.base_dir/'user'/'health'}/CORRUPT_*.flag after fixing."
            )
            return f"HALTED: {len(sentinels)} corruption sentinel(s) active"

        # Serialize the create + rotate sequence so a concurrent manual
        # backup can't interleave and cause rotation to delete a
        # still-writing file or count partials. R5 2026-04-21.
        with self._backup_op_lock:
            now = datetime.now()
            ok_tiers, failed_tiers, made = [], [], []

            def _tier(name):
                fn = self.create_backup(name)
                (ok_tiers if fn else failed_tiers).append(name)
                if fn:
                    made.append(fn)

            if getattr(config, 'BACKUPS_KEEP_DAILY', 7) > 0:
                _tier("daily")

            if now.weekday() == 6 and getattr(config, 'BACKUPS_KEEP_WEEKLY', 4) > 0:
                _tier("weekly")

            if now.day == 1 and getattr(config, 'BACKUPS_KEEP_MONTHLY', 3) > 0:
                _tier("monthly")

            # Rotate INSIDE the lock — otherwise a manual trigger between
            # create and rotate can race.
            self.rotate_backups()
            # What this run produced — the scheduler ships the newest tier of
            # it to the backup targets (devices, paths) after the lock drops.
            self.last_scheduled_files = made
        # Honest report (X1 F8, negspace 2026-08-31): the old path appended
        # every tier unconditionally and logged "complete" right after
        # create_backup's own ERROR — success printed over failure.
        if failed_tiers:
            msg = f"Scheduled backup FAILED for: {', '.join(failed_tiers)}"
            if ok_tiers:
                msg += f" (succeeded: {', '.join(ok_tiers)})"
            logger.error(msg)
            self.last_scheduled_result = msg
            return msg
        msg = (f"Scheduled backup complete: {', '.join(ok_tiers)}"
               if ok_tiers else "Scheduled backup: nothing due")
        self.last_scheduled_result = msg
        return msg

    def create_backup(self, backup_type="manual", extra_patterns=None, dest_dir=None,
                      require_complete=False, encrypt=None):
        """Create a .tar.gz backup of the user/ directory (or a sealed
        .sapphirebak of it when encrypting).

        Writes to `<filename>.partial` first, atomic-renames to final name on
        success. Without this, a disk-full / kill-mid-write leaves a truncated
        `.tar.gz` that `list_backups` parses as legitimate, and rotation may
        delete older valid backups in favor of the partial. Witch-hunt
        2026-04-21 finding H13.

        Encryption (2026-10-06): `encrypt=None` follows `BACKUPS_ENCRYPT_LOCAL`
        (off by default — local backups sit beside the live data, so at-rest
        encryption protects little and a lost password costs everything;
        H13 2026-04-21). True = seal the tar (encrypt + verify, GATE 1) and
        unlink the plaintext; the return is the `.sapphirebak` name. Encrypt
        wanted + no password = NO backup (refused before the walk, health
        alert) — never a plaintext one when encryption was supposed to be on.

        Optional (offsite path; defaults reproduce the local behavior exactly):
          extra_patterns — extra exclude globs merged with the page settings.
          dest_dir       — write the tarball here instead of user_backups/.
          require_complete — refuse instead of shipping a backup that had to
            skip WAL-busy databases. Scheduled backups keep the partial-is-
            better-than-none default; the pre-update backup passes True — an
            "insurance" snapshot missing her chat/knowledge DBs isn't
            insurance (2026-08-06 hunt, H6). Refusal reason lands in
            self.last_backup_error for the caller's message.
        """
        self.last_backup_error = None
        if not self.user_dir.exists():
            logger.error(f"User directory not found: {self.user_dir}")
            return None

        if encrypt is None:
            encrypt = bool(getattr(config, 'BACKUPS_ENCRYPT_LOCAL', False))
        password = self._backup_password() if encrypt else ''
        if encrypt and not password:
            self.last_backup_error = ("Encryption is on but no backup password is set "
                                      "— no backup made. Set one in Settings > Backup.")
            logger.error(f"Backup refused: {self.last_backup_error}")
            self._alert('backup_password_missing', backup_type=backup_type)
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"sapphire_{timestamp}_{backup_type}.tar.gz"
        out_dir = Path(dest_dir) if dest_dir else self.backup_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / filename
        partial = out_dir / (filename + ".partial")
        staging_dir = None
        plain_done = None   # the finished plaintext tar, once renamed

        try:
            # Checkpoint SQLite WAL files before backup. DBs whose checkpoint
            # failed (SQLITE_BUSY etc.) get skipped from the archive entirely
            # — better to omit a DB from this backup than capture it in a
            # torn state that won't restore cleanly. The next scheduled
            # backup will retry. Day-ruiner scout 2026-05-07 #K.
            # WAL trim first (best-effort — no longer gates inclusion; the
            # snapshot below is what guarantees consistency now).
            self._checkpoint_databases()
            # N10 (negspace 2026-08-31): NEVER tar a live SQLite file. The old
            # checkpoint-then-tar left a TOCTOU — an auto-checkpoint during the
            # minutes-long tar walk rewrote main.db pages mid-read, and the
            # archive held a silently inconsistent DB discovered only at
            # restore time. sqlite3's backup API produces a consistent
            # point-in-time copy even against live writers; live db/-wal/-shm
            # files are EXCLUDED from the walk and snapshots are added at the
            # same arcnames, so restore stays transparent.
            staging_dir = out_dir / f".dbsnap-{timestamp}"
            staging_dir.mkdir(parents=True, exist_ok=True)
            snapshots, failed_checkpoints = self._snapshot_databases(staging_dir)
            if require_complete and failed_checkpoints:
                names = ', '.join(sorted(p.name for p in failed_checkpoints))
                self.last_backup_error = (
                    f"{len(failed_checkpoints)} database(s) busy mid-write: {names}")
                logger.error(f"Backup refused (require_complete): {self.last_backup_error}")
                shutil.rmtree(staging_dir, ignore_errors=True)
                return None
            # Exclusions = page patterns + any caller extras (offsite-only excludes).
            merged_patterns = _exclude_patterns_setting() + (extra_patterns or [])
            def _patterns_filter(tarinfo):
                name = tarinfo.name
                rel = name[len("user/"):] if name.startswith("user/") else name
                if tarinfo.issym() or tarinfo.islnk():
                    return None
                if _is_excluded(rel, merged_patterns):
                    return None
                return tarinfo
            # Exclude EVERY live db + -wal/-shm from the walk: snapshotted
            # DBs ride in from staging below; failed ones are omitted entirely
            # (same day-ruiner #K philosophy: omit beats torn capture).
            _db_family = set()
            for _db in list(snapshots.keys()) + list(failed_checkpoints):
                _db_family.add(_db)
                _db_family.add(Path(str(_db) + "-wal"))
                _db_family.add(Path(str(_db) + "-shm"))
            def _filter_db_files(tarinfo):
                base = _patterns_filter(tarinfo)
                if base is None:
                    return None
                src = (self.user_dir / tarinfo.name[len("user/"):]).resolve() if tarinfo.name.startswith("user/") else None
                if src is not None and src in _db_family:
                    return None
                return tarinfo
            kept_files = [0]
            skipped_files = [0]
            base_filter = _filter_db_files
            def _counting_filter(tarinfo):
                ti = base_filter(tarinfo)
                if ti is not None and ti.isfile():
                    # Open-probe: one unreadable file (Windows exclusive
                    # lock, AV handle) otherwise aborts the ENTIRE backup
                    # from inside tar.add with a generic "Backup failed".
                    # Skip it, keep the other thousands of files.
                    if ti.name.startswith("user/"):
                        src = self.user_dir / ti.name[len("user/"):]
                        try:
                            with open(src, 'rb'):
                                pass
                        except OSError as pe:
                            skipped_files[0] += 1
                            logger.warning(f"Backup skipping unreadable file {ti.name}: {pe}")
                            return None
                    kept_files[0] += 1
                return ti
            user_root = self.user_dir.resolve()
            with tarfile.open(partial, "w:gz") as tar:
                tar.add(self.user_dir, arcname="user", filter=_counting_filter)
                # Snapshots ride in at the live DBs' arcnames (no -wal needed —
                # snapshots are complete, checkpointed copies).
                for _db, _snap in snapshots.items():
                    _rel = _db.relative_to(user_root)
                    # as_posix: the matcher is posix-only and the estimator
                    # (:582) already feeds it forward slashes — str() on
                    # Windows handed it backslashes, so a `history/*` exclude
                    # silently missed every DB snapshot (broadsword H13).
                    if _is_excluded(_rel.as_posix(), merged_patterns):
                        continue
                    tar.add(_snap, arcname=f"user/{_rel}", recursive=False)
                    kept_files[0] += 1
            shutil.rmtree(staging_dir, ignore_errors=True)
            staging_dir = None
            if skipped_files[0]:
                logger.warning(f"Backup completed with {skipped_files[0]} unreadable "
                               f"file(s) skipped — see warnings above")
            if kept_files[0] == 0:
                # 0 files after exclusions (a too-broad pattern like `*`, or an empty
                # user/) — refuse the useless "successful" empty backup that rotation
                # would then keep while aging out the good ones. War-campaign fix A.
                logger.error("Backup aborted: 0 files after exclusions — "
                             "check BACKUPS_EXCLUDE_PATTERNS")
                try:
                    partial.unlink()
                except OSError:
                    pass
                return None
            # chmod 0600 BEFORE rename — backups contain credentials.json (0600);
            # without this the archive is world-readable by default umask.
            # Day-ruiner scout 2026-05-07 #L.
            try:
                os.chmod(partial, 0o600)
            except OSError as _e:
                logger.warning(f"Could not chmod backup: {_e}")
            replace_with_retry(partial, filepath)   # atomic; list_backups skips .partial
            plain_done = filepath

            if encrypt:
                sealed = out_dir / (filename.removesuffix(".tar.gz") + ".sapphirebak")
                backup_crypto.seal(filepath, sealed, password)   # GATE 1: verify or raise
                filepath.unlink()                                 # plaintext never outlives the verify
                plain_done = None
                try:
                    os.chmod(sealed, 0o600)
                except OSError:
                    pass
                filepath, filename = sealed, sealed.name

            size_mb = filepath.stat().st_size / (1024 * 1024)
            logger.info(f"Created backup: {filename} ({size_mb:.2f} MB)")
            if dest_dir is None:
                self.ensure_openers()
            return filename
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            self.last_backup_error = str(e)
            if staging_dir is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)
            if encrypt and plain_done is not None:
                # Sealing failed after the tar landed: a plaintext backup must
                # not stand in for the encrypted one that was asked for.
                plain_done.unlink(missing_ok=True)
            try:
                partial.unlink()
            except FileNotFoundError:
                pass
            except Exception as cleanup_err:
                logger.warning(f"Backup partial cleanup failed: {cleanup_err}")
            return None

    def _checkpoint_databases(self):
        """Flush WAL journals on all SQLite databases so tar captures
        consistent state. Returns set of paths whose checkpoint failed —
        the backup loop may choose to skip these from the archive rather
        than tar a torn main+WAL pair.

        Pre-fix, a SQLITE_BUSY (long-running reader holding a lock)
        silently swallowed at debug level and tar proceeded with stale
        main.db + active .db-wal. On restore, the WAL had to replay or
        be discarded — either path could miss writes the user assumed
        were captured. Voice mode increases concurrent writers, raising
        the BUSY rate. Day-ruiner scout 2026-05-07 #K.
        """
        failed = set()
        for db_path in self.user_dir.rglob("*.db"):
            try:
                conn = sqlite3.connect(str(db_path), timeout=10.0)
                # busy_timeout for the checkpoint itself — better to wait
                # a few seconds than skip. But cap to avoid hanging the
                # whole backup if a chat is mid-stream.
                conn.execute("PRAGMA busy_timeout=10000")
                # wal_checkpoint returns (busy, log_pages, checkpointed_pages).
                # busy=1 means SQLITE_BUSY — the checkpoint did not complete.
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                conn.close()
                if row and row[0] == 1:
                    logger.warning(
                        f"WAL checkpoint BUSY for {db_path.name} — harmless for "
                        f"backups (the snapshot lane guarantees consistency); "
                        f"live WAL just stays un-trimmed this cycle."
                    )
                    failed.add(db_path.resolve())
            except Exception as e:
                logger.warning(
                    f"WAL checkpoint failed for {db_path.name}: {e} — harmless "
                    f"for backups (snapshot lane guarantees consistency)."
                )
                failed.add(db_path.resolve())
        return failed

    def _snapshot_databases(self, staging_dir):
        """Consistent point-in-time copy of every SQLite DB under user/ via
        sqlite3's backup API (safe against live writers — the API re-copies
        pages a writer touches mid-run). Returns (snapshots, failed):
        snapshots maps resolved live-db path -> staging file; failed is the
        set of DBs that could not be snapshotted (omitted from the archive,
        day-ruiner #K philosophy). negspace N10, 2026-08-31."""
        snapshots, failed = {}, set()
        user_root = self.user_dir.resolve()
        for db_path in sorted(self.user_dir.rglob("*.db")):
            resolved = db_path.resolve()
            try:
                resolved.relative_to(user_root)
            except ValueError:
                continue
            snap = staging_dir / f"{len(snapshots) + len(failed)}_{db_path.name}"
            src = dst = None
            try:
                src = sqlite3.connect(str(db_path), timeout=10.0)
                src.execute("PRAGMA busy_timeout=10000")
                dst = sqlite3.connect(str(snap))
                src.backup(dst)
                dst.close(); dst = None
                src.close(); src = None
                snapshots[resolved] = snap
            except Exception as e:
                logger.error(f"DB snapshot failed for {db_path.name}: {e} — "
                             f"DB will be OMITTED from this backup")
                failed.add(resolved)
                for _c in (dst, src):
                    try:
                        if _c is not None:
                            _c.close()
                    except Exception:
                        pass
                try:
                    snap.unlink(missing_ok=True)
                except OSError:
                    pass
        return snapshots, failed

    def _db_housekeeping(self, is_weekly: bool = False):
        """Run DB integrity_check daily and VACUUM weekly against every
        SQLite file under user/. Runs at 3am alongside backups so it doesn't
        contend with active chats.

        VACUUM rebuilds the entire DB and reclaims page space freed by
        deletes — without it, the file grows monotonically even as content
        shrinks. `integrity_check` catches corruption that'd otherwise stay
        invisible until the next restart.

        On integrity failure: writes a sentinel file to `user/health/` and
        publishes a `sapphire_health_alert` event. The sentinel HALTS the
        next `run_scheduled` backup + rotation cycle so a corrupt DB doesn't
        waterfall through daily/weekly/monthly generations, eventually
        rotating out the last known-good backup. The user clears the
        sentinel file manually once the underlying DB is fixed/restored.
        Witch-hunt 2026-04-21 finding R1.
        """
        corrupt = []
        for db_path in self.user_dir.rglob("*.db"):
            try:
                conn = sqlite3.connect(str(db_path), timeout=15)
            except Exception as e:
                logger.debug(f"Housekeeping: cannot open {db_path.name}: {e}")
                continue
            try:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result and result[0] != 'ok':
                    logger.critical(
                        f"Sapphire Health: {db_path.name} integrity_check "
                        f"returned {result[0]!r} — writing corruption sentinel, "
                        f"backup rotation will HALT to preserve last-known-good"
                    )
                    corrupt.append((db_path.name, str(result[0])))
                    # Skip VACUUM on a corrupt DB — VACUUM on corrupted pages
                    # can make the corruption worse or mask it.
                    continue
                else:
                    logger.debug(f"Housekeeping: {db_path.name} integrity_check OK")
                if is_weekly:
                    # VACUUM cannot run inside a transaction. Python sqlite3's
                    # default isolation mode auto-begins one after any SELECT
                    # (like the PRAGMA above), so `conn.execute("VACUUM")` here
                    # raises "cannot VACUUM from within a transaction" — and
                    # the outer try/except silently swallows it. Switching to
                    # autocommit (isolation_level=None) lets VACUUM run.
                    # Scout finding 2026-04-20 — weekly VACUUM was a no-op.
                    conn.isolation_level = None
                    # Fast-fail if any writer holds the lock — don't sit on
                    # the DB waiting 15s and then block incoming writers
                    # under an exclusive VACUUM lock for 30-90s on grown
                    # DBs. 2s is enough for a transient checkpoint to
                    # settle; beyond that, something real is writing and we
                    # skip this week. Logs the skip so it's not silent.
                    # Witch-hunt 2026-04-21 finding R4.
                    conn.execute("PRAGMA busy_timeout=2000")
                    try:
                        logger.info(f"Housekeeping: VACUUM {db_path.name}")
                        conn.execute("VACUUM")
                    except sqlite3.OperationalError as ve:
                        if 'locked' in str(ve).lower() or 'busy' in str(ve).lower():
                            logger.info(
                                f"Housekeeping: VACUUM {db_path.name} skipped "
                                f"(DB busy) — will retry next Sunday"
                            )
                        else:
                            raise
            except Exception as e:
                logger.warning(f"Housekeeping failed for {db_path.name}: {e}")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        if corrupt:
            self._write_corruption_sentinel(corrupt)
            try:
                from core.event_bus import publish
                publish('sapphire_health_alert', {
                    'type': 'integrity_check_failure',
                    'dbs': [name for name, _ in corrupt],
                })
            except Exception as e:
                logger.debug(f"sapphire_health_alert publish failed: {e}")
        return corrupt

    def _write_corruption_sentinel(self, corrupt_list):
        """Persist a sentinel file per corrupt DB so subsequent backup runs
        detect the state even across process restarts. User clears manually
        once the DB is fixed/restored. Sentinel content is human-readable for
        forensics — no machine parsing requirement."""
        try:
            sentinel_dir = self.base_dir / "user" / "health"
            sentinel_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            for db_name, integrity_result in corrupt_list:
                sentinel = sentinel_dir / f"CORRUPT_{db_name}_{ts}.flag"
                sentinel.write_text(
                    f"db={db_name}\n"
                    f"integrity_check={integrity_result}\n"
                    f"detected_at={ts}\n"
                    f"\n"
                    f"# Backup create + rotate will HALT while this file exists,\n"
                    f"# preserving last-known-good tarballs. Fix/restore the DB,\n"
                    f"# then delete this file to resume normal backups.\n",
                    encoding='utf-8',
                )
            logger.critical(
                f"Sapphire Health: {len(corrupt_list)} corruption sentinel(s) "
                f"written to {sentinel_dir} — backup rotation HALTED"
            )
        except Exception as e:
            logger.error(f"Could not write corruption sentinel: {e}", exc_info=True)

    def _active_corruption_sentinels(self) -> list:
        """Return list of active sentinel filenames. Empty list = backup green-light."""
        sentinel_dir = self.base_dir / "user" / "health"
        if not sentinel_dir.exists():
            return []
        try:
            return [f.name for f in sentinel_dir.glob("CORRUPT_*.flag")]
        except Exception:
            return []

    def list_backups(self):
        """List all backups grouped by type."""
        # "update" = pre-update safety tars from the updater (filename
        # sapphire_<ts>_pre_update.tar.gz parses to type "update"). Absent from
        # this dict they were a ghost tier: UI-invisible and never rotated —
        # one permanent full tar per update (2026-07-06 herring hunt).
        backups = {"daily": [], "weekly": [], "monthly": [], "manual": [], "update": []}

        if not self.backup_dir.exists():
            return backups

        for f in (list(self.backup_dir.glob("sapphire_*.tar.gz"))
                  + list(self.backup_dir.glob("sapphire_*.sapphirebak"))):
            try:
                parts = f.stem.split("_")
                if len(parts) >= 4:
                    backup_type = parts[-1].replace('.tar', '')
                    if backup_type in backups:
                        backups[backup_type].append({
                            "filename": f.name,
                            "date": parts[1],
                            "time": parts[2],
                            "size": f.stat().st_size,
                            "path": str(f),
                            "encrypted": f.name.endswith(".sapphirebak"),
                        })
            except Exception as e:
                logger.warning(f"Could not parse backup filename {f.name}: {e}")

        for backup_type in backups:
            backups[backup_type].sort(key=lambda x: x["filename"], reverse=True)

        return backups

    def estimate_size(self, patterns=None, extra_patterns=None):
        """Estimate the UNCOMPRESSED backup size ('before zip') with exclusions
        applied, plus a per-top-level-folder breakdown. `patterns` (if given) is
        the full user pattern list — used to preview unsaved edits on the page;
        otherwise the saved `BACKUPS_EXCLUDE_PATTERNS` is used. `extra_patterns`
        always adds on top (offsite plugin). The privacy floor always applies."""
        base = _exclude_patterns_setting() if patterns is None else list(patterns)
        all_patterns = [str(p).strip() for p in (base + list(extra_patterns or [])) if str(p).strip()]

        total = 0
        excluded = 0
        breakdown = {}
        if self.user_dir.exists():
            for root, _dirs, files in os.walk(self.user_dir):
                for fn in files:
                    fp = Path(root) / fn
                    try:
                        rel = fp.relative_to(self.user_dir).as_posix()
                        sz = fp.stat().st_size
                    except (OSError, ValueError):
                        continue
                    if _is_excluded(rel, all_patterns):
                        excluded += sz
                        continue
                    total += sz
                    # Top-level entry (du --max-depth=1 style): history, rag,
                    # memory.db, … The page hides the tiny ones behind "see all".
                    top = rel.split('/', 1)[0]
                    breakdown[top] = breakdown.get(top, 0) + sz

        warn_mb = float(getattr(config, 'BACKUPS_MAX_SIZE_WARN_MB', 2048) or 0)
        breakdown_list = sorted(
            ({"name": k, "bytes": v} for k, v in breakdown.items()),
            key=lambda x: x["bytes"], reverse=True,
        )
        return {
            "total_bytes": total,
            "excluded_bytes": excluded,
            "breakdown": breakdown_list,
            "warn_mb": warn_mb,
            "over_warn": bool(warn_mb > 0 and total > warn_mb * 1024 * 1024),
        }

    def delete_backup(self, filename):
        """Delete a specific backup file."""
        if "/" in filename or "\\" in filename:
            return False

        filepath = self.backup_dir / filename
        if not filepath.exists():
            return False
        if filepath.suffix not in (".gz", ".sapphirebak") or not filename.startswith("sapphire_"):
            return False

        try:
            filepath.unlink()
            logger.info(f"Deleted backup: {filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete backup {filename}: {e}")
            return False

    def rotate_backups(self):
        """Rotate backups based on retention settings (keep<=0 = pause that
        tier: retain existing, never purge). The selection is the pure
        `select_doomed`, shared with every remote target."""
        limits = {
            "daily": getattr(config, 'BACKUPS_KEEP_DAILY', 7),
            "weekly": getattr(config, 'BACKUPS_KEEP_WEEKLY', 4),
            "monthly": getattr(config, 'BACKUPS_KEEP_MONTHLY', 3),
            "manual": getattr(config, 'BACKUPS_KEEP_MANUAL', 5),
            "update": getattr(config, 'BACKUPS_KEEP_UPDATE', 3)
        }
        names = [b["filename"] for lst in self.list_backups().values() for b in lst]
        deleted = sum(1 for n in select_doomed(names, limits) if self.delete_backup(n))
        if deleted:
            logger.info(f"Rotation complete: deleted {deleted} old backups")
        self._sweep_stale_tmp()
        return deleted

    def _sweep_stale_tmp(self, max_age_s=86400):
        """Drop `ship_*` / `export_*` / `remembrance_*` temp dirs older than a
        day (a shipper that died mid-flight leaves one; its blob is ciphertext,
        but it's still disk)."""
        now = time.time()
        for pat in ("ship_*", "export_*", "remembrance_*"):
            for d in self.backup_dir.glob(pat):
                try:
                    if d.is_dir() and now - d.stat().st_mtime > max_age_s:
                        shutil.rmtree(d, ignore_errors=True)
                        logger.info(f"Removed stale backup temp dir {d.name}")
                except OSError:
                    pass

    def get_backup_path(self, filename):
        """Get full path to a backup file (for downloads)."""
        if "/" in filename or "\\" in filename:
            return None
        filepath = self.backup_dir / filename
        if filepath.exists() and filename.startswith("sapphire_"):
            return filepath
        return None

    # ------------------------------------------------------------------
    # Off-box (2026-10-06): the gate every shipper runs — core-owned so no
    # plugin or driver rebuilds encryption. Nothing plaintext survives these.
    # ------------------------------------------------------------------

    def export_encrypted(self, backup_type="offsite", dest_dir=None, extra_patterns=None,
                         cap_mb=0, require_complete=False):
        """Build a fresh SEALED backup for shipping: password → corruption
        sentinel halt → size cap → tar into dest_dir → seal (encrypt + verify,
        GATE 1) → unlink the plaintext. Returns the Path of the .sapphirebak
        (the caller ships it and removes dest_dir). Raises BackupRefused with a
        user-facing reason. Was Remembrance's gate; Remembrance calls this now."""
        if dest_dir is None:
            raise ValueError("export_encrypted needs dest_dir (the caller owns it)")
        if not self._backup_password():
            raise BackupRefused("Encryption requires a backup password — set one in Settings > Backup")
        sentinels = self._active_corruption_sentinels()
        if sentinels:
            raise BackupRefused(f"Halted — active corruption sentinel(s): "
                                f"{', '.join(sentinels)}. Clear them in Settings > Backup first.")
        extra = [str(p).strip() for p in (extra_patterns or []) if str(p).strip()]
        cap_mb = int(cap_mb or 0)
        if cap_mb > 0:
            # Runaway guard: refuse before building a giant blob (the 150 GB war story).
            try:
                est = self.estimate_size(extra_patterns=extra)
            except Exception as e:
                logger.warning(f"Backup size estimate failed (continuing): {e}")
                est = {}
            if est.get("total_bytes", 0) > cap_mb * 1024 * 1024:
                raise BackupRefused(f"Backup is ~{est['total_bytes'] // (1024 * 1024)} MB "
                                    f"(cap {cap_mb} MB) — add excludes or raise the cap")
        fn = self.create_backup(backup_type=backup_type, extra_patterns=extra,
                                dest_dir=dest_dir, require_complete=require_complete,
                                encrypt=True)
        if not fn:
            raise BackupRefused(self.last_backup_error or
                                "Backup produced no file (empty after excludes?)")
        return Path(dest_dir) / fn

    def sealed(self, filename):
        """A sealed (.sapphirebak) version of an EXISTING local backup, for
        shipping the 3am tar itself: the file when already encrypted, else a
        sealed copy in a temp dir under backup_dir. Returns (path, cleanup);
        call cleanup() once shipped. Raises BackupRefused."""
        src = self.get_backup_path(filename)
        if not src:
            raise BackupRefused(f"Backup not found: {filename}")
        if backup_crypto.is_encrypted_backup(src):
            return src, (lambda: None)
        password = self._backup_password()
        if not password:
            raise BackupRefused("Shipping requires a backup password — set one in Settings > Backup")
        tmp = Path(tempfile.mkdtemp(prefix="ship_", dir=str(self.backup_dir)))
        dst = tmp / (src.name.removesuffix(".tar.gz") + ".sapphirebak")
        try:
            backup_crypto.seal(src, dst, password)
        except Exception as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise BackupRefused(str(e))
        return dst, (lambda: shutil.rmtree(tmp, ignore_errors=True))

    # ------------------------------------------------------------------
    # Openers (2026-10-06): scripts beside the backups so "pop in and
    # restore" works with no Sapphire — tar for plain, decrypt_backup.py +
    # the password for sealed ones. Pure ASCII (Windows cmd is cp1252).
    # ------------------------------------------------------------------

    def ensure_openers(self, folder=None):
        """Write open-backup.sh / open-backup.bat / decrypt_backup.py /
        README.txt into `folder` (default backup_dir) when missing or stale.
        Best effort; never raises."""
        folder = Path(folder) if folder else self.backup_dir
        try:
            from core import backup_openers
            backup_openers.write_all(folder, self.base_dir / "tools" / "decrypt_backup.py")
        except Exception as e:
            logger.warning(f"Could not write backup openers to {folder}: {e}")


    def _next_run_seconds(self, now=None):
        """Seconds until the next BACKUPS_HOUR run (plus the target datetime).
        Coerces + clamps the hour: a hand-edited "3" (string) used to
        TypeError in the loop and kill the scheduler thread on iteration one
        (X1 F7, negspace 2026-08-31)."""
        from datetime import timedelta
        raw = getattr(config, 'BACKUPS_HOUR', 3)
        try:
            hour = int(raw)
        except (TypeError, ValueError):
            logger.error(f"BACKUPS_HOUR invalid ({raw!r}) — using 3")
            hour = 3
        if not 0 <= hour <= 23:
            logger.error(f"BACKUPS_HOUR out of range ({hour}) — using 3")
            hour = 3
        now = now or datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds(), target

    def newest_backup(self):
        """{filename, size, age_hours} of the newest backup of any tier, or None."""
        try:
            allb = [b for tier in self.list_backups().values() for b in tier]
            if not allb:
                return None

            def _mtime(b):
                try:
                    return Path(b['path']).stat().st_mtime
                except OSError:
                    return 0
            nb = max(allb, key=_mtime)
            ts = _mtime(nb)
            return {'filename': nb.get('filename'), 'size': nb.get('size'),
                    'age_hours': round((time.time() - ts) / 3600, 1) if ts else None}
        except Exception as e:
            logger.warning(f"backup health: list failed: {e}")
            return None

    def health_summary(self):
        """One honest dict about backup health (negspace N11, 2026-08-31).
        The mechanism was well-guarded but every failure mode was invisible:
        sentinel halt had no UI, the status widget lied structurally, a dead
        scheduler thread logged 'started', failures logged under 'complete'.
        Served by GET /api/backup/health; rendered on Settings > Backup."""
        newest = self.newest_backup()
        sentinels = self._active_corruption_sentinels()
        thread = getattr(self, '_scheduler_thread', None)
        try:
            from core.credentials_manager import credentials
            password_status = credentials.backup_password_status()
        except Exception:
            password_status = 'unreadable'
        folder = self.backup_dir
        try:
            from core import backup_targets
            devices = backup_targets.status()
        except Exception as e:
            logger.warning(f"backup health: targets failed: {e}")
            devices = {"targets": [], "shipping": None, "last_ship": None}
        return {
            'enabled': bool(getattr(config, 'BACKUPS_ENABLED', True)),
            'sentinels': sentinels,
            'halted': bool(sentinels),
            'scheduler_alive': bool(thread and thread.is_alive()),
            'newest': newest,
            'last_scheduled_result': getattr(self, 'last_scheduled_result', None),
            'last_backup_error': getattr(self, 'last_backup_error', None),
            'backup_dir': str(folder),
            'backup_dir_error': getattr(self, 'backup_dir_error', None),
            'encrypt': bool(getattr(config, 'BACKUPS_ENCRYPT_LOCAL', False)),
            'password_status': password_status,
            'devices': devices,
        }

    def stop(self):
        """Signal the backup scheduler to stop."""
        if self._stop_event:
            self._stop_event.set()

    def start_scheduler(self):
        """Start background thread that runs scheduled backups at BACKUPS_HOUR (default 3am local time)."""
        import threading
        from datetime import timedelta
        self._stop_event = threading.Event()
        self.ensure_openers()

        def _backup_loop():
            while not self._stop_event.is_set():
                try:
                    wait_seconds, target = self._next_run_seconds()
                    logger.info(f"Backup scheduler: next run in {wait_seconds / 3600:.1f}h at {target.strftime('%Y-%m-%d %H:%M')}")
                except Exception as e:
                    # The loop must NEVER die quietly — pre-fix a bad
                    # BACKUPS_HOUR raised here, killed this thread on iteration
                    # one, and "scheduler started" was logged right after over
                    # the corpse (X1 F7, negspace 2026-08-31).
                    logger.critical(f"Backup scheduler iteration failed: {e} — retrying in 1h")
                    wait_seconds = 3600.0
                if self._stop_event.wait(wait_seconds):
                    break  # Stop requested during sleep

                # Run DB housekeeping FIRST so integrity failures write their
                # corruption sentinels BEFORE run_scheduled fires. This is what
                # lets R1's sentinel-halt work: detect → sentinel → halt within
                # the same 3am cycle. Witch-hunt 2026-04-21 R1.
                try:
                    self._db_housekeeping(is_weekly=(datetime.now().weekday() == 6))
                except Exception as e:
                    logger.warning(f"DB housekeeping failed: {e}")

                try:
                    result = self.run_scheduled()
                    logger.info(f"Backup scheduler: {result}")
                except Exception as e:
                    logger.error(f"Backup scheduler failed: {e}")

                # Ship tonight's newest tier to the backup targets (devices,
                # paths) — on its own thread, OUTSIDE _backup_op_lock, so a
                # slow card (1-2 min per 70 MB) never blocks a manual backup
                # or this loop. 2026-10-06.
                try:
                    from core import backup_targets
                    backup_targets.ship_async(getattr(self, 'last_scheduled_files', None) or [])
                except Exception as e:
                    logger.warning(f"Backup targets ship failed to start: {e}")

                # Metrics retention piggybacks — low-priority "housekeep at 3am"
                # task, no reason for a separate scheduler.
                try:
                    from core.metrics import metrics
                    metrics.prune(keep_days=90)
                except Exception as e:
                    logger.warning(f"Metrics prune during backup cycle failed: {e}")

        thread = threading.Thread(target=_backup_loop, daemon=True, name="backup-scheduler")
        self._scheduler_thread = thread   # exposed via health_summary (N11)
        thread.start()
        logger.info("Backup scheduler started (daily at BACKUPS_HOUR local time)")


backup_manager = Backup()
