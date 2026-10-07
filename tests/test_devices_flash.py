# tests/test_devices_flash.py - a board set up from the browser
# (tmp/board-flash-web-plan.md): engine.provision, engine.learned, the
# firmware source, and the provision door. Real registry and secret store
# on temp files, one fake driver that learns its address.

import importlib
import io
import json
import sys
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


def test_provision_again_is_the_same_device_with_fresh_keys(rig):
    row, first = core.provision('pocket', 'board')
    core.learned('pocket', '192.168.1.40')
    row2, second = core.provision('pocket', 'board')
    assert row2['id'] == row['id'] and list(core.rows()) == ['pocket']
    assert second['token'] != first['token']
    assert core._part_secrets('pocket', 'board').get('voice_key') == second['voice_key']
    assert core.get('pocket')['parts'][0]['config']['url'] == 'http://192.168.1.40'   # the address it had is kept


def test_provision_refuses_bad_names_and_other_kinds(rig):
    for bad in ('', '!!!', 'found', 'firmware', 'provision'):
        with pytest.raises(core.DeviceError):
            core.provision(bad, 'board')
    core.add('lamp1', '', 'lamp', {'url': 'http://10.0.0.5'})
    with pytest.raises(core.DeviceError, match='different kind'):
        core.provision('lamp1', 'board')


# --- learned: the address comes from the board's own call --------------------------

def test_learned_fills_an_empty_address_and_tells_the_board(rig):
    core.provision('pocket', 'board')
    core.learned('pocket', '192.168.1.40')
    assert core.get('pocket')['parts'][0]['config']['url'] == 'http://192.168.1.40'
    assert rig.told == [('pocket', 'http://192.168.1.40')]          # apply() ran, with the address it can use now


def test_learned_follows_a_new_lease_and_keeps_scheme_and_port(rig):
    core.add('pi', '', 'board', {'url': 'https://192.168.1.50:8090', 'token': 'k', 'voice_key': 'v'})
    core.learned('pi', '192.168.1.51')
    assert core.get('pi')['parts'][0]['config']['url'] == 'https://192.168.1.51:8090'


def test_learned_ignores_the_same_host_cheaply_and_never_believes_the_internet(rig):
    core.provision('pocket', 'board')
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
    (root / 'pocket').mkdir(parents=True)
    (root / 'pocket' / 'app.bin').write_bytes(b'\xe9' + b'x' * 99)
    (root / 'pocket' / 'boot.bin').write_bytes(b'b' * 10)
    (root / 'pocket' / 'manifest.json').write_text(json.dumps({
        'name': 'Pocket', 'version': version,
        'builds': [{'chipFamily': chip, 'flash': {'mode': 'dio', 'size': '4MB', 'freq': '40m'},
                    'parts': [{'path': 'boot.bin', 'offset': 4096}, {'path': 'app.bin', 'offset': 65536}]}]}))
    (root / 'index.json').write_text(json.dumps({'boards': [{'id': 'pocket', 'name': 'Pocket (CYD)', 'manifest': 'pocket/manifest.json'}]}))
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
        assert b['parts'] == [{'path': 'boot.bin', 'offset': 4096}, {'path': 'app.bin', 'offset': 65536}]
        assert 'manifest' not in b and 'folder' not in b
        assert firmware.part('pocket', 'app.bin').read_bytes().startswith(b'\xe9')
        for bad in ('../index.json', 'boot.bin/../app.bin', 'nope.bin', '/etc/passwd'):
            with pytest.raises(firmware.FirmwareError):
                firmware.part('pocket', bad)
        with pytest.raises(firmware.FirmwareError):
            firmware.part('other', 'app.bin')


def test_no_source_and_a_broken_manifest(fw):
    with patch.object(firmware, 'source', lambda: ''):
        assert firmware.index()['boards'] == [] and 'No firmware source' in firmware.index()['error']
    root = _index(fw / 'src')
    (root / 'pocket' / 'manifest.json').write_text('{"name": "x"}')
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
            return Resp(200, (src / 'pocket' / 'app.bin').read_bytes())
        rel = url.replace('https://example.test/fw/', '')
        if rel == 'pocket/app.bin':
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
        assert (fw / 'cache' / 'pocket' / '0.2.0' / 'manifest.json').is_file()
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
                          if url.endswith('app.bin') else Resp(200, (src / url.split('/fw/')[1]).read_bytes())):
            firmware.index()
            with pytest.raises(firmware.FirmwareError, match='redirect'):
                firmware.part('pocket', 'app.bin')
        firmware._recent = (0.0, None)
        with patch.object(firmware, 'PART_MAX', 50), \
             patch.object(firmware.net, 'get', lambda url, **k: Resp(200, (src / url.split('/fw/')[1]).read_bytes())):
            firmware.index()
            with pytest.raises(firmware.FirmwareError, match='larger'):
                firmware.part('pocket', 'app.bin')
            assert not (fw / 'cache' / 'pocket' / '0.2.0' / 'app.bin').exists()
            assert not list((fw / 'cache' / 'pocket' / '0.2.0').glob('*.partial'))


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
        out = routes.provision_device({'label': 'pocket', 'driver': 'board', 'sapphire': 'http://sapphire-box:8073'})
        assert out['sapphire'] == 'http://sapphire-box:8073'
        for bad in ('https://sapphire.example.com:8073', 'ftp://192.168.0.1', 'https://8.8.8.8'):
            with pytest.raises(core.DeviceError):
                routes.provision_device({'label': 'pocket', 'driver': 'board', 'sapphire': bad})


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
