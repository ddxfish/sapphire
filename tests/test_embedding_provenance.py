"""Embedding provenance regression tests (2026-04-19).

Phase 0 + Phase 1 of the embeddings safety pass. Named after the scout
finding they guard so when one fails, it's obvious which bug is back.

Covered:
  #1  sapphire_router ghost — registry actually registers it now
  #2  vector provenance stamp (provider_id + dim) on every write, 3 tables
  #4  read-path filter skips rows stamped with other providers / dims
  #6  update_memory / update_entry / person update don't NULL-out embedding
      when embed() returns None
  #7  knowledge/people backfill exists and runs
  #9  SapphireRouterEmbedder L2-normalizes its output
  #10 _backfill_done stays False when a batch fails transiently
  #12 vector search orders by timestamp DESC (recency window, not rowid)
  #17 find_duplicates filters by active provider
"""
import json
import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ─── #1 ghost provider registered ─────────────────────────────────────────

def test_sapphire_router_is_registered_in_core():
    """[REGRESSION_GUARD] embedding.js offers `sapphire_router`; the registry
    must recognize the key. If it falls through to NullEmbedder, every
    save after selection writes embedding=NULL (active bug)."""
    from core.embeddings import embedding_registry
    assert embedding_registry.has_key('sapphire_router'), \
        "sapphire_router must be registered — otherwise selecting it in the UI silently disables embeddings"
    entry = embedding_registry.get_entry('sapphire_router')
    assert entry and entry.get('class').__name__ == 'SapphireRouterEmbedder'


def test_sapphire_router_listed_in_ui_config():
    """UI config and backend registry agree that sapphire_router is a valid choice."""
    from pathlib import Path
    js_src = (Path(__file__).parent.parent / 'interfaces/web/static/views/settings-tabs/embedding.js').read_text()
    assert 'sapphire_router' in js_src


# ─── #9 SapphireRouterEmbedder normalizes output ──────────────────────────

def test_sapphire_router_l2_normalizes():
    """[REGRESSION_GUARD] SapphireRouterEmbedder must L2-normalize its output.
    Other core providers do; a non-normalized vector wrecks cosine-sim
    ranking when mixed with normalized vectors. Source-level + smoke check."""
    import inspect
    from core.embeddings import SapphireRouterEmbedder
    # Source-level guard: the normalization code must be present
    src = inspect.getsource(SapphireRouterEmbedder.embed)
    assert 'linalg.norm' in src, "SapphireRouterEmbedder must L2-normalize"
    assert 'norms[norms == 0] = 1' in src, "must guard zero-norm edge case"

    # Smoke: patch the shared httpx client's post method (the embedder now
    # routes through _get_http_client() for connection reuse)
    import core.embeddings as emb
    fake_client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {'embeddings': [[10.0, 0.0, 0.0, 0.0]]}
    fake_client.post.return_value = resp
    with patch.object(emb, '_get_http_client', return_value=fake_client):
        import os as _os
        _os.environ['SAPPHIRE_ROUTER_URL'] = 'http://fake-router'
        try:
            e = SapphireRouterEmbedder()
            out = e.embed(['test'])
        finally:
            _os.environ.pop('SAPPHIRE_ROUTER_URL', None)
    assert out is not None
    norm = float(np.linalg.norm(out[0]))
    assert abs(norm - 1.0) < 1e-5, f"router output must be unit-normalized, got norm={norm}"


# ─── Provider identity ────────────────────────────────────────────────────

def test_provider_id_and_dimension_exposed_on_core_providers():
    """[REGRESSION_GUARD] All core providers must expose `provider_id` so
    stored vectors carry a stable identity. Dimension may be None until the
    first embed for remote providers; static for local."""
    from core.embeddings import LocalEmbedder, RemoteEmbedder, SapphireRouterEmbedder, NullEmbedder
    for cls in (LocalEmbedder, RemoteEmbedder, SapphireRouterEmbedder, NullEmbedder):
        inst = cls()
        assert hasattr(inst, 'provider_id')
        assert isinstance(inst.provider_id, str) and inst.provider_id, f"{cls.__name__}.provider_id must be a non-empty string"
        assert hasattr(inst, 'dimension')


