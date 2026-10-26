# core/devices/glass.py - a satellite's small screen as a window into its chat.
#
# A board with a screen shows the name of the chat it talks in and that chat's
# scene (chat.background, the Scene Backgrounds library) behind its clock. All
# the image work is done here; the board stores what it is sent and paints it.
#
# sync(device_id) compares what the board says it shows (/health.screen) with
# what it should and sends only the difference. It is called when the board's
# events stream opens (a boot, a new link), when its settings are saved, and by
# a listener on the event bus when a chat's scene changes or the chat in use
# changes (core.event_bus on()). A private chat is never named or pictured on a screen in a room.
import io
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

NAME_MAX = 31                 # bytes of the chat's name the board keeps
_EVENTS = ('chat_settings_changed', 'chat_switched', 'chat_renamed', 'chat_deleted')
_lock = threading.Lock()
_worker = None
_dirty = threading.Event()
_busy = {}                    # device id -> a lock: one sync at a time per board


def _name(text, most=NAME_MAX):
    """A name as the board can keep it: cut on a whole character."""
    return str(text or '').encode('utf-8')[:most].decode('utf-8', 'ignore')


def cover(raw, width, height):
    """An image cropped to fill width x height, as the bytes a small panel
    paints (RGB565 big-endian). A background fills the glass; her `picture`
    fits inside it instead (satellite.rgb565)."""
    import numpy as np
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    img = ImageOps.fit(img, (width, height), Image.Resampling.LANCZOS)
    a = np.asarray(img, dtype=np.uint16)
    packed = (a[..., 0] >> 3) << 11 | (a[..., 1] >> 2) << 5 | (a[..., 2] >> 3)
    return packed.astype('>u2').tobytes()


BRAIN_MAX = 47                # bytes of the model's name the board keeps
_TRIM = re.compile(r'^#[0-9a-fA-F]{6}$')


def _brain(settings):
    """The model that answers in this chat, by name, as the chat's own
    resolver would pick it now. '' when it cannot be said. Asks no provider."""
    try:
        from core.chat.llm_providers import resolve
        picked = resolve.resolve(settings.get('llm_primary'), settings.get('llm_model'), health='skip')
        said = str(picked.effective_model or picked.display_name)
        return _name(said.rstrip('/').rsplit('/', 1)[-1], BRAIN_MAX)      # accounts/fireworks/models/glm-5p3 -> glm-5p3: the page is 320 px wide
    except Exception:
        return ''


def wanted(part):
    """What this part's screen should show: {'chat', 'scene', 'brain', 'trim'}.
    All empty for a private chat or one that is gone."""
    from core.api_fastapi import get_system
    sm = get_system().llm_chat.session_manager
    chat = str((part.get('config') or {}).get('chat') or '').strip() or sm.get_active_chat_name()
    settings = sm.get_settings_for(chat)
    if settings is None or settings.get('private_chat'):
        return {'chat': '', 'scene': '', 'brain': '', 'trim': ''}
    trim = str(settings.get('trim_color') or '').strip().lower()
    return {'chat': _name(chat), 'scene': str(settings.get('background') or ''),
            'brain': _brain(settings), 'trim': trim if _TRIM.match(trim) else ''}


def _words_differ(screen, want):
    """The name, the model, the trim: only what this board's program states is compared."""
    return any(k in screen and screen.get(k) != want[k] for k in ('chat', 'brain', 'trim'))


def _scene_bytes(scene):
    from core.routes import backgrounds
    if not backgrounds._NAME_RE.match(scene):
        return None
    path = backgrounds._full_path(scene)
    if not backgrounds._contained(path) or not path.is_file():
        return None
    return path.read_bytes()


def sync(device_id):
    """Bring one device's screen in line with its chat. Never raises. Says
    what it sent ([] = it already showed it), or None when the board could
    not be asked or told."""
    from core.devices import engine
    from core.devices.drivers import satellite
    device_id = str(device_id or '').strip().lower()
    with _lock:
        one = _busy.setdefault(device_id, threading.Lock())
    did = []
    with one:
        try:
            row = engine.rows().get(device_id)
            if not row or not row.get('enabled', True):
                return did
            for part in row.get('parts', []):
                if part.get('driver') != 'satellite':
                    continue
                config = dict(part.get('config') or {})
                secrets = engine._part_secrets(row['id'], 'satellite')
                device = engine._brief(row)
                screen = satellite._health(device, config, secrets, fresh=True).get('screen')
                if not isinstance(screen, dict) or 'chat' not in screen:      # no screen, or a program before 0.5.3
                    continue
                want = wanted(part)
                scene = want['scene']
                if _words_differ(screen, want):
                    satellite.screen_chat(config, secrets, want['chat'], want['brain'], want['trim'])
                    did.append(f"chat {want['chat'] or '(none)'} (model {want['brain'] or '-'}, trim {want['trim'] or '-'})")
                if 'background' in screen and screen.get('background', '') != scene:   # a board that states no background draws none (the reader)
                    raw = _scene_bytes(scene) if scene else None
                    if raw:
                        w, h = int(screen['w']), int(screen['h'])
                        satellite.screen_scene(config, secrets, scene, w, h, cover(raw, w, h))
                        did.append(f'scene {scene}')
                    elif screen.get('background'):
                        satellite.screen_scene(config, secrets, '')
                        did.append('scene cleared')
                if did:
                    satellite.forget_health(row['id'])
                    logger.info(f"[DEVICES] {row['id']}: screen now shows {', '.join(did)}")
        except Exception as e:
            logger.info(f"[DEVICES] {device_id}: its screen was not brought up to date: {e}")
            return None
    return did


