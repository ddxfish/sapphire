# plugins/ipcam/tests/test_ipcam.py - the IP camera driver and its ONVIF calls.
# No network: onvif._post (the one function that sends) and the two picture
# takers are faked. The fake camera answers the way the Jidetech P1 dome did.
import base64
import hashlib
import io
import re
from types import SimpleNamespace

import pytest
from PIL import Image

from plugins.ipcam import device_driver as drv
from plugins.ipcam import onvif

DEVICE = {'id': 'dome', 'label': 'Dome', 'location': 'Office'}
PASSWORD = 'hunter2-not-for-print'


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)


def cfg(**over):
    base = {'host': '192.168.0.60', 'user': 'admin', 'port': 8999,
            'places': [{'name': 'door', 'slot': '3'}]}
    base.update(over)
    return base


def env(body):
    return ('<?xml version="1.0"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
            f'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><e:Body>{body}</e:Body></e:Envelope>')


def profile(token, w, h, ptz=True):
    return (f'<trt:Profiles token="{token}"><tt:VideoEncoderConfiguration><tt:Resolution><tt:Width>{w}</tt:Width>'
            f'<tt:Height>{h}</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration>'
            + ('<tt:PTZConfiguration token="p"/>' if ptz else '') + '</trt:Profiles>')


def jpeg(shade):
    out = io.BytesIO()
    Image.new('L', (64, 48), shade).save(out, 'JPEG')
    return out.getvalue()


@pytest.fixture
def cam(monkeypatch):
    """A fake camera. .sent = every call's name in order. .ptz False = a
    camera that cannot move. .view = the shade its picture has; a turn
    changes it unless .stuck."""
    c = SimpleNamespace(sent=[], bodies=[], ptz=True, view=40, stuck=False, refuse=False)

    def post(url, xml):
        name = re.search(r'<s:Body><(?:\w+:)?(\w+)', xml).group(1)
        c.sent.append(name)
        c.bodies.append(xml)
        if c.refuse:
            return 400, env('<e:Fault><e:Reason><e:Text>Sender not authorized</e:Text></e:Reason></e:Fault>')
        if name == 'GetSystemDateAndTime':
            return 200, env('<tds:UTCDateTime><tt:Time><tt:Hour>1</tt:Hour><tt:Minute>2</tt:Minute>'
                            '<tt:Second>3</tt:Second></tt:Time><tt:Date><tt:Year>2026</tt:Year>'
                            '<tt:Month>10</tt:Month><tt:Day>9</tt:Day></tt:Date></tds:UTCDateTime>')
        if name == 'GetDeviceInformation':
            return 200, env('<tds:Manufacturer></tds:Manufacturer><tds:Model>IPD-1</tds:Model>'
                            '<tds:FirmwareVersion>V1</tds:FirmwareVersion>')
        if name == 'GetCapabilities':
            # the camera names another host for its doors: it must not be believed
            return 200, env('<tt:Media><tt:XAddr>http://10.9.9.9:8999/onvif/media_service</tt:XAddr></tt:Media>'
                            '<tt:PTZ><tt:XAddr>http://10.9.9.9:8999/onvif/ptz_service</tt:XAddr></tt:PTZ>')
        if name == 'GetProfiles':
            return 200, env(profile('Sub', 640, 480, c.ptz) + profile('Main', 2592, 1944, c.ptz))
        if name == 'GetStreamUri':
            return 200, env('<tt:Uri>rtsp://10.9.9.9:554/1/h264major</tt:Uri>')
        if name == 'GetSnapshotUri':
            return 200, env('<tt:Uri>http://10.9.9.9/jpgimage/1/image.jpg</tt:Uri>')
        if name == 'ContinuousMove' and not c.stuck:
            c.view += 60
        if name == 'GotoPreset' and not c.stuck:
            c.view = 200
        return 200, env('')

    monkeypatch.setattr(onvif, '_post', post)
    monkeypatch.setattr(drv, '_ask', lambda cam, peek=False: None if peek else jpeg(c.view))
    monkeypatch.setattr(drv.time, 'sleep', lambda s: None)
    drv._cams.clear()
    yield c
    drv._cams.clear()