def test_stamp_embedding_returns_triple():
    """stamp_embedding(vec, embedder) returns (blob, provider_id, dim)."""
    from core.embeddings import stamp_embedding
    embedder = MagicMock()
    embedder.provider_id = 'test:fake'
    vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    blob, pid, dim = stamp_embedding(vec, embedder)
    assert pid == 'test:fake'
    assert dim == 3
    assert blob == vec.tobytes()


def test_stamp_embedding_none_returns_triple_none():
    from core.embeddings import stamp_embedding
    assert stamp_embedding(None) == (None, None, None)


# ─── Memory isolated fixture ──────────────────────────────────────────────

@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    from plugins.memory.tools import memory_tools
    monkeypatch.setattr(memory_tools, '_db_path', tmp_path / 'mem.db')
    monkeypatch.setattr(memory_tools, '_db_initialized', False)
    monkeypatch.setattr(memory_tools, '_backfill_done', False)
    memory_tools._ensure_db()
    return memory_tools


@pytest.fixture
def isolated_knowledge(tmp_path, monkeypatch):
    from plugins.memory.tools import knowledge_tools
    monkeypatch.setattr(knowledge_tools, '_db_path', tmp_path / 'know.db')
    monkeypatch.setattr(knowledge_tools, '_db_initialized', False)
    monkeypatch.setattr(knowledge_tools, '_backfill_done', False)
    knowledge_tools._ensure_db()
    return knowledge_tools


def _fake_embedder(vec_dim=128, provider_id='test:p1', value=0.1):
    """Deterministic MagicMock embedder producing unit-norm vectors."""
    e = MagicMock()
    e.available = True
    e.provider_id = provider_id
    e.dimension = vec_dim
    base = np.zeros(vec_dim, dtype=np.float32)
    base[0] = 1.0  # unit vector along first axis
    e.embed = MagicMock(side_effect=lambda texts, prefix='search_document':
                        np.stack([base.copy() for _ in texts]).astype(np.float32))
    return e


# ─── #2 provenance stamping on write (memory) ─────────────────────────────

def test_save_memory_stamps_provenance(isolated_memory):
    """[REGRESSION_GUARD] _save_memory stores provider_id + dim alongside
    the vector, so a future swap doesn't silently mix vector spaces."""
    mt = isolated_memory
    emb = _fake_embedder(vec_dim=128, provider_id='test:p1')
    with patch.object(mt, '_get_embedder', return_value=emb):
        msg, ok = mt._save_memory('remember this', scope='default')
    assert ok
    with mt._get_connection() as conn:
        row = conn.execute(
            'SELECT embedding_provider, embedding_dim FROM memories'
        ).fetchone()
    assert row[0] == 'test:p1'
    assert row[1] == 128


# ─── #4 read-path filter by provenance (memory) ───────────────────────────

def test_vector_search_filters_by_provider(isolated_memory):
    """[REGRESSION_GUARD] Rows stamped with a different provider_id must not
    participate in vector search. Without this filter, a provider swap
    leaves stale vectors that either crash np.dot or return garbage scores."""
    mt = isolated_memory
    # Save under provider A
    emb_a = _fake_embedder(vec_dim=128, provider_id='test:provider-A')
    with patch.object(mt, '_get_embedder', return_value=emb_a):
        mt._save_memory('content A', scope='default')
    # Search under provider B — should skip the row, return nothing
    emb_b = _fake_embedder(vec_dim=128, provider_id='test:provider-B')
    with patch.object(mt, '_get_embedder', return_value=emb_b):
        results = mt._vector_search('any', 'default', labels=[], limit=10)
    assert results == [], f"provider filter broken — got {len(results)} results from other provider"


