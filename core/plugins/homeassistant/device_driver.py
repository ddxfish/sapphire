# core/plugins/homeassistant/device_driver.py - a Home Assistant as a device (docs/plugin-author/devices.md)
#
# A HUB: one device, and everything the house has sits behind it as a thing
# (a light, a switch, a scene, the thermostat). The capabilities here are the
# KINDS of thing. Sapphire names a thing, or a room, where a capability would
# stand: device_action("home", "pole-lamp", "set", "40"). Core resolves the
# name and hands the thing over in device['things']; a room arrives as its
# lights. Which things she may use is the user's tick on the device's Things
# tab, kept by core. Locks, covers, alarms, cameras and phones start unticked.
#
# status() lists the things (asked again every FRESH seconds, and after a
# save). states() says what each is doing, asked when she looks.
#
# Home Assistant's REST door only. One request lists every state, one names
# every thing's room.
import base64
import json
import logging
import re
import time

import requests

from core import net

logger = logging.getLogger(__name__)

QUICK = 10            # seconds for a question
SLOW = 20             # seconds for an order, or a camera picture
FRESH = 120           # seconds the list of things is kept before it is asked for again
TEMPS = {'warm': 2700, 'neutral': 4000, 'cool': 5500, 'daylight': 6500}

_ONOFF = {'on': {'help': 'switch on', 'example': ''}, 'off': {'help': 'switch off', 'example': ''},
          'toggle': {'help': 'the other way from how it is now', 'example': ''}}
_LEVEL = '<0-100>'

# kind -> the Home Assistant domains it is made of, and what she reads. In the order that matters:
# when a house has more than core keeps, the last kinds are the ones left out.
KINDS = {
    'light': {'domains': ('light',), 'label': 'Lights', 'help': 'switch, dim and color', 'actions': dict(_ONOFF, set={
        'help': 'brightness 0-100, or a step like +20. A color name, warm, cool, daylight or 2700k '
                'may follow, or stand alone',
        'values': '<0-100|+N|-N> [color]', 'example': '40'})},
    'switch': {'domains': ('switch', 'input_boolean'), 'label': 'Switches', 'help': 'on and off', 'actions': _ONOFF},
    'climate': {'domains': ('climate',), 'label': 'Climate', 'help': 'the thermostat', 'actions': {
        'set': {'help': 'the temperature it aims for. Two numbers are a low and a high',
                'values': '<temperature>', 'example': '72'},
        'mode': {'help': 'heat, cool, off, or another mode it has', 'values': '<mode>', 'example': 'cool'}}},
    'scene': {'domains': ('scene',), 'label': 'Scenes', 'help': 'a saved look for the house',
              'actions': {'run': {'help': 'switch to it', 'example': ''}}},
    'script': {'domains': ('script',), 'label': 'Scripts', 'help': 'a saved set of steps',
               'actions': {'run': {'help': 'start it', 'example': ''}}},
    'cover': {'domains': ('cover',), 'label': 'Covers', 'help': 'blinds, shades, garage doors', 'actions': {
        'open': {'help': 'open it', 'example': ''}, 'close': {'help': 'close it', 'example': ''},
        'stop': {'help': 'stop it where it is', 'example': ''},
        'set': {'help': 'how far open, 100 is fully open', 'values': _LEVEL, 'example': '50'}}},
    'lock': {'domains': ('lock',), 'label': 'Locks', 'help': 'door locks', 'actions': {
        'lock': {'help': 'lock it', 'example': ''}, 'unlock': {'help': 'unlock it', 'example': ''}}},
    'fan': {'domains': ('fan',), 'label': 'Fans', 'help': 'on, off and speed', 'actions': dict(_ONOFF, set={
        'help': 'speed', 'values': _LEVEL, 'example': '50'})},
    'media': {'domains': ('media_player',), 'label': 'Media players', 'help': 'what is playing', 'actions': {
        'play': {'help': 'play', 'example': ''}, 'pause': {'help': 'pause', 'example': ''},
        'stop': {'help': 'stop', 'example': ''}, 'next': {'help': 'the next track', 'example': ''},
        'previous': {'help': 'the track before', 'example': ''},
        'volume': {'help': 'how loud', 'values': _LEVEL, 'example': '30'}}},
    'camera': {'domains': ('camera',), 'label': 'Cameras', 'help': 'see what it sees',
               'actions': {'look': {'help': 'take one picture and see it', 'example': '', 'wait': 30}}},
    'alarm': {'domains': ('alarm_control_panel',), 'label': 'Alarms', 'help': 'the alarm panel', 'actions': {
        'arm': {'help': 'arm it: away when left out', 'values': '[home|away|night]', 'example': 'home'},
        'disarm': {'help': 'disarm it, with its code if it asks for one', 'values': '[code]', 'example': ''}}},
    'sensor': {'domains': ('sensor', 'binary_sensor', 'person'), 'label': 'Sensors',
               'help': 'temperature, doors, motion, who is home',
               'actions': {'read': {'help': 'what it reads now', 'example': ''}}},
    'notify': {'domains': (), 'label': 'Notify', 'help': 'a notification on a phone', 'actions': {
        'ping': {'help': 'show these words on it', 'values': '<words>', 'example': 'Dinner is ready'}}},
}
_KIND_OF = {d: k for k, v in KINDS.items() for d in v['domains']}
_ORDER = {k: i for i, k in enumerate(KINDS)}
# A house has hundreds of readings (power, energy, signal). These are the ones worth a line.
_READ = {'sensor': ('temperature', 'humidity', 'illuminance', 'carbon_dioxide', 'pm25', 'aqi'),
         'binary_sensor': ('door', 'window', 'opening', 'garage_door', 'lock', 'motion', 'occupancy', 'presence',
                           'smoke', 'gas', 'carbon_monoxide', 'moisture')}
