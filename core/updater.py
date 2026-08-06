"""Auto-updater — checks GitHub for new versions, schedules a `git pull`-on-restart.

Architecture: updates are DEFERRED. `do_update()` runs a strict pre-flight,
takes a backup, writes a pending-update marker, and requests restart. The
runner (`main.py`) calls `apply_pending_update()` before the new sapphire.py
process is spawned, while no Python code holds files open — that's the only
way `git pull` works reliably on Windows, and it makes the Linux flow more
deterministic too. Result of the deferred attempt is written to
`user/last_update_result.json` and surfaced to the UI on next load.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from core.github_files import fetch_github_file
from core.versions import is_newer

logger = logging.getLogger(__name__)

VERSION_FILE = Path(__file__).parent.parent / 'VERSION'
REPO_DIR = VERSION_FILE.parent
GITHUB_REPO = 'ddxfish/sapphire'
CHECK_INTERVAL = 86400  # 24 hours

PENDING_UPDATE_FILE = REPO_DIR / 'user' / 'pending_update.json'
UPDATE_RESULT_FILE = REPO_DIR / 'user' / 'last_update_result.json'

# Branches that refuse the auto-update button. "dev" builds pull manually
# because the UI shouldn't surface work-in-progress commits to users.
_BLOCKED_BRANCHES = {'dev'}

# Minimum free disk for a safe update (backup + pull headroom).
_MIN_FREE_MB = 200


# ─── Subprocess helpers ─────────────────────────────────────────────────────
# Shared env/flags make git subprocesses Windows-safe: no credential prompts
# (blocks a GUI dialog forever under GCM), no pagers, utf-8 output, no stdin.

def _git_env():
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GCM_INTERACTIVE'] = 'Never'
    env['GIT_PAGER'] = 'cat'
    return env


def _run_git(args, timeout=60):
    """Run a git command with safe defaults. Returns CompletedProcess.

    Raises FileNotFoundError if git isn't on PATH — caller decides how to
    surface that. TimeoutExpired bubbles up so the caller can clean up
    `.git/index.lock` if needed.
    """
    return subprocess.run(
        ['git', *args],
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        stdin=subprocess.DEVNULL,
        env=_git_env(),
        timeout=timeout,
    )


def _clear_index_lock():
    """Clear a stale `.git/index.lock` left behind by a killed/timed-out git.

    Safe to call anytime — only unlinks if it exists, ignores errors.
    """
    try:
        lock = REPO_DIR / '.git' / 'index.lock'
        if lock.exists():
            lock.unlink()
    except Exception:
        pass


# ─── Fork detection ─────────────────────────────────────────────────────────

def _parse_github_slug(url: str):
    """Extract owner/repo from any GitHub URL shape. Returns lowercased
    'owner/repo' or None if we can't parse it."""
    if not url:
        return None
    url = url.strip()
    # SSH form: git@github.com:owner/repo.git
    m = re.match(r'^git@github\.com:([^/]+)/([^/\s]+?)(?:\.git)?/?$', url)
    if m:
        return f"{m.group(1).lower()}/{m.group(2).lower()}"
    # HTTPS / git-protocol form: parse via urlparse
    try:
        p = urlparse(url)
        if 'github.com' not in (p.netloc or '').lower():
            return None
        path = (p.path or '').strip('/').lower()
        if path.endswith('.git'):
            path = path[:-4]
        parts = path.split('/')
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return None


# ─── Result file helpers (called from main.py, no heavy deps) ───────────────

def _write_result(success, message, from_version=None, to_version=None):
    try:
        UPDATE_RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = UPDATE_RESULT_FILE.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({
            'success': bool(success),
            'message': str(message),
            'from_version': from_version,
            'to_version': to_version,
            'timestamp': time.time(),
        }), encoding='utf-8')
        tmp.replace(UPDATE_RESULT_FILE)
    except Exception:
        pass  # Best-effort; don't fail boot over a result write


