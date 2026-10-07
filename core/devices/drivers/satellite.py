# core/devices/drivers/satellite.py - a satellite (tmp/device-manager-plan.md)
#
# A small box in another room with a microphone, a speaker and a light: a
# Raspberry Pi body, an ESP32 board. It runs its own program and is driven
# over plain HTTP on the local network with a bearer key. What a board has to
# speak is written down in docs/SATELLITE-PROTOCOL.md.
#
# Two directions:
#   Sapphire to the satellite  - this driver: say, sound, listen, light, wake,
#                                look through its camera, and keep backups on
#                                its card (storage, 2026-10-06: the board is a
#                                backup target of core/backup_targets)
#   the satellite to Sapphire  - core/devices/voice.py: it heard its wake word
#                                and posts what was said
#
# A satellite says what it is in GET /health: "has" (its capabilities) and
# "plays" (the one sound format it plays). One that says neither is an early
# Pi body: it has everything, and plays the audio as the voice engine made it.
#
# The key travels without encryption, so a satellite has to be on the local
# network. That is checked when the device is saved.
import hashlib
import io
import re
import threading
import time
from urllib.parse import urlsplit

import requests

from core import net
from core.backup_targets import Target
from core.devices.storage import KEEP_FIELDS, keep_from

SPEC = {
    'label': 'Satellite (a board that speaks the protocol)',
    'icon': '\U0001f4e1',
    'capabilities': ['speaker', 'mic', 'light', 'wake', 'camera', 'power', 'storage', 'screen', 'keyboard',
                     'sensors'],
    # it calls Sapphire with its own key (wake, voice, text, events): the
    # address it calls from becomes its url, so a board set up from the
    # browser needs none typed, and a new DHCP lease is followed
    'learns_address': True,
    'config_schema': [
        {'key': 'url', 'type': 'string', 'label': 'Address', 'tab': 'Status', 'setup': True,
         'placeholder': 'http://192.168.1.100:8090',
         'help': "Where Sapphire reaches it. Leave it empty and it is learned the first time "
                 "the satellite calls in; it follows the satellite when its address changes."},
        {'key': 'token', 'type': 'string', 'widget': 'password', 'secret': True, 'tab': 'Status', 'setup': True,
         'label': 'Key Sapphire sends',
         'help': "The satellite's own key (SAPPH_BODY_TOKEN on a Pi body). Stored scrambled."},
        {'key': 'camera', 'type': 'boolean', 'label': 'Has a camera', 'tab': 'Status', 'default': True,
         'capability': 'camera',
         'help': "Off = Sapphire is not offered a camera on this satellite."},
        # the mic's fields, or the keyboard's on a board that types instead of listening
        {'key': 'chat', 'type': 'string', 'label': 'Talks in chat', 'capability': ('mic', 'keyboard'),
         'help': "The chat this satellite's questions land in. Empty = the last chat used."},
        {'key': 'voice_key', 'type': 'string', 'widget': 'password', 'secret': True, 'setup': True,
         'capability': ('mic', 'keyboard'), 'label': 'Key the satellite sends',
         'help': "Proves a question came from this satellite. Stored scrambled. On a Pi "
                 "body this is SAPPH_BRAIN_TOKEN, next to SAPPH_DEVICE_ID."},
        # the looks: what the ring shows in each state, in the words of `light set`
        {'key': 'look_resting', 'type': 'string', 'label': 'Resting', 'capability': 'light',
         'default': 'sapphire heartbeat bpm=33 ceiling=0.1',
         'help': 'What the ring shows when nothing is going on. Color, pattern, bpm, floor, ceiling.'},
        {'key': 'look_listening', 'type': 'string', 'label': 'Listening', 'capability': 'light',
         'default': 'yellow spin'},
        {'key': 'look_thinking', 'type': 'string', 'label': 'Thinking', 'capability': 'light',
         'default': 'rainbow spin'},
        {'key': 'look_tool', 'type': 'string', 'label': 'Using a tool', 'capability': 'light',
         'default': 'purple pulse',
         'help': 'While she looks something up or works a device, between thinking and speaking.'},
        {'key': 'look_speaking', 'type': 'string', 'label': 'Speaking', 'capability': 'light',
         'default': 'cyan solid'},
        {'key': 'look_nolink', 'type': 'string', 'label': 'No link to Sapphire', 'capability': 'light',
         'default': 'red pulse'},
        {'key': 'lights_from', 'type': 'string', 'label': 'Lights on from', 'capability': 'light',
         'default': '08:00', 'placeholder': '08:00',
         'help': 'Outside these hours the ring shows the look below instead of resting. '
                 'Both empty = always on. Listening, thinking and speaking show at any hour, '
                 'and so does what she sets.'},
        {'key': 'lights_until', 'type': 'string', 'label': 'until', 'capability': 'light',
         'default': '00:00', 'placeholder': '00:00'},
        {'key': 'look_night', 'type': 'string', 'label': 'Outside those hours', 'capability': 'light',
         'default': 'off', 'help': 'off = dark. Or something soft: sapphire pulse ceiling=0.05'},
        *KEEP_FIELDS,
    ],
}

LOOKS = ('resting', 'listening', 'thinking', 'tool', 'speaking', 'nolink', 'night')
INBOUND = ('keyboard',)       # the board sends, Sapphire asks nothing of it: core/devices/voice.py typed()
_CLOCK = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')
_SECONDS = re.compile(r'^seconds=(\d{1,4})$', re.I)