_BINARY = dict.fromkeys(('door', 'window', 'opening', 'garage_door'), ('open', 'closed'))
_BINARY.update(dict.fromkeys(('motion', 'occupancy', 'presence', 'smoke', 'gas', 'carbon_monoxide', 'moisture'),
                             ('detected', 'clear')), lock=('unlocked', 'locked'))
# services that take no value: (kind, action) -> domain.service
_PLAIN = {('scene', 'run'): 'scene.turn_on', ('script', 'run'): 'script.turn_on',
          ('cover', 'open'): 'cover.open_cover', ('cover', 'close'): 'cover.close_cover',
          ('cover', 'stop'): 'cover.stop_cover', ('lock', 'lock'): 'lock.lock', ('lock', 'unlock'): 'lock.unlock',
          ('media', 'play'): 'media_player.media_play', ('media', 'pause'): 'media_player.media_pause',
          ('media', 'stop'): 'media_player.media_stop', ('media', 'next'): 'media_player.media_next_track',
          ('media', 'previous'): 'media_player.media_previous_track'}
_SKIP_NOTIFY = ('notify', 'send_message', 'persistent_notification')
# every thing's room in one request: [[entity id, room, hidden], ...]
_ROOMS = ("{% set ns = namespace(o=[]) %}{% for s in states %}"
          "{% set ns.o = ns.o + [[s.entity_id, area_name(s.entity_id) or '', is_hidden_entity(s.entity_id)]] %}"
          "{% endfor %}{{ ns.o | to_json }}")

_held = {}            # device id -> {'url', 'at', 'things', 'unit'}


class Problem(Exception):
    """A reason fit to show as it is."""


# --- the door ----------------------------------------------------------------

