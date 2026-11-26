# core/plugins/homeassistant/tests/test_ha_driver.py - the Home Assistant driver
# against a fake Home Assistant (core.net.request is faked: no network), and
# the one-time carry from 1.x through the real device engine on temp files.
# Nothing here touches user/ or ~/.config/sapphire.

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from core.devices import engine
from core.devices import health
from plugins.homeassistant import device_driver as drv
from plugins.homeassistant.hooks import ready

DEVICE = {'id': 'home', 'label': 'Home', 'location': ''}
CONFIG = {'url': 'http://ha.local:8123'}
MANIFEST = json.loads((Path(drv.__file__).parent / 'plugin.json').read_text(encoding='utf-8'))


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)


SECRETS = Secrets(token='tok-secret-long')


def _state(eid, state='on', **attrs):
    return {'entity_id': eid, 'state': state, 'attributes': attrs}


STATES = [
    _state('light.pole', 'on', friendly_name='Pole Lamp', brightness=153),
    _state('light.arm', 'off', friendly_name='Arm Lamp'),
    _state('switch.zap', 'on', friendly_name='Bug Zappers'),
    _state('input_boolean.guest', 'off', friendly_name='Guest Mode'),
    _state('climate.main', 'cool', friendly_name='Thermostat', current_temperature=74, temperature=72),
    _state('scene.dark', '2026-11-01T00:00:00', friendly_name='Dark'),
    _state('script.blinds', 'off', friendly_name='Close blinds'),
    _state('cover.garage', 'closed', friendly_name='Garage Door', current_position=0),
    _state('lock.front', 'locked', friendly_name='Front Door'),
    _state('fan.attic', 'on', friendly_name='Attic Fan', percentage=50),
    _state('media_player.tv', 'playing', friendly_name='TV', media_title='A Film', volume_level=0.3),
    _state('camera.porch', 'idle', friendly_name='Porch Cam'),
    _state('alarm_control_panel.house', 'disarmed', friendly_name='House Alarm'),
    _state('sensor.bed_temp', '71.5', friendly_name='Bedroom Temperature', device_class='temperature',
           unit_of_measurement='°F'),
    _state('binary_sensor.back', 'on', friendly_name='Back Door', device_class='door'),
    _state('person.sam', 'home', friendly_name='Sam'),
    # what a house is mostly made of, and none of it is worth a line:
    _state('sensor.plug_power', '12', friendly_name='Plug Power', device_class='power'),
    _state('number.led_level', '5', friendly_name='LED level'),
    _state('button.identify', 'unknown', friendly_name='Identify'),
    _state('update.firmware', 'off', friendly_name='Firmware'),
    _state('light.hidden', 'off', friendly_name='Hidden Light'),
    _state('light.ghost', 'unavailable', friendly_name='Ghost Light', restored=True),
]
ROOMS = [['light.pole', 'Living Room', False], ['light.arm', 'Living Room', False],
         ['switch.zap', 'Back Porch', False], ['light.hidden', 'Hall', True]]
SERVICES = [{'domain': 'notify', 'services': {'notify': {}, 'persistent_notification': {}, 'send_message': {},
                                               'mobile_app_pixel_7': {}, 'mobile_app_tablet': {}}},
            {'domain': 'light', 'services': {'turn_on': {}}}]


