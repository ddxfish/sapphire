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
import base64
import hashlib
import io
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from core import net
from core.backup_targets import Target
from core.devices.storage import KEEP_FIELDS, keep_from

logger = logging.getLogger(__name__)

SPEC = {
    'label': 'Satellite (a board that speaks the protocol)',
    'kind': 'Satellite',
    'icon': '\U0001f4e1',
    'capabilities': ['speaker', 'mic', 'light', 'wake', 'camera', 'power', 'storage', 'screen', 'keyboard',
                     'buttons', 'sensors', 'firmware', 'ir'],
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
        # the chat this device is in, for everything it has that talks or shows: on the first tab (2026-10-09)
        {'key': 'chat', 'type': 'string', 'label': 'Talks in chat', 'tab': 'Status',
         'capability': ('mic', 'keyboard', 'buttons', 'screen'),
         'help': "The chat this device is in: what it hears, what is typed on it and what its buttons send "
                 "land there, and its screen shows it. Empty = the last chat used. A name that is not a "
                 "chat yet is made when you save."},
        # a reader's picker: every open chat, or the one above only (Krem's vote 2026-10-16: off by default,
        # so a board's key reaches no chat but its own)
        {'key': 'browse', 'type': 'boolean', 'label': 'Browse chats', 'tab': 'Status', 'default': False,
         'capability': 'keyboard',
         'help': "Its picker lists every open chat and it may read any of them back. Off = only the chat "
                 "above (the active one when that is empty): the board's key reaches nothing else."},
        {'key': 'camera', 'type': 'boolean', 'label': 'Has a camera', 'tab': 'Status', 'default': True,
         'capability': 'camera',
         'help': "Off = Sapphire is not offered a camera on this satellite."},
        {'key': 'camera_turn', 'type': 'select', 'label': 'Turn its pictures', 'default': '0',
         'capability': 'camera',
         'options': [{'value': '0', 'label': 'not at all'}, {'value': '90', 'label': '90 degrees to the right'},
                     {'value': '180', 'label': '180 degrees'}, {'value': '270', 'label': '90 degrees to the left'}],
         'help': "For a camera mounted on its side or upside down: each picture is turned this way before she sees it."},
        {'key': 'ota', 'type': 'boolean', 'label': 'Updates over the air', 'tab': 'Status', 'default': True,
         'capability': 'firmware',
         'help': "Off = no program is ever sent to this board from here; its Firmware tab only checks. "
                 "A board with one program slot never offers this and is updated over USB."},
        # the mic's field, or the keyboard's on a board that types instead of listening
        {'key': 'voice_key', 'type': 'string', 'widget': 'password', 'secret': True, 'setup': True,
         'capability': ('mic', 'keyboard', 'buttons'), 'label': 'Key the satellite sends',
         'help': "Proves a question came from this satellite. Stored scrambled. On a Pi "
                 "body this is SAPPH_BRAIN_TOKEN, next to SAPPH_DEVICE_ID."},
        # what each press of each button does: the board says which buttons it has and what it can do by
        # itself (/health `buttons`), bindings() below turns that into the menu the page draws
        {'key': 'buttons', 'type': 'bindings', 'label': 'Buttons', 'capability': 'buttons'},
        {'key': 'volume_step', 'takes': 'volume.step', 'type': 'select', 'label': 'A volume button changes it by',
         'capability': 'speaker', 'default': '10',
         'options': [{'value': '5', 'label': '5%'}, {'value': '10', 'label': '10%'}, {'value': '20', 'label': '20%'}],
         'help': "For a button set to Volume up or Volume down (Buttons tab), and for her own up and down."},
        {'key': 'question_max_s', 'takes': 'mic.max_s', 'type': 'number', 'label': 'Longest question (seconds)', 'capability': 'wake',
         'default': 30, 'min': 5, 'max': 60,
         'help': 'It stops listening when you go quiet. This is the cut-off for when you do not: '
                 'raise it if you talk for a long time in one go.'},
        # the wake word it listens for: a model from the library (core/wakeword/library.py), sent through
        # PUT /wakeword/model; the board restarts on it. Shown only on a board that states its model family
        # (/health wakeword.format: the program with the door). Its choices are this board's (options() below)
        {'key': 'wake_model', 'takes': 'wake.model', 'type': 'select', 'dynamic': True, 'label': 'Listens for',
         'capability': 'wake', 'default': 'keep',
         'help': "A wake word from the library (Settings > Wakeword). Save sends it to the board, which restarts on it, "
                 "about half a minute; a board that cannot be reached now is sent it when it is next seen. "
                 "'What it has now' leaves the board as it is; the board's own word is the one built into its program."},
        # the glass: its backlight is the board's biggest draw that can be chosen (firmware 0.5.5)
        {'key': 'brightness', 'takes': 'screen.brightness', 'type': 'number', 'label': 'Brightness in use', 'capability': 'screen',
         'default': 80, 'min': 0, 'max': 100,
         'help': 'Percent, while it is touched or she is listening, thinking or speaking.'},
        {'key': 'dim', 'takes': 'screen.dim', 'type': 'number', 'label': 'Brightness when left alone', 'capability': 'screen',
         'default': 15, 'min': 0, 'max': 100, 'help': 'Percent. A touch, or her stirring, brings it back.'},
        {'key': 'dim_after_s', 'takes': 'screen.dim_after_s', 'type': 'number', 'label': 'Dims after (seconds)', 'capability': 'screen',
         'default': 120, 'min': 0, 'max': 3600, 'help': '0 = never dims.'},
        {'key': 'off_after_min', 'takes': 'screen.off_after_min', 'type': 'number', 'label': 'Screen dark after (minutes)', 'capability': 'screen',
         'default': 0, 'min': 0, 'max': 1440,
         'help': '0 = never. The backlight goes out to save power; a touch or her stirring brings it back.'},
        {'key': 'rotation', 'takes': 'screen.rotation', 'type': 'select', 'label': 'Rotation', 'capability': 'screen', 'default': '0',
         'options': [{'value': '0', 'label': 'landscape'}, {'value': '90', 'label': 'portrait'},
                     {'value': '180', 'label': 'landscape, upside down'}, {'value': '270', 'label': 'portrait, the other way up'}],
         'help': "How the glass sits. The board restarts to draw that way (a CYD reader; other boards take Upside down)."},
        # a CYD glass comes in variants: these two put a wrong one right at once, no restart (firmware 0.5.1)
        {'key': 'invert', 'takes': 'screen.invert', 'type': 'boolean', 'label': 'Invert colors', 'capability': 'screen',
         'help': 'For a glass that shows the test card in negative: red as cyan, white as black. At once.'},
        {'key': 'mirror', 'takes': 'screen.mirror', 'type': 'boolean', 'label': 'Mirror image', 'capability': 'screen',
         'help': 'For a glass that scans the other way: the test card reads backwards. At once; its touch follows.'},
        {'key': 'flip', 'takes': 'screen.flip', 'type': 'boolean', 'label': 'Upside down', 'capability': 'screen',
         'help': 'Turns the picture and its touch 180 degrees, for a board mounted the other way up.'},
        {'key': 'home', 'takes': 'screen.home', 'type': 'select', 'label': 'Opens on', 'capability': 'screen', 'default': 'chat',
         'options': [{'value': 'chat', 'label': 'the chat'}, {'value': 'backups', 'label': 'Backups'}, {'value': 'status', 'label': 'Status'},
                     {'value': 'board', 'label': 'Board'}, {'value': 'actions', 'label': 'Actions'}],
         'help': 'The page the board shows when it starts (a board with pages, the reader).'},
        # the looks: what the ring shows in each state, in the words of `light set`
        {'key': 'look_resting', 'takes': 'look.resting', 'type': 'string', 'label': 'Resting', 'capability': 'light',
         'default': 'sapphire heartbeat bpm=33 ceiling=0.1',
         'help': 'What the ring shows when nothing is going on. Color, pattern, bpm, floor, ceiling.'},
        {'key': 'look_listening', 'takes': 'look.listening', 'type': 'string', 'label': 'Listening', 'capability': 'light',
         'default': 'yellow spin'},
        {'key': 'look_thinking', 'takes': 'look.thinking', 'type': 'string', 'label': 'Thinking', 'capability': 'light',
         'default': 'rainbow spin'},
        {'key': 'look_tool', 'takes': 'look.tool', 'type': 'string', 'label': 'Using a tool', 'capability': 'light',
         'default': 'purple pulse',
         'help': 'While she looks something up or works a device, between thinking and speaking.'},
        {'key': 'look_speaking', 'takes': 'look.speaking', 'type': 'string', 'label': 'Speaking', 'capability': 'light',
         'default': 'cyan solid'},
        {'key': 'look_nolink', 'takes': 'look.nolink', 'type': 'string', 'label': 'No link to Sapphire', 'capability': 'light',
         'default': 'red pulse'},
        {'key': 'lights_from', 'type': 'string', 'label': 'Lights on from', 'capability': 'light',
         'default': '08:00', 'placeholder': '08:00',
         'help': 'Outside these hours the ring shows the look below instead of resting. '
                 'Both empty = always on. Listening, thinking and speaking show at any hour, '
                 'and so does what she sets.'},
        {'key': 'lights_until', 'type': 'string', 'label': 'until', 'capability': 'light',
         'default': '00:00', 'placeholder': '00:00'},
        {'key': 'look_night', 'takes': 'look.night', 'type': 'string', 'label': 'Outside those hours', 'capability': 'light',
         'default': 'off', 'help': 'off = dark. Or something soft: sapphire pulse ceiling=0.05'},
        *KEEP_FIELDS,
    ],
}

