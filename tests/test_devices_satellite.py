# tests/test_devices_satellite.py - the satellite driver (core/devices/drivers).
# The network is faked: every reply below is the shape the real Pi body
# returned on 2026-09-27. No satellite, no speech engine, no sound.
import importlib
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from core.devices.drivers import satellite as sat
from core.devices import voice

DEV = {'id': 'pi2', 'label': 'Kitchen', 'location': 'Kitchen'}
CFG = {'url': 'http://192.168.1.100:8090', 'camera': True, 'chat': ''}


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)


KEY = Secrets(token='body-key-abcdefgh')

REAL = {
    ('GET', '/health'): {"ok": True, "body_name": "sapphire-pi2", "temp_c": 39.4, "uptime_s": 7500.4,
                         "led": {"state": "idle", "animation": "heartbeat-step", "blackout": False},
                         "wakeword": {"running": True, "enabled": True, "model": "hey_sapphire"},
                         "brain_events": {"connected": True}},
    ('GET', '/sounds'): {"sounds": [{"name": "ping", "ext": ".wav", "bytes": 70604}], "count": 1},
    ('GET', '/wakeword'): {"running": True, "enabled": True, "model": "hey_sapphire", "threshold": 0.5},
    ('GET', '/sensors'): {"sensors": {"light": 1234, "temp_c": 23.5}},
    ('GET', '/led/spec'): {"colors": ["cyan", "sapphire", "soft_blue", "off"], "color_aliases": ["red", "blue"],
                           "animations": {"solid": "no motion", "blink": "on and off", "heartbeat": "ba-bump",
                                          "breathe": "rise and fall"},
                           "baseline_now": {"color": [90, 140, 220], "animation": "heartbeat", "bpm": 33},
                           "is_night": True},
    ('POST', '/led'): {"state": "custom", "animation": "blink"},
    ('GET', '/led/baseline'): {"ok": True, "baseline": {"color": [90, 140, 220], "animation": "heartbeat",
                                                        "floor": 0.05, "ceiling": 0.17, "bpm": 33}},
    ('PUT', '/led/baseline'): {"ok": True, "baseline": {"color": [255, 140, 0], "animation": "heartbeat",
                                                        "floor": 0.05, "ceiling": 0.17, "bpm": 40}},
    ('POST', '/power'): {"ok": True, "action": "restart", "in_s": 3.0},
    ('GET', '/camera/snap'): {"ok": True, "width": 1296, "height": 972, "format": "jpeg",
                              "bytes": 12, "data_b64": "/9j/4AAQSkZJRg=="},
}


class Reply:
    def __init__(self, data=None, status=200, content=b''):
        self._data, self.status_code, self.content = data, status, content

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data


@pytest.fixture
def pi():
    calls = []

    def fake(method, url, **kw):
        path = url.replace(CFG['url'], '')
        calls.append(SimpleNamespace(method=method, path=path, kw=kw))
        if path.startswith('/audio/listen'):
            return Reply(content=b'RIFFfake-wav')
        return Reply(REAL.get((method, path.split('?')[0]), {"ok": True}))

    sat._about.clear()
    sat._palettes.clear()
    from core.devices import glass
    with patch.object(sat.net, 'request', fake), patch.object(glass, 'sync_soon', lambda d: None):
        yield calls                 # no screen-sync thread: it looks the name up in the real device store
    sat._about.clear()
    sat._palettes.clear()


run = lambda cap, action, value='', cfg=CFG, key=KEY: sat.run(DEV, cap, action, value, cfg, key, None)


# --- the declaration -----------------------------------------------------------

def test_spec_registers_as_a_core_driver():
    import core.devices.registry as reg
    importlib.reload(reg)
    assert reg.register_driver('satellite', sat.SPEC, 'core', builtin=True)
    spec = reg.get_driver('satellite')
    assert spec['capabilities'] == ['speaker', 'mic', 'light', 'wake', 'camera', 'power', 'storage', 'screen', 'keyboard',
                                    'buttons', 'sensors', 'firmware', 'ir', 'picker']
    sat._about[DEV['id']] = (0, {'has': ['firmware']})      # describe() offers firmware only to a board that says it
    try:
        assert sorted(spec['capabilities']) == sorted(sat.describe(DEV, CFG))
    finally:
        sat._about.pop(DEV['id'], None)
    assert spec['locked_by_default'] == []                    # she may restart a satellite
    assert [f['key'] for f in spec['config_schema'] if f.get('secret')] == ['token', 'voice_key']
    assert spec['learns_address'] is True                     # it calls in: its address is learned
    importlib.reload(reg)


def test_validate():
    ok = lambda url: sat.validate({'url': url})
    assert ok('192.168.1.100:8090/')[0]['url'] == 'http://192.168.1.100:8090'
    assert ok('http://sapphire-pi:8090')[1] == '' and ok('https://pi.local:8090')[1] == ''
    assert ok('')[1] == '' and ok('')[0]['url'] == ''        # learned when it calls in (engine.learned)
    assert 'own network' in ok('http://8.8.8.8:8090')[1]
    assert 'own network' in ok('http://pi.example.com')[1]
    assert 'not a usable address' in ok('ftp://192.168.0.2')[1]


# --- every request carries the key, and nothing else does ----------------------

def test_the_key_rides_the_header_never_the_address(pi):
    run('wake', 'read')
    call = pi[0]
    assert call.kw['headers'] == {'Authorization': 'Bearer body-key-abcdefgh', 'Connection': 'close'}
    assert 'body-key' not in call.path
    text, ok = run('wake', 'read', key=Secrets())
    assert not ok and text.startswith('No key is stored') and len(pi) == 1


def test_status_readings(pi):
    st = sat.status(DEV, CFG, KEY)
    assert st == {'online': True, 'detail': 'sapphire-pi2 at 192.168.1.100:8090',
                  'readings': {'running for': '2h 5m', 'temperature': '39.4C', 'wake word': 'listening for hey_sapphire',
                               'light': 'idle, heartbeat-step', 'link to Sapphire': 'connected'}}


# --- speaker -------------------------------------------------------------------

