import os
import tarfile
import logging
from datetime import datetime
from pathlib import Path
import config

logger = logging.getLogger(__name__)


class Backup:
    """Background backup module - no keywords, triggered by cron only."""
    
    def __init__(self):
        self.keyword_match = None
        self.full_command = None
        self.voice_chat_system = None
        
        # Paths - use config.BASE_DIR for reliability
        self.base_dir = Path(getattr(config, 'BASE_DIR', Path(__file__).parent.parent.parent.parent))
        self.user_dir = self.base_dir / "user"
        self.backup_dir = self.base_dir / "user_backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        logger.info(f"Backup module initialized - base_dir: {self.base_dir}, backup_dir: {self.backup_dir}")
    
    def process(self, user_input, llm_client=None):
        """Process backup commands from event scheduler."""
        logger.info(f"Backup module processing")
        
        if user_input == "run_scheduled":
            return self.run_scheduled()
        elif user_input.startswith("create "):
            backup_type = user_input.split(" ", 1)[1]
            return self.create_backup(backup_type)
        
        return "Backup module ready"
    
    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info("Backup module attached to system")
    
    def run_scheduled(self):
        """Run scheduled backup check - called daily at 3am."""
        if not getattr(config, 'BACKUPS_ENABLED', True):
            logger.info("Backups disabled, skipping scheduled run")
            return "Backups disabled"
        
        now = datetime.now()
        results = []
        
        # Always create daily
        if getattr(config, 'BACKUPS_KEEP_DAILY', 7) > 0:
            self.create_backup("daily")
            results.append("daily")
        
        # Weekly on Sunday (weekday 6)
        if now.weekday() == 6 and getattr(config, 'BACKUPS_KEEP_WEEKLY', 4) > 0:
            self.create_backup("weekly")
            results.append("weekly")
        
        # Monthly on 1st
        if now.day == 1 and getattr(config, 'BACKUPS_KEEP_MONTHLY', 3) > 0:
            self.create_backup("monthly")
            results.append("monthly")
        
        # Rotate after creating
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
        
        logger.info(f"Listing backups from: {self.backup_dir}")
        
        if not self.backup_dir.exists():
            logger.warning(f"Backup directory does not exist: {self.backup_dir}")
            return backups
        
        files_found = list(self.backup_dir.glob("sapphire_*.tar.gz"))
        logger.info(f"Found {len(files_found)} backup files")
        
        for f in files_found:
            try:
                # Parse: sapphire_2025-01-15_143022_daily.tar.gz
                parts = f.stem.split("_")
                logger.info(f"Parsing {f.name}: stem={f.stem}, parts={parts}, len={len(parts)}")
                
                if len(parts) >= 4:
                    backup_type = parts[-1].replace('.tar', '')  # Handle .tar.gz double extension
                    date_str = parts[1]
                    time_str = parts[2]
                    logger.info(f"  -> type={backup_type}, date={date_str}, time={time_str}, in_backups={backup_type in backups}")
                    
                    if backup_type in backups:
                        backups[backup_type].append({
                            "filename": f.name,
                            "date": date_str,
                            "time": time_str,
                            "size": f.stat().st_size,
                            "path": str(f)
                        })
                        logger.info(f"  -> ADDED to {backup_type}")
                    else:
                        logger.warning(f"  -> backup_type '{backup_type}' not in backups dict")
                else:
                    logger.warning(f"  -> Not enough parts: {len(parts)} < 4")
            except Exception as e:
                logger.warning(f"Could not parse backup filename {f.name}: {e}")
        
        # Sort each type by filename (newest first)
        for backup_type in backups:
            backups[backup_type].sort(key=lambda x: x["filename"], reverse=True)
        
        total = sum(len(v) for v in backups.values())
        logger.info(f"Returning {total} backups: daily={len(backups['daily'])}, weekly={len(backups['weekly'])}, monthly={len(backups['monthly'])}, manual={len(backups['manual'])}")
        
        return backups
    
    def delete_backup(self, filename):
        """Delete a specific backup file."""
        # Security: only allow filenames, no paths
        if "/" in filename or "\\" in filename:
            logger.warning(f"Invalid backup filename (path chars): {filename}")
            return False
        
        filepath = self.backup_dir / filename
        
        if not filepath.exists():
            logger.warning(f"Backup not found: {filename}")
            return False
        
        if not filepath.suffix == ".gz" or not filename.startswith("sapphire_"):
            logger.warning(f"Invalid backup filename: {filename}")
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
                # Delete oldest (they're sorted newest first)
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