SCREEN_KEYS = ('brightness', 'dim', 'dim_after_s', 'off_after_min')
LOOKS = ('resting', 'listening', 'thinking', 'tool', 'speaking', 'nolink', 'night')
INBOUND = ('keyboard', 'buttons')   # the board sends, Sapphire asks nothing of it: core/devices/voice.py typed(), pressed()
_CLOCK = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')
_SECONDS = re.compile(r'^seconds=(\d{1,4})$', re.I)
_HANDLE = re.compile(r'img:[A-Za-z0-9_.-]{1,72}|doc:\d{1,9}|last')   # what names an image to the picture action (an id may carry a .jpg)

WAITING = ("Waiting for the satellite to call in: its address is learned from that. "
           "Or type its address in Settings > Devices.")
QUICK = 8                     # seconds for a plain request
ABOUT_FRESH = 60              # seconds what a satellite said about itself is taken as true
SPEAK_WAIT = 150              # older boards answer when the sentence has played; 0.7.7+ when it is taken in
LOOK_WAIT = 25                # a picture: 2 s of warning light and sound, then the shot
ROOM_SPARE = 16 * 2**20       # the board wants this much left over (its own check before a PUT)
STORE_WAIT = 120              # one blocked send or the answer after a 70 MB backup onto a card
UPDATE_WAIT = 300             # a 2 MB program over WiFi, written and checked on the board
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
    from core.devices import voice
    config['chat'] = voice.chat_named(config.get('chat'))
    held = config.get('buttons') if isinstance(config.get('buttons'), dict) else {}
    for slot, pick in list(held.items()):
        if str(slot).rpartition('.')[2] not in voice.HOWS:
            held.pop(slot)
        elif pick.get('do') != voice.TELL:
            pick['text'] = ''                 # only a message to her has words
    config['buttons'] = held
    config['wake_model'] = _wish(config)      # keep | own | a library name
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
    """After a save: the settings to the board, the wake word last (the board restarts on a new one);
    the probe sends no wake word while this runs."""
    with _lock:
        _saving.add(device['id'])
    try:
        _apply(device, config, secrets)
        send_wake_model(device, config, secrets)
    finally:
        with _lock:
            _saving.discard(device['id'])


def _apply(device, config, secrets):
    """After a save: the looks and the hours go to the board. An early Pi
    body (before 0.7.0) has no such door and keeps its own; that is not a fault.
    First what needs no answer from the board: its chat is made when it is
    not one yet, and its stream is told what its buttons do now."""
    from core.devices import voice
    if config.get('chat'):
        try:
            voice.chat_for(config, device['id'])
        except Exception as e:                 # not a reason to hold the rest back: it is tried again when the device speaks
            logger.warning(f"[DEVICES] {device['id']}: its chat '{config['chat']}' could not be made now: {e}")
    jobs = voice.bound(device['id'])           # None: it has no buttons, and its program may not know the word
    if jobs is not None:
        labels = voice.labels(device['id'])
        voice.cue(device['id'], 'buttons', bound=jobs, **({'labels': labels} if labels else {}))
    body = {name: parse_light(config.get(f'look_{name}')) for name in LOOKS}
    body = {k: v for k, v in body.items() if v}
    body['from'] = str(config.get('lights_from') or '')
    body['until'] = str(config.get('lights_until') or '')
    try:
        _call('PUT', '/led/looks', config, secrets, json=body)
    except Missing:
        pass
    except Problem as e:
        raise _engine_error(str(e))
    longest = config.get('question_max_s')
    # only a board that states it (/health mic.max_s) is told: an older program refuses the word
    if isinstance(longest, (int, float)) and 'max_s' in (_health(device, config, secrets).get('mic') or {}):
        try:
            _call('POST', f"/mic?max_s={int(longest)}", config, secrets)
        except Problem as e:
            raise _engine_error(str(e))
    # only when it is set, and only a board that states it (0.7.0): an older program refuses the word
    if config.get('volume_step') and 'volume_step' in _health(device, config, secrets):
        try:
            _call('POST', f"/volume?step={_step(config)}", config, secrets)
        except Problem as e:
            raise _engine_error(str(e))
    screen = {k: int(config[k]) for k in SCREEN_KEYS if isinstance(config.get(k), (int, float))}
    for k in ('flip', 'invert', 'mirror'):
        if isinstance(config.get(k), bool):
            screen[k] = config[k]              # a board without the word (a CYD has no flip, the audio board no invert) ignores it
    if str(config.get('rotation', '')).isdigit():
        screen['rotation'] = int(config['rotation'])   # a board without it (one with `flip`) ignores the word
    if isinstance(config.get('home'), str) and config['home']:
        screen['home'] = config['home']        # a board without pages ignores the word
    if screen and 'screen' in (_health(device, config, secrets).get('has') or []):
        try:
            _call('PUT', '/screen/settings', config, secrets, json=screen)
        except Missing:
            pass                               # a program before 0.5.5 keeps its own
        except Problem as e:
            raise _engine_error(str(e))
    from core.devices import glass
    glass.sync_soon(device['id'])          # "Talks in chat" may have changed: its screen follows


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
        # Connection: close. A small board has a handful of sockets; kept open, ours were held on
        # it for good, and after it restarted or lost WiFi the next call met a dead one (scouts, 2026-10-11).
        r = net.request(method, base + path, timeout=timeout,
                        headers=dict(headers or {}, Authorization='Bearer ' + key, Connection='close'), **kw)
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
        try:
            said = _json(_call('GET', '/health', config, secrets))
        except Exception:
            with _lock:
                _about.pop(device['id'], None)   # no answer = nothing is known; while it is being asked, the last answer stands
            raise
        with _lock:
            _about[device['id']] = (time.monotonic(), said)
    return said