def test_say_renders_her_voice_and_sends_it(pi):
    with patch.object(voice, 'render', return_value=(b'OggS-audio', 'audio/ogg')) as render:
        assert run('speaker', 'say', ' Dinner is ready ') == ('Said there: "Dinner is ready"', True)
    render.assert_called_once_with('Dinner is ready')
    assert [(c.method, c.path) for c in pi] == [('GET', '/health'), ('POST', '/audio/speak')]
    call = pi[1]
    assert call.kw['files'] == {'audio': ('speech.ogg', b'OggS-audio', 'audio/ogg')}
    assert call.kw['timeout'] == sat.SPEAK_WAIT


def test_a_refused_voice_is_said_plainly_and_nothing_is_sent(pi):
    with patch.object(voice, 'render', return_value=(None, 'This chat is private and the voice engine is a cloud one.')):
        text, ok = run('speaker', 'say', 'a secret')
    assert not ok and text == 'Nothing was said: This chat is private and the voice engine is a cloud one.'
    assert pi == []
    assert run('speaker', 'say', '  ')[0].startswith('say: the value is what to say')


def test_sound(pi):
    assert run('speaker', 'sound') == ('Sounds: ping', True)
    assert run('speaker', 'sound', 'ping') == ('Played ping.', True)
    assert pi[1].path == '/audio/effect?name=ping'
    text, ok = run('speaker', 'sound', 'ping&x=../../etc')
    assert not ok and 'not a usable sound name' in text and len(pi) == 2


# --- mic -----------------------------------------------------------------------

def test_listen_transcribes_what_the_room_said(pi):
    with patch.object(voice, 'stt_refusal', return_value=''), \
         patch.object(voice, 'transcribe', return_value=('what time is it', '')) as stt:
        assert run('mic', 'listen', '10') == ('Heard: "what time is it"', True)
    stt.assert_called_once_with(b'RIFFfake-wav')
    heard = [c for c in pi if c.path.startswith('/audio/listen')]
    assert heard[0].path == '/audio/listen?vad=true&max_seconds=10' and heard[0].kw['timeout'] == 22
    with patch.object(voice, 'stt_refusal', return_value=''), \
         patch.object(voice, 'transcribe', return_value=('', '')):
        assert run('mic', 'listen') == ('Listened, and heard no speech.', True)
    assert [c for c in pi if c.path.startswith('/audio/listen')][1].path.endswith('max_seconds=15')
    with patch.object(voice, 'stt_refusal', return_value=''), \
         patch.object(voice, 'transcribe', return_value=('', '')):
        run('mic', 'listen', '9999')
    assert [c for c in pi if c.path.startswith('/audio/listen')][2].path.endswith('max_seconds=60')


def test_listen_waits_out_what_the_board_still_has_to_say(pi):
    REAL[('GET', '/health')] = {**REAL.get(('GET', '/health'), {}), 'sound': {'queued_kb': 625}}   # 20 s queued at 32 KB/s
    try:
        with patch.object(voice, 'stt_refusal', return_value=''), \
             patch.object(voice, 'transcribe', return_value=('', '')):
            run('mic', 'listen', '10')
    finally:
        REAL[('GET', '/health')].pop('sound')
    assert [c for c in pi if c.path.startswith('/audio/listen')][0].kw['timeout'] == 42


def test_a_private_chat_never_opens_the_mic(pi):
    with patch.object(voice, 'stt_refusal', return_value='this chat is private and speech recognition is a cloud one'):
        text, ok = run('mic', 'listen', '10')
    assert not ok and text.startswith('Not listening:') and pi == []


# --- light ---------------------------------------------------------------------

def test_light_words():
    p = sat.parse_light
    assert p('cyan blink 5s') == {'color': 'cyan', 'animation': 'blink', 'duration_s': 5.0}
    assert p('red') == {'color': 'red'}
    assert p('2m #ff8800 pulse') == {'r': 255, 'g': 136, 'b': 0, 'animation': 'pulse', 'duration_s': 120.0}
    assert p('#f80') == {'r': 255, 'g': 136, 'b': 0}
    assert p('') == {}
    assert p('soft blue breathe 10s') == {'color': 'soft_blue', 'animation': 'breathe', 'duration_s': 10.0}
    with pytest.raises(sat.Problem, match="did not understand 'fast'"):
        p('soft blue blink fast')
    with pytest.raises(sat.Problem, match="did not understand 'fast'"):
        p('#f80 blink fast')


def test_light_words_against_the_devices_own_palette():
    """With the palette, every word is checked and the wrong one is named with
    the choices (Sapphire burned seven calls on 'soft blue breathe', 2026-10-03)."""
    pal = {'colors': ['cyan', 'soft_blue', 'red'], 'animations': ['solid', 'blink', 'breathe']}
    p = lambda v: sat.parse_light(v, pal)
    assert p('soft blue breathe 10s') == {'color': 'soft_blue', 'animation': 'breathe', 'duration_s': 10.0}
    assert p('breathe soft blue') == {'color': 'soft_blue', 'animation': 'breathe'}      # any order
    assert p('soft blue') == {'color': 'soft_blue'}
    assert p('#f80 blink') == {'r': 255, 'g': 136, 'b': 0, 'animation': 'blink'}
    with pytest.raises(sat.Problem, match="did not understand 'soft blue breath'. Colors: cyan, soft_blue, red, "
                                          "or #hex like #ff8800. Animations: solid, blink, breathe."):
        p('soft blue breath')
    with pytest.raises(sat.Problem, match="did not understand 'teal'"):
        p('teal blink')
    with pytest.raises(sat.Problem, match="One animation at a time, not blink and breathe"):
        p('red blink breathe')
    with pytest.raises(sat.Problem, match="the color is already given as hex"):
        p('#f80 red blink')


def test_the_palette_is_asked_once_an_hour(pi):
    assert run('light', 'set', 'soft blue breathe 10s')[1]
    assert run('light', 'set', 'cyan blink 5s')[1]
    assert [c.path for c in pi] == ['/led/spec', '/led', '/led']
    assert pi[1].kw['json'] == {'color': 'soft_blue', 'animation': 'breathe', 'duration_s': 10.0}
    told, ok = run('light', 'set', 'soft blue breath')
    assert not ok and told.startswith("I did not understand 'soft blue breath'. Colors: cyan, sapphire, soft_blue")