WAITING = ("Waiting for the satellite to call in: its address is learned from that. "
           "Or type its address in Settings > Devices.")
QUICK = 8                     # seconds for a plain request
ABOUT_FRESH = 60              # seconds what a satellite said about itself is taken as true
SPEAK_WAIT = 150              # the satellite answers only when it has finished playing
LOOK_WAIT = 25                # a picture: 2 s of warning light and sound, then the shot
STORE_WAIT = 120              # one blocked send or the answer after a 70 MB backup onto a card
_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,96}$')
_DURATION = re.compile(r'^(\d+(?:\.\d+)?)(s|m|h)$', re.I)
_KNOB = re.compile(r'^(bpm|speed|floor|ceiling)=(.+)$', re.I)
_SPEEDS = ('slow', 'normal', 'fast')
_HEX = re.compile(r'^#?([0-9a-f]{6}|[0-9a-f]{3})$', re.I)
_UNIT = {'s': 1, 'm': 60, 'h': 3600}

_lock = threading.Lock()
_about = {}                   # device id -> (when, what its /health said)
_palettes = {}                # device id -> (when, its colors and animations, from /led/spec)
SPEC_FRESH = 3600             # seconds a device's palette is kept: it changes with its firmware, not its mood


class Problem(Exception):
    """A reason fit to show as it is."""


class Unfit(Problem):
    """Sound this satellite cannot play, by the format it stated."""


class Missing(Problem):
    """The satellite has no such address: its program is older than the ask."""


# --- talking to it -----------------------------------------------------------

def validate(config):
    url = str(config.get('url') or '').strip().rstrip('/')
    if url:
        if '://' not in url:
            url = 'http://' + url
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return config, f"'{url}' is not a usable address. Example: http://192.168.1.100:8090"
        if net.classify(parts.hostname) != 'lan':
            return config, ("A satellite has to be on your own network. Its key travels "
                            "without encryption, so an internet address is refused.")
        url = f"{parts.scheme}://{parts.netloc}"
    config['url'] = url                       # '' = learned when it calls in (engine.learned)
    config['chat'] = str(config.get('chat') or '').strip()[:64]
    for name in LOOKS:
        text = str(config.get(f'look_{name}') or '').strip()
        try:
            words = parse_light(text)
        except Problem as e:
            return config, f"{name.capitalize()}: {e}"
        if 'duration_s' in words:
            return config, f"{name.capitalize()}: a look has no time. Example: yellow spin bpm=60"
        config[f'look_{name}'] = text
    for key in ('lights_from', 'lights_until'):
        text = str(config.get(key) or '').strip()
        if text and not _CLOCK.match(text):
            return config, f"Lights on from and until are clock times like 07:00 and 23:00, not '{text}'."
        config[key] = text
    return config, ''


def apply(device, config, secrets):
    """After a save: the looks and the hours go to the board. An early Pi
    body (before 0.7.0) has no such door and keeps its own; that is not a fault."""
    body = {name: parse_light(config.get(f'look_{name}')) for name in LOOKS}
    body = {k: v for k, v in body.items() if v}
    body['from'] = str(config.get('lights_from') or '')
    body['until'] = str(config.get('lights_until') or '')
    try:
        _call('PUT', '/led/looks', config, secrets, json=body)
    except Missing:
        return
    except Problem as e:
        raise _engine_error(str(e))


def _engine_error(text):
    from core.devices.engine import DeviceError
    return DeviceError(text)


def _call(method, path, config, secrets, timeout=QUICK, headers=None, **kw):
    key = secrets.get('token')
    if not key:
        raise Problem("No key is stored for this satellite. Enter it in Settings > Devices.")
    base = str(config.get('url') or '')
    if not base:
        raise Problem(WAITING)
    where = urlsplit(base).netloc or base
    try:
        r = net.request(method, base + path, timeout=timeout,
                        headers=dict(headers or {}, Authorization='Bearer ' + key), **kw)
    except requests.exceptions.Timeout:
        raise Problem(f"No answer from {where} within {timeout}s.")
    except requests.exceptions.RequestException:
        raise Problem(f"Could not reach {where}. Check that it is powered and on the network.")
    if r.status_code in (401, 403):
        raise Problem("The satellite refused the key. Enter its key again in Settings > Devices.")
    if r.status_code >= 400:
        try:
            said = r.json().get('detail')
        except ValueError:
            said = None
        text = str(said or f"The satellite answered HTTP {r.status_code}.")[:300]
        raise Missing(text) if r.status_code == 404 else Problem(text)
    return r


def _json(r):
    try:
        data = r.json()
    except ValueError:
        raise Problem("The satellite answered, but not in a form I can read.")
    return data if isinstance(data, dict) else {'value': data}


def _health(device, config, secrets, fresh=False):
    """What the satellite says about itself. Asked again after a minute."""
    with _lock:
        when, said = _about.get(device['id'], (0, None))
    if fresh or said is None or time.monotonic() - when > ABOUT_FRESH:
        with _lock:
            _about.pop(device['id'], None)       # no answer = nothing is known
        said = _json(_call('GET', '/health', config, secrets))
        with _lock:
            _about[device['id']] = (time.monotonic(), said)
    return said


