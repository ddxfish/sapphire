"""Toolsets out of step with the memory engine, and the notification center's
reusable Repair button that fixes them (2026-11-06: a tester switched to the
Mind Palace and lost save/search/delete_knowledge with nothing in their place).
core/toolsets/engine_drift.py, core/notify.py register_repair/repair."""
import inspect
import json
import threading
from pathlib import Path

import pytest

from core.toolsets import engine_drift as ed
from core.toolsets.toolset_manager import ToolsetManager

# main's shipped personality (2026-09), the shape an upgrader carries
MAIN_PERSONALITY = ['prompt_view', 'get_time', 'save_memory', 'search_memory', 'get_recent_memories',
                    'list_entities', 'delete_memory', 'read_self', 'update_self',
                    'save_knowledge', 'search_knowledge', 'delete_knowledge', 'create_goal']


@pytest.fixture
def n(tmp_path, monkeypatch):
    import core.event_bus as eb
    from core import notify
    monkeypatch.setattr(eb, '_bus', eb.EventBus(replay_size=10))
    monkeypatch.setattr(notify, 'STORE', tmp_path / 'notifications.json')
    for k, v in (('_rows', {}), ('_once', []), ('_last_tick', {}), ('_repairs', {}), ('_repairing', set())):
        monkeypatch.setattr(notify, k, v)
    monkeypatch.setattr(notify, '_loaded', False)
    monkeypatch.setattr(notify, '_boot_results', lambda: None)
    return notify


# ── the plan ────────────────────────────────────────────────────────────────

def test_palace_swaps_the_classic_tools_everywhere_and_adds_only_to_builtins():
    p = ed.plan({'personality': {'functions': list(MAIN_PERSONALITY)},
                 'mine': {'functions': ['web_search', 'search_knowledge']},
                 'clean': {'functions': ['web_search']}}, 'mindpalace')
    assert set(p) == {'personality', 'mine'}                       # a clean custom toolset is left out
    assert set(p['personality']['swap']) == {'save_knowledge', 'search_knowledge', 'delete_knowledge'}
    assert 'library' in p['personality']['add'] or 'library' in p['personality']['swap']['search_knowledge']
    assert p['mine']['add'] == []                                   # custom: swaps only
    out = ed.apply(MAIN_PERSONALITY, p['personality'])
    assert not {'save_knowledge', 'search_knowledge', 'delete_knowledge'} & set(out)
    assert {'library', 'read_document'} <= set(out) and len(out) == len(set(out))
    assert out[:2] == ['prompt_view', 'get_time']                   # order kept
    assert ed.plan({'personality': {'functions': out}}, 'mindpalace') == {}   # repaired = in step


def test_classic_gets_the_mirror_repair():
    lw = ['web_search', 'save_memory', 'update_memory', 'library', 'read_document', 'read_self']
    p = ed.plan({'personality': {'functions': list(lw)}}, 'memory')
    out = ed.apply(lw, p['personality'])
    assert 'library' not in out and 'read_document' not in out
    assert 'update_memory' in out                                   # no whole round trip: left alone
    assert {'search_knowledge', 'save_knowledge', 'delete_knowledge'} <= set(out)
    assert 'read_self' in out                                       # no classic twin: left alone, never deleted
    assert ed.plan({'personality': {'functions': out}}, 'memory') == {}


def test_no_engine_no_plan():
    assert ed.plan({'personality': {'functions': list(MAIN_PERSONALITY)}}, 'none') == {}


def test_the_tables_name_real_tools():
    palace, classic = ed.tools_of('mindpalace'), ed.tools_of('memory')
    assert palace and classic
    for old, new in ed.SWAPS['mindpalace'].items():
        assert old in classic and old not in palace and set(new) <= palace
    for old, new in ed.SWAPS['memory'].items():
        assert old in palace and old not in classic and set(new) <= classic
    for tools in ed.CLASSIC_ADDS.values():
        assert set(tools) <= classic


# ── the manager applies it ──────────────────────────────────────────────────

def _mgr(tmp_path, data):
    (tmp_path / "toolsets.json").write_text(json.dumps(data), encoding="utf-8")
    tm = ToolsetManager.__new__(ToolsetManager)
    tm.BASE_DIR = Path(inspect.getfile(ToolsetManager)).parent
    tm.USER_DIR = tmp_path
    tm._toolsets, tm._lock, tm._last_mtimes = {}, threading.Lock(), {}
    tm._watcher_thread, tm._watcher_running = None, False
    tm._load()
    return tm


