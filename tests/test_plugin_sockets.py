"""Plugin WebSockets: manifest `capabilities.sockets` -> /ws/plugin/<name>/<path>.

Core owns the door (login + Origin, accept, the cap, close codes); the plugin
handler only talks. Unloading the plugin ends its open sockets.
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.api_fastapi import app
from core.plugin_loader import plugin_loader


async def echo(websocket, settings):
    await websocket.send_json({'hello': settings.get('greeting')})
    while True:
        await websocket.send_text('echo:' + await websocket.receive_text())


@pytest.fixture
def door(monkeypatch):
    """A registered socket for plugin 'sk', the login check passed."""
    async def logged_in(ws):
        return True
    monkeypatch.setattr('core.auth.require_login_ws', logged_in)
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda name: {'greeting': 'hi'})
    monkeypatch.setitem(plugin_loader._sockets, 'sk', {'echo': echo})
    yield TestClient(app)
    plugin_loader._sockets.pop('sk', None)
    plugin_loader._live_sockets.pop('sk', None)


def _closed_with(client, url):
    with client.websocket_connect(url) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    return e.value.code


def test_a_plugin_socket_talks_with_its_settings(door):
    with door.websocket_connect('/ws/plugin/sk/echo') as ws:
        assert ws.receive_json() == {'hello': 'hi'}
        ws.send_text('ping')
        assert ws.receive_text() == 'echo:ping'
    assert not plugin_loader._live_sockets.get('sk')          # counted out on close


def test_no_login_no_socket(monkeypatch):
    monkeypatch.setitem(plugin_loader._sockets, 'sk', {'echo': echo})
    monkeypatch.setattr('core.setup.get_password_hash', lambda: '$2b$12$' + 'x' * 53)
    try:
        assert _closed_with(TestClient(app), '/ws/plugin/sk/echo') == 4401
    finally:
        plugin_loader._sockets.pop('sk', None)


def test_unknown_socket_closes_4404(door):
    assert _closed_with(door, '/ws/plugin/sk/nope') == 4404
    assert _closed_with(door, '/ws/plugin/ghost/echo') == 4404


def test_unloading_the_plugin_ends_its_open_socket(door):
    with door.websocket_connect('/ws/plugin/sk/echo') as ws:
        ws.receive_json()
        plugin_loader._unregister_sockets('sk')
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4410
    assert plugin_loader.get_socket_handler('sk', 'echo') is None


def test_a_plugin_holds_at_most_its_cap(door, monkeypatch):
    import core.routes.plugins as rp
    monkeypatch.setattr(rp, 'SOCKETS_PER_PLUGIN', 1)
    with door.websocket_connect('/ws/plugin/sk/echo') as first:
        first.receive_json()
        assert _closed_with(door, '/ws/plugin/sk/echo') == 4429
        first.send_text('still here')
        assert first.receive_text() == 'echo:still here'


def test_manifest_sockets_load_async_handlers_only(tmp_path):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 's.py').write_text(
        "async def good(websocket, settings):\n    pass\n\ndef sync_one(websocket, settings):\n    pass\n",
        encoding='utf-8')
    try:
        plugin_loader._register_sockets('sk2', tmp_path, [
            {'path': 'good', 'handler': 'routes/s.py:good'},
            {'path': 'bad', 'handler': 'routes/s.py:sync_one'},
            {'path': 'gone', 'handler': 'routes/missing.py:x'},
            {'path': 'out', 'handler': '../escape.py:x'},
        ])
        assert set(plugin_loader._sockets['sk2']) == {'good'}
    finally:
        plugin_loader._sockets.pop('sk2', None)


def test_malformed_socket_and_route_entries_are_skipped_not_raised(tmp_path):
    # one plugin's typo used to raise out of scan(): every plugin after it never loaded
    from core.plugin_loader import plugin_loader
    for bad in (["view"], {"path": "view"}, [{"path": "v", "handler": ["a"]}], "view"):
        plugin_loader._register_sockets('typo', tmp_path, bad)
        plugin_loader._register_routes('typo', tmp_path, bad)
    assert 'typo' not in plugin_loader._sockets and 'typo' not in plugin_loader._routes


def test_scan_survives_a_plugin_that_raises():
    src = open('core/plugin_loader.py', encoding='utf-8').read()
    loop = src.split('for name, info in ordered:')[1][:900]
    assert 'ok = self._load_plugin(name)' in loop and 'except Exception as e:' in loop


# --- "door": "device": a paired device's key opens the socket, not a login ---

import secrets as _secrets
import time as _time

KEY_ID, KEY = 'k1d2c3b4', 'the-key-only-the-device-and-the-store-know'       # 32 characters or more


async def who(websocket, settings, device):
    await websocket.send_json({'device': device})
    while True:
        await websocket.receive_text()


class FakeDevices:
    """core.devices.engine as the door uses it: rows, each part's secrets, the hosted refusal."""
    def __init__(self):
        self.table = {'desk': {'id': 'desk', 'enabled': True,
                               'parts': [{'driver': 'thing', 'plugin': 'sk', 'config': {}}]}}
        self.keys = {'desk': f'{KEY_ID}.{KEY}'}
        self.why = ''
        self.lookups = 0

    def refusal(self):
        return self.why

    def rows(self):
        return self.table

    def _part_secrets(self, device_id, driver_id):
        self.lookups += 1
        return {'socket_key': self.keys.get(device_id, '')}


