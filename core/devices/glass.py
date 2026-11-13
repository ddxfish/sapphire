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
import collections
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


# A 4x4 ordered dither (Bayer), as fractions of one step: RGB565 keeps 5 bits of red and blue and 6 of
# green, so a smooth dark gradient steps, and where red steps where blue does not, a thin band reads
# warmer (Krem saw one in her scene's fog on the speaker, "very slight but I can tell every time",
# 2026-11-12). A fixed pattern spreads each step over its neighbours and repaints identically; the offset
# is under one step, so a colour that is exactly a level (pure red, black, white) is left as it is.
_BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def pack565(img):
    """A PIL RGB image as the bytes a small panel paints: RGB565 big-endian, dithered."""
    import numpy as np
    a = np.asarray(img, dtype=np.uint16)
    h, w = a.shape[:2]
    t = np.tile(np.array(_BAYER, dtype=np.uint16), (h // 4 + 1, w // 4 + 1))[:h, :w]
    r = np.minimum(a[..., 0] + (t >> 1), 255) >> 3          # a step of 8: offsets 0..7
    g = np.minimum(a[..., 1] + (t >> 2), 255) >> 2          # a step of 4: offsets 0..3
    b = np.minimum(a[..., 2] + (t >> 1), 255) >> 3
    return (r << 11 | g << 5 | b).astype('>u2').tobytes()


def cover(raw, width, height):
    """An image cropped to fill width x height, as the bytes a small panel
    paints (RGB565 big-endian). A background fills the glass; her `picture`
    fits inside it instead (satellite.rgb565)."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    img = ImageOps.fit(img, (width, height), Image.Resampling.LANCZOS)
    return pack565(img)


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
    from core.chat.history import landing_chat
    sm = get_system().llm_chat.session_manager
    chat = str((part.get('config') or {}).get('chat') or '').strip() or landing_chat(sm)   # unnamed: where its voice lands (voice.chat_for), the same name
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
            try:
                k = _lookup(self.device_id)                    # the board's beat, asked of it at most once a minute: a setting or a program update changes it
            except Exception:
                k = self.known
            fps = max(1, min(30, int(k['fps'] or STREAM_FPS)))
            if fps != self.fps:
                logger.info(f"[DEVICES] {self.device_id}: its glass paints at {fps} fps now (was {self.fps})")
                self.fps = fps
            due = time.monotonic() + 1.0 / self.fps
            with _lock:
                own = _owners.get(self.device_id)
            path = f'/screen/picture?format=jpeg&seconds={own.hold if own else STREAM_SECONDS}' + ('&taps=1' if own else '')
            try:
                r = satellite._call('POST', path, k['config'], k['secrets'], timeout=5, data=jpeg,
                                    headers={'Content-Type': 'image/jpeg'}, keep=True, port=k.get('door') or None)   # one socket, on the board's glass door when it has one
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


FACE_MODES = ('off', 'talking', 'always')   # her face on a glass: never, while she talks, or always (a Devices page field, `avatar`)


def face_mode(said, av):
    """A board's mode for her face: what its Devices page says, else While she talks on a board that
    takes her as one stream (an S3 with PSRAM: the pendant) and Off on a glass that only takes frames
    (a CYD reader: a chat and status reader first). Krem, 2026-11-12."""
    said = str(said or '').strip().lower()
    return said if said in FACE_MODES else ('talking' if av else 'off')


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _learn(row, config, secrets, screen, plays=None):
    w, h = int(screen.get('w') or 0), int(screen.get('h') or 0)
    if not (screen.get('jpeg') and screen.get('on', True) and min(w, h) >= 64):
        return None
    zones = _zones(screen.get('zones'))
    side = min(w, h) // 16 * 16
    av_w, av_h = _int(screen.get('av_w'), side) // 16 * 16, _int(screen.get('av_h'), side) // 16 * 16
    if not (64 <= av_w <= w + OVERSCAN and 64 <= av_h <= h + OVERSCAN):
        av_w = av_h = side                       # a face box the glass cannot hold: the square, as every board before 0.6.0
    known = {'config': config, 'secrets': secrets, 'w': w, 'h': h, 'fps': int(screen.get('fps') or STREAM_FPS), 'at': time.monotonic(),
             'door': int(screen.get('door') or 0),       # a second port the board takes frames on, in its own task (the pendant from 0.1.45)
             'av': bool(screen.get('av')),               # takes her voice and her face as ONE stream: POST /face/packet (the pendant from 0.2.0)
             'av_w': av_w, 'av_h': av_h,                 # the face box a packet's frames fill (0.6.0; before, the glass's square)
             'av_frames': max(1, _int(screen.get('av_frames'), AV_FRAMES)),   # frame entries a packet may carry: past it the board refuses the packet whole
             'av_v': _int(screen.get('av_v'), 1),        # the packet's shape: 1 = a JSON frame list, 2 = a binary frame table (avatar.h)
             'av_tells': bool(screen.get('av_tells')),   # it says when her tile comes up or goes (face/shown): nothing is sent while she is hidden
             'plays': plays if isinstance(plays, dict) else {},   # the sound it plays (/health plays): the stream's voice is fitted to it
             'avatar': face_mode((config or {}).get('avatar'), screen.get('av')),   # her face here: off | talking | always (Devices page)
             'fit': 'cover' if str((config or {}).get('avatar_fit') or '').lower() == 'cover' else 'contain',   # Devices page, Avatar tab
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
    if known and (known.get('at') is None or time.monotonic() - known['at'] < 60):   # the minute the docstring promised (2026-11-03); an entry without an age was put there by hand (tests) and stands
        return known
    row = engine.rows().get(device_id)
    part = engine._part(row, 'satellite') if row else None
    if not part or not row.get('enabled', True):
        raise LookupError(f"{device_id} is not a satellite with a screen")
    config, secrets = part.get('config') or {}, engine._part_secrets(row['id'], 'satellite')
    try:
        health = satellite._health(row, config, secrets, fresh=known is not None)   # re-asking: a fresh answer, not the health cache's (else the minute is two)
    except Exception as e:
        if known:                            # a board rebooting or asleep: what it said last stands until it answers again
            logger.info(f"[DEVICES] {device_id}: not answering ({e}); its glass as last known")
            return known
        raise
    known = _learn(row, config, secrets, health.get('screen') or {}, health.get('plays'))
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
            said = satellite._health(row, config, secrets)
        except Exception:
            continue
        known = _learn(row, config, secrets, said.get('screen') or {}, said.get('plays'))
        if known:
            out.append((row['id'], known['w'], known['h'], known['fps']))
    return out


def takes(device_id):
    """What that board's glass takes: {'w', 'h', 'fps', 'max_bytes', 'taps', 'zones'}.
    `taps` = a stream may own its taps (claim), `zones` the places a tap is
    named by. Raises LookupError for a board with no glass that takes frames."""
    k = _lookup(device_id)
    return {'w': k['w'], 'h': k['h'], 'fps': k['fps'], 'max_bytes': FRAME_MAX, 'taps': k['taps'], 'zones': list(k['zones']),
            'avatar': k.get('avatar') or face_mode(None, k.get('av'))}


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


# --- her voice and her face as one stream --------------------------------------------------
# An avatar plugin hands core packets (audio + the frames due within it, on one clock, see
# firmware/pendant/main/avatar.h); one sender per board posts them to its glass door, kept
# socket, and HOLDS when the board's queue is full (429): audio is never dropped. While a
# stream is live for a board, core's own sentences to it are skipped (satellite.play): her
# voice is in the packets. (2026-11-03: two clocks drifted however they were glued.)

AV_HOLD_S = 0.25                   # a full queue: ask again this soon
AV_FRAMES = 32                     # frame entries a packet may carry when a board states none (avatar.h AV_FRAMES_MAX before 0.6.0)
OVERSCAN = 16                      # a face box may run this far past its glass: the 16 px grid rounded up (the pendant's 284 rows -> 288, 2 px under each curved edge)
AV_LIVE_S = 3.0                    # a voice packet this recent: something of hers was going (av_clear's honest "Stopped"); never ownership
_av = {}
_av_last = {}
_av_claim = {}                     # device id -> core's stream id: that board's one-stream carries THAT reply's voice, so core's sentence
                                   # door skips it there and only it. Set by the feeder at the reply's start, ended at its end or a stop
                                   # (3 s of recency stood in for this, and a pause in her reply handed a sentence to both doors, scouts 2026-11-05)


class _AvSender:
    def __init__(self, device_id, known):
        self.device_id, self.known = device_id, known
        self.items = collections.deque()
        self.cv = threading.Condition()
        self.alive = True
        self.sent = self.held = self.failed = 0
        self.lost = 0                           # packets that never painted whole: dropped here, failed, or the board asked for a key. A rise = the builder sends a key
        self.refused_rate = False               # the board said 415 once: told in the log once, not every second
        self.last_error = ''
        self.shown = True                       # the board's last answer: her tile is up on a lit screen
        self.thread = threading.Thread(target=self._run, daemon=True, name=f'glass-av-{device_id}')
        self.thread.start()

    def put(self, packet):
        with self.cv:
            while len(self.items) > 4:              # four seconds ahead is plenty: the oldest goes
                self.items.popleft()
                self.lost += 1
            self.items.append(packet)
            self.cv.notify()

    def drop(self):
        """Everything not yet sent goes: she was stopped."""
        with self.cv:
            self.items.clear()

    def status(self):
        return {'sent': self.sent, 'held': self.held, 'failed': self.failed, 'lost': self.lost, 'queued': len(self.items),
                'shown': self.shown, 'last_error': self.last_error}

    def _run(self):
        from core.devices.drivers import satellite
        while True:
            with self.cv:
                while not self.items and self.alive:
                    if not self.cv.wait(timeout=STREAM_IDLE_S):
                        self.alive = False
                if not self.alive:
                    break
                packet = self.items[0]
            try:
                k = _lookup(self.device_id)
            except Exception:
                k = self.known
            try:
                r = satellite._call('POST', '/face/packet', k['config'], k['secrets'], timeout=5, data=packet,
                                    headers={'Content-Type': 'application/octet-stream'}, keep=True, port=k.get('door') or None)
            except satellite.Problem as e:
                if getattr(e, 'status', 0) == 429 or '429' in str(e) or 'full' in str(e).lower():
                    self.held += 1
                    time.sleep(AV_HOLD_S)
                    continue                            # the same packet, again
                self.failed += 1
                if getattr(e, 'status', 0) == 415:                 # it plays another rate or codec than it said a minute ago (a speaker_rate switch): ask it again now
                    with _lock:
                        k['at'] = 0.0                          # BEFORE the loss is counted: the builder re-reads on the count (scout)
                self.lost += 1
                self.last_error = str(e)[:160]
                if getattr(e, 'status', 0) == 415:
                    if not self.refused_rate:
                        self.refused_rate = True
                        logger.warning(f"[DEVICES] {self.device_id}: her stream's sound refused (415): its format is asked again")
                with self.cv:
                    if self.items and self.items[0] is packet:
                        self.items.popleft()
                time.sleep(0.2)
                continue
            except Exception as e:
                self.failed += 1
                self.lost += 1
                self.last_error = str(e)[:160]
                with self.cv:
                    if self.items and self.items[0] is packet:
                        self.items.popleft()
                time.sleep(0.2)
                continue
            self.sent += 1
            try:
                ans = r.json()
                self.shown = bool(ans.get('shown', True))
                if not (k or {}).get('av_tells'):           # a board that tells (face/shown) is believed: an answer in flight was older than its push
                    with _lock:
                        _shown[self.device_id] = self.shown
                if ans.get('need_key'):             # a region the board could not paint: its face is not ours until a key (0.6.0)
                    self.lost += 1
                if ans.get('stopped'):              # the board is stopped, or hears a question: it dropped her voice, and what waits here goes too (0.5.0)
                    self.drop()
            except Exception:
                self.shown = True                   # an older board does not say: treat her as shown
            with self.cv:
                if self.items and self.items[0] is packet:
                    self.items.popleft()
        with _lock:
            if _av.get(self.device_id) is self:
                del _av[self.device_id]


def av_boards():
    """[(device id, side)] of the boards that take her voice and face as one stream, asked of each
    (once a minute): in a fresh process nothing is learned yet, and a list read off the cache was
    empty until something else asked, so the plugin took the old frame path after every restart (2026-11-03)."""
    out = []
    for d, w, h, fps in stream_boards(online_only=True):   # a board the health keeper knows is down is not asked: each one cost a full timeout, on the hook's thread before her first word (scouts 2026-11-05)
        try:
            if _lookup(d).get('av'):
                out.append((d, min(w, h) // 16 * 16))
        except Exception:
            pass
    return out


def av_spec(device_id):
    """What a one-stream board takes, for whoever builds its packets: {'w', 'h' (the face box),
    'frames' (entries a packet may carry), 'v' (the packet's shape), 'plays' (its sound), 'fps'}."""
    k = _lookup(device_id)
    return {'w': k.get('av_w') or min(k['w'], k['h']) // 16 * 16, 'h': k.get('av_h') or min(k['w'], k['h']) // 16 * 16,
            'frames': k.get('av_frames') or AV_FRAMES, 'v': k.get('av_v') or 1, 'plays': dict(k.get('plays') or {}), 'fps': k['fps'],
            'tells': bool(k.get('av_tells')), 'fit': k.get('fit') or 'contain'}


def forget(device_id=None):
    """What glass learned of a board (or of all): its next use asks it again (a device was saved)."""
    with _lock:
        if device_id is None:
            _known.clear()
        else:
            _known.pop(device_id, None)


def av_lost(device_id):
    """Packets to that board that never painted whole, so far: a rise means its face is not the one last sent."""
    with _lock:
        s = _av.get(device_id)
    return s.lost if s else 0


def av_packet(device_id, packet, voice=True):
    """One packet (bytes, the format in avatar.h) for that board's stream. Returns the sender's counts.
    `voice`: the packet carries her voice; only those make the stream live (her idle never silences core's own sentences)."""
    k = _lookup(device_id)
    if not k.get('av'):
        raise LookupError(f"{device_id} does not take her voice and face as one stream")
    with _lock:
        s = _av.get(device_id)
        if s is None or not s.alive:
            s = _av[device_id] = _AvSender(device_id, k)
        if voice:
            _av_last[device_id] = time.monotonic()
    s.put(packet)
    return s.status()


_av_stoppers = {}                  # device id -> callable: whoever feeds that board's stream, told to stop at its source


def av_on_stop(device_id, fn, was=None):
    """Whoever feeds a board's stream registers how to stop it at the source (the face server's
    queue holds her reply long after core's turn is over: a stop that only cleared the board
    paused her a second and she resumed, Krem 2026-11-03). None unregisters; with `was`, only
    when that is the one registered (a feeder's late close after a reload took the new feeder's
    stopper with it, scouts 2026-11-05)."""
    with _lock:
        if fn is None:
            if was is None or _av_stoppers.get(device_id) == was:
                _av_stoppers.pop(device_id, None)
        else:
            _av_stoppers[device_id] = fn


def av_clear(device_id, upstream=True):
    """She was stopped: what core still holds for that board goes, and the board drops what it queued
    (POST /face/packet?clear=1). `upstream`: the source is told too (av_on_stop); the source's own
    flush handler passes False, or the two would call each other forever. Without this the seconds
    already in flight played on after a stop (the board's button, 2026-11-03)."""
    with _lock:
        s = _av.get(device_id)
        live = time.monotonic() - _av_last.get(device_id, 0) < AV_LIVE_S   # her voice was going there this moment
        _av_last.pop(device_id, None)
        live = bool(_av_claim.pop(device_id, None)) or live               # a reply claimed for that board was on its way too; the stop ends the claim
        stopper = _av_stoppers.get(device_id) if upstream else None
        known = _known.get(device_id) or (s.known if s else None)   # the board as last learned, not as the sender was born knowing it: a re-keyed or re-addressed board was cleared at its old door (scouts 2026-11-05)
    if not s and not stopper and not live:
        return False                            # no stream of hers to that board, none claimed or recent: nothing to clear, no board to call
    was = live or bool(s and s.items)
    if stopper:
        try:
            was = bool(stopper()) or was
        except Exception as e:
            logger.info(f"[DEVICES] {device_id}: her stream's source not stopped ({e})")
    if s:
        s.drop()
    try:
        k = known or _lookup(device_id)        # what the sender knows: a stop never waits on a health call
        from core.devices.drivers import satellite
        satellite._call('POST', '/face/packet?clear=1', k['config'], k['secrets'], timeout=3, data=b'',
                        headers={'Content-Type': 'application/octet-stream'}, port=k.get('door') or None)
    except Exception as e:
        logger.info(f"[DEVICES] {device_id}: her stream not cleared ({e})")
    return was                                  # only when something of hers was going: a stop with nothing playing is "Nothing to stop" (scouts 2026-11-05)


_shown = {}                        # device id -> her tile is up on its lit screen: the last word, a packet's answer or the board's own push


def av_shown(device_id):
    """Her tile is up on that board's lit screen, as it last said (True until a board says otherwise)."""
    with _lock:
        return _shown.get(device_id, True)


def av_tell_shown(device_id, shown):
    """The board says her tile came up or went (POST /api/devices/{id}/face/shown, 1.0.0): a board that
    tells is sent nothing while she is hidden, and its first frame after is a key."""
    device_id = str(device_id or '').strip().lower()
    with _lock:
        was = _shown.get(device_id)
        _shown[device_id] = bool(shown)
    if was != bool(shown):
        logger.info(f"[DEVICES] {device_id}: her tile {'up' if shown else 'hidden'} (the board says)")


def av_claim(device_id, sid):
    """The feeder of that board's stream is connected and will carry this reply's voice (core's stream id):
    core's sentence door skips that reply there, from its first sentence. One reply at a time per board."""
    with _lock:
        _av_claim[device_id] = str(sid or '')


def av_release(sid=None, device_id=None):
    """The claim ends: the reply is over or flushed (`sid`: wherever it is held), or the feeder of that
    board is gone (`device_id`: whatever it held). Core's sentence door carries what is left of a reply."""
    with _lock:
        if device_id is not None:
            _av_claim.pop(device_id, None)
        if sid:
            for d in [d for d, s in _av_claim.items() if s == str(sid)]:
                _av_claim.pop(d, None)


def av_carried(device_id, sid):
    """That reply's voice goes to that board in her stream: core's own sentence of it is not sent (one door per reply)."""
    with _lock:
        return bool(sid) and _av_claim.get(device_id) == str(sid)


def av_status(device_id):
    with _lock:
        s = _av.get(device_id)
        return s.status() if s else None
