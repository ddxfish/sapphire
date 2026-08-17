"""Core install integrity — detect (and help repair) a tree that doesn't match its
shipped manifest: partial updates, half-applied `git pull`, corruption, stray edits.

UNSIGNED SHA256 by design — this is a file-consistency checker, not a security boundary.
A mismatch means "your install is inconsistent," not "an attacker is here."

- build_manifest(): hash every shipped file via `git ls-files`. Generation + the staleness
  test only — needs git.
- verify(): read core_manifest.json, hash the LISTED files, report missing/mismatched.
  NO git — works identically on a git clone or a zip/release install.
- repair(): restore only the offending files (git checkout) + per-file status + re-verify.

The whole tree (677 files / 8.5 MB) hashes in ~22 ms, so everything here runs synchronously,
on demand and at boot, with no caching or backgrounding.
"""
import hashlib
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "core_manifest.json"

# user/ is user data, never part of core integrity. The manifest never lists itself.
EXCLUDE_PREFIXES = ("user/",)
EXCLUDE_FILES = {"core_manifest.json"}


def _hash_file(path: Path) -> str:
    # CRLF-normalized like plugin signing (plugin_verify.py) — Windows checkouts
    # with autocrlf=true would otherwise mismatch every text file.
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _tracked_files():
    """Shipped fileset via `git ls-files` (generation/test only; needs git)."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(ROOT),
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, stdin=subprocess.DEVNULL,
    ).stdout
    for line in out.splitlines():
        p = line.strip()
        if p and p not in EXCLUDE_FILES and not any(p.startswith(x) for x in EXCLUDE_PREFIXES):
            yield p


def build_manifest() -> dict:
    """Hash the shipped tree. Used by tools/generate_core_manifest.py and the staleness test."""
    version = "?"
    vf = ROOT / "VERSION"
    if vf.exists():
        version = vf.read_text(encoding="utf-8").strip()
    files = {}
    for rel in sorted(_tracked_files()):
        fp = ROOT / rel
        if fp.is_file():
            files[rel] = _hash_file(fp)
    return {"version": version, "files": files}


def manifest_json(manifest: dict) -> str:
    """Canonical serialization — deterministic so regen is a no-op unless files changed."""
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def load_manifest():
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def verify() -> dict:
    """Hash the files LISTED in the manifest, compare. No git. Returns a report dict:
    {ok, available, version, total, matched, missing[], mismatched[]}."""
    m = load_manifest()
    if not m or not isinstance(m.get("files"), dict):
        return {"ok": False, "available": False, "version": None, "total": 0,
                "matched": 0, "missing": [], "mismatched": [],
                "error": "core_manifest.json missing or unreadable"}
    files = m["files"]
    missing, mismatched, matched = [], [], 0
    for rel, expected in files.items():
        fp = ROOT / rel
        if not fp.is_file():
            missing.append(rel)
            continue
        try:
            actual = _hash_file(fp)
        except Exception:
            missing.append(rel)
            continue
        if actual == expected:
            matched += 1
        else:
            mismatched.append(rel)
    ok = not missing and not mismatched
    return {"ok": ok, "available": True, "version": m.get("version"),
            "total": len(files), "matched": matched,
            "missing": sorted(missing), "mismatched": sorted(mismatched)}


def log_boot_status():
    """Run verify() at boot and log the outcome. A skew logs at WARNING so it surfaces
    in journalctl even from the launcher (logging's lastResort emits WARNING+ to stderr,
    unlike main.py's block-buffered print). No-op if no manifest is shipped."""
    try:
        r = verify()
        if not r.get("available"):
            return
        if r["ok"]:
            logger.info(f"[INTEGRITY] ok - {r['matched']}/{r['total']} files match v{r.get('version')}")
        else:
            logger.warning(
                f"[INTEGRITY] install does NOT match v{r.get('version')}: "
                f"{len(r['mismatched'])} modified, {len(r['missing'])} missing. "
                f"Open Settings > System > Verify to see + Repair."
            )
    except Exception as e:
        logger.warning(f"[INTEGRITY] boot check raised: {e}")


def _is_git_install() -> bool:
    return (ROOT / ".git").exists()


def _repair_git(rel: str):
    """Restore one file to its committed (HEAD) version. Fixes local edits + half-applied
    pulls where HEAD advanced but the working tree is stale. Returns (ok, detail)."""
    try:
        r = subprocess.run(
            ["git", "checkout", "HEAD", "--", rel], cwd=str(ROOT),
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, stdin=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return True, "restored from HEAD"
        return False, ((r.stderr or r.stdout or "git checkout failed").strip()[:200])
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _dirty_files(rels):
    """Subset of `rels` carrying UNCOMMITTED local changes (index or worktree),
    per `git status --porcelain`. Fail-SAFE: any git hiccup reports ALL files
    dirty, so repair refuses them — 'refused to fix' beats 'silently destroyed
    in-progress work'."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--"] + list(rels), cwd=str(ROOT),
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            return set(rels)
        dirty = set()
        for line in r.stdout.splitlines():
            p = line[3:].strip().strip('"') if len(line) > 3 else ""
            if " -> " in p:                      # rename entries: "old -> new"
                p = p.split(" -> ", 1)[1].strip('"')
            if p:
                dirty.add(p)
        return dirty & set(rels)
    except Exception:
        return set(rels)


def repair(targets=None, force=False) -> dict:
    """Restore only the offending files, with per-file status + a final re-verify.

    NEVER touches a file with uncommitted local changes unless force=True —
    repair fixes drift and corruption, it does not eat in-progress work. This
    guard is in repair() itself, not the callers, so NO invoker (route, test,
    tool, future code) can mass-revert uncommitted edits. (2026-07-05 incident:
    a test called repair() on a dev tree and git-reverted staged work.)

    git installs: `git checkout HEAD -- <file>`. If files are STILL mismatched after
    (HEAD itself is behind), the answer is to re-run the updater, surfaced in the message.
    Non-git installs: cannot auto-repair in v1 — reported per file."""
    before = verify()
    bad = list(targets) if targets is not None else (before["missing"] + before["mismatched"])
    if not bad:
        return {"repaired": [], "failed": [], "skipped": [], "reverify": before,
                "message": "Nothing to repair — install matches the manifest."}

    git = _is_git_install()
    skipped = []
    if git and not force:
        dirty = _dirty_files(bad)
        if dirty:
            skipped = [{"file": rel, "detail": "uncommitted local edits — refusing to overwrite"}
                       for rel in sorted(dirty)]
            bad = [rel for rel in bad if rel not in dirty]

    # Every repair run is loud — file counts up front, before anything is touched.
    logger.warning(f"[INTEGRITY] repair invoked: restoring {len(bad)} file(s)"
                   + (f", refusing {len(skipped)} with uncommitted local edits" if skipped else ""))

    repaired, failed = [], []
    for rel in bad:
        if git:
            ok, detail = _repair_git(rel)
        else:
            ok, detail = False, "non-git install — re-download the release from GitHub"
        (repaired if ok else failed).append({"file": rel, "detail": detail})

    after = verify()
    msg = f"Repaired {len(repaired)}, failed {len(failed)}"
    if skipped:
        msg += f", refused {len(skipped)} (uncommitted local edits)"
    msg += f". Now {after['matched']}/{after['total']} match."
    if not after["ok"] and git and not failed and not skipped:
        msg += " Some files still differ — your local commit may be behind; re-run the updater."
    logger.info(f"[INTEGRITY] repair: {msg}")
    return {"repaired": repaired, "failed": failed, "skipped": skipped, "reverify": after, "message": msg}
