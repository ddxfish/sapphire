"""Goals — palace layer 4 (2026-07-11). Chunks-backed goals with subtask_of /
progress_of edges, permanent = never-fades importance, classic tool contracts.

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import json
import re

import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import goal_tools as gt
from plugins.mindpalace.routes import goals_routes as gr


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    return pt


def _gid(msg):
    return int(re.search(r'\[(\d+)\]', msg).group(1))


def _create(title, **kw):
    msg, ok = gt._create('default', title, **kw)
    assert ok, msg
    return _gid(msg)


def test_create_list_complete_lifecycle(palace):
    gid = _create("Get the boat fund started", description="save monthly",
                  priority='high')
    sub = _create("Open the account", parent_id=gid)
    out, ok = gt._list('default')
    assert ok and "Get the boat fund started" in out and "high" in out
    assert "Open the account" in out
    # Complete the subtask, then the goal.
    msg, ok = gt._update('default', sub, status='completed')
    assert ok, msg
    msg, ok = gt._update('default', gid, status='completed',
                         progress_note="account opened, autodraft set")
    assert ok, msg
    out, ok = gt._list('default', goal_id=gid)
    assert "[x]" in out and "autodraft" in out
    # Active view now empty but shows recently completed.
    out, _ = gt._list('default')
    assert "Recently completed" in out


def test_goals_are_chunks_in_the_graph(palace):
    with pt._get_connection() as conn:
        ts = pt._now()
        conn.execute("INSERT INTO entities (name, scope, created, updated) "
                     "VALUES ('Krem', 'default', ?, ?)", (ts, ts))
        conn.commit()
    gid = _create("Finish the boat with Krem")
    with pt._get_connection() as conn:
        layer, meta = conn.execute(
            'SELECT layer, meta FROM chunks WHERE id = ?', (gid,)).fetchone()
        edges = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src_id = ? AND kind = 'mentions'",
            (gid,)).fetchone()[0]
    assert layer == 'goals'
    assert json.loads(meta)['goal_status'] == 'active'
    assert edges == 1                       # goal → Krem, spider-walkable
    # And search_memory finds goals — they're in the mind now.
    out, ok = pt._search_memory("boat", 'default')
    assert ok and f"[{gid}]" in out


def test_permanent_never_fades_and_guards(palace):
    gid = _create("Keep Krem safe", permanent=True)
    with pt._get_connection() as conn:
        imp = conn.execute('SELECT importance FROM chunks WHERE id = ?',
                           (gid,)).fetchone()[0]
    assert imp == gt.PERMANENT_IMPORTANCE   # never-fades band
    # AI can't complete/retitle a permanent goal — only add notes.
    msg, ok = gt._update('default', gid, status='completed')
    assert not ok and 'permanent' in msg
    msg, ok = gt._update('default', gid, progress_note="still holding")
    assert ok, msg
    msg, ok = gt._delete('default', gid)
    assert not ok and 'permanent' in msg
    # UI path has full control (ai=False) + force delete.
    msg, ok = gt._update('default', gid, priority='high', ai=False)
    assert ok, msg
    out = gr.delete_goal(gid=gid, query={'scope': 'default'})
    assert out[1] == 409                    # needs force
    out = gr.delete_goal(gid=gid, query={'scope': 'default', 'force': '1'})
    assert out.get('success')


def test_delete_cascades_subtasks_notes_edges(palace):
    gid = _create("Tidy the workshop")
    sub = _create("Sort the bench", parent_id=gid)
    gt._update('default', gid, progress_note="half done")
    msg, ok = gt._delete('default', gid)
    assert ok and '+2 subtasks/notes' in msg
    with pt._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE layer = 'goals'").fetchone()[0]
        e = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert n == 0 and e == 0


def test_routes_structured_list(palace):
    gid = _create("Ship the palace", description="all layers home")
    _create("Write the tests", parent_id=gid)
    gt._update('default', gid, progress_note="layer 4 landing")
    data = gr.list_goals(query={'scope': 'default', 'status': 'active'})
    g = data['goals'][0]
    assert g['title'] == "Ship the palace"
    assert g['subtasks'][0]['title'] == "Write the tests"
    assert g['progress'][0]['note'] == "layer 4 landing"
    assert data['goals'][0]['status'] == 'active'
    # Notes and subtasks never appear as top-level goals.
    assert len(data['goals']) == 1


def test_instructions_and_due_roundtrip(palace):
    gid = _create("Load the todo list", description="Krem's whole backlog",
                  instructions="Start at the top. Ask Krem when blocked.",
                  due='2026-08-01')
    sub = _create("Fix LLM order", parent_id=gid,
                  description="providers list respects drag order",
                  instructions="settings_manager owns the list; UI reorders")
    # Overview: descriptions surface (the door out of storage), how-notes don't.
    out, ok = gt._list('default')
    assert ok and "providers list respects drag order" in out
    assert "due 2026-08-01" in out
    assert "settings_manager owns" not in out
    # Deep view: instructions for goal AND subtask.
    out, ok = gt._list('default', goal_id=gid)
    assert ok and "Ask Krem when blocked" in out
    assert "settings_manager owns the list" in out
    # Clearing: empty strings remove both fields.
    msg, ok = gt._update('default', sub, instructions='', due='2026-09-01')
    assert ok and 'instructions updated' in msg and 'due → 2026-09-01' in msg
    msg, ok = gt._update('default', sub, due='')
    assert ok and 'due cleared' in msg


def test_due_date_validation(palace):
    msg, ok = gt._create('default', "Bad date", due='next tuesday')
    assert not ok and 'YYYY-MM-DD' in msg
    gid = _create("Good goal")
    msg, ok = gt._update('default', gid, due='08-01-2026')
    assert not ok and 'YYYY-MM-DD' in msg


def test_in_progress_folds_under_active(palace):
    gid = _create("Rebuild the dock")
    msg, ok = gt._update('default', gid, status='in_progress')
    assert ok, msg
    with pt._get_connection() as conn:
        goals = gt._top_level(conn.cursor(), 'default', 'active')
    assert [g['id'] for g in goals] == [gid]     # started = still open
    out, ok = gt._list('default')                # default active view shows it
    assert ok and "in progress" in out


def test_priority_sort_top_level_and_subtasks(palace):
    _create("Medium thing")
    _create("Low thing", priority='low')
    hi = _create("High thing", priority='high')
    _create("Sub low", parent_id=hi, priority='low')
    _create("Sub high", parent_id=hi, priority='high')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        goals = gt._top_level(cur, 'default')
        assert [g['title'] for g in goals] == ["High thing", "Medium thing", "Low thing"]
        subs = gt._subtasks_of(cur, hi, 'default')
        assert [s['title'] for s in subs] == ["Sub high", "Sub low"]


def test_search_goals_layer_returns_whole_goals(palace):
    gid = _create("Ship the palace", description="all layers home")
    _create("Wire the sextant brackets", parent_id=gid,
            description="brass screws only")
    # The hit is the SUBTASK — the presenter resolves it to the parent,
    # rendered whole (subtask descriptions included).
    out, ok = pt._search_memory("sextant brackets", 'default', layer='goals')
    assert ok and "Ship the palace" in out
    assert "brass screws only" in out
    assert f"[{gid}]" in out and "Found 1 goals" in out
    # Mixed (no layer) searches keep plain chunk lines — no detour.
    out, ok = pt._search_memory("sextant brackets", 'default')
    assert ok and "Found" in out and "all layers home" not in out


def test_goal_writes_hit_the_ledger(palace):
    gid = _create("Track me")
    gt._update('default', gid, status='completed')
    gt._delete('default', gid)
    with pt._get_connection() as conn:
        rows = conn.execute(
            "SELECT action FROM ledger WHERE layer = 'goals' AND target = ?",
            (str(gid),)).fetchall()
    assert {r[0] for r in rows} == {'saved', 'updated', 'deleted'}


def test_bulk_subtasks_via_route(palace):
    out = gr.create_goal(body={'scope': 'default', 'title': 'Sapphire Core Code',
                               'description': 'the whole backlog',
                               'subtasks': ['Fix LLM order', ' Fix broken startup ', '']})
    assert out.get('success') and '+2 subtasks' in out['message']
    data = gr.list_goals(query={'scope': 'default'})
    g = data['goals'][0]
    assert {s['title'] for s in g['subtasks']} == {'Fix LLM order', 'Fix broken startup'}
    # Subtask dicts carry the full shape now (the UI's door).
    assert set(g['subtasks'][0]) >= {'description', 'instructions', 'priority',
                                     'due', 'created', 'status'}


def test_goals_publish_on_the_goal_domain(palace):
    from core import mind_events
    assert pt._MIND_DOMAIN['goals'] == 'goal'
    assert 'goal' in mind_events._VALID_DOMAINS


def test_import_goals_from_classic_db(palace, tmp_path):
    import sqlite3
    src = tmp_path / "goals.db"
    conn = sqlite3.connect(src)
    conn.executescript('''
        CREATE TABLE goals (id INTEGER PRIMARY KEY, title TEXT, description TEXT,
            priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'active',
            parent_id INTEGER, scope TEXT DEFAULT 'default',
            created_at TEXT, updated_at TEXT, completed_at TEXT, permanent INTEGER DEFAULT 0);
        CREATE TABLE goal_progress (id INTEGER PRIMARY KEY, goal_id INTEGER,
            note TEXT, created_at TEXT);
        INSERT INTO goals VALUES
            (1, 'Learn the stars', 'navigation basics', 'high', 'active', NULL,
             'default', '2025-01-01 00:00:00', '2025-01-01 00:00:00', NULL, 0),
            (2, 'Buy a sextant', NULL, 'medium', 'completed', 1,
             'default', '2025-01-02 00:00:00', '2025-01-02 00:00:00',
             '2025-02-01 00:00:00', 0),
            (3, 'Stay curious', NULL, 'medium', 'active', NULL,
             'default', '2025-01-03 00:00:00', '2025-01-03 00:00:00', NULL, 1);
        INSERT INTO goal_progress VALUES
            (1, 1, 'found the almanac', '2025-01-05 00:00:00');
    ''')
    conn.commit()
    conn.close()

    from plugins.mindpalace.tools import import_tools as it
    orig = it._source_path
    it._source_path = lambda config, name: tmp_path / name
    try:
        with pt._get_connection() as conn:
            cur = conn.cursor()
            scopes_seen = set()
            copied, skipped, failed = it._import_goals(None, cur, set(), scopes_seen)
            conn.commit()
    finally:
        it._source_path = orig
    assert (copied, failed) == (4, 0)       # 3 goals + 1 note
    with pt._get_connection() as conn:
        cur = conn.cursor()
        goals = gt._top_level(cur, 'default')
        assert {g['title'] for g in goals} == {'Learn the stars', 'Stay curious'}
        stars = next(g for g in goals if g['title'] == 'Learn the stars')
        subs = gt._subtasks_of(cur, stars['id'], 'default')
        assert subs[0]['title'] == 'Buy a sextant'
        assert gt._status_of(subs[0]['meta']) == 'completed'
        notes = gt._notes_of(cur, stars['id'], 'default')
        assert notes[0]['note'] == 'found the almanac'
        perm = next(g for g in goals if g['title'] == 'Stay curious')
        imp = cur.execute('SELECT importance FROM chunks WHERE id = ?',
                          (perm['id'],)).fetchone()[0]
    assert imp == 0.95
    # Idempotent: re-run copies nothing.
    it._source_path = lambda config, name: tmp_path / name
    try:
        with pt._get_connection() as conn:
            cur = conn.cursor()
            keys = {r[0] for r in cur.execute(
                "SELECT json_extract(meta, '$.import_key') FROM chunks "
                "WHERE meta IS NOT NULL").fetchall() if r[0]}
            copied2, skipped2, _ = it._import_goals(None, cur, keys, set())
            conn.commit()
    finally:
        it._source_path = orig
    assert copied2 == 0 and skipped2 == 4
