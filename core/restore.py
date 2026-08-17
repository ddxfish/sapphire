# core/restore.py — apply a staged backup over user/ at boot, BEFORE any DB opens.
#
# Flow: the web endpoint validates + decrypts + stages a plaintext .tar.gz into
# user_restore/ and writes pending_restore.json, then requests a restart. main.py
# calls apply_pending_restore() BEFORE re-spawning sapphire.py — the one window
# where nothing holds user/ open — so the swap is OFFLINE and safe. The current
# user/ is preserved as user.old for rollback; a broken restore never loops.
import json
import logging
import shutil
import tarfile
import time
from pathlib import Path

from core.fs_utils import replace_with_retry, rmtree_robust

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent.parent
RESTORE_DIR = BASE / "user_restore"
MARKER = RESTORE_DIR / "pending_restore.json"
STAGED = RESTORE_DIR / "pending.tar.gz"
USER = BASE / "user"
USER_NEW = BASE / "user.new"
USER_OLD = BASE / "user.old"
USER_OLD_PREV = BASE / "user.old.prev"
# Outcome of the last restore, written by apply_pending_restore() at boot and
# surfaced to the UI on reconnect. Lives in user_restore/ (survives the swap,
# gitignored, never in a backup).
RESULT = RESTORE_DIR / "last_restore_result.json"


def _write_result(ok: bool, source: str = "", error: str = ""):
    try:
        RESTORE_DIR.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps({
            "ok": bool(ok), "source": source, "error": error,
            "ts": time.strftime("%Y-%m-%d_%H%M%S"),
        }), encoding="utf-8")
    except Exception:
        pass  # feedback is best-effort; never let it break the boot


def read_restore_result(clear: bool = False):
    """Return the last restore outcome dict, or None. clear=True removes it."""
    if not RESULT.exists():
        return None
    try:
        data = json.loads(RESULT.read_text(encoding="utf-8"))
    except Exception:
        RESULT.unlink(missing_ok=True)
        return None
    if clear:
        RESULT.unlink(missing_ok=True)
    return data


def validate_tar(path):
    """Confirm `path` is a gzip tar rooted at `user/`, with no path-traversal in
    member NAMES (the safety floor for the trusted/fully-trusted extract). Returns
    sorted top-level entries. Link/device safety is enforced at extract time by the
    tarfile filter (strict for uploads). Raises ValueError / tarfile.TarError."""
    import sys
    _WIN_RESERVED = ({'con', 'prn', 'aux', 'nul'}
                     | {f'com{i}' for i in range(1, 10)}
                     | {f'lpt{i}' for i in range(1, 10)})
    with tarfile.open(path, "r:gz") as t:
        names = t.getnames()
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise ValueError(f"unsafe path in archive: {n}")
        # Windows shapes the POSIX check never sees: drive-relative names
        # ('D:evil') escape containment, backslash '..' segments dodge the
        # Path.parts test above.
        if (len(n) > 1 and n[1] == ':') or '\\..' in n or n.startswith('..\\'):
            raise ValueError(f"unsafe path in archive: {n}")
        # Reserved device names (CON, NUL, COM1…) abort a Windows extract
        # mid-way with a cryptic OSError — refuse up front with a real
        # message. Linux restores of the same archive still work.
        if sys.platform == 'win32':
            for part in Path(n).parts:
                if part.split('.')[0].lower() in _WIN_RESERVED:
                    raise ValueError(
                        f"archive contains a Windows-reserved filename "
                        f"('{n}') — restore it on Linux, rename that file, "
                        f"and re-create the backup")
    roots = sorted({n.split("/", 1)[0] for n in names if n})
    if "user" not in roots:
        raise ValueError("archive has no top-level user/ folder")
    return roots