def forget_health(device_id):
    """What this board said about itself is out of date (its screen was just changed)."""
    with _lock:
        _about.pop(device_id, None)


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
                    'values': '<text>', 'wait': SPEAK_WAIT},
            'stop': {'help': 'stop what you are saying there', 'example': '', 'values': '(no value)'},
            'sound': {'help': 'play a stored sound. No value lists them', 'example': '',
                      'values': '[name]'},
            'volume': {'help': 'set it, or step it. No value reads it', 'example': '80',
                       'values': '[0-100 | up | down]'},
        }},
        'mic': {'label': 'Mic', 'help': 'hear that room', 'actions': {
            'listen': {'help': 'longest wait in seconds, ends when they stop talking', 'example': '10',
                       'values': '[seconds]', 'wait': 60 + 12},
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
    if 'wake.model' in (device.get('takes') or []):     # it states its model family: the program with the model door
        told['wake']['actions']['install'] = {
            'help': "listen for a wake word from the library (Settings > Wakeword): the model is sent to the board, "
                    "which restarts on it, about half a minute. 'own' = the word built into its program. "
                    "Sent even when it has that name already (a retrained model lands)",
            'example': 'hey_marcus', 'values': '<model name> | own', 'wait': 35}
    told['power'] = {'label': 'Power', 'help': 'restart it or shut it down', 'actions': {
        'restart': {'help': 'restart the whole satellite. Back in about a minute', 'example': ''},
        'shutdown': {'help': 'switch it off. Someone must unplug and replug it to bring it back',
                     'example': ''},
    }}
    told['ir'] = {'label': 'IR blaster', 'help': 'send infrared remote codes to a TV or other appliance in that room',
                  'actions': {
        'blast': {'help': "send an IR code by name - '<appliance> <command>' from the IR library "
                          "(e.g. 'tv power', 'tv mute'), or a raw NEC code as two hex bytes 'addr cmd' (04 08)",
                  'example': 'tv power', 'values': '<appliance command | addr cmd>'},
    }}
    told['screen'] = {'label': 'Screen', 'help': 'its small screen: show puts words on it, picture puts an image on it', 'actions': {
        'show': {'help': 'words in a line across the top of its screen for a while. seconds= how long, 20 if left out. '
                         'For an image use picture',
                 'example': 'Dinner in ten minutes', 'values': '<text> [seconds=20]'},
        'clear': {'help': 'take the line down', 'example': ''},
        'picture': {'help': 'an image on the whole screen until a tap, or seconds= (60 if left out). This is how to '
                            'show someone a picture there. Give an image handle like img:ab12 (the receipt any image '
                            'tool gives you), doc:N from the library, or last = the newest image in this chat. '
                            'A landscape image is turned sideways',
                    'example': 'last seconds=120', 'values': '<img:id | doc:N | last> [seconds=60]', 'wait': PICTURE_WAIT},
    }}
    told['buttons'] = {'label': 'Buttons', 'help': "its buttons: each press does what is set above. Set to send a "
                       "message, the words land in its chat as if someone there had said them", 'actions': {
        'press': {'help': 'press one from here, to try what it sends. Only a press set to send a message does '
                          'anything from here: the rest the board does by itself',
                  'example': 'boot short', 'values': '<button> [short | long | double]', 'owner': True},
    }}
    told['keyboard'] = {'label': 'Keyboard', 'help': 'what is typed on it lands in your chat; your reply is shown on its screen',
                        'actions': {}}
    if config.get('camera', True):
        told['camera'] = {'label': 'Camera', 'help': 'see that room', 'actions': {
            'look': {'help': 'take one picture and see it. The ring warns the room first',
                     'example': '', 'wait': LOOK_WAIT},
        }}
    told['storage'] = {'label': 'Backup', 'help': 'sealed copies of her memory on its card', 'actions': {
        'backup': {'help': 'back everything up onto its card now, sealed with the backup password',
                   'example': ''},
        'list': {'help': 'what is on the card and how much room is left', 'example': ''},
    }}
    if isinstance(device.get('takes'), list) and 'screen.picture' not in device['takes']:
        told['screen']['actions'].pop('picture')          # a screen for words only
    if isinstance(device.get('takes'), list) and 'screen.calibrate' in device['takes']:
        told['screen']['actions']['calibrate'] = {        # a resistive glass (the CYD 2.8): three crosses on the board
            'help': 'calibrate its touch glass: three crosses come up on the board to tap, 60 s. '
                    'Someone has to be at the board', 'example': '', 'owner': True}
    if 'cancel' in _can(device):
        told['storage']['actions']['cancel'] = {
            'help': 'stop the backup landing on its card right now; what landed of it is dropped',
            'example': '', 'owner': True}
    if 'check' in _can(device):
        told['storage']['actions']['check'] = {
            'help': 'read every backup on the card back now and compare it with the hash kept when it was '
                    'written. Runs by itself; the readings say how far it is and what it found', 'example': ''}
    if 'format' in _can(device):
        told['storage']['actions']['format'] = {
            'help': 'wipe the card and format it FAT32', 'example': '', 'owner': True, 'wait': STORE_WAIT,
            'danger': f"Formats the card in {device['id']}. Every backup on it is erased."}
    if 'firmware' in _has(device):             # two program slots: a board says so; a Pi never does
        told['firmware'] = {'label': 'Firmware', 'help': "the board's program, sent over the air from the firmware "
                            "source (Settings > Devices > New board uses the same source)", 'actions': {
            'check': {'help': "read the firmware source now and say whether a newer program is there for this board",
                      'example': '', 'values': '(no value)'},
        }}
        if config.get('ota', True) is False:
            told['firmware']['help'] += ". Updates over the air are OFF for this device (its Status tab)"
        else:
            told['firmware']['actions']['update'] = {
                'help': "install the newest program the source has for this board, then restart onto it. "
                        "A program that fails to run rolls back by itself. 'again' installs the same version once more",
                'example': '', 'values': "(no value) | again", 'owner': True, 'wait': UPDATE_WAIT,
                'danger': f"Replaces the program on {device['id']} and restarts it. The board is away for a "
                          f"minute. If the new program does not run, the old one comes back on its own."}
        from core.devices import firmware
        if not firmware.official():            # a folder or someone's own release: said wherever a program is sent
            told['firmware']['help'] += (f". The source is NOT Sapphire's release now: {firmware.source()[:120] or 'none set'} "
                                         "(Devices > Settings changes it)")
            if 'update' in told['firmware']['actions']:
                told['firmware']['actions']['update']['danger'] += " It comes from a source that is not Sapphire's release."
    return told


HOW_WORDS = {'short': 'short press', 'long': 'long press', 'double': 'double press'}


def bindings(device, config, key):
    """The menu of the `buttons` setting (engine._menu): one slot for each
    way each button is pressed, and what a press can be set to. From what
    the board's last /health said: {"buttons": {"list": [{"name", "short",
    "long", "double", "ways"}], "can": {job: words}}}, where short/long/double
    name the job the board does by itself when the press is left to it, and
    `ways` (when stated) the only ways that button can be pressed."""
    from core.devices import voice
    with _lock:
        _, said = _about.get(device['id'], (0, None))
    told = (said or {}).get('buttons') if isinstance(said, dict) else None
    told = told if isinstance(told, dict) else {}
    can = {str(k)[:24].lower(): str(v)[:60] for k, v in (told.get('can') or {}).items()} \
        if isinstance(told.get('can'), dict) else {}
    slots = []
    for b in (told.get('list') if isinstance(told.get('list'), list) else []):
        name = str(b.get('name') or '').strip().lower()[:24] if isinstance(b, dict) else ''
        ways = b.get('ways') if name and isinstance(b.get('ways'), list) else voice.HOWS   # a place on a touch screen: short only
        for how in [h for h in voice.HOWS if h in ways] if name else ():
            own = str(b.get(how) or '').lower()
            slots.append({'key': f'{name}.{how}', 'label': f'{name}: {HOW_WORDS[how]}',
                          'own': can.get(own, own) or 'nothing'})
    if not slots:
        return {'slots': [], 'choices': [], 'note': "This board has not said which buttons it has yet. "
                "Press Test now on the Status tab while it is on."}
    choices = [{'value': '', 'label': 'Leave it to the board'},
               {'value': voice.TELL, 'label': 'Send Sapphire a message', 'text': True,
                'placeholder': f"The button on {device['id']} was pressed."},
               *({'value': k, 'label': v} for k, v in can.items() if k != voice.TELL),
               {'value': 'none', 'label': 'Nothing'}]
    return {'slots': slots, 'choices': choices,
            'note': "A press set here replaces what the board does with it. A double press makes a short press "
                    "wait a third of a second, only on a button that has one set."}


KEEP, OWN = 'keep', 'own'      # the wake_model wish: never set here (leave the board alone) | its built-in word | a library name
SENT_FRESH = 60                # seconds within which the same wake word is not sent to a board twice (a save and a probe, two saves)


def _wish(config):
    """The wake_model wish as kept: KEEP, OWN or a library name (as the library lists it, never slugged:
    it only picks an entry)."""
    w = config.get('wake_model')
    w = '' if w is None else str(w).strip()
    return KEEP if w in ('', KEEP) else OWN if w == OWN else w[:48]


def options(device, config, key):
    """The choices of a dynamic select on this device (engine._options). `wake_model`: what it has now,
    its own word, and the library's usable models of the family this board runs. openWakeWord's built-in
    names have no file to send."""
    if key != 'wake_model':
        return []
    from core.wakeword import library
    row = {'fingerprint': device.get('fingerprint'), 'parts': [{'driver': 'satellite', 'has': device.get('has')}]}
    said = wake_state(device['id'])
    fam = said.get('format') or wake_format(device['id'], row=row)
    out = [{'value': KEEP, 'label': f"what it has now ({said['model']})" if said.get('model') else 'what it has now'},
           {'value': OWN, 'label': "the board's own word"}]
    out += [{'value': e['name'], 'label': f"{e['phrase']} ({e['name']})"}
            for e in library.entries() if e['family'] == fam and e['source'] != 'builtin' and e.get('ok', True)]
    return out


# ONE owner for sending a wake word to a board (2026-10-19). Three lanes call it: a save (apply, no force:
# only a changed choice goes), an explicit switch (the Wakeword Maker's Send, her `wake install`: force, so a
# retrained model under the same name, or a model the board stopped trusting, lands), and the probe (_settle_wake:
# only what was never tried in this process or failed last time). The last send per board is remembered, so the
# same bytes never go twice within SENT_FRESH and the readings can say what became of a wish.
_pushed = {}      # device id -> {'ident': (name, sha8) | (OWN, ''), 'ok': bool, 'at': monotonic}
_saving = set()   # device ids being told their settings right now: the probe sends nothing under a save


def _wake_now(device, config, secrets):
    """What the board listens for now, fresh (GET /wakeword): {'model', 'stored', 'format'}; None when its
    program has no such door or states no model family (an older program: nothing is sent to it)."""
    try:
        said = _json(_call('GET', '/wakeword', config, secrets))
    except Missing:
        return None
    fmt = str(said.get('format') or '').lower()
    if fmt not in ('tflite', 'onnx'):
        return None
    return {'model': str(said.get('model') or ''), 'stored': said.get('stored') is True, 'format': fmt}


def _tried(device_id, wish):
    """The last send of this wish on this board, or None when it was never sent in this process."""
    with _lock:
        last = _pushed.get(device_id)
    return last if last and last['ident'][0] == wish else None


def send_wake_model(device, config, secrets, force=False):
    """The wake_model wish to the board: PUT /wakeword/model with the library's bytes, or DELETE for its own
    word; the board restarts on it. Returns 'sent', 'has' (it listens for it already, or the same bytes went
    moments ago) or None (nothing to send: KEEP, or a program without the door). Raises DeviceError in words
    when it could not go; the failure is remembered, so the probe sends it when the board answers."""
    wish = _wish(config)
    if wish == KEEP:
        return None
    now = _wake_now(device, config, secrets)
    if now is None:
        if force:
            raise _engine_error("this board's program has no door for a wake word model: update it, or copy the files by hand")
        return None
    from core.wakeword import library
    data = e = None
    if wish == OWN:
        if not force and not now['stored']:
            return 'has'
        ident = (OWN, '')
    else:
        if not force and now['model'] == wish:
            return 'has'
        e = library.get(wish, now['format'])
        if not e or not e.get('path'):
            raise _engine_error(f"the library has no usable {library.FAMILIES[now['format']]} model named {wish}; pick another on its Wake word tab")
        data = Path(e['path']).read_bytes()
        ident = (wish, hashlib.sha256(data).hexdigest()[:8])
    with _lock:
        last = _pushed.get(device['id'])
        if last and last['ident'] == ident and last['ok'] and time.monotonic() - last['at'] < SENT_FRESH:
            return 'has'                            # the same bytes went moments ago: the board is restarting on them
    try:
        if wish == OWN:
            _call('DELETE', '/wakeword/model', config, secrets, timeout=30)
            forget_health(device['id'])
        else:
            micro = e.get('micro') or {}
            put_model(device, config, secrets, wish, data, now['format'], float(e['threshold']),
                      phrase=e.get('phrase') or wish, sliding_window=micro.get('sliding_window'), step_ms=micro.get('step_ms'))
    except Missing:
        raise _engine_error("this board's program has no door for a wake word model: update it, or copy the files by hand")
    except Problem as ex:
        with _lock:
            _pushed[device['id']] = {'ident': ident, 'ok': False, 'at': time.monotonic()}
        raise _engine_error(f"the wake word ({'its own word' if wish == OWN else wish}) could not be sent now: {ex} It goes when the board answers.")
    with _lock:
        _pushed[device['id']] = {'ident': ident, 'ok': True, 'at': time.monotonic()}
    logger.info(f"[DEVICES] {device['id']}: sent the wake word {'(its own word)' if wish == OWN else wish}"
                f"{' again' if force else ''}; it restarts on it")
    return 'sent'


def _settle_wake(device, config, secrets):
    """At a probe, a wish the board does not listen for yet: sent when it was never tried in this process (a
    board that was off when it was set, or Sapphire restarted since) or the last try failed (unreachable, or
    busy with a backup). Never under a save, and never again after a send the board did not keep (it crashed
    on it, or was reflashed): that is said in the readings and left to the person. The wish is read live, not
    from this probe's row, so a save that just changed it is not undone."""
    with _lock:
        if device['id'] in _saving:
            return
    try:
        from core.devices import engine
        live = engine._part(engine.get(device['id']), 'satellite')
        config = dict(config, wake_model=(live.get('config') or {}).get('wake_model')) if live else config
        last = _tried(device['id'], _wish(config))
        if last and last['ok']:
            return
        send_wake_model(device, config, secrets)
    except Exception as e:
        logger.warning(f"[DEVICES] {device['id']}: the wake word it was set to could not be sent: {e}")


def _has(device):
    """What the board's last /health said it has, from the health cache
    (describe() has no key to ask with). [] for a board never heard from, or
    an early Pi that says nothing."""
    with _lock:
        _, said = _about.get(device['id'], (0, None))
    has = (said or {}).get('has') if isinstance(said, dict) else None
    if not isinstance(has, list):
        has = device.get('has')            # what the engine keeps on the row: a missed probe drops no tab
    return [str(c) for c in has] if isinstance(has, list) else []


def _check(device, config, secrets):
    """The source read now, against what the board runs. Fills what the
    status strip reads from (firmware.known_version)."""
    from core.devices import firmware
    h = _health(device, config, secrets, fresh=True)
    model = str(h.get('model') or '').strip().lower()
    running = str(h.get('firmware') or '?')
    if not model:
        return f"{device['id']} runs {running}, and its program does not say which firmware it is: too old to update over the air.", False
    try:
        b = firmware.board(model, fresh=True)
    except firmware.FirmwareError as e:
        return f"{device['id']} runs {running}. {e}", False
    if b['version'] == running:
        return f"{device['id']} runs {running}, the newest the source has.", True
    return f"{device['id']} runs {running}; the source has {b['version']}. 'update' installs it.", True


def _update(device, config, secrets, value):
    """The newest program for this board, from the firmware source, onto the
    board's other slot (ota.c on the board: PUT /firmware with the sha256)."""
    import hashlib
    from core.devices import firmware
    h = _health(device, config, secrets, fresh=True)
    model = str(h.get('model') or '').strip().lower()
    if not model:
        return "This board's program does not say which firmware it is (no model in /health): too old to update over the air.", False
    try:
        path, version = firmware.app_part(model)
    except firmware.FirmwareError as e:
        return f"No program to send: {e}", False
    running = str(h.get('firmware') or '?')
    if version == running and str(value or '').strip().lower() != 'again':
        return f"{device['id']} already runs {version}. Say 'again' to install it once more.", True
    sha = hashlib.sha256()
    with open(path, 'rb') as f:
        for piece in iter(lambda: f.read(1 << 20), b''):
            sha.update(piece)
    with open(path, 'rb') as f:
        out = _json(_call('PUT', '/firmware', config, secrets, timeout=UPDATE_WAIT, data=f,
                          headers={'Content-Type': 'application/octet-stream', 'X-Sha256': sha.hexdigest()}))
    with _lock:
        _about.pop(device['id'], None)
    return (f"{device['id']}: {version} is in slot {out.get('slot', '?')} and it is restarting onto it "
            f"(it ran {running}). Back in about a minute; if the new program fails, the old one returns by itself."), True


def _takes(h):
    """The finer things a board's /health says it takes, as the engine keeps
    them (a field or an action that names one is shown only on such a board):
    each setting its `screen` states, `screen.picture` when it states a
    picture's size, `mic.max_s`, and its looks (`led.looks`; a board that
    lists none takes them all, as every board did)."""
    takes = []
    screen = h.get('screen') if isinstance(h.get('screen'), dict) else {}
    takes += [f'screen.{k}' for k in screen if isinstance(k, str)]
    if all(k in screen for k in ('w', 'h', 'format')):
        takes.append('screen.picture')
    if 'max_s' in (h.get('mic') if isinstance(h.get('mic'), dict) else {}):
        takes.append('mic.max_s')
    if 'volume_step' in h:
        takes.append('volume.step')
    ww = h.get('wakeword') if isinstance(h.get('wakeword'), dict) else {}
    if str(ww.get('format') or '').lower() in ('tflite', 'onnx'):
        takes.append('wake.model')                 # it states its model family: the program with the model door
    led = h.get('led') if isinstance(h.get('led'), dict) else {}
    looks = led['looks'] if isinstance(led.get('looks'), list) else LOOKS
    takes += [f'look.{n}' for n in looks if isinstance(n, str)]
    return takes


def wake_state(device_id):
    """What this board last said of its wake word (/health `wakeword`): {'model', 'format', 'enabled'};
    {} when nothing is known. Read from the health cache; the library asks with no key."""
    with _lock:
        _, said = _about.get(device_id, (0, None))
    ww = said.get('wakeword') if isinstance(said, dict) and isinstance(said.get('wakeword'), dict) else None
    if not ww:
        return {}
    fmt = str(ww.get('format') or '').lower()
    return {'model': str(ww.get('model') or ''), 'format': fmt if fmt in ('tflite', 'onnx') else '',
            'enabled': bool(ww.get('enabled'))}


def wake_format(device_id, health=None, row=None):
    """Which model family a satellite runs: 'tflite' (microWakeWord, an ESP32) or 'onnx' (openWakeWord,
    a Pi). The board states it (/health `wakeword.format`). One that is silent (off, or an older program)
    is read from its device row: a chip the flasher wrote (`fingerprint`) or a board with two program
    slots (`firmware` in `has`) is an ESP32, a Pi is neither; then from its board and device names;
    and a Pi is the default."""
    said = health
    if said is None:
        with _lock:
            _, said = _about.get(device_id, (0, None))
    said = said if isinstance(said, dict) else {}
    ww = said.get('wakeword') if isinstance(said.get('wakeword'), dict) else {}
    fmt = str(ww.get('format') or '').lower()
    if fmt in ('tflite', 'onnx'):
        return fmt
    if isinstance(row, dict):
        parts = row.get('parts') if isinstance(row.get('parts'), list) else []
        has = next((p.get('has') for p in parts if isinstance(p, dict) and p.get('driver') == 'satellite'), None)
        if row.get('fingerprint') or (isinstance(has, list) and 'firmware' in has):
            return 'tflite'
    hint = f"{said.get('board') or ''} {device_id}".lower()
    return 'tflite' if any(k in hint for k in ('esp', '-s3', '-c3', '-c6', 'xiao')) else 'onnx'


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
    # what did not start, and why it last went down, first: they are what a person needs to see (firmware 0.5.2)
    for n, said in enumerate(p for p in (h.get('problems') or []) if isinstance(p, str)):
        readings['trouble' if n == 0 else f'trouble {n + 1}'] = said[:80]
    if h.get('reset') in ('crash', 'task watchdog', 'interrupt watchdog', 'watchdog', 'brownout'):
        readings['started after'] = f"a {h['reset']}"
    words = h.get('last_words') if isinstance(h.get('last_words'), dict) else {}
    if words.get('reason'):
        when = words.get('when')
        ago = f", {_span(max(0, time.time() - when))} ago" if isinstance(when, (int, float)) and when > 0 else ''
        readings['last went down'] = f"{words['reason']}{ago}"[:80]
    from core.devices import glass
    if glass.behind(config, h.get('screen')):                         # its screen shows another chat or scene: put right
        glass.sync_soon(device['id'])
    if h.get('firmware'):
        readings['program'] = ' '.join(str(x) for x in (h.get('board'), h['firmware']) if x)[:60]
    if h.get('model'):
        readings['model'] = str(h['model'])[:33]                       # the firmware source's board id: a reflash offers that one first
    if h.get('mac'):
        readings['board id'] = str(h['mac'])[:17]                      # the chip's MAC (pocket 0.2.1, satellite 0.2.3)
    if isinstance(h.get('has'), list):                                 # how its program is replaced
        if 'firmware' not in h['has']:
            readings['updates'] = 'over USB only: one program slot'
        elif config.get('ota', True) is False:
            readings['updates'] = 'over the air, turned off for this device'
        else:
            readings['updates'] = 'over the air' + (f", running {h['slot']}" if h.get('slot') else '')
    if h.get('model') and h.get('firmware') and 'firmware' in (h.get('has') or []) and config.get('ota', True) is not False:
        from core.devices import firmware                               # the source's newest, from what is known: no network here
        newest = firmware.known_version(h['model'])
        if newest and newest != str(h['firmware']):
            readings['update'] = f"{newest} is in the firmware source (Firmware tab)"
    if isinstance(h.get('volume'), (int, float)):
        readings['volume'] = f"{int(h['volume'])}%"
    mic = h.get('mic') if isinstance(h.get('mic'), dict) else {}
    if isinstance(mic.get('max_s'), (int, float)):
        readings['longest question'] = f"{int(mic['max_s'])} s"
    if 'gain_db' in mic:
        readings['mic gain'] = f"{mic.get('gain_db')} dB" + (', AGC on' if mic.get('agc') else '')
    if isinstance(h.get('uptime_s'), (int, float)):
        readings['running for'] = _span(h['uptime_s'])
    if h.get('temp_c') is not None:
        readings['temperature'] = f"{h['temp_c']}C"
    wake = h.get('wakeword') if isinstance(h.get('wakeword'), dict) else {}
    if wake:
        readings['wake word'] = ('paused while a backup lands' if wake.get('paused') else
                                 'listening' if wake.get('enabled') and wake.get('running') else 'off') \
            + (f" for {wake['model']}" if wake.get('model') else '')
        wish = _wish(config)                           # health says the model, not `stored`: only a named wish can be judged here
        if wish not in (KEEP, OWN) and wish != str(wake.get('model') or '') and 'format' in wake:
            last = _tried(device['id'], wish)
            readings['wake word'] += f"; set to {wish}, " + ('not sent yet' if last is None else
                                                            'will be sent when it answers' if not last['ok'] else
                                                            'the board did not keep it')
            if last is None or not last['ok']:
                _settle_wake(device, config, secrets)
    cap = h.get('capture') if isinstance(h.get('capture'), dict) else {}
    if cap.get('active'):                                              # the room in pieces to the Wakeword Maker (0.7.6)
        readings['capturing'] = (f"room to Sapphire: {cap.get('sent', 0)} pieces, {cap.get('fires', 0)} wake fires"
                                 + (', test mode' if cap.get('test') else ''))[:80]
    led = h.get('led') if isinstance(h.get('led'), dict) else {}
    if led:
        readings['light'] = 'turned off: dark through every state' if led.get('off') else \
            'dark' if led.get('blackout') else f"{led.get('state', '?')}, {led.get('animation', '?')}"
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
    if isinstance(st.get('count'), int):
        readings['backups'] = f"{st['count']} on the card" + (', one arriving now' if st.get('writing') else '') \
            + (', the last one sent did NOT land' if st.get('failed') else '')
    ch = st.get('checks') if isinstance(st.get('checks'), dict) else None
    if ch:                                                        # a board that reads its backups back (tdongle 0.1.0)
        if ch.get('damaged'):
            readings['damaged backups'] = f"{ch['damaged']}: replaced at the next backup"
        if ch.get('running'):
            readings['card check'] = f"reading every backup back, {ch.get('done', 0)} of {ch.get('of', 0)}"
        elif ch.get('unchecked'):
            readings['card check'] = f"done; {ch['unchecked']} older backup(s) have no hash to check against"
        else:
            readings['card check'] = 'every backup read back and whole' if not ch.get('damaged') else 'done'
    where = urlsplit(str(config.get('url') or '')).netloc
    out = {'online': bool(h.get('ok', True)), 'readings': readings,
           'detail': f"{h.get('name') or h.get('body_name') or device['id']} at {where}"}
    if isinstance(h.get('has'), list):           # the engine keeps it with the device
        out['has'] = h['has']
        out['takes'] = _takes(h)
    return out


def play(audio, kind, device, config, secrets):
    """Sound on the satellite: what it said back ({'ok', 'stopped'?}). Older
    boards answer when it has played; 0.7.7+ once it is taken into the board's
    play queue. A board that states the format
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
        seconds = voice.duration(audio)       # of sound, as fitted (any codec): what a log can time against
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


def _upright(b64, turn, width, height):
    """A camera's JPEG (base64) turned `camera_turn` degrees to the right,
    for a camera mounted turned: (base64, w, h). Left as it came when it is
    not turned, or will not open."""
    how = {'90': 'ROTATE_270', '180': 'ROTATE_180', '270': 'ROTATE_90'}.get(str(turn or '0'))   # PIL counts to the left
    if not how:
        return b64, width, height
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(b64))).transpose(getattr(Image.Transpose, how))
        out = io.BytesIO()
        img.convert('RGB').save(out, 'JPEG', quality=90)
        return base64.b64encode(out.getvalue()).decode('ascii'), img.width, img.height
    except Exception as e:
        logger.warning(f"[DEVICES] a camera picture could not be turned: {e}")
        return b64, width, height


def _picture(value, device, config, secrets):
    from core import images
    source, seconds = _seconds_and_rest(value, 60)
    if not source or source == 'last':
        found = images.last_image_id()
        if not found:
            return "There is no image in this chat yet. Make or find one first, then say picture last.", True
        source = f'img:{found}'
    if not _HANDLE.fullmatch(source) or source == 'last':                       # the three handles, nothing fetched
        return "Give an image handle: img:<id> (the receipt an image tool gives you), doc:<n> from the library, or last.", False
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


def screen_chat(config, secrets, name, brain='', trim=''):
    """The chat the board talks in, on its glass: its name ('' hides it), the
    model that answers in it, its trim color '#rrggbb'. (core/devices/glass.py)"""
    _call('POST', '/screen', config, secrets, json={'chat': name, 'brain': brain, 'trim': trim})


def screen_scene(config, secrets, name, w=0, h=0, data=None):
    """The scene behind its clock: w x h of RGB565 big-endian, or no data = none."""
    if data is None:
        _call('POST', '/screen/background', config, secrets, json={'clear': True})
        return
    _call('POST', f'/screen/background?w={w}&h={h}&name={name}', config, secrets, timeout=PICTURE_WAIT,
          data=data, headers={'Content-Type': 'application/octet-stream'})


_stops = {}           # device id -> stops so far: a say sent sentence by sentence ends at the next one


def _say(text, device, config, secrets):
    """Said sentence by sentence, as a reply is: the board holds one sentence
    of sound at a time (~47 s at most), and the first is heard while the rest
    are made. A stop there ends what is left."""
    from core.devices import voice
    from core.tts.streaming import chunkify_for_speech
    text = str(text or '').strip()
    if not text:
        return "say: the value is what to say. Example: Dinner is ready", True
    stops = _stops.get(device['id'], 0)
    said = 0
    for piece in chunkify_for_speech([text], max_chars=300, split_mode='sentence'):
        if _stops.get(device['id'], 0) != stops:
            break
        audio, kind = voice.render(piece['text'])     # a refused voice asks the satellite nothing
        if audio is None:
            if said:
                break
            return f"Nothing was said: {kind}", False
        try:
            if play(audio, kind, device, config, secrets).get('stopped'):
                break
        except Unfit as e:
            if said:
                break
            return f"Nothing was said: {e}", False
        said += 1
    short = text if len(text) <= 80 else text[:77] + '...'
    return f'Said there: "{short}"', True


def _stop(device, config, secrets):
    """Stop her voice there: core drops the sentences still to be sent
    (voice.halt), the satellite cuts what is playing (POST /audio/stop). A
    program without that door is said so; its current sentence ends itself."""
    from core.devices import voice
    _stops[device['id']] = _stops.get(device['id'], 0) + 1
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


def _step(config):
    """The device's volume step, 1 to 50; 10 when it is not set or not a number."""
    try:
        return max(1, min(50, int(config.get('volume_step') or VOLUME_STEP)))
    except (TypeError, ValueError):
        return VOLUME_STEP


def _volume(value, config, secrets):
    """Read, set, or step the speaker's volume. A satellite whose program
    has no volume door is told so, plainly."""
    want = str(value or '').strip().lower()
    try:
        if not want or want in ('up', 'down'):
            now = int(_json(_call('GET', '/volume', config, secrets)).get('volume', 0))
            if not want:
                return f"Volume is {now}%.", True
            level = max(0, min(100, now + (_step(config) if want == 'up' else -_step(config))))
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
            if 'gain_db' not in now:           # a Pi body's /mic holds only the longest question
                raise Missing('no gain')
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
    try:                                     # 0.7.7+ listens once she is done: what it still has to say counts
        ahead = _json(_call('GET', '/health', config, secrets)).get('sound', {}).get('queued_kb', 0) * 1024 / 32000
    except Exception:
        ahead = 0
    r = _call('GET', f'/audio/listen?vad=true&max_seconds={seconds}', config, secrets,
              timeout=seconds + 12 + min(ahead, 60))
    heard, problem = voice.transcribe(r.content)
    if problem:
        return problem, False
    if not heard:
        return "Listened, and heard no speech.", True
    return f'Heard: "{heard}"', True


def capture(device, config, secrets, seconds=0, chunk_s=30, test=False):
    """The room in pieces to Sapphire's room door (POST /api/devices/{id}/room), for `seconds`: the board posts
    every `chunk_s` seconds of its clean microphone stream from its own task, with the wake fires that fell in
    each piece, and keeps hearing its wake word meanwhile. With `test` its fires are counted and not acted on.
    `seconds` 0 ends a capture (DELETE /capture). What the Wakeword Maker's room watch asks of an ESP32.
    Answers the board's capture block. Raises Missing on a board without the door."""
    from urllib.parse import urlencode
    if int(seconds or 0) <= 0:
        out = _json(_call('DELETE', '/capture', config, secrets))
    else:
        q = urlencode({'seconds': int(seconds), 'chunk': int(chunk_s), 'test': 'true' if test else 'false'})
        out = _json(_call('POST', '/capture?' + q, config, secrets))
    with _lock:
        _about.pop(device['id'], None)
    return out.get('capture') if isinstance(out.get('capture'), dict) else out


def capture_state(device, config, secrets):
    """How a capture goes (GET /capture): {'active', 'test', 'seconds', 'left_s', 'sent', 'dropped', 'lost', 'fires'}."""
    out = _json(_call('GET', '/capture', config, secrets))
    return out.get('capture') if isinstance(out.get('capture'), dict) else out


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

    def damaged(self):
        # a board that keeps hashes says "ok": false for a backup that no longer matches its own
        files = self.info().get('files') or []
        return [str(f['name']) for f in files if isinstance(f, dict) and f.get('name') and f.get('ok') is False]

    def put(self, path, name):
        self.check(path, name)
        if not _NAME.match(name):
            raise Problem(f"'{name}' is not a usable file name for the card.")
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        sha = h.hexdigest()
        # Asked before it is sent: a board that refuses still has to take every byte we send to
        # throw it away, deaf for a minute. And the wait for its answer grows with the backup: it
        # reads the whole file back from its card (about 1 MB/s at worst) before it says anything.
        size = os.path.getsize(path)
        try:
            told = self.info()
        except Missing:
            told = {}
        room = told.get('free_bytes')
        if isinstance(room, (int, float)) and room < size + ROOM_SPARE:
            raise Problem(f"Not enough room on the card in {self.label}: {size // 2**20} MB to send, {int(room) // 2**20} MB free.")
        with open(path, 'rb') as f:
            r = _call('PUT', f'/storage/{name}', self._config, self._secrets, timeout=STORE_WAIT + size // 2**20,
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
            if action == 'check':
                if 'check' not in _can(device):
                    return "This satellite does not read its backups back.", False
                _json(_call('POST', '/storage/check', config, secrets))
                with _lock:
                    _about.pop(device['id'], None)
                return f"{device['id']} is reading its backups back now. Its readings say how far it is.", True
            if action == 'cancel':
                if 'cancel' not in _can(device):
                    return "This satellite cannot stop a backup once it is on its way.", False
                try:
                    _json(_call('POST', '/storage/cancel', config, secrets))
                except Problem as e:
                    return f"Nothing to stop: {e}", False
                with _lock:
                    _about.pop(device['id'], None)
                return f"The backup landing on {device['id']} is cancelled; what landed of it is dropped.", True
            if action == 'format':
                if 'format' not in _can(device):
                    return "This satellite cannot format its card.", False
                _json(_call('POST', '/storage/format', config, secrets, timeout=STORE_WAIT))
                with _lock:
                    _about.pop(device['id'], None)
                return f"The card in {device['id']} is formatted (FAT32) and empty.", True
        if capability == 'speaker':
            if action == 'say':
                from core.devices import voice
                if voice.answering(device['id']):          # her own reply is already spoken there, sentence by sentence
                    return (f"Not said: your reply to {device['id']} is already spoken there as you write it. "
                            "Do not use say for it. Just answer in plain words."), False
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
        if capability == 'ir' and action == 'blast':
            v = str(value or '').strip()
            parts = v.split()
            if len(parts) == 2 and re.fullmatch(r'[0-9a-fA-F]{1,4}', parts[0]) and re.fullmatch(r'[0-9a-fA-F]{1,2}', parts[1]):
                addr, cmd = int(parts[0], 16) & 0xffff, int(parts[1], 16) & 0xff   # a raw NEC code (addr up to 16-bit = extended)
                _call('POST', f'/ir/blast?addr={addr:x}&cmd={cmd:02x}', config, secrets)
                return f"Sent IR code {addr:x} {cmd:02x}.", True
            from core.ir import library                                           # a name from the library
            app, code = library.resolve(v)
            if not code:
                known = library.command_names()
                return (f"No IR code named '{v}'." + (f" I know: {known}." if known else
                        " The IR library is empty - add codes in Settings, or give a raw NEC code like 04 08.")), False
            if code.get('protocol') != 'nec':
                return f"The code for {app['name']} {code['name']} is {code.get('protocol')}, which this board cannot send yet.", False
            _call('POST', f"/ir/blast?addr={int(code['addr']) & 0xffff:x}&cmd={int(code['cmd']) & 0xff:02x}", config, secrets)
            return f"Sent {app['name']} {code['name']}.", True
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
            if action == 'install':
                from core.wakeword import library
                try:
                    out = library.switch(device['id'], value)
                except library.LibraryError as e:
                    return str(e), False
                name = 'its own word' if out['model'] == OWN else out['model']
                if out['sent']:
                    return f"{device['id']} is restarting on {name}. Back in about half a minute.", True
                if out.get('had'):
                    return f"{device['id']} has {name} already.", True
                return f"{device['id']} is set to {name}, but: {out['warning']}", False
            if action in ('on', 'off'):
                _call('POST', f"/wakeword?enabled={'true' if action == 'on' else 'false'}", config, secrets)
                return ('Listening for the wake word.' if action == 'on'
                        else 'No longer listening for the wake word.'), True
            if action == 'read':
                w = _json(_call('GET', '/wakeword', config, secrets))
                state = 'listening' if w.get('enabled') and w.get('running') else 'not listening'
                return f"It is {state}" + (f" for {w['model']}." if w.get('model') else '.'), True
        if capability == 'buttons' and action == 'press':
            from core.devices import voice
            words = str(value or '').split()
            if not words:
                return "press: the button's name, then short, long or double. Example: boot short", True
            out = voice.pressed(device['id'], words[0], words[1] if len(words) > 1 else 'short')
            if not out.get('ok'):
                return str(out.get('error') or 'It was not taken.'), False
            if not out.get('accepted'):
                return (f"Nothing is set to be sent for {words[0]} {words[1] if len(words) > 1 else 'short'}. "
                        "What the board does by itself only a real press does."), True
            return f"Sent to the chat '{out.get('chat')}': \"{out.get('text')}\"", True
        if capability == 'sensors' and action == 'read':
            try:
                got = _json(_call('GET', '/sensors', config, secrets)).get('sensors')
            except Missing:                                   # an older board: what /health last said
                got = (_health(device, config, secrets, fresh=True) or {}).get('sensors')
            if not isinstance(got, dict) or not got:
                return "No sensor answered.", False
            return ' · '.join(f"{k}: {v}" for k, v in got.items()), True
        if capability == 'firmware' and action == 'check':
            return _check(device, config, secrets)
        if capability == 'firmware' and action == 'update':
            try:
                return _update(device, config, secrets, value)
            except Missing:
                return "This board's program has no door for updates over the air. Update it over USB once.", False
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
            if action == 'calibrate':
                _call('POST', '/screen/calibrate', config, secrets)
                return f"The crosses are up on {device['id']}: tap the three, top-left, top-right, bottom-right, within 60 s.", True
            if action == 'picture':
                return _picture(value, device, config, secrets)
            if action == 'show':
                first = str(value or '').split()[:1]
                if first and _HANDLE.fullmatch(first[0]):           # "show doc:23": the picture was meant (AIX, 2026-10-16)
                    said, ok = _picture(value, device, config, secrets)
                    return (said + " (An image handle goes up as a picture: that is the `picture` action; `show` is for words.)"
                            if ok else said), ok
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
            data, w, h = _upright(shot['data_b64'], config.get('camera_turn'), shot.get('width'), shot.get('height'))
            size = f", {w}x{h}" if w and h else ''
            return {'text': f"A picture from the camera of {device['id']}{where}{size}.",
                    'images': [{'data': data, 'media_type': 'image/jpeg'}]}, True
    except Problem as e:
        return str(e), False
    return f"A satellite has no {capability} / {action}.", False
