# tests/test_devices_satellite.py - the satellite driver (core/devices/drivers).
# The network is faked: every reply below is the shape the real Pi body
# returned on 2026-09-27. No satellite, no speech engine, no sound.
import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from core.devices.drivers import satellite as sat
from core.devices import voice

DEV = {'id': 'pi2', 'label': 'Kitchen', 'location': 'Kitchen'}
CFG = {'url': 'http://192.168.0.221:8090', 'camera': True, 'chat': ''}


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
    with patch.object(sat.net, 'request', fake):
        yield calls
    sat._about.clear()
    sat._palettes.clear()


run = lambda cap, action, value='', cfg=CFG, key=KEY: sat.run(DEV, cap, action, value, cfg, key, None)


# --- the declaration -----------------------------------------------------------

def test_spec_registers_as_a_core_driver():
    import core.devices.registry as reg
    importlib.reload(reg)
    assert reg.register_driver('satellite', sat.SPEC, 'core', builtin=True)
    spec = reg.get_driver('satellite')
    assert spec['capabilities'] == ['speaker', 'mic', 'light', 'wake', 'camera', 'power']
    assert sorted(spec['capabilities']) == sorted(sat.describe(DEV, CFG))
    assert spec['locked_by_default'] == []                    # she may restart a satellite
    assert [f['key'] for f in spec['config_schema'] if f.get('secret')] == ['token', 'voice_key']
    importlib.reload(reg)


def test_validate():
    ok = lambda url: sat.validate({'url': url})
    assert ok('192.168.0.221:8090/')[0]['url'] == 'http://192.168.0.221:8090'
    assert ok('http://sapphire-pi:8090')[1] == '' and ok('https://pi.local:8090')[1] == ''
    assert 'needed' in ok('')[1]
    assert 'own network' in ok('http://8.8.8.8:8090')[1]
    assert 'own network' in ok('http://pi.example.com')[1]
    assert 'not a usable address' in ok('ftp://192.168.0.2')[1]


# --- every request carries the key, and nothing else does ----------------------

def test_the_key_rides_the_header_never_the_address(pi):
    run('wake', 'read')
    call = pi[0]
    assert call.kw['headers'] == {'Authorization': 'Bearer body-key-abcdefgh'}
    assert 'body-key' not in call.path
    text, ok = run('wake', 'read', key=Secrets())
    assert not ok and text.startswith('No key is stored') and len(pi) == 1


def test_status_readings(pi):
    st = sat.status(DEV, CFG, KEY)
    assert st == {'online': True, 'detail': 'sapphire-pi2 at 192.168.0.221:8090',
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
    assert pi[0].path == '/audio/listen?vad=true&max_seconds=10' and pi[0].kw['timeout'] == 22
    with patch.object(voice, 'stt_refusal', return_value=''), \
         patch.object(voice, 'transcribe', return_value=('', '')):
        assert run('mic', 'listen') == ('Listened, and heard no speech.', True)
    assert pi[1].path.endswith('max_seconds=15')
    with patch.object(voice, 'stt_refusal', return_value=''), \
         patch.object(voice, 'transcribe', return_value=('', '')):
        run('mic', 'listen', '9999')
    assert pi[2].path.endswith('max_seconds=60')


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
         patch.object(voice, 'transcribe', return_value=('hi', '')):
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
        assert st == {'online': False, 'detail': 'Could not reach 192.168.0.221:8090. '
                                                 'Check that it is powered and on the network.'}
    with answer(error=requests.exceptions.Timeout()):
        assert run('wake', 'read') == ('No answer from 192.168.0.221:8090 within 8s.', False)
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
    assert st['detail'] == 'den at 192.168.0.221:8090'
    assert st['readings'] == {'program': 'waveshare-s3-audio 0.1.0', 'volume': '85%', 'running for': '1m 1s',
                              'wake word': 'listening for hey_sapphire', 'link to Sapphire': 'connected'}


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
    assert call.kw['headers'] == {'Content-Type': 'audio/wav', 'Authorization': 'Bearer body-key-abcdefgh'}


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


def test_the_camera_switch_belongs_to_the_camera():
    field = next(f for f in sat.SPEC['config_schema'] if f['key'] == 'camera')
    assert field['capability'] == 'camera' and field['tab'] == 'Status'


# --- the looks and the hours ---------------------------------------------------------

LOOKS = {'look_resting': 'sapphire heartbeat bpm=33 ceiling=0.1', 'look_listening': 'yellow spin',
         'look_thinking': 'rainbow spin', 'look_speaking': 'green spin', 'look_nolink': 'red pulse'}


def test_the_looks_are_checked_at_save():
    good = dict(CFG, **LOOKS, lights_from='07:00', lights_until='23:00')
    assert sat.validate(dict(good))[1] == ''
    assert 'Listening' in sat.validate(dict(good, look_listening='yellow spin fast wobble'))[1]
    assert 'has no time' in sat.validate(dict(good, look_thinking='rainbow spin 5s'))[1]
    assert 'clock times' in sat.validate(dict(good, lights_from='7pm'))[1]
    assert sat.validate(dict(good, lights_from='', lights_until=''))[1] == ''


def test_a_save_hands_the_looks_and_hours_to_the_board(board):
    with patch.dict(REAL, {('PUT', '/led/looks'): {"ok": True}}):
        sat.apply(DEV, dict(CFG, **LOOKS, lights_from='07:00', lights_until='23:00'), KEY)
    call = board[-1]
    assert (call.method, call.path) == ('PUT', '/led/looks')
    assert call.kw['json'] == {
        'resting': {'color': 'sapphire', 'animation': 'heartbeat', 'bpm': 33, 'ceiling': 0.1},
        'listening': {'color': 'yellow', 'animation': 'spin'},
        'thinking': {'color': 'rainbow', 'animation': 'spin'},
        'speaking': {'color': 'green', 'animation': 'spin'},
        'nolink': {'color': 'red', 'animation': 'pulse'},
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