# --- reading her words -------------------------------------------------------

def _knob(name, text):
    """One fine setting of the ring, as the satellite wants it."""
    if name == 'speed':
        if text not in _SPEEDS:
            raise Problem(f"speed is one of {', '.join(_SPEEDS)}, not '{text}'.")
        return text
    try:
        n = float(text)
    except ValueError:
        raise Problem(f"{name} has to be a number, not '{text}'.")
    if name == 'bpm':
        return int(max(10, min(120, n)))
    return max(0.0, min(1.0, n))                  # floor and ceiling: brightness, 0 to 1


def _palette(device, config, secrets):
    """The device's own colors and animations (its /led/spec), kept an hour.
    {} when it cannot be asked: parse_light then falls back to word order."""
    with _lock:
        when, said = _palettes.get(device['id'], (0, None))
    if said is not None and time.monotonic() - when < SPEC_FRESH:
        return said
    try:
        s = _json(_call('GET', '/led/spec', config, secrets))
    except Problem:
        return {}
    anims = s.get('animations')
    said = {'colors': [str(c).lower() for c in list(s.get('colors') or []) + list(s.get('color_aliases') or [])],
            'animations': [str(a).lower() for a in (anims if isinstance(anims, (dict, list)) else [])]}
    with _lock:
        _palettes[device['id']] = (time.monotonic(), said)
    return said


def parse_light(value, palette=None):
    """'cyan blink 5s' -> {'color': 'cyan', 'animation': 'blink', 'duration_s': 5.0}
    Plain words are the color, then the animation. A color may be two words:
    soft blue is the device's soft_blue (Sapphire wrote it that way, 2026-10-03,
    and the old parser blamed the animation). With the device's own palette
    every word is checked, and a wrong one is named with the choices. Fine
    settings are written name=value: bpm=40 speed=fast floor=0.05 ceiling=0.4"""
    body, words = {}, []
    for tok in str(value or '').split():
        low = tok.lower()
        m = _DURATION.match(low)
        k = _KNOB.match(low)
        if k:
            body[k.group(1)] = _knob(k.group(1), k.group(2))
        elif m:
            body['duration_s'] = float(m.group(1)) * _UNIT[m.group(2)]
        elif _HEX.match(low) and (low.startswith('#') or len(low) == 6):
            h = _HEX.match(low).group(1)
            h = h if len(h) == 6 else ''.join(c * 2 for c in h)
            body['r'], body['g'], body['b'] = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        else:
            words.append(low)
    have_color = 'r' in body
    colors = list((palette or {}).get('colors') or [])
    anims = list((palette or {}).get('animations') or [])
    if anims:                                   # the device says what an animation is
        picked = [w for w in words if w in anims]
        rest = [w for w in words if w not in anims]
        menu = (f"Colors: {', '.join(colors)}, or #hex like #ff8800. " if colors else '') \
            + f"Animations: {', '.join(anims)}."
        if len(picked) > 1:
            raise Problem(f"One animation at a time, not {' and '.join(picked)}.")
        if rest and have_color:
            raise Problem(f"I did not understand '{' '.join(rest)}': the color is already given as hex. {menu}")
        if rest and colors and '_'.join(rest) not in colors:
            raise Problem(f"I did not understand '{' '.join(rest)}'. {menu}")
        if rest:
            body['color'] = '_'.join(rest)
        if picked:
            body['animation'] = picked[0]
        return body
    if len(words) > 3 or (have_color and len(words) > 1):   # no palette: by word order
        raise Problem(f"I did not understand '{' '.join(words[1 if have_color else 3:])}'. Example: cyan blink 5s")
    if have_color:
        if words:
            body['animation'] = words[0]
    elif len(words) == 1:
        body['color'] = words[0]
    elif words:
        body['color'], body['animation'] = '_'.join(words[:-1]), words[-1]
    return body


def _span(seconds):
    """A length of time in the two largest units: 3d 4h, 2h 5m, 40s."""
    s = max(0, int(seconds))
    for size, big, small, per in ((86400, 'd', 'h', 3600), (3600, 'h', 'm', 60), (60, 'm', 's', 1)):
        if s >= size:
            return f"{s // size}{big} {(s % size) // per}{small}"
    return f"{s}s"


def _ring_words(led):
    """A ring state or a resting light, in a few words."""
    color = led.get('color')
    if isinstance(color, (list, tuple)) and len(color) == 3:
        color = '#%02x%02x%02x' % tuple(int(c) for c in color)
    bits = [str(color or 'its color'), str(led.get('animation') or 'solid')]
    for name in ('bpm', 'floor', 'ceiling'):
        if led.get(name) is not None:
            bits.append(f"{name}={led[name]:g}" if isinstance(led[name], float) else f"{name}={led[name]}")
    return ' '.join(bits)


def _whole(value, default, lo, hi, what):
    text = str(value or '').strip()
    if not text:
        return default
    try:
        n = int(float(text.rstrip('sS')))
    except ValueError:
        raise Problem(f"{what} has to be a number of seconds, not '{text}'.")
    return max(lo, min(hi, n))


# --- the driver --------------------------------------------------------------

