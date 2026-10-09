# plugins/ipcam/device_driver.py - an IP camera as a device (docs/plugin-author/devices.md)
#
# Any camera that speaks ONVIF: she sees through it, and turns it when it
# can turn. The camera is asked for everything (its ONVIF port, its picture
# addresses, whether it moves), so the user enters an address and a login.
#
# Pictures: the camera's own snapshot door first (quick, small on most
# cameras), its RTSP stream as the fallback and for the full-size picture.
#
# Moving: start, wait, stop. The first camera this was built on (a Jidetech
# P1 dome, 2026-10-09) taught three things, so nothing here trusts a camera:
#   it ignores the time limit a move carries, and turns until told to stop
#   it accepts a "step from here" and does nothing
#   it reports a position that never changes
# So a move is timed HERE and always ends with a stop, a move is over when
# two pictures in a row match, and the first contact with a camera sends one
# stop: if Sapphire died in the middle of a move, that ends it.
import base64
import io
import logging
import re
import time

import requests
from PIL import Image, ImageChops, ImageStat

from core import net
from plugins.ipcam import onvif
from plugins.ipcam.onvif import Problem

logger = logging.getLogger(__name__)

QUICK = 8                 # seconds for one snapshot
STREAM_WAIT = 15          # seconds for one picture off the stream
SHARP_WIDTH = 1600        # the full-size picture is scaled to this
SETTLE = 6                # seconds a camera may take to come to rest (and to focus) after a stop
TRAVEL = 20               # seconds it may take to reach a saved place
STILL = 1.0               # two pictures this close are the same view (0-255, mean; a still camera reads 0.3)
STEP = 30                 # a move with no number
FULL_TURN = 8.0           # seconds of turning that 100 means (on the P1 dome: about a quarter turn)
FULL_ZOOM = 2.0           # seconds of zooming that 100 means
SPEED = 0.5
SLOTS = 255
TURNS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, 1), 'down': (0, -1)}
_HOST = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9._-]{0,250}[A-Za-z0-9])?$')
_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,30}$')

_cams = {}                # device id -> (what it was made from, onvif.Camera)


# --- the camera --------------------------------------------------------------

def validate(config):
    host = re.sub(r'^[a-z]+://', '', str(config.get('host') or '').strip(), flags=re.I).split('/')[0]
    config['host'] = host = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    if not host:
        return config, "The camera's address is needed."
    if not _HOST.match(host):
        return config, f"'{host}' is not a usable address. Use an IP like 192.168.0.60."
    if net.classify(host) != 'lan':
        return config, ("A camera has to be on your own network. Its login travels with little "
                        "protection, so an internet address is refused.")
    config['user'] = str(config.get('user') or '').strip() or 'admin'
    places, seen = [], set()
    for row in config.get('places') or []:
        name, slot = str(row.get('name') or '').strip().lower().replace(' ', '-'), str(row.get('slot') or '').strip()
        if not _NAME.match(name) or name in TURNS or name in seen:
            return config, f"Places: '{row.get('name')}' needs a short name of its own (letters, digits, - and _)."
        if not slot.isdigit() or not 1 <= int(slot) <= SLOTS:
            return config, f"Places: '{name}' needs a number from 1 to {SLOTS}, the one it was saved under."
        seen.add(name)
        places.append({'name': name, 'slot': str(int(slot))})
    config['places'] = places
    port = config.get('port') or 0
    if not port:
        port = onvif.find(host)
        if not port:
            return config, (f"No ONVIF camera answered at {host}. Check the address, and that ONVIF is "
                            "switched on in the camera's own settings. If you know its ONVIF port, enter it.")
    config['port'] = int(port)
    return config, ''


def _camera(device, config, secrets):
    """This device's camera, asked once what it has. The first contact also
    proves the login and sends one stop (see the top of this file)."""
    made_from = (config.get('host'), config.get('port'), config.get('user'), secrets.get('password') or '')
    held = _cams.get(device['id'])
    if held and held[0] == made_from:
        return held[1]
    cam = onvif.Camera(*made_from)
    cam.setup()
    _ask(cam, peek=True)         # some cameras let ONVIF in with any password; their picture door does not
    if cam.ptz:
        cam.stop()
    _cams[device['id']] = (made_from, cam)
    return cam


# --- pictures ----------------------------------------------------------------

def _is_jpeg(data):
    return bool(data) and data[:2] == b'\xff\xd8'