class Answer:
    def __init__(self, body=None, status=200, content=b'', headers=None):
        self.status_code, self._body, self.content, self.headers = status, body, content, headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class FakeHA:
    """core.net.request, answering as Home Assistant would."""

    def __init__(self):
        self.calls, self.fail, self.changed = [], None, []

    def __call__(self, method, url, json=None, timeout=None, headers=None, **kw):
        assert url.startswith(CONFIG['url']) and headers['Authorization'] == 'Bearer tok-secret-long'
        path = url[len(CONFIG['url']):]
        self.calls.append((method, path, json))
        if self.fail:
            if isinstance(self.fail, Exception):
                raise self.fail
            return self.fail
        if path == '/api/config':
            return Answer({'version': '2025.10.3', 'unit_system': {'temperature': '°F'}})
        if path == '/api/states':
            return Answer(STATES)
        if path.startswith('/api/states/'):
            return Answer(next(s for s in STATES if s['entity_id'] == path.split('/')[-1]))
        if path == '/api/template':
            return Answer(globals()['json'].dumps(ROOMS))
        if path == '/api/services':
            return Answer(SERVICES)
        if path.startswith('/api/camera_proxy/'):
            return Answer(content=b'\xff\xd8jpeg', headers={'Content-Type': 'image/jpeg; charset=binary'})
        if path.startswith('/api/services/'):
            return Answer(self.changed)
        raise AssertionError(f"unexpected call {method} {path}")

    def sent(self):
        """The orders only: (path, body)."""
        return [(p, b) for m, p, b in self.calls if m == 'POST' and p.startswith('/api/services/')]


@pytest.fixture
def ha():
    fake = FakeHA()
    drv._held.clear()
    with patch.object(drv.net, 'request', fake):
        yield fake
    drv._held.clear()


def _thing(eid, name='Thing', kind='light', room=''):
    return {'id': eid, 'name': name, 'kind': kind, 'room': room, 'call': name.lower().replace(' ', '-')}


def _run(kind, action, value='', things=None, room=''):
    return drv.run(dict(DEVICE, things=things or [_thing('light.pole', 'Pole Lamp')], room=room),
                   kind, action, value, CONFIG, SECRETS, None)


# --- the manifest ------------------------------------------------------------

def test_the_manifest_and_the_driver_name_the_same_kinds():
    spec = MANIFEST['capabilities']['devices'][0]
    assert spec['things'] is True and spec['capabilities'] == list(drv.KINDS)
    assert set(spec['locked_by_default']) <= set(drv.KINDS)
    import core.devices.registry as reg
    importlib.reload(reg)
    try:
        assert reg.register_driver(spec['driver'], spec, 'homeassistant')
        assert reg.get_driver('homeassistant')['things'] is True
    finally:
        importlib.reload(reg)


def test_validate_takes_an_address_and_refuses_what_is_not_one():
    assert drv.validate({'url': 'ha.local:8123/'}) == ({'url': 'http://ha.local:8123'}, '')
    assert drv.validate({'url': 'https://ha.example.com'})[1] == ''
    for bad in ('', 'http://', 'http://ha.local/path/more', 'ftp://ha'):
        assert drv.validate({'url': bad})[1]


# --- what the house has --------------------------------------------------------

def test_status_lists_the_things_worth_naming(ha):
    told = drv.status(DEVICE, CONFIG, SECRETS)
    assert told['online'] is True and told['detail'] == 'Home Assistant 2025.10.3 at ha.local:8123'
    by_id = {t['id']: t for t in told['things']}
    assert set(by_id) == {'light.pole', 'light.arm', 'switch.zap', 'input_boolean.guest', 'climate.main',
                          'scene.dark', 'script.blinds', 'cover.garage', 'lock.front', 'fan.attic',
                          'media_player.tv', 'camera.porch', 'alarm_control_panel.house', 'sensor.bed_temp',
                          'binary_sensor.back', 'person.sam', 'notify.mobile_app_pixel_7',
                          'notify.mobile_app_tablet'}
    assert by_id['light.pole'] == {'id': 'light.pole', 'name': 'Pole Lamp', 'kind': 'light', 'room': 'Living Room'}
    assert by_id['input_boolean.guest']['kind'] == 'switch' and by_id['person.sam']['kind'] == 'sensor'
    assert by_id['notify.mobile_app_pixel_7']['name'] == 'Pixel 7'
    assert [t['kind'] for t in told['things']][:2] == ['light', 'light'] and told['things'][-1]['kind'] == 'notify'
    assert told['has'] == sorted(drv.KINDS)
    assert told['readings'] == {'things': '18', 'rooms': '2'}