def run(capability, action, value='', config=None):
    return drv.run(DEVICE, capability, action, value, config or cfg(), Secrets(password=PASSWORD), None)


# --- the wire ----------------------------------------------------------------

def test_the_password_is_sent_as_a_digest_never_itself(cam):
    drv.status(DEVICE, cfg(), Secrets(password=PASSWORD))
    xml = next(b for b, n in zip(cam.bodies, cam.sent) if n == 'GetDeviceInformation')
    assert PASSWORD not in ''.join(cam.bodies)
    nonce = base64.b64decode(re.search(r'<Nonce[^>]*>([^<]+)<', xml).group(1))
    created = re.search(r'<Created[^>]*>([^<]+)<', xml).group(1)
    want = base64.b64encode(hashlib.sha1(nonce + created.encode() + PASSWORD.encode()).digest()).decode()
    assert re.search(r'<Password[^>]*>([^<]+)<', xml).group(1) == want
    assert created.startswith('2026-10-09T01:0')          # stamped with the CAMERA's clock


def test_addresses_the_camera_names_are_held_to_the_host_the_user_entered(cam):
    drv.status(DEVICE, cfg(), Secrets(password=PASSWORD))
    held = drv._cams['dome'][1]
    assert held.ptz == 'http://192.168.0.60:8999/onvif/ptz_service'
    assert held.profiles[0]['snapshot'] == 'http://192.168.0.60/jpgimage/1/image.jpg'
    assert held.profiles[0]['stream'] == 'rtsp://192.168.0.60:554/1/h264major'
    assert '10.9.9.9' not in repr(vars(held))


def test_the_stream_address_quotes_the_login():
    c = onvif.Camera('192.168.0.60', 80, 'ad min', 'p@ss/word')
    assert c.stream_url({'stream': 'rtsp://192.168.0.60:554/1/x'}) == 'rtsp://ad%20min:p%40ss%2Fword@192.168.0.60:554/1/x'


def test_an_answer_with_a_doctype_is_not_parsed():
    assert onvif._parse('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>') is None


# --- status ------------------------------------------------------------------

def test_status_says_what_the_camera_has(cam):
    st = drv.status(DEVICE, cfg(), Secrets(password=PASSWORD))
    assert st['online'] and st['has'] == ['camera', 'ptz']
    assert st['detail'] == 'IPD-1 at 192.168.0.60'
    assert st['readings']['picture'] == '2592x1944'        # the sharpest profile, whatever order they came in
    assert 'Stop' in cam.sent                              # first contact ends a move left running


def test_a_camera_that_cannot_move_has_no_ptz(cam):
    cam.ptz = False
    st = drv.status(DEVICE, cfg(), Secrets(password=PASSWORD))
    assert st['has'] == ['camera'] and 'Stop' not in cam.sent
    text, ok = run('ptz', 'move', 'left')
    assert not ok and text == "This camera cannot move."


def test_a_refused_login_is_offline_with_the_reason(cam):
    cam.refuse = True
    st = drv.status(DEVICE, cfg(), Secrets(password='wrong'))
    assert st['online'] is False and 'refused the login' in st['detail']
    assert 'dome' not in drv._cams


def test_the_camera_is_asked_once_what_it_has(cam):
    for _ in range(3):
        drv.status(DEVICE, cfg(), Secrets(password=PASSWORD))
    assert cam.sent.count('GetProfiles') == 1
    drv.status(DEVICE, cfg(), Secrets(password='changed'))   # a new login: asked afresh
    assert cam.sent.count('GetProfiles') == 2


# --- pictures ----------------------------------------------------------------

def test_look_answers_with_a_picture(cam):
    told, ok = run('camera', 'look')
    assert ok and told['text'] == 'A picture from the camera dome (Office).'
    assert base64.b64decode(told['images'][0]['data'])[:2] == b'\xff\xd8'
    assert told['images'][0]['media_type'] == 'image/jpeg'