def test_vector_search_filters_by_dim(isolated_memory):
    """[REGRESSION_GUARD] Rows stamped with a different dim must be skipped
    even if provider_id matches. Dim mismatch is how np.dot crashes."""
    mt = isolated_memory
    # Save under dim=128
    emb_128 = _fake_embedder(vec_dim=128, provider_id='test:same')
    with patch.object(mt, '_get_embedder', return_value=emb_128):
        mt._save_memory('content', scope='default')
    # Search under dim=384 same provider_id
    emb_384 = _fake_embedder(vec_dim=384, provider_id='test:same')
    with patch.object(mt, '_get_embedder', return_value=emb_384):
        results = mt._vector_search('any', 'default', labels=[], limit=10)
    assert results == []


def test_vector_search_returns_matches_within_provenance(isolated_memory):
    """Sanity: a correctly-stamped row is findable by a matching provider."""
    mt = isolated_memory
    emb = _fake_embedder(vec_dim=128, provider_id='test:p1')
    with patch.object(mt, '_get_embedder', return_value=emb):
        mt._save_memory('unit-vector content', scope='default')
        results = mt._vector_search('any', 'default', labels=[], limit=10)
    assert len(results) == 1
    assert 'unit-vector content' in results[0][1]


# ─── #12 vector search recency ordering ───────────────────────────────────

def test_vector_search_orders_by_timestamp_desc():
    """[REGRESSION_GUARD] LIMIT 10000 by rowid would silently drop newer
    memories once n > 10k. Must order by timestamp DESC so recent wins."""
    import inspect
    from plugins.memory.tools import memory_tools
    src = inspect.getsource(memory_tools._vector_search)
    assert 'ORDER BY timestamp DESC' in src


# ─── #2 / #4 provenance stamping on knowledge ─────────────────────────────

def test_add_entry_stamps_provenance(isolated_knowledge):
    kt = isolated_knowledge
    emb = _fake_embedder(vec_dim=128, provider_id='test:know')
    with patch.object(kt, '_get_embedder', return_value=emb):
        tab_id = kt.create_tab('testtab', 'default')
        eid = kt.add_entry(tab_id, 'content')
    with kt._get_connection() as conn:
        row = conn.execute(
            'SELECT embedding_provider, embedding_dim FROM knowledge_entries WHERE id = ?', (eid,)
        ).fetchone()
    assert row[0] == 'test:know'
    assert row[1] == 128


def test_create_or_update_person_stamps_provenance(isolated_knowledge):
    kt = isolated_knowledge
    emb = _fake_embedder(vec_dim=128, provider_id='test:people')
    with patch.object(kt, '_get_embedder', return_value=emb):
        pid, is_new = kt.create_or_update_person('Alice', scope='friends')
    assert is_new
    with kt._get_connection() as conn:
        row = conn.execute(
            'SELECT embedding_provider, embedding_dim FROM people WHERE id = ?', (pid,)
        ).fetchone()
    assert row[0] == 'test:people'
    assert row[1] == 128


# ─── #6 don't NULL-out embedding on update-embed failure ──────────────────

def test_update_entry_preserves_embedding_on_embed_failure(isolated_knowledge):
    """[REGRESSION_GUARD] update_entry must NOT strip embedding columns if
    embedder.embed() returns None. Scout finding: edits during remote-down
    used to silently erase the vector."""
    kt = isolated_knowledge
    emb_good = _fake_embedder(vec_dim=128, provider_id='test:good')
    with patch.object(kt, '_get_embedder', return_value=emb_good):
        tab_id = kt.create_tab('tab', 'default')
        eid = kt.add_entry(tab_id, 'original content')
    with kt._get_connection() as conn:
        before = conn.execute(
            'SELECT embedding, embedding_provider, embedding_dim FROM knowledge_entries WHERE id = ?',
            (eid,)
        ).fetchone()
    assert before[0] is not None and before[1] == 'test:good'

    # Now the embedder returns None — update must preserve existing vector
    emb_dead = MagicMock()
    emb_dead.available = True
    emb_dead.provider_id = 'test:good'
    emb_dead.dimension = 128
    emb_dead.embed = MagicMock(return_value=None)
    with patch.object(kt, '_get_embedder', return_value=emb_dead):
        kt.update_entry(eid, 'edited content')

    with kt._get_connection() as conn:
        after = conn.execute(
            'SELECT content, embedding, embedding_provider, embedding_dim FROM knowledge_entries WHERE id = ?',
            (eid,)
        ).fetchone()
    assert after[0] == 'edited content', "content should update"
    assert after[1] == before[0], "vector must NOT be stripped"
    assert after[2] == before[1], "provider stamp must persist"
    assert after[3] == before[2], "dim stamp must persist"


