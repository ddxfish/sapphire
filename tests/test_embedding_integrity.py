"""integrity_report and UI swap-confirmation guards (2026-04-19, Phase A).

Covers:
  #5 Settings UI warns before EMBEDDING_PROVIDER swap — integrity endpoint
      provides the counts, save-time confirm blocks the swap if user cancels
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ─── integrity_report core function ───────────────────────────────────────

@pytest.fixture
def isolated_stores(tmp_path, monkeypatch):
    """Point memory + knowledge DBs at tmp, initialize fresh."""
    from plugins.memory.tools import memory_tools, knowledge_tools
    monkeypatch.setattr(memory_tools, '_db_path', tmp_path / 'mem.db')
    monkeypatch.setattr(memory_tools, '_db_initialized', False)
    monkeypatch.setattr(memory_tools, '_backfill_done', False)
    monkeypatch.setattr(knowledge_tools, '_db_path', tmp_path / 'know.db')
    monkeypatch.setattr(knowledge_tools, '_db_initialized', False)
    monkeypatch.setattr(knowledge_tools, '_backfill_done', False)
    memory_tools._ensure_db()
    knowledge_tools._ensure_db()
    return memory_tools, knowledge_tools


def _embedder(dim=128, pid='test:alpha'):
    e = MagicMock()
    e.available = True
    e.provider_id = pid
    e.dimension = dim
    base = np.zeros(dim, dtype=np.float32)
    base[0] = 1.0
    e.embed = MagicMock(side_effect=lambda texts, prefix='search_document':
                        np.stack([base.copy() for _ in texts]).astype(np.float32))
    return e


def test_integrity_report_empty_db(isolated_stores, monkeypatch):
    import core.embeddings as emb
    # Active provider = our fake with known identity
    fake = _embedder(dim=128, pid='test:alpha')
    monkeypatch.setattr(emb, '_embedder', fake)
    monkeypatch.setattr(emb, 'get_embedder', lambda: fake)
    report = emb.integrity_report()
    assert report['active']['provider'] == 'test:alpha'
    assert report['active']['dim'] == 128
    for t in ('memories', 'knowledge_entries', 'people'):
        assert report['tables'][t]['total'] == 0
        assert report['tables'][t]['matching_active'] == 0
        assert report['tables'][t]['legacy_unstamped'] == 0


def test_integrity_report_counts_matching_vectors(isolated_stores, monkeypatch):
    mt, kt = isolated_stores
    import core.embeddings as emb
    fake = _embedder(dim=128, pid='test:alpha')
    monkeypatch.setattr(emb, '_embedder', fake)
    monkeypatch.setattr(emb, 'get_embedder', lambda: fake)
    with patch.object(mt, '_get_embedder', return_value=fake), \
         patch.object(kt, '_get_embedder', return_value=fake):
        mt._save_memory('m1', scope='default')
        mt._save_memory('m2', scope='default')
        tab = kt.create_tab('t', 'default')
        kt.add_entry(tab, 'e1')
        kt.create_or_update_person('Alice', scope='default')
    report = emb.integrity_report()
    assert report['tables']['memories']['matching_active'] == 2
    assert report['tables']['knowledge_entries']['matching_active'] == 1
    assert report['tables']['people']['matching_active'] == 1


def test_integrity_report_detects_other_stamps(isolated_stores, monkeypatch):
    """[REGRESSION_GUARD] Rows stamped with a non-active provider must show
    as 'other_stamps', not as matching. This is what powers the swap warning."""
    mt, kt = isolated_stores
    import core.embeddings as emb
    # Save with provider A
    fake_a = _embedder(dim=128, pid='test:provider-A')
    with patch.object(mt, '_get_embedder', return_value=fake_a):
        mt._save_memory('m1', scope='default')
    # Report while provider B is active
    fake_b = _embedder(dim=128, pid='test:provider-B')
    monkeypatch.setattr(emb, '_embedder', fake_b)
    monkeypatch.setattr(emb, 'get_embedder', lambda: fake_b)
    report = emb.integrity_report()
    assert report['tables']['memories']['matching_active'] == 0
    assert report['tables']['memories']['other_stamps'] == 1


def test_integrity_report_detects_legacy_unstamped(isolated_stores, monkeypatch):
    """[REGRESSION_GUARD] Rows with NULL provenance (pre-migration data) show
    as legacy_unstamped so the UI can prompt for backfill."""
    mt, _kt = isolated_stores
    import core.embeddings as emb
    # Insert a legacy-style row directly (no stamp)
    with mt._get_connection() as conn:
        conn.execute(
            "INSERT INTO memories (content, embedding) VALUES ('legacy', ?)",
            (np.zeros(128, dtype=np.float32).tobytes(),)
        )
        conn.commit()
    fake = _embedder(dim=128, pid='test:alpha')
    monkeypatch.setattr(emb, '_embedder', fake)
    monkeypatch.setattr(emb, 'get_embedder', lambda: fake)
    report = emb.integrity_report()
    assert report['tables']['memories']['legacy_unstamped'] == 1
    assert report['tables']['memories']['matching_active'] == 0


# ─── settings.js pre-save confirm guard ───────────────────────────────────

def test_settings_save_warns_before_embedding_provider_swap():
    """[REGRESSION_GUARD] settings.js saveChanges must invoke the integrity
    endpoint before committing an EMBEDDING_PROVIDER change, and must let
    the user back out via confirm()."""
    src = (Path(__file__).parent.parent / 'interfaces/web/static/views/settings.js').read_text()
    # Must fetch the integrity endpoint
    assert "/api/embedding/integrity" in src, \
        "settings.js must call /api/embedding/integrity before committing a swap"
    # Must gate on EMBEDDING_PROVIDER
    assert "'EMBEDDING_PROVIDER'" in src
    # Must confirm() and respect cancel
    assert 'confirm(' in src


def test_embedding_tab_shows_integrity_status():
    """[REGRESSION_GUARD] Embedding tab renders a status block so the user
    can see how many vectors are orphaned at any time."""
    src = (Path(__file__).parent.parent / 'interfaces/web/static/views/settings-tabs/embedding.js').read_text()
    assert '#embedding-integrity' in src
    assert "/api/embedding/integrity" in src
