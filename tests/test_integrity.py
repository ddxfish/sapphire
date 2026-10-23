"""Core integrity checker (core/integrity.py) + the manifest-staleness guard.

The checker hashes the files LISTED in core_manifest.json and reports missing/mismatched
files — catching partial updates, half-applied pulls, corruption. Unsigned SHA256 by design.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import integrity


def _release_branch() -> bool:
    """The release guards below protect main pushes — on dev the manifest is one
    release behind BY DESIGN, so they'd be red on every dev run. Non-git installs
    (release zips) DO run them: there the manifest must match the tree."""
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           cwd=str(integrity.ROOT), capture_output=True, text=True,
                           timeout=10, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return True          # no git (zip/release install) → guards apply
        return r.stdout.strip() in ("main", "master")
    except Exception:
        return True


release_guard = pytest.mark.skipif(
    not _release_branch(),
    reason="release-branch guard — the dev manifest lags the tree by design")


@release_guard
def test_verify_clean_tree_is_ok():
    """The committed manifest must verify clean against the real tree."""
    r = integrity.verify()
    assert r["available"] is True
    assert r["ok"] is True, f"unexpected drift: missing={r['missing']} mismatched={r['mismatched']}"
    assert r["matched"] == r["total"] > 0


def test_verify_flags_mismatch_and_missing(tmp_path, monkeypatch):
    real = "VERSION"
    correct = integrity._hash_file(integrity.ROOT / real)
    manifest = {"version": "test", "files": {
        real: correct,                          # present + correct -> matched
        "core/integrity.py": "0" * 64,          # present + wrong hash -> mismatched
        "core/does_not_exist_xyz.py": "0" * 64,  # absent -> missing
    }}
    mpath = tmp_path / "core_manifest.json"
    mpath.write_text(integrity.manifest_json(manifest))
    monkeypatch.setattr(integrity, "MANIFEST_PATH", mpath)

    r = integrity.verify()
    assert r["ok"] is False
    assert "core/integrity.py" in r["mismatched"]
    assert "core/does_not_exist_xyz.py" in r["missing"]
    assert real not in r["mismatched"] and real not in r["missing"]  # the good one passed
    assert r["matched"] == 1


def test_verify_missing_manifest_is_graceful(tmp_path, monkeypatch):
    """A missing/unreadable manifest must report available=False, never crash."""
    monkeypatch.setattr(integrity, "MANIFEST_PATH", tmp_path / "nope.json")
    r = integrity.verify()
    assert r["ok"] is False and r["available"] is False
    assert r["total"] == 0


def test_repair_nothing_to_do_on_clean_tree(tmp_path, monkeypatch):
    """repair() no-ops when the manifest matches. HERMETIC — runs against a tmp
    manifest that genuinely matches, so repair() can never touch the real repo.

    NEVER let repair() see the real manifest from a test: on a dev checkout the
    release manifest mismatches by design, and repair() then git-checkouts every
    listed file — on 2026-07-04 and 2026-07-05 this test (pre-fix) silently
    reverted uncommitted core edits mid-session."""
    real = "VERSION"
    manifest = {"version": "test",
                "files": {real: integrity._hash_file(integrity.ROOT / real)}}
    mpath = tmp_path / "core_manifest.json"
    mpath.write_text(integrity.manifest_json(manifest))
    monkeypatch.setattr(integrity, "MANIFEST_PATH", mpath)

    r = integrity.repair()
    assert r["repaired"] == [] and r["failed"] == [] and r["skipped"] == []
    assert r["reverify"]["ok"] is True


def test_repair_refuses_uncommitted_edits(tmp_path, monkeypatch):
    """The load-bearing guard: files with uncommitted local changes are SKIPPED,
    never checked out, no matter who calls repair(). Hermetic — git access is
    stubbed, so nothing real is read or written."""
    real = "VERSION"
    manifest = {"version": "test", "files": {
        real: "0" * 64,                       # mismatched -> repair candidate
    }}
    mpath = tmp_path / "core_manifest.json"
    mpath.write_text(integrity.manifest_json(manifest))
    monkeypatch.setattr(integrity, "MANIFEST_PATH", mpath)
    monkeypatch.setattr(integrity, "_is_git_install", lambda: True)
    monkeypatch.setattr(integrity, "_dirty_files", lambda rels: set(rels))  # all dirty
    checkouts = []
    monkeypatch.setattr(integrity, "_repair_git",
                        lambda rel: checkouts.append(rel) or (True, "restored"))

    r = integrity.repair()
    assert checkouts == []                    # git checkout NEVER ran
    assert [s["file"] for s in r["skipped"]] == [real]
    assert r["repaired"] == []
    assert "refused 1" in r["message"]


@release_guard
def test_core_manifest_is_current():
    """REGRESSION GUARD: the committed manifest must match the current tracked tree.

    If this is RED, you edited tracked files without regenerating the manifest. Run
    `python tools/generate_core_manifest.py` and commit the result before pushing —
    otherwise users get false 'mismatch' alarms."""
    committed = integrity.load_manifest()
    assert committed is not None, "core_manifest.json missing"
    fresh = integrity.build_manifest()
    assert committed.get("files") == fresh["files"], (
        "core_manifest.json is STALE - run tools/generate_core_manifest.py and commit it"
    )