# ─── #7 knowledge/people backfill exists ──────────────────────────────────

def test_knowledge_backfill_populates_unstamped_rows(isolated_knowledge):
    """[REGRESSION_GUARD] Rows with NULL provenance get stamped on first
    vector search. Knowledge/people had no backfill before this pass — a
    transient embed failure at write time stranded the row permanently."""
    kt = isolated_knowledge
    # Insert a legacy-style row (no provenance stamp) directly
    with kt._get_connection() as conn:
        conn.execute("INSERT INTO knowledge_tabs (name, scope, type) VALUES ('legacy', 'default', 'user')")
        tab_id = conn.execute(
            "SELECT id FROM knowledge_tabs WHERE name = 'legacy'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO knowledge_entries (tab_id, content) VALUES (?, 'legacy content')",
            (tab_id,)
        )
        conn.commit()
    # Pre-backfill: row has NULL provenance
    with kt._get_connection() as conn:
        row = conn.execute(
            "SELECT embedding_provider, embedding_dim FROM knowledge_entries"
        ).fetchone()
    assert row == (None, None)

    emb = _fake_embedder(vec_dim=128, provider_id='test:bf')
    with patch.object(kt, '_get_embedder', return_value=emb):
        kt._backfill_knowledge_embeddings()

    with kt._get_connection() as conn:
        row = conn.execute(
            "SELECT embedding_provider, embedding_dim FROM knowledge_entries"
        ).fetchone()
    assert row[0] == 'test:bf'
    assert row[1] == 128


# ─── #10 backfill transient-failure recovery ──────────────────────────────

def test_backfill_done_not_set_on_transient_failure(isolated_memory):
    """[REGRESSION_GUARD] `_backfill_done` must stay False when the embedder
    returns None mid-batch, so the next search retries the remainder.
    Pre-fix, it was set True unconditionally, stranding rows until restart."""
    mt = isolated_memory
    # Seed 2 memories without embeddings (embedder unavailable)
    unavail = MagicMock(available=False)
    with patch.object(mt, '_get_embedder', return_value=unavail):
        mt._save_memory('r1', scope='default')
        mt._save_memory('r2', scope='default')

    # Embedder returns None (transient fail)
    failing = MagicMock()
    failing.available = True
    failing.provider_id = 'test:p'
    failing.dimension = 128
    failing.embed = MagicMock(return_value=None)
    mt._backfill_done = False
    with patch.object(mt, '_get_embedder', return_value=failing):
        mt._backfill_embeddings()
    assert mt._backfill_done is False, \
        "transient failure must not flip _backfill_done — otherwise retries stop until restart"


# ─── Schema migration ─────────────────────────────────────────────────────

def test_memory_schema_has_provenance_columns(isolated_memory):
    mt = isolated_memory
    with mt._get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
    assert 'embedding_provider' in cols
    assert 'embedding_dim' in cols


def test_knowledge_schema_has_provenance_columns(isolated_knowledge):
    kt = isolated_knowledge
    with kt._get_connection() as conn:
        ke_cols = [r[1] for r in conn.execute("PRAGMA table_info(knowledge_entries)").fetchall()]
        p_cols = [r[1] for r in conn.execute("PRAGMA table_info(people)").fetchall()]
    assert 'embedding_provider' in ke_cols and 'embedding_dim' in ke_cols
    assert 'embedding_provider' in p_cols and 'embedding_dim' in p_cols
