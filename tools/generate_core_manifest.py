#!/usr/bin/env python3
"""Generate core_manifest.json — SHA256 of every shipped file, for the integrity check.

Run this as the LAST step before commit/push (it hashes the CURRENT state), then commit the
regenerated manifest alongside your changes. Same ritual as re-signing a plugin.

The integrity check (core/integrity.py) reads this manifest at boot + on demand. A stale
manifest produces false "mismatch" alarms for users, so tests/test_core_manifest_current.py
fails until you regenerate — a red light before you push.

    python tools/generate_core_manifest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.integrity import build_manifest, manifest_json, MANIFEST_PATH


def main():
    manifest = build_manifest()
    MANIFEST_PATH.write_text(manifest_json(manifest), encoding="utf-8")
    print(f"Wrote {MANIFEST_PATH.name}: {len(manifest['files'])} files, version {manifest['version']}")


if __name__ == "__main__":
    main()
