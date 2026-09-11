"""get_self_info knows Mind v2 (2026-09-11).

The status plugin used to run raw SQL against the classic v1 files
(user/memory.db, user/knowledge.db) — which Mind Palace's import copies and
never deletes — so a palace box reported ghost v1 counts as current and a
fresh box reported zeros, every failure swallowed into "0". Now the LOADED
memory plugin answers for itself through `status_summary(scope)` (one per
module that owns a DB file) and status merges the answers:

- palace: totals per scope/layer, entities, library docs, and a current-scope
  block (events, this week, entities, knowledge, goals, self-sheet, ledger
  unread WITHOUT stamping the watermark, global overlay, librarian)
- classic: memories / people+knowledge / goals from the three modules, never
  creating a DB file a status poll would otherwise leave behind
- discovery = loader `loaded` flag + sys.modules lookup, never a bare import
- the session scope line is registry-driven (no hardcoded knowledge_scope)
"""
import sys
import json
import types
import sqlite3
from pathlib import Path
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta

import pytest


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec='seconds')


# ─── Mind Palace ─────────────────────────────────────────────────────────────

class _NoEmbedder:
    provider_id = 'test:none'
    available = False

    def embed(self, texts, prefix=''):
        return None


@pytest.fixture
def palace(tmp_path, monkeypatch):
    from plugins.mindpalace.tools import palace_tools as pt
    from plugins.mindpalace.tools import library
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_dark_cache", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _NoEmbedder(), raising=False)
    monkeypatch.setattr(library, "_db_path", tmp_path / "library.db", raising=False)
    monkeypatch.setattr(library, "_db_initialized", False, raising=False)
    monkeypatch.setattr(library, "ensure_worker", lambda: None, raising=False)
    assert pt._ensure_db()
    return pt


def _chunk(pt, layer, scope, content='x', meta=None, created=None, label=None, favorite=0):
    conn = sqlite3.connect(pt._get_db_path())
    now = created or _iso()
    conn.execute(
        "INSERT INTO chunks (layer, scope, content, meta, created, updated, label, favorite) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (layer, scope, content, json.dumps(meta) if isinstance(meta, dict) else meta,
         now, now, label, favorite))
    conn.commit()
    conn.close()


def _entity(pt, name, scope):
    conn = sqlite3.connect(pt._get_db_path())
    conn.execute("INSERT INTO entities (name, scope, created, updated) VALUES (?,?,?,?)",
                 (name, scope, _iso(), _iso()))
    conn.commit()
    conn.close()


def _sql(pt, sql, params=()):
    conn = sqlite3.connect(pt._get_db_path())
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def test_palace_totals_layers_and_current_scope(palace):
    _chunk(palace, 'events', 'default', created=_iso(10))
    _chunk(palace, 'events', 'default')
    _chunk(palace, 'events', 'default')
    _chunk(palace, 'knowledge', 'default')
    _chunk(palace, 'events', 'trinity')
    _chunk(palace, 'events', 'trinity', favorite=1)
    _entity(palace, 'Krem', 'default')
    _entity(palace, 'Rook', 'trinity')
    _sql(palace, "INSERT INTO mind_scopes (name, created) VALUES ('empty', ?)", (_iso(),))

    out = palace.status_summary('trinity')
    assert out['scopes'] == {'default': 4, 'trinity': 2, 'empty': 0}
    assert out['layers'] == {'events': 5, 'knowledge': 1}
    assert out['entities'] == 2
    cur = out['current']
    assert cur['scope'] == 'trinity'
    assert cur['events'] == 2 and cur['events_7d'] == 2
    assert cur['entities'] == 1 and cur['favorites'] == 1
    assert cur['knowledge'] == 0 and cur['global_overlay'] == 0

    cur = palace.status_summary('default')['current']
    assert cur['events'] == 3 and cur['events_7d'] == 2   # the 10-day-old one ages out
    assert cur['knowledge'] == 1 and cur['entities'] == 1


def test_palace_none_scope_is_totals_only(palace):
    _chunk(palace, 'events', 'default')
    out = palace.status_summary(None)
    assert out['current'] is None
    assert out['scopes'] == {'default': 1}


