# tests/test_devices_things.py - a HUB in the device engine: one device with
# many things behind it (a home's lights). She names a thing or a room where
# a capability would stand. Real registry, real secrets store on a temp file,
# one fake hub driver. Nothing here touches user/ or ~/.config/sapphire.

import importlib
import sys
import types
from unittest.mock import patch

import pytest

from core.devices import engine as core
from core.devices import health
from core.routes import devices as routes

MOD = 'plugins.fakehub.device_driver'
DRIVER = {'label': 'Fake hub', 'module': 'device_driver.py', 'things': True,
          'capabilities': ['light', 'switch', 'lock', 'scene'], 'locked_by_default': ['lock'],
          'config_schema': [{'key': 'url', 'type': 'string'},
                            {'key': 'token', 'type': 'string', 'secret': True}]}
HOUSE = [
    {'id': 'light.pole', 'name': 'Pole Lamp', 'kind': 'light', 'room': 'Living Room'},
    {'id': 'light.arm', 'name': 'Arm Lamp', 'kind': 'light', 'room': 'Living Room'},
    {'id': 'light.kitchen', 'name': 'Kitchen', 'kind': 'light', 'room': 'Kitchen'},       # named as its room
    {'id': 'switch.zap', 'name': 'Bug Zappers', 'kind': 'switch', 'room': 'Back Porch'},
    {'id': 'switch.hall', 'name': 'Hall', 'kind': 'switch', 'room': ''},                  # two things, one name
    {'id': 'light.hall', 'name': 'Hall', 'kind': 'light', 'room': ''},
    {'id': 'lock.front', 'name': 'Front Door', 'kind': 'lock', 'room': 'Hall Way'},
    {'id': 'scene.dark', 'name': 'Scene', 'kind': 'scene', 'room': ''},                   # named as a kind
    {'id': 'vacuum.rob', 'name': 'Rob', 'kind': 'vacuum', 'room': ''},                    # a kind the driver lacks
]


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


def _fake_hub(box):
    m = types.ModuleType(MOD)
    on = {'on': {'help': 'switch on', 'example': ''}, 'off': {'help': 'switch off', 'example': ''}}

    def describe(device, config):
        return {'light': {'label': 'Lights', 'help': 'switch and dim', 'actions': dict(on, set={
                    'help': 'brightness', 'values': '<0-100>', 'example': '40'})},
                'switch': {'label': 'Switches', 'help': '', 'actions': on},
                'lock': {'label': 'Locks', 'help': '', 'actions': {'unlock': {'help': 'open it', 'example': ''}}},
                'scene': {'label': 'Scenes', 'help': '', 'actions': {'run': {'help': 'switch to it', 'example': ''}}}}

    def status(device, config, secrets):
        return {'online': True, 'detail': 'hub', 'things': box.house,
                'has': sorted({t['kind'] for t in box.house} & set(DRIVER['capabilities']))}

    def states(device, config, secrets):
        return {'light.pole': 'on 60%', 'light.arm': 'off', 'lock.front': f"locked {secrets.get('token')}"}

    def run(device, capability, action, value, config, secrets, call_tool):
        box.ran.append((capability, action, value, [t['id'] for t in device['things']], device.get('room')))
        return f"{action} done", True

    m.describe, m.status, m.states, m.run = describe, status, states, run
    return m