@pytest.fixture
def device_door(monkeypatch):
    """Plugin 'sk' with a device-door socket 'pipe' and a login-door socket 'echo'; one paired device."""
    from core.devices import door, health
    fake = FakeDevices()
    seen = []
    monkeypatch.setattr(door, '_engine', lambda: fake)
    monkeypatch.setattr(health, 'seen', seen.append)
    monkeypatch.setattr(door, '_tries', type(door._tries)(list))
    monkeypatch.setattr(door, '_used', {})
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda name: {})
    monkeypatch.setitem(plugin_loader._sockets, 'sk', {'pipe': who, 'echo': echo})
    monkeypatch.setitem(plugin_loader._socket_doors, 'sk', {'pipe': 'device'})
    monkeypatch.setattr('core.setup.get_password_hash', lambda: '$2b$12$' + 'x' * 53)
    client = TestClient(app)
    client.fake, client.seen = fake, seen
    yield client
    plugin_loader._sockets.pop('sk', None)
    plugin_loader._socket_doors.pop('sk', None)
    plugin_loader._live_sockets.pop('sk', None)


def _proof(key=KEY, key_id=KEY_ID, plugin='sk', path='pipe', at=None, nonce=None):
    from core.devices import door
    at = int(_time.time() if at is None else at)
    nonce = nonce or _secrets.token_hex(8)
    return [door.NAME, f"sapphire.v1.{key_id}.{at}.{nonce}.{door.signed(plugin, path, key_id, at, nonce, key)}"]


def _refused(client, offered, url='/ws/plugin/sk/pipe'):
    with client.websocket_connect(url, subprotocols=offered) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    return e.value.code


def test_a_paired_device_opens_its_socket_with_a_proof(device_door):
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=_proof()) as ws:
        assert ws.accepted_subprotocol == 'sapphire-device'
        assert ws.receive_json() == {'device': 'desk'}        # the handler is told which device it is
    assert device_door.seen == ['desk']                       # it spoke with its own key: online


def test_a_device_socket_refuses_everything_but_its_own_key(device_door):
    assert _refused(device_door, None) == 4401                                    # nothing offered
    assert _refused(device_door, ['sapphire-device']) == 4401                     # no proof
    assert _refused(device_door, _proof(key='a-guess')) == 4401                   # wrong key
    assert _refused(device_door, _proof(key_id='zzzzzzzz')) == 4401               # a key id nobody holds
    assert _refused(device_door, _proof(path='other')) == 4401                    # signed for another socket
    assert _refused(device_door, _proof(plugin='other')) == 4401                  # ...or another plugin
    assert _refused(device_door, _proof(at=_time.time() - 3600)) == 4401          # an old proof
    assert _refused(device_door, _proof(at=_time.time() + 3600)) == 4401          # a clock far ahead
    assert device_door.seen == []


