"""MIND_CHANGED event wiring (2026-04-19).

AIX bug class: AI writes to memory/goal/knowledge/people via a tool, Sapphire
says "done," but the Mind tab stays stale until the user switches tabs or
reloads. User can't tell "tool silently failed" from "tool worked, view
stale." Scout 3 finding.

These tests verify:
  - Events.MIND_CHANGED constant exists
  - core.mind_events.publish_mind_changed validates domain/action, swallows errors
  - Each mutation site publishes MIND_CHANGED with the correct domain/scope/action
"""
import pytest


# ─── publish_mind_changed helper ─────────────────────────────────────────────

def test_mind_changed_event_constant_exists():
    from core.event_bus import Events
    assert hasattr(Events, 'MIND_CHANGED')
    assert Events.MIND_CHANGED == 'mind_changed'


def test_publish_mind_changed_fires_event(event_bus_capture):
    from core.mind_events import publish_mind_changed
    publish_mind_changed('goal', 'default', 'save')
    types = [t for t, _ in event_bus_capture.events]
    assert 'mind_changed' in types
    # Payload shape — `origin` is the requesting tab's X-Session-ID (None from
    # a tool / cron / test: no request in flight), see tests/test_mind_origin.py.
    for t, data in event_bus_capture.events:
        if t == 'mind_changed':
            assert data == {'domain': 'goal', 'scope': 'default', 'action': 'save', 'origin': None}
            break


def test_publish_mind_changed_rejects_unknown_domain(event_bus_capture):
    from core.mind_events import publish_mind_changed
    publish_mind_changed('not-a-domain', 'x', 'save')
    types = [t for t, _ in event_bus_capture.events]
    assert 'mind_changed' not in types


def test_publish_mind_changed_rejects_unknown_action(event_bus_capture):
    from core.mind_events import publish_mind_changed
    publish_mind_changed('goal', 'x', 'bogus-action')
    types = [t for t, _ in event_bus_capture.events]
    assert 'mind_changed' not in types


def test_publish_mind_changed_swallows_publish_failure(monkeypatch):
    """Tool saves must never fail because the publish path errored."""
    from core import mind_events
    import core.event_bus as eb

    def _boom(*a, **k):
        raise RuntimeError("publish broken")

    monkeypatch.setattr(eb, 'publish', _boom)
    # Should not raise
    mind_events.publish_mind_changed('goal', 'default', 'save')


# ─── Goal mutation wiring ────────────────────────────────────────────────────

@pytest.fixture
def isolated_goals(tmp_path, monkeypatch):
    """Point goals DB at a tmp path. Reset module state between tests."""
    from plugins.memory.tools import goals_tools
    monkeypatch.setattr(goals_tools, '_db_path', tmp_path / 'goals.db')
    monkeypatch.setattr(goals_tools, '_db_initialized', False)
    return goals_tools


def _mind_events(capture, **match):
    """Return mind_changed events whose data matches the given keys."""
    out = []
    for t, data in capture.events:
        if t != 'mind_changed':
            continue
        if all(data.get(k) == v for k, v in match.items()):
            out.append(data)
    return out


def test_create_goal_tool_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    _msg, ok = goals._create_goal(title="test goal", scope='default')
    assert ok
    assert _mind_events(event_bus_capture, domain='goal', action='save')


def test_update_goal_tool_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    _msg, ok = goals._create_goal(title="x", scope='default')
    assert ok
    event_bus_capture.events.clear()
    _msg, ok = goals._update_goal(1, scope='default', priority='high')
    assert ok
    assert _mind_events(event_bus_capture, domain='goal', action='update')


def test_delete_goal_tool_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    _msg, ok = goals._create_goal(title="x", scope='default')
    assert ok
    event_bus_capture.events.clear()
    _msg, ok = goals._delete_goal(1, scope='default')
    assert ok
    assert _mind_events(event_bus_capture, domain='goal', action='delete')


def test_create_goal_api_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    goals.create_goal_api(title="ui goal", scope='work')
    assert _mind_events(event_bus_capture, domain='goal', scope='work', action='save')


def test_update_goal_api_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    gid = goals.create_goal_api(title="x", scope='work')
    event_bus_capture.events.clear()
    goals.update_goal_api(gid, priority='high')
    assert _mind_events(event_bus_capture, domain='goal', scope='work', action='update')


def test_add_progress_note_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    gid = goals.create_goal_api(title="x", scope='work')
    event_bus_capture.events.clear()
    goals.add_progress_note(gid, "journal entry")
    assert _mind_events(event_bus_capture, domain='goal', scope='work', action='update')


def test_delete_goal_api_fires_mind_changed(isolated_goals, event_bus_capture):
    goals = isolated_goals
    gid = goals.create_goal_api(title="x", scope='work')
    event_bus_capture.events.clear()
    goals.delete_goal_api(gid)
    assert _mind_events(event_bus_capture, domain='goal', scope='work', action='delete')