def test_the_list_is_kept_two_minutes_and_asked_afresh_after_a_save(ha):
    drv.status(DEVICE, CONFIG, SECRETS)
    drv.status(DEVICE, CONFIG, SECRETS)
    assert [p for _, p, _ in ha.calls].count('/api/states') == 1
    drv.apply(DEVICE, CONFIG, SECRETS)
    drv.status(DEVICE, CONFIG, SECRETS)
    assert [p for _, p, _ in ha.calls].count('/api/states') == 2


def test_a_house_that_does_not_answer_is_said_in_words(ha):
    ha.fail = requests.exceptions.ConnectionError('refused by tok-secret-long')
    told = drv.status(DEVICE, CONFIG, SECRETS)
    assert told == {'online': False, 'detail': 'Could not reach Home Assistant at http://ha.local:8123.'}
    ha.fail = Answer({'message': 'nope'}, status=401)
    assert 'refused the access token' in drv.status(DEVICE, CONFIG, SECRETS)['detail']
    assert 'No access token is stored' in drv.status(DEVICE, CONFIG, Secrets())['detail']


def test_rooms_that_cannot_be_read_do_not_lose_the_things(ha):
    real = ha.__call__

    def no_template(method, url, **kw):
        if url.endswith('/api/template'):
            return Answer({'message': 'TemplateError'}, status=400)
        return real(method, url, **kw)
    with patch.object(drv.net, 'request', no_template):
        told = drv.status(DEVICE, CONFIG, SECRETS)
    assert len(told['things']) == 19 and not any(t['room'] for t in told['things'])     # the hidden light is not known as hidden


def test_what_each_thing_is_doing(ha):
    drv.status(DEVICE, CONFIG, SECRETS)                     # the house's unit is learned here
    now = drv.states(DEVICE, CONFIG, SECRETS)
    assert now['light.pole'] == 'on 60%' and now['light.arm'] == 'off'
    assert now['climate.main'] == 'cool, 74°F now, set to 72'
    assert now['cover.garage'] == 'closed 0%' and now['fan.attic'] == 'on 50%'
    assert now['media_player.tv'] == 'playing: A Film, volume 30'
    assert now['sensor.bed_temp'] == '71.5 °F' and now['binary_sensor.back'] == 'open'
    assert now['person.sam'] == 'home' and now['lock.front'] == 'locked'
    assert now['light.ghost'] == 'unavailable'
    assert 'scene.dark' not in now and 'camera.porch' not in now


# --- orders ------------------------------------------------------------------

def test_every_example_in_her_help_runs(ha):
    """Sapphire copies the examples: each must run exactly as written."""
    told = drv.describe(DEVICE, CONFIG)
    assert set(told) == set(drv.KINDS)
    for kind, cap in told.items():
        for action, a in cap['actions'].items():
            eid = 'notify.mobile_app_pixel_7' if kind == 'notify' else next(
                s['entity_id'] for s in STATES if drv._KIND_OF.get(s['entity_id'].split('.')[0]) == kind)
            value = a['example'] or ('hello' if kind == 'notify' else '')
            result, ok = _run(kind, action, value, [_thing(eid, 'It', kind)])
            assert ok is True, (kind, action, result)