def test_palace_goals_and_self_sheet_filters(palace):
    _chunk(palace, 'goals', 'default', meta={})                                   # active top-level
    _chunk(palace, 'goals', 'default', meta={'goal_status': 'in_progress'})       # open
    _chunk(palace, 'goals', 'default', meta={'goal_status': 'completed'})         # closed
    _chunk(palace, 'goals', 'default', meta={'parent_goal': 1})                   # subtask
    _chunk(palace, 'goals', 'default', meta={'progress_of': 1})                   # note
    _chunk(palace, 'goals', 'default', meta='not json at all')                    # malformed → tolerated
    _chunk(palace, 'self', 'default', meta={'section': 'values'}, label='self-sheet')
    _chunk(palace, 'self', 'default', meta={'section': 'values', 'superseded_at': _iso()},
           label='self-sheet')
    _chunk(palace, 'self', 'default', meta={'section': 'growing'}, label='self-sheet')
    _chunk(palace, 'self', 'default', content='free self note')                   # not the sheet
    cur = palace.status_summary('default')['current']
    assert cur['goals_active'] == 3
    assert cur['self_sections'] == 2


def test_palace_ledger_unread_never_stamps_watermark(palace):
    t1, t2, t3 = _iso(3), _iso(2), _iso(1)
    for ts in (t1, t2, t3):
        _sql(palace, "INSERT INTO ledger (ts, scope, actor, action, summary) VALUES (?,?,?,?,?)",
             (ts, 'default', 'sapphire', 'saved', 'x'))
    _sql(palace, "INSERT INTO ledger (ts, scope, actor, action, summary, parent_id) "
                 "VALUES (?,?,?,?,?,1)", (t3, 'default', 'librarian', 'noted', 'child'))
    assert palace.status_summary('default')['current']['ledger_unread'] == 3   # no watermark = all

    _sql(palace, "INSERT INTO ledger_reads (scope, last_read_ts) VALUES ('default', ?)", (t2,))
    assert palace.status_summary('default')['current']['ledger_unread'] == 1
    conn = sqlite3.connect(palace._get_db_path())
    rows = conn.execute("SELECT scope, last_read_ts FROM ledger_reads").fetchall()
    conn.close()
    assert rows == [('default', t2)]   # a status glance must not mark_read


def test_palace_global_overlay_and_dark_layers(palace):
    _chunk(palace, 'events', 'global')
    _chunk(palace, 'knowledge', 'global')
    _chunk(palace, 'events', 'work')
    # A layer owned by a plugin that is NOT registered = dark: rows survive,
    # every read excludes them — status included.
    _sql(palace, "INSERT INTO layers (key, num, label, owner, created) VALUES ('ghost', 99, 'Ghost', 'someplugin', ?)",
         (_iso(),))
    _chunk(palace, 'ghost', 'global')
    _chunk(palace, 'ghost', 'work')
    palace._dark_cache = None
    out = palace.status_summary('work')
    assert 'ghost' not in out['layers']
    assert out['scopes'] == {'global': 2, 'work': 1, 'default': 0}
    assert out['current']['global_overlay'] == 2
    assert palace.status_summary('global')['current']['global_overlay'] == 0


def test_palace_librarian_block(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda name: {})   # not this box's
    _chunk(palace, 'events', 'default')
    cur = palace.status_summary('default')['current']
    assert cur['librarian'] == {'enabled': False, 'last_pass': None, 'last_pass_at': None}

    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda name: {'librarian_enabled': True})
    _sql(palace, "CREATE TABLE librarian_state (scope TEXT NOT NULL, pass TEXT NOT NULL, "
                 "last_pass TEXT, day TEXT, passes_today INTEGER NOT NULL DEFAULT 0, "
                 "last_result TEXT, PRIMARY KEY (scope, pass))")
    _sql(palace, "INSERT INTO librarian_state (scope, pass, last_pass) VALUES ('default', 'dates', ?)", (_iso(5),))
    _sql(palace, "INSERT INTO librarian_state (scope, pass, last_pass) VALUES ('default', 'sort', ?)", (_iso(1),))
    lib = palace.status_summary('default')['current']['librarian']
    assert lib['enabled'] is True and lib['last_pass'] == 'sort'
    assert lib['last_pass_at'].startswith(_iso(1)[:10])


def test_palace_library_docs(palace):
    from plugins.mindpalace.tools import library
    _chunk(palace, 'events', 'trinity')
    with library.get_connection() as conn:
        conn.execute("INSERT INTO documents (scope, title, kind, status) VALUES ('trinity', 'Doc', 'note', 'ready')")
        conn.execute("INSERT INTO documents (scope, title, kind, status) VALUES ('default', 'Doc2', 'note', 'ready')")
        conn.commit()
    out = palace.status_summary('trinity')
    assert out['library_docs'] == 2
    assert out['current']['library_docs'] == 1


# ─── Classic Memory plugin ───────────────────────────────────────────────────

