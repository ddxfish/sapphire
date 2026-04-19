"""Regression guards for scope-orphan sweep — Scout 2 HIGH finding (2026-04-18).

When a scope is deleted, chats whose settings still reference that scope by
name must be rewritten to 'default'. Without this, apply_scopes_from_settings
on next activation sets the ContextVar to the ghost string and the AI writes
into a room the UI can't show. Same class as the Mind-view hardcoded-scope
bug we fixed same day.

Covers:
  - ChatSessionManager.reset_chat_scope_ref correctness
  - Helper fires from memory_tools.delete_scope
  - Helper fires from goals_tools.delete_scope
  - Helper fires from knowledge_tools.delete_scope
  - Helper fires from knowledge_tools.delete_people_scope
  - Active-chat reload after sweep
"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def session_manager_with_chats(tmp_path, monkeypatch):
    """Spin up a real ChatSessionManager with a tmp SQLite backing store.

    Seeds 3 chats with known scope settings so sweep behavior is observable.
    """
    from core.chat.history import ChatSessionManager
    import config
    monkeypatch.setattr(config, 'CHAT_HISTORY_DIR', str(tmp_path), raising=False)
    monkeypatch.setattr(config, 'BASE_DIR', str(tmp_path), raising=False)

    sm = ChatSessionManager()
    # Redirect the db path (ChatSessionManager resolves in __init__)
    sm._db_path = tmp_path / "chats.db"
    sm._init_db()

    # Seed three chats with known scope settings
    for name, settings in [
        ('chat_work', {'memory_scope': 'work', 'goal_scope': 'work', 'knowledge_scope': 'default', 'people_scope': 'default'}),
        ('chat_home', {'memory_scope': 'home', 'goal_scope': 'default', 'knowledge_scope': 'default', 'people_scope': 'family'}),
        ('chat_default', {'memory_scope': 'default', 'goal_scope': 'default', 'knowledge_scope': 'default', 'people_scope': 'default'}),
    ]:
        sm.create_chat(name)
        sm.set_active_chat(name)
        sm.update_chat_settings(settings)

    return sm


# ─── ChatSessionManager.reset_chat_scope_ref ─────────────────────────────────

def test_reset_chat_scope_ref_rewrites_only_matching_chats(session_manager_with_chats):
    """[REGRESSION_GUARD] Sweep must rewrite only chats whose setting matches
    the deleted scope — sibling chats untouched."""
    sm = session_manager_with_chats

    affected = sm.reset_chat_scope_ref('memory_scope', 'work')
    assert 'chat_work' in affected
    assert 'chat_home' not in affected
    assert 'chat_default' not in affected

    # Verify on disk
    conn = sqlite3.connect(sm._db_path)
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT name, settings FROM chats"):
        s = json.loads(row['settings'])
        if row['name'] == 'chat_work':
            assert s['memory_scope'] == 'default', "target chat not rewritten"
        elif row['name'] == 'chat_home':
            assert s['memory_scope'] == 'home', "home chat was wrongly touched"
        elif row['name'] == 'chat_default':
            assert s['memory_scope'] == 'default', "default chat scope changed"
    conn.close()


def test_reset_chat_scope_ref_empty_when_no_matches(session_manager_with_chats):
    sm = session_manager_with_chats
    affected = sm.reset_chat_scope_ref('memory_scope', 'nonexistent_scope')
    assert affected == []


def test_reset_chat_scope_ref_honors_custom_reset_to(session_manager_with_chats):
    """[PROACTIVE] reset_to parameter lets caller choose something other than 'default'."""
    sm = session_manager_with_chats
    affected = sm.reset_chat_scope_ref('people_scope', 'family', reset_to='archived')
    assert 'chat_home' in affected

    conn = sqlite3.connect(sm._db_path)
    s = json.loads(conn.execute("SELECT settings FROM chats WHERE name='chat_home'").fetchone()[0])
    conn.close()
    assert s['people_scope'] == 'archived'


def test_reset_chat_scope_ref_publishes_events_per_chat(
    session_manager_with_chats, event_bus_capture,
):
    """[PROACTIVE] Every affected chat gets a CHAT_SETTINGS_CHANGED event with
    origin='scope_cleanup' — so the UI can refresh sidebar dropdowns."""
    sm = session_manager_with_chats
    # Seed a second chat with same target scope for this test
    sm.set_active_chat('chat_home')
    sm.update_chat_settings({'memory_scope': 'work'})
    sm.set_active_chat('chat_default')

    affected = sm.reset_chat_scope_ref('memory_scope', 'work')
    assert len(affected) >= 2

    events = [d for ev, d in event_bus_capture.events
              if ev == 'chat_settings_changed' and d.get('origin') == 'scope_cleanup']
    assert len(events) == len(affected), f"expected one event per chat, got {events}"
    for e in events:
        assert 'memory_scope' in e.get('reason', '')


def test_reset_chat_scope_ref_reloads_active_chat(session_manager_with_chats):
    """[REGRESSION_GUARD] If the active chat's settings were rewritten, its
    in-memory current_settings must reload so the next get_chat_settings
    reflects disk state — otherwise the rewrite is invisible until a restart."""
    sm = session_manager_with_chats
    sm.set_active_chat('chat_work')
    assert sm.get_chat_settings()['memory_scope'] == 'work'

    sm.reset_chat_scope_ref('memory_scope', 'work')
    # Active-chat reload was triggered internally
    assert sm.get_chat_settings()['memory_scope'] == 'default'


# ─── Helper fires from each delete_scope call site ───────────────────────────

def _stub_system_with_session_manager(session_manager, monkeypatch):
    """Wire session_manager into a fake system singleton for sweep_orphaned_scope_ref."""
    import core.api_fastapi as apifa
    sys_mock = MagicMock()
    sys_mock.llm_chat.session_manager = session_manager
    monkeypatch.setattr(apifa, '_system', sys_mock, raising=False)


def test_memory_delete_scope_triggers_sweep(session_manager_with_chats, tmp_path, monkeypatch):
    from plugins.memory.tools import memory_tools
    db_path = tmp_path / "memory.db"
    monkeypatch.setattr(memory_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(memory_tools, "_db_initialized", False, raising=False)
    memory_tools._ensure_db()
    memory_tools.create_scope('work')

    _stub_system_with_session_manager(session_manager_with_chats, monkeypatch)

    memory_tools.delete_scope('work')

    # chat_work should have been swept to default
    conn = sqlite3.connect(session_manager_with_chats._db_path)
    s = json.loads(conn.execute("SELECT settings FROM chats WHERE name='chat_work'").fetchone()[0])
    conn.close()
    assert s['memory_scope'] == 'default', "memory sweep didn't fire"


def test_goals_delete_scope_triggers_sweep(session_manager_with_chats, tmp_path, monkeypatch):
    from plugins.memory.tools import goals_tools
    db_path = tmp_path / "goals.db"
    monkeypatch.setattr(goals_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(goals_tools, "_db_initialized", False, raising=False)
    goals_tools._ensure_db()
    # Manually register the scope then delete it
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO goal_scopes (name) VALUES ('work')")

    _stub_system_with_session_manager(session_manager_with_chats, monkeypatch)

    goals_tools.delete_scope('work')

    conn = sqlite3.connect(session_manager_with_chats._db_path)
    s = json.loads(conn.execute("SELECT settings FROM chats WHERE name='chat_work'").fetchone()[0])
    conn.close()
    assert s['goal_scope'] == 'default', "goals sweep didn't fire"


def test_knowledge_delete_scope_triggers_sweep(session_manager_with_chats, tmp_path, monkeypatch):
    from plugins.memory.tools import knowledge_tools
    db_path = tmp_path / "knowledge.db"
    monkeypatch.setattr(knowledge_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(knowledge_tools, "_db_initialized", False, raising=False)
    knowledge_tools._ensure_db()
    knowledge_tools.create_scope('work')
    # Set chat_work's knowledge_scope to 'work' so the sweep has something to hit
    session_manager_with_chats.set_active_chat('chat_work')
    session_manager_with_chats.update_chat_settings({'knowledge_scope': 'work'})

    _stub_system_with_session_manager(session_manager_with_chats, monkeypatch)

    knowledge_tools.delete_scope('work')

    conn = sqlite3.connect(session_manager_with_chats._db_path)
    s = json.loads(conn.execute("SELECT settings FROM chats WHERE name='chat_work'").fetchone()[0])
    conn.close()
    assert s['knowledge_scope'] == 'default', "knowledge sweep didn't fire"


def test_knowledge_delete_people_scope_triggers_sweep(session_manager_with_chats, tmp_path, monkeypatch):
    from plugins.memory.tools import knowledge_tools
    db_path = tmp_path / "knowledge.db"
    monkeypatch.setattr(knowledge_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(knowledge_tools, "_db_initialized", False, raising=False)
    knowledge_tools._ensure_db()
    knowledge_tools.create_people_scope('family')

    _stub_system_with_session_manager(session_manager_with_chats, monkeypatch)

    knowledge_tools.delete_people_scope('family')

    conn = sqlite3.connect(session_manager_with_chats._db_path)
    s = json.loads(conn.execute("SELECT settings FROM chats WHERE name='chat_home'").fetchone()[0])
    conn.close()
    assert s['people_scope'] == 'default', "people_scope sweep didn't fire"


# ─── Helper is fault-tolerant when system singleton not available ────────────

def test_sweep_helper_noop_when_system_unavailable(monkeypatch):
    """Plugin delete_scope must not crash when run before system singleton
    is wired (e.g. boot-time scan or test harness without full stack)."""
    from core.chat import scope_cleanup
    import core.api_fastapi as apifa
    # Force get_system() to raise
    monkeypatch.setattr(apifa, '_system', None, raising=False)

    # Should return empty list, not crash
    result = scope_cleanup.sweep_orphaned_scope_ref('memory_scope', 'anything')
    assert result == []