def behind(config, screen):
    """Does this /health screen block show something other than its chat?
    The level check: the driver's status() asks at every probe, so a push
    that was lost (the board was still starting, the link dropped) is made
    good at the next one, whatever was missed."""
    if not isinstance(screen, dict) or 'chat' not in screen:
        return False
    try:
        want = wanted({'config': config})
    except Exception:
        return False
    return _words_differ(screen, want) or ('background' in screen and screen.get('background', '') != want['scene'])


RETRY_AFTER = (3, 10)         # seconds: a board that has just linked may not answer its own door yet


def _sync_until_told(device_id):
    for wait in (0,) + RETRY_AFTER:
        if wait:
            time.sleep(wait)
        if sync(device_id) is not None:
            return


def sync_soon(device_id):
    threading.Thread(target=_sync_until_told, args=(device_id,), daemon=True, name='device-glass').start()


def sync_all():
    """Every satellite that has said it has a screen."""
    from core.devices import engine
    from core.devices.drivers import satellite
    for row in engine.rows().values():
        if row.get('enabled', True) and any(p.get('driver') == 'satellite' for p in row.get('parts', [])) \
                and 'screen' in satellite._has(row):
            sync(row['id'])


def _work():
    while True:
        _dirty.wait()
        _dirty.clear()
        try:
            sync_all()
        except Exception as e:
            logger.warning(f"[DEVICES] screens were not brought up to date: {e}")


def start():
    """Follow the bus: a chat's scene or the chat in use changed. Safe to call
    twice. The listener only raises a flag; the worker does the sending, so a
    board that is slow to answer never holds up whoever published."""
    global _worker
    from core.event_bus import get_event_bus
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_work, daemon=True, name='device-glass')
        _worker.start()
        get_event_bus().on(_EVENTS, lambda event: _dirty.set())


# --- a stream on the glass ----------------------------------------------------
# JPEG frames from any sender: a plugin in process (stream_frame) or a browser
# through POST /api/devices/{id}/screen/frame (a game casting its stage). The
# board paints frames on a beat of its own (screen.fps in /health, 0.7.8); here
# each board has one sender thread paced to that beat, and the newest frame
# wins: one that arrives while the last is in flight replaces the one waiting,
# nothing queues, and whoever made the frame never waits on the board. Each
# board is reached with its own secrets, as every call is. Nothing here knows
# what the frames show.
#
# A sender may also OWN the board's taps while its stream runs (claim, 0.9.0
# firmware): the board then names the place under every tap (screen.zones) and
# sends it as a raw press, which voice.pressed hands to the owner instead of
# the bindings; no tap dismisses the stream, and a long press on it is the
# player's way out (the next frame is answered `hidden`: the owner is told).

STREAM_SECONDS = 3            # the board's own page comes back this long after the last frame
STREAM_IDLE_S = 15            # a sender with nothing to send for this long ends; the next frame starts one again
STREAM_FPS = 3                # the beat when a board states none
FRAME_MAX = 256 * 1024        # a frame larger than this is refused (the board's own limit)
HOLD_DEFAULT = 60             # seconds an owned stream stays up after its last frame (a table changes on moves, not on a clock)
ZONE_NAME = re.compile(r'[^a-z0-9-]')

_known = {}                   # device id -> {'config', 'secrets', 'w', 'h', 'fps', 'taps', 'zones'}
_streams = {}                 # device id -> _Stream
_owners = {}                  # device id -> _Owner: who has the board's taps while a stream runs


class _Owner:
    def __init__(self, who, on_press, on_gone, hold):
        self.who, self.on_press, self.on_gone, self.hold = who, on_press, on_gone, hold
        self.gone = False


