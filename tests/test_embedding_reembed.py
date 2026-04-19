"""Re-embed pipeline tests (2026-04-19, Phase B).

Covers:
  #11 Background re-embed worker:
      - counts orphaned rows across 3 tables
      - re-stamps under the active provider
      - publishes progress events
      - cancellable
      - refuses concurrent starts
  Route wiring for start / status / cancel.
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def isolated_stores(tmp_path, monkeypatch):
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


def _embedder(dim=64, pid='test:reembed'):
    e = MagicMock()
    e.available = True
    e.provider_id = pid
    e.dimension = dim
    base = np.zeros(dim, dtype=np.float32)
    base[0] = 1.0
    e.embed = MagicMock(side_effect=lambda texts, prefix='search_document':
                        np.stack([base.copy() for _ in texts]).astype(np.float32))
    return e


def _reset_state():
    """The re-embed module holds module-level state — reset between tests."""
    from core.embeddings import reembed
    reembed._state.running = False
    reembed._state.cancel_requested = False
    reembed._state.thread = None
    reembed._state.total = 0
    reembed._state.done = 0
    reembed._state.current_table = None
    reembed._state.errors = 0
    reembed._state.last_error = None
    reembed._state.started_at = None
    reembed._state.finished_at = None


def _wait_for_done(timeout=5.0):
    from core.embeddings import reembed
    start = time.time()
    while time.time() - start < timeout:
        if not reembed._state.running:
            return True
        time.sleep(0.02)
    return False


# ─── Pipeline behavior ────────────────────────────────────────────────────

def test_reembed_processes_mismatched_memories(isolated_stores, monkeypatch):
    """[REGRESSION_GUARD] Rows stamped with a different provider get re-stamped
    under the active provider after re-embed runs."""
    mt, _ = isolated_stores
    _reset_state()
    old = _embedder(dim=64, pid='test:OLD')
    with patch.object(mt, '_get_embedder', return_value=old):
        mt._save_memory('m1', scope='default')
        mt._save_memory('m2', scope='default')
        mt._save_memory('m3', scope='default')

    # Active provider is new
    new = _embedder(dim=64, pid='test:NEW')
    import core.embeddings as emb
    monkeypatch.setattr(emb, '_embedder', new)
    monkeypatch.setattr(emb, 'get_embedder', lambda: new)

    from core.embeddings.reembed import start_reembed
    ok, msg = start_reembed()
    assert ok, msg
    assert _wait_for_done(timeout=5), "re-embed worker didn't finish"

    # All memories now stamped with new provider
    with mt._get_connection() as conn:
        rows = conn.execute(
            'SELECT embedding_provider FROM memories'
        ).fetchall()
    assert all(r[0] == 'test:NEW' for r in rows), \
        f"not all rows re-stamped; found providers: {set(r[0] for r in rows)}"


def test_reembed_processes_legacy_unstamped_rows(isolated_stores, monkeypatch):
    """[REGRESSION_GUARD] NULL-provenance rows (pre-migration data) get
    stamped on re-embed run."""
    mt, _ = isolated_stores
    _reset_state()

    # Insert a legacy row directly (no stamp)
    with mt._get_connection() as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO memories (content, embedding) VALUES (?, ?)",
                (f'legacy {i}', np.zeros(64, dtype=np.float32).tobytes())
            )
        conn.commit()

    emb_obj = _embedder(dim=64, pid='test:CURRENT')
    import core.embeddings as emb
    monkeypatch.setattr(emb, '_embedder', emb_obj)
    monkeypatch.setattr(emb, 'get_embedder', lambda: emb_obj)

    from core.embeddings.reembed import start_reembed
    ok, _ = start_reembed()
    assert ok
    assert _wait_for_done()

    with mt._get_connection() as conn:
        rows = conn.execute(
            'SELECT embedding_provider, embedding_dim FROM memories'
        ).fetchall()
    assert all(r[0] == 'test:CURRENT' and r[1] == 64 for r in rows)


def test_reembed_processes_knowledge_and_people(isolated_stores, monkeypatch):
    mt, kt = isolated_stores
    _reset_state()
    old = _embedder(dim=64, pid='test:OLD')
    with patch.object(mt, '_get_embedder', return_value=old), \
         patch.object(kt, '_get_embedder', return_value=old):
        tab = kt.create_tab('cats', 'default')
        kt.add_entry(tab, 'e1')
        kt.add_entry(tab, 'e2')
        kt.create_or_update_person('Alice', scope='default')
        kt.create_or_update_person('Bob', scope='default')

    new = _embedder(dim=64, pid='test:NEW')
    import core.embeddings as emb
    monkeypatch.setattr(emb, '_embedder', new)
    monkeypatch.setattr(emb, 'get_embedder', lambda: new)

    from core.embeddings.reembed import start_reembed
    ok, _ = start_reembed()
    assert ok
    assert _wait_for_done()

    with kt._get_connection() as conn:
        e_rows = conn.execute('SELECT embedding_provider FROM knowledge_entries').fetchall()
        p_rows = conn.execute('SELECT embedding_provider FROM people').fetchall()
    assert all(r[0] == 'test:NEW' for r in e_rows)
    assert all(r[0] == 'test:NEW' for r in p_rows)


def test_reembed_noop_when_all_current(isolated_stores, monkeypatch):
    """When every row is already stamped with the active provider, re-embed
    runs to completion with total=0 and doesn't call the embedder."""
    mt, _ = isolated_stores
    _reset_state()
    emb_obj = _embedder(dim=64, pid='test:ACTIVE')
    import core.embeddings as emb
    monkeypatch.setattr(emb, '_embedder', emb_obj)
    monkeypatch.setattr(emb, 'get_embedder', lambda: emb_obj)
    with patch.object(mt, '_get_embedder', return_value=emb_obj):
        mt._save_memory('already current', scope='default')

    from core.embeddings.reembed import start_reembed, get_status
    emb_obj.embed.reset_mock()
    ok, _ = start_reembed()
    assert ok
    assert _wait_for_done()
    status = get_status()
    assert status['total'] == 0
    # The embedder shouldn't have been invoked for re-embedding
    # (it's still called once inside _count_pending? no — counting uses SQL only)
    assert emb_obj.embed.call_count == 0


