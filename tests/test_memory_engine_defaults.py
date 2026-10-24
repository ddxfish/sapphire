"""Mind Palace is the shipped default memory engine (2026-10); classic memory
is legacy. The loader pins an existing install to what it runs, so only a
fresh install takes the new default (core/plugin_loader.py
_pin_memory_engine_once)."""
import json
import sqlite3
from pathlib import Path

import pytest

import core.plugin_loader as pl

ROOT = Path(__file__).resolve().parent.parent


def test_manifest_defaults_and_legacy_title():
    mem = json.loads((ROOT / 'core/plugins/memory/plugin.json').read_text(encoding='utf-8'))
    mp = json.loads((ROOT / 'core/plugins/mindpalace/plugin.json').read_text(encoding='utf-8'))
    assert mem['default_enabled'] is False and mp['default_enabled'] is True
    assert mem['short_display_name'] == 'Memory (legacy)' and mem['short_name'] == 'Memory System (legacy)'


def _classic(user_dir, memories=0, people=0, goals=0, rag_docs=0):
    user_dir.mkdir(parents=True, exist_ok=True)
    if memories:
        c = sqlite3.connect(user_dir / 'memory.db')
        c.execute('CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)')
        c.executemany('INSERT INTO memories (content) VALUES (?)', [('m',)] * memories)
        c.commit(); c.close()
    if people or rag_docs:
        c = sqlite3.connect(user_dir / 'knowledge.db')
        c.execute('CREATE TABLE knowledge_tabs (id INTEGER PRIMARY KEY, name TEXT, scope TEXT)')
        c.execute('CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY, tab_id INTEGER, content TEXT)')
        c.execute('CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)')
        c.execute("INSERT INTO knowledge_tabs (id, name, scope) VALUES (1, 'doc', '__rag__:chat')")
        c.executemany('INSERT INTO knowledge_entries (tab_id, content) VALUES (1, ?)', [('x',)] * rag_docs)
        c.executemany('INSERT INTO people (name) VALUES (?)', [('p',)] * people)
        c.commit(); c.close()
    if goals:
        c = sqlite3.connect(user_dir / 'goals.db')
        c.execute('CREATE TABLE goals (id INTEGER PRIMARY KEY, title TEXT)')
        c.executemany('INSERT INTO goals (title) VALUES (?)', [('g',)] * goals)
        c.commit(); c.close()


@pytest.fixture
def pin(tmp_path, monkeypatch):
    """(run, state_path, user_dir, notices): run() is the guard against a tmp user/."""
    user_dir = tmp_path / 'user'
    state = user_dir / 'webui' / 'plugins.json'
    monkeypatch.setattr(pl, 'USER_PLUGINS_JSON', state)
    monkeypatch.setattr(pl, 'STATIC_PLUGINS_JSON', tmp_path / 'no-static.json')
    import core.notify as notify
    notices = []
    monkeypatch.setattr(notify, 'notify', lambda *a, **k: notices.append(k.get('key')))
    loader = pl.PluginLoader.__new__(pl.PluginLoader)      # only the list readers/writers are exercised
    return loader._pin_memory_engine_once, state, user_dir, notices


def _lists(state):
    d = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
    return d.get('enabled', []), d.get('disabled', [])


def test_counts_classic_rows_but_not_rag_documents(tmp_path):
    _classic(tmp_path / 'a', memories=2, people=1, goals=3, rag_docs=5)
    assert pl.classic_memory_rows(tmp_path / 'a') == 6
    _classic(tmp_path / 'b', rag_docs=5)
    assert pl.classic_memory_rows(tmp_path / 'b') == 0
    assert pl.classic_memory_rows(tmp_path / 'nothing-here') == 0


def test_fresh_install_takes_the_new_default(pin):
    run, state, user_dir, notices = pin
    run()
    assert not state.exists() and notices == []


def test_rag_documents_alone_are_a_fresh_install(pin):
    run, state, user_dir, notices = pin
    _classic(user_dir, rag_docs=4)
    run()
    assert not state.exists()


def test_classic_on_by_the_old_default_with_data_stays_on(pin):
    run, state, user_dir, notices = pin
    _classic(user_dir, memories=1)
    run()
    assert _lists(state) == (['memory'], ['mindpalace']) and notices == ['memory-engine-pin']
    run()                                                   # idempotent: chosen now
    assert _lists(state) == (['memory'], ['mindpalace']) and len(notices) == 1


def test_classic_explicitly_on_keeps_the_palace_off(pin):
    run, state, user_dir, notices = pin
    state.parent.mkdir(parents=True)
    state.write_text('{"enabled": ["discord", "memory"], "disabled": ["email"]}', encoding='utf-8')
    run()
    assert _lists(state) == (['discord', 'memory'], ['email', 'mindpalace']) and notices == ['memory-engine-pin']


def test_classic_explicitly_off_means_no_engine_as_before(pin):
    run, state, user_dir, notices = pin
    state.parent.mkdir(parents=True)
    state.write_text('{"enabled": [], "disabled": ["memory"]}', encoding='utf-8')
    _classic(user_dir, memories=9)
    run()
    assert _lists(state) == ([], ['memory', 'mindpalace']) and notices == []


def test_palace_already_chosen_is_left_alone(pin):
    run, state, user_dir, notices = pin
    state.parent.mkdir(parents=True)
    state.write_text('{"enabled": ["mindpalace"], "disabled": ["memory"]}', encoding='utf-8')
    _classic(user_dir, memories=9)
    before = state.read_text(encoding='utf-8')
    run()
    assert state.read_text(encoding='utf-8') == before and notices == []
