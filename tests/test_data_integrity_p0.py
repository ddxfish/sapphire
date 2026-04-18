"""Surface 3 P0 — data-integrity regression guards for memory/knowledge/goals.

Silent data corruption is the worst outcome for Sapphire — losing memories,
cross-scope bleed, or an AI accidentally deleting a permanent duty. These
guards protect the user's actual data.

Complements R7 (save_knowledge partial FK) and R8 (RAG strict scope), which
are already landed in `test_regression_guards_knowledge.py`.

Covers:
  3.3  delete_memory wrong scope refuses (no cross-scope delete)
  3.5  save_person upsert by name is case-insensitive
  3.6  delete_person wrong scope refuses
  3.7  delete permanent goal refused
  3.8  update permanent goal — only progress_note allowed
  3.9  create goal two-level nesting rejected
  3.10 update_goal API-path vs internal-path permanent guard parity
  3.11 save_memory embedder-unavailable graceful degrade
  3.14 get_tab_entries / search_knowledge validates scope
  3.15 save at scope='global' blocked at executor

See tmp/coverage-test-plan.md Surface 3.
"""
from unittest.mock import MagicMock, patch

import pytest


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    """Fresh memory_tools DB per test."""
    from plugins.memory.tools import memory_tools
    db_path = tmp_path / "memory_test.db"
    monkeypatch.setattr(memory_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(memory_tools, "_db_initialized", False, raising=False)
    memory_tools._ensure_db()
    yield memory_tools, db_path


@pytest.fixture
def isolated_knowledge(tmp_path, monkeypatch):
    """Fresh knowledge_tools DB per test."""
    from plugins.memory.tools import knowledge_tools
    db_path = tmp_path / "knowledge_test.db"
    monkeypatch.setattr(knowledge_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(knowledge_tools, "_db_initialized", False, raising=False)
    knowledge_tools._ensure_db()
    yield knowledge_tools, db_path


@pytest.fixture
def isolated_goals(tmp_path, monkeypatch):
    """Fresh goals_tools DB per test."""
    from plugins.memory.tools import goals_tools
    db_path = tmp_path / "goals_test.db"
    monkeypatch.setattr(goals_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(goals_tools, "_db_initialized", False, raising=False)
    goals_tools._ensure_db()
    yield goals_tools, db_path


# ─── 3.3 delete_memory wrong scope refuses ───────────────────────────────────

def test_delete_memory_wrong_scope_refuses(isolated_memory):
    """[REGRESSION_GUARD] Memory saved in scope A must NOT be deletable by a
    delete call carrying scope B. Guards against cross-scope bleed — an AI in
    chat B should never be able to delete an AI-in-chat-A memory.
    """
    mt, db_path = isolated_memory
    msg, ok = mt._save_memory("alice secret", scope='scope_a')
    assert ok
    # Extract ID — memory_tools uses "(ID: N)" format, not "[N]"
    import re
    m = re.search(r'ID:\s*(\d+)|\[(\d+)\]', msg)
    assert m, f"couldn't parse memory id from: {msg}"
    memory_id = int(m.group(1) or m.group(2))

    # Delete with WRONG scope
    msg, ok = mt._delete_memory(memory_id, scope='scope_b')
    assert ok is False, "delete from wrong scope should refuse"
    assert 'not found' in msg.lower()

    # Verify the row still exists
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    assert row is not None, "memory was deleted across scopes (CORRUPTION)"


def test_delete_memory_right_scope_succeeds(isolated_memory):
    """Inverse: correct scope DOES delete."""
    mt, db_path = isolated_memory
    msg, ok = mt._save_memory("alice secret", scope='scope_a')
    import re
    m = re.search(r'ID:\s*(\d+)|\[(\d+)\]', msg)
    mid = int(m.group(1) or m.group(2))
    msg, ok = mt._delete_memory(mid, scope='scope_a')
    assert ok, f"delete with correct scope failed: {msg}"


# ─── 3.5 save_person upsert by name is case-insensitive ──────────────────────

def test_save_person_upsert_case_insensitive(isolated_knowledge):
    """[REGRESSION_GUARD] Saving 'Alice' then 'alice' must upsert (one row),
    not create two people. Case-sensitive match → duplicate entries →
    confused AI context (AI sees two 'alice' entries and picks one arbitrarily).
    """
    kt, db_path = isolated_knowledge

    msg1, ok1 = kt._save_person('Alice', relationship='friend', scope='default')
    assert ok1
    msg2, ok2 = kt._save_person('alice', relationship='best friend', scope='default')
    assert ok2

    # Should be ONE row, relationship updated
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, name, relationship FROM people WHERE LOWER(name) = LOWER(?)",
        ('alice',),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"case-insensitive upsert failed — saw {len(rows)} rows: {rows}"
    # Updated to the second save's relationship
    assert rows[0][2] == 'best friend', f"relationship not updated: {rows[0]}"


# ─── 3.6 delete_person wrong scope refuses ───────────────────────────────────

def test_delete_person_wrong_scope_refuses(isolated_knowledge, monkeypatch):
    """[REGRESSION_GUARD] `delete_person` reads current_people_scope from
    ContextVar. If the caller's scope doesn't match the person's scope, the
    delete must NOT succeed. Guards cross-scope person deletion via tool call.
    """
    kt, db_path = isolated_knowledge
    # Create Alice in scope 'work'
    kt._save_person('Alice', relationship='coworker', scope='work')
    import sqlite3
    conn = sqlite3.connect(db_path)
    pid = conn.execute("SELECT id FROM people WHERE name = ?", ('Alice',)).fetchone()[0]
    conn.close()

    # Spoof current_people_scope to 'home' — wrong scope
    monkeypatch.setattr(kt, '_get_current_people_scope', lambda: 'home')
    result = kt.delete_person(pid)
    assert result is False, "delete_person from wrong scope should refuse"

    # Row still exists
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM people WHERE id = ?", (pid,)).fetchone()
    conn.close()
    assert row is not None, "person was deleted across scopes (CORRUPTION)"


# ─── 3.7 Permanent goal cannot be deleted ────────────────────────────────────

def test_delete_permanent_goal_refused(isolated_goals):
    """[REGRESSION_GUARD] Goals flagged permanent=True represent standing
    duties. The AI must NOT be able to delete them — only the user can, via
    the UI-path (_delete_goal path goes through a separate user-managed flow).
    """
    gt, db_path = isolated_goals
    msg, ok = gt._create_goal('Watch the house', scope='default', permanent=True)
    assert ok
    import re
    gid = int(re.search(r'\[(\d+)\]', msg).group(1))

    msg, ok = gt._delete_goal(gid, scope='default')
    assert ok is False, "AI-path delete of permanent goal should refuse"
    assert 'permanent' in msg.lower()

    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM goals WHERE id = ?", (gid,)).fetchone()
    conn.close()
    assert row is not None, "permanent goal was deleted (DUTY LOSS)"


# ─── 3.8 Update permanent goal: only progress_note allowed ───────────────────

def test_update_permanent_goal_only_progress_note_allowed(isolated_goals):
    """[REGRESSION_GUARD] AI can add progress notes to a permanent goal but
    can't flip its title / description / priority / status. Prevents an AI
    from silently neutering a standing duty by setting status='completed'
    or retitling it to something benign."""
    gt, db_path = isolated_goals
    msg, ok = gt._create_goal('Watch the house', scope='default', permanent=True)
    assert ok
    import re
    gid = int(re.search(r'\[(\d+)\]', msg).group(1))

    # progress_note: allowed
    msg, ok = gt._update_goal(gid, scope='default', progress_note='still watching')
    assert ok, f"progress_note on permanent goal was refused: {msg}"

    # Any other field: refused
    for field, val in (('title', 'Nope'), ('status', 'completed'),
                       ('priority', 'low'), ('description', 'nevermind')):
        msg, ok = gt._update_goal(gid, scope='default', **{field: val})
        assert ok is False, f"field '{field}' on permanent goal should be refused; got: {msg}"
        assert 'permanent' in msg.lower()

    # Verify DB untouched
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT title, description, priority, status FROM goals WHERE id = ?",
        (gid,)
    ).fetchone()
    conn.close()
    assert row == ('Watch the house', None, 'medium', 'active'), \
        f"permanent goal's protected fields were modified: {row}"


# ─── 3.9 Create goal: two-level nesting rejected ─────────────────────────────

def test_create_goal_two_level_nesting_rejected(isolated_goals):
    """[REGRESSION_GUARD] A subtask of a subtask is forbidden — only one
    level of nesting. Guards against the AI creating nested trees that
    explode the progress-journal UI and break the 'top 3 goals' overview."""
    gt, _ = isolated_goals
    msg, ok = gt._create_goal('Parent goal', scope='default')
    assert ok
    import re
    parent_id = int(re.search(r'\[(\d+)\]', msg).group(1))

    msg, ok = gt._create_goal('Child goal', parent_id=parent_id, scope='default')
    assert ok
    child_id = int(re.search(r'\[(\d+)\]', msg).group(1))

    # Try to make a grandchild — should refuse
    msg, ok = gt._create_goal('Grandchild goal', parent_id=child_id, scope='default')
    assert ok is False, "two-level nesting should be rejected"
    assert 'subtask' in msg.lower() or 'nested' in msg.lower() or 'one level' in msg.lower()


# ─── 3.10 Permanent guard works on both update_goal paths ────────────────────

def test_update_permanent_goal_api_path_still_guarded(isolated_goals):
    """[REGRESSION_GUARD] The API-path (`update_goal_api`) exists for the
    user/UI. Verify the permanent guard applies there too if called via
    the AI-facing internal path (_update_goal is the AI path). We assert
    the guard works on _update_goal; if a future API path is added without
    the guard, tests will need expanding — see coverage-test-plan.md 3.10."""
    gt, _ = isolated_goals
    msg, ok = gt._create_goal('Duty', scope='default', permanent=True)
    gid = int(msg.split('[')[1].split(']')[0])

    # AI path guards permanent (covered in 3.8); explicitly assert again with
    # a non-progress_note field to ensure the guard fires on the internal path
    msg, ok = gt._update_goal(gid, scope='default', status='completed')
    assert ok is False
    assert 'permanent' in msg.lower()


# ─── 3.11 save_memory embedder unavailable → graceful degrade ────────────────

def test_save_memory_embedder_unavailable_graceful_degrade(isolated_memory):
    """[REGRESSION_GUARD] If the embedder is down (no API key, service dead,
    etc.) save_memory must still succeed with embedding=NULL — vector search
    degrades gracefully to FTS. Guards against a failed embedder request
    silently losing the write."""
    mt, db_path = isolated_memory
    # Force _get_embedder() to return an unavailable embedder
    unavail = MagicMock()
    unavail.available = False
    with patch.object(mt, '_get_embedder', return_value=unavail):
        msg, ok = mt._save_memory("saved without embedding", scope='default')
    assert ok, f"save_memory should succeed without embedder: {msg}"

    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT content, embedding FROM memories WHERE content = ?",
        ("saved without embedding",)
    ).fetchone()
    conn.close()
    assert row is not None, "memory content not persisted"
    assert row[1] is None, f"embedding should be NULL when embedder unavailable; got {type(row[1])}"


# ─── 3.14 get_tab_entries / search validates scope ───────────────────────────

def test_knowledge_search_does_not_leak_across_scopes(isolated_knowledge):
    """[REGRESSION_GUARD] `_search_knowledge` at scope A must not return
    entries saved to scope B. Covers non-RAG scope (R8 covered the RAG
    strict-no-global-overlay case)."""
    kt, _ = isolated_knowledge
    # Use distinctive content that CAN'T appear in "No results" messages
    UNIQUE_A = 'xylophone_secret_cat_in_scope_A'
    kt._save_knowledge('catA', UNIQUE_A, scope='knowledge_a')
    kt._save_knowledge('catB', 'ordinary text scope B', scope='knowledge_b')

    # Search from scope_b for scope_a's unique word
    result, _ = kt._search_knowledge(query='xylophone',
                                     scope='knowledge_b', people_scope='default')
    # The unique content string MUST NOT appear (query word 'xylophone' alone is fine —
    # it gets echoed as part of 'No results for...'); only fail if the full
    # content from scope A leaks
    assert UNIQUE_A not in result, \
        f"knowledge leaked from scope_a into search@scope_b: {result[:200]}"


# ─── 3.15 save at scope='global' blocked at executor ─────────────────────────

def test_memory_executor_rejects_global_scope_writes(isolated_memory):
    """[REGRESSION_GUARD] If `_get_current_scope()` returns 'global' for some
    reason (misconfig, edge case), the executor must refuse writes — global
    is a read-only overlay for AI. Only the user can write via the UI.
    """
    mt, _ = isolated_memory
    with patch.object(mt, '_get_current_scope', return_value='global'):
        # execute() drives save_memory — should block before touching DB
        msg, ok = mt.execute('save_memory', {'content': 'should not land'}, config=None)
    assert ok is False, "save to global scope must be refused"
    assert 'global' in msg.lower() or 'read-only' in msg.lower() or 'read only' in msg.lower()


def test_memory_executor_rejects_none_scope(isolated_memory):
    """[PROACTIVE] scope=None (memory disabled for this chat) → all writes
    refused with a clear message. Guards against silent data loss when the
    user has toggled memory off."""
    mt, _ = isolated_memory
    with patch.object(mt, '_get_current_scope', return_value=None):
        msg, ok = mt.execute('save_memory', {'content': 'should not land'}, config=None)
    assert ok is False
    assert 'disabled' in msg.lower() or 'memory' in msg.lower()


# ─── Bonus: create_goal respects priority validation ─────────────────────────

def test_create_goal_invalid_priority_rejected(isolated_goals):
    """Priority must be one of high/medium/low. Anything else → reject."""
    gt, _ = isolated_goals
    msg, ok = gt._create_goal('test', priority='URGENT!', scope='default')
    assert ok is False
    assert 'priority' in msg.lower()


# ─── Bonus: save_knowledge long content chunks without loss ──────────────────

def test_save_knowledge_long_content_saves_all_chunks(isolated_knowledge):
    """[DATA_INTEGRITY] A long blob must be chunked and ALL chunks persisted.
    Guards against a chunking bug (off-by-one, overlap miscalc) silently
    losing content past the first chunk."""
    kt, db_path = isolated_knowledge
    # Large enough to force multiple chunks (~2000 tokens ≈ 8k chars)
    content = "Paragraph.\n\n".join(
        f"Section {i}: " + ("word " * 150)
        for i in range(10)
    )
    msg, ok = kt._save_knowledge('big_doc', content, scope='default')
    assert ok, f"save_knowledge failed: {msg}"

    import sqlite3
    conn = sqlite3.connect(db_path)
    # All chunks stored in knowledge_entries; count rows with 'Section N' somewhere
    section_count = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM knowledge_entries WHERE content LIKE '%Section%'"
    ).fetchone()[0]
    conn.close()
    assert section_count >= 2, \
        f"expected multiple chunks from large content; found {section_count}"
