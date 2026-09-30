# core/devices/drivers/satellite.py - a satellite (tmp/device-manager-plan.md)
#
# A small box in another room with a microphone, a speaker and a light: a
# Raspberry Pi body, an ESP32 board. It runs its own program and is driven
# over plain HTTP on the local network with a bearer key. What a board has to
# speak is written down in docs/SATELLITE-PROTOCOL.md.
#
# Two directions:
#   Sapphire to the satellite  - this driver: say, sound, listen, light, wake,
#                                and look through its camera
#   the satellite to Sapphire  - core/devices/voice.py: it heard its wake word
#                                and posts what was said
#
# A satellite says what it is in GET /health: "has" (its capabilities) and
# "plays" (the one sound format it plays). One that says neither is an early
# Pi body: it has everything, and plays the audio as the voice engine made it.
#
# The key travels without encryption, so a satellite has to be on the local
# network. That is checked when the device is saved.
import re
import threading
import time
from urllib.parse import urlsplit

import requests

from core import net

SPEC = {
    'label': 'Satellite (mic, speaker, light)',
    'icon': '\U0001f4e1',
    'capabilities': ['speaker', 'mic', 'light', 'wake', 'camera', 'power'],
    'config_schema': [
        {'key': 'url', 'type': 'string', 'label': 'Address', 'tab': 'Status',
         'placeholder': 'http://192.168.0.221:8090'},
        {'key': 'token', 'type': 'string', 'widget': 'password', 'secret': True, 'tab': 'Status',
         'label': 'Key Sapphire sends',
         'help': "The satellite's own key (SAPPH_BODY_TOKEN on a Pi body). Stored scrambled."},
        {'key': 'camera', 'type': 'boolean', 'label': 'Has a camera', 'tab': 'Status', 'default': True,
         'capability': 'camera',
         'help': "Off = Sapphire is not offered a camera on this satellite."},
        {'key': 'chat', 'type': 'string', 'label': 'Talks in chat', 'capability': 'mic',
         'help': "The chat this satellite's questions land in. Empty = the last chat used."},
        {'key': 'voice_key', 'type': 'string', 'widget': 'password', 'secret': True,
         'capability': 'mic', 'label': 'Key the satellite sends',
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
        {'key': 'look_speaking', 'type': 'string', 'label': 'Speaking', 'capability': 'light',
         'default': 'green spin'},
        {'key': 'look_nolink', 'type': 'string', 'label': 'No link to Sapphire', 'capability': 'light',
         'default': 'red pulse'},
        {'key': 'lights_from', 'type': 'string', 'label': 'Lights on from', 'capability': 'light',
         'placeholder': '07:00', 'help': 'Outside these hours the ring rests dark. Both empty = always on. '
                                          'A state still shows, and so does what she sets.'},
        {'key': 'lights_until', 'type': 'string', 'label': 'until', 'capability': 'light',
         'placeholder': '23:00'},
    ],
}

LOOKS = ('resting', 'listening', 'thinking', 'speaking', 'nolink')
_CLOCK = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')

QUICK = 8                     # seconds for a plain request
ABOUT_FRESH = 60              # seconds what a satellite said about itself is taken as true
SPEAK_WAIT = 150              # the satellite answers only when it has finished playing
LOOK_WAIT = 25                # a picture: 2 s of warning light and sound, then the shot
_DURATION = re.compile(r'^(\d+(?:\.\d+)?)(s|m|h)$', re.I)
_KNOB = re.compile(r'^(bpm|speed|floor|ceiling)=(.+)$', re.I)
_SPEEDS = ('slow', 'normal', 'fast')
_HEX = re.compile(r'^#?([0-9a-f]{6}|[0-9a-f]{3})$', re.I)
_UNIT = {'s': 1, 'm': 60, 'h': 3600}

_lock = threading.Lock()
_about = {}                   # device id -> (when, what its /health said)


class Problem(Exception):
    """A reason fit to show as it is."""


class Missing(Problem):
    """The satellite has no such address: its program is older than the ask."""


# --- talking to it -----------------------------------------------------------

def validate(config):
    url = str(config.get('url') or '').strip().rstrip('/')
    if not url:
        return config, "The satellite's address is needed."
    if '://' not in url:
        url = 'http://' + url
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return config, f"'{url}' is not a usable address. Example: http://192.168.0.221:8090"
    if net.classify(parts.hostname) != 'lan':
        return config, ("A satellite has to be on your own network. Its key travels "
                        "without encryption, so an internet address is refused.")
    config['url'] = f"{parts.scheme}://{parts.netloc}"
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
    """After a save: the looks and the hours go to the board. A Pi body has
    no such door and keeps its own; that is not a fault."""
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
    where = urlsplit(base).netloc or base
    try:
        r = net.request(method, base + path, timeout=timeout,
                        headers=dict(headers or {}, Authorization='Bearer ' + key), **kw)
    except requests.exceptions.Timeout:
        raise Problem(f"No answer from {where} within {timeout}s.")
    except requests.exceptions.RequestException:
        raise Problem(f"Could not reach {where}. Is the satellite powered and on the network?")
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


def parse_light(value):
    """'cyan blink 5s' -> {'color': 'cyan', 'animation': 'blink', 'duration_s': 5.0}
    The first plain word is the color, the second the animation. Fine
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
    if len(words) > 2:
        raise Problem(f"I did not understand '{' '.join(words[2:])}'. Example: cyan blink 5s")
    have_color = 'r' in body
    if words and not have_color:
        body['color'] = words.pop(0)
    if words:
        body['animation'] = words.pop(0)
    if words:
        raise Problem(f"I did not understand '{' '.join(words)}'. Example: cyan blink 5s")
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
            'say': {'help': 'say it out loud there', 'example': 'Dinner is ready'},
            'sound': {'help': 'play a stored sound, no value lists them', 'example': ''},
            'volume': {'help': '0 to 100, or up, down. No value reads it', 'example': '80'},
        }},
        'mic': {'label': 'Mic', 'help': 'hear that room', 'actions': {
            'listen': {'help': 'longest wait in seconds, ends when they stop', 'example': '10'},
        }},
        'light': {'label': 'Light', 'help': 'the light ring', 'actions': {
            'set': {'help': 'color, animation, time', 'example': 'cyan blink 5s'},
            'clear': {'help': 'back to its resting light', 'example': ''},
            'off': {'help': 'ring dark', 'example': ''},
            'rest': {'help': 'its resting light, kept after a restart. No value reads it',
                     'example': 'sapphire heartbeat bpm=33'},
            'options': {'help': 'its colors and animations', 'example': ''},
        }},
        'wake': {'label': 'Wake word', 'help': 'listening for its wake word', 'actions': {
            'read': {'help': 'is it listening', 'example': ''},
            'on': {'help': 'listen for the wake word', 'example': ''},
            'off': {'help': 'stop listening for it', 'example': ''},
        }},
    }
    told['power'] = {'label': 'Power', 'help': 'restart it or shut it down', 'actions': {
        'restart': {'help': 'restart the whole satellite. Back in about a minute', 'example': ''},
        'shutdown': {'help': 'switch it off. Someone must unplug and replug it to bring it back',
                     'example': ''},
    }}
    if config.get('camera', True):
        told['camera'] = {'label': 'Camera', 'help': 'see that room', 'actions': {
            'look': {'help': 'take one picture and see it. The ring warns the room first',
                     'example': ''},
        }}
    return told


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
    where = urlsplit(str(config.get('url') or '')).netloc
    out = {'online': bool(h.get('ok', True)), 'readings': readings,
           'detail': f"{h.get('name') or h.get('body_name') or device['id']} at {where}"}
    if isinstance(h.get('has'), list):           # the engine keeps it with the device
        out['has'] = h['has']
    return out


def _say(text, device, config, secrets):
    from core.devices import voice
    text = str(text or '').strip()
    if not text:
        return "say: the value is what to say. Example: Dinner is ready", True
    audio, kind = voice.render(text)             # first: a refused voice asks the satellite nothing
    if audio is None:
        return f"Nothing was said: {kind}", False
    plays = _health(device, config, secrets).get('plays')
    if plays is not None:     # a board that states its format gets the sound itself, no form around it
        audio, kind = voice.fit(audio, kind, plays)
        if audio is None:
            return f"Nothing was said: {kind}", False
        _call('POST', '/audio/speak', config, secrets, timeout=SPEAK_WAIT, data=audio,
              headers={'Content-Type': kind})
    else:
        ext = {'audio/ogg': 'ogg', 'audio/wav': 'wav', 'audio/mpeg': 'mp3', 'audio/mp3': 'mp3'}.get(kind, 'ogg')
        _call('POST', '/audio/speak', config, secrets, timeout=SPEAK_WAIT,
              files={'audio': (f'speech.{ext}', audio, kind)})
    short = text if len(text) <= 80 else text[:77] + '...'
    return f'Said there: "{short}"', True


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


def run(device, capability, action, value, config, secrets, call_tool):
    try:
        if capability == 'speaker':
            if action == 'say':
                return _say(value, device, config, secrets)
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
        if capability == 'light':
            if action == 'off':
                _call('POST', '/led', config, secrets, json={'state': 'off'})
                return 'Ring dark.', True
            if action == 'clear':
                _call('POST', '/led', config, secrets, json={'state': 'idle'})
                return 'Back to its resting light.', True
            if action == 'rest':
                body = parse_light(value)
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
                body = parse_light(value)
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