def test_light_fine_settings():
    p = sat.parse_light
    assert p('amber heartbeat bpm=40 speed=FAST floor=0.05 ceiling=0.4') == {
        'color': 'amber', 'animation': 'heartbeat', 'bpm': 40, 'speed': 'fast', 'floor': 0.05, 'ceiling': 0.4}
    assert p('bpm=900 ceiling=7 floor=-1') == {'bpm': 120, 'ceiling': 1.0, 'floor': 0.0}    # held in range
    with pytest.raises(sat.Problem, match="bpm has to be a number, not 'quick'"):
        p('bpm=quick')
    with pytest.raises(sat.Problem, match="speed is one of slow, normal, fast"):
        p('speed=warp')


def test_light_with_no_time_holds_five_minutes(pi):
    """A ring set with no time would stay forever. It is given five minutes."""
    assert run('light', 'set', 'red') == ('Ring: red, blink, 300s.', True)
    assert pi[-1].kw['json'] == {'color': 'red', 'animation': 'solid', 'duration_s': 300.0}


def test_resting_light(pi):
    assert run('light', 'rest') == ('Its resting light is: #5a8cdc heartbeat bpm=33 floor=0.05 ceiling=0.17. '
                                    'Nothing was changed. To change it, give a value. '
                                    'Example: sapphire heartbeat bpm=33', True)
    assert (pi[0].method, pi[0].path) == ('GET', '/led/baseline')
    text, ok = run('light', 'rest', '#ff8c00 bpm=40')
    assert ok and text == ('Resting light is now: #ff8c00 heartbeat bpm=40 floor=0.05 ceiling=0.17. '
                           'Kept after a restart.')
    put = [c for c in pi if c.method == 'PUT']
    assert [(c.method, c.path) for c in pi] == [('GET', '/led/baseline'), ('GET', '/led/spec'), ('PUT', '/led/baseline')]
    assert put[0].kw['json'] == {'r': 255, 'g': 140, 'b': 0, 'bpm': 40}      # only what she named
    for wrong in ('cyan 5m', 'cyan speed=fast'):
        text, ok = run('light', 'rest', wrong)
        assert not ok and text.startswith('A resting light has no time and no speed.')
    assert len(pi) == 3
    assert run('light', 'clear') == ('Back to its resting light.', True)
    assert pi[-1].kw['json'] == {'state': 'idle'}


# --- camera --------------------------------------------------------------------

def test_look(pi):
    out, ok = run('camera', 'look')
    assert ok and out == {'text': 'A picture from the camera of pi2 in Kitchen, 1296x972.',
                          'images': [{'data': '/9j/4AAQSkZJRg==', 'media_type': 'image/jpeg'}]}
    assert (pi[0].method, pi[0].path) == ('GET', '/camera/snap?b64=true')
    assert pi[0].kw['timeout'] == sat.LOOK_WAIT


def test_a_satellite_without_a_camera(pi):
    blind = dict(CFG, camera=False)
    assert 'camera' not in sat.describe(DEV, blind)
    assert run('camera', 'look', cfg=blind) == ('This satellite has no camera.', False)
    assert pi == []                                           # it was never asked
    with patch.dict(REAL, {('GET', '/camera/snap'): {"ok": True}}):
        assert run('camera', 'look') == ('The camera gave no picture.', False)


def test_light_set_off_options(pi):
    assert run('light', 'set', 'cyan blink 5s') == ('Ring: cyan, blink, 5s.', True)
    assert [c.path for c in pi] == ['/led/spec', '/led']              # its palette first, once
    assert pi[1].kw['json'] == {'color': 'cyan', 'animation': 'blink', 'duration_s': 5.0}
    assert run('light', 'off') == ('Ring dark.', True)
    assert pi[2].kw['json'] == {'state': 'off'}
    text, ok = run('light', 'options')
    assert ok and text == (
        'Colors: cyan, sapphire, soft_blue, off, red, blue. Any hex color works too, like #ff8800.\n'
        'Animations:\n'
        '  solid: no motion\n'
        '  blink: on and off\n'
        '  heartbeat: ba-bump\n'
        '  breathe: rise and fall\n'
        'Fine settings, written name=value: bpm=10 to 120, speed=slow, normal or fast, '
        'floor and ceiling=0 to 1 (lowest and highest brightness).\n'
        'Its resting light is: #5a8cdc heartbeat bpm=33. It is night there, so the night light shows.')
    assert 'Example: cyan blink 5s' in run('light', 'set', '')[0] and len(pi) == 4


# --- wake ----------------------------------------------------------------------

def test_wake_word(pi):
    assert run('wake', 'read') == ('It is listening for hey_sapphire.', True)
    assert run('wake', 'off') == ('No longer listening for the wake word.', True)
    assert run('wake', 'on') == ('Listening for the wake word.', True)
    assert [c.path for c in pi] == ['/wakeword', '/wakeword?enabled=false', '/wakeword?enabled=true']


# --- when it goes wrong --------------------------------------------------------

def test_every_example_in_the_help_really_runs(pi):
    with patch.object(voice, 'render', return_value=(b'OggS', 'audio/ogg')), \
         patch.object(voice, 'stt_refusal', return_value=''), \
         patch.object(voice, 'transcribe', return_value=('hi', '')), \
         patch.object(voice, 'pressed', return_value={'ok': True, 'accepted': False}), \
         patch.object(sat, '_update', lambda d, c, s, v: ('over the air: tests/test_devices_flash.py', True)), \
         patch.object(sat, '_check', lambda d, c, s: ('the source: tests/test_devices_flash.py', True)):
        for cap, info in sat.describe(DEV, CFG).items():
            for action, a in info['actions'].items():
                told, ok = run(cap, action, a['example'])
                assert ok, (cap, action, told)


