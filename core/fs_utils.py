"""Small filesystem helpers shared by writers that atomic-replace files.

Import-light on purpose (stdlib only): migration.py runs before the
prompt_manager singleton loads, so this module must not pull in core state.
"""
import logging
import os
import shutil
import stat
import time
from pathlib import Path, PurePath
from urllib.parse import quote

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


def ro_uri(path) -> str:
    """The `file:` URI that opens a SQLite store read-only:
    sqlite3.connect(ro_uri(p), uri=True). Path.as_uri() puts a UNC host
    (\\\\server\\share\\...) in the URI authority, which sqlite refuses
    ("invalid uri authority") unless built with SQLITE_ALLOW_URI_AUTHORITY,
    so an install on a Windows share could never read its own stores. Here
    the authority stays empty and //server/share rides in the path, which
    the parser accepts. '#', '?' and '%' in the install path are
    percent-encoded: a bare f'file:{p}' truncated at the first one and
    opened (created!) a different file read-write."""
    p = path if isinstance(path, PurePath) else Path(path)
    if isinstance(p, Path):
        p = p.absolute()
    s = p.as_posix()
    if not s.startswith('/'):           # a Windows drive: /C:/...
        s = '/' + s
    return 'file://' + quote(s, safe='/:') + '?mode=ro'
