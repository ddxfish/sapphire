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


def _palace(user_dir, rows=0):
    (user_dir / 'memory').mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(user_dir / 'memory' / 'mind.db')
    c.execute('CREATE TABLE chunks (id INTEGER PRIMARY KEY, content TEXT)')
    c.execute('CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)')
    c.executemany('INSERT INTO chunks (content) VALUES (?)', [('m',)] * rows)
    c.commit(); c.close()


@pytest.fixture
def pin(tmp_path, monkeypatch):
    """(run, state_path, user_dir, notices): run() is the guard against a tmp user/."""
    user_dir = tmp_path / 'user'
    state = user_dir / 'webui' / 'plugins.json'
    monkeypatch.setattr(pl, 'USER_PLUGINS_JSON', state)
    import core.notify as notify
    notices = []
    monkeypatch.setattr(notify, 'notify', lambda *a, **k: notices.append(k))
    loader = pl.PluginLoader.__new__(pl.PluginLoader)      # only the list readers/writers are exercised
    return loader._pin_memory_engine_once, state, user_dir, notices, state.with_name('memory-engine.json')


def _engine(efile):
    return json.loads(efile.read_text(encoding='utf-8')).get('engine') if efile.exists() else None


def _set_engine(efile, engine):
    efile.parent.mkdir(parents=True, exist_ok=True)
    efile.write_text(json.dumps({'engine': engine}), encoding='utf-8')


def _lists(state):
    d = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
    return d.get('enabled', []), d.get('disabled', [])


def _pinned(state):
    return 'memory-engine' in json.loads(state.read_text(encoding='utf-8')).get('pinned', [])


def _state(state, **lists):
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(lists), encoding='utf-8')


def test_counts_classic_rows_but_not_rag_documents(tmp_path):
    _classic(tmp_path / 'a', memories=2, people=1, goals=3, rag_docs=5)
    assert pl.classic_memory_rows(tmp_path / 'a') == 6
    _classic(tmp_path / 'b', rag_docs=5)
    assert pl.classic_memory_rows(tmp_path / 'b') == 0
    assert pl.classic_memory_rows(tmp_path / 'nothing-here') == 0


def test_a_store_that_will_not_open_is_not_an_empty_one(tmp_path):
    (tmp_path / 'memory.db').write_bytes(b'not a database at all' * 100)
    assert pl.classic_memory_rows(tmp_path) is None
    c = sqlite3.connect(tmp_path / 'knowledge.db')                  # an older schema: no people table
    c.execute('CREATE TABLE knowledge_tabs (id INTEGER PRIMARY KEY, name TEXT, scope TEXT)')
    c.execute('CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY, tab_id INTEGER, content TEXT)')
    c.commit(); c.close()
    (tmp_path / 'memory.db').unlink()
    assert pl.classic_memory_rows(tmp_path) == 0
    _palace(tmp_path, rows=3)
    assert pl.palace_memory_rows(tmp_path) == 3
    assert pl.palace_memory_rows(tmp_path / 'nothing-here') == 0


def test_a_fresh_install_takes_the_new_default_and_the_verdict_is_recorded(pin):
    run, state, user_dir, notices, efile = pin
    assert run() == ({'pinned': ['memory-engine'], 'disabled': ['memory']}, {})
    assert _lists(state) == ([], ['memory']) and _pinned(state) and notices == []
    assert _engine(efile) == 'mindpalace'                    # the verdict's durable home (H8)
    _classic(user_dir, memories=3)                           # a classic row appears later (a restore, a copied user/)
    assert run() == ({}, {})
    assert _lists(state) == ([], ['memory'])                 # decided: never re-judged


def test_rag_documents_alone_are_a_fresh_install(pin):
    run, state, user_dir, notices, efile = pin
    _classic(user_dir, rag_docs=4)
    run()
    assert _lists(state) == ([], ['memory']) and _pinned(state)