def test_a_refusal_tells_an_honest_device_the_time(device_door):
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=_proof(key='a-guess')) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401 and abs(int(e.value.reason[2:]) - _time.time()) < 5


def test_a_proof_opens_one_socket_only(device_door):
    offered = _proof()
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=offered) as ws:
        ws.receive_json()
    assert _refused(device_door, offered) == 4401             # the same proof, copied off the wire


def test_a_login_does_not_open_a_device_socket_and_a_proof_does_not_open_a_login_socket(device_door, monkeypatch):
    assert _refused(device_door, _proof(path='echo'), '/ws/plugin/sk/echo') == 4401   # no session: the login door says no

    async def logged_in(ws):
        return True
    monkeypatch.setattr('core.auth.require_login_ws', logged_in)
    assert _refused(device_door, None) == 4401                # a logged-in browser, no proof


def test_a_disabled_or_foreign_device_cannot_open_it(device_door):
    device_door.fake.table['desk']['enabled'] = False
    assert _refused(device_door, _proof()) == 4401
    device_door.fake.table['desk']['enabled'] = True
    device_door.fake.table['desk']['parts'][0]['plugin'] = 'another-plugin'   # the key belongs to another plugin's driver
    assert _refused(device_door, _proof()) == 4401


def test_devices_refused_here_means_no_device_socket(device_door):
    device_door.fake.why = "Devices are not available on hosted Sapphire."
    assert _refused(device_door, _proof()) == 4401


def test_guessing_is_counted_before_any_key_is_looked_at(device_door, monkeypatch):
    from core.devices import door
    monkeypatch.setattr(door, 'PER_MIN', 3)
    for _ in range(3):
        assert _refused(device_door, _proof(key='a-guess')) == 4401
    looked = device_door.fake.lookups
    assert _refused(device_door, _proof()) == 4401            # the right key, but this key id used its tries here
    assert device_door.fake.lookups == looked                 # ...and no key was read to say so
    assert device_door.seen == []


def test_a_stranger_at_the_same_address_cannot_use_up_the_devices_tries(device_door, monkeypatch):
    from core.devices import door
    monkeypatch.setattr(door, 'PER_MIN', 3)
    for _ in range(10):                                       # a web page in that browser: no key id, or a made-up one
        assert _refused(device_door, ['sapphire-device']) == 4401
        assert _refused(device_door, _proof(key_id='notmine1')) == 4401
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=_proof()) as ws:
        assert ws.receive_json() == {'device': 'desk'}


def test_what_is_not_a_proof_costs_no_lookup(device_door):
    for offered in (None, ['sapphire-device'], ['sapphire.v1.short'], _proof(at=_time.time() - 3600)):
        assert _refused(device_door, offered) == 4401
    assert device_door.fake.lookups == 0


def test_a_short_stored_key_opens_nothing(device_door):
    device_door.fake.keys['desk'] = f'{KEY_ID}.tooshort'
    assert _refused(device_door, _proof(key='tooshort')) == 4401


def test_every_refusal_looks_the_same(device_door):
    def how(offered):
        with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=offered) as ws:
            proto = ws.accepted_subprotocol
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
        return proto, e.value.code, e.value.reason[:2]
    device_door.fake.table['ghost'] = {'id': 'ghost', 'enabled': False,
                                       'parts': [{'driver': 'thing', 'plugin': 'sk', 'config': {}}]}
    device_door.fake.keys['ghost'] = 'ghostkid.' + KEY
    used = _proof()
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=used) as ws:
        ws.receive_json()
    answers = {how(o) for o in (_proof(key='a-guess'), _proof(key_id='zzzzzzzz'), _proof(key_id='ghostkid'),
                                _proof(at=_time.time() - 3600), used)}
    assert answers == {('sapphire-device', 4401, 't=')}       # wrong key, unknown, disabled, stale, replayed


def test_a_trailing_slash_is_the_same_socket(device_door):
    with device_door.websocket_connect('/ws/plugin/sk/pipe/', subprotocols=_proof()) as ws:
        assert ws.receive_json() == {'device': 'desk'}