def describe(device, config):
    told = {
        'speaker': {'label': 'Speaker', 'help': 'speak in that room', 'actions': {
            'say': {'help': 'say it out loud there, in your voice', 'example': 'Dinner is ready',
                    'values': '<text>'},
            'stop': {'help': 'stop what you are saying there', 'example': '', 'values': '(no value)'},
            'sound': {'help': 'play a stored sound. No value lists them', 'example': '',
                      'values': '[name]'},
            'volume': {'help': 'set it, or step it. No value reads it', 'example': '80',
                       'values': '[0-100 | up | down]'},
        }},
        'mic': {'label': 'Mic', 'help': 'hear that room', 'actions': {
            'listen': {'help': 'longest wait in seconds, ends when they stop talking', 'example': '10',
                       'values': '[seconds]'},
            'gain': {'help': 'the microphones\' gain in dB; no value reads it. A board without the door says so',
                     'example': '36', 'values': '[0-37.5 | up | down]'},
            'agc': {'help': 'automatic gain control on the board; the board restarts to apply it',
                    'example': 'on', 'values': '[on | off]'},
        }},
        'light': {'label': 'Light', 'help': 'the light ring', 'actions': {
            'set': {'help': 'color: a name (red, cyan, sapphire, amber, white...) or #hex. animation: solid, '
                            'blink, breathe, heartbeat, rotate, wave, spin, rain... No time = 5 minutes',
                    'example': 'cyan blink 5s',
                    'values': '<color> [animation] [5s|2m] [bpm= speed= floor= ceiling=]'},
            'clear': {'help': 'back to its resting light', 'example': ''},
            'off': {'help': 'ring dark', 'example': ''},
            'rest': {'help': 'its resting light, kept after a restart. No value reads it',
                     'example': 'sapphire heartbeat bpm=33', 'values': '[color animation bpm= floor= ceiling=]'},
            'options': {'help': "this device's full list of colors and animations", 'example': ''},
        }},
        'wake': {'label': 'Wake word', 'help': 'listening for its wake word', 'actions': {
            'read': {'help': 'is it listening', 'example': ''},
            'on': {'help': 'listen for the wake word', 'example': ''},
            'off': {'help': 'stop listening for it', 'example': ''},
        }},
        'sensors': {'label': 'Sensors', 'help': 'what the board measures: a light sensor, a module for '
                    'temperature or humidity. A name ends in its unit (temp_c, humidity_pct)', 'actions': {
            'read': {'help': 'every sensor, measured now', 'example': ''},
        }},
    }
    told['power'] = {'label': 'Power', 'help': 'restart it or shut it down', 'actions': {
        'restart': {'help': 'restart the whole satellite. Back in about a minute', 'example': ''},
        'shutdown': {'help': 'switch it off. Someone must unplug and replug it to bring it back',
                     'example': ''},
    }}
    told['screen'] = {'label': 'Screen', 'help': 'its small screen', 'actions': {
        'show': {'help': 'a line across the top of its screen for a while. seconds= how long, 20 if left out',
                 'example': 'Dinner in ten minutes', 'values': '<text> [seconds=20]'},
        'clear': {'help': 'take the line down', 'example': ''},
        'picture': {'help': 'an image on the whole screen until a tap, or seconds= (60 if left out). '
                            'An image handle like img:ab12 (the receipt any image tool gives you), doc:N from the '
                            'library, or last = the newest image in this chat. A landscape image is turned sideways',
                    'example': 'last seconds=120', 'values': '<img:id | doc:N | last> [seconds=60]'},
    }}
    told['keyboard'] = {'label': 'Keyboard', 'help': 'what is typed on it lands in your chat; your reply is shown on its screen',
                        'actions': {}}
    if config.get('camera', True):
        told['camera'] = {'label': 'Camera', 'help': 'see that room', 'actions': {
            'look': {'help': 'take one picture and see it. The ring warns the room first',
                     'example': ''},
        }}
    told['storage'] = {'label': 'Backup', 'help': 'sealed copies of her memory on its card', 'actions': {
        'backup': {'help': 'back everything up onto its card now, sealed with the backup password',
                   'example': ''},
        'list': {'help': 'what is on the card and how much room is left', 'example': ''},
    }}
    if 'format' in _can(device):
        told['storage']['actions']['format'] = {
            'help': 'wipe the card and format it FAT32', 'example': '', 'owner': True,
            'danger': f"Formats the card in {device['id']}. Every backup on it is erased."}
    return told


def _can(device):
    """What the board's last /health said its storage can do beyond the
    doors every board has (`storage: {can: [...]}`): 'format' on the ESP32.
    Read from the health cache; describe() has no key to ask with."""
    with _lock:
        _, said = _about.get(device['id'], (0, None))
    st = (said or {}).get('storage') if isinstance(said, dict) else None
    can = st.get('can') if isinstance(st, dict) else None
    return [str(c) for c in can] if isinstance(can, list) else []