def _ask(cam, peek=False):
    """One picture from the camera's snapshot door, or None when it has none
    or gave none. The login is sent the way the camera asks for it, and only
    when it asks. A refused login is a Problem. peek=True knocks and reads no
    picture."""
    url = next((q['snapshot'] for q in cam.profiles if q['snapshot']), '')
    if not url:
        return None
    try:
        r = net.request('GET', url, timeout=QUICK, stream=peek)
        if r.status_code == 401:
            r.close()
            digest = 'digest' in r.headers.get('WWW-Authenticate', '').lower()
            auth = requests.auth.HTTPDigestAuth if digest else requests.auth.HTTPBasicAuth
            r = net.request('GET', url, timeout=QUICK, stream=peek, auth=auth(cam.user, cam.password))
        if peek:
            r.close()
    except requests.exceptions.RequestException:
        return None
    if r.status_code in (401, 403):
        raise Problem("The camera refused the login. Check the user and password in Settings > Devices.")
    return r.content if not peek and r.status_code == 200 and _is_jpeg(r.content) else None


def _from_stream(cam, sharp):
    """One picture off the RTSP stream, as JPEG bytes. The sharpest profile,
    or the smallest (it starts sooner)."""
    streams = [q for q in cam.profiles if q['stream']]
    if not streams:
        raise Problem("The camera gave no picture, and it names no stream to take one from.")
    try:
        import av
    except ImportError:
        raise Problem("A picture off the camera's stream needs the 'av' package, which is not installed.")
    profile = streams[0] if sharp else streams[-1]
    try:
        with av.open(cam.stream_url(profile), options={'rtsp_transport': 'tcp'},
                     timeout=(STREAM_WAIT, STREAM_WAIT)) as box:
            image = next(box.decode(box.streams.video[0])).to_image()
    except Exception as e:
        # never the error's own words: they can hold the address, login and all
        logger.warning(f"[IPCAM] stream picture failed: {type(e).__name__}")
        raise Problem("The camera's stream gave no picture.")
    if image.width > SHARP_WIDTH:
        image = image.resize((SHARP_WIDTH, round(image.height * SHARP_WIDTH / image.width)))
    out = io.BytesIO()
    image.convert('RGB').save(out, 'JPEG', quality=85)
    return out.getvalue()


def _picture(cam, sharp=False):
    return (None if sharp else _ask(cam)) or _from_stream(cam, sharp)


def _small(data):
    return Image.open(io.BytesIO(data)).convert('L').resize((160, 120))


def _apart(a, b):
    """How far two pictures are apart, 0 to 255."""
    return ImageStat.Stat(ImageChops.difference(_small(a), _small(b))).mean[0]


def _seen(data, text):
    return {'text': text, 'images': [{'data': base64.b64encode(data).decode(), 'media_type': 'image/jpeg'}]}, True


def _where(device):
    return f" ({device['location']})" if device.get('location') else ''


# --- moving ------------------------------------------------------------------

def _amount(text, word):
    """'30' -> 0.3. Empty = STEP."""
    text = str(text or '').strip().rstrip('%')
    if not text:
        return STEP / 100
    if not text.isdigit() or not 1 <= int(text) <= 100:
        raise Problem(f"{word}: how far is a number from 1 to 100, not '{text}'.")
    return int(text) / 100


def _stop(cam, device):
    """Stop it, and mean it: a camera that was not stopped keeps turning."""
    for attempt in range(3):
        try:
            return cam.stop()
        except Problem as e:
            logger.warning(f"[IPCAM] {device['id']}: stop failed ({attempt + 1} of 3): {e}")
            time.sleep(0.3)


def _rest(cam, start, leave=0, most=SETTLE):
    """The view once the camera is at rest: two pictures in a row that match.
    leave = seconds the view is given to LEAVE `start` first (a camera sent
    to a saved place needs a moment to get going)."""
    last = _picture(cam)
    until = time.monotonic() + leave
    while time.monotonic() < until and _apart(start, last) < STILL:
        time.sleep(0.3)
        last = _picture(cam)
    until = time.monotonic() + most
    while time.monotonic() < until:
        time.sleep(0.3)
        now = _picture(cam)
        still = _apart(last, now) < STILL
        last = now
        if still:
            break
    return last


def _view(device, start, last, said):
    if _apart(start, last) < STILL:
        return _seen(last, f"{said}, but the view did not change. It may be at the end of its travel.")
    return _seen(last, f"{said}. This is what {device['id']} sees now.")


def _turned(cam, device, seconds, said, **velocity):
    """Start, wait, stop. The stop is sent whatever happens in between."""
    start = _picture(cam)
    try:
        cam.turn(**velocity)
        time.sleep(seconds)
    finally:
        _stop(cam, device)
    return _view(device, start, _rest(cam, start), said)


def _sent(cam, device, send, said):
    """A saved place: the camera travels by itself and stops there.
    One stop afterwards costs nothing and ends a camera that did not."""
    start = _picture(cam)
    try:
        send()
        last = _rest(cam, start, leave=3, most=TRAVEL)
    finally:
        _stop(cam, device)
    return _view(device, start, last, said)


