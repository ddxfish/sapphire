# tests/test_devices_flash.py - a board set up from the browser
# (tmp/board-flash-web-plan.md): engine.provision, engine.learned, the
# firmware source, and the provision door. Real registry and secret store
# on temp files, one fake driver that learns its address.

import importlib
import io
import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from core.devices import engine as core
from core.devices import firmware
from core.devices import health
from core.devices.drivers import satellite as sat
from core.routes import devices as routes

SCHEMA = [
    {'key': 'url', 'type': 'string', 'label': 'Address'},
    {'key': 'token', 'type': 'string', 'secret': True, 'label': 'Key'},
    {'key': 'voice_key', 'type': 'string', 'secret': True, 'label': 'Key back'},
]
BOARD = {'label': 'Fake board', 'module': 'device_driver.py', 'capabilities': ['light'],
         'config_schema': SCHEMA, 'learns_address': True}
LAMP = {'label': 'Fake lamp', 'module': 'lamp_driver.py', 'capabilities': ['light'],
        'config_schema': [{'key': 'url', 'type': 'string', 'label': 'Address'}]}
MOD, LAMP_MOD = 'plugins.fakeplug.device_driver', 'plugins.fakeplug.lamp_driver'


class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def update_with_lock(self, k, mutator, default=None):
        self.d[k] = mutator(self.d.get(k, default))
        return self.d[k]


def _driver(name, told):
    m = types.ModuleType(name)
    m.describe = lambda device, config: {'light': {'label': 'Light', 'help': '', 'actions': {}}}
    m.status = lambda device, config, secrets: {'online': bool(config.get('url')), 'detail': config.get('url') or 'waiting'}
    m.run = lambda *a: ('', True)
    m.apply = lambda device, config, secrets: told.append((device['id'], config.get('url')))
    return m