def test_problems_read_like_sentences():
    def answer(reply=None, error=None):
        def fake(method, url, **kw):
            if error:
                raise error
            return reply
        return patch.object(sat.net, 'request', fake)

    with answer(error=requests.exceptions.ConnectionError('boom body-key-abcdefgh')):
        st = sat.status(DEV, CFG, KEY)
        assert st == {'online': False, 'detail': 'Could not reach 192.168.1.100:8090. '
                                                 'Check that it is powered and on the network.'}
    with answer(error=requests.exceptions.Timeout()):
        assert run('wake', 'read') == ('No answer from 192.168.1.100:8090 within 8s.', False)
    with answer(Reply({"detail": "bad token"}, 401)):
        assert 'refused the key' in run('wake', 'read')[0]
    with answer(Reply({"detail": "mic busy (wakeword post-fire or concurrent listen)"}, 503)), \
         patch.object(voice, 'stt_refusal', return_value=''):
        assert run('mic', 'listen') == ('mic busy (wakeword post-fire or concurrent listen)', False)
    with answer(Reply(None, 500)):
        assert run('light', 'off') == ('The satellite answered HTTP 500.', False)
    assert run('speaker', 'explode') == ('A satellite has no speaker / explode.', False)


def test_a_length_of_time_reads_short():
    assert [sat._span(n) for n in (0, 40, 125, 7500, 273600, -5)] == ['0s', '40s', '2m 5s', '2h 5m', '3d 4h', '0s']


# --- power ---------------------------------------------------------------------

def test_restart_and_shutdown(pi):
    assert run('power', 'restart') == ('pi2 is restarting in 3 seconds. It will be back in about a minute.', True)
    assert (pi[0].method, pi[0].path) == ('POST', '/power?action=restart')
    text, ok = run('power', 'shutdown')
    assert ok and text == ('pi2 is shutting down in 3 seconds. To bring it back, someone has to '
                           'unplug its power and plug it in again.')
    assert pi[1].path == '/power?action=shutdown'
    assert run('power', 'explode') == ('A satellite has no power / explode.', False) and len(pi) == 2


def test_a_satellite_too_old_for_power():
    with patch.object(sat.net, 'request', lambda m, u, **kw: Reply({"detail": "Not Found"}, status=404)):
        assert run('power', 'restart') == ("This satellite's program is too old to restart or shut down. "
                                           "Update it.", False)


# --- a board that says what it is -------------------------------------------------

BOARD = {"ok": True, "name": "den", "board": "waveshare-s3-audio", "firmware": "0.1.0",
         "has": ["speaker", "mic", "wake"], "uptime_s": 61, "volume": 85,
         "plays": {"type": "audio/wav", "rate": 16000, "channels": 1},
         "wakeword": {"enabled": True, "running": True, "model": "hey_sapphire"},
         "link": {"connected": True}}


@pytest.fixture
def board(pi):
    with patch.dict(REAL, {('GET', '/health'): BOARD}):
        yield pi


def test_a_board_says_what_it_has_and_the_engine_is_told(board):
    st = sat.status(DEV, CFG, KEY)
    assert st['has'] == ['speaker', 'mic', 'wake']
    assert st['detail'] == 'den at 192.168.1.100:8090'
    assert st['readings'] == {'program': 'waveshare-s3-audio 0.1.0', 'volume': '85%', 'running for': '1m 1s',
                              'wake word': 'listening for hey_sapphire', 'link to Sapphire': 'connected',
                              'updates': 'over USB only: one program slot'}       # it said `has` without firmware


def test_volume_read_set_and_step(board):
    with patch.dict(REAL, {('GET', '/volume'): {"volume": 60}, ('POST', '/volume'): {"volume": 70}}):
        assert run('speaker', 'volume') == ('Volume is 60%.', True)
        assert run('speaker', 'volume', '80') == ('Volume is now 70%.', True)      # what the board says it is
        assert board[-1].path == '/volume?level=80'
        assert run('speaker', 'volume', 'up') == ('Volume is now 70%.', True)
        assert board[-1].path == '/volume?level=70'                               # 60 + 10
        assert run('speaker', 'volume', 'DOWN')[1] and board[-1].path == '/volume?level=50'
        assert run('speaker', 'volume', '250')[1] and board[-1].path == '/volume?level=100'
        text, ok = run('speaker', 'volume', 'loud')
    assert not ok and text.startswith('volume: a number from 0 to 100')


def test_an_early_pi_has_no_volume_door(pi):
    def old(method, url, **kw):
        return Reply({"detail": "Not Found"}, status=404)
    with patch.object(sat.net, 'request', old):
        assert run('speaker', 'volume', '80') == ("This satellite's program has no volume control.", False)


def test_an_early_pi_says_nothing_and_nothing_is_passed_on(pi):
    assert 'has' not in sat.status(DEV, CFG, KEY)


def test_a_board_gets_her_voice_in_the_format_it_stated(board):
    with patch.object(voice, 'render', return_value=(b'OggS-audio', 'audio/ogg')), \
         patch.object(voice, 'fit', return_value=(b'RIFF-fitted', 'audio/wav')) as fit:
        assert run('speaker', 'say', 'Dinner is ready') == ('Said there: "Dinner is ready"', True)
    fit.assert_called_once_with(b'OggS-audio', 'audio/ogg', BOARD['plays'])
    call = board[-1]
    assert (call.method, call.path) == ('POST', '/audio/speak')
    assert call.kw['data'] == b'RIFF-fitted' and 'files' not in call.kw        # the sound itself, no form
    assert call.kw['headers'] == {'Content-Type': 'audio/wav', 'Authorization': 'Bearer body-key-abcdefgh', 'Connection': 'close'}


def test_a_format_that_cannot_be_made_is_said_and_nothing_is_sent(board):
    odd = dict(BOARD, plays={"type": "audio/opus", "rate": 16000})
    with patch.dict(REAL, {('GET', '/health'): odd}), \
         patch.object(voice, 'render', return_value=(b'OggS-audio', 'audio/ogg')):
        text, ok = run('speaker', 'say', 'Dinner is ready')
    assert not ok and text == 'Nothing was said: This device stated a sound format I cannot make.'
    assert [c.path for c in board] == ['/health']


