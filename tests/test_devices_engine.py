# tests/test_devices_engine.py - the device engine, end to end against the
# REAL registry and the REAL secrets store (on a temp file), with one fake
# driver. Also its three doors: the tools, the routes, the hosted-mode gate.
# Nothing here touches user/ or ~/.config/sapphire.

import asyncio
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from core.devices import engine as core
from core.routes import devices as routes

ROOT = Path(__file__).absolute().parent.parent


def _tools():
    """functions/devices.py, loaded by path the way the function manager does."""
    spec = importlib.util.spec_from_file_location('devices_tools_under_test',
                                                  ROOT / 'functions' / 'devices.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

SCHEMA = [
    {'key': 'host', 'type': 'string', 'label': 'Host'},
    {'key': 'port', 'type': 'number', 'label': 'Port', 'default': 22},
    {'key': 'auth', 'type': 'string', 'widget': 'select', 'label': 'Login', 'default': 'auto',
     'options': [{'label': 'Auto', 'value': 'auto'}, {'label': 'Password', 'value': 'password'}]},
    {'key': 'password', 'type': 'string', 'widget': 'password', 'secret': True, 'label': 'Password'},
    {'key': 'allow_all', 'type': 'boolean', 'label': 'Allow any command', 'default': False},
    {'key': 'commands', 'widget': 'rows', 'label': 'Commands', 'columns': ['name', 'command']},
]
DRIVER = {'label': 'Fake machine', 'module': 'device_driver.py', 'capabilities': ['shell', 'lamp'],
          'config_schema': SCHEMA}
MOD = 'plugins.fakeplug.device_driver'


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def update_with_lock(self, k, mutator, default=None):
        self.d[k] = mutator(self.d.get(k, default))
        return self.d[k]


def _fake_driver(seen):
    m = types.ModuleType(MOD)

    def describe(device, config):
        actions = {c['name']: {'help': c['command'], 'example': ''} for c in config.get('commands', [])}
        if config.get('allow_all'):
            actions['run'] = {'help': 'any command', 'example': 'uptime'}
        return {'shell': {'label': 'Shell', 'help': 'run commands', 'actions': actions},
                'lamp': {'label': 'Lamp', 'help': 'a light',
                         'actions': {'set': {'help': 'color', 'example': 'red pulse 5s'}}}}

    def status(device, config, secrets):
        seen.append(('status', device['id']))
        return {'online': config.get('host') != 'dead', 'detail': f"{config.get('host')}:{config.get('port')}",
                'readings': {'temp': '41C'}}

    def run(device, capability, action, value, config, secrets, call_tool):
        seen.append(('run', device['id'], capability, action, value))
        if action == 'boom':
            raise RuntimeError('stack detail with hunter2-long inside')
        if action == 'leak':
            return f"auth used {secrets.get('password')}", True
        if action == 'tool':
            return call_tool(value, {'x': 1})
        return f"{capability}.{action}({value})", True

    def validate(config):
        if any(c['name'] == 'run' for c in config.get('commands', [])):
            return config, "'run' is reserved."
        return config, ''

    m.describe, m.status, m.run, m.validate = describe, status, run, validate
    return m


@pytest.fixture
def host(tmp_path):
    from core.credentials_manager import CredentialsManager
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    seen, store = [], FakeStore()
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        mgr = CredentialsManager()
        with patch.object(sec, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(sec, 'CONFIG_DIR', tmp_path), \
             patch.object(sec, '_crypto', lambda: mgr), \
             patch.object(core, '_store', lambda: store), \
             patch.object(core, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
             patch.object(core, '_all_plugin_info', lambda: []), \
             patch.object(core, '_managed', lambda: False):
            sec.reload()
            importlib.reload(reg)
            reg.CORE_DRIVERS = ()              # these tests run on the fake driver alone
            core._modules.clear()
            core._modules_gen = None
            core._status.clear()
            sys.modules[MOD] = _fake_driver(seen)
            assert reg.register_driver('fake', DRIVER, 'fakeplug')
            yield types.SimpleNamespace(seen=seen, store=store, reg=reg, sec=sec, tmp=tmp_path)
            sys.modules.pop(MOD, None)
            core._modules.clear()
            core._modules_gen = None
            core._status.clear()
            importlib.reload(reg)
            sec.reload()


CMDS = [{'name': 'close_firefox', 'command': 'pkill firefox'}, {'name': 'play_beep', 'command': 'beep -f 800'}]


def _add(device_id='desktop', **config):
    base = {'host': 'tower', 'commands': CMDS, 'password': 'hunter2-long', 'auth': 'password'}
    base.update(config)
    return core.add(device_id, "Krem's desktop", 'fake', base)


# --- add ---------------------------------------------------------------------

def test_add_stores_config_and_splits_out_the_secret(host):
    row, failed = _add(port='2222', allow_all='false', junk='dropped')
    assert failed == []
    cfg = row['parts'][0]['config']
    assert cfg == {'host': 'tower', 'port': 2222, 'auth': 'password', 'allow_all': False, 'commands': CMDS}
    assert row['parts'][0]['plugin'] == 'fakeplug' and row['enabled'] is True
    assert 'hunter2-long' not in json.dumps(host.store.d)
    assert host.sec.status('desktop') == {'fake.password': 'set'}
    assert 'hunter2-long' not in (host.tmp / 'device_secrets.json').read_text(encoding='utf-8')


def test_add_refuses_bad_input(host):
    for bad in ('', 'Has Space', '../x', 'x' * 40):
        with pytest.raises(core.DeviceError):
            core.add(bad, '', 'fake', {})
    _add()
    with pytest.raises(core.DeviceError, match='already exists'):
        _add()
    with pytest.raises(core.DeviceError, match='not loaded'):
        core.add('other', '', 'nodriver', {})
    with pytest.raises(core.DeviceError, match='reserved'):
        _add('two', commands=[{'name': 'run', 'command': 'x'}])
    assert list(core.rows()) == ['desktop']


def test_a_reused_name_never_inherits_old_secrets(host):
    host.sec.put('desktop', 'fake.password', 'left-over-long')
    core.add('desktop', '', 'fake', {'host': 'tower'})
    assert host.sec.status('desktop') == {}


def test_config_is_coerced_to_the_schema(host):
    row, _ = _add(port='nonsense', auth='telnet', allow_all='yes',
                  commands=[{'name': ' a ', 'command': 'b', 'extra': 'x'}, {'name': '', 'command': ''},
                            'junk', {'name': 'c'}])
    cfg = row['parts'][0]['config']
    assert cfg['port'] == 22 and cfg['auth'] == 'auto' and cfg['allow_all'] is True
    assert cfg['commands'] == [{'name': 'a', 'command': 'b'}, {'name': 'c', 'command': ''}]


# --- update ------------------------------------------------------------------

def test_update_keeps_what_was_not_sent(host):
    _add()
    row, failed = core.update('desktop', label='Tower', parts={'fake': {'host': 'tower2'}})
    assert failed == [] and row['label'] == 'Tower'
    cfg = row['parts'][0]['config']
    assert cfg['host'] == 'tower2' and cfg['commands'] == CMDS and cfg['auth'] == 'password'
    assert core._part_secrets('desktop', 'fake').get('password') == 'hunter2-long'


def test_update_replaces_and_clears_a_secret(host):
    _add()
    core.update('desktop', parts={'fake': {'password': 'new-pass-long'}})
    assert core._part_secrets('desktop', 'fake').get('password') == 'new-pass-long'
    core.update('desktop', parts={'fake': {'password': ''}})
    assert host.sec.status('desktop') == {}


def test_update_refuses_a_bad_part_and_changes_nothing(host):
    _add()
    with pytest.raises(core.DeviceError):
        core.update('desktop', label='X', parts={'nope': {}})
    with pytest.raises(core.DeviceError, match='reserved'):
        core.update('desktop', label='X', parts={'fake': {'commands': [{'name': 'run', 'command': 'x'}]}})
    assert core.get('desktop')['label'] == "Krem's desktop"


def test_rename_moves_the_secrets(host):
    _add()
    _add('laptop')
    row, _ = core.update('desktop', new_id='tower')
    assert row['id'] == 'tower' and sorted(core.rows()) == ['laptop', 'tower']
    assert host.sec.status('desktop') == {} and host.sec.status('tower') == {'fake.password': 'set'}
    with pytest.raises(core.DeviceError, match='already exists'):
        core.update('tower', new_id='laptop')
    with pytest.raises(core.DeviceError):
        core.update('tower', new_id='Bad Name')
    assert sorted(core.rows()) == ['laptop', 'tower']


def test_remove_deletes_row_and_secrets(host):
    _add()
    core.remove('desktop')
    assert core.rows() == {} and host.sec.status('desktop') == {}
    with pytest.raises(core.DeviceError):
        core.remove('desktop')


def test_public_never_carries_a_secret(host):
    _add()
    view = core.public(core.get('desktop'))
    assert view['parts'][0]['values']['password'] == 'set'
    assert 'hunter2-long' not in json.dumps(view)
    assert view['parts'][0]['capabilities'] == ['shell', 'lamp'] and view['parts'][0]['available'] is True


# --- what she reads ----------------------------------------------------------

def test_empty_list(host):
    text, ok = core.list_text()
    assert ok and text == "No devices yet. The user adds them in Settings > Devices."


def test_list_text_format(host):
    _add()
    _add('fm1', host='dead')
    text, ok = core.list_text()
    assert ok and text == ('Devices (2):\n'
                           '  desktop  online   shell, lamp\n'
                           '  fm1      offline  shell, lamp\n'
                           'Next: device_action("desktop")')


def test_help_at_every_level(host):
    _add(allow_all=True)
    text, ok = core.run('desktop')
    assert ok and text == ('desktop - Krem\'s desktop - online\n'
                           '  shell  run commands  device_action("desktop","shell","close_firefox")\n'
                           '  lamp   a light       device_action("desktop","lamp","set","red pulse 5s")\n'
                           'Each line is one example. device_action("desktop","shell") lists all 3 actions of shell.')
    text, ok = core.run('desktop', 'shell')
    assert ok and text == ('desktop / shell\n'
                           '  close_firefox  pkill firefox  device_action("desktop","shell","close_firefox")\n'
                           '  play_beep      beep -f 800    device_action("desktop","shell","play_beep")\n'
                           '  run            any command    device_action("desktop","shell","run","uptime")')
    assert not any(s[0] == 'run' for s in host.seen)


def test_offline_is_said_before_she_wastes_a_call(host):
    _add(host='dead')
    text, ok = core.run('desktop')
    assert ok and text == ('desktop - Krem\'s desktop - offline\n'
                           '  shell  run commands  device_action("desktop","shell","close_firefox")\n'
                           '  lamp   a light       device_action("desktop","lamp","set","red pulse 5s")\n'
                           'Each line is one example. device_action("desktop","shell") lists all 2 actions of shell.\n'
                           'Offline: dead:22\n'
                           'Commands will fail until it is back.')
    text, ok = core.run('desktop', 'lamp')
    assert ok and text.endswith('Offline: dead:22\nCommands will fail until it is back.')
    assert 'Offline' not in core.run('desktop', 'lamp', 'set', 'red')[0]   # a run is the driver's words


def test_no_device_named_falls_back_to_the_list(host):
    _add()
    assert core.run('')[0].startswith('Devices (1):')
    assert core.run(None)[0].startswith('Devices (1):')


def test_wrong_guess_answers_one_level_up(host):
    _add()
    text, ok = core.run('toaster')
    assert not ok and "no device named 'toaster'" in text and 'Devices: desktop.' in text
    text, ok = core.run('desktop', 'laser')
    assert not ok and "has no 'laser'" in text and 'device_action("desktop","lamp","set"' in text
    text, ok = core.run('desktop', 'shell', 'reboot')
    assert not ok and "no action 'reboot'" in text and 'close_firefox' in text
    # free-form run is absent while the checkbox is off
    text, ok = core.run('desktop', 'shell', 'run', 'uptime')
    assert not ok and "no action 'run'" in text
    assert not any(s[0] == 'run' for s in host.seen)


def test_run_reaches_the_driver(host):
    _add()
    assert core.run('Desktop', 'SHELL', 'Close_Firefox') == ('shell.close_firefox()', True)
    assert core.run('desktop', 'lamp', 'set', 40) == ('lamp.set(40)', True)
    assert host.seen[-1] == ('run', 'desktop', 'lamp', 'set', '40')


def test_results_are_scrubbed_and_errors_stay_private(host):
    _add(commands=[{'name': 'leak', 'command': 'x'}, {'name': 'boom', 'command': 'x'}])
    text, ok = core.run('desktop', 'shell', 'leak')
    assert ok and text == 'auth used [secret]'
    text, ok = core.run('desktop', 'shell', 'boom')
    assert not ok and 'hunter2-long' not in text and 'stack detail' not in text
    assert text == 'desktop / shell / boom failed: RuntimeError'


def test_a_disabled_device_is_invisible_to_her(host):
    _add()
    _add('fm1')
    core.update('fm1', enabled=False)
    assert 'fm1' not in core.list_text()[0]
    text, ok = core.run('fm1')
    assert not ok and text == "'fm1' is turned off in Settings > Devices."
    assert core.status_text('fm1') == (text, False)


# --- what she may not use ----------------------------------------------------

def test_a_locked_capability_is_hers_to_see_but_not_to_use(host):
    """The user's own button still works. She is told why she cannot."""
    with patch.object(core, 'LOCKABLE', ('power', 'lamp')):
        _add()
        assert core.locked(core.get('desktop')) == []
        row, _ = core.update('desktop', locked=['lamp', 'shell', 'nonsense'])
        assert row['locked'] == ['lamp']                      # shell has no such switch
        assert core.public(row)['locked'] == ['lamp']
        caps = {c['capability']: c for c in core.describe(row)}
        assert (caps['lamp']['lockable'], caps['lamp']['locked']) == (True, True)
        assert (caps['shell']['lockable'], caps['shell']['locked']) == (False, False)

        host.seen.clear()
        text, ok = core.run('desktop', 'lamp', 'set', 'red')
        assert not ok and text == ("'lamp' on 'desktop' is locked: the user has not allowed you this. "
                                   "They can change it in Settings > Devices.")
        assert core.run('desktop', 'lamp')[1] is False        # asking for its actions is refused too
        assert [s for s in host.seen if s[0] == 'run'] == []  # the driver never ran
        listing = core.run('desktop')[0]
        assert 'lamp   a light       locked: the user has not allowed you this.' in listing
        assert 'device_action("desktop","lamp"' not in listing
        assert 'lamp (locked)' in core.list_text()[0]

        assert core.run('desktop', 'lamp', 'set', 'red', owner=True) == ('lamp.set(red)', True)
        assert routes.run_action('desktop', {'capability': 'lamp', 'action': 'set', 'value': 'blue'}) == \
            {'text': 'lamp.set(blue)', 'ok': True}
        tools = _tools()
        assert tools.execute('device_action', {'device': 'desktop', 'capability': 'lamp',
                                               'action': 'set', 'value': 'red'}, None)[1] is False

        core.update('desktop', label='changed')                # a change elsewhere keeps the lock
        assert core.get('desktop')['locked'] == ['lamp']
        core.update('desktop', locked=[])
        assert core.run('desktop', 'lamp', 'set', 'red') == ('lamp.set(red)', True)


def test_a_switch_the_page_did_not_show_is_never_touched(host):
    """A device that is switched off shows no capability tabs, so its window
    has no lock switches. Saving it used to send "nothing is locked" and
    opened every lock, power included (quality check, 2026-09-28)."""
    with patch.object(core, 'LOCKABLE', ('power', 'lamp', 'camera')):
        _add()
        core.update('desktop', locked=['lamp', 'camera'])
        out = routes.update_device('desktop', {'enabled': False, 'locked': {}})
        assert out['device']['capabilities'] == [] and out['device']['locked'] == ['camera', 'lamp']
        out = routes.update_device('desktop', {'enabled': True, 'locked': {}})     # and on again
        assert out['device']['locked'] == ['camera', 'lamp']
        assert core.run('desktop', 'lamp', 'set', 'red')[1] is False

        # a map changes only what it names
        assert core.update('desktop', locked={'lamp': False})[0]['locked'] == ['camera']
        assert core.update('desktop', locked={'power': True, 'shell': True, 'Camera': True})[0]['locked'] == \
            ['camera', 'power']                                  # shell has no such switch
        assert core.update('desktop', locked={})[0]['locked'] == ['camera', 'power']
        # a device that never had the key starts from what its drivers ask for
        host.store.d['devices']['desktop'].pop('locked')
        assert core.update('desktop', locked={'lamp': True})[0]['locked'] == ['lamp']


def test_the_page_sends_only_the_switches_it_showed():
    """The other half of the same fix, in the page itself."""
    page = (ROOT / 'interfaces/web/static/views/settings-tabs/devices.js').read_text(encoding='utf-8')
    assert 'lockKey(c.capability) in form' in page and 'Object.fromEntries' in page
    assert ".map(c => c.capability);" not in page                # the old list of "what is locked"


def test_a_device_may_not_take_a_name_the_routes_use(host):
    with pytest.raises(core.DeviceError, match='uses herself'):
        core.add('found', 'x', 'fake', {'host': 'h'})
    _add()
    with pytest.raises(core.DeviceError, match='not a valid device name'):
        core.update('desktop', new_id='found')
    assert list(core.rows()) == ['desktop']


def test_the_pick_list_asks_the_driver_what_is_here(host):
    seen = []

    def discover(config):
        seen.append(config)
        return [{'id': 'usb-2', 'name': 'Second board', 'kind': 'USB'},
                {'id': 'usb-1', 'name': 'First board', 'kind': 'USB'}]
    sys.modules[MOD].discover = discover
    assert routes.found_things('fake') == {'found': [
        {'id': 'usb-1', 'name': 'First board', 'kind': 'USB'},
        {'id': 'usb-2', 'name': 'Second board', 'kind': 'USB'}]}
    assert seen == [{}]                                          # the add form: no device yet
    _add(port=2222)
    routes.found_things('FAKE', 'desktop')
    assert seen[-1]['port'] == 2222 and 'password' not in seen[-1]   # a saved device lends its settings
    assert _status_of(routes.found_things, 'fake', 'nope')[0] == 404
    assert _status_of(routes.found_things, 'ghost') == \
        (400, "The 'ghost' driver is not loaded. Enable the ghost plugin in Settings > Plugins.")
    with patch.object(core, '_plugin_info', lambda n: {'enabled': False, 'loaded': False}):
        assert _status_of(routes.found_things, 'fake')[0] == 400     # a plugin that is off never looks
    assert len(seen) == 2
    paths = [r.path for r in routes.router.routes]
    assert paths.index('/api/devices/found/{driver_id}') < paths.index('/api/devices/{device_id}')


def test_a_driver_may_ask_for_a_capability_to_start_out_locked(host):
    with patch.object(core, 'LOCKABLE', ('power', 'lamp')):
        sys.modules['plugins.fakeplug.careful'] = sys.modules[MOD]
        try:
            assert host.reg.register_driver('careful', dict(DRIVER, module='careful.py',
                                                            locked_by_default=['lamp', 'ghost']), 'fakeplug')
            assert host.reg.get_driver('careful')['locked_by_default'] == ['lamp']
            assert host.reg.get_driver('fake')['locked_by_default'] == []
            row, _ = core.add('server', '', 'careful', {'host': 'tower'})
            assert row['locked'] == ['lamp']
            assert core.run('server', 'lamp', 'set', 'red')[1] is False
            # a device saved before locks existed has no list of its own: the driver's wish holds
            host.store.d['devices']['server'].pop('locked')
            assert core.locked(core.get('server')) == ['lamp']
            core.update('server', locked=[])                  # the user opened it: that holds
            assert core.locked(core.get('server')) == []
        finally:
            sys.modules.pop('plugins.fakeplug.careful', None)


def test_an_action_may_answer_with_pictures(host):
    _add()
    shot = {'data': 'QUJD', 'media_type': 'image/jpeg'}
    sec = core._part_secrets('desktop', 'fake')
    assert core._result({'text': 'A picture. hunter2-long', 'images': [shot]}, sec) == \
        {'text': 'A picture. [secret]', 'images': [shot]}
    out = core._result({'text': '', 'images': [shot, {'data': 'x', 'media_type': 'text/html'},
                                                {'data': '', 'media_type': 'image/png'}, 'junk',
                                                {'data': 'y' * (core.MAX_PICTURE + 1), 'media_type': 'image/png'},
                                                {'data': 'QQ==', 'media_type': 'IMAGE/PNG', 'display_only': True}]}, sec)
    assert out == {'text': 'A picture.', 'images': [shot, {'data': 'QQ==', 'media_type': 'image/png'}]}
    assert len(core._result({'images': [shot] * 9}, sec)['images']) == core.MAX_PICTURES
    assert core._result({'text': 'dark room', 'images': []}, sec) == 'dark room'
    assert core._result({'images': ['junk']}, sec) == 'No picture came back.'
    assert core._result(None, sec) == '(no output)'
    assert core.text_of({'text': 'A picture.', 'images': [shot]}) == 'A picture.'
    assert core.text_of('plain') == 'plain'


def test_the_try_button_is_handed_the_pictures(host):
    _add()
    shot = {'data': 'QUJD', 'media_type': 'image/jpeg'}
    with patch.object(core, 'run', return_value=({'text': 'A picture.', 'images': [shot]}, True)):
        assert routes.run_action('desktop', {'capability': 'lamp', 'action': 'on'}) == \
            {'text': 'A picture.', 'ok': True, 'images': [shot]}
    with patch.object(core, 'run', return_value=('Lamp on.', True)):
        assert routes.run_action('desktop', {'capability': 'lamp', 'action': 'on'}) == \
            {'text': 'Lamp on.', 'ok': True}


def test_location_is_part_of_what_she_reads(host):
    _add()
    _add('fm1', host='dead')
    assert core.public(core.get('desktop'))['location'] == ''
    row, _ = core.update('desktop', location='  the   Office  ' + 'x' * 100)
    assert row['location'] == ('the Office ' + 'x' * 100)[:60]
    core.update('desktop', location='Office')
    assert core.public(core.get('desktop'))['location'] == 'Office'
    assert core._brief(core.get('desktop'))['location'] == 'Office'
    assert core.list_text()[0] == ('Devices (2):\n'
                                   '  desktop  online   Office  shell, lamp\n'
                                   '  fm1      offline  -       shell, lamp\n'
                                   'Next: device_action("desktop")')
    assert core.status_text('desktop')[0].startswith("desktop - Krem's desktop (Office) - online")
    core.update('desktop', label='changed')                  # a change elsewhere keeps it
    assert core.get('desktop')['location'] == 'Office'
    row, _ = core.add('lamp', '', 'fake', {'host': 'tower'}, location='Hall')
    assert row['location'] == 'Hall'


def test_status_text(host):
    _add()
    text, ok = core.status_text('desktop')
    assert ok and text == ("desktop - Krem's desktop - online (checked just now)\n"
                           "  fake  ok  tower:22\n"
                           "  temp: 41C\n"
                           "Can do: shell, lamp\n"
                           'Next: device_action("desktop")')
    assert core.status_text('nope')[1] is False


# --- status cache ------------------------------------------------------------

def test_status_is_cached_until_asked_fresh(host):
    _add()
    core.status('desktop', fresh=False)
    core.status('desktop', fresh=False)
    assert host.seen.count(('status', 'desktop')) == 1
    core.status('desktop', fresh=True)
    assert host.seen.count(('status', 'desktop')) == 2
    core.update('desktop', label='changed')          # a change forgets the old answer
    core.status('desktop', fresh=False)
    assert host.seen.count(('status', 'desktop')) == 3


def test_statuses_waits_for_stale_devices(host):
    _add()
    _add('fm1', host='dead')
    found = core.statuses(wait_s=5)
    assert found['desktop']['online'] is True and found['fm1']['online'] is False


# --- drivers -----------------------------------------------------------------

def test_driver_plugin_off(host):
    _add()
    with patch.object(core, '_plugin_info', lambda n: {'enabled': False, 'loaded': False}):
        text, ok = core.run('desktop', 'shell', 'close_firefox')
        assert not ok and 'fakeplug plugin is off' in text
        assert core.status('desktop')['online'] is False
    host.reg.unregister_plugin('fakeplug')
    text, ok = core.run('desktop')
    assert ok and "Enable the fakeplug plugin" in text
    assert 'fake (driver off)' in core.list_text()[0]
    assert core.public(core.get('desktop'))['parts'][0]['available'] is False
    assert not any(s[0] == 'run' for s in host.seen)


def test_driver_cache_is_dropped_when_the_registry_changes(host):
    _add()
    core.run('desktop', 'lamp', 'set', 'red')
    first = core._modules['fake']
    assert core._driver('fake')[0] is first               # cached while nothing changes
    host.reg.unregister_plugin('fakeplug')                # the plugin reloads
    host.reg.register_driver('fake', DRIVER, 'fakeplug')
    replacement = _fake_driver(host.seen)
    with patch.object(core.importlib, 'import_module', lambda name: replacement):
        mod, _spec = core._driver('fake')
    assert mod is replacement and mod is not first
    assert MOD not in sys.modules                          # the stale module was evicted


def test_a_driver_missing_a_function_is_refused(host):
    _add()
    del sys.modules[MOD].status
    core._modules.clear()
    text, ok = core.run('desktop', 'lamp', 'set', 'red')
    assert not ok and 'missing status()' in text


def test_drivers_lists_the_greyed_out_ones(host):
    off = [{'name': 'ssh', 'manifest': {'short_display_name': 'SSH', 'capabilities': {'devices': [
              {'driver': 'ssh', 'label': 'SSH machine', 'capabilities': ['ssh']}]}}},
           {'name': 'fakeplug', 'manifest': {'capabilities': {'devices': [{'driver': 'fake'}]}}},
           {'name': 'plain', 'manifest': {'capabilities': {}}}]
    with patch.object(core, '_all_plugin_info', lambda: off):
        found = core.drivers()
    assert [(d['driver'], d['available']) for d in found] == [('fake', True), ('ssh', False)]
    assert found[1]['note'] == 'Enable the SSH plugin to use this' and found[1]['config_schema'] == []


# --- the tool door -----------------------------------------------------------

class FakeFM:
    def __init__(self):
        self.calls = []

    def tool_plugin(self, name):
        return {'fake_play': 'fakeplug', 'ssh_run_command': 'ssh'}.get(name)

    def execute_function(self, name, args, allowed_tools=None, with_success=False):
        self.calls.append((name, args, allowed_tools, with_success))
        return 'played', True


def test_a_driver_runs_only_its_own_plugins_tools(host):
    _add(commands=[{'name': 'tool', 'command': 'x'}])
    fm = FakeFM()
    with patch.object(core, '_function_manager', lambda: fm):
        assert core.run('desktop', 'shell', 'tool', 'fake_play') == ('played', True)
        assert fm.calls == [('fake_play', {'x': 1}, {'fake_play'}, True)]
        text, ok = core.run('desktop', 'shell', 'tool', 'ssh_run_command')
        assert not ok and "may only run its own plugin's tools" in text
        text, ok = core.run('desktop', 'shell', 'tool', 'no_such_tool')
        assert not ok
        assert len(fm.calls) == 1


# --- the three tools ---------------------------------------------------------

def test_tools_are_thin_doors(host):
    tools = _tools()
    _add()
    assert tools.execute('device_list', {}, None) == core.list_text()
    assert tools.execute('device_status', {'device': 'desktop'}, None)[1] is True
    assert tools.execute('device_status', {}, None)[0].startswith('Devices (1):')
    assert tools.execute('device_action', {'device': 'desktop', 'capability': 'lamp',
                                           'action': 'set', 'value': 'blue'}, None) == ('lamp.set(blue)', True)
    assert tools.execute('device_nope', {}, None)[1] is False
    names = [t['function']['name'] for t in tools.TOOLS]
    assert names == tools.AVAILABLE_FUNCTIONS == ['device_list', 'device_status', 'device_action']
    assert all(t['is_local'] is True for t in tools.TOOLS)


def test_no_tool_can_change_a_device(host):
    """The safety line: adding, changing and removing is the page's alone."""
    src = (ROOT / 'functions' / 'devices.py').read_text(encoding='utf-8')
    for verb in ('engine.add', 'engine.update', 'engine.remove', '_write', 'put(', 'clear('):
        assert verb not in src, verb


# --- the routes --------------------------------------------------------------

def test_routes_round_trip(host):
    out = routes.add_device({'id': 'desktop', 'label': 'Tower', 'driver': 'fake',
                             'config': {'host': 'tower', 'password': 'hunter2-long', 'commands': CMDS}})
    assert out['device']['id'] == 'desktop' and 'warning' not in out
    assert 'hunter2-long' not in json.dumps(out)
    assert [c['capability'] for c in out['device']['capabilities']] == ['shell', 'lamp']

    listing = routes.list_devices()
    assert listing['devices'][0]['capabilities'] == ['shell', 'lamp']
    assert [d['driver'] for d in listing['drivers']] == ['fake']

    assert routes.test_device('desktop')['status']['online'] is True
    assert routes.get_device('desktop')['device']['status']['online'] is True
    assert routes.run_action('desktop', {'capability': 'lamp', 'action': 'set', 'value': 'red'}) == \
        {'text': 'lamp.set(red)', 'ok': True}

    out = routes.update_device('desktop', {'enabled': False})
    assert out['device']['enabled'] is False and out['device']['capabilities'] == []
    assert routes.remove_device('desktop') == {'removed': 'desktop'}
    assert core.rows() == {}


def _status_of(fn, *args):
    with pytest.raises(HTTPException) as err:
        asyncio.run(routes._do(fn, *args))
    return err.value.status_code, err.value.detail


def test_route_errors_are_the_users_to_read(host):
    _add()
    assert _status_of(routes.add_device, {'id': 'desktop', 'driver': 'fake'}) == \
        (400, "A device named 'desktop' already exists.")
    assert _status_of(routes.add_device, {'id': 'Bad Name', 'driver': 'fake'})[0] == 400
    assert _status_of(routes.get_device, 'nope') == (404, "There is no device named 'nope'.")
    assert _status_of(routes.remove_device, 'nope')[0] == 404
    assert _status_of(routes.test_device, 'nope')[0] == 404


def test_a_crash_is_a_500_without_its_detail(host):
    def boom():
        raise RuntimeError('stack detail with hunter2-long inside')
    code, detail = _status_of(boom)
    assert code == 500 and 'hunter2-long' not in detail and 'RuntimeError' in detail


def test_the_routes_are_all_behind_login():
    """Every devices route must carry require_login. A route without it would
    hand the device list, and the Try button, to anyone on the network.
    The TWO exceptions are the doors a device opens with its own key: what it
    heard, and what its light should show (tests/test_devices_voice.py proves
    that lock on both)."""
    from core.auth import require_login
    found = [r for r in routes.router.routes if r.path.startswith('/api/devices')]
    assert len(found) == 10
    assert not [r.path for r in routes.router.routes if r.path.startswith('/api/body')]
    open_doors = []
    for r in found:
        deps = [d.call for d in r.dependant.dependencies]
        if require_login not in deps:
            open_doors.append(r.path)
    assert open_doors == ['/api/devices/{device_id}/voice', '/api/devices/{device_id}/events']


# --- hosted Sapphire ---------------------------------------------------------

class _Req:
    session = {'csrf_token': 'test-session'}
    headers = {}
    client = None


def test_hosted_sapphire_has_no_devices(host):
    _add()
    tools = _tools()
    with patch.object(core, '_managed', lambda: True):
        assert core.refusal() == 'Devices are not available on hosted Sapphire.'
        for name, args in (('device_list', {}), ('device_status', {'device': 'desktop'}),
                           ('device_action', {'device': 'desktop', 'capability': 'lamp',
                                              'action': 'set', 'value': 'red'})):
            assert tools.execute(name, args, None) == ('Devices are not available on hosted Sapphire.', False)
        for write in (False, True):
            with pytest.raises(HTTPException) as err:
                routes._open(_Req(), write=write)
            assert err.value.status_code == 404
    assert not any(s[0] == 'run' for s in host.seen)
    assert core.refusal() == ''


def test_an_unreadable_hosted_flag_does_not_lock_the_user_out(host):
    def broken():
        raise RuntimeError('settings not loaded')
    with patch.object(core, '_managed', broken):
        assert core.refusal() == ''


# --- the engine is never loaded at boot ---------------------------------------

def test_the_doors_import_the_engine_lazily():
    """A fault in the engine must not stop Sapphire starting: the route module
    is imported at boot, the engine only on first use."""
    for rel in ('core/routes/devices.py', 'functions/devices.py'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        top = [ln for ln in src.splitlines() if ln.startswith(('import ', 'from '))]
        assert not any('core.devices' in ln for ln in top), (rel, top)
    init = (ROOT / 'core' / 'devices' / '__init__.py').read_text(encoding='utf-8')
    assert 'import' not in [ln.split()[0] for ln in init.splitlines() if ln and not ln.startswith((' ', '"', '#'))]
