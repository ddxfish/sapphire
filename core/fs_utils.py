"""Small filesystem helpers shared by writers that atomic-replace files.

Import-light on purpose (stdlib only): migration.py runs before the
prompt_manager singleton loads, so this module must not pull in core state.
"""
import logging
import os
import shutil
import stat
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def rmtree_robust(path):
    """shutil.rmtree that survives Windows read-only files.

    Plain `shutil.rmtree` crashes on Windows the first time it hits a
    read-only file (.pyc, git-set permissions, AV-locked caches). The
    onerror handler clears the read-only bit and retries the single
    delete — if that still fails, we swallow the exception so a broken
    file doesn't abort the whole removal and leave a half-deleted tree.
    """
    def _on_error(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception as e:
            logger.warning(f"rmtree could not remove {p}: {e}")
    shutil.rmtree(path, onerror=_on_error)


def replace_with_retry(tmp_path: Path, target_path: Path,
                       attempts: int = 5, delay: float = 0.1):
    """Path.replace with a short retry loop.

    Windows raises PermissionError on replace() while another process holds
    the target open (AV scanner, editor, search indexer). A few 100ms
    retries outlast the common transient holders; the final attempt
    re-raises so callers still see a real lock. No-op cost on POSIX.
    """
    for _ in range(attempts - 1):
        try:
            tmp_path.replace(target_path)
            return
        except PermissionError:
            time.sleep(delay)
    tmp_path.replace(target_path)