@pytest.fixture
def rig(tmp_path):
    from core.credentials_manager import CredentialsManager
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    told, store, hstore = [], FakeStore(), FakeStore()
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        mgr = CredentialsManager()
        with patch.object(sec, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(sec, 'CONFIG_DIR', tmp_path), \
             patch.object(sec, '_crypto', lambda: mgr), \
             patch.object(core, '_store', lambda: store), \
             patch.object(health, '_store', lambda: hstore), \
             patch.object(core, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
             patch.object(core, '_all_plugin_info', lambda: []), \
             patch.object(core, '_managed', lambda: False), \
             patch.object(core.threading, 'Timer', lambda delay, fn, args=(): types.SimpleNamespace(start=lambda: fn(*args))):
            sec.reload()
            importlib.reload(reg)
            reg.CORE_DRIVERS = ()
            core._modules.clear(); core._modules_gen = None; core._seen_from.clear()
            health._belief.clear(); health._saved = None
            sys.modules[MOD], sys.modules[LAMP_MOD] = _driver(MOD, told), _driver(LAMP_MOD, told)
            assert reg.register_driver('board', BOARD, 'fakeplug')
            assert reg.register_driver('lamp', LAMP, 'fakeplug')
            yield types.SimpleNamespace(told=told, store=store, sec=sec, tmp=tmp_path)
            sys.modules.pop(MOD, None); sys.modules.pop(LAMP_MOD, None)
            core._modules.clear(); core._modules_gen = None; core._seen_from.clear()
            health._belief.clear(); health._saved = None
            importlib.reload(reg)
            sec.reload()


# --- provision: a name and two keys, no address --------------------------------

def test_name_from_label():
    assert core._name_from('Kitchen Pocket') == 'kitchen-pocket'
    assert core._name_from('  pocket  ') == 'pocket'
    assert core._name_from('Pi #2 (hall)') == 'pi-2-hall'
    assert core._name_from('---') == ''
    assert len(core._name_from('x' * 50)) == 33


def test_provision_makes_a_row_with_no_address_and_two_keys(rig):
    row, keys = core.provision('Kitchen Pocket', 'board', location='Kitchen')
    assert row['id'] == 'kitchen-pocket' and row['label'] == 'Kitchen Pocket' and row['location'] == 'Kitchen'
    assert row['parts'][0]['config']['url'] == ''
    assert set(keys) == {'token', 'voice_key'} and all(len(v) >= 30 for v in keys.values())
    assert keys['token'] != keys['voice_key']
    assert rig.sec.status('kitchen-pocket') == {'board.token': 'set', 'board.voice_key': 'set'}
    assert keys['token'] not in json.dumps(rig.store.d)                 # the keys never land in the table
    assert core._part_secrets('kitchen-pocket', 'board').get('token') == keys['token']


def test_provision_again_keeps_the_old_keys_until_the_board_calls_in_with_the_new(rig):
    """Flashing a board again: the device keeps working on its old keys
    while the new ones wait. A setup that fails costs nothing; one that
    succeeds proves itself when the board calls in (voice.key_ok)."""
    from core.devices import voice
    row, first = core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:01')
    core.learned('pocket', '192.168.1.40')
    row2, second = core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:01')
    assert row2['id'] == row['id'] and list(core.rows()) == ['pocket']
    assert second['token'] != first['token']
    assert core._part_secrets('pocket', 'board').get('voice_key') == first['voice_key']      # still the old ones
    assert core.promote('pocket', 'not-a-key') is False and core.promote('pocket', first['voice_key']) is False
    assert core._part_secrets('pocket', 'board').get('voice_key') == first['voice_key']
    # the board calls in with the new key: that is the moment they become its keys
    with patch.object(voice, '_talk_part', lambda row: row['parts'][0]):
        assert voice.key_ok('pocket', first['voice_key']) is True
        assert voice.key_ok('pocket', second['voice_key']) is True
        assert voice.key_ok('pocket', first['voice_key']) is False
    assert core._part_secrets('pocket', 'board').get('token') == second['token']
    assert core.get('pocket')['parts'][0]['config']['url'] == 'http://192.168.1.40'   # the same board: address kept
    assert 'pocket' not in core._pending
    # minted keys that no board ever brings are forgotten
    core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:01')
    core._pending['pocket']['ts'] -= core.PENDING_FOR + 1
    assert core.promote('pocket', core._pending['pocket']['keys']['voice_key']) is False
    assert 'pocket' not in core._pending


def test_a_known_board_given_a_new_name_is_renamed_when_it_calls_in(rig):
    row, first = core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:01')
    core.learned('pocket', '192.168.1.40')
    row2, second = core.provision('hall', 'board', mac='aa:bb:cc:dd:ee:01')
    assert row2['id'] == 'hall' and list(core.rows()) == ['pocket']               # not yet
    assert core.promote('hall', second['voice_key']) is True
    assert list(core.rows()) == ['hall'] and core.get('hall')['fingerprint'] == 'aa:bb:cc:dd:ee:01'
    assert core._part_secrets('hall', 'board').get('token') == second['token']
    assert core.get('hall')['parts'][0]['config']['url'] == 'http://192.168.1.40'
    core.add('lamp1', '', 'board', {'url': ''})
    with pytest.raises(core.DeviceError, match="is the device 'hall'"):          # a name another device has
        core.provision('lamp1', 'board', mac='aa:bb:cc:dd:ee:01')


def test_a_name_in_use_by_a_board_that_cannot_be_proven_needs_replace(rig):
    """A row made by hand, or a board whose id could not be read: the name
    may be taken over only when the page says so. The old board then loses
    its keys and its address when the new one calls in."""
    core.add('pocket', '', 'board', {'url': 'http://192.168.1.40', 'token': 'old-t', 'voice_key': 'old-v'})
    with pytest.raises(core.DeviceError, match=core.REPLACE_NEEDED):
        core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:02')
    with pytest.raises(core.DeviceError, match=core.REPLACE_NEEDED):
        core.provision('pocket', 'board')                                           # no mac read either
    assert core._part_secrets('pocket', 'board').get('token') == 'old-t'
    row, keys = core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:02', replace=True)
    assert core._part_secrets('pocket', 'board').get('token') == 'old-t'              # until it calls in
    assert core.promote('pocket', keys['voice_key']) is True
    assert core._part_secrets('pocket', 'board').get('token') == keys['token']
    assert core.get('pocket')['fingerprint'] == 'aa:bb:cc:dd:ee:02'
    assert core.get('pocket')['parts'][0]['config']['url'] == ''                      # another board: learned afresh
    core.learned('pocket', '192.168.1.77')
    assert core.get('pocket')['parts'][0]['config']['url'] == 'http://192.168.1.77'
    # removing the device forgets what was minted for it
    core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:02')
    core.remove('pocket')
    assert core._pending == {}


def test_provision_refuses_bad_names_and_other_kinds(rig):
    for bad in ('', '!!!', 'found', 'firmware', 'provision'):
        with pytest.raises(core.DeviceError):
            core.provision(bad, 'board')
    core.add('lamp1', '', 'lamp', {'url': 'http://10.0.0.5'})
    with pytest.raises(core.DeviceError, match='different kind'):
        core.provision('lamp1', 'board')


# --- one bad key, one plugin per row -----------------------------------------------

HORN = {'label': 'Fake horn', 'module': 'horn_driver.py', 'capabilities': ['speaker'], 'config_schema': SCHEMA}
HORN_MOD = 'plugins.fakeplug.horn_driver'


def test_one_unreadable_secret_leaves_only_that_device_out(rig):
    """A key this machine cannot read (the salt changed, user/ restored on
    another box) takes that device out of her voice, her list and the backup
    targets: never every device."""
    import core.devices.registry as reg
    horn = _driver(HORN_MOD, rig.told)
    horn.play = lambda *a: {'ok': True}
    sys.modules[HORN_MOD] = horn
    assert reg.register_driver('horn', HORN, 'fakeplug')
    try:
        core._unreadable.clear()
        for name in ('good', 'bad'):
            core.add(name, '', 'horn', {'url': f'http://10.0.0.{len(name)}', 'token': 't', 'voice_key': 'v'})
            core.add(name + '-lamp', '', 'board', {'url': 'http://10.0.0.7', 'token': 't', 'voice_key': 'v'})
        real = rig.sec.resolve

        def resolve(device_id):
            if device_id.startswith('bad'):
                raise RuntimeError('the salt is gone')
            return real(device_id)

        with patch.object(rig.sec, 'resolve', resolve):
            assert core.speaker('good') is not None and core.speaker('bad') is None
            assert [b['id'] for _, b, _, _ in core.speakers(online=False)] == ['good']
            assert [b['id'] for _, b, _, _ in core.doors('light', 'apply', online=False)] == ['good-lamp']
            assert [d['id'] for d in core.fleet()] == ['bad', 'bad-lamp', 'good', 'good-lamp']   # listed, not blind
            assert 'bad' in core.list_text()[0]
            assert core._unreadable == {('bad', 'horn'), ('bad-lamp', 'board')}   # said once each
    finally:
        sys.modules.pop(HORN_MOD, None)


def test_a_row_keeps_the_plugin_it_was_set_up_with(rig):
    """Driver ids are first come, first served. A row made with one plugin's
    driver is never handed to another plugin that took the same id later."""
    core.add('lamp1', '', 'lamp', {'url': 'http://10.0.0.5'})
    row = core.get('lamp1')
    assert row['parts'][0]['plugin'] == 'fakeplug'
    assert core._driver('lamp', 'fakeplug')[1]['plugin_name'] == 'fakeplug'
    assert core._driver('lamp', '')[1]['plugin_name'] == 'fakeplug'           # a row from before plugins were kept
    with pytest.raises(core.DeviceError, match='set up with the otherplug'):
        core._driver('lamp', 'otherplug')
    row['parts'][0]['plugin'] = 'otherplug'
    caps = core.describe(row)
    assert caps and 'set up with the otherplug' in caps[0]['error']            # the page says so; nothing runs


# --- learned: the address comes from the board's own call --------------------------

def test_learned_fills_an_empty_address_and_tells_the_board(rig):
    core.provision('pocket', 'board')
    core.learned('pocket', '192.168.1.40')
    assert core.get('pocket')['parts'][0]['config']['url'] == 'http://192.168.1.40'
    assert rig.told == [('pocket', 'http://192.168.1.40')]          # apply() ran, with the address it can use now


def test_learned_follows_a_new_lease_only_once_the_old_address_stops_answering(rig):
    """While the keeper still reaches the device where it is, a call from
    elsewhere is not believed: a typed address behind a gateway stays, and
    someone else holding its key cannot pull her traffic their way. Two
    missed probes later, a real move is followed, scheme and port kept."""
    core.add('pi', '', 'board', {'url': 'https://192.168.1.50:8090', 'token': 'k', 'voice_key': 'v'})
    looked = []
    with patch.object(health, 'look', looked.append):
        core.learned('pi', '192.168.1.51')
        assert core.get('pi')['parts'][0]['config']['url'] == 'https://192.168.1.50:8090'
        assert looked == ['pi'] and core._declined['pi'][0] == '192.168.1.51'
        core.learned('pi', '192.168.1.51')                              # asked again only after a while
        assert looked == ['pi']
        health.told('pi', {'online': False, 'parts': []})
        core._declined.clear()
        core.learned('pi', '192.168.1.51')                              # one miss is not gone yet
        assert core.get('pi')['parts'][0]['config']['url'] == 'https://192.168.1.50:8090' and looked == ['pi', 'pi']
        health.told('pi', {'online': False, 'parts': []})
        core._declined.clear()
        core.learned('pi', '192.168.1.51')
    assert core.get('pi')['parts'][0]['config']['url'] == 'https://192.168.1.51:8090'
    assert health.reached('pi')                                         # the new address starts with a clean slate
    # a typed address that answers is never clobbered, however often the board calls from elsewhere
    core.update('pi', parts={'board': {'url': 'https://192.168.1.60:8090'}})
    health.told('pi', {'online': True, 'parts': []})
    core.learned('pi', '192.168.1.51')
    assert core.get('pi')['parts'][0]['config']['url'] == 'https://192.168.1.60:8090'
    # an address it never had is learned at once
    core.update('pi', parts={'board': {'url': ''}})
    core.learned('pi', '192.168.1.52')
    assert core.get('pi')['parts'][0]['config']['url'] == 'http://192.168.1.52'


def test_learned_ignores_the_same_host_cheaply_and_never_believes_the_internet(rig):
    core.provision('pocket', 'board')
    health.told('pocket', {'online': False, 'parts': []}); health.told('pocket', {'online': False, 'parts': []})
    core.learned('pocket', '8.8.8.8')
    assert core.get('pocket')['parts'][0]['config']['url'] == ''
    core.learned('pocket', '192.168.1.40')
    rig.told.clear()
    with patch.object(core, '_write', side_effect=AssertionError('no write')):
        core.learned('pocket', '192.168.1.40')                       # the same host: not even read
    assert rig.told == []
    core.learned('nobody', '192.168.1.9')                            # an unknown device: nothing happens


def test_learned_touches_only_drivers_that_learn(rig):
    core.add('lamp1', '', 'lamp', {'url': 'http://10.0.0.5'})
    core.learned('lamp1', '10.0.0.6')
    assert core.get('lamp1')['parts'][0]['config']['url'] == 'http://10.0.0.5'


def test_ipv6_host_is_bracketed(rig):
    core.provision('pocket', 'board')
    core.learned('pocket', 'fe80::1')
    assert core.get('pocket')['parts'][0]['config']['url'] == 'http://[fe80::1]'


# --- the satellite driver without an address -------------------------------------

def test_satellite_waits_without_an_address():
    cfg, err = sat.validate({'url': ''})
    assert err == '' and cfg['url'] == ''
    st = sat.status({'id': 'pocket'}, {'url': ''}, types.SimpleNamespace(get=lambda k: 'key'))
    assert st['online'] is False and 'call in' in st['detail']


# --- the firmware source -------------------------------------------------------------

def _index(root, version='0.2.0', chip='ESP32'):
    """A firmware source: one file for the board, its two parts at their addresses."""
    import hashlib
    root.mkdir(parents=True)
    boot, app = b'b' * 10, b'\xe9' + b'x' * 99
    image = b'\xff' * 4096 + boot + b'\xff' * (65536 - 4096 - len(boot)) + app
    (root / f'pocket-{version}.bin').write_bytes(image)
    sha = lambda d: hashlib.sha256(d).hexdigest()
    (root / 'index.json').write_text(json.dumps({'boards': [{
        'id': 'pocket', 'name': 'Pocket (CYD)', 'version': version, 'chipFamily': chip,
        'flash': {'mode': 'dio', 'size': '4MB', 'freq': '40m'},
        'file': f'pocket-{version}.bin', 'size': len(image), 'sha256': sha(image),
        'parts': [{'name': 'boot', 'offset': 4096, 'size': len(boot), 'sha256': sha(boot)},
                  {'name': 'app', 'offset': 65536, 'size': len(app), 'sha256': sha(app), 'app': True}]}]}))
    return root


@pytest.fixture
def fw(tmp_path):
    firmware._recent = (0.0, None)
    with patch.object(firmware, 'CACHE', tmp_path / 'cache'):
        yield tmp_path
    firmware._recent = (0.0, None)


def test_local_folder_source(fw):
    _index(fw / 'src')
    with patch.object(firmware, 'source', lambda: str(fw / 'src')):
        idx = firmware.index()
        assert idx['error'] is None and [b['id'] for b in idx['boards']] == ['pocket']
        b = idx['boards'][0]
        assert b['chipFamily'] == 'ESP32' and b['version'] == '0.2.0' and b['flash']['size'] == '4MB'
        assert [(p['path'], p['offset'], p['size'], p['app']) for p in b['parts']] == [('boot.bin', 4096, 10, False), ('app.bin', 65536, 100, True)]
        assert 'entry' not in b and 'file' not in b
        assert firmware.part('pocket', 'app.bin').read_bytes() == b'\xe9' + b'x' * 99        # cut out of the one file
        assert firmware.part('pocket', 'boot.bin').read_bytes() == b'b' * 10
        for bad in ('../index.json', 'boot.bin/../app.bin', 'nope.bin', '/etc/passwd'):
            with pytest.raises(firmware.FirmwareError):
                firmware.part('pocket', bad)
        with pytest.raises(firmware.FirmwareError):
            firmware.part('other', 'app.bin')


def test_no_source_and_a_broken_index(fw):
    with patch.object(firmware, 'source', lambda: ''):
        assert firmware.index()['boards'] == [] and 'No firmware source' in firmware.index()['error']
    root = _index(fw / 'src')
    (root / 'index.json').write_text('{"boards": [{"id": "pocket", "name": "x"}]}')
    with patch.object(firmware, 'source', lambda: str(root)):
        idx = firmware.index()
        assert idx['boards'] == [] and 'version' in idx['error']


class Resp:
    def __init__(self, status=200, body=b'', headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


def test_url_source_caches_parts_and_drops_old_versions(fw):
    src = _index(fw / 'web')
    calls = []

    def get(url, **kw):
        calls.append(url)
        assert kw.get('allow_redirects') is False
        if url.startswith('https://objects.githubusercontent.com/'):          # a release asset, after its 302
            return Resp(200, (src / 'pocket-0.2.0.bin').read_bytes())
        rel = url.replace('https://example.test/fw/', '')
        if rel == 'pocket-0.2.0.bin':
            return Resp(302, headers={'Location': 'https://objects.githubusercontent.com/x'})
        p = src / rel
        return Resp(200, p.read_bytes()) if p.is_file() else Resp(404)

    (fw / 'cache' / 'pocket' / '0.1.0').mkdir(parents=True)
    (fw / 'cache' / 'pocket' / '0.1.0' / 'app.bin').write_bytes(b'old')
    with patch.object(firmware, 'source', lambda: 'https://example.test/fw/'), patch.object(firmware.net, 'get', get):
        idx = firmware.index()
        assert idx['error'] is None and idx['boards'][0]['version'] == '0.2.0'
        where = firmware.part('pocket', 'app.bin')
        assert where == fw / 'cache' / 'pocket' / '0.2.0' / 'app.bin' and where.read_bytes().startswith(b'\xe9')
        assert not (fw / 'cache' / 'pocket' / '0.1.0').exists()
        assert (fw / 'cache' / 'pocket' / '0.2.0' / 'board.json').is_file()
        n = len(calls)
        assert firmware.part('pocket', 'app.bin') == where and len(calls) == n       # cached: nothing fetched
        firmware.part('pocket', 'boot.bin')
    # the source gone: the board whose parts are all in the cache is still offered, from there
    firmware._recent = (0.0, None)
    with patch.object(firmware, 'source', lambda: 'https://example.test/fw/'), \
         patch.object(firmware.net, 'get', lambda *a, **k: Resp(500)):
        idx = firmware.index()
        assert 'Could not read' in idx['error'] and [b['id'] for b in idx['boards']] == ['pocket']
        assert firmware.part('pocket', 'boot.bin').read_bytes() == b'b' * 10
        assert firmware.part('pocket', 'app.bin') == where


def test_url_source_refuses_strange_redirects_and_big_parts(fw):
    src = _index(fw / 'web')
    with patch.object(firmware, 'source', lambda: 'https://example.test/fw/'):
        with patch.object(firmware.net, 'get', lambda url, **k: Resp(302, headers={'Location': 'https://evil.test/x'})
                          if url.endswith('.bin') else Resp(200, (src / url.split('/fw/')[1]).read_bytes())):
            firmware.index()
            with pytest.raises(firmware.FirmwareError, match='redirect'):
                firmware.part('pocket', 'app.bin')
        firmware._recent = (0.0, None)
        with patch.object(firmware, 'FILE_MAX', 50), \
             patch.object(firmware.net, 'get', lambda url, **k: Resp(200, (src / url.split('/fw/')[1]).read_bytes())):
            firmware.index()
            with pytest.raises(firmware.FirmwareError, match='larger'):
                firmware.part('pocket', 'app.bin')
            assert not (fw / 'cache' / 'pocket' / '0.2.0' / 'app.bin').exists()


def test_a_file_that_is_not_the_one_in_the_index_is_never_cut_up(fw):
    root = _index(fw / 'src')
    image = root / 'pocket-0.2.0.bin'
    good = image.read_bytes()
    image.write_bytes(good[:-1] + b'y')
    with patch.object(firmware, 'source', lambda: str(root)):
        with pytest.raises(firmware.FirmwareError, match='sha256'):
            firmware.part('pocket', 'app.bin')
        assert not (fw / 'cache' / 'pocket').exists()
        image.write_bytes(good)
        assert firmware.part('pocket', 'app.bin').is_file()
    # the same version built again: the cache follows the file, not the version number
    image.write_bytes(good[:-1] + b'z')
    idx = root / 'index.json'
    import hashlib
    entry = json.loads(idx.read_text())['boards'][0]
    entry['sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
    entry['parts'][1]['sha256'] = hashlib.sha256(image.read_bytes()[65536:]).hexdigest()
    idx.write_text(json.dumps({'boards': [entry]}))
    firmware._recent = (0.0, None)
    with patch.object(firmware, 'source', lambda: str(root)):
        assert firmware.part('pocket', 'app.bin').read_bytes().endswith(b'z')


def test_a_file_of_ones_own_is_checked_and_kept_as_the_board_own(fw):
    with pytest.raises(firmware.FirmwareError, match='whole-board'):
        firmware.keep_own(b'\xe9' + b'x' * 0x9000, 'app.bin')              # a lone program: no partition table
    with pytest.raises(firmware.FirmwareError, match='bootloader'):
        firmware.keep_own(b'\xff' * 0x8000 + b'\xaa\x50' + b'\xff' * 64, 'x.bin')
    assert firmware.own() is None
    with pytest.raises(firmware.FirmwareError, match='Choose it again'):
        firmware.part('own', 'image.bin')
    esp32 = b'\xff' * 0x1000 + b'\xe9' + b'\0' * 11 + b'\x00\x00' + b'\xff' * (0x7000 - 14) + b'\xaa\x50' + b'rest'
    s3 = b'\xe9' + b'\0' * 11 + b'\x09\x00' + b'\xff' * (0x8000 - 14) + b'\xaa\x50' + b'rest'
    assert firmware.keep_own(esp32, '../../My Build.bin')['chipFamily'] == 'ESP32'
    b = firmware.keep_own(s3, 'mine.bin')                                # the next file replaces the one before
    assert (b['id'], b['name'], b['chipFamily'], b['flash']['size']) == ('own', 'mine.bin', 'ESP32-S3', 'keep')
    assert b['parts'] == [{'path': 'image.bin', 'offset': 0, 'size': len(s3), 'sha256': b['parts'][0]['sha256'], 'app': False}]
    assert firmware.part('own', 'image.bin').read_bytes() == s3 and firmware.own() == b
    assert firmware.part('own', 'image.bin').parent == fw / 'cache' / 'own'
    with pytest.raises(firmware.FirmwareError):
        firmware.part('own', 'other.bin')
    firmware._own = None


def test_a_source_url_ends_with_a_slash_and_a_folder_is_left_alone():
    with patch('config.DEVICE_FIRMWARE_SOURCE', 'https://example.test/fw/releases/latest/download', create=True):
        assert firmware.source() == 'https://example.test/fw/releases/latest/download/'
    with patch('config.DEVICE_FIRMWARE_SOURCE', '/srv/firmware', create=True):
        assert firmware.source() == '/srv/firmware'


def test_latest_download_hops_once_on_github_then_to_the_asset_host(fw):
    """releases/latest/download/x is a 302 to the tagged URL on github.com,
    which is a 302 to objects.githubusercontent.com. A hop anywhere else, or
    a third hop, is refused."""
    src = _index(fw / 'web')
    hops = {'https://example.test/fw/pocket-0.2.0.bin': 'https://example.test/fw/releases/download/v0.2.0/pocket-0.2.0.bin',
            'https://example.test/fw/releases/download/v0.2.0/pocket-0.2.0.bin': 'https://objects.githubusercontent.com/x'}

    def get(url, **kw):
        if url in hops:
            return Resp(302, headers={'Location': hops[url]})
        if url.startswith('https://objects.githubusercontent.com/'):
            return Resp(200, (src / 'pocket-0.2.0.bin').read_bytes())
        p = src / url.replace('https://example.test/fw/', '')
        return Resp(200, p.read_bytes()) if p.is_file() else Resp(404)

    with patch.object(firmware, 'source', lambda: 'https://example.test/fw/'), patch.object(firmware.net, 'get', get):
        firmware.index()
        assert firmware.part('pocket', 'app.bin').read_bytes().startswith(b'\xe9')
    firmware._recent = (0.0, None)
    for path in [fw / 'cache' / 'pocket' / '0.2.0' / 'app.bin']:
        path.unlink()
    with patch.dict(hops, {'https://example.test/fw/releases/download/v0.2.0/pocket-0.2.0.bin': 'https://evil.test/x'}), \
         patch.object(firmware, 'source', lambda: 'https://example.test/fw/'), patch.object(firmware.net, 'get', get):
        firmware.index()
        with pytest.raises(firmware.FirmwareError, match='redirect'):
            firmware.part('pocket', 'app.bin')
    firmware._recent = (0.0, None)
    with patch.dict(hops, {'https://objects.githubusercontent.com/x': 'https://objects.githubusercontent.com/y'}), \
         patch.object(firmware, 'source', lambda: 'https://example.test/fw/'), patch.object(firmware.net, 'get', get):
        firmware.index()
        with pytest.raises(firmware.FirmwareError, match='HTTP 302'):
            firmware.part('pocket', 'app.bin')


# --- the door --------------------------------------------------------------------------

def test_provision_door_hands_the_board_everything_it_needs(rig):
    cfg = types.SimpleNamespace(WEB_UI_SSL_ADHOC=True, WEB_UI_PORT=8073)
    with patch.dict(sys.modules, {'config': cfg}), \
         patch('core.net.local_ips', lambda: ['192.168.1.2', '10.0.0.4']), \
         patch('core.ssl_utils.cert_pem', lambda: '-----BEGIN CERTIFICATE-----\nabc\n'):
        assert routes.here() == {'sapphire': 'https://192.168.1.2:8073',
                                 'addresses': ['https://192.168.1.2:8073', 'https://10.0.0.4:8073']}
        out = routes.provision_device({'label': 'Hall Pocket', 'driver': 'board'})
    assert out['id'] == 'hall-pocket' and out['sapphire'] == 'https://192.168.1.2:8073'
    assert out['cert'].startswith('-----BEGIN') and len(out['token']) >= 30 and len(out['voice_key']) >= 30
    part = out['device']['parts'][0]
    assert out['device']['id'] == 'hall-pocket' and part['values']['url'] == ''
    assert part['values'].get('token') in (None, 'set')                            # never the key itself


def test_provision_door_needs_a_network(rig):
    cfg = types.SimpleNamespace(WEB_UI_SSL_ADHOC=False, WEB_UI_PORT=8073)
    with patch.dict(sys.modules, {'config': cfg}), patch('core.net.local_ips', lambda: []):
        with pytest.raises(core.DeviceError, match='network'):
            routes.provision_device({'label': 'pocket', 'driver': 'board'})
        assert routes.here() == {'sapphire': '', 'addresses': []}


def test_the_user_may_say_where_sapphire_is(rig):
    """The guess can be wrong (a VPN's tunnel address, 2026-10-07): what the
    user typed wins, but it has to be in the house."""
    cfg = types.SimpleNamespace(WEB_UI_SSL_ADHOC=True, WEB_UI_PORT=8073)
    with patch.dict(sys.modules, {'config': cfg}), patch('core.net.local_ips', lambda: ['10.131.93.212']), \
         patch('core.ssl_utils.cert_pem', lambda: ''):
        out = routes.provision_device({'label': 'pocket', 'driver': 'board', 'sapphire': '192.168.0.69:8073/'})
        assert out['sapphire'] == 'https://192.168.0.69:8073'
        with pytest.raises(core.DeviceError, match=core.REPLACE_NEEDED):            # the name is taken, no proof
            routes.provision_device({'label': 'pocket', 'driver': 'board', 'sapphire': 'http://sapphire-box:8073'})
        out = routes.provision_device({'label': 'pocket', 'driver': 'board', 'sapphire': 'http://sapphire-box:8073',
                                       'replace': True})
        assert out['sapphire'] == 'http://sapphire-box:8073' and out['id'] == 'pocket'
        for bad in ('https://sapphire.example.com:8073', 'ftp://192.168.0.1', 'https://8.8.8.8'):
            with pytest.raises(core.DeviceError):
                routes.provision_device({'label': 'pocket', 'driver': 'board', 'sapphire': bad, 'replace': True})


def test_local_ips_skips_tunnels_bridges_and_loopback():
    """psutil's picture of Krem's box on 2026-10-07: the Mullvad tunnel was
    the route out and a board was sent there."""
    import socket
    from core import net
    A = lambda ip, mask: types.SimpleNamespace(family=socket.AF_INET, address=ip, netmask=mask)
    six = types.SimpleNamespace(family=socket.AF_INET6, address='fe80::1', netmask=None)
    table = {'lo': [A('127.0.0.1', '255.0.0.0')], 'eno1': [A('192.168.0.69', '255.255.255.0'), six],
             'virbr0': [A('192.168.122.1', '255.255.255.0')], 'docker0': [A('172.17.0.1', '255.255.0.0')],
             'br-4357': [A('172.19.0.1', '255.255.0.0')], 'wg0-mullvad': [A('10.131.93.212', '255.255.255.255')],
             'eth1': [A('10.1.2.3', '255.255.0.0')], 'wlan0': [A('8.8.8.8', '255.255.255.0')]}
    fake = types.SimpleNamespace(net_if_addrs=lambda: table)
    with patch.dict(sys.modules, {'psutil': fake}), patch.object(net, '_route_out', lambda: '10.131.93.212'):
        assert net.local_ips() == ['192.168.0.69', '10.1.2.3']
        assert net.local_ip() == '192.168.0.69'
    # no VPN: the route out is the house, and it comes first
    with patch.dict(sys.modules, {'psutil': fake}), patch.object(net, '_route_out', lambda: '10.1.2.3'):
        assert net.local_ips() == ['10.1.2.3', '192.168.0.69']
    # no interfaces known: the route out, if it is private at all
    with patch.dict(sys.modules, {'psutil': types.SimpleNamespace(net_if_addrs=lambda: {})}), \
         patch.object(net, '_route_out', lambda: '10.131.93.212'):
        assert net.local_ips() == ['10.131.93.212']
    with patch.dict(sys.modules, {'psutil': types.SimpleNamespace(net_if_addrs=lambda: {})}), \
         patch.object(net, '_route_out', lambda: '8.8.4.4'):
        assert net.local_ips() == []


def test_firmware_names_are_reserved(rig):
    for name in ('firmware', 'provision', 'here'):
        with pytest.raises(core.DeviceError):
            core.add(name, '', 'board', {'url': ''})


# --- the fingerprint: a board is known by its chip ---------------------------------

def test_a_board_keeps_its_name_and_a_name_keeps_its_board(rig):
    row, _ = core.provision('pocket', 'board', mac='AA:BB:CC:DD:EE:01')
    assert row['fingerprint'] == 'aa:bb:cc:dd:ee:01'
    assert core.public(core.get('pocket'))['fingerprint'] == 'aa:bb:cc:dd:ee:01'
    assert routes.list_devices()['devices'][0]['fingerprint'] == 'aa:bb:cc:dd:ee:01'
    # another board claiming this name: refused
    with pytest.raises(core.DeviceError, match='different board'):
        core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:02')
    # the same board again, same name: fine, keys pending, fingerprint kept
    row2, keys = core.provision('pocket', 'board', mac='aa:bb:cc:dd:ee:01')
    assert row2['fingerprint'] == 'aa:bb:cc:dd:ee:01' and list(core.rows()) == ['pocket']
    # a device added by hand (no fingerprint): the page must say replace (above); the board that
    # then calls in is its board from then on
    core.add('lamp1', '', 'board', {'url': ''})
    row3, keys3 = core.provision('lamp1', 'board', mac='aa:bb:cc:dd:ee:03', replace=True)
    assert not core.get('lamp1').get('fingerprint')
    assert core.promote('lamp1', keys3['voice_key']) and core.get('lamp1')['fingerprint'] == 'aa:bb:cc:dd:ee:03'
    # the same board, no mac read this time: still that device, nothing claimed or checked
    assert core.provision('pocket', 'board', replace=True)[0]['fingerprint'] == 'aa:bb:cc:dd:ee:01'
    assert core._mac('junk') == '' and core._mac('AA-BB-CC-DD-EE-FF') == 'aa:bb:cc:dd:ee:ff'


def test_satellite_status_shows_the_board_id():
    from core.devices.drivers import satellite as sat
    with patch.object(sat, '_health', lambda *a, **k: {'ok': True, 'firmware': '0.2.1', 'mac': 'aa:bb:cc:dd:ee:01'}):
        st = sat.status({'id': 'pocket'}, {'url': 'http://192.168.0.5'}, types.SimpleNamespace(get=lambda k: 'key'))
    assert st['readings']['board id'] == 'aa:bb:cc:dd:ee:01'


# --- the server lane: Sapphire's own computer -----------------------------------------

from core.devices import flasher


class FakeEsp:
    CHIP_NAME = 'ESP32'

    def __init__(self):
        self._port = types.SimpleNamespace(close=lambda: None)

    def read_mac(self): return (0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0x01)
    def get_chip_description(self): return 'ESP32-D0WD-V3 (revision 3)'
    def change_baud(self, baud): self.baud = baud


class FakeCmds:
    def __init__(self, fail_write=False):
        self.calls, self.fail_write = [], fail_write

    def detect_chip(self, port, baud=115200, **kw): self.calls.append(('detect', port)); return FakeEsp()
    def run_stub(self, esp): self.calls.append('stub'); return esp
    def attach_flash(self, esp): self.calls.append('attach')
    def reset_chip(self, esp, mode='hard-reset'): self.calls.append(('reset', mode))

    def write_flash(self, esp, parts, **kw):
        from esptool.logger import log
        self.calls.append(('write', [(off, Path(p).name) for off, p in parts], kw))
        for i in range(1, 5):
            log.progress_bar(i * 25, 100)
        if self.fail_write:
            log.die('A fatal error occurred: Timed out waiting for packet header')
        log.print('Hash of data verified.')


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """esptool's API faked, the port 'exists' and is ours, the firmware local."""
    pytest.importorskip('esptool')
    from esptool.logger import EspLog, EspLogBase
    fake = FakeCmds()
    port = tmp_path / 'ttyUSB9'
    port.write_text('')
    monkeypatch.setattr(flasher, '_esptool', lambda: (fake, EspLog, EspLogBase))
    flasher._job = None
    _index(tmp_path / 'src')
    firmware._recent = (0.0, None)
    with patch.object(firmware, 'source', lambda: str(tmp_path / 'src')), patch.object(firmware, 'CACHE', tmp_path / 'cache'):
        yield types.SimpleNamespace(cmds=fake, port=str(port), tmp=tmp_path)
    firmware._recent = (0.0, None)
    flasher._job = None
    EspLog.instance = None


def test_chip_reads_family_text_and_mac_then_lets_the_board_run(lane):
    assert flasher.chip(lane.port) == {'family': 'ESP32', 'text': 'ESP32-D0WD-V3 (revision 3)', 'mac': 'aa:bb:cc:dd:ee:01'}
    assert lane.cmds.calls[-1] == ('reset', 'hard-reset')
    with pytest.raises(flasher.FlashError, match='no /dev/ttyNOPE'):
        flasher.chip('/dev/ttyNOPE')


def test_a_write_runs_in_the_background_and_reports_progress(lane):
    import time
    st = flasher.start(lane.port, 'pocket')
    assert st['state'] in ('getting', 'writing', 'done') and st['board'] == 'pocket' and st['version'] == '0.2.0'
    for _ in range(100):
        st = flasher.status()
        if st['state'] in ('done', 'failed'):
            break
        time.sleep(0.02)
    assert st['state'] == 'done' and st['percent'] == 100 and st['verified'] is True, st
    write = next(c for c in lane.cmds.calls if c[0] == 'write')
    assert write[1] == [(4096, 'boot.bin'), (65536, 'app.bin')]      # every part, the program among them
    assert write[2] == {'flash_freq': '40m', 'flash_mode': 'dio', 'flash_size': '4MB', 'erase_all': True, 'compress': True}
    assert lane.cmds.calls[-1] == ('reset', 'hard-reset')
    with pytest.raises(flasher.FlashError, match='No such board'):
        flasher.start(lane.port, 'nope')
    with pytest.raises(flasher.FlashError, match='No such board'):
        flasher.start(lane.port, 'own')                                  # no file given yet
    image = b'\xe9' + b'\0' * 11 + b'\x09\x00' + b'\xff' * (0x8000 - 14) + b'\xaa\x50'
    firmware.keep_own(image, 'mine.bin')
    lane.cmds.calls.clear()
    flasher.start(lane.port, 'own')
    for _ in range(100):
        if flasher.status()['state'] in ('done', 'failed'):
            break
        time.sleep(0.02)
    assert flasher.status()['state'] == 'done', flasher.status()
    write = next(c for c in lane.cmds.calls if c[0] == 'write')
    assert write[1] == [(0, 'image.bin')] and write[2]['flash_size'] == 'keep'      # the whole file, at address 0
    firmware._own = None


def test_a_failed_write_says_why_and_never_exits(lane):
    import time
    lane.cmds.fail_write = True
    flasher.start(lane.port, 'pocket')
    for _ in range(100):
        if flasher.status()['state'] in ('done', 'failed'):
            break
        time.sleep(0.02)
    st = flasher.status()
    assert st['state'] == 'failed' and 'Timed out' in st['error']


def test_one_write_at_a_time(lane):
    flasher._job = {'state': 'writing', 'percent': 3}
    with pytest.raises(flasher.FlashError, match='already'):
        flasher.start(lane.port, 'pocket')


class FakeSerial:
    """pyserial's Serial, answering the firmware's console."""
    opened = []

    def __init__(self):
        self.port = self.baudrate = self.timeout = None
        self.dtr = self.rts = None
        self.signals, self.sent, self.lines = [], [], []

    def __setattr__(self, k, v):
        if k in ('dtr', 'rts') and v is not None:
            self.__dict__.setdefault('signals', []).append((k, v))
        object.__setattr__(self, k, v)

    def open(self): FakeSerial.opened.append(self.port)
    def reset_input_buffer(self): pass
    def flush(self): pass
    def close(self): pass

    def write(self, data):
        line = data.decode().strip()
        self.sent.append(line)
        if line == 'show':
            self.lines = [b'I (12) wifi: joined\r\n', b'>> {"name": "pocket", "ip": "192.168.0.5"}\r\n']
        elif line.startswith('setup '):
            self.lines = [b'>> {"ok": true}\r\n']
        elif line == 'mute':
            self.lines = [b'I (40) main: busy\r\n']                 # chatter, never an answer
        else:
            self.lines = [b'>> {"error": "commands: show"}\r\n']

    def readline(self):
        return self.lines.pop(0) if self.lines else b''


def test_the_console_boots_the_program_and_stays_open(lane, monkeypatch):
    import serial
    monkeypatch.setattr(serial, 'Serial', FakeSerial)
    monkeypatch.setattr(flasher.time, 'sleep', lambda s: None)
    FakeSerial.opened.clear()
    flasher._consoles.clear()
    got = flasher.ask(lane.port, 'show', 5)
    assert got['answer'] == {'name': 'pocket', 'ip': '192.168.0.5'} and got['said'] == ['I (12) wifi: joined']
    con = flasher._consoles[lane.port]
    assert con.s.signals[:4] == [('dtr', False), ('rts', False), ('rts', True), ('rts', False)]   # run mode, never the loader
    assert flasher.ask(lane.port, 'setup {}', 5)['answer'] == {'ok': True}
    assert FakeSerial.opened == [lane.port]                          # opened once: a second open would reset the board
    got = flasher.ask(lane.port, 'mute', 0.05)                       # no answer: the error and the chatter since THIS line
    assert got['answer'] is None and 'did not answer "mute"' in got['error'] and got['said'] == ['I (40) main: busy']
    with pytest.raises(flasher.FlashError, match='did not answer'):
        con.ask('x', 0.0)                                            # no time to answer: the plain error
    flasher.close(lane.port)
    assert lane.port not in flasher._consoles
    con.used = 0
    flasher._consoles[lane.port] = con
    flasher.tend()
    assert lane.port not in flasher._consoles


# --- over the air: the program itself, from the source to the board ----------------

def test_the_app_part_and_the_version_known_without_the_network(fw):
    _index(fw / 'src')
    with patch.object(firmware, 'source', lambda: str(fw / 'src')):
        assert firmware.known_version('pocket') == ''                  # nothing read yet, nothing cached: no reaching out
        path, version = firmware.app_part('pocket')
        assert path.name == 'app.bin' and version == '0.2.0'
        assert firmware.known_version('pocket') == '0.2.0' and firmware.known_version('nope') == ''
        with pytest.raises(firmware.FirmwareError, match='No firmware'):
            firmware.app_part('nope')
    # an index without the mark cannot be sent over the air
    m = fw / 'src' / 'index.json'
    m.write_text(m.read_text().replace(', "app": true', ''))
    firmware._recent = (0.0, None)
    with patch.object(firmware, 'source', lambda: str(fw / 'src')):
        with pytest.raises(firmware.FirmwareError, match='is the program'):
            firmware.app_part('pocket')


def test_update_sends_the_program_with_its_sha_and_says_what_happens(fw):
    import hashlib
    from core.devices.drivers import satellite as sat
    _index(fw / 'src')
    sent = []

    def call(method, path, config, secrets, timeout=8, headers=None, **kw):
        body = kw['data'].read()
        sent.append((method, path, timeout, headers, body))
        return types.SimpleNamespace(json=lambda: {'ok': True, 'restarting': True, 'slot': 'ota_1'})

    dev, cfg, sec = {'id': 'pocket'}, {'url': 'http://192.168.0.5'}, types.SimpleNamespace(get=lambda k: 'key')
    health = {'ok': True, 'firmware': '0.1.0', 'model': 'pocket', 'slot': 'ota_0', 'mac': 'aa:bb:cc:dd:ee:01'}
    with patch.object(firmware, 'source', lambda: str(fw / 'src')), \
         patch.object(sat, '_health', lambda *a, **k: health), patch.object(sat, '_call', call):
        text, ok = sat.run(dev, 'firmware', 'update', '', cfg, sec, None)
        assert ok and '0.2.0' in text and 'ota_1' in text and 'rolls back' in text.lower() or 'returns by itself' in text
        method, path, timeout, headers, body = sent[0]
        assert (method, path) == ('PUT', '/firmware') and timeout == sat.UPDATE_WAIT
        assert body.startswith(b'\xe9') and headers['X-Sha256'] == hashlib.sha256(body).hexdigest()
        # the status strip says an update is there, from what was just read: no network
        health['has'] = ['light', 'firmware']
        st = sat.status(dev, cfg, sec)
        assert st['readings']['update'].startswith('0.2.0') and st['readings']['updates'] == 'over the air, running ota_0'
        # turned off for this device: no offer, and no update action in its tab
        off = sat.status(dev, dict(cfg, ota=False), sec)
        assert 'update' not in off['readings'] and off['readings']['updates'] == 'over the air, turned off for this device'
        sat._about[dev['id']] = (time.monotonic(), health)
        assert 'update' not in sat.describe(dev, dict(cfg, ota=False))['firmware']['actions']
        assert 'update' in sat.describe(dev, cfg)['firmware']['actions']
        # one program slot: USB only, no Firmware tab at all
        sat._about[dev['id']] = (time.monotonic(), dict(health, has=['light']))
        assert 'firmware' not in sat.describe(dev, cfg)
        assert sat.status(dev, cfg, sec)['readings']['updates'] == 'over USB only: one program slot' or True
        sat._about.pop(dev['id'], None)
        # check: the source read now, in words
        text, ok = sat.run(dev, 'firmware', 'check', '', cfg, sec, None)
        assert ok and 'runs 0.1.0' in text and 'source has 0.2.0' in text
        # the same version again: only on 'again'
        health['firmware'] = '0.2.0'
        text, ok = sat.run(dev, 'firmware', 'check', '', cfg, sec, None)
        assert ok and 'the newest the source has' in text
        text, ok = sat.run(dev, 'firmware', 'update', '', cfg, sec, None)
        assert ok and 'already runs 0.2.0' in text and len(sent) == 1
        text, ok = sat.run(dev, 'firmware', 'update', 'again', cfg, sec, None)
        assert ok and len(sent) == 2
        assert 'update' not in sat.status(dev, cfg, sec)['readings']
        # a board whose program does not say what it is
        del health['model']
        text, ok = sat.run(dev, 'firmware', 'update', '', cfg, sec, None)
        assert not ok and 'too old' in text


def test_update_is_the_owners_and_dangerous():
    from core.devices.drivers import satellite as sat
    sat._about['pocket'] = (time.monotonic(), {'has': ['firmware']})
    try:
        a = sat.describe({'id': 'pocket'}, {})['firmware']['actions']['update']
        assert a['owner'] is True and 'restarts' in a['danger']
        assert a['wait'] == sat.UPDATE_WAIT == 300                         # the page's button waits that long
        assert 'firmware' not in sat.describe({'id': 'pi2'}, {})            # a Pi says no `has`: no tab, no offer
    finally:
        sat._about.pop('pocket', None)


def test_danger_is_the_owners_even_when_a_driver_forgets_to_say_so(rig):
    mod = sys.modules[MOD]
    mod.describe = lambda device, config: {'light': {'label': 'Light', 'help': '', 'actions': {
        'off': {'help': ''}, 'burn': {'help': 'burn the bulb', 'danger': 'Burns the bulb out.'}}}}
    core.add('l1', '', 'board', {'url': 'http://10.0.0.5', 'token': 't', 'voice_key': 'v'})
    assert core.run('l1', 'light', 'off', '')[1] is True
    told, ok = core.run('l1', 'light', 'burn', '')
    assert not ok and 'for the person at' in told
    assert core.run('l1', 'light', 'burn', '', owner=True)[1] is True
    assert 'burn' not in core.status_text('l1')                                   # not in what she reads either


def test_a_chip_is_not_read_while_a_board_is_being_written(lane):
    flasher._job = {'state': 'writing'}
    try:
        with pytest.raises(flasher.FlashError, match='being written'):
            flasher.chip(lane.port)
    finally:
        flasher._job = None


def test_the_firmware_tab_holds_on_what_the_row_remembers():
    from core.devices.drivers import satellite as sat
    sat._about.pop('pocket', None)                                                # a missed probe: cache empty
    assert 'firmware' in sat.describe({'id': 'pocket', 'has': ['firmware', 'light']}, {})
    assert 'firmware' not in sat.describe({'id': 'pocket', 'has': ['light']}, {})
    assert 'firmware' not in sat.describe({'id': 'pocket', 'has': None}, {})        # a Pi: never said


def test_a_picture_takes_only_the_three_handles():
    from core.devices.drivers import satellite as sat
    for bad in ('https://x.test/?beacon', '/etc/hostname', 'img:../x', 'file:///tmp/a'):
        told, ok = sat._picture(bad, {'id': 'pocket'}, {}, None)
        assert not ok and 'image handle' in told


def test_removing_or_renaming_a_device_clears_what_its_voice_held(rig):
    from core.devices import voice
    core.add('pocket', '', 'board', {'url': 'http://10.0.0.5', 'token': 't', 'voice_key': 'v'})
    voice._replies['pocket'] = ['old reply']; voice._showing['pocket'] = 'think'
    core.update('pocket', new_id='hall')
    assert 'pocket' not in voice._replies and 'pocket' not in voice._showing
    voice._replies['hall'] = ['x']
    core.remove('hall')
    assert 'hall' not in voice._replies


def test_slow_actions_say_how_long_and_the_page_hears_it(rig):
    """A driver's `wait` reaches the page through describe(), clamped;
    nothing a driver forgets or garbles becomes a long request."""
    assert core._seconds(300) == 300 and core._seconds('45') == 45
    assert core._seconds(None) == 0 and core._seconds('soon') == 0 and core._seconds(-5) == 0
    assert core._seconds(10 ** 6) == 900
    mod = sys.modules[MOD]
    mod.describe = lambda device, config: {'light': {'label': 'Light', 'help': '', 'actions': {
        'glow': {'help': 'slow fade', 'wait': 120}, 'off': {'help': ''}}}}
    core.add('l1', '', 'board', {'url': 'http://10.0.0.5', 'token': 't', 'voice_key': 'v'})
    acts = core.describe(core.get('l1'))[0]['actions']
    assert acts['glow']['wait'] == 120 and acts['off']['wait'] == 0


def test_a_page_action_on_firmware_or_power_makes_the_keeper_look_again(rig):
    import core.devices.registry as reg
    plug = _driver('plugins.fakeplug.plug_driver', rig.told)
    plug.describe = lambda device, config: {'light': {'label': 'Light', 'help': '', 'actions': {'off': {'help': ''}}},
                                            'power': {'label': 'Power', 'help': '', 'actions': {'restart': {'help': ''}}}}
    sys.modules['plugins.fakeplug.plug_driver'] = plug
    assert reg.register_driver('plug', dict(BOARD, module='plug_driver.py', capabilities=['light', 'power']), 'fakeplug')
    try:
        core.add('p1', '', 'plug', {'url': 'http://10.0.0.5', 'token': 't', 'voice_key': 'v'})
        poked = []
        with patch.object(health, 'poke', poked.append):
            assert core.run('p1', 'light', 'off', '', owner=True)[1] and poked == []       # a light: nothing to re-read
            assert core.run('p1', 'power', 'restart', '', owner=True)[1] and poked == ['p1']
            assert core.run('p1', 'power', 'restart', '')[1] and poked == ['p1']            # hers: the keeper's own clock
    finally:
        sys.modules.pop('plugins.fakeplug.plug_driver', None)