@pytest.fixture
def hub(tmp_path):
    from core.credentials_manager import CredentialsManager
    import core.devices.registry as reg
    import core.devices.secret_store as sec
    box = types.SimpleNamespace(house=[dict(t) for t in HOUSE], ran=[], store=FakeStore())
    with patch('core.credentials_manager.CREDENTIALS_FILE', tmp_path / 'credentials.json'), \
         patch('core.credentials_manager.SCRAMBLE_SALT_FILE', tmp_path / '.scramble_salt'), \
         patch('core.credentials_manager.CONFIG_DIR', tmp_path):
        mgr = CredentialsManager()
        with patch.object(sec, 'SECRETS_FILE', tmp_path / 'device_secrets.json'), \
             patch.object(sec, 'CONFIG_DIR', tmp_path), \
             patch.object(sec, '_crypto', lambda: mgr), \
             patch.object(core, '_store', lambda: box.store), \
             patch.object(health, '_store', FakeStore().__class__), \
             patch.object(core, '_plugin_info', lambda n: {'enabled': True, 'loaded': True}), \
             patch.object(core, '_all_plugin_info', lambda: []), \
             patch.object(core, '_managed', lambda: False):
            sec.reload()
            importlib.reload(reg)
            reg.CORE_DRIVERS = ()
            core._modules.clear()
            core._modules_gen = None
            health._belief.clear(); health._saved = None
            sys.modules[MOD] = _fake_hub(box)
            assert reg.register_driver('fakehub', DRIVER, 'fakehub')
            core.add('home', 'Home', 'fakehub', {'url': 'http://ha', 'token': 'tok-secret-long'})
            core.status('home', fresh=True)             # it says what is behind it
            yield box
            sys.modules.pop(MOD, None)
            core._modules.clear()
            core._modules_gen = None
            health._belief.clear(); health._saved = None
            importlib.reload(reg)
            sec.reload()


def _calls():
    return {t['id']: t['call'] for t in core.things(core.get('home'))}


# --- what the hub said -------------------------------------------------------

def test_things_are_kept_on_the_row_and_an_unknown_kind_is_left_out(hub):
    kept = core.get('home')['parts'][0]['things']
    assert len(kept) == 8 and 'vacuum.rob' not in [t['id'] for t in kept]
    assert set(kept[0]) == {'id', 'name', 'kind', 'room'}
    before = dict(hub.store.d['devices'])
    core.status('home', fresh=True)                     # the same list again writes nothing
    assert hub.store.d['devices'] is before or hub.store.d['devices'] == before


def test_things_survive_a_status_that_does_not_name_them(hub):
    hub.house = None                                    # offline, or it could not list them
    core.status('home', fresh=True)
    assert len(core.things(core.get('home'))) == 8


def test_no_more_than_the_limit_is_kept_first_named_first(hub):
    hub.house = [{'id': f'light.n{i}', 'name': f'N {i:04}', 'kind': 'light', 'room': ''}
                 for i in range(core.MAX_THINGS + 30)]
    core.status('home', fresh=True)
    kept = core.get('home')['parts'][0]['things']
    assert len(kept) == core.MAX_THINGS and kept[-1]['id'] == f'light.n{core.MAX_THINGS - 1}'


def test_the_name_she_types_is_made_from_the_things_own(hub):
    calls = _calls()
    assert calls['light.pole'] == 'pole-lamp' and calls['switch.zap'] == 'bug-zappers'
    assert calls['light.kitchen'] == 'kitchen-light'            # 'kitchen' is the room
    assert {calls['switch.hall'], calls['light.hall']} == {'hall-switch', 'hall-light'}
    assert calls['scene.dark'] == 'scene-scene'                 # 'scene' is a kind
    assert len(set(calls.values())) == len(calls)


def test_long_names_that_differ_at_the_end_stay_apart(hub):
    """A camera nobody named: Home Assistant calls everything on it by its address."""
    urn = 'urn:uuid:00000000-1111-2222-3333-444455556666'
    hub.house = [{'id': 'light.a', 'name': f'{urn} MainStream', 'kind': 'light', 'room': ''},
                 {'id': 'switch.b', 'name': f'{urn} Wiper', 'kind': 'switch', 'room': ''}]
    core.status('home', fresh=True)
    assert _calls() == {'light.a': 'urn-uuid-00000000-1111-2222-3333-444455556666-mainstream',
                        'switch.b': 'urn-uuid-00000000-1111-2222-3333-444455556666-wiper'}


# --- device_action -----------------------------------------------------------

def test_a_thing_is_named_where_a_capability_would_stand(hub):
    assert core.run('home', 'pole-lamp', 'set', '40') == ('set done', True)
    assert core.run('home', 'Bug_Zappers', 'on') == ('on done', True)       # her spelling is forgiven
    assert core.run('home', 'light.arm', 'off') == ('off done', True)       # its id works too
    assert hub.ran == [('light', 'set', '40', ['light.pole'], ''),
                       ('switch', 'on', '', ['switch.zap'], ''),
                       ('light', 'off', '', ['light.arm'], '')]