def status(device, config, secrets):
    try:
        h = _health(device, config, secrets, fresh=True)
    except Problem as e:
        return {'online': False, 'detail': str(e)}
    readings = {}
    if h.get('firmware'):
        readings['program'] = ' '.join(str(x) for x in (h.get('board'), h['firmware']) if x)[:60]
    if isinstance(h.get('volume'), (int, float)):
        readings['volume'] = f"{int(h['volume'])}%"
    mic = h.get('mic') if isinstance(h.get('mic'), dict) else {}
    if mic:
        readings['mic gain'] = f"{mic.get('gain_db')} dB" + (', AGC on' if mic.get('agc') else '')
    if isinstance(h.get('uptime_s'), (int, float)):
        readings['running for'] = _span(h['uptime_s'])
    if h.get('temp_c') is not None:
        readings['temperature'] = f"{h['temp_c']}C"
    wake = h.get('wakeword') if isinstance(h.get('wakeword'), dict) else {}
    if wake:
        readings['wake word'] = ('listening' if wake.get('enabled') and wake.get('running') else 'off') \
            + (f" for {wake['model']}" if wake.get('model') else '')
    led = h.get('led') if isinstance(h.get('led'), dict) else {}
    if led:
        readings['light'] = 'dark' if led.get('blackout') else f"{led.get('state', '?')}, {led.get('animation', '?')}"
    link = next((h[k] for k in ('link', 'brain_events') if isinstance(h.get(k), dict)), {})
    if link:
        readings['link to Sapphire'] = 'connected' if link.get('connected') else 'NOT connected'
    if isinstance(h.get('free_internal_kb'), (int, float)):      # the RAM TLS needs; a board says it from 0.2.1
        readings['free RAM'] = f"{int(h['free_internal_kb'])} KB internal"
    for name, value in (h.get('sensors') or {}).items() if isinstance(h.get('sensors'), dict) else ():
        if isinstance(value, (int, float, str)):             # its sensors: name -> number (pocket 0.2.0)
            readings[str(name)[:24]] = str(value)[:24]
    st = h.get('storage') if isinstance(h.get('storage'), dict) else {}
    if isinstance(st.get('free_bytes'), (int, float)):
        from core.devices.storage import _gb
        readings['card'] = f"{_gb(st['free_bytes'])} free" + \
            (f" of {_gb(st['total_bytes'])}" if st.get('total_bytes') else '')
    elif st.get('mounted') is False:                              # a slot with no card in it (pocket 0.2.0)
        readings['card'] = 'none: put one in (FAT32) and restart the board'
    where = urlsplit(str(config.get('url') or '')).netloc
    out = {'online': bool(h.get('ok', True)), 'readings': readings,
           'detail': f"{h.get('name') or h.get('body_name') or device['id']} at {where}"}
    if isinstance(h.get('has'), list):           # the engine keeps it with the device
        out['has'] = h['has']
    return out


def play(audio, kind, device, config, secrets):
    """Sound on the satellite, answered when it has finished playing: what the
    satellite said back ({'ok', 'stopped'?}). A board that states the format
    it plays gets the sound itself, fitted; one that does not gets the file
    as it was made. Raises Unfit when the sound cannot be made to fit, Problem
    when the satellite could not play it. Core's voice lane calls this once
    per sentence as her reply is made."""
    from core.devices import voice
    plays = _health(device, config, secrets).get('plays')
    seconds = None
    if plays is not None:
        audio, kind = voice.fit(audio, kind, plays)
        if audio is None:
            raise Unfit(kind)
        seconds = round(max(0, len(audio) - 44) / (plays['rate'] * plays['channels'] * 2), 2)
        r = _call('POST', '/audio/speak', config, secrets, timeout=SPEAK_WAIT, data=audio,
                  headers={'Content-Type': kind})
    else:
        ext = {'audio/ogg': 'ogg', 'audio/wav': 'wav', 'audio/mpeg': 'mp3', 'audio/mp3': 'mp3'}.get(kind, 'ogg')
        r = _call('POST', '/audio/speak', config, secrets, timeout=SPEAK_WAIT,
                  files={'audio': (f'speech.{ext}', audio, kind)})
    try:
        said = r.json()
    except ValueError:
        said = {}
    said = said if isinstance(said, dict) else {}
    if seconds is not None:
        said.setdefault('seconds', seconds)          # of sound, as fitted: what a log can time against
    return said


PICTURE_WAIT = 60             # a 300 KB picture over WiFi to a small board, painted as it lands


def _seconds_and_rest(value, default):
    """'last seconds=120' -> ('last', 120)."""
    words, seconds = [], default
    for tok in str(value or '').split():
        m = _SECONDS.match(tok)
        if m:
            seconds = max(1, min(3600, int(m.group(1))))
        else:
            words.append(tok)
    return ' '.join(words).strip(), seconds


def rgb565(raw, width, height):
    """An image as the bytes a small panel paints, RGB565 big-endian, fitted
    inside width x height: (bytes, w, h). A landscape image on a portrait
    screen is turned a quarter turn so it uses the glass; the board centres
    what it gets on black."""
    import numpy as np
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    if (img.width > img.height) != (width > height):
        img = img.transpose(Image.Transpose.ROTATE_90)
    img.thumbnail((width, height), Image.Resampling.LANCZOS)
    a = np.asarray(img, dtype=np.uint16)
    packed = (a[..., 0] >> 3) << 11 | (a[..., 1] >> 2) << 5 | (a[..., 2] >> 3)
    return packed.astype('>u2').tobytes(), img.width, img.height