def read_last_update_result(clear: bool = False):
    """Read the last deferred-update result (written by main.py post-apply).
    If `clear=True`, deletes the file after reading so the UI only shows it once."""
    if not UPDATE_RESULT_FILE.exists():
        return None
    try:
        data = json.loads(UPDATE_RESULT_FILE.read_text(encoding='utf-8'))
    except Exception:
        data = None
    if clear:
        try:
            UPDATE_RESULT_FILE.unlink()
        except Exception:
            pass
    return data


def _clear_pending():
    try:
        if PENDING_UPDATE_FILE.exists():
            PENDING_UPDATE_FILE.unlink()
    except Exception:
        pass


# ─── Main entry point called from main.py ───────────────────────────────────

def apply_pending_update():
    """Apply a pending update if one was scheduled.

    Called by main.py BEFORE spawning sapphire.py, so no Python code holds
    files open (critical on Windows). Always returns silently — the result
    is written to UPDATE_RESULT_FILE for the UI to display on next load.
    Never blocks boot on failure; on any error we clear the pending marker
    and continue booting the previous version.
    """
    if not PENDING_UPDATE_FILE.exists():
        return

    try:
        data = json.loads(PENDING_UPDATE_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        _write_result(False, f"Pending update marker unreadable: {e}")
        _clear_pending()
        return

    branch = data.get('branch') or 'main'
    from_version = data.get('from_version')

    # Run pull. On Windows, timeouts can leave index.lock behind — clean up.
    try:
        pull = _run_git(['pull', '--ff-only', 'origin', branch], timeout=180)
    except subprocess.TimeoutExpired:
        _clear_index_lock()
        _write_result(False, "git pull timed out after 180s. Kept previous version.")
        _clear_pending()
        return
    except FileNotFoundError:
        _write_result(False, "git is not installed. Update skipped.")
        _clear_pending()
        return

    if pull.returncode != 0:
        err = (pull.stderr or '').strip() or (pull.stdout or '').strip() or 'unknown error'
        _write_result(False, f"git pull failed: {err}")
        _clear_pending()
        return

    # Pip sync — new deps are a silent-brick risk if skipped.
    req_file = REPO_DIR / 'requirements.txt'
    if req_file.exists():
        try:
            pip_env = dict(os.environ)
            pip_env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
            pip = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-r', str(req_file), '--quiet'],
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                stdin=subprocess.DEVNULL,
                env=pip_env,
                timeout=300,
            )
            if pip.returncode != 0:
                err = (pip.stderr or '').strip() or (pip.stdout or '').strip() or 'unknown'
                # Pull already landed, so the old code is gone. We surface the
                # pip failure prominently; user may need to run pip manually.
                _write_result(False, f"Code updated but pip install failed: {err}")
                _clear_pending()
                return
        except subprocess.TimeoutExpired:
            _write_result(False, "pip install timed out after 300s. Dependencies may be stale.")
            _clear_pending()
            return
        except Exception as e:
            _write_result(False, f"pip install raised: {e}. Dependencies may be stale.")
            _clear_pending()
            return

    # Read the new version that just landed.
    new_version = '?'
    try:
        new_version = (REPO_DIR / 'VERSION').read_text(encoding='utf-8').strip()
    except Exception:
        pass

    _write_result(
        True,
        f"Updated {from_version or '?'} → {new_version}",
        from_version=from_version,
        to_version=new_version,
    )
    _clear_pending()


# ─── Updater class (lifecycle + UI-facing methods) ──────────────────────────