def test_it_is_asked_what_it_plays_once_a_minute_not_at_every_sentence(board):
    with patch.object(voice, 'render', return_value=(b'RIFF', 'audio/wav')), \
         patch.object(voice, 'fit', return_value=(b'RIFF', 'audio/wav')):
        for _ in range(3):
            run('speaker', 'say', 'Dinner is ready')
        assert [c.path for c in board].count('/health') == 1
        with patch.object(sat.time, 'monotonic', return_value=sat.time.monotonic() + sat.ABOUT_FRESH + 1):
            run('speaker', 'say', 'Dinner is ready')
    assert [c.path for c in board].count('/health') == 2


def test_a_look_at_its_health_is_always_fresh_and_a_silent_board_is_forgotten(board):
    sat.status(DEV, CFG, KEY)
    sat.status(DEV, CFG, KEY)
    assert [c.path for c in board] == ['/health', '/health']
    assert DEV['id'] in sat._about

    def dead(method, url, **kw):
        raise requests.exceptions.ConnectionError('gone')
    with patch.object(sat.net, 'request', dead):
        assert sat.status(DEV, CFG, KEY)['online'] is False
        assert DEV['id'] not in sat._about
        with patch.object(voice, 'render', return_value=(b'RIFF', 'audio/wav')):
            text, ok = run('speaker', 'say', 'Dinner is ready')
    assert not ok and text.startswith('Could not reach')


def test_play_is_the_door_core_speaks_through_as_her_voice_is_made(board):
    """One sentence at a time: fitted for a board that states its format, the
    file as made for one that does not, and the board's answer comes back."""
    with patch.object(voice, 'fit', return_value=(b'RIFF-fitted', 'audio/wav')):
        assert sat.play(b'OggS-one', 'audio/ogg', DEV, CFG, KEY) == {'ok': True, 'seconds': 0.0}   # fitted: it knows how long
    assert board[-1].kw['data'] == b'RIFF-fitted'
    older = {k: v for k, v in BOARD.items() if k != 'plays'}
    with patch.dict(REAL, {('GET', '/health'): older, ('POST', '/audio/speak'): {'ok': True, 'stopped': True}}):
        sat._about.clear()
        assert sat.play(b'OggS-two', 'audio/ogg', DEV, CFG, KEY) == {'ok': True, 'stopped': True}
    assert board[-1].kw['files'] == {'audio': ('speech.ogg', b'OggS-two', 'audio/ogg')}
    odd = dict(BOARD, plays={"type": "audio/opus", "rate": 16000})
    with patch.dict(REAL, {('GET', '/health'): odd}), pytest.raises(sat.Unfit):
        sat._about.clear()
        sat.play(b'OggS-three', 'audio/ogg', DEV, CFG, KEY)


def test_a_board_stating_its_format_loosely_still_gets_its_seconds(board):
    """A Pi says rate "16000" and no channels: wanted() forgives that, so
    the arithmetic after fit() must too (it once did string maths)."""
    loose = dict(BOARD, plays={"type": "audio/wav", "rate": "16000"})
    with patch.dict(REAL, {('GET', '/health'): loose}), \
         patch.object(voice, 'fit', return_value=(b'\0' * (44 + 32000), 'audio/wav')):
        sat._about.clear()
        assert sat.play(b'OggS', 'audio/ogg', DEV, CFG, KEY) == {'ok': True, 'seconds': 1.0}


def test_setup_is_what_a_satellite_needs_before_it_can_work():
    """Add Device asks for these and nothing else; the rest keep their defaults."""
    assert [f['key'] for f in sat.SPEC['config_schema'] if f.get('setup')] == ['url', 'token', 'voice_key']


def test_the_camera_switch_belongs_to_the_camera():
    field = next(f for f in sat.SPEC['config_schema'] if f['key'] == 'camera')
    assert field['capability'] == 'camera' and field['tab'] == 'Status'


# --- the looks and the hours ---------------------------------------------------------

LOOKS = {'look_resting': 'sapphire heartbeat bpm=33 ceiling=0.1', 'look_listening': 'yellow spin',
         'look_thinking': 'rainbow spin', 'look_tool': 'purple pulse', 'look_speaking': 'cyan solid',
         'look_nolink': 'red pulse', 'look_night': 'off'}


def test_the_looks_are_checked_at_save():
    good = dict(CFG, **LOOKS, lights_from='07:00', lights_until='23:00')
    assert sat.validate(dict(good))[1] == ''
    assert 'Listening' in sat.validate(dict(good, look_listening='yellow spin fast wobble'))[1]
    assert 'has no time' in sat.validate(dict(good, look_thinking='rainbow spin 5s'))[1]
    assert 'Night' in sat.validate(dict(good, look_night='sapphire pulse 2h'))[1]
    assert sat.validate(dict(good, look_tool='purple pulse 3s'))[1].startswith('Tool:')
    assert 'clock times' in sat.validate(dict(good, lights_from='7pm'))[1]
    assert sat.validate(dict(good, lights_from='', lights_until=''))[1] == ''
    assert sat.validate(dict(good, look_night='sapphire pulse ceiling=0.05'))[1] == ''


def test_a_new_satellite_rests_dark_from_midnight_to_eight():
    """One truth with the boards' built-in: the page starts where they start."""
    by = {f['key']: f for f in sat.SPEC['config_schema']}
    assert (by['lights_from']['default'], by['lights_until']['default']) == ('08:00', '00:00')
    assert by['look_night']['default'] == 'off'


def test_a_save_hands_the_looks_and_hours_to_the_board(board):
    with patch.dict(REAL, {('PUT', '/led/looks'): {"ok": True}}):
        sat.apply(DEV, dict(CFG, **LOOKS, lights_from='07:00', lights_until='23:00'), KEY)
    call = board[-1]
    assert (call.method, call.path) == ('PUT', '/led/looks')
    assert call.kw['json'] == {
        'resting': {'color': 'sapphire', 'animation': 'heartbeat', 'bpm': 33, 'ceiling': 0.1},
        'listening': {'color': 'yellow', 'animation': 'spin'},
        'thinking': {'color': 'rainbow', 'animation': 'spin'},
        'tool': {'color': 'purple', 'animation': 'pulse'},
        'speaking': {'color': 'cyan', 'animation': 'solid'},
        'nolink': {'color': 'red', 'animation': 'pulse'},
        'night': {'color': 'off'},
        'from': '07:00', 'until': '23:00'}