def _picture(value, device, config, secrets):
    from core import images
    source, seconds = _seconds_and_rest(value, 60)
    if not source or source == 'last':
        found = images.last_image_id()
        if not found:
            return "There is no image in this chat yet. Make or find one first, then say picture last.", True
        source = f'img:{found}'
    try:
        got = images.resolve(source)
    except Exception as e:
        return f"I could not open {source}: {str(e)[:160]}", False
    screen = _health(device, config, secrets).get('screen')
    if not isinstance(screen, dict) or not screen.get('w') or not screen.get('h'):
        return "This board's program has no picture door. Update it.", False
    try:
        data, w, h = rgb565(got.data, int(screen['w']), int(screen['h']))
    except Exception as e:
        return f"That image could not be made to fit the screen ({type(e).__name__}).", False
    _call('POST', f'/screen/picture?w={w}&h={h}&seconds={seconds}', config, secrets, timeout=PICTURE_WAIT,
          data=data, headers={'Content-Type': 'application/octet-stream'})
    return (f'On the screen of {device["id"]}: {got.label} ({w}x{h}), for {seconds} seconds or until a tap.'), True


def _say(text, device, config, secrets):
    from core.devices import voice
    text = str(text or '').strip()
    if not text:
        return "say: the value is what to say. Example: Dinner is ready", True
    audio, kind = voice.render(text)             # first: a refused voice asks the satellite nothing
    if audio is None:
        return f"Nothing was said: {kind}", False
    try:
        play(audio, kind, device, config, secrets)
    except Unfit as e:
        return f"Nothing was said: {e}", False
    short = text if len(text) <= 80 else text[:77] + '...'
    return f'Said there: "{short}"', True


def _stop(device, config, secrets):
    """Stop her voice there: core drops the sentences still to be sent
    (voice.halt), the satellite cuts what is playing (POST /audio/stop). A
    program without that door is said so; its current sentence ends itself."""
    from core.devices import voice
    more = voice.halt(device['id'])
    try:
        out = _json(_call('POST', '/audio/stop', config, secrets))
    except Missing:
        return ("Stopped: nothing more will be said there. The sentence playing now ends by itself; "
                "this satellite's program has no stop door yet."), True
    except Problem as e:
        return (f"Stopped what was still to come, but the satellite could not be told: {e}"), more
    if out.get('stopped'):
        return "Stopped.", True
    return "Stopped what was still to come; nothing was playing there." if more else "Nothing was playing there.", True


VOLUME_STEP = 10


def _volume(value, config, secrets):
    """Read, set, or step the speaker's volume. A satellite whose program
    has no volume door is told so, plainly."""
    want = str(value or '').strip().lower()
    try:
        if not want or want in ('up', 'down'):
            now = int(_json(_call('GET', '/volume', config, secrets)).get('volume', 0))
            if not want:
                return f"Volume is {now}%.", True
            level = max(0, min(100, now + (VOLUME_STEP if want == 'up' else -VOLUME_STEP)))
        else:
            try:
                level = max(0, min(100, int(float(want))))
            except ValueError:
                return "volume: a number from 0 to 100, or up, down. Example: 80", False
        out = _json(_call('POST', f'/volume?level={level}', config, secrets))
        return f"Volume is now {int(out.get('volume', level))}%.", True
    except Missing:
        return "This satellite's program has no volume control.", False


def _mic_gain(value, config, secrets):
    """Read, set, or step the microphones' gain (dB). Bodies without a /mic door say so."""
    want = str(value or '').strip().lower()
    try:
        if not want or want in ('up', 'down'):
            now = _json(_call('GET', '/mic', config, secrets))
            if not want:
                return f"Mic gain is {now.get('gain_db')} dB of {now.get('gain_max_db')}; AGC {'on' if now.get('agc') else 'off'}.", True
            db = float(now.get('gain_db', 0)) + (3 if want == 'up' else -3)
        else:
            try:
                db = float(want)
            except ValueError:
                return "mic gain: a number of decibels, or up, down. Example: 36", False
        out = _json(_call('POST', f'/mic?gain={max(0.0, db):g}', config, secrets))
        return f"Mic gain is now {out.get('gain_db')} dB (of {out.get('gain_max_db')}).", True
    except Missing:
        return "This satellite's program has no mic gain door.", False


def _mic_agc(value, config, secrets):
    want = str(value or '').strip().lower()
    if want not in ('on', 'off'):
        return "mic agc: on or off", False
    try:
        out = _json(_call('POST', f'/mic?agc={want}', config, secrets))
        return f"AGC {'on' if out.get('agc') else 'off'}." + (f" {out['note']}." if out.get('note') else ''), True
    except Missing:
        return "This satellite's program has no AGC door.", False


def _listen(value, config, secrets):
    from core.devices import voice
    seconds = _whole(value, 15, 1, 60, 'The wait')
    gate = voice.stt_refusal()
    if gate:
        return f"Not listening: {gate}", False
    r = _call('GET', f'/audio/listen?vad=true&max_seconds={seconds}', config, secrets,
              timeout=seconds + 12)
    heard, problem = voice.transcribe(r.content)
    if problem:
        return problem, False
    if not heard:
        return "Listened, and heard no speech.", True
    return f'Heard: "{heard}"', True


