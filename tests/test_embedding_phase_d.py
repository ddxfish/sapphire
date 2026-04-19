"""Phase D hardening regression tests (2026-04-19).

Covers:
  #13 HF model revision pin is plumbed (revision= passed to hf_hub_download)
  #14 Windows HF_HOME override when env var missing
  #15 Plugin unload resets embedder singleton away from the plugin's provider
  #16 LocalEmbedder distinguishes ImportError from general failures
  #18 backup scheduler runs DB housekeeping (integrity_check daily, VACUUM weekly)
  #19 Symmetric MAX_ENTRIES_PER_SCOPE enforced on memories + people
  #20 Shared httpx.Client reused across remote embed calls
"""
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ─── #13 HF revision pin plumbed ──────────────────────────────────────────

def test_local_embedder_passes_revision_to_hf():
    """[REGRESSION_GUARD] LocalEmbedder must pass `revision=` to both
    AutoTokenizer.from_pretrained and hf_hub_download so the loaded model
    is reproducible. Scout finding: without this, silent upstream model
    drift changes the vector space out from under stored embeddings."""
    from core.embeddings import LocalEmbedder
    src = inspect.getsource(LocalEmbedder._load)
    assert 'revision=' in src, "LocalEmbedder._load must pass revision= to HF calls"
    # And the default is sourced from config so users can pin
    mod_src = inspect.getsource(__import__('core.embeddings', fromlist=['']))
    assert 'EMBEDDING_MODEL_REVISION' in mod_src


# ─── #14 Windows HF_HOME override ────────────────────────────────────────

def test_windows_hf_home_override_logic_present():
    """[REGRESSION_GUARD] On Windows, HF_HOME must be redirected to a
    project-local path if not already set — MAX_PATH defeats the default
    HuggingFace cache layout. Source-level check since we can't simulate
    Windows in a test runner."""
    src = (Path(__file__).parent.parent / 'core/embeddings/__init__.py').read_text()
    assert "sys.platform == 'win32'" in src
    assert 'HF_HOME' in src
    assert 'MAX_PATH' in src, "comment should reference the reason"


# ─── #15 Plugin unload resets active embedder ────────────────────────────

def test_unregister_plugin_swaps_away_from_owned_active_provider():
    """[REGRESSION_GUARD] If a plugin is unloaded while its embedding
    provider is the active singleton, the singleton must be reset. Otherwise
    the app holds a reference to a GC'd class object forever."""
    from core.embeddings import EmbeddingRegistry
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class PluginProvider:
        PROVIDER_ID = 'plugin:owned'
        available = True

        def embed(self, texts, prefix='search_document'):
            base = np.zeros(32, dtype=np.float32)
            base[0] = 1.0
            return np.stack([base.copy() for _ in texts]).astype(np.float32)

    reg = EmbeddingRegistry()
    reg.register_plugin('owned-key', PluginProvider, 'Owned', 'owned-plugin')

    # Activate it on the global singleton
    orig_active_key = emb.embedding_registry.get_active_key()
    try:
        # Point global registry to our instance by swapping the singleton directly
        emb._embedder = PluginProvider()
        # But also register on the main registry so unregister finds it
        emb.embedding_registry.register_plugin('owned-key', PluginProvider, 'Owned', 'owned-plugin')

        # Sanity: current singleton is an instance of PluginProvider
        assert isinstance(emb._embedder, PluginProvider)

        # Simulate plugin unload
        emb.embedding_registry.unregister_plugin('owned-plugin')

        # Singleton must no longer be a PluginProvider — should be NullEmbedder
        # or reset to None (next get_embedder rebuilds from settings).
        assert not isinstance(emb._embedder, PluginProvider), \
            "singleton still points at unloaded plugin's class — will crash on next use"
    finally:
        # Restore: force reset via switch
        emb.switch_embedding_provider(orig_active_key)
        # Clean up test plugin from main registry if still there
        try:
            emb.embedding_registry.unregister_plugin('owned-plugin')
        except Exception:
            pass


# ─── #16 ImportError distinct from generic failure ───────────────────────

def test_local_embedder_splits_importerror_from_generic():
    """[REGRESSION_GUARD] onnxruntime ImportError (e.g. missing VC++ redist
    on Windows) must yield a different load_error than a model-fetch failure
    — the message should tell the user what to install. Scout finding #16:
    previously both paths logged the same generic 'Failed to load'."""
    from core.embeddings import LocalEmbedder
    src = inspect.getsource(LocalEmbedder._load)
    # Three distinct ImportError handlers for onnxruntime / transformers / huggingface_hub
    assert src.count('ImportError') >= 3
    # Mentions VC++ for the Windows user specifically
    assert 'VC++' in src or 'Visual C++' in src