def test_classic_on_by_the_old_default_with_data_stays_on(pin):
    run, state, user_dir, notices, efile = pin
    _classic(user_dir, memories=1)
    run()
    assert _lists(state) == (['memory'], ['mindpalace']) and _pinned(state)
    assert [n['key'] for n in notices] == ['memory-engine-pin']
    assert notices[0]['level'] == 'warning' and notices[0]['go'] == {'view': 'settings', 'tab': 'plugins'}
    run()                                                   # idempotent: chosen now
    assert _lists(state) == (['memory'], ['mindpalace']) and len(notices) == 1


def test_classic_explicitly_on_keeps_the_palace_off(pin):
    run, state, user_dir, notices, efile = pin
    _state(state, enabled=['discord', 'memory'], disabled=['email'])
    run()
    assert _lists(state) == (['discord', 'memory'], ['email', 'mindpalace']) and len(notices) == 1


def test_classic_explicitly_off_means_no_engine_as_before(pin):
    run, state, user_dir, notices, efile = pin
    _state(state, enabled=[], disabled=['memory'])
    _classic(user_dir, memories=9)
    run()
    assert _lists(state) == ([], ['memory', 'mindpalace']) and notices == []


def test_palace_already_chosen_is_left_alone(pin):
    run, state, user_dir, notices, efile = pin
    _state(state, enabled=['mindpalace'], disabled=['memory'])
    _classic(user_dir, memories=9)
    run()
    assert _lists(state) == (['mindpalace'], ['memory']) and _pinned(state) and notices == []


def test_a_stale_palace_entry_from_the_old_toggle_bug_yields_to_classic_data(pin):
    run, state, user_dir, notices, efile = pin                     # A3: Mind pressed on a build that wrote enabled, then failed
    _state(state, enabled=['mindpalace'], disabled=[])
    _classic(user_dir, memories=40)
    assert run() == ({'pinned': ['memory-engine'], 'enabled': ['memory'], 'disabled': ['mindpalace']},
                     {'enabled': ['mindpalace']})
    assert _lists(state) == (['memory'], ['mindpalace']) and len(notices) == 1


def test_a_palace_install_with_stray_classic_rows_stays_on_the_palace(pin):
    run, state, user_dir, notices, efile = pin                     # A1 strays, a layer mirror, an old experiment
    _state(state, enabled=['mindpalace'], disabled=[])
    _classic(user_dir, memories=2)
    _palace(user_dir, rows=200)
    run()
    assert _lists(state) == (['mindpalace'], ['memory']) and _pinned(state) and notices == []


def test_both_engines_on_is_settled_by_who_holds_the_data(pin):
    run, state, user_dir, notices, efile = pin                     # A8: the collision toast every boot
    _state(state, enabled=['memory', 'mindpalace'], disabled=[])
    _classic(user_dir, memories=500)
    _palace(user_dir, rows=3)
    run()
    assert _lists(state) == (['memory'], ['mindpalace']) and 'holds your data' in notices[0]['body']
    efile.unlink()                                          # another install with the same shape
    _state(state, enabled=['memory', 'mindpalace'], disabled=[])
    c = sqlite3.connect(user_dir / 'memory' / 'mind.db')
    c.executemany('INSERT INTO chunks (content) VALUES (?)', [('m',)] * 600); c.commit(); c.close()
    run()
    assert _lists(state) == (['mindpalace'], ['memory']) and 'Both engines' in notices[1]['body']


def test_a_tie_goes_to_classic_and_nothing_to_the_palace(pin):
    run, state, user_dir, notices, efile = pin
    _classic(user_dir, memories=5)
    _palace(user_dir, rows=5)
    run()
    assert _lists(state) == (['memory'], ['mindpalace'])


