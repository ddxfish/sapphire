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