def _classic(monkeypatch, modname, path):
    import importlib
    mod = importlib.import_module(f"plugins.memory.tools.{modname}")
    monkeypatch.setattr(mod, "_db_path", path, raising=False)
    monkeypatch.setattr(mod, "_db_initialized", False, raising=False)
    monkeypatch.setattr(mod, "_get_embedder", lambda: _NoEmbedder(), raising=False)
    return mod


def test_classic_memory_summary(tmp_path, monkeypatch):
    mem = _classic(monkeypatch, "memory_tools", tmp_path / "memory.db")
    assert mem._ensure_db()
    conn = sqlite3.connect(mem._get_db_path())
    for scope in ('default', 'default', 'work'):
        conn.execute("INSERT INTO memories (content, scope) VALUES ('m', ?)", (scope,))
    conn.execute("INSERT OR IGNORE INTO memory_scopes (name) VALUES ('spare')")
    conn.commit(); conn.close()
    out = mem.status_summary('work')
    assert out['memories'] == 3
    assert out['memory_scopes'] == {'default': 2, 'work': 1}
    assert out['scopes'] == {'default': 2, 'work': 1, 'spare': 0}
    assert out['current'] == {'scope': 'work', 'memories': 1}


def test_classic_missing_db_reports_zero_and_creates_nothing(tmp_path, monkeypatch):
    for modname, fname in (("memory_tools", "memory.db"), ("knowledge_tools", "knowledge.db"),
                           ("goals_tools", "goals.db")):
        mod = _classic(monkeypatch, modname, tmp_path / fname)
        out = mod.status_summary('default')
        assert out['current'] is None
        assert sum(v for v in out.values() if isinstance(v, int)) == 0
        assert not (tmp_path / fname).exists()


def test_classic_knowledge_and_goals_summary(tmp_path, monkeypatch):
    kb = _classic(monkeypatch, "knowledge_tools", tmp_path / "knowledge.db")
    kb._ensure_db()   # classic init returns None
    conn = sqlite3.connect(kb._get_db_path())
    conn.execute("INSERT INTO people (name, scope) VALUES ('Krem', 'work')")
    conn.execute("INSERT INTO knowledge_tabs (name, scope) VALUES ('Boats', 'work')")
    conn.execute("INSERT INTO knowledge_tabs (name, scope) VALUES ('Empty', 'default')")
    conn.execute("INSERT INTO knowledge_entries (tab_id, content) VALUES (1, 'a')")
    conn.execute("INSERT INTO knowledge_entries (tab_id, content) VALUES (1, 'b')")
    conn.commit(); conn.close()
    out = kb.status_summary('work')
    assert out['people'] == 1 and out['people_by_scope'] == {'work': 1}
    assert out['knowledge_total'] == 2 and out['knowledge_scopes'] == {'work': 2, 'default': 0}
    assert out['scopes'] == {'default': 0, 'work': 3}
    assert out['current'] == {'people': 1, 'knowledge': 2}

    goals = _classic(monkeypatch, "goals_tools", tmp_path / "goals.db")
    goals._ensure_db()
    conn = sqlite3.connect(goals._get_db_path())
    conn.execute("INSERT INTO goals (title, scope, status) VALUES ('a', 'work', 'active')")
    conn.execute("INSERT INTO goals (title, scope, status) VALUES ('b', 'work', 'completed')")
    conn.execute("INSERT INTO goals (title, scope, status, parent_id) VALUES ('sub', 'work', 'active', 1)")
    conn.commit(); conn.close()
    out = goals.status_summary('work')
    assert out['goals_active'] == 1 and out['goals_by_scope'] == {'work': 1}
    assert out['current'] == {'goals_active': 1}


# ─── Status plugin: discovery + merge + session scopes ───────────────────────

def _fake_loader(monkeypatch, plugins):
    from core.plugin_loader import plugin_loader
    monkeypatch.setattr(plugin_loader, "_plugins", plugins)


def _manifest(tools, label=None):
    m = {"essential": "memory", "capabilities": {"tools": tools}}
    if label:
        m["short_name"] = label
    return m


def _stub(status_summary):
    mod = types.ModuleType("stub")
    mod.status_summary = status_summary
    return mod