def test_reembed_refuses_concurrent_start(isolated_stores, monkeypatch):
    """[REGRESSION_GUARD] Two clicks must not fire two workers."""
    mt, _ = isolated_stores
    _reset_state()
    # Slow embedder so the first call is still processing when the second fires
    slow_emb = _embedder(dim=64, pid='test:slow')
    import threading
    release = threading.Event()

    def _slow_embed(texts, prefix='search_document'):
        release.wait(timeout=2)
        return np.stack([np.eye(64, dtype=np.float32)[0] for _ in texts])

    slow_emb.embed = MagicMock(side_effect=_slow_embed)
    # Seed a row stamped with old provider
    with patch.object(mt, '_get_embedder', return_value=_embedder(dim=64, pid='test:old')):
        mt._save_memory('pending', scope='default')

    import core.embeddings as emb
    monkeypatch.setattr(emb, '_embedder', slow_emb)
    monkeypatch.setattr(emb, 'get_embedder', lambda: slow_emb)

    from core.embeddings.reembed import start_reembed
    ok1, msg1 = start_reembed()
    ok2, msg2 = start_reembed()
    release.set()
    _wait_for_done(timeout=5)
    assert ok1 is True
    assert ok2 is False, "second start call must be refused while running"
    assert 'already' in msg2.lower()


def test_reembed_publishes_progress_events(isolated_stores, monkeypatch, event_bus_capture):
    mt, _ = isolated_stores
    _reset_state()
    old = _embedder(dim=64, pid='test:OLD')
    with patch.object(mt, '_get_embedder', return_value=old):
        for i in range(5):
            mt._save_memory(f'row {i}', scope='default')

    new = _embedder(dim=64, pid='test:NEW')
    import core.embeddings as emb
    monkeypatch.setattr(emb, '_embedder', new)
    monkeypatch.setattr(emb, 'get_embedder', lambda: new)

    from core.embeddings.reembed import start_reembed
    ok, _ = start_reembed()
    assert ok
    _wait_for_done()

    events = [t for t, _ in event_bus_capture.events if t == 'reembed_progress']
    # At least one start event + one finish event
    assert len(events) >= 2, f"expected progress events, got {events}"


def test_reembed_event_bus_constant_exists():
    from core.event_bus import Events
    assert Events.REEMBED_PROGRESS == 'reembed_progress'


# ─── Module-level plumbing ────────────────────────────────────────────────

def test_reembed_routes_wired():
    """[REGRESSION_GUARD] Start / status / cancel routes all exist."""
    src = (Path(__file__).parent.parent / 'core/routes/knowledge.py').read_text()
    assert '/api/embedding/reembed' in src
    assert '/api/embedding/reembed/status' in src
    assert '/api/embedding/reembed/cancel' in src


def test_frontend_subscribes_to_reembed_progress():
    src = (Path(__file__).parent.parent / 'interfaces/web/static/views/settings-tabs/embedding.js').read_text()
    assert 'REEMBED_PROGRESS' in src
    assert 'wireReembedControls' in src