class _Stream:
    def __init__(self, device_id, known):
        self.device_id = device_id
        self.known = known
        self.fps = max(1, min(30, int(known['fps'] or STREAM_FPS)))
        self.next = None
        self.sent = self.replaced = self.failed = 0
        self.last_error = ''
        self.alive = True
        self.cv = threading.Condition()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f'glass-stream-{device_id}')
        self.thread.start()

    def offer(self, jpeg):
        with self.cv:
            if self.next is not None:
                self.replaced += 1
            self.next = jpeg
            self.cv.notify()

    def stop(self):
        with self.cv:
            self.alive = False
            self.next = None
            self.cv.notify()

    def status(self):
        return {'fps': self.fps, 'sent': self.sent, 'replaced': self.replaced, 'failed': self.failed,
                'last_error': self.last_error}

    def _run(self):
        from core.devices.drivers import satellite
        period = 1.0 / self.fps
        due = time.monotonic()
        while True:
            with self.cv:
                while self.next is None and self.alive:
                    if not self.cv.wait(timeout=STREAM_IDLE_S):
                        self.alive = False             # idle: this sender is done
                if not self.alive:
                    break
            wait = due - time.monotonic()               # the beat: never faster than the board paints
            if wait > 0:
                time.sleep(wait)                        # a frame arriving meanwhile replaces the one waiting
            with self.cv:
                jpeg, self.next = self.next, None
            due = time.monotonic() + period
            with _lock:
                own = _owners.get(self.device_id)
            path = f'/screen/picture?format=jpeg&seconds={own.hold if own else STREAM_SECONDS}' + ('&taps=1' if own else '')
            try:
                r = satellite._call('POST', path, self.known['config'], self.known['secrets'],
                                    timeout=5, data=jpeg, headers={'Content-Type': 'image/jpeg'})
                self.sent += 1
            except Exception as e:
                self.failed += 1
                self.last_error = str(e)[:160]
                continue
            if own and _said_hidden(r):
                _gone(self.device_id, own)
        with _lock:
            if _streams.get(self.device_id) is self:
                del _streams[self.device_id]


def _said_hidden(r):
    """The board answered a frame `hidden`: tapped away, or a tapping stream left by a long press."""
    try:
        from core.devices.drivers import satellite
        return bool(satellite._json(r).get('hidden'))
    except Exception:
        return False


def _gone(device_id, own):
    """The player left an owned stream (the board said `hidden`): the owner is told once and forgotten."""
    with _lock:
        if _owners.get(device_id) is not own or own.gone:
            return
        own.gone = True
        del _owners[device_id]
    if own.on_gone:
        threading.Thread(target=own.on_gone, daemon=True, name=f'glass-gone-{device_id}').start()


def _zones(said):
    """The places a board names its taps by, as /health screen.zones states them: [{'name', 'x', 'y', 'w', 'h'}]."""
    out = []
    for z in said if isinstance(said, list) else []:
        try:
            name = ZONE_NAME.sub('', str(z.get('name') or '').strip().lower())[:24]
            box = {k: int(z[k]) for k in ('x', 'y', 'w', 'h')}
            if name and box['w'] > 0 and box['h'] > 0:
                out.append({'name': name, **box})
        except Exception:
            continue
    return out


def _learn(row, config, secrets, screen):
    w, h = int(screen.get('w') or 0), int(screen.get('h') or 0)
    if not (screen.get('jpeg') and screen.get('on', True) and min(w, h) >= 64):
        return None
    zones = _zones(screen.get('zones'))
    known = {'config': config, 'secrets': secrets, 'w': w, 'h': h, 'fps': int(screen.get('fps') or STREAM_FPS),
             'taps': bool(screen.get('taps')) and bool(zones), 'zones': zones}
    with _lock:
        _known[row['id']] = known
    return known


def _lookup(device_id):
    """What that board's glass takes, asked of it at most once a minute."""
    from core.devices import engine
    from core.devices.drivers import satellite
    with _lock:
        known = _known.get(device_id)
    if known:
        return known
    row = engine.rows().get(device_id)
    part = engine._part(row, 'satellite') if row else None
    if not part or not row.get('enabled', True):
        raise LookupError(f"{device_id} is not a satellite with a screen")
    config, secrets = part.get('config') or {}, engine._part_secrets(row['id'], 'satellite')
    known = _learn(row, config, secrets, satellite._health(row, config, secrets).get('screen') or {})
    if not known:
        raise LookupError(f"{device_id} has no glass that takes frames (firmware 0.7.7+, its screen on)")
    return known


def stream_boards(online_only=False):
    """Every enabled satellite whose glass is on and takes a JPEG frame
    (firmware 0.7.7+, `screen.jpeg` in its /health): [(device id, w, h, fps)],
    the whole glass and the beat it paints on. Each board's /health is asked at
    most once a minute. `online_only`: a board the health keeper believes offline
    is not asked at all (an offline board costs a full timeout to ask)."""
    from core.devices import engine, health
    from core.devices.drivers import satellite
    out = []
    with _lock:
        _known.clear()
    for row in engine.rows().values():
        part = engine._part(row, 'satellite')
        if not part or not row.get('enabled', True):
            continue
        if online_only and not (health.view(row['id']) or {}).get('online', True):
            continue
        try:
            config, secrets = part.get('config') or {}, engine._part_secrets(row['id'], 'satellite')
            screen = satellite._health(row, config, secrets).get('screen') or {}
        except Exception:
            continue
        known = _learn(row, config, secrets, screen)
        if known:
            out.append((row['id'], known['w'], known['h'], known['fps']))
    return out