def put_model(device, config, secrets, name, data, fmt, threshold, phrase=None, sliding_window=None, step_ms=None):
    """A new wake word model onto the board (PUT /wakeword/model, the bytes as the body): what the Wakeword Maker's
    Install page sends. `fmt` is 'tflite' (microWakeWord) or 'onnx' (openWakeWord). Raises Missing when the board's
    program has no such door; the caller then tells the person to copy the files by hand."""
    from urllib.parse import urlencode
    q = {'name': name, 'threshold': threshold, 'format': fmt, 'phrase': phrase or name}
    if sliding_window is not None:
        q['sliding_window'] = sliding_window
    if step_ms is not None:
        q['step_ms'] = step_ms
    r = _call('PUT', '/wakeword/model?' + urlencode(q), config, secrets, timeout=180,
              headers={'Content-Type': 'application/octet-stream'}, data=data)
    with _lock:
        _about.pop(device['id'], None)        # its health names the new model from now on
    return _json(r)


# --- backups on its card -----------------------------------------------------

class SatelliteTarget(Target):
    """The board's card as a place backups go: four doors, PUT with the bytes
    as the body. remote=True is the class rule: nothing plaintext goes here,
    whatever any setting says; put() checks the magic itself (GATE 3) and
    the board checks it again (GATE 4)."""
    kind = 'satellite'
    remote = True

    def __init__(self, device, config, secrets):
        self.label = device['id']
        self.keep = keep_from(config)
        self._config, self._secrets = config, secrets

    def info(self):
        return _json(_call('GET', '/storage', self._config, self._secrets))

    def names(self):
        return list(self.sizes())

    def sizes(self):
        files = self.info().get('files') or []
        return {str(f.get('name')): int(f.get('size') or 0) for f in files if isinstance(f, dict) and f.get('name')}

    def put(self, path, name):
        self.check(path, name)
        if not _NAME.match(name):
            raise Problem(f"'{name}' is not a usable file name for the card.")
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        sha = h.hexdigest()
        with open(path, 'rb') as f:
            r = _call('PUT', f'/storage/{name}', self._config, self._secrets, timeout=STORE_WAIT,
                      headers={'Content-Type': 'application/octet-stream', 'X-Sha256': sha}, data=f)
        told = _json(r)
        if told.get('sha256') and str(told['sha256']).lower() != sha:
            try:
                self.delete(name)
            finally:
                raise Problem(f"The copy on {self.label} does not match what was sent (sha256).")

    def delete(self, name):
        if _NAME.match(name):
            _call('DELETE', f'/storage/{name}', self._config, self._secrets)

    def get(self, name, dst):
        if not _NAME.match(name):
            raise Problem(f"'{name}' is not a usable file name.")
        r = _call('GET', f'/storage/{name}', self._config, self._secrets, timeout=STORE_WAIT, stream=True)
        with open(dst, 'wb') as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)


def storage_target(device, config, secrets):
    """Core's door (core/devices/storage.py): this board as a backup target."""
    return SatelliteTarget(device, config, secrets)


