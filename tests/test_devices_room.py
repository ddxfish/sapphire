# tests/test_devices_room.py - the room door: a board pushes pieces of its room (capture.c, firmware 0.7.6) to
# voice.room, which hands them to whoever collects (voice.room_sink) and tells the board to stop when nobody does.
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.devices import voice
from core.devices.drivers import satellite as sat
from core.routes import devices as routes
from test_devices_voice import home, _request   # noqa: F401  (the fixture rides along)


def test_nobody_collecting_tells_the_board_to_stop(home):
    with patch.object(voice, 'room_sink', None):
        out = voice.room('pi2', b'RIFF' * 100, {})
    assert out == {'ok': True, 'accepted': False, 'error': 'Nobody is collecting room sound from this device.'}


def test_a_collector_gets_the_piece_and_its_fires(home):
    got = []
    with patch.object(voice, 'room_sink', lambda d, a, m: got.append((d, a, m)) or {'ok': True, 'accepted': True}):
        out = voice.room('pi2', b'x' * 10, {'chunk': 2, 'fires': [[1.5, 0.99]]})
        assert out['accepted'] is True
        assert got[0][0] == 'pi2' and got[0][2]['fires'] == [[1.5, 0.99]]
        assert voice.room('nobody', b'x', {})['ok'] is False
        assert voice.room('pi2', b'', {})['ok'] is False
        assert voice.room('pi2', b'x' * (voice.MAX_AUDIO + 1), {})['ok'] is False


def test_the_route_reads_the_boards_headers(home):
    got = []
    req = _request(key='voice-key-12345678', body=b'RIFFfake', kind='audio/wav')
    req.headers.update({'x-capture': '184233', 'x-chunk': '7', 'x-fires': '[[32000,991],[48000,"no"]]',
                        'x-fire-count': '3', 'x-test': '1', 'x-model': 'hey_marcus'})
    with patch.object(voice, 'room_sink', lambda d, a, m: got.append((d, a, m)) or {'ok': True, 'accepted': True}):
        out = asyncio.run(routes.devices_room('pi2', req))
    assert out['accepted'] is True
    d, a, m = got[0]
    assert d == 'pi2' and a == b'RIFFfake'
    assert m['capture'] == '184233' and m['chunk'] == 7 and m['fire_count'] == 3 and m['test'] is True and m['model'] == 'hey_marcus'
    assert m['fires'] == [[2.0, 0.991]]           # the broken stamp is left out; offsets become seconds, scores probabilities


def test_the_driver_asks_the_board_to_capture_and_to_stop():
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url))
        return SimpleNamespace(status_code=200, content=b'', json=lambda: {'ok': True, 'capture': {'active': method == 'POST', 'sent': 0}})
    dev, cfg, sec = {'id': 'den'}, {'url': 'http://192.168.1.100'}, {'token': 'k'}
    with patch.object(sat.net, 'request', fake):
        assert sat.capture(dev, cfg, sec, 36000, 30, True)['active'] is True
        assert sat.capture(dev, cfg, sec, 0)['active'] is False
        assert sat.capture_state(dev, cfg, sec)['active'] is False
    assert calls[0] == ('POST', 'http://192.168.1.100/capture?seconds=36000&chunk=30&test=true')
    assert calls[1] == ('DELETE', 'http://192.168.1.100/capture')
    assert calls[2] == ('GET', 'http://192.168.1.100/capture')


def test_a_capturing_board_reads_so_on_the_devices_page():
    h = {'name': 'den', 'board': 'x', 'has': ['mic'], 'capture': {'active': True, 'sent': 12, 'fires': 1, 'test': True}}
    reply = SimpleNamespace(status_code=200, content=b'', json=lambda: h)
    sat._about.clear()
    with patch.object(sat.net, 'request', lambda method, url, **kw: reply):
        st = sat.status({'id': 'den', 'label': 'Den', 'location': ''}, {'url': 'http://192.168.1.100'}, {'token': 'k'})
    sat._about.clear()
    assert st['readings']['capturing'] == 'room to Sapphire: 12 pieces, 1 wake fires, test mode'
