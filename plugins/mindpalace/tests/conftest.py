"""Standalone test config for the mindpalace plugin.

Makes `pytest user/plugins/mindpalace/tests/` (or `plugins/mindpalace/tests/`
after the plugin moves back) resolve two things regardless of on-disk location:
  - `core.*` — via the repo root (walked up from this file until a `core/` dir).
  - `plugins.mindpalace.*` — via the dir that CONTAINS the plugin's `plugins/`
    namespace (plugins/ has no __init__, so adding this parent lets the namespace
    package include this plugin, whether it lives in plugins/ or user/plugins/).

Travels with the plugin. The main suite skips it (norecursedirs = user).
"""
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent          # .../mindpalace
_repo_root = PLUGIN_DIR
while _repo_root != _repo_root.parent and not (_repo_root / "core").is_dir():
    _repo_root = _repo_root.parent
# Parent of the plugin's `plugins/` dir (e.g. <repo>/user or <repo>) — putting it
# on the path makes `plugins.mindpalace` a resolvable namespace-package member.
_plugins_parent = PLUGIN_DIR.parent.parent

for _p in (str(_repo_root), str(_plugins_parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─── Library isolation (v3, 2026-07-17) ──────────────────────────────────────
# save/search layer='knowledge' reroutes into tools/library.py, so ANY test
# touching knowledge reaches the library module. Reset its globals per test
# (get_db_path caches), point it at tmp, and never let it near the real
# embedder. Tests that want retrieval override _embedder with their own fake.
import pytest


@pytest.fixture(autouse=True)
def _library_isolation(monkeypatch, tmp_path):
    from plugins.mindpalace.tools import library

    class _NoEmbedder:
        provider_id = 'test:none'
        available = False

        def embed(self, texts, prefix=''):
            return None

    class _NoVision:
        provider_id = 'test:novision'
        available = False

        def embed_paths(self, paths):
            return [None] * len(paths)

    monkeypatch.setattr(library, "_db_path", tmp_path / "library.db",
                        raising=False)
    monkeypatch.setattr(library, "_db_initialized", False, raising=False)
    monkeypatch.setattr(library, "_embedder", lambda: _NoEmbedder())
    monkeypatch.setattr(library, "_vision", lambda: _NoVision())
    monkeypatch.setattr(library, "ensure_worker", lambda: None)
    # Watch scans spawn threads — tests drive scan_folder() by hand.
    monkeypatch.setattr(library, "ensure_scan_async",
                        lambda min_interval=0: False)
    library._matrix_cache.clear()