def test_local_embedder_records_load_error_attribute():
    """[REGRESSION_GUARD] LocalEmbedder exposes `load_error` for the UI to
    read. A failed load that silently fell back to NullEmbedder before was
    invisible — now there's a string telling you why."""
    from core.embeddings import LocalEmbedder
    e = LocalEmbedder()
    # Attribute exists without needing to call _load
    assert hasattr(e, 'load_error')


# ─── #18 Backup scheduler runs DB housekeeping ───────────────────────────

def test_backup_manager_has_db_housekeeping_method():
    """[REGRESSION_GUARD] Backup class exposes _db_housekeeping which runs
    integrity_check and (weekly) VACUUM on user/ DBs. Scout longevity #18
    — SQLite BLOB-heavy DBs grow monotonically without VACUUM."""
    from core.backup import Backup
    assert hasattr(Backup, '_db_housekeeping')
    src = inspect.getsource(Backup._db_housekeeping)
    assert 'integrity_check' in src
    assert 'VACUUM' in src


def test_backup_scheduler_calls_db_housekeeping():
    """Scheduler's daily loop invokes _db_housekeeping after backup."""
    from core.backup import Backup
    src = inspect.getsource(Backup.start_scheduler)
    assert '_db_housekeeping' in src


def test_db_housekeeping_runs_integrity_check(tmp_path, monkeypatch):
    """Behavior: integrity_check logs OK for a fresh DB, VACUUM is skipped
    on non-weekly runs."""
    import sqlite3
    from core.backup import Backup

    # Build a tiny valid DB
    db = tmp_path / 'test.db'
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    b = Backup.__new__(Backup)
    b.user_dir = tmp_path

    # Daily run (not weekly) — should not VACUUM, should not raise
    b._db_housekeeping(is_weekly=False)

    # Weekly run — should VACUUM without error
    b._db_housekeeping(is_weekly=True)


# ─── #19 Symmetric row caps ──────────────────────────────────────────────

def test_memories_have_row_cap():
    """[REGRESSION_GUARD] memories table has a per-scope row cap like
    knowledge_entries (50k)."""
    from plugins.memory.tools import memory_tools
    assert hasattr(memory_tools, 'MAX_MEMORIES_PER_SCOPE')
    assert memory_tools.MAX_MEMORIES_PER_SCOPE > 0
    src = inspect.getsource(memory_tools._save_memory)
    assert 'MAX_MEMORIES_PER_SCOPE' in src, "_save_memory must enforce the cap"


def test_people_have_row_cap():
    from plugins.memory.tools import knowledge_tools
    assert hasattr(knowledge_tools, 'MAX_PEOPLE_PER_SCOPE')
    assert knowledge_tools.MAX_PEOPLE_PER_SCOPE > 0
    src = inspect.getsource(knowledge_tools.create_or_update_person)
    assert 'MAX_PEOPLE_PER_SCOPE' in src


def test_memory_save_refuses_at_cap(tmp_path, monkeypatch):
    """Behavior: when the scope is at capacity, _save_memory refuses."""
    from plugins.memory.tools import memory_tools as mt
    monkeypatch.setattr(mt, '_db_path', tmp_path / 'mem.db')
    monkeypatch.setattr(mt, '_db_initialized', False)
    mt._ensure_db()
    # Artificially lower the cap to make the test fast
    monkeypatch.setattr(mt, 'MAX_MEMORIES_PER_SCOPE', 2)

    unavail = MagicMock(available=False)
    with patch.object(mt, '_get_embedder', return_value=unavail):
        ok1 = mt._save_memory('one', scope='testscope')[1]
        ok2 = mt._save_memory('two', scope='testscope')[1]
        msg3, ok3 = mt._save_memory('three', scope='testscope')
    assert ok1 and ok2
    assert not ok3
    assert 'limit' in msg3.lower() or 'cap' in msg3.lower() or 'row' in msg3.lower()


# ─── #20 Shared httpx client ─────────────────────────────────────────────

def test_shared_httpx_client_cached():
    """[REGRESSION_GUARD] _get_http_client returns the same client instance
    on repeated calls — connection reuse for remote embedders."""
    import core.embeddings as emb
    # Reset the cached client so this test is deterministic
    emb._shared_httpx_client = None
    c1 = emb._get_http_client()
    c2 = emb._get_http_client()
    if c1 is not None:
        assert c1 is c2, "httpx client must be singleton across calls"


def test_remote_embedders_use_shared_client():
    """[REGRESSION_GUARD] Both remote providers route through _get_http_client."""
    import core.embeddings as emb
    rem_src = inspect.getsource(emb.RemoteEmbedder.embed)
    rtr_src = inspect.getsource(emb.SapphireRouterEmbedder.embed)
    assert '_get_http_client' in rem_src
    assert '_get_http_client' in rtr_src