def test_the_verdict_holds_for_the_boot_when_the_file_cannot_be_written(pin, monkeypatch):
    run, state, user_dir, notices, efile = pin                     # A4: AV, OneDrive, a read-only user/
    _classic(user_dir, memories=7)
    monkeypatch.setattr(pl.PluginLoader, '_append_plugins_json', lambda self, pins, drop=None: (_ for _ in ()).throw(PermissionError('held')))
    add, drop = run()
    assert add['enabled'] == ['memory'] and add['disabled'] == ['mindpalace'] and not state.exists()


def test_an_unreadable_store_is_classic_for_the_boot_and_never_recorded(pin):
    run, state, user_dir, notices, efile = pin                     # A5: a locked file, a UNC share
    user_dir.mkdir(parents=True)
    (user_dir / 'memory.db').write_bytes(b'garbage' * 100)
    add, drop = run()
    assert add == {'enabled': ['memory'], 'disabled': ['mindpalace']} and not state.exists() and notices == []
    _palace(user_dir, rows=50)                              # unless the palace is known to hold more
    assert run() == ({'disabled': ['memory']}, {}) and not state.exists() and not efile.exists()


def test_a_malformed_list_in_plugins_json_is_ignored_not_fatal(pin):
    run, state, user_dir, notices, efile = pin                     # A6
    _state(state, enabled=None, disabled='memory', pinned=[3, 'x'])
    loader = pl.PluginLoader.__new__(pl.PluginLoader)
    assert loader._get_enabled_list() == [] and loader._get_disabled_list() == []
    run()
    assert _lists(state) == ([], ['memory']) and _pinned(state)


def test_scan_applies_the_pins_it_could_not_write(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, 'USER_PLUGINS_JSON', tmp_path / 'user' / 'webui' / 'plugins.json')
    loader = pl.PluginLoader.__new__(pl.PluginLoader)
    seen = {}
    monkeypatch.setattr(loader, '_pin_memory_engine_once',
                        lambda: ({'enabled': ['memory'], 'disabled': ['mindpalace']}, {'enabled': ['mindpalace']}))
    monkeypatch.setattr(loader, '_pin_featured_once', lambda: None)
    monkeypatch.setattr(loader, '_get_enabled_list', lambda: ['mindpalace', 'discord'])
    monkeypatch.setattr(loader, '_get_disabled_list', lambda: [])
    monkeypatch.setattr(loader, '_band_move_tripwires', lambda: None)
    monkeypatch.setattr(loader, '_scan_dir', lambda d, band, enabled_list, disabled_list: seen.update(enabled=list(enabled_list), disabled=list(disabled_list)))
    loader._plugins = {}
    try:
        loader.scan()
    except Exception:
        pass                                                # whatever follows the two scans is not under test
    assert seen == {'enabled': ['discord', 'memory'], 'disabled': ['mindpalace']}


def test_ro_uri_keeps_a_share_readable_and_encodes_the_odd_characters():
    from pathlib import PurePosixPath, PureWindowsPath
    from core.fs_utils import ro_uri
    assert ro_uri(PureWindowsPath(r'\\nas\share\sapphire\user\memory.db')) == 'file:////nas/share/sapphire/user/memory.db?mode=ro'
    assert ro_uri(PureWindowsPath(r'C:\Users\me\sapphire\user\memory.db')) == 'file:///C:/Users/me/sapphire/user/memory.db?mode=ro'
    assert ro_uri(PurePosixPath('/home/me/sapphire #1/user/50%.db')) == 'file:///home/me/sapphire%20%231/user/50%25.db?mode=ro'


def test_palace_off_with_classic_unplaced_runs_classic(pin):
    """The old toggle bug's other leftover: the palace switched on then off,
    classic never touched. Before the upgrade classic ran by the old default;
    recording that shape as "placed" left the install with no engine (hunt
    of the hunt H7)."""
    run, state, user_dir, notices, efile = pin
    _state(state, enabled=[], disabled=['mindpalace'])
    _classic(user_dir, memories=12)
    run()
    assert _lists(state) == (['memory'], ['mindpalace']) and _engine(efile) == 'memory'
    assert len(notices) == 1


