# tests/test_docs_device_driver_example.py - the example in the driver guide
# (docs/plugin-author/devices.md) is taken from the page as it is written and
# run through the REAL registry and the REAL engine. A guide whose example
# does not work teaches people to fail.
import importlib
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from core.devices import engine

GUIDE = Path(__file__).absolute().parent.parent / 'docs' / 'plugin-author' / 'devices.md'
MOD = 'plugins.my-lamp.device_driver'


def _blocks(kind):
    return re.findall(rf"```{kind}\n(.*?)```", GUIDE.read_text(encoding='utf-8'), re.S)


def _manifest():
    found = [b for b in _blocks('json') if '"capabilities"' in b and '"devices"' in b and '"name"' in b]
    assert len(found) == 1, "the guide should hold exactly one whole plugin.json"
    return json.loads(found[0])


def _driver_source():
    found = [b for b in _blocks('python') if 'def describe(' in b and 'def run(' in b and 'def status(' in b]
    assert len(found) == 1, "the guide should hold exactly one whole driver"
    return found[0]


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def update_with_lock(self, k, mutator, default=None):
        self.d[k] = mutator(self.d.get(k, default))
        return self.d[k]


@pytest.fixture
def lamp(tmp_path):
    from core.credentials_manager import CredentialsManager
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    manifest = _manifest()
    decl = manifest['capabilities']['devices'][0]
    module = types.ModuleType(MOD)
    exec(compile(_driver_source(), str(GUIDE), 'exec'), module.__dict__)
    sent = []

    def fake_send(config, secrets, path):
        sent.append((config['host'], secrets.get('token'), path))
        if config['host'] == 'unplugged':
            raise module.Problem(f"Could not reach the lamp at {config['host']}. Is it plugged in?")
        return {'brightness': 40}

    module._send = fake_send
    store = FakeStore()
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        mgr = CredentialsManager()
        with patch.object(sec, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(sec, 'CONFIG_DIR', tmp_path), \
             patch.object(sec, '_crypto', lambda: mgr), \
             patch.object(engine, '_store', lambda: store), \
             patch.object(engine, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
             patch.object(engine, '_all_plugin_info', lambda: []), \
             patch.object(engine, '_managed', lambda: False):
            sec.reload()
            importlib.reload(reg)
            reg.CORE_DRIVERS = ()
            engine._modules.clear()
            engine._modules_gen = None
            engine._status.clear()
            sys.modules[MOD] = module
            assert reg.register_driver(decl['driver'], decl, manifest['name'])
            yield types.SimpleNamespace(manifest=manifest, decl=decl, module=module, sent=sent)
            sys.modules.pop(MOD, None)
            engine._modules.clear()
            engine._modules_gen = None
            engine._status.clear()
            importlib.reload(reg)
            sec.reload()


def test_the_guides_manifest_is_a_whole_plugin(lamp):
    m = lamp.manifest
    assert m['name'] == 'my-lamp' and m['version'] and m['description']
    assert lamp.decl['module'] == 'device_driver.py' and lamp.decl['capabilities'] == ['light']
    assert [f['key'] for f in lamp.decl['config_schema'] if f.get('secret')] == ['token']


def test_the_guides_driver_works_through_the_real_engine(lamp):
    row, failed = engine.add('desk', 'Desk lamp', 'lamp', {'host': ' 192.168.0.20 ', 'token': 'lamp-token-12345'})
    assert failed == [] and row['parts'][0]['config'] == {'host': '192.168.0.20'}      # validate() ran
    assert 'lamp-token-12345' not in json.dumps(row)                                   # the secret is not in the row

    assert engine.run('desk', 'light', 'on') == ('Desk lamp is on.', True)
    assert engine.run('desk', 'light', 'dim', '40') == ('Desk lamp is at 40%.', True)
    assert lamp.sent[-1] == ('192.168.0.20', 'lamp-token-12345', '/dim?to=40')         # and it reached the driver
    assert engine.run('desk', 'light', 'dim', 'bright') == \
        ("dim takes a number from 0 to 100, not 'bright'.", False)

    listing, ok = engine.run('desk')
    assert ok and 'light  switch and dim it  device_action("desk","light","on")' in listing
    st = engine.status('desk')
    assert st['online'] is True and st['parts'][0]['readings'] == {'brightness': '40%'}


def test_the_guides_driver_says_plainly_what_is_wrong(lamp):
    with pytest.raises(engine.DeviceError, match="The lamp's address is needed."):
        engine.add('nowhere', '', 'lamp', {'host': '  '})
    engine.add('dark', 'Desk lamp', 'lamp', {'host': 'unplugged', 'token': 'lamp-token-12345'})
    assert engine.run('dark', 'light', 'on') == \
        ('Could not reach the lamp at unplugged. Is it plugged in?', False)
    st = engine.status('dark')
    assert st['online'] is False and st['parts'][0]['detail'].startswith('Could not reach the lamp')


def test_every_example_in_the_guides_help_really_runs(lamp):
    engine.add('desk', 'Desk lamp', 'lamp', {'host': '192.168.0.20', 'token': 'lamp-token-12345'})
    for cap in engine.describe(engine.get('desk')):
        for action, a in cap['actions'].items():
            text, ok = engine.run('desk', cap['capability'], action, a['example'])
            assert ok, (action, text)


def test_the_guides_own_test_example_is_true(lamp):
    found = [b for b in _blocks('python') if 'def test_dim' in b]
    assert len(found) == 1
    space = {}
    sys.modules['device_driver'] = lamp.module
    try:
        exec(compile(found[0], str(GUIDE), 'exec'), space)
        space['test_dim']()
    finally:
        sys.modules.pop('device_driver', None)


# --- the guide's presence example: a board on USB ------------------------------

def _presence_source():
    found = [b for b in _blocks('python') if 'def discover(' in b and 'def tend(' in b]
    assert len(found) == 1, "the guide should hold exactly one presence example"
    return found[0]


def _presence_entry():
    found = [b for b in _blocks('json') if '"presence"' in b]
    assert len(found) == 1, "the guide should hold exactly one manifest entry with presence"
    return json.loads(found[0])


class _Port:
    def __init__(self, path):
        self.path, self.closed = path, False

    def close(self):
        self.closed = True


def test_the_guides_presence_example_runs_through_the_real_keeper(tmp_path):
    import importlib
    import threading
    import time
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    from core.devices import presence

    boards = tmp_path / 'by-id'
    boards.mkdir()
    a, b = 'usb-Espressif_Board-A_1111-if00', 'usb-Espressif_Board-B_2222-if00'
    (boards / a).write_text('')
    (boards / b).write_text('')
    opened = []
    name = 'plugins.my-board.device_driver'
    mod = types.ModuleType(name)
    mod.open_port = lambda path: opened.append(_Port(path)) or opened[-1]
    exec(compile(_presence_source(), 'the guide', 'exec'), mod.__dict__)
    mod.BY_ID = str(boards)
    mod.describe = lambda device, config: {'light': {'label': 'Light', 'help': 'a light', 'actions': {}}}
    mod.status = lambda device, config, secrets: {'online': True, 'detail': '', 'readings': {}}
    mod.run = lambda *a: ('', True)
    store = FakeStore()

    def until(test, seconds=4.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not test():
            time.sleep(0.02)
        return bool(test())

    with patch.object(engine, '_store', lambda: store), \
         patch.object(engine, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
         patch.object(engine, '_all_plugin_info', lambda: []), \
         patch.object(engine, '_managed', lambda: False), \
         patch.object(engine, '_part_secrets', lambda d, p: sec.Secrets(d, {})), \
         patch.object(sec, 'delete', lambda d: True), patch.object(sec, 'status', lambda d: {}), \
         patch.object(presence, 'SETTLE', 0), patch.object(presence, 'GATHER', 0.02), \
         patch.object(presence, 'LAST_WORD', 2):
        importlib.reload(reg)
        reg.CORE_DRIVERS = ()
        engine._modules.clear()
        engine._modules_gen = None
        sys.modules[name] = mod
        try:
            entry = _presence_entry()
            assert reg.register_driver(entry['driver'], entry, 'my-board')
            assert reg.get_driver('board')['presence'] is True
            assert [t['name'] for t in engine.found('board')] == ['Board-A', 'Board-B']
            engine.add('left', 'Left board', 'board', {'which': {'all': False, 'only': [{'id': a, 'name': 'Board-A'}]}})
            engine.add('right', 'Right board', 'board', {'which': {'all': False, 'only': [{'id': b, 'name': 'Board-B'}]}})
            presence.start()
            assert until(lambda: len(mod._held) == 2)                 # each device holds its own board
            assert mod._held['left']['path'].endswith(a) and mod._held['right']['path'].endswith(b)
            assert len(opened) == 2
            left, right = mod._held['left']['port'], mod._held['right']['port']   # opened in any order

            (boards / b).unlink()                                     # one is pulled out
            assert until(lambda: 'right' not in mod._held)            # the guide's watch saw it
            assert right.closed and not left.closed
            (boards / b).write_text('')                               # and plugged back in
            assert until(lambda: 'right' in mod._held) and len(opened) == 3

            presence.release('my-board')                              # the plugin unloads
            assert mod._held == {} and all(p.closed for p in opened)
            assert not [t for t in threading.enumerate() if t.name == 'device-watch-board' and t.is_alive()]
        finally:
            presence.stop()
            for held in (presence._told, presence._busy, presence._watchers):
                held.clear()
            presence._pending.clear()
            presence._again.clear()
            presence._wake.clear()
            sys.modules.pop(name, None)
            engine._modules.clear()
            engine._modules_gen = None
            engine._status.clear()
            importlib.reload(reg)
