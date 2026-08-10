"""Small filesystem helpers shared by writers that atomic-replace files.

Import-light on purpose (stdlib only): migration.py runs before the
prompt_manager singleton loads, so this module must not pull in core state.
"""
import time
from pathlib import Path


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
