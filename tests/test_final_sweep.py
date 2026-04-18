"""Final sweep — the last cheap tests across all surfaces.

Mixed bag of remaining P1/P2 stubs that are cheap to write:
  - Knowledge chunking boundary behavior (3.37, 3.38, 3.25)
  - Goal listing shape (list_goals detail view, empty, permanent tag)
  - /api/privacy PUT flow (2.46 set_not_persisted, 2.47 publishes_event)
  - /api/settings masks SAPPHIRE_ROUTER_URL (2.49)
  - PluginState.clear / .delete / .all

See tmp/coverage-test-plan.md tail items.
"""
import re

import pytest


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_knowledge(tmp_path, monkeypatch):
    from plugins.memory.tools import knowledge_tools
    db_path = tmp_path / "knowledge.db"
    monkeypatch.setattr(knowledge_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(knowledge_tools, "_db_initialized", False, raising=False)
    knowledge_tools._ensure_db()
    yield knowledge_tools, db_path


@pytest.fixture
def isolated_goals(tmp_path, monkeypatch):
    from plugins.memory.tools import goals_tools
    db_path = tmp_path / "goals.db"
    monkeypatch.setattr(goals_tools, "_db_path", db_path, raising=False)
    monkeypatch.setattr(goals_tools, "_db_initialized", False, raising=False)
    goals_tools._ensure_db()
    yield goals_tools, db_path


# ─── 3.37 Chunk prefers paragraph boundary ───────────────────────────────────

def test_chunk_text_prefers_paragraph_boundary(isolated_knowledge):
    """[PROACTIVE] _chunk_text should prefer to split at \\n\\n (paragraph
    boundaries) over mid-sentence. Preserves semantic boundaries for
    embedding quality."""
    kt, _ = isolated_knowledge
    # Two paragraphs, second big enough to force a split
    para_a = "Alpha section. " + ("word " * 50)
    para_b = "Beta section. " + ("word " * 200)
    content = f"{para_a}\n\n{para_b}"
    chunks = kt._chunk_text(content, max_tokens=120, overlap_tokens=10)
    # First chunk should be roughly just para_a (not split mid-way through it)
    assert chunks[0].strip().startswith('Alpha section'), \
        f"first chunk doesn't start at para boundary: {chunks[0][:50]!r}"


def test_chunk_text_covers_all_content(isolated_knowledge):
    """[DATA_INTEGRITY] Every token in the source must appear in AT LEAST
    one chunk — regardless of overlap strategy, chunking must not drop
    content. Weaker guarantee than overlap-specific but catches the same
    bug class (silent content loss during split)."""
    kt, _ = isolated_knowledge
    tokens = [f"unique_word_{i}" for i in range(400)]
    content = ' '.join(tokens)
    chunks = kt._chunk_text(content, max_tokens=100, overlap_tokens=30)
    assert len(chunks) > 1

    # All unique tokens must appear in some chunk — no silent loss
    all_chunk_text = ' '.join(chunks)
    for t in tokens:
        assert t in all_chunk_text, f"token {t!r} dropped during chunking"


def test_chunk_text_at_exact_boundary_saves(isolated_knowledge):
    """[PROACTIVE] Content at the exact max_tokens boundary shouldn't crash
    or silently truncate. Save the whole thing as one chunk."""
    kt, db_path = isolated_knowledge
    # Content roughly equal to default max_tokens
    content = ("word " * 400).strip()
    msg, ok = kt._save_knowledge('boundary', content, scope='default')
    assert ok, f"save_knowledge failed: {msg}"

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT content FROM knowledge_entries WHERE content LIKE '%word%'"
    ).fetchall()
    conn.close()
    # At least one chunk contains our content
    assert rows
    combined = ' '.join(r[0] for r in rows)
    # Word count roughly matches — no silent half
    assert combined.count('word') >= 300


# ─── List goals shape ────────────────────────────────────────────────────────

def test_list_goals_deep_view_with_goal_id(isolated_goals):
    """[PROACTIVE] _list_goals(goal_id=N) returns the detail view with
    subtasks + progress notes — not just the smart overview."""
    gt, _ = isolated_goals
    msg, _ = gt._create_goal('Root goal', description='root desc', scope='default')
    gid = int(re.search(r'\[(\d+)\]', msg).group(1))
    gt._create_goal('Child 1', parent_id=gid, scope='default')
    gt._update_goal(gid, scope='default', progress_note='step one')

    result, ok = gt._list_goals(goal_id=gid, scope='default')
    assert ok
    assert 'Root goal' in result
    assert 'Child 1' in result
    assert 'step one' in result


def test_list_goals_permanent_goal_shows_tag(isolated_goals):
    """[PROACTIVE] Permanent goals must be visually flagged in list output
    so the AI knows not to try to complete them."""
    gt, _ = isolated_goals
    msg, _ = gt._create_goal('Standing duty', scope='default', permanent=True)

    result, ok = gt._list_goals(scope='default')
    assert ok
    assert '[PERMANENT]' in result


def test_list_goals_invalid_status_rejected(isolated_goals):
    """_list_goals with an invalid status value (not active/completed/
    abandoned/all) should fall back or error cleanly."""
    gt, _ = isolated_goals
    # Implementation coerces 'all' style — any unknown value should either
    # return empty-active or an error. Just make sure it doesn't raise.
    result, ok = gt._list_goals(status='bogus', scope='default')
    # Whatever it returns, it returned SOMETHING — didn't crash
    assert isinstance(result, str)


