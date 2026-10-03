# tests/test_devices_presence.py - things that come and go (core/devices/presence.py).
# The REAL engine and the REAL registry, with one fake driver that keeps
# presence. Nothing here touches user/, no process starts, no hardware is asked.

import importlib
import sys
import threading
import time
import types
from unittest.mock import patch

import pytest

from core.devices import engine as core
import core.devices.health as health
from core.devices import presence

MOD = 'plugins.fakeplug.keys_driver'
SCHEMA = [
    {'key': 'sources', 'type': 'found', 'label': 'Plays through it', 'all_label': 'Every one'},
    {'key': 'instrument', 'type': 'string', 'label': 'Instrument', 'default': 'piano'},
]
DRIVER = {'label': 'Fake keys', 'module': 'keys_driver.py', 'capabilities': ['sound'],
          'config_schema': SCHEMA, 'presence': True}
AKM = {'id': 'usb-akm', 'name': 'AKM320', 'kind': 'USB'}
FM1 = {'id': 'name:FM-1_BLE', 'name': 'FM-1_BLE', 'kind': 'Bluetooth'}


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def update_with_lock(self, k, mutator, default=None):
        self.d[k] = mutator(self.d.get(k, default))
        return self.d[k]


def _until(test, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.01)
    return bool(test())