def run(device, capability, action, value, config, secrets, call_tool):
    try:
        if capability == 'storage':
            from core.devices import storage as st
            target = SatelliteTarget(device, config, secrets)
            if action == 'backup':
                return st.backup_now(target)
            if action == 'list':
                try:
                    info = target.info()
                except Missing:
                    return "This satellite's program has no backup door. Update it.", False
                return st.listing_text(target, info), True
            if action == 'format':
                if 'format' not in _can(device):
                    return "This satellite cannot format its card.", False
                _json(_call('POST', '/storage/format', config, secrets, timeout=STORE_WAIT))
                with _lock:
                    _about.pop(device['id'], None)
                return f"The card in {device['id']} is formatted (FAT32) and empty.", True
        if capability == 'speaker':
            if action == 'say':
                return _say(value, device, config, secrets)
            if action == 'stop':
                return _stop(device, config, secrets)
            if action == 'volume':
                return _volume(value, config, secrets)
            if action == 'sound':
                name = str(value or '').strip()
                if not name:
                    names = _json(_call('GET', '/sounds', config, secrets)).get('sounds') or []
                    names = [n.get('name') if isinstance(n, dict) else str(n) for n in names]
                    return ('Sounds: ' + ', '.join(sorted(n for n in names if n))) if names \
                        else 'No sounds are stored on this satellite.', True
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name):
                    return f"'{name}' is not a usable sound name.", False
                _call('POST', f'/audio/effect?name={name}', config, secrets, timeout=60)
                return f"Played {name}.", True
        if capability == 'mic' and action == 'listen':
            return _listen(value, config, secrets)
        if capability == 'mic' and action == 'gain':
            return _mic_gain(value, config, secrets)
        if capability == 'mic' and action == 'agc':
            return _mic_agc(value, config, secrets)
        if capability == 'light':
            if action == 'off':
                _call('POST', '/led', config, secrets, json={'state': 'off'})
                return 'Ring dark.', True
            if action == 'clear':
                _call('POST', '/led', config, secrets, json={'state': 'idle'})
                return 'Back to its resting light.', True
            if action == 'rest':
                body = parse_light(value, _palette(device, config, secrets) if str(value or '').strip() else None)
                if not body:
                    now = _json(_call('GET', '/led/baseline', config, secrets)).get('baseline') or {}
                    return (f"Its resting light is: {_ring_words(now)}. Nothing was changed. "
                            "To change it, give a value. Example: sapphire heartbeat bpm=33"), True
                if 'duration_s' in body or 'speed' in body:
                    return "A resting light has no time and no speed. Example: sapphire heartbeat bpm=33", False
                out = _json(_call('PUT', '/led/baseline', config, secrets, json=body))
                return (f"Resting light is now: {_ring_words(out.get('baseline') or body)}. "
                        "Kept after a restart."), True
            if action == 'options':
                s = _json(_call('GET', '/led/spec', config, secrets))
                anims = s.get('animations')
                colors = list(s.get('colors') or []) + list(s.get('color_aliases') or [])
                lines = [f"Colors: {', '.join(colors)}. Any hex color works too, like #ff8800."]
                if isinstance(anims, dict):
                    lines.append('Animations:')
                    lines += [f"  {name}: {str(what)[:90]}" for name, what in anims.items()]
                elif isinstance(anims, list):
                    lines.append(f"Animations: {', '.join(str(a) for a in anims)}.")
                lines.append("Fine settings, written name=value: bpm=10 to 120, speed=slow, normal or fast, "
                             "floor and ceiling=0 to 1 (lowest and highest brightness).")
                if isinstance(s.get('baseline_now'), dict):
                    hours = ' It is night there, so the night light shows.' if s.get('is_night') else ''
                    lines.append(f"Its resting light is: {_ring_words(s['baseline_now'])}.{hours}")
                return '\n'.join(lines), True
            if action == 'set':
                body = parse_light(value, _palette(device, config, secrets) if str(value or '').strip() else None)
                if not body:
                    return ("set: a color, then an animation, then a time like 5s or 2m. "
                            "With no time it holds for 5 minutes. 'options' lists the colors and "
                            "animations. Fine settings: bpm=40 speed=fast floor=0.05 ceiling=0.4. "
                            "Example: cyan blink 5s"), True
                body.setdefault('animation', 'solid')
                body.setdefault('duration_s', 300.0)
                out = _json(_call('POST', '/led', config, secrets, json=body))
                led = out.get('led') if isinstance(out.get('led'), dict) else out
                held = f", {body['duration_s']:g}s" if 'duration_s' in body else ''
                return (f"Ring: {body.get('color') or 'custom color'}, "
                        f"{led.get('animation') or body['animation']}{held}."), True
        if capability == 'wake':
            if action in ('on', 'off'):
                _call('POST', f"/wakeword?enabled={'true' if action == 'on' else 'false'}", config, secrets)
                return ('Listening for the wake word.' if action == 'on'
                        else 'No longer listening for the wake word.'), True
            if action == 'read':
                w = _json(_call('GET', '/wakeword', config, secrets))
                state = 'listening' if w.get('enabled') and w.get('running') else 'not listening'
                return f"It is {state}" + (f" for {w['model']}." if w.get('model') else '.'), True
        if capability == 'sensors' and action == 'read':
            try:
                got = _json(_call('GET', '/sensors', config, secrets)).get('sensors')
            except Missing:                                   # an older board: what /health last said
                got = (_health(device, config, secrets, fresh=True) or {}).get('sensors')
            if not isinstance(got, dict) or not got:
                return "No sensor answered.", False
            return ' · '.join(f"{k}: {v}" for k, v in got.items()), True
        if capability == 'power' and action in ('restart', 'shutdown'):
            try:
                out = _json(_call('POST', f'/power?action={action}', config, secrets))
            except Missing:
                return "This satellite's program is too old to restart or shut down. Update it.", False
            wait = f" in {out['in_s']:g} seconds" if isinstance(out.get('in_s'), (int, float)) else ''
            if action == 'restart':
                return f"{device['id']} is restarting{wait}. It will be back in about a minute.", True
            return (f"{device['id']} is shutting down{wait}. To bring it back, someone has to "
                    "unplug its power and plug it in again."), True
        if capability == 'screen':
            if action == 'clear':
                _call('POST', '/screen', config, secrets, json={'clear': True})
                return 'The line is down.', True
            if action == 'picture':
                return _picture(value, device, config, secrets)
            if action == 'show':
                words, seconds = [], 20
                for tok in str(value or '').split():
                    m = _SECONDS.match(tok)
                    if m:
                        seconds = max(1, min(3600, int(m.group(1))))
                    else:
                        words.append(tok)
                text = ' '.join(words)
                if not text:
                    return ("show: the text, then seconds= for how long (20 if left out). The screen is "
                            "small: a few short lines. Example: Dinner in ten minutes seconds=60"), True
                out = _json(_call('POST', '/screen', config, secrets, json={'text': text[:300], 'seconds': seconds}))
                shown = out.get('seconds', seconds)
                cut = ' It was cut to 300 characters.' if len(text) > 300 else ''
                return f'On the screen of {device["id"]} for {shown:g} seconds: "{text[:300]}".{cut}', True
        if capability == 'camera' and action == 'look':
            if not config.get('camera', True):
                return "This satellite has no camera.", False
            shot = _json(_call('GET', '/camera/snap?b64=true', config, secrets, timeout=LOOK_WAIT))
            if not shot.get('data_b64'):
                return "The camera gave no picture.", False
            where = f" in {device['location']}" if device.get('location') else ''
            size = f", {shot['width']}x{shot['height']}" if shot.get('width') and shot.get('height') else ''
            return {'text': f"A picture from the camera of {device['id']}{where}{size}.",
                    'images': [{'data': shot['data_b64'], 'media_type': 'image/jpeg'}]}, True
    except Problem as e:
        return str(e), False
    return f"A satellite has no {capability} / {action}.", False