def test_discovery_reads_only_the_loaded_plugin_via_sys_modules(monkeypatch):
    from plugins.status.routes import status as st

    def never(scope):
        raise AssertionError("a NOT-loaded plugin must never be consulted")

    monkeypatch.setitem(sys.modules, "plugins.mindpalace.tools.palace_tools", _stub(never))
    monkeypatch.setitem(sys.modules, "plugins.memory.tools.memory_tools", _stub(
        lambda scope: {'memories': 5, 'scopes': {'default': 4, 'work': 1},
                       'current': {'scope': scope, 'memories': 1}}))
    monkeypatch.setitem(sys.modules, "plugins.memory.tools.knowledge_tools", _stub(
        lambda scope: {'people': 1, 'knowledge_total': 3, 'scopes': {'work': 4, 'kb': 0},
                       'current': {'people': 1, 'knowledge': 3}}))
    monkeypatch.setitem(sys.modules, "plugins.memory.tools.goals_tools", _stub(
        lambda scope: {'goals_active': 2, 'current': {'goals_active': 2}}))
    _fake_loader(monkeypatch, {
        "mindpalace": {"loaded": False, "enabled": False, "path": "/x/plugins/mindpalace",
                       "manifest": _manifest(["tools/palace_tools.py"], "Mind Palace")},
        "memory": {"loaded": True, "enabled": True, "path": "/x/plugins/memory",
                   "manifest": _manifest(["tools/memory_tools.py", "tools/knowledge_tools.py",
                                          "tools/goals_tools.py"], "Memory System")},
        "status": {"loaded": True, "enabled": True, "path": "/x/plugins/status",
                   "manifest": {"capabilities": {}}},
    })
    name, label, fns = st._memory_plugin()
    assert (name, label, len(fns)) == ("memory", "Memory System", 3)
    mind = st._get_mind_info("work", (name, label, fns))
    assert mind["engine"] == "memory" and mind["engine_label"] == "Memory System"
    assert mind["scopes"] == {'default': 4, 'work': 5, 'kb': 0}        # summed across modules
    assert mind["current"] == {'scope': 'work', 'memories': 1, 'people': 1,
                               'knowledge': 3, 'goals_active': 2}      # folded into one block
    assert mind["memories"] == 5 and mind["goals_active"] == 2


def test_discovery_no_plugin_and_module_not_in_sys_modules(monkeypatch):
    from plugins.status.routes import status as st
    _fake_loader(monkeypatch, {"status": {"loaded": True, "manifest": {"capabilities": {}}}})
    assert st._memory_plugin() == (None, None, [])
    assert st._get_mind_info("x", st._memory_plugin()) == {
        "engine": None, "engine_label": None, "scopes": {}, "current": None}

    # Loaded but its canonical module isn't in sys.modules → no import attempted,
    # the block just has no counts (the plugin dir is a namespace package: a bare
    # import would exec it from disk).
    monkeypatch.delitem(sys.modules, "plugins.nomem.tools.tools_x", raising=False)
    _fake_loader(monkeypatch, {"nomem": {"loaded": True, "path": "/x/plugins/nomem",
                                         "manifest": _manifest(["tools/tools_x.py"])}})
    assert st._memory_plugin() == ("nomem", "nomem", [])
    assert "plugins.nomem.tools.tools_x" not in sys.modules


def test_one_failing_module_does_not_sink_the_others(monkeypatch):
    from plugins.status.routes import status as st

    def boom(scope):
        raise RuntimeError("disk on fire")
    fns = [boom, lambda scope: {'memories': 2, 'scopes': {'default': 2}}]
    mind = st._get_mind_info("default", ("memory", "Memory System", fns))
    assert mind["memories"] == 2 and mind["scopes"] == {'default': 2}


def test_session_scopes_follow_the_registry(monkeypatch):
    from plugins.status.routes import status as st
    from core.chat import function_manager as fm
    reg = {
        'memory': {'var': ContextVar('t_mem', default='default'), 'default': 'default',
                   'setting': 'memory_scope', 'plugin': 'mindpalace'},
        'people': {'var': ContextVar('t_ppl', default='default'), 'default': 'default',
                   'setting': 'people_scope', 'plugin': 'mindpalace'},
        'email': {'var': ContextVar('t_em', default='default'), 'default': 'default',
                  'setting': 'email_scope', 'plugin': 'email'},
    }
    monkeypatch.setattr(fm, "SCOPE_REGISTRY", reg)
    out = st._session_scopes({'memory_scope': 'trinity', 'people_scope': 'none',
                              'email_scope': 'work', 'knowledge_scope': 'stale'}, 'mindpalace')
    assert out == {'memory': 'trinity', 'people': None}


def test_norm_scope():
    from plugins.status.routes import status as st
    assert st._norm_scope('none') is None
    assert st._norm_scope('NONE') is None
    assert st._norm_scope('') == 'default'
    assert st._norm_scope(' trinity ') == 'trinity'
    assert st._norm_scope(None) is None