# ─── Memory mutation wiring ──────────────────────────────────────────────────

@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    from plugins.memory.tools import memory_tools
    monkeypatch.setattr(memory_tools, '_db_path', tmp_path / 'memory.db')
    monkeypatch.setattr(memory_tools, '_db_initialized', False)
    return memory_tools


def test_save_memory_fires_mind_changed(isolated_memory, event_bus_capture):
    memory = isolated_memory
    _msg, ok = memory._save_memory("remember this", scope='personal')
    assert ok
    assert _mind_events(event_bus_capture, domain='memory', scope='personal', action='save')


def test_delete_memory_fires_mind_changed(isolated_memory, event_bus_capture):
    memory = isolated_memory
    _msg, ok = memory._save_memory("x", scope='personal')
    assert ok
    event_bus_capture.events.clear()
    _msg, ok = memory._delete_memory(1, scope='personal')
    assert ok
    assert _mind_events(event_bus_capture, domain='memory', scope='personal', action='delete')


# ─── Knowledge / people mutation wiring ──────────────────────────────────────

@pytest.fixture
def isolated_knowledge(tmp_path, monkeypatch):
    from plugins.memory.tools import knowledge_tools
    monkeypatch.setattr(knowledge_tools, '_db_path', tmp_path / 'knowledge.db')
    monkeypatch.setattr(knowledge_tools, '_db_initialized', False)
    return knowledge_tools


def test_create_or_update_person_fires_mind_changed_on_save(isolated_knowledge, event_bus_capture):
    knowledge = isolated_knowledge
    pid, is_new = knowledge.create_or_update_person(name="Alice", scope='friends')
    assert is_new
    assert _mind_events(event_bus_capture, domain='people', scope='friends', action='save')


def test_create_or_update_person_fires_mind_changed_on_update(isolated_knowledge, event_bus_capture):
    knowledge = isolated_knowledge
    pid, _ = knowledge.create_or_update_person(name="Alice", scope='friends')
    event_bus_capture.events.clear()
    knowledge.create_or_update_person(name="Alice", relationship='friend', scope='friends')
    assert _mind_events(event_bus_capture, domain='people', scope='friends', action='update')


def test_create_tab_fires_mind_changed(isolated_knowledge, event_bus_capture):
    knowledge = isolated_knowledge
    tab_id = knowledge.create_tab("recipes", scope='default')
    assert tab_id
    assert _mind_events(event_bus_capture, domain='knowledge', scope='default', action='save')


def test_add_entry_fires_mind_changed(isolated_knowledge, event_bus_capture):
    knowledge = isolated_knowledge
    tab_id = knowledge.create_tab("recipes", scope='cooking')
    event_bus_capture.events.clear()
    knowledge.add_entry(tab_id, "pasta: boil water", chunk_index=0)
    assert _mind_events(event_bus_capture, domain='knowledge', scope='cooking', action='save')


def test_delete_entry_fires_mind_changed(isolated_knowledge, event_bus_capture):
    knowledge = isolated_knowledge
    tab_id = knowledge.create_tab("recipes", scope='cooking')
    eid = knowledge.add_entry(tab_id, "pasta", chunk_index=0)
    event_bus_capture.events.clear()
    knowledge.delete_entry(eid)
    assert _mind_events(event_bus_capture, domain='knowledge', scope='cooking', action='delete')


# ─── Frontend wiring guard ───────────────────────────────────────────────────

def test_mind_view_subscribes_to_mind_changed():
    """[REGRESSION_GUARD] Mind view must subscribe to MIND_CHANGED over SSE,
    otherwise the server-side publishes above are noise. Source-level check
    so a future refactor that drops the handler is caught in CI.

    The subscription moved from views/mind.js to shared/mind-common.js
    (subscribeMindDomain) when the Mind view was split into dispatch + per-domain
    views — the guarded behavior lives there now."""
    from pathlib import Path
    mind_js = Path(__file__).parent.parent / 'interfaces/web/static/shared/mind-common.js'
    src = mind_js.read_text()
    assert 'MIND_CHANGED' in src, "mind-common.js must reference MIND_CHANGED event"
    assert 'subscribeMindSse' in src or 'onBusEvent' in src, \
        "mind-common.js must subscribe to the event bus for MIND_CHANGED"


def test_mind_changed_listed_on_frontend_event_bus_constants():
    from pathlib import Path
    eb_js = Path(__file__).parent.parent / 'interfaces/web/static/core/event-bus.js'
    src = eb_js.read_text()
    assert 'MIND_CHANGED' in src, "frontend event-bus.js must export MIND_CHANGED"
    assert "'mind_changed'" in src, "frontend must use exact wire name 'mind_changed'"