@pytest.fixture
def rig():
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    box = types.SimpleNamespace(here=[AKM], tends=[], watches=0, changed=None, hold=None,
                                watch_fails=False, tend_fails=False, store=FakeStore(), reg=reg)
    m = types.ModuleType(MOD)
    m.describe = lambda device, config: {'sound': {'label': 'Sound', 'help': 'sound', 'actions': {
        'on': {'help': 'on', 'example': ''}}}}
    m.status = lambda device, config, secrets: {'online': True, 'detail': 'fine', 'readings': {'sound': 'on'}}
    m.run = lambda *a: ('done', True)
    m.discover = lambda config: list(box.here)

    def watch(changed, stopped):
        box.watches += 1
        if box.watch_fails:
            raise RuntimeError('the wire fell out')
        box.changed = changed
        stopped.wait()

    def tend(device, config, secrets, present, leaving):
        if box.hold is not None:
            box.hold.wait(3)
        box.tends.append((device['id'], [t['name'] for t in present], leaving))
        if box.tend_fails:
            raise RuntimeError('the synth fell over')

    m.watch, m.tend = watch, tend
    with patch.object(core, '_store', lambda: box.store), \
         patch.object(core, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
         patch.object(core, '_all_plugin_info', lambda: []), \
         patch.object(core, '_managed', lambda: False), \
             patch.object(health, '_store', lambda: types.SimpleNamespace(get=lambda k, d=None: d, save=lambda k, v: None)), \
         patch.object(core, '_part_secrets', lambda device_id, driver_id: sec.Secrets(device_id, {})), \
         patch.object(sec, 'delete', lambda device_id: True), \
         patch.object(sec, 'status', lambda device_id: {}), \
         patch.object(presence, 'SETTLE', 0), patch.object(presence, 'GATHER', 0.02), \
         patch.object(presence, 'HEARTBEAT', 30), patch.object(presence, 'LAST_WORD', 2), \
         patch.object(presence, 'WATCH_RETRY', (0.05,)):
        importlib.reload(reg)
        reg.CORE_DRIVERS = ()
        core._modules.clear()
        core._modules_gen = None
        health._belief.clear(); health._saved = None
        sys.modules[MOD] = m
        assert reg.register_driver('keys', DRIVER, 'fakeplug')
        box.mod = m
        try:
            yield box
        finally:
            if box.hold is not None:
                box.hold.set()
            presence.stop()
            for held in (presence._told, presence._busy, presence._watchers):
                held.clear()
            presence._pending.clear()
            presence._again.clear()
            presence._wake.clear()
            sys.modules.pop(MOD, None)
            core._modules.clear()
            core._modules_gen = None
            health._belief.clear(); health._saved = None
            importlib.reload(reg)


def _add(device_id='keyboard', **config):
    return core.add(device_id, 'My keyboard', 'keys', config)[0]


def _told(box, device_id='keyboard'):
    return [t for t in box.tends if t[0] == device_id]


# --- the filter ------------------------------------------------------------------

def test_a_filter_is_stored_as_it_is_meant(rig):
    row = _add()
    assert row['parts'][0]['config']['sources'] == {'all': True, 'only': []}      # never set = everything
    row, _ = core.update('keyboard', parts={'keys': {'sources': {
        'all': False, 'only': [{'id': 'usb-akm', 'name': 'AKM320'}, {'id': 'usb-akm', 'name': 'twice'},
                               {'name': 'no id'}, 'junk', {'id': '  name:FM-1_BLE  '}]}}})
    assert row['parts'][0]['config']['sources'] == {'all': False, 'only': [
        {'id': 'usb-akm', 'name': 'AKM320'}, {'id': 'name:FM-1_BLE', 'name': 'name:FM-1_BLE'}]}
    for junk in ('everything', None, 7, ['usb-akm']):
        row, _ = core.update('keyboard', parts={'keys': {'sources': junk}})
        assert row['parts'][0]['config']['sources'] == {'all': True, 'only': []}


def test_core_applies_the_filter_and_the_driver_never_sees_it(rig):
    rig.here = [AKM, FM1]
    spec = rig.reg.get_driver('keys')
    things = core.found('keys')
    assert [t['name'] for t in things] == ['AKM320', 'FM-1_BLE']
    assert core.passing(spec, {}, things) == things
    only = {'sources': {'all': False, 'only': [{'id': 'name:FM-1_BLE', 'name': 'FM-1_BLE'}]}}
    assert [t['name'] for t in core.passing(spec, only, things)] == ['FM-1_BLE']
    assert core.passing(spec, {'sources': {'all': False, 'only': []}}, things) == []
    # the ticks are kept while "every one" is on, and mean nothing then
    assert core.passing(spec, {'sources': dict(only['sources'], all=True)}, things) == things


def test_pick_one_takes_one(rig):
    rig.here = [AKM, FM1]
    one = dict(DRIVER, config_schema=[dict(SCHEMA[0], many=False)])
    assert rig.reg.register_driver('board', dict(one, module='keys_driver.py'), 'fakeplug')
    spec = rig.reg.get_driver('board')
    assert [t['name'] for t in core.passing(spec, {}, core.found('keys'))] == ['AKM320']
    cfg = core._coerce(spec['config_schema'][0], {'all': False, 'only': [
        {'id': 'name:FM-1_BLE', 'name': 'FM-1_BLE'}, {'id': 'usb-akm', 'name': 'AKM320'}]})
    assert cfg == {'all': False, 'only': [{'id': 'name:FM-1_BLE', 'name': 'FM-1_BLE'}]}
    assert [t['name'] for t in core.passing(spec, {'sources': cfg}, core.found('keys'))] == ['FM-1_BLE']


def test_what_a_driver_finds_is_checked_never_trusted(rig):
    rig.here = [AKM, dict(AKM, name='the same again'), {'name': 'no id'}, 'junk', None,
                {'id': 'x' * 500, 'name': 'long\n name ' * 40, 'kind': 'USB ' * 40}]
    things = core.found('keys')
    assert [t['id'] for t in things] == ['usb-akm', 'x' * 200]
    assert len(things[1]['name']) <= 80 and '\n' not in things[1]['name'] and len(things[1]['kind']) <= 24
    rig.here = [dict(AKM, id=f'k{n}') for n in range(200)]
    assert len(core.found('keys')) == core.MAX_FOUND
    rig.mod.discover = lambda config: 1 / 0
    with pytest.raises(core.DeviceError, match="could not look"):
        core.found('keys')
    del rig.mod.discover
    assert core.found('keys') == []                                  # a driver that cannot look sees nothing


def test_status_says_what_is_here_the_same_way_for_every_driver(rig):
    rig.here = [AKM, FM1]
    _add()
    part = core.status('keyboard')['parts'][0]
    assert part['readings'] == {'connected': 'AKM320 (USB), FM-1_BLE (Bluetooth)', 'sound': 'on'}
    rig.here = []
    assert core.status('keyboard')['parts'][0]['readings']['connected'] == 'nothing'
    assert 'connected: nothing' in core.status_text('keyboard')[0]


# --- the keeper ------------------------------------------------------------------

def test_a_driver_is_told_what_is_here(rig):
    rig.here = [AKM, FM1]
    _add()
    assert presence.start() is True
    assert _until(lambda: _told(rig))
    assert _told(rig)[0] == ('keyboard', ['AKM320', 'FM-1_BLE'], False)
    assert presence.here() == {'keyboard': ['AKM320', 'FM-1_BLE']}
    assert _until(lambda: rig.watches == 1)


def test_the_watcher_says_when_something_changed(rig):
    _add()
    presence.start()
    assert _until(lambda: _told(rig) and rig.changed)
    rig.here = [AKM, FM1]
    before = len(rig.tends)
    rig.changed()
    assert _until(lambda: len(rig.tends) > before)
    assert rig.tends[-1] == ('keyboard', ['AKM320', 'FM-1_BLE'], False)
    rig.here = []
    rig.changed()
    assert _until(lambda: rig.tends[-1] == ('keyboard', [], False))   # nothing here is not leaving


def test_a_burst_of_changes_is_one_look(rig):
    _add()
    presence.start()
    assert _until(lambda: _told(rig) and rig.changed)
    time.sleep(0.1)
    before = len(rig.tends)
    for _ in range(40):
        rig.changed()
    time.sleep(0.4)
    assert 1 <= len(rig.tends) - before <= 2


def test_the_heartbeat_tells_the_whole_truth_again(rig):
    _add()
    with patch.object(presence, 'HEARTBEAT', 0.1):
        presence.start()
        assert _until(lambda: len(rig.tends) >= 3)                    # nobody poked
    assert all(t == ('keyboard', ['AKM320'], False) for t in rig.tends)


def test_every_device_has_its_own_filter(rig):
    rig.here = [AKM, FM1]
    _add('keyboard', sources={'all': False, 'only': [{'id': 'usb-akm', 'name': 'AKM320'}]})
    _add('second', sources={'all': False, 'only': [{'id': 'name:FM-1_BLE', 'name': 'FM-1_BLE'}]})
    presence.start()
    assert _until(lambda: _told(rig, 'keyboard') and _told(rig, 'second'))
    assert _told(rig, 'keyboard')[-1][1] == ['AKM320'] and _told(rig, 'second')[-1][1] == ['FM-1_BLE']
    assert rig.watches == 1                                           # one watcher for the driver


def test_a_saved_change_is_told_at_once(rig):
    rig.here = [AKM, FM1]
    _add()
    presence.start()
    assert _until(lambda: _told(rig))
    core.update('keyboard', parts={'keys': {'sources': {'all': False, 'only': [
        {'id': 'name:FM-1_BLE', 'name': 'FM-1_BLE'}]}}})
    assert _until(lambda: rig.tends[-1] == ('keyboard', ['FM-1_BLE'], False))


def test_a_device_that_is_switched_off_or_removed_lets_go(rig):
    _add()
    _add('second')
    presence.start()
    assert _until(lambda: _told(rig, 'keyboard') and _told(rig, 'second'))
    core.update('keyboard', enabled=False)
    assert _until(lambda: ('keyboard', [], True) in rig.tends)
    core.remove('second')
    assert _until(lambda: ('second', [], True) in rig.tends)
    assert presence.here() == {}
    assert _until(lambda: not presence._watchers)                     # no device, no watcher
    core.update('keyboard', enabled=True)
    assert _until(lambda: rig.tends[-1] == ('keyboard', ['AKM320'], False))
    assert rig.tends.count(('keyboard', [], True)) == 1               # said once


def test_a_plugin_that_unloads_lets_go_first(rig):
    _add()
    presence.start()
    assert _until(lambda: _told(rig) and rig.changed)
    sys.modules.pop(MOD, None)                       # the plugin is already half gone
    with patch.object(core, '_plugin_info', lambda n: {'enabled': False, 'loaded': False}):
        presence.release('someone-else')
        assert ('keyboard', [], True) not in rig.tends
        presence.release('fakeplug')
        assert rig.tends[-1] == ('keyboard', [], True)                # done before release returns
    assert presence.here() == {} and 'keys' not in presence._watchers


def test_stopping_lets_everything_go(rig):
    _add()
    _add('second')
    presence.start()
    assert _until(lambda: _told(rig, 'keyboard') and _told(rig, 'second'))
    presence.stop()
    assert sorted(t for t in rig.tends if t[2]) == [('keyboard', [], True), ('second', [], True)]
    assert not presence._watchers and presence._keeper is None
    count = len(rig.tends)
    presence.poke()
    time.sleep(0.2)
    assert len(rig.tends) == count                                    # it stays stopped


def test_one_device_is_never_tended_twice_at_once(rig):
    _add()
    rig.hold = threading.Event()
    presence.start()
    assert _until(lambda: ('keyboard', 'keys') in presence._busy)
    for _ in range(5):
        presence.poke('keys')
        time.sleep(0.05)
    assert rig.tends == []                                            # the first is still at it
    rig.hold.set()
    assert _until(lambda: len(rig.tends) == 2)                        # asked for again: once
    time.sleep(0.2)
    assert len(rig.tends) == 2


def test_a_driver_that_does_not_let_go_does_not_hold_the_plugin(rig):
    _add()
    presence.start()
    assert _until(lambda: _told(rig))
    rig.hold = threading.Event()
    with patch.object(presence, 'LAST_WORD', 0.3):
        began = time.monotonic()
        presence.release('fakeplug')
        assert time.monotonic() - began < 2
    assert presence.here() == {}


def test_a_failing_driver_never_stops_the_keeper(rig):
    _add()
    rig.tend_fails = True
    rig.watch_fails = True
    presence.start()
    assert _until(lambda: len(rig.tends) >= 1 and rig.watches >= 3)   # the watcher is started again
    rig.tend_fails = False
    presence.poke()
    assert _until(lambda: presence.here() == {'keyboard': ['AKM320']})
    rig.mod.discover = lambda config: 1 / 0
    count = len(rig.tends)
    presence.poke()
    time.sleep(0.2)
    assert len(rig.tends) == count and presence._keeper.is_alive()    # it could not look: nothing is told


def test_a_driver_without_a_watcher_lives_on_the_heartbeat(rig):
    del rig.mod.watch
    _add()
    with patch.object(presence, 'HEARTBEAT', 0.1):
        presence.start()
        assert _until(lambda: len(rig.tends) >= 3)
    assert presence._watchers['keys']['idle'] is True
    assert threading.active_count() < 40                              # no thread per heartbeat


def test_a_driver_that_does_not_ask_is_left_alone(rig):
    assert rig.reg.register_driver('plain', dict(DRIVER, presence=False), 'fakeplug')
    assert rig.reg.get_driver('plain')['presence'] is False
    assert rig.reg.get_driver('keys')['presence'] is True
    assert rig.reg.register_driver('sloppy', dict(DRIVER, presence='yes'), 'fakeplug')
    assert rig.reg.get_driver('sloppy')['presence'] is False          # true, or it is not asked
    core.add('lamp', 'A lamp', 'plain', {})
    presence.start()
    presence.poke()
    time.sleep(0.2)
    assert rig.tends == [] and not presence._watchers
    assert 'connected' not in core.status('lamp')['parts'][0]['readings']


def test_a_hosted_install_keeps_nothing(rig):
    _add()
    with patch.object(core, '_managed', lambda: True):
        assert presence.start() is False
    assert presence._keeper is None


def test_a_change_to_a_device_is_never_stopped_by_presence(rig):
    with patch.object(presence, 'poke', lambda *a: 1 / 0):
        row = _add()
        assert core.update('keyboard', label='Still saved')[0]['label'] == 'Still saved'
        core.remove(row['id'])