def test_a_used_proof_is_forgotten_only_after_it_could_no_longer_pass(device_door, monkeypatch):
    from core.devices import door
    offered = _proof()
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=offered) as ws:
        ws.receive_json()
    (kept_until,) = door._used.values()
    assert kept_until >= _time.time() + door.WINDOW           # its clock stops passing before the memory of it goes


def test_a_path_declared_twice_keeps_its_first_door(tmp_path):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 's.py').write_text(
        "async def a(websocket, settings):\n    pass\n\nasync def b(websocket, settings, device):\n    pass\n",
        encoding='utf-8')
    try:
        plugin_loader._register_sockets('sk4', tmp_path, [
            {'path': 'x', 'handler': 'routes/s.py:b', 'door': 'device'},
            {'path': 'x', 'handler': 'routes/s.py:a'},
        ])
        handler, door = plugin_loader.get_socket('sk4', 'x')
        assert handler.__name__ == 'b' and door == 'device'
    finally:
        plugin_loader._unregister_sockets('sk4')


def test_a_door_that_changed_mid_handshake_hands_over_nothing(device_door, monkeypatch):
    from core.devices import door

    def reload_then_prove(*a):
        plugin_loader._socket_doors['sk'] = {}                # the plugin reloaded: 'pipe' is a login socket now
        return 'desk', KEY_ID
    monkeypatch.setattr(door, 'prove', reload_then_prove)
    assert _refused(device_door, _proof()) == 4404


def test_an_unknown_door_is_skipped_never_opened_with_a_login(tmp_path):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 's.py').write_text(
        "async def a(websocket, settings):\n    pass\n\nasync def b(websocket, settings, device):\n    pass\n",
        encoding='utf-8')
    try:
        plugin_loader._register_sockets('sk3', tmp_path, [
            {'path': 'plain', 'handler': 'routes/s.py:a'},
            {'path': 'dev', 'handler': 'routes/s.py:b', 'door': 'device'},
            {'path': 'typo', 'handler': 'routes/s.py:a', 'door': 'devise'},
            {'path': 'open', 'handler': 'routes/s.py:a', 'door': 'none'},
        ])
        assert set(plugin_loader._sockets['sk3']) == {'plain', 'dev'}
        assert plugin_loader.get_socket_door('sk3', 'dev') == 'device'
        assert plugin_loader.get_socket_door('sk3', 'plain') is None
        plugin_loader._unregister_sockets('sk3')
        assert plugin_loader.get_socket_door('sk3', 'dev') is None
    finally:
        plugin_loader._sockets.pop('sk3', None)
        plugin_loader._socket_doors.pop('sk3', None)


@pytest.mark.parametrize('undo', ['disable', 'remove', 'rekey', 'hosted'])
def test_an_open_device_socket_closes_when_its_device_is_no_longer_welcome(device_door, monkeypatch, undo):
    from core.devices import door
    monkeypatch.setattr(door, 'RECHECK', 0.05)
    fake = device_door.fake
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=_proof()) as ws:
        assert ws.receive_json() == {'device': 'desk'}
        if undo == 'disable':
            fake.table['desk']['enabled'] = False
        elif undo == 'remove':
            fake.table.pop('desk')
        elif undo == 'rekey':
            fake.keys['desk'] = 'newkeyid.' + KEY              # a new pairing code was pasted in
        else:
            fake.why = "Devices are not available on hosted Sapphire."
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401
    assert not plugin_loader._live_sockets.get('sk')


def test_an_open_device_socket_stays_open_while_its_device_is_welcome(device_door, monkeypatch):
    from core.devices import door
    monkeypatch.setattr(door, 'RECHECK', 0.02)
    with device_door.websocket_connect('/ws/plugin/sk/pipe', subprotocols=_proof()) as ws:
        ws.receive_json()
        _time.sleep(0.3)                                       # a dozen looks
        ws.send_text('still here')                             # the handler is alive to take it
    assert device_door.fake.lookups > 5