@pytest.mark.parametrize('kind,action,value,service,data', [
    ('light', 'on', '', 'homeassistant/turn_on', {}),
    ('switch', 'toggle', '', 'homeassistant/toggle', {}),
    ('light', 'set', '40', 'light/turn_on', {'brightness_pct': 40}),
    ('light', 'set', '+20%', 'light/turn_on', {'brightness_step_pct': 20}),
    ('light', 'set', '-15', 'light/turn_on', {'brightness_step_pct': -15}),
    ('light', 'set', '0', 'light/turn_off', {}),
    ('light', 'set', 'Blue', 'light/turn_on', {'color_name': 'blue'}),
    ('light', 'set', '30 light blue', 'light/turn_on', {'brightness_pct': 30, 'color_name': 'lightblue'}),
    ('light', 'set', 'warm white', 'light/turn_on', {'color_temp_kelvin': 2700}),
    ('light', 'set', '80, 4000k', 'light/turn_on', {'brightness_pct': 80, 'color_temp_kelvin': 4000}),
    ('light', 'set', '#ff8800', 'light/turn_on', {'rgb_color': [255, 136, 0]}),
    ('climate', 'set', '76', 'climate/set_temperature', {'temperature': 76.0}),
    ('climate', 'set', '68-74', 'climate/set_temperature', {'target_temp_low': 68.0, 'target_temp_high': 74.0}),
    ('climate', 'mode', 'Heat', 'climate/set_hvac_mode', {'hvac_mode': 'heat'}),
    ('scene', 'run', '', 'scene/turn_on', {}),
    ('script', 'run', '', 'script/turn_on', {}),
    ('cover', 'set', '50', 'cover/set_cover_position', {'position': 50}),
    ('cover', 'stop', '', 'cover/stop_cover', {}),
    ('lock', 'unlock', '', 'lock/unlock', {}),
    ('fan', 'set', '30', 'fan/set_percentage', {'percentage': 30}),
    ('media', 'volume', '30', 'media_player/volume_set', {'volume_level': 0.3}),
    ('media', 'next', '', 'media_player/media_next_track', {}),
    ('alarm', 'arm', '', 'alarm_control_panel/alarm_arm_away', {}),
    ('alarm', 'disarm', '1234', 'alarm_control_panel/alarm_disarm', {'code': '1234'}),
])
def test_an_order_is_one_service_call(ha, kind, action, value, service, data):
    result, ok = _run(kind, action, value, [_thing('x.one', 'One', kind)])
    assert ok is True, result
    assert ha.sent() == [('/api/services/' + service, dict(data, entity_id=['x.one']))]


@pytest.mark.parametrize('kind,action,value,says', [
    ('light', 'set', '', 'a brightness from 0 to 100'),
    ('light', 'set', '140', 'a brightness from 0 to 100'),
    ('light', 'set', '40 !!', 'a brightness from 0 to 100'),
    ('climate', 'set', 'warm', 'a temperature like 72'),
    ('climate', 'mode', '', 'the value is the mode'),
    ('cover', 'set', 'half', 'a number from 0 to 100'),
    ('alarm', 'arm', 'tight', 'home, away or night'),
    ('notify', 'ping', '', 'the words to show'),
    ('light', 'fly', '', 'A light has no fly'),
])
def test_a_value_she_got_wrong_is_explained_and_nothing_is_sent(ha, kind, action, value, says):
    result, ok = _run(kind, action, value, [_thing('x.one', 'One', kind)])
    assert ok is False and says in result and ha.sent() == []


def test_the_answer_says_what_the_thing_now_does_when_home_assistant_knows(ha):
    ha.changed = [_state('light.pole', 'on', brightness=102)]
    assert _run('light', 'set', '40') == ('Pole Lamp: on 40%.', True)
    ha.changed = []                                         # a slow radio: it answers later
    assert _run('light', 'set', '40') == ('Pole Lamp: set 40 sent.', True)


def test_a_room_is_one_call_for_all_its_lights(ha):
    things = [_thing('light.pole', 'Pole Lamp', room='Living Room'), _thing('light.arm', 'Arm Lamp', room='Living Room')]
    result, ok = _run('light', 'set', '+20', things, room='Living Room')
    assert ok is True and result == 'Living Room: set +20 sent to 2 lights (pole-lamp, arm-lamp).'
    assert ha.sent() == [('/api/services/light/turn_on',
                          {'brightness_step_pct': 20, 'entity_id': ['light.pole', 'light.arm']})]