def test_an_early_pi_keeps_its_own_looks_and_that_is_no_fault(pi):
    def old(method, url, **kw):
        return Reply({"detail": "Not Found"}, status=404)
    with patch.object(sat.net, 'request', old):
        assert sat.apply(DEV, dict(CFG, **LOOKS), KEY) is None


def test_a_board_that_refuses_a_look_is_heard_at_save(board):
    from core.devices.engine import DeviceError
    def refuse(method, url, **kw):
        return Reply({"detail": "the thinking look names a color this ring does not know"}, status=400)
    with patch.object(sat.net, 'request', refuse), pytest.raises(DeviceError, match='thinking look'):
        sat.apply(DEV, dict(CFG, **LOOKS), KEY)


def test_sensors_read_and_readings(pi):
    told, ok = run('sensors', 'read')
    assert ok and told == 'light: 1234 · temp_c: 23.5'
    assert pi[-1].path == '/sensors'
    with patch.dict(REAL, {('GET', '/health'): dict(REAL[('GET', '/health')], sensors={"light": 1234})}):
        st = sat.status(DEV, CFG, KEY)
    assert st['readings']['light'] == '1234'


# --- 0.7.0: a turned camera, the volume step ------------------------------------

def test_a_turned_camera_is_set_upright(pi):
    import base64, io
    from PIL import Image
    wide = io.BytesIO()
    Image.new('RGB', (40, 20), 'red').save(wide, 'JPEG')
    shot = {"ok": True, "width": 40, "height": 20, "data_b64": base64.b64encode(wide.getvalue()).decode()}
    with patch.dict(REAL, {('GET', '/camera/snap'): shot}):
        out, ok = run('camera', 'look', cfg=dict(CFG, camera_turn='90'))
        assert ok and out['text'].endswith('20x40.')
        assert Image.open(io.BytesIO(base64.b64decode(out['images'][0]['data']))).size == (20, 40)
        out, ok = run('camera', 'look', cfg=dict(CFG, camera_turn='180'))
        assert ok and out['text'].endswith('40x20.')
        out, ok = run('camera', 'look', cfg=dict(CFG, camera_turn='0'))
        assert out['images'][0]['data'] == shot['data_b64']          # untouched when not turned


def test_the_volume_step_is_the_devices_own(board):
    with patch.dict(REAL, {('GET', '/volume'): {"volume": 60}, ('POST', '/volume'): {"volume": 80}}):
        run('speaker', 'volume', 'up', cfg=dict(CFG, volume_step='20'))
        assert board[-1].path == '/volume?level=80'
        run('speaker', 'volume', 'down', cfg=dict(CFG, volume_step='5'))
        assert board[-1].path == '/volume?level=55'
        run('speaker', 'volume', 'up', cfg=dict(CFG, volume_step='loud'))   # not a number: ten
        assert board[-1].path == '/volume?level=70'


def test_say_is_refused_on_the_device_she_is_already_answering(board):
    """Her reply is spoken there as she writes it; a say for it was a tool call a stop could not keep."""
    from core.devices import voice
    with voice._lock:
        voice._running[DEV['id']] = ({'speech': SimpleNamespace(ready=True), 'thread': threading.get_ident()}, 'default')
    try:
        calls = len(board)
        out, ok = run('speaker', 'say', 'hello')
        assert not ok and 'Just answer' in out and len(board) == calls      # nothing was sent to the board
        token = voice._lane.set(True)                                       # the reply lane's own say goes through
        try:
            assert voice.answering(DEV['id']) is False
        finally:
            voice._lane.reset(token)
        seen = []                                                           # and so does anyone else's: a reminder, another chat
        t = threading.Thread(target=lambda: seen.append(voice.answering(DEV['id'])))
        t.start(); t.join()
        assert seen == [False]
    finally:
        with voice._lock:
            voice._running.pop(DEV['id'], None)


# --- the wake word it listens for (core/wakeword/library.py, 2026-10-19) ---------
# A board that states its model family (/health wakeword.format) takes a model from
# the library through PUT /wakeword/model and restarts on it. One owner sends it
# (send_wake_model): a save only a changed choice, a switch with force, the probe
# only what was never tried or failed. The library is a temp folder; the board is
# the fake above with the ESP32's health; the device store is a dict here.

TFL = b'\0\0\0\0TFL3' + b'\0' * 5000
ESP = dict(BOARD, has=["speaker", "mic", "wake", "firmware"], model="ai-speaker-mic", firmware="0.7.6", slot="ota_0",
           wakeword={"enabled": True, "running": True, "model": "hey_jarvis", "format": "tflite"})
ESP_WAKE = {"enabled": True, "running": True, "model": "hey_jarvis", "format": "tflite", "stored": False, "threshold": 0.97}
LIVE = {}      # the device row's wake_model as the store holds it now (what the probe reads live)


@pytest.fixture
def esp(pi, tmp_path, monkeypatch):
    from core.wakeword import library as lib
    from core.devices import engine
    monkeypatch.setattr(lib, 'USER_DIR', tmp_path / 'models')
    monkeypatch.setattr(lib, 'CORE_DIR', tmp_path / 'core')
    lib.add('hey_marcus', TFL, 'tflite', {'threshold': 0.9, 'micro': {'sliding_window_size': 6, 'feature_step_size': 10}})
    LIVE.clear()
    monkeypatch.setattr(engine, 'get', lambda d: {'id': d, 'parts': [{'driver': 'satellite', 'config': dict(CFG, **LIVE)}]})
    sat._pushed.clear(); sat._saving.clear()
    with patch.dict(REAL, {('GET', '/health'): ESP, ('PUT', '/led/looks'): {"ok": True}, ('GET', '/wakeword'): ESP_WAKE}):
        yield pi
    sat._pushed.clear(); sat._saving.clear(); LIVE.clear()


sent_models = lambda calls: [c for c in calls if c.path.startswith('/wakeword/model')]