class Updater:
    def __init__(self):
        self.current_version = self._read_local_version()
        self.latest_version = None
        self.update_available = False
        self.last_check = 0
        self.checking = False
        self._check_thread = None
        self._bg_thread = None
        # Lock serializes do_update so two clicks / two tabs don't double-fire.
        self._update_lock = threading.Lock()
        # Check git availability once at startup; surfaces cleanly in status().
        self.git_available = self._detect_git_available()
        self.branch = self._detect_branch()
        self.is_fork = self._detect_fork()
        # Target SHA for the currently-advertised `latest_version`. Set when
        # check_for_update finds an update, used by pre-flight to verify
        # upstream hasn't moved between check time and click time.
        self._target_sha = None

    def _read_local_version(self):
        try:
            return VERSION_FILE.read_text(encoding='utf-8').strip()
        except Exception:
            return '?'

    def _detect_git_available(self):
        """Returns True if the `git` binary is callable. False on 'git not on
        PATH' (common on Windows). Stored for status() so the UI can say
        'Install git' instead of lying about the branch."""
        try:
            result = subprocess.run(
                ['git', '--version'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        except Exception:
            return False

    def _detect_branch(self):
        if not self.git_available:
            return 'main'
        try:
            result = _run_git(['rev-parse', '--abbrev-ref', 'HEAD'], timeout=5)
            if result.returncode == 0:
                return result.stdout.strip() or 'main'
        except Exception:
            pass
        return 'main'

    def _detect_fork(self):
        if not self.git_available:
            return False
        try:
            result = _run_git(['remote', 'get-url', 'origin'], timeout=5)
            if result.returncode == 0:
                slug = _parse_github_slug(result.stdout.strip())
                if slug is None:
                    # Non-GitHub origin or unparseable — safer to treat as fork
                    # so we don't auto-pull from mystery remotes.
                    return True
                return slug != GITHUB_REPO.lower()
        except Exception:
            pass
        return False

    def has_git(self):
        """True if this is a git repo AND git is installed. Two separate
        conditions — the UI currently only needs the combined view."""
        if not self.git_available:
            return False
        git_dir = REPO_DIR / '.git'
        return git_dir.exists()

    # ─── Version check ──────────────────────────────────────────────────

    def check_for_update_async(self):
        if self.checking or (self._check_thread and self._check_thread.is_alive()):
            return
        now = time.time()
        if self.last_check and (now - self.last_check) < 300:
            return
        self._check_thread = threading.Thread(
            target=self._check_sync, daemon=True, name='updater-check'
        )
        self._check_thread.start()

    def _check_sync(self):
        try:
            self.check_for_update(force=False)
        except Exception as e:
            logger.warning(f"Background update check failed: {e}")

    def check_for_update(self, force=False):
        """Check GitHub for a newer version. Returns status dict. BLOCKING."""
        if self.checking:
            return self.status()

        now = time.time()
        if not force and self.last_check and (now - self.last_check) < 300:
            return self.status()

        self.checking = True
        try:
            # Always check official repo — branch-aware
            candidate_branches = [self.branch]
            if self.branch != 'main':
                candidate_branches.append('main')  # fall back if branch not on official
            for br in candidate_branches:
                # Contents-API-first fetch — raw.githubusercontent lags pushes
                # by ~5 min, which read as "no updates" right after a release.
                text = fetch_github_file(GITHUB_REPO, br, 'VERSION')
                if text is None or not text.strip():
                    logger.warning(f"Version check failed for branch '{br}'")
                    continue
                self.latest_version = text.strip()
                self.update_available = is_newer(self.latest_version, self.current_version)
                self.last_check = now
                # Record the target SHA at check time so pre-flight can
                # refuse if upstream moves before the user clicks Update.
                if self.update_available:
                    self._target_sha = self._fetch_remote_sha(br)
                    logger.info(f"Update available: {self.current_version} -> {self.latest_version}")
                else:
                    self._target_sha = None
                break
        finally:
            self.checking = False

        return self.status()

    def _fetch_remote_sha(self, branch):
        """Fetch the latest commit SHA for a branch via GitHub's API. Returns
        the SHA string or None. Used by pre-flight to verify upstream hasn't
        force-pushed between check and click."""
        try:
            resp = requests.get(
                f'https://api.github.com/repos/{GITHUB_REPO}/commits/{branch}',
                headers={'Accept': 'application/vnd.github.sha'},
                timeout=10,
            )
            if resp.status_code == 200:
                sha = resp.text.strip()
                # Sanity check — should be a 40-char hex string
                if re.fullmatch(r'[0-9a-f]{7,40}', sha):
                    return sha.lower()
        except Exception as e:
            logger.debug(f"Remote SHA fetch failed: {e}")
        return None

    # ─── Status ──────────────────────────────────────────────────────────

    def status(self):
        return {
            'current': self.current_version,
            'latest': self.latest_version,
            'available': self.update_available,
            'has_git': self.has_git(),
            'git_available': self.git_available,
            'last_check': self.last_check,
            'branch': self.branch,
            'is_fork': self.is_fork,
            'blocked_branch': self.branch in _BLOCKED_BRANCHES,
            'pending_update': PENDING_UPDATE_FILE.exists(),
        }

    # ─── Pre-flight + schedule ──────────────────────────────────────────

    def _preflight_check(self):
        """Return (ok, message) for whether an update is safe to schedule.

        Philosophy: if ANYTHING looks weird, refuse with a specific, actionable
        message. We'd rather a user click Update a second time than pull into
        a broken state silently.
        """
        if not self.git_available:
            return False, "Git is not installed or not on PATH. Install git, restart Sapphire, and try again."
        if not self.has_git():
            return False, "This install isn't a git repository. Download the latest release from GitHub."
        if self.is_fork:
            return False, "Fork detected — pull updates from upstream manually."
        if self.branch in _BLOCKED_BRANCHES:
            return False, (f"You're on the '{self.branch}' branch. Dev-like branches don't "
                           "auto-update; use `git pull` manually when you're ready.")

        # Working-tree state: uncommitted changes + mid-operation markers
        try:
            status = _run_git(['status', '--porcelain=v2', '--branch'], timeout=10)
        except Exception as e:
            return False, f"git status failed: {e}"
        if status.returncode != 0:
            err = (status.stderr or '').strip() or 'unknown error'
            return False, f"git status failed: {err}"

        dirty = []
        detached = False
        for line in status.stdout.splitlines():
            if line.startswith('# branch.head'):
                head = line.split(' ', 2)[-1].strip()
                if head == '(detached)':
                    detached = True
            elif line and not line.startswith('#'):
                # Porcelain v2 entry lines never start with '#'; last field is path
                parts = line.split()
                if parts:
                    dirty.append(parts[-1])
        if detached:
            return False, "Repository is in detached-HEAD state. Check out a branch first."

        # Mid-operation markers (rebase/merge/cherry-pick/bisect in progress)
        git_dir = REPO_DIR / '.git'
        for marker, label in [
            ('MERGE_HEAD', 'merge'),
            ('REBASE_HEAD', 'rebase'),
            ('rebase-apply', 'rebase'),
            ('rebase-merge', 'rebase'),
            ('CHERRY_PICK_HEAD', 'cherry-pick'),
            ('BISECT_LOG', 'bisect'),
        ]:
            if (git_dir / marker).exists():
                return False, f"Repository is mid-{label}. Finish or abort that first."

        if dirty:
            sample = ', '.join(dirty[:3])
            more = '' if len(dirty) <= 3 else f' (and {len(dirty) - 3} more)'
            return False, (f"Working tree has uncommitted changes: {sample}{more}. "
                           "Commit or stash your work before updating.")

        # Disk space
        try:
            free_mb = shutil.disk_usage(str(REPO_DIR)).free / (1024 * 1024)
            if free_mb < _MIN_FREE_MB:
                return False, f"Low disk space ({free_mb:.0f} MB free). Need at least {_MIN_FREE_MB} MB."
        except Exception:
            pass  # shutil.disk_usage can fail on exotic filesystems; don't block on that

        # Verify upstream hasn't force-pushed between check and click. We do a
        # lightweight fetch first so local refs know about origin HEAD.
        try:
            fetch = _run_git(['fetch', 'origin', self.branch], timeout=30)
        except subprocess.TimeoutExpired:
            _clear_index_lock()
            return False, "Couldn't reach upstream (fetch timed out). Check your network and retry."
        except Exception as e:
            return False, f"fetch failed: {e}"
        if fetch.returncode != 0:
            err = (fetch.stderr or '').strip() or 'unknown'
            return False, f"Couldn't reach upstream: {err}"

        try:
            rev = _run_git(['rev-parse', f'origin/{self.branch}'], timeout=5)
            remote_sha = rev.stdout.strip().lower() if rev.returncode == 0 else None
        except Exception:
            remote_sha = None

        if self._target_sha and remote_sha:
            # Target SHA from GitHub API may be longer/shorter; compare prefix.
            t, r = self._target_sha, remote_sha
            min_len = min(len(t), len(r))
            if t[:min_len] != r[:min_len]:
                return False, ("Upstream moved since the update check. "
                               "Refresh 'Check for updates' and try again.")

        return True, "ok"

    def do_update(self):
        """Schedule a deferred update. Does NOT pull here — the pull runs in
        main.py before the next sapphire.py spawn.

        Flow:
          1. Acquire lock (reject if another update is already scheduled)
          2. Run pre-flight; refuse with specific message if anything's weird
          3. Create backup; REFUSE if backup fails (no silent update)
          4. Write pending-update marker
          5. Return success; caller triggers restart

        Returns (success: bool, message: str).
        """
        # Non-blocking acquire — concurrent calls reject cleanly instead of
        # queueing and creating two backups / two pulls.
        if not self._update_lock.acquire(blocking=False):
            return False, "An update is already being scheduled. Please wait."
        try:
            if PENDING_UPDATE_FILE.exists():
                return False, "An update is already pending a restart."

            ok, msg = self._preflight_check()
            if not ok:
                return False, msg

            # Backup must succeed — if we can't back up, we don't update.
            try:
                from core.backup import backup_manager
                result = backup_manager.create_backup('pre_update')
            except Exception as e:
                return False, f"Pre-update backup raised: {e}. Refusing to update."
            if not result:
                return False, "Pre-update backup failed. Refusing to update."

            # Resolve target SHA once more, right before writing the marker, so
            # the deferred pull lands on the exact commit we advertised.
            target_sha = None
            try:
                rev = _run_git(['rev-parse', f'origin/{self.branch}'], timeout=5)
                if rev.returncode == 0:
                    target_sha = rev.stdout.strip().lower()
            except Exception:
                pass

            try:
                PENDING_UPDATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = PENDING_UPDATE_FILE.with_suffix('.json.tmp')
                tmp.write_text(json.dumps({
                    'branch': self.branch,
                    'target_sha': target_sha,
                    'requested_at': time.time(),
                    'from_version': self.current_version,
                    'to_version': self.latest_version,
                }), encoding='utf-8')
                tmp.replace(PENDING_UPDATE_FILE)
            except Exception as e:
                return False, f"Could not schedule update: {e}"

            return True, (f"Update scheduled: {self.current_version} → {self.latest_version or '?'}. "
                          "Restarting to apply.")
        finally:
            self._update_lock.release()

    # ─── Background checker ─────────────────────────────────────────────

    def start_background_checker(self):
        def _checker():
            time.sleep(30)
            while True:
                try:
                    self.check_for_update()
                except Exception as e:
                    logger.warning(f"Background version check failed: {e}")
                time.sleep(CHECK_INTERVAL)

        self._bg_thread = threading.Thread(
            target=_checker, daemon=True, name='updater'
        )
        self._bg_thread.start()
        logger.info("Background update checker started (24h interval)")


# Singleton
updater = Updater()