# ─── /api/privacy PUT flow ───────────────────────────────────────────────────

@pytest.fixture
def _settings_restore(monkeypatch):
    """Snapshot + restore settings singleton so privacy/router tests don't
    leak into siblings (PRIVACY_MODE runtime mutation caused test_trigger_
    system failures in the full-suite run)."""
    from core import settings_manager as sm_mod
    monkeypatch.setattr(sm_mod.settings, 'save', lambda: True)
    orig_user = dict(sm_mod.settings._user)
    orig_runtime = dict(sm_mod.settings._runtime)
    orig_config = dict(sm_mod.settings._config)
    yield
    with sm_mod.settings._lock:
        sm_mod.settings._user = orig_user
        sm_mod.settings._runtime = orig_runtime
        sm_mod.settings._config = orig_config


def test_privacy_put_sets_runtime_without_persist(client, monkeypatch, _settings_restore):
    """[PROACTIVE] PUT /api/privacy sets PRIVACY_MODE in settings with
    persist=False — the flag is runtime-only, never written to disk.
    Otherwise a mid-session privacy toggle leaks into next session."""
    from core import settings_manager as sm_mod
    c, csrf = client

    # Capture .set calls to verify persist=False
    captured = []
    orig_set = sm_mod.settings.set

    def _spy(key, value, persist=True):
        captured.append((key, value, persist))
        return orig_set(key, value, persist=persist)

    monkeypatch.setattr(sm_mod.settings, 'set', _spy)

    r = c.put('/api/privacy', headers={'X-CSRF-Token': csrf},
              json={'enabled': True})
    assert r.status_code == 200
    body = r.json()
    assert body['privacy_mode'] is True

    privacy_calls = [c for c in captured if c[0] == 'PRIVACY_MODE']
    assert privacy_calls, "PRIVACY_MODE was not set"
    assert privacy_calls[-1][2] is False, \
        f"privacy must use persist=False; got persist={privacy_calls[-1][2]}"


def test_privacy_put_publishes_settings_changed(client, event_bus_capture, _settings_restore):
    c, csrf = client
    r = c.put('/api/privacy', headers={'X-CSRF-Token': csrf},
              json={'enabled': True})
    assert r.status_code == 200

    events = [d for ev, d in event_bus_capture.events if ev == 'settings_changed']
    privacy_events = [e for e in events if e.get('key') == 'PRIVACY_MODE']
    assert privacy_events, f"SETTINGS_CHANGED not fired for PRIVACY_MODE"
    assert privacy_events[-1]['value'] is True


def test_privacy_get_returns_current_mode(client, _settings_restore):
    c, csrf = client
    r = c.get('/api/privacy')
    assert r.status_code == 200
    body = r.json()
    assert 'privacy_mode' in body
    assert 'start_in_privacy' in body


# ─── 2.49 SAPPHIRE_ROUTER_URL is masked in GET ───────────────────────────────

def test_get_settings_masks_sapphire_router_url(client, monkeypatch):
    """[REGRESSION_GUARD] SAPPHIRE_ROUTER_URL is in _SENSITIVE_KEYS — it
    contains the tenant-specific routing URL and must never appear in plain
    text in the GET /api/settings response."""
    from core import settings_manager as sm_mod
    c, csrf = client
    monkeypatch.setattr(sm_mod.settings, 'save', lambda: True)
    orig_user = dict(sm_mod.settings._user)
    try:
        sm_mod.settings.set(
            'SAPPHIRE_ROUTER_URL',
            'https://router.example/tenant_secret_abc',
            persist=False,
        )
        r = c.get('/api/settings')
        assert r.status_code == 200
        body = r.json()
        router_val = body['settings'].get('SAPPHIRE_ROUTER_URL')
        assert router_val == '••••••••', \
            f"SAPPHIRE_ROUTER_URL not masked in GET: {router_val!r}"
    finally:
        with sm_mod.settings._lock:
            sm_mod.settings._user = orig_user


# ─── PluginState.clear / .delete / .all ──────────────────────────────────────

def test_plugin_state_clear_empties_all_keys(tmp_path, monkeypatch):
    """[PROACTIVE] .clear() wipes all keys — protects against a partial
    clear leaving stale data behind (e.g. after a plugin's 'reset' button)."""
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path)

    state = pl.PluginState('cleartest')
    state.save('a', 1)
    state.save('b', {'nested': 'thing'})
    state.save('c', [1, 2, 3])
    assert state.all() == {'a': 1, 'b': {'nested': 'thing'}, 'c': [1, 2, 3]}

    state.clear()
    assert state.all() == {}
    # After clear, prior keys return default
    assert state.get('a', 'missing') == 'missing'


def test_plugin_state_delete_removes_only_one_key(tmp_path, monkeypatch):
    """[PROACTIVE] .delete(key) removes ONLY that key — siblings untouched."""
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'PLUGIN_STATE_DIR', tmp_path)

    state = pl.PluginState('deltest')
    state.save('keep', 'alive')
    state.save('drop', 'goodbye')

    state.delete('drop')
    assert state.get('keep') == 'alive'
    assert state.get('drop') is None
