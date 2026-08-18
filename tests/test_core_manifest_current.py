"""Red light before push: core_manifest.json must match the CURRENT tree.

build_manifest() hashes every git-tracked file (working-tree content) and
manifest_json() is canonical, so a byte-equal compare against the file on
disk is exact. If this fails, the manifest is stale and every user pulling
the push boots into false [INTEGRITY] alarms (233 of them on 2026-08-18,
caught by hand -- this test exists so a human never has to).

Fix: git add any new files, then `python tools/generate_core_manifest.py`,
then commit the regenerated manifest alongside your changes.

Read-only by design: builds hashes in memory and compares. Never writes,
never calls repair().
"""
import json
import subprocess

import pytest

from core.integrity import ROOT, MANIFEST_PATH, build_manifest, manifest_json


def _git_available():
    if not (ROOT / ".git").exists():
        return False
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True,
                       stdin=subprocess.DEVNULL, timeout=10)
        return True
    except Exception:
        return False


def test_core_manifest_is_current():
    if not _git_available():
        pytest.skip("not a git checkout - manifest generation needs git ls-files")
    assert MANIFEST_PATH.is_file(), \
        "core_manifest.json missing - run: python tools/generate_core_manifest.py"
    current = build_manifest()
    on_disk_text = MANIFEST_PATH.read_text(encoding="utf-8")
    if manifest_json(current) == on_disk_text:
        return

    # Stale -- say exactly what drifted so the fix is obvious.
    try:
        on_disk_files = json.loads(on_disk_text).get("files", {})
        on_disk_version = json.loads(on_disk_text).get("version")
    except Exception:
        on_disk_files, on_disk_version = {}, None
    new = current["files"]
    added = sorted(set(new) - set(on_disk_files))
    removed = sorted(set(on_disk_files) - set(new))
    changed = sorted(k for k in set(on_disk_files) & set(new)
                     if on_disk_files[k] != new[k])
    detail = []
    if on_disk_version != current["version"]:
        detail.append(f"version: {on_disk_version} -> {current['version']}")
    for label, items in (("new files", added), ("removed", removed),
                         ("changed", changed)):
        if items:
            shown = ", ".join(items[:5]) + ("..." if len(items) > 5 else "")
            detail.append(f"{label}: {len(items)} ({shown})")
    pytest.fail(
        "core_manifest.json is STALE - users would boot into false "
        "[INTEGRITY] alarms.\n" + "\n".join(detail) + "\n"
        "Fix: git add any new files, then run "
        "`python tools/generate_core_manifest.py` and commit the manifest "
        "alongside your changes.")