def test_a_board_that_states_its_model_family_takes_a_wake_word():
    assert 'wake.model' in sat._takes(ESP) and 'wake.model' not in sat._takes(BOARD)
    told = sat.describe(dict(DEV, has=ESP['has'], takes=['wake.model']), CFG)
    assert told['wake']['actions']['install']['values'] == '<model name> | own'
    assert 'install' not in sat.describe(dict(DEV, has=BOARD['has'], takes=[]), CFG)['wake']['actions']
    assert sat.wake_format('pi2') == 'onnx'
    assert sat.wake_format('pi2', row={'fingerprint': 'aa:bb', 'parts': []}) == 'tflite'
    assert sat.wake_format('pi2', row={'parts': [{'driver': 'satellite', 'has': ['wake', 'firmware']}]}) == 'tflite'
    assert sat.wake_format('esp32-den') == 'tflite'
    assert sat.wake_format('pi2', health=ESP) == 'tflite'
    from core.devices import engine
    assert 'wake' in engine.LOCKABLE                                   # her install restarts the board: lockable
    from core.wakeword import library
    assert not engine.ID_RE.fullmatch(library.DESKTOP)                   # this computer's slot can never be a device's name


def test_options_are_what_it_has_its_own_word_and_the_librarys_models_of_its_family(esp):
    sat.status(DEV, CFG, KEY)                                        # its health is now known: tflite
    assert sat.wake_state('pi2') == {'model': 'hey_jarvis', 'format': 'tflite', 'enabled': True}
    assert sat.options(dict(DEV, has=ESP['has']), CFG, 'wake_model') == [
        {'value': 'keep', 'label': 'what it has now (hey_jarvis)'}, {'value': 'own', 'label': "the board's own word"},
        {'value': 'hey_marcus', 'label': 'hey marcus (hey_marcus)'}]
    assert sat.options(DEV, CFG, 'rotation') == []
    sat._about.clear()
    pi_health = {k: v for k, v in BOARD.items() if k != 'board'}    # a Pi: no board name, no format stated: onnx
    with patch.dict(REAL, {('GET', '/health'): pi_health}):
        sat.status(DEV, CFG, KEY)
    assert sat.options(dict(DEV, has=pi_health['has']), CFG, 'wake_model') == [
        {'value': 'keep', 'label': 'what it has now (hey_sapphire)'}, {'value': 'own', 'label': "the board's own word"}]
    sat._about.clear()
    with patch.dict(REAL, {('GET', '/health'): dict(ESP, wakeword=dict(ESP['wakeword'], format='micro'))}):
        sat.status(DEV, CFG, KEY)
    assert sat.wake_state('pi2')['format'] == ''                     # a family that is not one is not one


def test_a_save_sends_a_changed_wake_word_last_and_the_board_restarts_on_it(esp):
    sat.apply(DEV, dict(CFG, **LOOKS, wake_model='hey_marcus'), KEY)
    mine = [c for c in esp if c.kw['headers']['Authorization'] == 'Bearer ' + KEY['token']]
    sent = sent_models(mine)
    assert len(sent) == 1 and sent[0].method == 'PUT' and mine.index(sent[0]) == len(mine) - 1   # last of the save
    from urllib.parse import parse_qs
    q = {k: v[0] for k, v in parse_qs(sent[0].path.split('?', 1)[1]).items()}
    assert q == {'name': 'hey_marcus', 'threshold': '0.9', 'format': 'tflite', 'phrase': 'hey marcus',
                 'sliding_window': '6', 'step_ms': '10'}
    assert sent[0].kw['data'] == TFL and sent[0].kw['timeout'] == 180
    assert [c.path for c in mine if c.path == '/led/looks'] == ['/led/looks']     # the looks went first
    assert sat._pushed['pi2']['ok'] and sat._pushed['pi2']['ident'][0] == 'hey_marcus'
    assert 'pi2' not in sat._saving


def test_what_it_has_its_current_word_and_the_same_bytes_twice_send_nothing(esp):
    sat.apply(DEV, dict(CFG, **LOOKS), KEY)                              # never set here: keep
    sat.apply(DEV, dict(CFG, **LOOKS, wake_model=''), KEY)               # '' is keep too (an old row)
    sat.apply(DEV, dict(CFG, **LOOKS, wake_model='hey_jarvis'), KEY)     # what it listens for already
    assert not sent_models(esp)
    assert sat.send_wake_model(DEV, dict(CFG, wake_model='hey_marcus'), KEY) == 'sent'
    assert sat.send_wake_model(DEV, dict(CFG, wake_model='hey_marcus'), KEY) == 'has'       # the same bytes, moments ago
    assert sat.send_wake_model(DEV, dict(CFG, wake_model='hey_marcus'), KEY, force=True) == 'has'
    assert len(sent_models(esp)) == 1


def test_force_sends_under_the_same_name_and_its_own_word_clears_a_distrusted_model(esp):
    with patch.dict(REAL, {('GET', '/wakeword'): dict(ESP_WAKE, model='hey_marcus', stored=True)}):
        assert sat.send_wake_model(DEV, dict(CFG, wake_model='hey_marcus'), KEY) == 'has'          # a save: it has that name
        assert sat.send_wake_model(DEV, dict(CFG, wake_model='hey_marcus'), KEY, force=True) == 'sent'   # a switch: retrained bytes land
    assert sat.send_wake_model(DEV, dict(CFG, wake_model='own'), KEY) == 'has'                    # stored False: nothing to clear at a save
    assert sat.send_wake_model(DEV, dict(CFG, wake_model='own'), KEY, force=True) == 'sent'       # a switch: DELETE even so (a distrusted file)
    assert [(c.method, c.path.split('?')[0]) for c in sent_models(esp)] == [('PUT', '/wakeword/model'), ('DELETE', '/wakeword/model')]


def test_a_wish_the_board_has_not_taken_is_said_and_sent_once_at_a_probe(esp):
    LIVE['wake_model'] = 'hey_marcus'
    st = sat.status(DEV, dict(CFG, wake_model='hey_marcus'), KEY)
    assert st['readings']['wake word'] == 'listening for hey_jarvis; set to hey_marcus, not sent yet'
    assert len(sent_models(esp)) == 1
    st = sat.status(DEV, dict(CFG, wake_model='hey_marcus'), KEY)        # sent, and the board still says otherwise: not again
    assert st['readings']['wake word'] == 'listening for hey_jarvis; set to hey_marcus, the board did not keep it'
    assert len(sent_models(esp)) == 1
    st = sat.status(DEV, dict(CFG, wake_model='hey_jarvis'), KEY)        # what it has: no note
    assert st['readings']['wake word'] == 'listening for hey_jarvis'