def test_a_room_is_its_lights(hub):
    assert core.run('home', 'living-room', 'set', '+20')[1] is True
    assert hub.ran == [('light', 'set', '+20', ['light.arm', 'light.pole'], 'Living Room')]
    text, ok = core.run('home', 'back-porch', 'on')             # a room with a switch and no light
    assert ok is False and 'no light in Back Porch' in text and 'bug-zappers' in text


def test_a_kind_alone_is_not_something_to_act_on(hub):
    text, ok = core.run('home', 'light', 'on')
    assert ok is False and "Name the light itself" in text and 'pole-lamp' in text
    text, ok = core.run('home', 'light')
    assert ok is True and 'brightness' in text and 'pole-lamp=on 60%' in text and 'bug-zappers' not in text
    assert hub.ran == []


def test_a_wrong_name_or_action_answers_with_the_way_forward(hub):
    text, ok = core.run('home', 'lamp', 'on')
    assert ok is False and 'Close: arm-lamp, pole-lamp.' in text
    text, ok = core.run('home', 'nothing-like-it', 'on')
    assert ok is False and "has no 'nothing-like-it'" in text and 'living-room: ' in text
    text, ok = core.run('home', 'pole-lamp', 'explode')
    assert ok is False and "A light has no action 'explode'" in text and 'set <0-100>' in text
    assert hub.ran == []


# --- what she reads ----------------------------------------------------------

def test_the_house_on_one_screen(hub):
    text, ok = core.run('home')
    assert ok is True
    assert 'Each light takes on, off, set <0-100>:' in text
    assert '  living-room: arm-lamp=off, pole-lamp=on 60%' in text
    assert '  hall-light' in text                               # no room: no room in front of it
    assert 'Each switch takes on, off:' in text and '  back-porch: bug-zappers' in text
    assert 'Locked (the user has not allowed you this' in text and text.count('front-door') == 1
    assert 'Each lock' not in text                              # a kind with nothing she may use is not taught
    assert text.splitlines()[1].startswith('Name a thing where a capability would stand: device_action("home","')
    assert "A room's name there means its lights." in text      # said before the kinds, not after them
    assert 'tok-secret-long' not in text


def test_things_the_hub_cannot_reach_are_said_once(hub):
    mod = sys.modules[MOD]
    with patch.object(mod, 'states', lambda d, c, s: {'light.pole': 'on 60%', 'light.arm': 'unavailable',
                                                      'switch.zap': 'unavailable', 'switch.hall': 'unavailable'}):
        text, _ = core.run('home')
    assert '  living-room: pole-lamp=on 60%' in text and 'back-porch' not in text
    assert 'Not answering now: arm-lamp, bug-zappers, hall-switch' in text and '=unavailable' not in text
    assert 'Each switch' not in text                            # every switch is away: no empty heading


def test_one_thing_and_one_room(hub):
    text, ok = core.run('home', 'pole-lamp')
    assert ok is True and text.startswith('pole-lamp: Pole Lamp, a light in Living Room (home). Now: on 60%')
    assert 'set <0-100>  brightness' in text and 'Run one: device_action("home","pole-lamp","set","40")' in text
    text, ok = core.run('home', 'living-room')
    assert ok is True and 'living-room: the room Living Room' in text and 'arm-lamp=off, pole-lamp=on 60%' in text
    assert 'bug-zappers' not in text
    assert hub.ran == []


def test_a_big_house_is_told_shorter(hub):
    hub.house = [{'id': f'light.n{i}', 'name': f'Lamp number {i:03} of the house', 'kind': 'light',
                  'room': f'Room {i % 12}'} for i in range(300)]
    core.status('home', fresh=True)
    text, _ = core.run('home')
    assert len(text) <= core.SCREEN and '  room-0: 25' in text and "A room's things by name" in text
    text, _ = core.run('home', 'room-0')
    assert text.count('lamp-number-') == 26                     # the room's 25, and the example call