def _ask(config, secrets, method, path, body=None, timeout=QUICK, order=False):
    """One request to Home Assistant. order=True is a thing being told to do
    something: no answer in time does not mean it did not happen, and says so."""
    token = secrets.get('token')
    if not token:
        raise Problem("No access token is stored for this Home Assistant. Enter one in Settings > Devices.")
    try:
        r = net.request(method, config.get('url', '') + path, json=body, timeout=timeout,
                        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    except requests.exceptions.Timeout:
        raise Problem(f"Home Assistant took the order and did not answer in {timeout} s. It may still have "
                      "happened: look at the thing before sending it again." if order
                      else "Home Assistant did not answer in time.")
    except requests.exceptions.RequestException:
        raise Problem(f"Could not reach Home Assistant at {config.get('url')}.")
    if r.status_code == 401:
        raise Problem("Home Assistant refused the access token. Make a new one in its profile page and "
                      "enter it in Settings > Devices.")
    if r.status_code >= 400:
        try:
            why = str(r.json().get('message') or '')[:200]
        except Exception:
            why = ''
        raise Problem(f"Home Assistant answered HTTP {r.status_code}" + (f": {why}" if why else '.'))
    return r


def validate(config):
    url = str(config.get('url') or '').strip()
    if url and '://' not in url:
        url = 'http://' + url
    config['url'] = url = url.rstrip('/')
    if not re.fullmatch(r'https?://[^/\s]+', url):
        return config, "The address looks like http://homeassistant.local:8123."
    return config, ''


def apply(device, config, secrets):
    _held.pop(device['id'], None)          # saved: list its things afresh


# --- what the house has --------------------------------------------------------

def _gather(config, secrets):
    """Every thing worth naming: [{'id', 'name', 'kind', 'room'}]."""
    states = _ask(config, secrets, 'GET', '/api/states').json()
    room, hidden = {}, set()
    try:
        for eid, place, hid in json.loads(_ask(config, secrets, 'POST', '/api/template', {'template': _ROOMS}).text):
            room[eid] = place
            if hid:
                hidden.add(eid)
    except (Problem, ValueError, TypeError) as e:
        logger.warning(f"[HA] rooms could not be read, so things are listed without one: {e}")
    out = []
    for s in states:
        eid, attrs = str(s.get('entity_id') or ''), s.get('attributes') or {}
        domain = eid.split('.')[0]
        kind = _KIND_OF.get(domain)
        if not kind or eid in hidden or attrs.get('restored'):      # restored = its own device is gone
            continue
        if domain in _READ and attrs.get('device_class') not in _READ[domain]:
            continue
        out.append({'id': eid, 'name': str(attrs.get('friendly_name') or eid.split('.', 1)[-1]),
                    'kind': kind, 'room': room.get(eid, '')})
    try:
        for d in _ask(config, secrets, 'GET', '/api/services').json():
            for name in (d.get('services') or {}) if d.get('domain') == 'notify' else ():
                if name not in _SKIP_NOTIFY:
                    out.append({'id': f'notify.{name}', 'kind': 'notify', 'room': '',
                                'name': name.replace('mobile_app_', '').replace('_', ' ').title()})
    except Problem as e:
        logger.warning(f"[HA] phones to notify could not be listed: {e}")
    return sorted(out, key=lambda t: _ORDER[t['kind']])


def _words(s, unit=''):
    """What one thing is doing, in a few words. '' for a thing with nothing to say."""
    eid, state, a = str(s.get('entity_id') or ''), str(s.get('state') or ''), s.get('attributes') or {}
    domain = eid.split('.')[0]
    if domain in ('scene', 'script', 'camera'):
        return ''
    if state in ('unavailable', 'unknown', ''):
        return state or 'unknown'
    if domain == 'light':
        b = a.get('brightness')
        return f"on {max(1, round(b / 2.55))}%" if state == 'on' and b else state
    if domain == 'climate':
        now, goal = a.get('current_temperature'), a.get('temperature')
        if goal is None and a.get('target_temp_low') is not None:
            goal = f"{a['target_temp_low']}-{a.get('target_temp_high')}"
        return (state + (f", {now}{unit} now" if now is not None else '')
                + (f", set to {goal}" if goal is not None else ''))
    if domain == 'cover' and a.get('current_position') is not None:
        return f"{state} {a['current_position']}%"
    if domain == 'fan' and state == 'on' and a.get('percentage') is not None:
        return f"on {a['percentage']}%"
    if domain == 'media_player':
        vol = a.get('volume_level')
        return (state + (f": {str(a['media_title'])[:30]}" if a.get('media_title') else '')
                + (f", volume {round(vol * 100)}" if isinstance(vol, (int, float)) else ''))
    if domain == 'binary_sensor':
        return _BINARY.get(a.get('device_class'), ('on', 'off'))[0 if state == 'on' else 1]
    if domain == 'sensor':
        return f"{state} {a.get('unit_of_measurement') or ''}".strip()
    return state


# --- the driver --------------------------------------------------------------

def describe(device, config):
    return {k: {'label': v['label'], 'help': v['help'], 'actions': v['actions']} for k, v in KINDS.items()}


def status(device, config, secrets):
    try:
        info = _ask(config, secrets, 'GET', '/api/config').json()
    except Problem as e:
        return {'online': False, 'detail': str(e)}
    told = {'online': True, 'detail': f"Home Assistant {info.get('version') or ''} at "
                                      f"{config.get('url', '').split('://')[-1]}".replace('  ', ' ')}
    held = _held.get(device['id'])
    if not held or held['url'] != config.get('url') or time.monotonic() - held['at'] > FRESH:
        try:
            held = {'url': config.get('url'), 'at': time.monotonic(), 'things': _gather(config, secrets)}
        except (Problem, ValueError, TypeError, AttributeError) as e:
            told['detail'] += f". Its things could not be listed: {e}"
            return told
        _held[device['id']] = held
    held['unit'] = str((info.get('unit_system') or {}).get('temperature') or '')
    things = held['things']
    told.update(things=things, has=sorted({t['kind'] for t in things}),
                readings={'things': str(len(things)),
                          'rooms': str(len({t['room'] for t in things if t['room']}))})
    return told


def states(device, config, secrets):
    """{thing id: what it is doing now}, for core's screens and the Things tab."""
    unit = (_held.get(device['id']) or {}).get('unit', '')
    out = {}
    for s in _ask(config, secrets, 'GET', '/api/states').json():
        words = _words(s, unit)
        if words:
            out[s['entity_id']] = words
    return out


def _level(value, word):
    text = value.strip().rstrip('%')
    if not text.isdigit() or not 0 <= int(text) <= 100:
        raise Problem(f"{word}: the value is a number from 0 to 100, not '{value}'.")
    return int(text)


def _light(value):
    """'40', '+20', 'blue', '40 warm', '2700k', '#ff8800' -> what light.turn_on takes."""
    data, color = {}, []
    for w in value.lower().replace(',', ' ').split():
        w = w.rstrip('%')
        if re.fullmatch(r'[+-]\d{1,3}', w):
            data['brightness_step_pct'] = max(-100, min(100, int(w)))
        elif w.isdigit() and int(w) <= 100:
            data['brightness_pct'] = int(w)
        elif re.fullmatch(r'\d{4,5}k', w):
            data['color_temp_kelvin'] = int(w[:-1])
        elif w in TEMPS:
            data['color_temp_kelvin'] = TEMPS[w]
        elif re.fullmatch(r'#[0-9a-f]{6}', w):
            data['rgb_color'] = [int(w[i:i + 2], 16) for i in (1, 3, 5)]
        elif w.isalpha():
            color.append(w)
        else:
            data = {}
            break
    if 'color_temp_kelvin' in data and color == ['white']:      # "warm white" is a warmth, not a color
        color = []
    if color:
        data['color_name'] = ''.join(color)
    if not data:
        raise Problem("set: a brightness from 0 to 100, a step like +20 or -20, a color (blue, warm, cool, "
                      f"daylight, 2700k, #ff8800), or a brightness then a color. Not '{value}'.")
    return data


def _plan(kind, action, value):
    """(domain.service, its data) for one order. Raises Problem with the way to say it."""
    if (kind, action) in _PLAIN:
        return _PLAIN[(kind, action)], {}
    if action in _ONOFF and 'on' in KINDS[kind]['actions']:
        return 'homeassistant.' + ('toggle' if action == 'toggle' else 'turn_' + action), {}
    if (kind, action) == ('light', 'set'):
        data = _light(value)
        return ('light.turn_off', {}) if data == {'brightness_pct': 0} else ('light.turn_on', data)
    if (kind, action) == ('fan', 'set'):
        return 'fan.set_percentage', {'percentage': _level(value, 'set')}
    if (kind, action) == ('cover', 'set'):
        return 'cover.set_cover_position', {'position': _level(value, 'set')}
    if (kind, action) == ('media', 'volume'):
        return 'media_player.volume_set', {'volume_level': _level(value, 'volume') / 100}
    if (kind, action) == ('climate', 'set'):
        nums = [float(n) for n in re.findall(r'\d+(?:\.\d+)?', value)]
        if len(nums) == 1:
            return 'climate.set_temperature', {'temperature': nums[0]}
        if len(nums) == 2:
            return 'climate.set_temperature', {'target_temp_low': min(nums), 'target_temp_high': max(nums)}
        raise Problem(f"set: the value is a temperature like 72, or a low and a high like 68 74. Not '{value}'.")
    if (kind, action) == ('climate', 'mode'):
        if not value.strip():
            raise Problem("mode: the value is the mode, like heat, cool or off.")
        return 'climate.set_hvac_mode', {'hvac_mode': value.strip().lower()}
    if (kind, action) == ('alarm', 'arm'):
        how = value.strip().lower() or 'away'
        if how not in ('home', 'away', 'night'):
            raise Problem(f"arm: home, away or night. Not '{value}'.")
        return f'alarm_control_panel.alarm_arm_{how}', {}
    if (kind, action) == ('alarm', 'disarm'):
        return 'alarm_control_panel.alarm_disarm', ({'code': value.strip()} if value.strip() else {})
    raise Problem(f"A {kind} has no {action}.")


def _told(config, secrets, who, things, action, value, answer, unit):
    """(text, ok): what an order came to. Home Assistant answers with the
    states that changed while it waited; a thing that answers later is only
    said as sent. It also takes an order for a thing it cannot reach and does
    nothing, so a thing that did not change is looked at: unreachable is a
    failure, never "sent"."""
    try:
        changed = {s['entity_id']: s for s in answer.json() if isinstance(s, dict) and s.get('entity_id')}
    except (ValueError, TypeError, AttributeError):
        changed = {}
    gone = []
    if any(t['id'] not in changed for t in things):
        try:
            now = {s.get('entity_id'): s.get('state') for s in _ask(config, secrets, 'GET', '/api/states').json()}
            gone = [t for t in things if t['id'] not in changed and now.get(t['id']) == 'unavailable']
        except (Problem, ValueError, TypeError, AttributeError):
            pass                                # it cannot be looked at: what was sent is still what is said
    order = f"{action} {value}".strip()
    if len(gone) == len(things):
        return (f"{who} is not answering Home Assistant right now (unavailable), so nothing happened. "
                "It may be unplugged or out of reach."), False
    if len(things) == 1:
        words = _words(changed[things[0]['id']], unit) if things[0]['id'] in changed else ''
        return (f"{who}: {words}." if words else f"{who}: {order} sent."), True
    names = ', '.join(t['call'] for t in things[:8] if t not in gone) + (' ...' if len(things) > 8 else '')
    away = f" Not answering, so left as they were: {', '.join(t['call'] for t in gone)}." if gone else ''
    n = len(things) - len(gone)
    return f"{who}: {order} sent to {n} {things[0]['kind']}{'s' if n != 1 else ''} ({names}).{away}", True


def run(device, capability, action, value, config, secrets, call_tool):
    things = device.get('things') or []
    ids = [t['id'] for t in things]
    value = str(value or '').strip()
    unit = (_held.get(device['id']) or {}).get('unit', '')
    try:
        if capability not in KINDS or not ids:
            raise Problem("Name one of its things where the capability stands.")
        who = device.get('room') or things[0]['name']
        if capability == 'camera':
            r = _ask(config, secrets, 'GET', f"/api/camera_proxy/{ids[0]}", timeout=SLOW)
            kind = r.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip().lower()
            return {'text': f"A picture from the camera {who}.",
                    'images': [{'data': base64.b64encode(r.content).decode(), 'media_type': kind}]}, True
        if capability == 'sensor':
            return '\n'.join(f"{t['name']}: " + (_words(_ask(config, secrets, 'GET', f"/api/states/{t['id']}").json(),
                                                        unit) or 'no reading') for t in things), True
        if capability == 'notify':
            if not value:
                raise Problem("ping: the value is the words to show. Example: Dinner is ready")
            _ask(config, secrets, 'POST', f"/api/services/notify/{ids[0].split('.', 1)[-1]}",
                 {'message': value[:1000]}, timeout=SLOW, order=True)
            return f"Shown on {who}: {value[:120]}", True
        service, data = _plan(capability, action, value)
        answer = _ask(config, secrets, 'POST', '/api/services/' + service.replace('.', '/'),
                      dict(data, entity_id=ids), timeout=SLOW, order=True)
        return _told(config, secrets, who, things, action, value, answer, unit)
    except Problem as e:
        return str(e), False