def test_sharp_and_a_camera_without_a_snapshot_use_the_stream(cam, monkeypatch):
    asked = []
    monkeypatch.setattr(drv, '_from_stream', lambda c, sharp: asked.append(sharp) or jpeg(9))
    assert run('camera', 'look', 'sharp')[1]
    monkeypatch.setattr(drv, '_ask', lambda c, peek=False: None)
    assert run('camera', 'look')[1]
    assert asked == [True, False]
    assert run('camera', 'look', 'now please') == (
        "look: leave the value empty, or say 'sharp' for the full-size picture.", False)


# --- moving ------------------------------------------------------------------

@pytest.mark.parametrize('value, velocity', [
    ('left', 'x="-0.5" y="0.0"'), ('right 60', 'x="0.5" y="0.0"'),
    ('up', 'x="0.0" y="0.5"'), ('down 5', 'x="0.0" y="-0.5"')])
def test_a_move_starts_then_stops_then_shows_the_view(cam, value, velocity):
    told, ok = run('ptz', 'move', value)
    assert ok and told['text'] == f"Turned {value.split()[0]}. This is what dome sees now."
    assert told['images']
    moves = cam.sent[cam.sent.index('ContinuousMove'):]
    assert moves == ['ContinuousMove', 'Stop']
    body = next(b for b, n in zip(cam.bodies, cam.sent) if n == 'ContinuousMove')
    assert f'<tt:PanTilt {velocity}/>' in body
    assert 'Timeout' not in body             # the stop is ours: a camera's own time limit is not relied on


def test_how_far_is_how_long(cam, monkeypatch):
    slept = []
    monkeypatch.setattr(drv.time, 'sleep', slept.append)
    run('ptz', 'move', 'left 50')
    assert slept[0] == pytest.approx(drv.FULL_TURN / 2)
    slept.clear()
    run('ptz', 'zoom', 'in')
    assert slept[0] == pytest.approx(drv.FULL_ZOOM * drv.STEP / 100)


def test_the_stop_is_sent_even_when_the_wait_fails(cam, monkeypatch):
    def boom(s):
        raise RuntimeError('interrupted')
    monkeypatch.setattr(drv.time, 'sleep', boom)
    with pytest.raises(RuntimeError):
        run('ptz', 'move', 'left')
    assert cam.sent[-2:] == ['ContinuousMove', 'Stop']


def test_a_move_that_changes_nothing_says_so(cam):
    cam.stuck = True
    told, ok = run('ptz', 'move', 'up 100')
    assert ok and told['text'] == "Turned up, but the view did not change. It may be at the end of its travel."


def test_zoom_sends_zoom_alone(cam):
    told, ok = run('ptz', 'zoom', 'out 40')
    assert ok and told['text'].startswith('Zoomed out.')
    body = next(b for b, n in zip(cam.bodies, cam.sent) if n == 'ContinuousMove')
    assert '<tt:Zoom x="-1"/>' in body and 'PanTilt' not in body


def test_goto_takes_a_name_and_sends_its_number(cam):
    told, ok = run('ptz', 'goto', 'Door')
    assert ok and told['text'] == "Turned to door. This is what dome sees now."
    body = next(b for b, n in zip(cam.bodies, cam.sent) if n == 'GotoPreset')
    assert '<tptz:PresetToken>3</tptz:PresetToken>' in body
    assert cam.sent[-1] == 'Stop'
    assert run('ptz', 'goto', 'garden') == ("goto: the value is a place's name. Places: door.", False)


def test_save_stores_a_number(cam):
    text, ok = run('ptz', 'save', '4')
    assert ok and 'stored under 4' in text and cam.sent[-1] == 'SetPreset'
    for bad in ('', '0', '256', 'door', '1 2'):
        assert run('ptz', 'save', bad)[1] is False


@pytest.mark.parametrize('action, value', [('move', 'sideways'), ('move', 'left 500'), ('move', 'left 3 4'),
                                           ('zoom', 'closer'), ('zoom', 'in 0')])