def test_an_offline_hub_is_not_asked_how_its_things_are(hub):
    with patch.object(health, 'view', lambda i: {'online': False, 'checking': False, 'ts': 1.0, 'misses': 2,
                                                 'parts': []}):
        text, ok = core.run('home')
    assert ok is True and 'pole-lamp' in text and '=on 60%' not in text and 'Offline' in text


# --- what she may use --------------------------------------------------------

def test_a_kind_can_start_locked_and_the_tick_is_the_users(hub):
    assert [t['id'] for t in core.things(core.get('home')) if t['locked']] == ['lock.front']
    text, ok = core.run('home', 'front-door', 'unlock')
    assert ok is False and "'front-door' on 'home' is locked" in text and hub.ran == []
    assert 'Locked: the user has not allowed you this' in core.run('home', 'front-door')[0]
    assert core.run('home', 'front-door', 'unlock', owner=True)[1] is True           # the user's own button
    core.update('home', allow={'lock.front': True, 'light.pole': False, 'light.nosuch': False})
    part = core.get('home')['parts'][0]
    assert part['allow'] == {'lock.front': True, 'light.pole': False}
    assert core.run('home', 'front-door', 'unlock')[1] is True
    assert core.run('home', 'pole-lamp', 'on')[1] is False
    hub.ran.clear()
    core.run('home', 'living-room', 'on')                       # a room leaves out what she may not use
    assert hub.ran == [('light', 'on', '', ['light.arm'], 'Living Room')]


def test_ticks_outlive_a_settings_save_and_a_thing_that_went_away(hub):
    core.update('home', allow={'light.pole': False})
    core.update('home', parts={'fakehub': {'url': 'http://other'}})
    hub.house = [t for t in HOUSE if t['id'] != 'light.pole']
    core.status('home', fresh=True)
    hub.house = [dict(t) for t in HOUSE]
    core.status('home', fresh=True)                             # back again: still not hers
    assert next(t for t in core.things(core.get('home')) if t['id'] == 'light.pole')['locked'] is True


def test_a_hubs_kinds_carry_no_switch_of_their_own(hub):
    caps = core.describe(core.get('home'))
    assert [c['capability'] for c in caps] == ['light', 'switch', 'lock', 'scene']
    assert all(c['things'] and not c['lockable'] and not c['locked'] for c in caps)
    assert core.fleet()[0]['caps'] == ['light', 'switch', 'lock', 'scene']        # kinds, never 44 names


# --- the page and the scheduler ----------------------------------------------

def test_the_page_reads_things_with_what_they_do_and_saves_ticks(hub):
    view = routes.get_device('home')['device']
    assert [t['call'] for t in view['things']][:2] == ['arm-lamp', 'hall-light']
    now = {t['id']: t['now'] for t in routes.things_now('home')['things']}
    assert now['light.pole'] == 'on 60%' and now['switch.zap'] == ''
    assert 'tok-secret-long' not in str(now)                    # a driver that leaks is scrubbed
    routes.update_device('home', {'allow': {'light.arm': False}})
    assert core.get('home')['parts'][0]['allow'] == {'light.arm': False}
    assert routes.run_action('home', {'capability': 'arm-lamp', 'action': 'on'})['ok'] is True   # the user's button


def test_a_device_task_may_name_a_thing_or_a_room(hub):
    from core.continuity.scheduler import ContinuityScheduler as S
    ok = S._check_device_action({'device_action': {'device': 'home', 'capability': 'light.pole', 'action': 'set',
                                                   'value': '30'}})
    assert ok == {'device': 'home', 'capability': 'light.pole', 'action': 'set', 'value': '30'}
    assert S._check_device_action({'device_action': {'device': 'home', 'capability': 'living-room',
                                                     'action': 'off'}})['action'] == 'off'
    for bad in ({'capability': 'light', 'action': 'on'}, {'capability': 'nope', 'action': 'on'},
                {'capability': 'bug-zappers', 'action': 'set'}):
        with pytest.raises(ValueError):
            S._check_device_action({'device_action': dict(bad, device='home')})