def test_an_install_pinned_before_the_setting_existed_gets_one_from_its_lists(pin):
    run, state, user_dir, notices, efile = pin
    _state(state, enabled=['memory'], disabled=['mindpalace'], pinned=['memory-engine'])
    assert run() == ({}, {}) and _engine(efile) == 'memory'
    _state(state, enabled=[], disabled=[], pinned=['memory-engine'])
    efile.unlink(); run()
    assert _engine(efile) == 'mindpalace'
    _state(state, enabled=[], disabled=['memory', 'mindpalace'], pinned=['memory-engine'])
    efile.unlink(); run()
    assert _engine(efile) == 'none'


def test_the_setting_restores_the_lists_without_a_data_vote(pin, monkeypatch):
    """plugins.json quarantined or lost: the verdict lives in MEMORY_ENGINE and
    the lists are made to agree with it; the data never votes again (H8)."""
    run, state, user_dir, notices, efile = pin
    _set_engine(efile, 'mindpalace')
    _classic(user_dir, memories=5000)                        # would win any vote
    monkeypatch.setattr(pl, 'classic_memory_rows', lambda d: (_ for _ in ()).throw(AssertionError('voted')))
    assert run() == ({'disabled': ['memory'], 'pinned': ['memory-engine']}, {})
    assert _lists(state) == ([], ['memory']) and notices == []
    _state(state, enabled=['memory'], disabled=['mindpalace'])  # a stale file from before the choice
    assert run() == ({'disabled': ['memory'], 'pinned': ['memory-engine']}, {'enabled': ['memory'], 'disabled': ['mindpalace']})
    assert _lists(state) == ([], ['memory'])
    _set_engine(efile, 'memory')
    run()
    assert _lists(state) == (['memory'], ['mindpalace'])
    _set_engine(efile, 'none')
    run()
    assert _lists(state) == ([], ['mindpalace', 'memory'])


# --- hunt 20: the featured plugins (default_enabled since 2026-10-24) -------

@pytest.fixture
def featured(tmp_path, monkeypatch):
    state = tmp_path / 'user' / 'webui' / 'plugins.json'
    monkeypatch.setattr(pl, 'USER_PLUGINS_JSON', state)
    import core.notify as notify
    notices = []
    monkeypatch.setattr(notify, 'notify', lambda *a, **k: notices.append(k.get('key')))
    from core.settings_manager import settings
    vals = {}
    monkeypatch.setattr(settings, 'get', lambda k, d=None: vals.get(k, d))
    loader = pl.PluginLoader.__new__(pl.PluginLoader)
    return loader._pin_featured_once, state, vals, notices


def test_an_upgrader_never_wakes_up_with_a_shell_and_a_camera(featured):
    run, state, vals, notices = featured
    vals['SETUP_COMPLETE'] = True                          # the old wizard, no SETUP_CONSENT
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({'enabled': ['telegram'], 'disabled': ['email']}))
    run()
    enabled, disabled = _lists(state)
    assert 'telegram' in enabled and 'telegram' not in disabled          # chosen: stays on
    for n in ('coding-harness', 'webcam', 'screenshot', 'discord', 'homeassistant', 'email'):
        assert n in disabled
    assert notices == ['featured-pin']
    before = state.read_text()
    run()                                                               # once per install
    assert state.read_text() == before and notices == ['featured-pin']


def test_a_fresh_install_or_the_new_wizard_keeps_the_defaults(featured):
    run, state, vals, notices = featured
    run()                                                               # fresh: setup not complete
    assert _lists(state) == ([], []) and notices == []
    vals.update(SETUP_COMPLETE=True)
    run()                                                               # marked done: never later
    assert _lists(state) == ([], [])
    state.unlink()
    vals.update(SETUP_CONSENT='2026-10-25T10:00:00+00:00')
    run()                                                               # walked the new wizard
    assert _lists(state) == ([], []) and notices == []
