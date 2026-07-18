import os
import fnmatch
import tarfile
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path
import config

logger = logging.getLogger(__name__)


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
def _cache_excluded(rel: str) -> bool:
    return rel == 'models' or rel.startswith('models/')


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
        self.backup_dir = self.base_dir / "user_backups"
        self.backup_dir.mkdir(exist_ok=True)
        # Serializes create + rotate as a single critical section. Without
        # this, a manual backup triggered during the scheduled 3am run can
        # race with rotation: both paths sort-by-mtime and delete oldest-
        # past-limit, and an in-flight partial can be counted or a valid
        # older backup deleted to make room for a still-writing new one.
        # Witch-hunt 2026-04-21 finding R5.
        self._backup_op_lock = threading.Lock()
        logger.info(f"Backup initialized - base_dir: {self.base_dir}, backup_dir: {self.backup_dir}")

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
            results = []

            if getattr(config, 'BACKUPS_KEEP_DAILY', 7) > 0:
                self.create_backup("daily")
                results.append("daily")

            if now.weekday() == 6 and getattr(config, 'BACKUPS_KEEP_WEEKLY', 4) > 0:
                self.create_backup("weekly")
                results.append("weekly")

            if now.day == 1 and getattr(config, 'BACKUPS_KEEP_MONTHLY', 3) > 0:
                self.create_backup("monthly")
                results.append("monthly")

            # Rotate INSIDE the lock — otherwise a manual trigger between
            # create and rotate can race.
            self.rotate_backups()
        return f"Scheduled backup complete: {', '.join(results)}"

    def create_backup(self, backup_type="manual", extra_patterns=None, dest_dir=None):
        """Create a plain .tar.gz backup of the user/ directory.

        Writes to `<filename>.partial` first, atomic-renames to final name on
        success. Without this, a disk-full / kill-mid-write leaves a truncated
        `.tar.gz` that `list_backups` parses as legitimate, and rotation may
        delete older valid backups in favor of the partial. Witch-hunt
        2026-04-21 finding H13.

        Local backups are never encrypted — they sit on the same disk as the
        live user/ data, so at-rest encryption here protected nothing and cost
        users who lost the password. Encryption happens in the Remembrance
        plugin, only when a backup leaves the machine. (Restore still decrypts
        old .sapphirebak files — see core/routes/system.py.)

        Optional (offsite path; defaults reproduce the local behavior exactly):
          extra_patterns — extra exclude globs merged with the page settings.
          dest_dir       — write the tarball here instead of user_backups/.
        """
        if not self.user_dir.exists():
            logger.error(f"User directory not found: {self.user_dir}")
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"sapphire_{timestamp}_{backup_type}.tar.gz"
        out_dir = Path(dest_dir) if dest_dir else self.backup_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / filename
        partial = out_dir / (filename + ".partial")

        try:
            # Checkpoint SQLite WAL files before backup. DBs whose checkpoint
            # failed (SQLITE_BUSY etc.) get skipped from the archive entirely
            # — better to omit a DB from this backup than capture it in a
            # torn state that won't restore cleanly. The next scheduled
            # backup will retry. Day-ruiner scout 2026-05-07 #K.
            failed_checkpoints = self._checkpoint_databases()
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
            def _filter_with_busy(tarinfo):
                base = _patterns_filter(tarinfo)
                if base is None:
                    return None
                # Drop the failed DB and its WAL/SHM siblings so we don't
                # ship a half-snapshot. Match against absolute path of the
                # underlying file (tarinfo.name is relative to the arcname).
                src = (self.user_dir / tarinfo.name[len("user/"):]).resolve() if tarinfo.name.startswith("user/") else None
                if src is not None:
                    for db in failed_checkpoints:
                        if src == db or src == Path(str(db) + "-wal") or src == Path(str(db) + "-shm"):
                            return None
                return tarinfo
            kept_files = [0]
            base_filter = _filter_with_busy if failed_checkpoints else _patterns_filter
            def _counting_filter(tarinfo):
                ti = base_filter(tarinfo)
                if ti is not None and ti.isfile():
                    kept_files[0] += 1
                return ti
            with tarfile.open(partial, "w:gz") as tar:
                tar.add(self.user_dir, arcname="user", filter=_counting_filter)
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
            partial.replace(filepath)   # atomic; list_backups skips .partial

            size_mb = filepath.stat().st_size / (1024 * 1024)
            logger.info(f"Created backup: {filename} ({size_mb:.2f} MB)")
            return filename
        except Exception as e:
            logger.error(f"Backup failed: {e}")
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
                        f"WAL checkpoint BUSY for {db_path.name} — backup will "
                        f"skip this DB to avoid torn main+WAL state."
                    )
                    failed.add(db_path.resolve())
            except Exception as e:
                logger.warning(
                    f"WAL checkpoint failed for {db_path.name}: {e} — "
                    f"DB skipped from backup to avoid inconsistent capture."
                )
                failed.add(db_path.resolve())
        return failed

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
        """Rotate backups based on retention settings."""
        backups = self.list_backups()
        limits = {
            "daily": getattr(config, 'BACKUPS_KEEP_DAILY', 7),
            "weekly": getattr(config, 'BACKUPS_KEEP_WEEKLY', 4),
            "monthly": getattr(config, 'BACKUPS_KEEP_MONTHLY', 3),
            "manual": getattr(config, 'BACKUPS_KEEP_MANUAL', 5),
            "update": getattr(config, 'BACKUPS_KEEP_UPDATE', 3)
        }

        deleted = 0
        for backup_type, backup_list in backups.items():
            limit = limits.get(backup_type, 5)
            if limit <= 0:
                continue  # keep<=0 = "pause this tier": retain existing, never purge
            if len(backup_list) > limit:
                for backup in backup_list[limit:]:
                    if self.delete_backup(backup["filename"]):
                        deleted += 1

        if deleted:
            logger.info(f"Rotation complete: deleted {deleted} old backups")
        return deleted

    def get_backup_path(self, filename):
        """Get full path to a backup file (for downloads)."""
        if "/" in filename or "\\" in filename:
            return None
        filepath = self.backup_dir / filename
        if filepath.exists() and filename.startswith("sapphire_"):
            return filepath
        return None


    def stop(self):
        """Signal the backup scheduler to stop."""
        if self._stop_event:
            self._stop_event.set()

    def start_scheduler(self):
        """Start background thread that runs scheduled backups at BACKUPS_HOUR (default 3am local time)."""
        import threading
        from datetime import timedelta
        self._stop_event = threading.Event()

        def _backup_loop():
            while not self._stop_event.is_set():
                hour = getattr(config, 'BACKUPS_HOUR', 3)
                now = datetime.now()
                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"Backup scheduler: next run in {wait_seconds / 3600:.1f}h at {target.strftime('%Y-%m-%d %H:%M')}")
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

                # Metrics retention piggybacks — low-priority "housekeep at 3am"
                # task, no reason for a separate scheduler.
                try:
                    from core.metrics import metrics
                    metrics.prune(keep_days=90)
                except Exception as e:
                    logger.warning(f"Metrics prune during backup cycle failed: {e}")

        thread = threading.Thread(target=_backup_loop, daemon=True, name="backup-scheduler")
        thread.start()
        hour = getattr(config, 'BACKUPS_HOUR', 3)
        logger.info(f"Backup scheduler started (daily at {hour}:00 local time)")


backup_manager = Backup()