def test_a_bad_value_is_answered_in_words_and_nothing_moves(cam, action, value):
    text, ok = run('ptz', action, value)
    assert not ok and isinstance(text, str)
    assert 'ContinuousMove' not in cam.sent


def test_no_result_ever_holds_the_password(cam, monkeypatch):
    monkeypatch.setattr(drv, '_ask', lambda c, peek=False: None)        # force the stream, with av failing

    class Av:
        @staticmethod
        def open(url, **kw):
            raise OSError(f"could not open {url}")
    monkeypatch.setitem(__import__('sys').modules, 'av', Av)
    text, ok = run('camera', 'look')
    assert not ok and text == "The camera's stream gave no picture." and PASSWORD not in text


# --- help --------------------------------------------------------------------

def test_every_example_in_the_help_runs(cam):
    told = drv.describe(DEVICE, cfg())
    assert list(told) == ['camera', 'ptz']
    for capability, info in told.items():
        for action, a in info['actions'].items():
            result, ok = run(capability, action, a['example'])
            assert ok, f"{capability} / {action} '{a['example']}' -> {result}"


def test_goto_is_offered_only_when_places_are_named(cam):
    assert 'goto' not in drv.describe(DEVICE, cfg(places=[]))['ptz']['actions']
    goto = drv.describe(DEVICE, cfg())['ptz']['actions']['goto']
    assert goto['example'] == 'door' and 'door' in goto['help']
    assert drv.describe(DEVICE, cfg())['ptz']['actions']['save']['owner'] is True


# --- saving the device -------------------------------------------------------

def test_validate_finds_the_port_and_cleans_the_rest(monkeypatch):
    monkeypatch.setattr(onvif, 'find', lambda host: 8999)
    config, error = drv.validate({'host': 'http://192.168.0.60:80/', 'user': '', 'port': 0,
                                  'places': [{'name': 'Front Door', 'slot': '03'}]})
    assert error == ''
    assert config == {'host': '192.168.0.60', 'user': 'admin', 'port': 8999,
                      'places': [{'name': 'front-door', 'slot': '3'}]}


def test_validate_keeps_a_port_that_was_entered(monkeypatch):
    monkeypatch.setattr(onvif, 'find', lambda host: pytest.fail('a known port is not looked for'))
    assert drv.validate(cfg())[0]['port'] == 8999


@pytest.mark.parametrize('config, word', [
    ({'host': ''}, 'address is needed'),
    ({'host': 'camera.example.com'}, 'your own network'),
    ({'host': '192.168.0.60', 'places': [{'name': 'left', 'slot': '1'}]}, 'name of its own'),
    ({'host': '192.168.0.60', 'places': [{'name': 'door', 'slot': '300'}]}, '1 to 255'),
    ({'host': '192.168.0.60', 'places': [{'name': 'a', 'slot': '1'}, {'name': 'a', 'slot': '2'}]}, 'name of its own'),
    ({'host': '192.168.0.60', 'port': 0}, 'No ONVIF camera answered'),
])
def test_validate_refuses_in_words(monkeypatch, config, word):
    monkeypatch.setattr(onvif, 'find', lambda host: None)
    assert word in drv.validate(dict(config))[1]


def test_find_asks_the_camera_then_tries_the_usual_ports(monkeypatch):
    monkeypatch.setattr(onvif, '_announced', lambda host, wait: 8999)
    monkeypatch.setattr(onvif, '_speaks_onvif', lambda host, port: port == 8999)
    assert onvif.find('192.168.0.60') == 8999
    monkeypatch.setattr(onvif, '_announced', lambda host, wait: None)
    monkeypatch.setattr(onvif, '_speaks_onvif', lambda host, port: port == 8080)
    assert onvif.find('192.168.0.60') == 8080
    monkeypatch.setattr(onvif, '_speaks_onvif', lambda host, port: False)
    assert onvif.find('192.168.0.60') is None