def test_repair_writes_once_and_keeps_the_ledger(tmp_path):
    tm = _mgr(tmp_path, {'_migrations': ['help_docs_2026_11'],
                         'personality': {'functions': list(MAIN_PERSONALITY), 'emoji': 'x'}})
    assert tm.repair_engine_drift('mindpalace') == ['personality']
    disk = json.loads((tmp_path / "toolsets.json").read_text(encoding="utf-8"))
    assert 'save_knowledge' not in disk['personality']['functions']
    assert disk['personality']['emoji'] == 'x' and 'help_docs_2026_11' in disk['_migrations']
    assert tm.repair_engine_drift('mindpalace') == []


def test_an_early_migration_save_keeps_the_once_add_ledger(tmp_path):
    """Two scouts, 2026-11-06: the seed/rename saves ran before _migrations
    was read, dropped it from the file, and the once-adds re-ran next boot."""
    _mgr(tmp_path, {'_migrations': ['help_docs_2026_11'],
                    'personality': {'functions': ['view_prompt']}})       # rename + 'default' seed both save
    disk = json.loads((tmp_path / "toolsets.json").read_text(encoding="utf-8"))
    assert disk['_migrations'] == ['help_docs_2026_11']
    assert 'search_help_docs' not in disk['personality']['functions']    # not re-added


# ── the reusable Repair button ──────────────────────────────────────────────

def test_a_row_wears_a_repair_and_the_button_runs_it(n):
    calls = []
    n.register_repair('fix-it', lambda: calls.append(1) or 'Fixed the thing.')
    rid = n.notify('Something is off', 'warning', 'details', key='k', action='fix-it')
    assert n.get(rid)['action'] == {'repair': 'fix-it', 'label': 'Repair'}
    ok, words = n.repair(rid)
    row = n.get(rid)
    assert ok and words == 'Fixed the thing.' and calls == [1]
    assert row['level'] == 'success' and row['state'] == 'done' and row['action'] is None
    with pytest.raises(LookupError):                                  # the button is spent
        n.repair(rid)


def test_a_failed_repair_says_why_and_keeps_the_button(n):
    def boom():
        raise RuntimeError('disk full')
    n.register_repair('fix-it', boom)
    rid = n.notify('Something is off', 'warning', action={'repair': 'fix-it', 'label': 'Fix'})
    ok, words = n.repair(rid)
    row = n.get(rid)
    assert not ok and words == 'disk full'
    assert row['level'] == 'error' and 'disk full' in row['body'] and row['action']['label'] == 'Fix'


def test_unknown_or_running_repairs_refuse(n):
    rid = n.notify('Off', 'warning', action='nobody')
    with pytest.raises(LookupError):
        n.repair(rid)
    n.register_repair('slow', lambda: 'ok')
    rid2 = n.notify('Off', 'warning', action='slow')
    n._repairing.add('slow')
    with pytest.raises(RuntimeError):
        n.repair(rid2)


def test_check_posts_and_clears_the_drift_row(n, monkeypatch):
    from core.toolsets import toolset_manager
    state = {'personality': {'functions': list(MAIN_PERSONALITY)}}
    monkeypatch.setattr(toolset_manager, 'get_all_toolsets', lambda: state)
    monkeypatch.setattr(ed, '_engine', lambda: 'mindpalace')
    rid = ed.check()
    row = n.get(rid)
    assert row['action']['repair'] == ed.REPAIR and 'old memory tools' in row['body'] and len(row['body']) < 200
    state['personality']['functions'] = ['save_memory']
    monkeypatch.setattr(ed, '_adds', lambda engine: {})
    assert ed.check() is None and n.get(rid) is None


def test_a_round_trip_keeps_a_custom_toolset_whole():
    """Live test 2026-11-06: classic and back must not shed a custom toolset's tools."""
    rook = ['web_search', 'save_memory', 'search_memory', 'update_memory', 'library', 'read_document']
    to_classic = ed.apply(rook, ed.plan({'rook': {'functions': rook}}, 'memory')['rook'])
    back = ed.apply(to_classic, ed.plan({'rook': {'functions': to_classic}}, 'mindpalace')['rook'])
    assert set(back) == set(rook)
