import tarfile
import logging
from datetime import datetime
from pathlib import Path
import config

logger = logging.getLogger(__name__)


class Backup:
    """Backup manager for the user/ directory."""

    def __init__(self):
        self.base_dir = Path(getattr(config, 'BASE_DIR', Path(__file__).parent.parent))
        self.user_dir = self.base_dir / "user"
        self.backup_dir = self.base_dir / "user_backups"
        self.backup_dir.mkdir(exist_ok=True)
        logger.info(f"Backup initialized - base_dir: {self.base_dir}, backup_dir: {self.backup_dir}")

    def run_scheduled(self):
        """Run scheduled backup check - called daily at 3am."""
        if not getattr(config, 'BACKUPS_ENABLED', True):
            logger.info("Backups disabled, skipping scheduled run")
            return "Backups disabled"

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

        self.rotate_backups()
        return f"Scheduled backup complete: {', '.join(results)}"

    def create_backup(self, backup_type="manual"):
        """Create a backup of the user/ directory."""
        if not self.user_dir.exists():
            logger.error(f"User directory not found: {self.user_dir}")
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"sapphire_{timestamp}_{backup_type}.tar.gz"
        filepath = self.backup_dir / filename

        try:
            with tarfile.open(filepath, "w:gz") as tar:
                tar.add(self.user_dir, arcname="user")

            size_mb = filepath.stat().st_size / (1024 * 1024)
            logger.info(f"Created backup: {filename} ({size_mb:.2f} MB)")
            return filename
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return None

    def list_backups(self):
        """List all backups grouped by type."""
        backups = {"daily": [], "weekly": [], "monthly": [], "manual": []}

        if not self.backup_dir.exists():
            return backups

        for f in self.backup_dir.glob("sapphire_*.tar.gz"):
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
                            "path": str(f)
                        })
            except Exception as e:
                logger.warning(f"Could not parse backup filename {f.name}: {e}")

        for backup_type in backups:
            backups[backup_type].sort(key=lambda x: x["filename"], reverse=True)

        return backups

    def delete_backup(self, filename):
        """Delete a specific backup file."""
        if "/" in filename or "\\" in filename:
            return False

        filepath = self.backup_dir / filename
        if not filepath.exists():
            return False
        if not filepath.suffix == ".gz" or not filename.startswith("sapphire_"):
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
            "manual": getattr(config, 'BACKUPS_KEEP_MANUAL', 5)
        }

        deleted = 0
        for backup_type, backup_list in backups.items():
            limit = limits.get(backup_type, 5)
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


    def start_scheduler(self):
        """Start background thread that runs scheduled backups at BACKUPS_HOUR (default 3am local time)."""
        import threading
        from datetime import timedelta

        def _backup_loop():
            import time
            while True:
                hour = getattr(config, 'BACKUPS_HOUR', 3)
                now = datetime.now()
                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"Backup scheduler: next run in {wait_seconds / 3600:.1f}h at {target.strftime('%Y-%m-%d %H:%M')}")
                time.sleep(wait_seconds)
                try:
                    result = self.run_scheduled()
                    logger.info(f"Backup scheduler: {result}")
                except Exception as e:
                    logger.error(f"Backup scheduler failed: {e}")

        thread = threading.Thread(target=_backup_loop, daemon=True, name="backup-scheduler")
        thread.start()
        hour = getattr(config, 'BACKUPS_HOUR', 3)
        logger.info(f"Backup scheduler started (daily at {hour}:00 local time)")


backup_manager = Backup()