def test_an_order_to_a_thing_that_is_not_answering_is_a_failure(ha):
    """Home Assistant answers 200 to an order for an unplugged lamp and does nothing."""
    ghost = _thing('light.ghost', 'Ghost Light')
    result, ok = _run('light', 'set', '40', [ghost])
    assert ok is False and result.startswith('Ghost Light is not answering Home Assistant right now (unavailable)')
    result, ok = _run('light', 'on', '', [_thing('light.pole', 'Pole Lamp'), ghost], room='Living Room')
    assert ok is True and result == ('Living Room: on sent to 1 light (pole-lamp). '
                                     'Not answering, so left as they were: ghost-light.')


def test_no_answer_in_time_is_never_called_done(ha):
    """1.x answered OK when Home Assistant timed out."""
    ha.fail = requests.exceptions.Timeout()
    result, ok = _run('light', 'on')
    assert ok is False and 'did not answer in 20 s' in result and 'before sending it again' in result


def test_home_assistants_own_refusal_is_passed_on(ha):
    ha.fail = Answer({'message': 'Color name nope is not known'}, status=400)
    result, ok = _run('light', 'set', 'nope')
    assert ok is False and result == 'Home Assistant answered HTTP 400: Color name nope is not known'
    assert 'tok-secret-long' not in result


def test_a_camera_a_sensor_and_a_phone(ha):
    result, ok = _run('camera', 'look', '', [_thing('camera.porch', 'Porch Cam', 'camera')])
    assert ok is True and result['text'] == 'A picture from the camera Porch Cam.'
    assert result['images'] == [{'data': '/9hqcGVn', 'media_type': 'image/jpeg'}]
    drv.status(DEVICE, CONFIG, SECRETS)
    assert _run('sensor', 'read', '', [_thing('sensor.bed_temp', 'Bedroom Temperature', 'sensor')]) \
        == ('Bedroom Temperature: 71.5 °F', True)
    result, ok = _run('notify', 'ping', 'Dinner is ready', [_thing('notify.mobile_app_pixel_7', 'Pixel 7', 'notify')])
    assert ok is True and result == 'Shown on Pixel 7: Dinner is ready'
    assert ha.sent() == [('/api/services/notify/mobile_app_pixel_7', {'message': 'Dinner is ready'})]


def test_with_no_thing_named_nothing_is_sent(ha):
    result, ok = drv.run(dict(DEVICE), 'light', 'on', '', CONFIG, SECRETS, None)
    assert ok is False and ha.calls == []


# --- through the real engine, and the carry from 1.x --------------------------

class FakeStore:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def update_with_lock(self, k, mutator, default=None):
        self.d[k] = mutator(self.d.get(k, default))
        return self.d[k]

    def save(self, k, v):
        self.d[k] = v