def test_the_probe_reads_the_wish_live_and_sends_nothing_under_a_save(esp):
    LIVE['wake_model'] = 'own'                                           # a save just changed it: this probe's row is stale
    sat.status(DEV, dict(CFG, wake_model='hey_marcus'), KEY)
    assert not sent_models(esp)
    LIVE['wake_model'] = 'hey_marcus'
    sat._saving.add('pi2')
    sat.status(DEV, dict(CFG, wake_model='hey_marcus'), KEY)
    assert not sent_models(esp)
    sat._saving.discard('pi2')
    sat.status(DEV, dict(CFG, wake_model='hey_marcus'), KEY)
    assert len(sent_models(esp)) == 1


def test_a_failed_send_is_said_and_tried_again_at_the_next_probe(esp):
    from core.devices.engine import DeviceError
    LIVE['wake_model'] = 'hey_marcus'
    def down(method, url, **kw):
        if '/wakeword/model' in url:
            raise requests.exceptions.ConnectionError()
        return Reply(REAL.get((method, url.replace(CFG['url'], '').split('?')[0]), {"ok": True}))
    with patch.object(sat.net, 'request', down), pytest.raises(DeviceError, match='could not be sent now.*goes when the board answers'):
        sat.apply(DEV, dict(CFG, **LOOKS, wake_model='hey_marcus'), KEY)
    assert sat._pushed['pi2']['ok'] is False
    st = sat.status(DEV, dict(CFG, wake_model='hey_marcus'), KEY)        # the board answers: the probe sends it
    assert st['readings']['wake word'] == 'listening for hey_jarvis; set to hey_marcus, will be sent when it answers'
    assert len(sent_models(esp)) == 1 and sat._pushed['pi2']['ok'] is True


def test_a_library_model_that_is_gone_is_a_reason_at_save(esp):
    from core.devices.engine import DeviceError
    with pytest.raises(DeviceError, match='no usable microWakeWord model named hey_nova'):
        sat.apply(DEV, dict(CFG, **LOOKS, wake_model='hey_nova'), KEY)


def test_an_older_program_that_states_no_family_is_left_alone(esp):
    from core.devices.engine import DeviceError
    with patch.dict(REAL, {('GET', '/health'): BOARD, ('GET', '/wakeword'): {"running": True, "enabled": True, "model": "hey_sapphire", "threshold": 0.5}}):
        sat.apply(DEV, dict(CFG, **LOOKS, wake_model='hey_marcus'), KEY)
        with pytest.raises(DeviceError, match='no door'):
            sat.send_wake_model(DEV, dict(CFG, wake_model='hey_marcus'), KEY, force=True)
    assert not sent_models(esp)


def test_validate_keeps_the_wish_as_the_library_names_it():
    cfg, err = sat.validate(dict(CFG, wake_model=' Hey_Jarvis_v0.1 '))
    assert err == '' and cfg['wake_model'] == 'Hey_Jarvis_v0.1'          # never slugged: it picks an entry by its listed name
    assert sat.validate(dict(CFG, wake_model='own'))[0]['wake_model'] == 'own'
    assert sat.validate(dict(CFG, wake_model=''))[0]['wake_model'] == 'keep'
    assert sat.validate(dict(CFG))[0]['wake_model'] == 'keep'


def test_the_install_action_goes_through_the_library(esp, monkeypatch):
    from core.wakeword import library as lib
    asked = []
    monkeypatch.setattr(lib, 'switch', lambda d, m: asked.append((d, m)) or {'model': m, 'sent': True, 'warning': ''})
    assert run('wake', 'install', 'hey_marcus') == ('pi2 is restarting on hey_marcus. Back in about half a minute.', True)
    assert asked == [('pi2', 'hey_marcus')]
    monkeypatch.setattr(lib, 'switch', lambda d, m: {'model': m, 'sent': False, 'had': True, 'warning': ''})
    assert run('wake', 'install', 'hey_marcus') == ('pi2 has hey_marcus already.', True)
    monkeypatch.setattr(lib, 'switch', lambda d, m: {'model': 'own', 'sent': False, 'warning': 'satellite: Could not reach it.'})
    assert run('wake', 'install', 'own') == ('pi2 is set to its own word, but: satellite: Could not reach it.', False)
    monkeypatch.setattr(lib, 'switch', lambda d, m: (_ for _ in ()).throw(lib.LibraryError('no such model')))
    assert run('wake', 'install', 'x') == ('no such model', False)


def test_the_engine_asks_the_driver_what_a_dynamic_select_offers(monkeypatch):
    from core.devices import engine
    row = {'id': 'pi2', 'label': 'Kitchen', 'fingerprint': 'aa:bb'}
    part = {'driver': 'satellite', 'has': ['wake'], 'takes': ['wake.model'], 'config': {'wake_model': 'hey_gone'}}
    field = {'key': 'wake_model', 'type': 'select', 'dynamic': True}
    seen = {}
    fake = SimpleNamespace(options=lambda device, config, key: seen.update(device=device, key=key) or
                           [{'value': 'keep', 'label': 'what it has now'}, {'value': 'hey_marcus', 'label': 'hey marcus'}, 'junk'])
    monkeypatch.setattr(engine, '_driver', lambda d, p='': (fake, {}))
    assert engine._options(row, part, field) == [{'value': 'keep', 'label': 'what it has now'}, {'value': 'hey_marcus', 'label': 'hey marcus'},
                                                 {'value': 'hey_gone', 'label': 'hey_gone (not in the library now)'}]
    assert seen['key'] == 'wake_model' and seen['device']['fingerprint'] == 'aa:bb' and seen['device']['takes'] == ['wake.model']
    monkeypatch.setattr(engine, '_driver', lambda d, p='': (_ for _ in ()).throw(RuntimeError('off')))
    assert engine._options(row, part, field) == [{'value': 'hey_gone', 'label': 'hey_gone (not in the library now)'}]