def request_restore(staged_tar, source: str = "", trusted: bool = False) -> None:
    """Record a pending restore — `staged_tar` is an already-validated plaintext
    .tar.gz; it's moved into place and a marker written for main.py to apply.
    `trusted` = the user's own backup (restore faithfully, symlinks and all);
    False = an uploaded archive (extract under the strict 'data' filter)."""
    RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        RESTORE_DIR.chmod(0o700)   # holds plaintext staging — owner-only
    except OSError:
        pass
    staged_tar = Path(staged_tar)
    if staged_tar != STAGED:
        if STAGED.exists():
            STAGED.unlink()
        shutil.move(str(staged_tar), str(STAGED))
    try:
        STAGED.chmod(0o600)
    except OSError:
        pass
    MARKER.write_text(json.dumps({
        "staged": str(STAGED), "source": source, "trusted": bool(trusted),
        "ts": time.strftime("%Y-%m-%d_%H%M%S"),
    }), encoding="utf-8")


def _extract(tar, dest, trusted):
    # Extract regular files + dirs ONLY — skip symlinks/hardlinks/devices. They
    # leak target paths, need admin to recreate on Windows, and aren't data. New
    # backups no longer capture them; old/uploaded archives still might.
    members = [m for m in tar.getmembers() if m.isfile() or m.isdir()]
    filt = "fully_trusted" if trusted else "data"
    try:
        tar.extractall(dest, members=members, filter=filt)
    except TypeError:
        tar.extractall(dest, members=members)


def apply_pending_restore(log=print):
    """Called by main.py before spawning sapphire.py. No-op without a marker.
    Returns True (applied) / False (failed, rolled back) / None (nothing to do)."""
    if not MARKER.exists():
        return None
    try:
        info = json.loads(MARKER.read_text(encoding="utf-8"))
    except Exception:
        MARKER.unlink(missing_ok=True)
        return None

    source = info.get("source", "")
    staged = Path(info.get("staged") or STAGED)
    if not staged.exists():
        log("[Restore] staged backup missing — aborting")
        MARKER.unlink(missing_ok=True)
        _write_result(False, source, "staged backup file was missing")
        return False

    try:
        if USER_NEW.exists():
            rmtree_robust(USER_NEW)
        USER_NEW.mkdir(parents=True)
        try:
            USER_NEW.chmod(0o700)
        except OSError:
            pass
        with tarfile.open(staged, "r:gz") as t:
            _extract(t, USER_NEW, bool(info.get("trusted")))
        new_user = USER_NEW / "user"
        if not new_user.is_dir():
            raise ValueError("archive has no user/ root after extract")

        # Snapshot current user/ → user.old (rollback). Rotate the previous
        # user.old → user.old.prev first, so restoring twice in a row doesn't
        # silently destroy the first rollback point (the panic-recovery footgun).
        # Windows: a single open handle anywhere under these trees (Explorer,
        # AV scan, an editor) fails a bare rename/rmtree — robust rmtree
        # clears read-only bits, and the retried replaces outlast transient
        # holders. A real lock still aborts, with the cause named.
        if USER_OLD.exists():
            if USER_OLD_PREV.exists():
                rmtree_robust(USER_OLD_PREV)
            replace_with_retry(USER_OLD, USER_OLD_PREV)
        if USER.exists():
            replace_with_retry(USER, USER_OLD)
            try:
                USER_OLD.chmod(0o700)   # full plaintext copy of private data — owner-only
            except OSError:
                pass
        replace_with_retry(new_user, USER)

        shutil.rmtree(USER_NEW, ignore_errors=True)
        MARKER.unlink(missing_ok=True)
        try:
            staged.unlink()
        except OSError:
            pass
        log("[Restore] applied — previous user/ preserved at user.old (delete it once you're happy)")
        _write_result(True, source)
        return True
    except Exception as e:
        # If we moved user aside but didn't finish, put it back.
        if USER_OLD.exists() and not USER.exists():
            try:
                replace_with_retry(USER_OLD, USER)
            except Exception:
                pass
        if USER_NEW.exists():
            shutil.rmtree(USER_NEW, ignore_errors=True)
        MARKER.unlink(missing_ok=True)  # never loop-retry a broken restore
        hint = (" (a PermissionError here usually means something on Windows "
                "holds a file open under user/ — close Explorer/editors and retry)"
                if isinstance(e, PermissionError) else "")
        log(f"[Restore] FAILED — kept existing user/: {e}{hint}")
        _write_result(False, source, str(e))
        return False