@pytest.fixture
def house(tmp_path, ha):
    """The real engine and the real driver on temp files, with the fake Home Assistant behind them."""
    from core.credentials_manager import CredentialsManager
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    box = types.SimpleNamespace(ha=ha, store=FakeStore(), state=FakeStore(), token='tok-secret-long',
                                old={'url': CONFIG['url'], 'blacklist': ['cover.*', 'lock.*', 'area:back porch',
                                                                        'light.arm'],
                                     'notify_service': 'notify.mobile_app_pixel_7'})
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        mgr = CredentialsManager()
        with patch.object(sec, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(sec, 'CONFIG_DIR', tmp_path), \
             patch.object(sec, '_crypto', lambda: mgr), \
             patch.object(engine, '_store', lambda: box.store), \
             patch.object(health, '_store', FakeStore), \
             patch.object(engine, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
             patch.object(engine, '_all_plugin_info', lambda: []), \
             patch.object(engine, '_managed', lambda: False), \
             patch.object(ready, '_old', lambda: box.old), \
             patch.object(ready, '_token', lambda: box.token), \
             patch.object(ready, '_state', lambda: box.state):
            sec.reload()
            importlib.reload(reg)
            reg.CORE_DRIVERS = ()
            engine._modules.clear()
            engine._modules_gen = None
            health._belief.clear(); health._saved = None
            assert sys.modules[drv.__name__] is drv         # the engine must load THIS module, not a second copy
            assert reg.register_driver('homeassistant', MANIFEST['capabilities']['devices'][0], 'homeassistant')
            yield box
            engine._modules.clear()
            engine._modules_gen = None
            health._belief.clear(); health._saved = None
            importlib.reload(reg)
            sec.reload()


def test_her_three_calls_end_to_end(house):
    engine.add('home', 'Home', 'homeassistant', {'url': 'ha.local:8123', 'token': 'tok-secret-long'})
    engine.status('home', fresh=True)
    text, ok = engine.run('home')
    assert ok is True and '  living-room: arm-lamp=off, pole-lamp=on 60%' in text
    assert 'Each climate takes set (value <temperature>), mode (value <mode>):' in text
    assert 'thermostat=cool, 74°F now, set to 72' in text
    assert 'Each sensor takes read:' in text and 'back-door=open' in text
    assert 'Locked by the user: ' in text and 'garage-door' in text and 'pixel-7' in text
    assert 'tok-secret-long' not in text and len(text) < 1500
    assert engine.run('home', 'living-room', 'set', '+20')[1] is True
    assert engine.run('home', 'thermostat', 'set', '76') == ('Thermostat: set 76 sent.', True)
    assert engine.run('home', 'front-door', 'unlock')[1] is False                    # starts locked
    assert house.ha.sent() == [
        ('/api/services/light/turn_on', {'brightness_step_pct': 20, 'entity_id': ['light.arm', 'light.pole']}),
        ('/api/services/climate/set_temperature', {'temperature': 76.0, 'entity_id': ['climate.main']})]


def test_the_old_setup_becomes_a_device_once(house):
    ready.carry()
    row = engine.get('homeassistant')
    assert row['label'] == 'Home Assistant'
    assert row['parts'][0]['config'] == {'url': CONFIG['url']} and row['parts'][0]['plugin'] == 'homeassistant'
    assert 'tok-secret-long' not in json.dumps(house.store.d)
    locked = {t['id'] for t in engine.things(row) if t['locked']}
    assert {'light.arm', 'switch.zap', 'cover.garage', 'lock.front'} <= locked         # the old blacklist, kept
    assert 'notify.mobile_app_pixel_7' not in locked and 'notify.mobile_app_tablet' in locked
    assert 'light.pole' not in locked
    assert house.state.d == {'carried': True}
    engine.remove('homeassistant')
    ready.carry()                                           # the user deleted it: it does not come back
    assert engine.rows() == {}


def test_nothing_is_carried_while_home_assistant_does_not_answer(house):
    house.ha.fail = requests.exceptions.ConnectionError()
    ready.carry()
    assert engine.rows() == {} and house.state.d == {}      # the blacklist is not lost: tried again next start
    house.ha.fail = None
    ready.carry()
    assert 'homeassistant' in engine.rows()


def test_nothing_is_carried_without_an_old_token_or_onto_a_house_that_is_there(house):
    house.token = ''
    ready.carry()
    assert engine.rows() == {} and house.state.d == {}
    house.token = 'tok-secret-long'
    engine.add('cabin', 'Cabin', 'homeassistant', {'url': 'ha.local:8123', 'token': 'tok-secret-long'})
    ready.carry()
    assert list(engine.rows()) == ['cabin'] and house.state.d == {'carried': True}


def test_the_proxy_bypass_names_every_houses_host(house):
    engine.add('home', 'Home', 'homeassistant', {'url': 'ha.local:8123', 'token': 'tok-secret-long'})
    engine.add('cabin', 'Cabin', 'homeassistant', {'url': 'https://cabin.example.com', 'token': 'tok-secret-long'})
    assert ready._hosts() == {'ha.local', 'cabin.example.com'}
