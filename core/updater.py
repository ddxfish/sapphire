"""Auto-updater — checks GitHub for new versions, runs git pull to update."""
import logging
import subprocess
import threading
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

VERSION_FILE = Path(__file__).parent.parent / 'VERSION'
REPO_DIR = VERSION_FILE.parent
GITHUB_REPO = 'ddxfish/sapphire'
GITHUB_RAW_URL = f'https://raw.githubusercontent.com/{GITHUB_REPO}'
CHECK_INTERVAL = 86400  # 24 hours


class Updater:
    def __init__(self):
        self.current_version = self._read_local_version()
        self.latest_version = None
        self.update_available = False
        self.last_check = 0
        self.checking = False
        self._thread = None
        self.branch = self._detect_branch()
        self.is_fork = self._detect_fork()

    def _read_local_version(self):
        try:
            return VERSION_FILE.read_text().strip()
        except Exception:
            return '?'

    def _detect_branch(self):
        """Get current git branch name."""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=str(REPO_DIR), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return 'main'

    def _detect_fork(self):
        """Check if origin points to the official repo."""
        try:
            result = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                cwd=str(REPO_DIR), capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                url = result.stdout.strip().lower()
                return GITHUB_REPO.lower() not in url
        except Exception:
            pass
        return False

    def has_git(self):
        """Check if we're in a git repo."""
        git_dir = REPO_DIR / '.git'
        return git_dir.exists()

    def check_for_update(self, force=False):
        """Check GitHub for a newer version. Returns dict with status."""
        if self.checking:
            return self.status()

        now = time.time()
        if not force and self.last_check and (now - self.last_check) < 300:
            return self.status()

        self.checking = True

        def _parse_version(v):
            """Parse version string to tuple, stripping non-numeric suffixes."""
            parts = []
            for x in v.split('.'):
                # Strip suffixes like -rc1, .dev, -beta
                num = ''
                for ch in x:
                    if ch.isdigit():
                        num += ch
                    else:
                        break
                parts.append(int(num) if num else 0)
            return tuple(parts)

        try:
            # Always check official repo — branch-aware
            url = f'{GITHUB_RAW_URL}/{self.branch}/VERSION'
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                self.latest_version = resp.text.strip()
                self.update_available = (
                    _parse_version(self.latest_version) >
                    _parse_version(self.current_version)
                )
                self.last_check = now
                if self.update_available:
                    logger.info(f"Update available: {self.current_version} → {self.latest_version}")
            elif resp.status_code == 404:
                # Branch doesn't exist on official repo — fall back to main
                resp = requests.get(f'{GITHUB_RAW_URL}/main/VERSION', timeout=10)
                if resp.status_code == 200:
                    self.latest_version = resp.text.strip()
                    self.update_available = (
                        _parse_version(self.latest_version) >
                        _parse_version(self.current_version)
                    )
                    self.last_check = now
            else:
                logger.warning(f"Version check failed: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Version check failed: {e}")
        finally:
            self.checking = False

        return self.status()

    def status(self):
        return {
            'current': self.current_version,
            'latest': self.latest_version,
            'available': self.update_available,
            'has_git': self.has_git(),
            'last_check': self.last_check,
            'branch': self.branch,
            'is_fork': self.is_fork,
        }

    def do_update(self):
        """Run backup then git pull. Returns (success, message)."""
        if not self.has_git():
            return False, "Not a git repository. Download the latest release from GitHub."
        if self.is_fork:
            return False, "Fork detected — pull updates from upstream manually."

        # Run backup first
        try:
            from core.backup import backup_manager
            backup_manager.create_backup('pre_update')
            logger.info("Pre-update backup created")
        except Exception as e:
            logger.warning(f"Pre-update backup failed (continuing anyway): {e}")

        # Git pull
        try:
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                # Re-read version after pull
                self.current_version = self._read_local_version()
                self.update_available = False
                logger.info(f"Update successful: {result.stdout.strip()}")
                return True, result.stdout.strip()
            else:
                error = result.stderr.strip() or result.stdout.strip()
                logger.error(f"Git pull failed: {error}")
                return False, f"Git pull failed: {error}"
        except subprocess.TimeoutExpired:
            return False, "Git pull timed out after 60 seconds"
        except FileNotFoundError:
            return False, "Git not found. Install git or download the update manually."
        except Exception as e:
            return False, f"Update failed: {e}"

    def start_background_checker(self):
        """Start periodic version check in background thread."""
        def _checker():
            # Initial check after 30s delay (let app finish booting)
            time.sleep(30)
            while True:
                try:
                    self.check_for_update()
                except Exception as e:
                    logger.warning(f"Background version check failed: {e}")
                time.sleep(CHECK_INTERVAL)

        self._thread = threading.Thread(target=_checker, daemon=True, name='updater')
        self._thread.start()
        logger.info("Background update checker started (24h interval)")


# Singleton
updater = Updater()