def test_disk_db_list_is_the_real_layout(tmp_path):
    from plugins.status.routes import status as st
    (tmp_path / "history").mkdir(); (tmp_path / "memory").mkdir()
    (tmp_path / "history" / "sapphire_history.db").write_bytes(b"x" * 2048)
    (tmp_path / "memory" / "mind.db").write_bytes(b"x" * 1024)
    (tmp_path / "memory.db").write_bytes(b"x")          # a v1 leftover still shows
    sizes = st._get_disk_info(tmp_path)["db_sizes_mb"]
    assert set(sizes) == {"history/sapphire_history.db", "memory/mind.db", "memory.db"}
    assert "chats.db" not in st._DB_FILES


# ─── The tool's rendering ────────────────────────────────────────────────────

_BASE = {"identity": {}, "session": {"chat": "trinity", "scopes": {"memory": "trinity"}},
         "services": {}, "tasks": {}}


def _render(monkeypatch, mind, registry=None):
    from plugins.status.routes import status as st
    from plugins.status.tools import status_tool
    from core.chat import function_manager as fm
    seen = {}

    def fake(scope=st._FROM_CHAT):
        seen['scope'] = scope
        return {**_BASE, "mind": mind}
    monkeypatch.setattr(st, "get_full_status_sync", fake)
    if registry is not None:
        monkeypatch.setattr(fm, "SCOPE_REGISTRY", registry)
    text, ok = status_tool.execute("get_self_info", {})
    assert ok, text
    return text, seen


def test_tool_passes_the_turns_memory_scope(monkeypatch):
    var = ContextVar('t_mem2', default='default')
    var.set('trinity')
    _, seen = _render(monkeypatch, {"engine": None},
                      registry={'memory': {'var': var, 'default': 'default',
                                           'setting': 'memory_scope', 'plugin': 'x'}})
    assert seen['scope'] == 'trinity'
    _, seen = _render(monkeypatch, {"engine": None}, registry={})
    assert seen['scope'] is None


def test_tool_renders_palace_block(monkeypatch):
    text, _ = _render(monkeypatch, {
        "engine": "mindpalace", "engine_label": "Mind Palace",
        "scopes": {"trinity": 144, "default": 812}, "layers": {"events": 900, "self": 56},
        "entities": 40, "library_docs": 12,
        "current": {"scope": "trinity", "events": 120, "events_7d": 6, "entities": 18,
                    "knowledge": 0, "library_docs": 9, "goals_active": 3, "self_sections": 5,
                    "favorites": 2, "global_overlay": 7, "ledger_unread": 4,
                    "librarian": {"enabled": True, "last_pass": "sort",
                                  "last_pass_at": "2026-09-09T02:00:00+00:00"}}})
    assert "Scopes: memory=trinity" in text
    assert "Mind: Mind Palace | scopes: default (812), trinity (144)" in text
    assert "Layers (chunks): events 900, self 56 | Entity cards: 40 | Library: 12 docs" in text
    assert ("This scope (trinity): 120 events (6 this week) · 18 entities · 9 library docs · "
            "3 active goals · self-sheet 5 sections · 2 favorites · +7 global") in text
    assert "Ledger: 4 new since last read | Librarian: on (last pass: sort 2026-09-09)" in text
    assert "knowledge" not in text.split("This scope")[1].split("\n")[0]   # 0 legacy chunks stay quiet


def test_tool_renders_classic_block_and_off_states(monkeypatch):
    text, _ = _render(monkeypatch, {
        "engine": "memory", "engine_label": "Memory System", "scopes": {"default": 8},
        "memories": 5, "people": 1, "knowledge_total": 3, "goals_active": 1,
        "current": {"scope": "default", "memories": 5, "people": 1, "knowledge": 3, "goals_active": 1}})
    assert "Mind: Memory System | scopes: default (8)" in text
    assert "Memories: 5 | People: 1 | Knowledge: 3 entries | Active goals: 1" in text
    assert "This scope (default): 5 memories · 1 people · 3 knowledge · 1 active goals" in text
    assert "Ledger" not in text

    text, _ = _render(monkeypatch, {"engine": "mindpalace", "engine_label": "Mind Palace",
                                    "scopes": {"default": 1}, "layers": {}, "current": None})
    assert "This chat: memory off" in text and "Layers (chunks): empty" in text

    text, _ = _render(monkeypatch, {"engine": None, "scopes": {}, "current": None})
    assert "Mind: no memory plugin loaded" in text
