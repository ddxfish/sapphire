"""Standalone test config for the mindpalace plugin.

Makes `pytest <wherever>/mindpalace/tests/` resolve `core.*` and
`plugins.mindpalace.*` regardless of on-disk location: both come from the repo
root (walked up from this file to the dir holding sapphire.py). The
`plugins.<name>` namespace is the root-level plugins.py, so the band never
matters. The old "parent of the plugin's plugins/ dir" trick is gone: with the
system band under core/ it would have put core/ itself on sys.path.

Travels with the plugin.
"""
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).absolute().parent.parent         # .../mindpalace
# .absolute(), never .resolve() — the symlinked-plugin house rule.
_repo_root = PLUGIN_DIR
while _repo_root != _repo_root.parent and not (_repo_root / "sapphire.py").is_file():
    _repo_root = _repo_root.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


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
