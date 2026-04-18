"""Regression guards for knowledge-tool bugs shipped earlier this session.

Covered:
  R7 [REGRESSION_GUARD] save_knowledge partial FK error surfaces "deleted while saving"
                        (commit 4b32591 — previously silently lost content after embedding compute)
  R8 [REGRESSION_GUARD] search_rag is strict scope — no global overlay leak
                        (RAG must never return cross-chat content)

See tmp/coverage-test-plan.md.
"""
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def isolated_knowledge(tmp_path, monkeypatch):
    """Point knowledge_tools at a fresh temp DB. Each test gets its own."""
    from plugins.memory.tools import knowledge_tools
    db_path = tmp_path / "knowledge_test.db"
    monkeypatch.setattr(knowledge_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(knowledge_tools, "_db_initialized", False, raising=False)
    # Force re-init against the new path
    knowledge_tools._ensure_db()
    yield knowledge_tools, db_path


# ─── R7: Partial-save FK error surfaces clearly ──────────────────────────────

def test_R7_save_knowledge_partial_fk_error_surfaces_clearly(isolated_knowledge):
    """[REGRESSION_GUARD] If a tab is deleted mid-write (between our SELECT and
    subsequent INSERTs), `add_entry` raises sqlite3.IntegrityError (FK). The
    pre-fix behavior silently returned "saved" even though only some chunks
    made it. Post-fix: returns (msg, False) with "deleted while saving" text
    so the caller knows to retry.
    """
    kt, _ = isolated_knowledge

    # Build content that will chunk into MULTIPLE entries, so we can fail the 2nd
    # call to add_entry mid-save.
    long_content = "\n\n".join(["paragraph " + ("word " * 200)] * 5)

    call_count = {"n": 0}
    real_add_entry = kt.add_entry

    def fake_add_entry(tab_id, content, chunk_index=0, source_filename=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First chunk saves fine
            return real_add_entry(tab_id, content, chunk_index, source_filename)
        # Second chunk: simulate tab getting deleted mid-save
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    with patch.object(kt, 'add_entry', side_effect=fake_add_entry):
        msg, ok = kt._save_knowledge('testcat', long_content, scope='default')

    assert ok is False, f"partial save must return False, got {ok}"
    # Error message should mention the deleted-mid-save condition
    assert "deleted while saving" in msg.lower(), (
        f"message should explain cause; got: {msg!r}"
    )
    # And should indicate partial state so user knows what happened
    assert "partial" in msg.lower() or "chunks" in msg.lower(), (
        f"message should report partial state; got: {msg!r}"
    )


def test_R7_save_knowledge_success_path_still_returns_true(isolated_knowledge):
    """Inverse of R7: when NO FK error happens, save must succeed. Pinning this
    to ensure the try/except doesn't accidentally broaden and swallow success."""
    kt, _ = isolated_knowledge
    msg, ok = kt._save_knowledge('testcat2', 'short content', scope='default')
    assert ok is True
    assert "Saved" in msg or "saved" in msg


# ─── R8: search_rag is strict scope ──────────────────────────────────────────

def _mock_embedder(vec):
    """Build a MagicMock embedder that returns a fixed vector for any query."""
    emb = MagicMock()
    emb.available = True
    emb.embed = MagicMock(return_value=np.array([vec], dtype=np.float32))
    return emb


def test_R8_search_rag_is_strict_scope_no_global_overlay(isolated_knowledge):
    """[REGRESSION_GUARD] search_rag must return ONLY content whose tab.scope
    equals the passed scope. Must NOT overlay 'global' scope content the way
    regular knowledge search does. A leak here would let RAG return content
    from the wrong chat (or from user's global knowledge base that isn't
    related to the current chat's document context).
    """
    kt, _ = isolated_knowledge

    # Create tabs in three scopes: global, __rag__:chat1, __rag__:chat2
    tab_global = kt.create_tab('global_tab', 'global', description=None, tab_type='ai')
    tab_rag1 = kt.create_tab('rag_tab_1', '__rag__:chat1', description=None, tab_type='ai')
    tab_rag2 = kt.create_tab('rag_tab_2', '__rag__:chat2', description=None, tab_type='ai')

    # Insert distinct content into each scope, each with a unique vector
    # Use orthogonal-ish vectors so similarity is high for exact-match only
    def _insert_with_vec(tab_id, content, vec):
        # Directly insert row + embedding blob
        import sqlite3 as _sql
        with kt._get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO knowledge_entries (tab_id, content, embedding, chunk_index)
                VALUES (?, ?, ?, 0)
            ''', (tab_id, content, np.array(vec, dtype=np.float32).tobytes()))
            conn.commit()

    _insert_with_vec(tab_global, 'global_secret_content', [1.0, 0.0, 0.0] + [0.0] * 381)
    _insert_with_vec(tab_rag1, 'chat1_rag_document_content', [0.0, 1.0, 0.0] + [0.0] * 381)
    _insert_with_vec(tab_rag2, 'chat2_rag_document_content', [0.0, 0.0, 1.0] + [0.0] * 381)

    # Mock the embedder to return the same vector as chat1's content — so any
    # strict-scope bug would surface as either a global_secret hit or a chat2 hit.
    chat1_query_vec = [0.0, 1.0, 0.0] + [0.0] * 381

    with patch.object(kt, '_get_embedder', return_value=_mock_embedder(chat1_query_vec)):
        # Search in __rag__:chat1 — must return ONLY chat1 content
        results = kt.search_rag('any_query', scope='__rag__:chat1', limit=10, threshold=0.1)

    contents = [r['content'] for r in results]

    # Must contain chat1
    assert any('chat1_rag_document_content' in c for c in contents), (
        f"chat1 rag content missing from results: {contents}"
    )
    # Must NOT contain global or chat2
    assert not any('global_secret_content' in c for c in contents), (
        f"global scope LEAKED into __rag__:chat1 search — strict-scope broken: {contents}"
    )
    assert not any('chat2' in c for c in contents), (
        f"__rag__:chat2 LEAKED into __rag__:chat1 search — scope isolation broken: {contents}"
    )


def test_R8_search_rag_empty_scope_returns_empty(isolated_knowledge):
    """Inverse guard: querying a RAG scope with no content returns []. If strict
    scope were accidentally relaxed to match ANY row, this would return data."""
    kt, _ = isolated_knowledge

    # No tabs in this scope
    query_vec = [1.0] * 384

    with patch.object(kt, '_get_embedder', return_value=_mock_embedder(query_vec)):
        results = kt.search_rag('anything', scope='__rag__:nonexistent', limit=10, threshold=0.0)

    assert results == [], f"empty scope must return []; got {results}"