def takes(device_id):
    """What that board's glass takes: {'w', 'h', 'fps', 'max_bytes', 'taps', 'zones'}.
    `taps` = a stream may own its taps (claim), `zones` the places a tap is
    named by. Raises LookupError for a board with no glass that takes frames."""
    k = _lookup(device_id)
    return {'w': k['w'], 'h': k['h'], 'fps': k['fps'], 'max_bytes': FRAME_MAX, 'taps': k['taps'], 'zones': list(k['zones'])}


def stream_frame(device_id, jpeg):
    """One JPEG frame for that board's glass (up to its w x h, sides multiples
    of 16): handed to the board's sender, which paces frames to the board's beat
    and keeps only the newest. Returns at once with the sender's counts. Raises
    LookupError when the board has no glass that takes frames, ValueError on a
    frame too big."""
    if not jpeg or len(jpeg) > FRAME_MAX:
        raise ValueError(f"a frame is up to {FRAME_MAX // 1024} KB")
    known = _lookup(device_id)
    with _lock:
        stream = _streams.get(device_id)
        if stream is None or not stream.alive:
            stream = _streams[device_id] = _Stream(device_id, known)
    stream.offer(jpeg)
    return stream.status()


def stream_end(device_id):
    """The sender stops and the board's own page comes back now."""
    from core.devices.drivers import satellite
    with _lock:
        stream = _streams.pop(device_id, None)
        known = _known.get(device_id)
    if stream:
        stream.stop()
    if known:
        try:
            satellite._call('POST', '/screen/picture', known['config'], known['secrets'], timeout=5, json={'clear': True})
        except Exception:
            pass


def stream_status(device_id=None):
    """The senders' counts: one board's, or every board's."""
    with _lock:
        if device_id is not None:
            s = _streams.get(device_id)
            return s.status() if s else None
        return {d: s.status() for d, s in _streams.items()}


# --- a stream that owns the board's taps ------------------------------------

def claim(device_id, on_press, on_gone=None, hold=HOLD_DEFAULT, who=''):
    """Own that board's taps while a stream runs: its frames go with `taps=1`
    and stay up `hold` seconds after the last one; every tap on the stream
    reaches `on_press(zone, how)` (the zone's name from `zones`, 'short'), on
    a thread of its own, and never the bindings or a chat turn. When the
    player leaves (a long press on the board), `on_gone()` is called once and
    the claim is over. Returns what the glass takes (`takes`). Raises
    LookupError for a glass that does not pass its taps on (firmware before
    0.9.0, or none), RuntimeError when another owner has it."""
    what = takes(device_id)
    if not what['taps']:
        raise LookupError(f"{device_id}'s glass does not pass its taps on (firmware 0.9.0 or later)")
    with _lock:
        held = _owners.get(device_id)
        if held is not None:
            raise RuntimeError(f"{device_id}'s glass is already taken{' by ' + held.who if held.who else ''}")
        _owners[device_id] = _Owner(who, on_press, on_gone, max(5, min(3600, int(hold))))
    return what


def release(device_id):
    """The claim is over and the board's own page comes back now. Safe to call twice."""
    with _lock:
        own = _owners.pop(device_id, None)
    if own:
        own.gone = True
    stream_end(device_id)


def owner(device_id):
    """Who owns that board's taps now ('' for a nameless claim), or None."""
    with _lock:
        own = _owners.get(device_id)
    return own.who if own else None


def press(device_id, button, how):
    """A press on that board, from voice.pressed: True when a claim took it
    (the owner's on_press runs on a thread of its own: the board waits on the
    answer), False when nobody owns the board's taps."""
    with _lock:
        own = _owners.get(device_id)
    if own is None:
        return False
    threading.Thread(target=own.on_press, args=(button, how), daemon=True, name=f'glass-press-{device_id}').start()
    return True


# The first senders (FlashHead) asked for a square and painted a face. Kept as
# the stream, one release: the frames ride the same sender and beat.
def face_boards():
    """[(device id, side)], side the largest square the glass paints, a multiple of 16."""
    return [(d, min(w, h) // 16 * 16) for d, w, h, fps in stream_boards()]


def face_frame(device_id, jpeg, seconds=3):
    """stream_frame(). The board's page comes back STREAM_SECONDS after the last frame."""
    stream_frame(device_id, jpeg)
