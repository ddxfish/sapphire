# tests/test_device_registry.py - capabilities.devices registry
# (tmp/device-manager-plan.md). Pure-module tests, no app boot.

import importlib

import core.devices.registry as dr

SSH = {'label': 'SSH machine', 'module': 'device_driver.py', 'capabilities': ['ssh'],
       'config_schema': [{'key': 'host', 'type': 'string'},
                         {'key': 'password', 'type': 'string', 'secret': True}]}


def _fresh():
    return importlib.reload(dr)


def test_register_and_list():
    m = _fresh()
    assert m.register_driver('ssh', SSH, 'ssh')
    drivers = m.list_drivers()
    assert len(drivers) == 1
    d = drivers[0]
    assert d['driver'] == 'ssh' and d['plugin_name'] == 'ssh'
    assert d['capabilities'] == ['ssh'] and d['module'] == 'device_driver.py'
    assert d['config_schema'][1]['secret'] is True
    assert m.get_driver('SSH')['label'] == 'SSH machine'
    assert m.get_driver('nope') is None


def test_bad_declarations_rejected():
    m = _fresh()
    assert not m.register_driver('Bad Id!', SSH, 'p')
    assert not m.register_driver('', SSH, 'p')
    assert not m.register_driver('a', {**SSH, 'capabilities': []}, 'p')
    assert not m.register_driver('a', {**SSH, 'capabilities': ['Not A Slug']}, 'p')
    assert not m.register_driver('a', {**SSH, 'config_schema': 'nope'}, 'p')
    assert not m.register_driver('a', {**SSH, 'config_schema': [{'label': 'no key'}]}, 'p')
    assert not m.register_driver('a', {**SSH, 'config_schema': [{'key': 'k'}] * 41}, 'p')
    assert m.list_drivers() == [] and m.generation() == 0


def test_module_cannot_leave_the_plugin_dir():
    m = _fresh()
    for bad in ('', None, '../evil.py', '/etc/evil.py', 'a/../../evil.py',
                'driver.txt', '..\\evil.py'):
        assert not m.register_driver('a', {**SSH, 'module': bad}, 'p'), bad
    assert m.register_driver('a', {**SSH, 'module': 'drivers/ssh.py'}, 'p')
    assert m.get_driver('a')['module'] == 'drivers/ssh.py'


def test_cross_plugin_shadow_refused():
    m = _fresh()
    assert m.register_driver('ssh', SSH, 'ssh')
    assert not m.register_driver('ssh', {**SSH, 'label': 'Imposter'}, 'evil')
    assert m.get_driver('ssh')['label'] == 'SSH machine'
    # the owner may re-register (plugin reload)
    assert m.register_driver('ssh', {**SSH, 'label': 'SSH v2'}, 'ssh')
    assert m.get_driver('ssh')['label'] == 'SSH v2'


def test_unregister_plugin_and_generation():
    m = _fresh()
    g0 = m.generation()
    m.register_driver('ssh', SSH, 'ssh')
    m.register_driver('fm1', {**SSH, 'capabilities': ['notes', 'sound']}, 'fm1')
    assert m.generation() == g0 + 2
    assert m.unregister_plugin('ssh') == ['ssh']
    assert [d['driver'] for d in m.list_drivers()] == ['fm1']
    assert m.generation() == g0 + 3
    assert m.unregister_plugin('ssh') == [] and m.generation() == g0 + 3


def test_bad_spec_never_raises():
    m = _fresh()
    assert not m.register_driver('a', None, 'p')
    assert not m.register_driver('a', {**SSH, 'config_schema': [{'key': 'k', 'x': object()}]}, 'p')
    assert not m.register_driver(None, {}, 'p')


def test_snapshots_are_copies():
    m = _fresh()
    m.register_driver('ssh', SSH, 'ssh')
    m.list_drivers()[0]['config_schema'].append({'key': 'injected'})
    m.get_driver('ssh')['capabilities'].append('root')
    d = m.get_driver('ssh')
    assert len(d['config_schema']) == 2 and d['capabilities'] == ['ssh']


def test_loader_wires_register_and_unregister():
    """The loader blocks exist beside the games blocks (source-level guard:
    booting a loader here would load every plugin)."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / 'core' / 'plugin_loader.py').read_text(encoding='utf-8')
    assert 'capabilities.get("devices", [])' in src
    assert 'from core.devices.registry import register_driver' in src
    assert 'from core.devices.registry import unregister_plugin as _unreg_drivers' in src


def test_core_drivers_cannot_be_claimed_by_a_plugin():
    m = _fresh()
    assert 'satellite' in m.CORE_DRIVERS and m.CORE == 'core'
    assert not m.register_driver('satellite', SSH, 'evil')          # a core driver's id
    assert not m.register_driver('anything', SSH, 'core')           # a plugin calling itself core
    assert not m.has('satellite') and m.list_drivers() == []
    # the engine registers it, with no module path of its own
    spec = {k: v for k, v in SSH.items() if k != 'module'}
    assert m.register_driver('satellite', spec, 'whoever', builtin=True)
    d = m.get_driver('satellite')
    assert d['plugin_name'] == 'core' and d['module'] == 'satellite.py' and m.has('Satellite')
    assert not m.register_driver('satellite', SSH, 'evil')
    assert m.unregister_plugin('evil') == [] and m.has('satellite')