def _place(config, value):
    want = str(value or '').strip().lower().replace(' ', '-')
    places = {p['name']: int(p['slot']) for p in config.get('places') or []}
    if want in places:
        return want, places[want]
    names = ', '.join(places) or 'none are named yet'
    raise Problem(f"goto: the value is a place's name. Places: {names}.")


# --- the driver --------------------------------------------------------------

def describe(device, config):
    told = {'camera': {'label': 'Camera', 'help': 'see what it sees', 'actions': {
        'look': {'help': "take one picture and see it. 'sharp' is the full-size picture, a few seconds slower",
                 'example': '', 'values': '[sharp]', 'wait': 30},
    }}}
    names = [p['name'] for p in config.get('places') or []]
    actions = {
        'move': {'help': f'turn and see the new view. The number is how far: {STEP} when left out, 100 is a wide sweep',
                 'example': 'left', 'values': '<left|right|up|down> [1-100]', 'wait': 30},
        'zoom': {'help': 'closer or wider, and see the new view', 'example': 'in',
                 'values': '<in|out> [1-100]', 'wait': 30},
    }
    if names:
        actions['goto'] = {'help': ('turn to a saved place: ' + ', '.join(names))[:160], 'example': names[0],
                           'values': '<place>', 'wait': 30}
    actions['stop'] = {'help': 'stop it where it is', 'example': ''}
    actions['save'] = {'help': 'store where it points now under a number, then name that number under Places. '
                               'Use low numbers: some cameras keep the high ones for themselves',
                       'example': '1', 'values': f'<1-{SLOTS}>', 'owner': True}
    told['ptz'] = {'label': 'Move', 'help': 'turn it, zoom, and go to saved places', 'actions': actions}
    return told


def status(device, config, secrets):
    try:
        cam = _camera(device, config, secrets)
        info = cam.info()
    except Problem as e:
        _cams.pop(device['id'], None)        # ask it afresh next time: it may have come back changed
        return {'online': False, 'detail': str(e)}
    sharpest = cam.profiles[0]
    readings = {'picture': f"{sharpest['width']}x{sharpest['height']}"} if sharpest['width'] else {}
    readings['moves'] = 'yes' if cam.ptz else 'no'
    if info['FirmwareVersion']:
        readings['firmware'] = info['FirmwareVersion']
    name = ' '.join(x for x in (info['Manufacturer'], info['Model']) if x) or 'camera'
    return {'online': True, 'detail': f"{name} at {config.get('host')}", 'readings': readings,
            'has': ['camera'] + (['ptz'] if cam.ptz else [])}


def run(device, capability, action, value, config, secrets, call_tool):
    try:
        cam = _camera(device, config, secrets)
        words = str(value or '').split()
        if capability == 'camera' and action == 'look':
            if words and words != ['sharp']:
                return "look: leave the value empty, or say 'sharp' for the full-size picture.", False
            return _seen(_picture(cam, sharp=bool(words)),
                         f"A picture from the camera {device['id']}{_where(device)}.")
        if capability == 'ptz':
            if action == 'stop':
                cam.stop()
                return f"{device['id']} is stopped.", True
            if action == 'save':
                if len(words) != 1 or not words[0].isdigit() or not 1 <= int(words[0]) <= SLOTS:
                    return f"save: the value is a number from 1 to {SLOTS}. Example: 1", False
                cam.save(int(words[0]))
                return (f"Where {device['id']} points now is stored under {int(words[0])}. Give that number "
                        "a name under Places and Sapphire can go there."), True
            if action == 'move':
                if not words or words[0].lower() not in TURNS or len(words) > 2:
                    return ("move: a direction, then how far from 1 to 100 if you like. "
                            "Directions: left, right, up, down. Example: left 30"), not words
                far = _amount(words[1] if len(words) > 1 else '', 'move')
                x, y = TURNS[words[0].lower()]
                return _turned(cam, device, far * FULL_TURN, f"Turned {words[0].lower()}",
                               x=x * SPEED, y=y * SPEED)
            if action == 'zoom':
                if not words or words[0].lower() not in ('in', 'out') or len(words) > 2:
                    return "zoom: in or out, then how far from 1 to 100 if you like. Example: in 30", not words
                far = _amount(words[1] if len(words) > 1 else '', 'zoom')
                return _turned(cam, device, far * FULL_ZOOM, f"Zoomed {words[0].lower()}",
                               zoom=1 if words[0].lower() == 'in' else -1)
            if action == 'goto':
                name, slot = _place(config, value)
                return _sent(cam, device, lambda: cam.goto(slot), f"Turned to {name}")
    except Problem as e:
        return str(e), False
    return f"A camera has no {capability} / {action}.", False
