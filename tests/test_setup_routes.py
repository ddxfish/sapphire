"""Setup wizard state (2026-10-22, tmp/setup-wizard-plan.md wave 3): the state
door validates, stamps consent server-side and lists the steps that apply; the
featured list follows the loader; the old int step migrates on load."""
import json
import threading
from types import SimpleNamespace

import pytest

import core.api_fastapi  # noqa: F401 — the app wires the routes; a route module imported first is a circular import
from core.routes import setup as setup_routes
from fastapi import HTTPException


@pytest.fixture
def mem_settings(monkeypatch):
    """settings.set/get on a dict, never the disk."""
    store = {}
    sm = setup_routes.settings
    monkeypatch.setattr(sm, 'set', lambda key, value, persist=False, _skip_callbacks=False: store.__setitem__(key, value))
    monkeypatch.setattr(sm, 'get', lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(sm, 'is_managed', lambda: False)
    monkeypatch.setattr(sm, 'is_docker', lambda: False)
    monkeypatch.setattr(setup_routes, '_packages', lambda: {'tts': True, 'stt': False, 'wakeword': True})
    return store


def test_steps_follow_the_install(monkeypatch, mem_settings):
    sm = setup_routes.settings
    assert setup_routes.step_ids() == list(setup_routes.ALL_STEPS)
    monkeypatch.setattr(sm, 'is_docker', lambda: True)
    assert 'audio' not in setup_routes.step_ids() and 'voice' in setup_routes.step_ids()
    monkeypatch.setattr(sm, 'is_managed', lambda: True)
    ids = setup_routes.step_ids()
    assert 'voice' not in ids and 'audio' not in ids and ids[0] == 'consent' and ids[-1] == 'next'


def test_state_reads_the_keys_and_drops_an_unknown_step(mem_settings):
    mem_settings.update(SETUP_COMPLETE=True, SETUP_STEP='brain', SETUP_CONSENT='2026-10-22T18:00:00+00:00')
    s = setup_routes.state()
    assert s['complete'] is True and s['step'] == 'brain' and s['consent'] == '2026-10-22T18:00:00+00:00'
    assert s['packages'] == {'tts': True, 'stt': False, 'wakeword': True}
    assert s['managed'] is False and s['docker'] is False
    mem_settings['SETUP_STEP'] = 'identity'          # the old wizard's step name
    assert setup_routes.state()['step'] == ''


def test_apply_validates_and_stamps_consent(mem_settings):
    for bad in ({'complete': 'yes'}, {'step': 'identity'}, {'step': 3}, {'consent': False}, 'nope'):
        with pytest.raises(HTTPException) as e:
            setup_routes.apply(bad)
        assert e.value.status_code == 400
    assert mem_settings == {}                          # a refused change stores nothing
    out = setup_routes.apply({'step': 'voice', 'consent': True, 'ignored': 1})
    assert mem_settings['SETUP_STEP'] == 'voice'
    assert mem_settings['SETUP_CONSENT'].startswith('20') and '+00:00' in mem_settings['SETUP_CONSENT']
    assert out['step'] == 'voice' and out['consent'] == mem_settings['SETUP_CONSENT']
    setup_routes.apply({'complete': True, 'step': ''})
    assert mem_settings['SETUP_COMPLETE'] is True and mem_settings['SETUP_STEP'] == ''


def test_state_routes(client, mem_settings):
    c, csrf = client
    r = c.get('/api/setup/state')
    assert r.status_code == 200 and r.json()['complete'] is False and r.json()['steps'][0] == 'consent'
    r = c.put('/api/setup/state', json={'step': 'nope'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 400
    r = c.put('/api/setup/state', json={'step': 'plugins'}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200 and r.json()['step'] == 'plugins'


def test_featured_follows_the_loader(monkeypatch, tmp_path):
    (tmp_path / 'discord' / 'web').mkdir(parents=True)
    (tmp_path / 'discord' / 'web' / 'index.js').write_text('', encoding='utf-8')
    infos = {
        'mindpalace': {'name': 'mindpalace', 'enabled': False, 'loaded': False, 'path': str(tmp_path / 'mp'),
                       'manifest': {'icon': '🏛️', 'description': 'long words', 'settingsUI': 'none'}, 'missing_deps': []},
        'memory': {'name': 'memory', 'enabled': True, 'loaded': True, 'path': str(tmp_path / 'mem'),
                   'manifest': {'icon': '🧠', 'capabilities': {'settings': [{'key': 'x'}]}}, 'missing_deps': []},
        'discord': {'name': 'discord', 'enabled': False, 'loaded': False, 'path': str(tmp_path / 'discord'),
                    'manifest': {'icon': '🎮'}, 'missing_deps': ['py-cord']},
        # telegram, email, homeassistant, coding-harness, webcam, screenshot: not scanned here (managed_hide, or not shipped) -> dropped
    }
    import core.plugin_loader as pl
    import core.routes.plugins as rp
    monkeypatch.setattr(pl, 'plugin_loader', SimpleNamespace(get_plugin_info=lambda n: infos.get(n)))
    monkeypatch.setattr(rp, '_plugin_display_title', lambda m, n: n.title())
    monkeypatch.setattr(rp, '_safe_emoji_icon', lambda i: i or '')
    out = setup_routes.featured()
    assert [p['name'] for p in out] == ['mindpalace', 'memory', 'discord']
    mp, mem, dc = out
    assert mp['group'] == 'mind' and mem['group'] == 'mind' and dc['group'] is None
    assert 'has_settings' not in dc                                   # nothing on the step leaves the wizard
    assert mem['legacy'] is True and mp['legacy'] is False and dc['legacy'] is False
    assert mem['enabled'] is True and dc['missing_deps'] == ['py-cord']
    assert mp['title'] == 'Mindpalace' and mp['blurb'] and mp['description'] == 'long words'


def test_featured_ship_on_by_default():
    """2026-10-24: the ungrouped featured plugins arrive ticked on a fresh install
    (`default_enabled` in their manifests; the loader's disabled list still wins).
    The memory pair is a radio: one of them ships on, not both."""
    import json as _json
    from pathlib import Path as _P
    root = _P(setup_routes.__file__).resolve().parents[2] / 'core' / 'plugins'
    names = [n for n, group, _ in setup_routes.FEATURED if group is None]
    assert names, "the wizard offers something"
    for n in names:
        m = _json.loads((root / n / 'plugin.json').read_text(encoding='utf-8'))
        assert m.get('default_enabled') is True, f"{n} is featured but does not ship on"
    assert 'piper' not in names and 'remembrance' not in names     # voice providers wake from the Voice step's cards


def _manager(tmp_path, user, nested):
    """Just what _migrate_setup_state reads: _user, BASE_DIR, _save_lock, _last_mtime."""
    (tmp_path / 'user').mkdir()
    (tmp_path / 'user' / 'settings.json').write_text(json.dumps(nested), encoding='utf-8')
    return SimpleNamespace(_user=dict(user), BASE_DIR=tmp_path, _save_lock=threading.Lock(), _last_mtime=None)


@pytest.mark.parametrize('old,done', [(4, True), (3, True), ('3', True), ('complete', True),
                                      (2, False), (0, False), ('identity', False), (None, False)])
def test_old_step_migrates_on_load(tmp_path, old, done):
    from core.settings_manager import SettingsManager
    m = _manager(tmp_path, {'SETUP_WIZARD_STEP': old, '_wizard_steps': 'words', 'TTS_PROVIDER': 'kokoro'},
                 {'setup': {'SETUP_WIZARD_STEP': old, '_wizard_steps': 'words'}, 'tts': {'TTS_PROVIDER': 'kokoro'}})
    SettingsManager._migrate_setup_state(m)
    assert 'SETUP_WIZARD_STEP' not in m._user and '_wizard_steps' not in m._user
    assert m._user.get('SETUP_COMPLETE', False) is done
    on_disk = json.loads((tmp_path / 'user' / 'settings.json').read_text(encoding='utf-8'))
    assert 'SETUP_WIZARD_STEP' not in on_disk.get('setup', {}) and '_wizard_steps' not in on_disk.get('setup', {})
    assert on_disk.get('setup', {}).get('SETUP_COMPLETE', False) is done
    assert on_disk['tts'] == {'TTS_PROVIDER': 'kokoro'}            # the rest of the file is untouched
    assert m._last_mtime is not None                               # the watcher sees our own write


def test_migration_is_a_no_op_without_the_old_key(tmp_path):
    from core.settings_manager import SettingsManager
    m = _manager(tmp_path, {'SETUP_COMPLETE': False}, {'setup': {'SETUP_COMPLETE': False}})
    before = (tmp_path / 'user' / 'settings.json').read_text(encoding='utf-8')
    SettingsManager._migrate_setup_state(m)
    assert m._user == {'SETUP_COMPLETE': False}
    assert (tmp_path / 'user' / 'settings.json').read_text(encoding='utf-8') == before


def test_a_finished_install_keeps_an_explicit_new_value(tmp_path):
    """SETUP_COMPLETE already written by the new wizard wins over the old int."""
    from core.settings_manager import SettingsManager
    m = _manager(tmp_path, {'SETUP_WIZARD_STEP': 4, 'SETUP_COMPLETE': False}, {'setup': {'SETUP_WIZARD_STEP': 4, 'SETUP_COMPLETE': False}})
    SettingsManager._migrate_setup_state(m)
    assert m._user['SETUP_COMPLETE'] is False
