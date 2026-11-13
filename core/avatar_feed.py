"""Her face as a feed.

ONE source at a time (an avatar plugin: the 3D avatar, FlashHead) hands in
slices of frames, with her voice when it has it. The hub plays them out, on one
clock, to every screen that shows her: the glass of each board, as frames alone
or, where the board takes it, as her voice and face in one stream.

    words = avatar_feed.register("avatar", stop=fn)     # "" = yours; else why not
    avatar_feed.wake("avatar")                           # a reply starts: the boards open and wait for her first word
    avatar_feed.packet("avatar", "speech", frames, 25, pcm=pcm, sid=sid, device=dev)
    avatar_feed.packet("avatar", "idle", [jpeg], 5)
    avatar_feed.flush("avatar", sid)                     # she was stopped
    avatar_feed.release("avatar")                        # the plugin unloads: ALWAYS, or no other avatar can be her face

A slice is a run of frames (half a second to a sentence) with the voice of the same
span. Slices of one reply follow each other on one clock, so a source may hand them
in faster than she speaks: the hub holds them until their time. `packet` never
blocks and never raises: it answers False for a slice it will not play. No call here
waits on a board: each board has a thread of its own, the board list is asked on
another. `watching()` says whether any screen wants her, so a renderer rests when
none does.

Lifted from FlashHead's screens.py (its playout clock, pushers and one-stream
builders, with the reasons they are shaped this way). FlashHead moves onto this
next; a webcam and a browser door are outputs to come and take the same slices.
"""
import collections
import io
import json
import logging
import struct
import threading
import time

logger = logging.getLogger(__name__)

TALK_FPS, IDLE_FPS = 25, 2      # frames-only boards: every frame the beat allows while she talks, a gentle idle
IDLE_AV_FPS = 12                # a one-stream board's idle: a still face costs nothing at any rate (8 read as choppy; changes-only made 12 cheaper than 8 was, 2026-11-12)
JPEG_Q = 72                     # what goes over a board's WiFi (80 was 12 KB a whole face at 240 px, 72 is 10)
RATE_S = 10                     # the window a board's kbps is told over (status)
HOLD_S = 4                      # screens "talking": her face stays this long after her last word has played
WAKE_S = 90                     # woken, no word yet (a thinking model, a tool call first): the boards wait this long for it
AHEAD_S = 0.15                  # a slice's first frame is due this long after it lands
REST_S = 5                      # nothing to show for this long: the boards are let go
BOARDS_S = 30                   # the board list is asked again this often while she is wanted
NO_BOARDS_S = 10                # ... and this often while there is none (an offline board costs a health timeout each time)
AV_LEAD_S = 2.0                 # a one-stream board gets a slice this long before its start: glass keeps five, the rest waits here with its voice
QUEUE_MAX = 64                  # slices waiting for the hub thread; the oldest goes (her idle first)
SLICE_MAX_S = 120               # a slice longer than this is a mistake (one JPEG's bytes handed in as its frames)
KINDS = ("speech", "idle")
SCREENS = ("off", "talking", "always")

_lock = threading.Lock()
_hub = None
_offered = {}                   # avatar name -> when it last asked to be her face: the Devices page's choices
_modes = {"at": 0.0, "want": "off"}   # the strongest mode any screen asks for, read off the device rows (a few seconds old at most)
MODES_S = 2.0
RANK = {"off": 0, "talking": 1, "always": 2}


def _strongest(modes):
    return max(modes, key=lambda m: RANK.get(m, 0), default="off")


def _seat():
    """Who may be her face, as Settings > Devices > Settings says: an avatar's name, "off" for none,
    or "" (never chosen): the first avatar that asks."""
    try:
        import config
        return str(getattr(config, "AVATAR_FACE_SOURCE", "") or "").strip()
    except Exception:
        return ""


def _wanted():
    """The strongest mode any enabled screen asks for (Devices page, per board), from the device rows:
    no board is asked. A board that never said what it takes counts as wanting her while she talks,
    so a fresh install's avatar looks; the hub then asks each board and keeps only those that want her."""
    now = time.monotonic()
    if now - _modes["at"] < MODES_S:
        return _modes["want"]
    want = "off"
    try:
        from core.devices import engine, glass
        modes = []
        for row in engine.rows().values():
            part = engine._part(row, "satellite") if row.get("enabled", True) else None
            if not part:
                continue
            takes = part.get("takes")
            if isinstance(takes, list) and "screen.jpeg" not in takes:
                continue                         # no glass that takes a picture
            modes.append(glass.face_mode((part.get("config") or {}).get("avatar"),
                                         not isinstance(takes, list) or "screen.av" in takes))
        want = _strongest(modes)
    except Exception as e:
        logger.info(f"[avatar_feed] the screens' modes unread ({e})")
    _modes.update(at=now, want=want)
    return want


def mode_for(name):
    """What an avatar's screens loop does now: "off" when it is not the one chosen as her face, else
    the strongest mode its screens ask for (off | talking | always). Each screen keeps its own: the
    hub sends her idle only to a screen set Always."""
    _offered[name] = time.monotonic()
    seat = _seat()
    if seat == "off" or (seat and seat != name):
        return "off"
    h = _hub
    if not seat and h and h.name != name:
        return "off"                             # never chosen: the first that asked has her
    if h and h.name == name and h.looked is not None:
        return h.mode()                          # the boards that answered: an offline Always board never keeps the renderer drawing idle (scout)
    return _wanted()


def nudge():
    """A device was saved: the screens' modes and boards are read again now, not within a minute."""
    _modes["at"] = 0.0
    try:
        _glass().forget()
    except Exception:
        pass
    h = _hub
    if h:
        h.dirty = h.looking              # a look already out read the boards before this save: another follows it
        h.looked = None
        h.put(("tick",))


def offered():
    """The avatars that asked to be her face lately (their loops ask every few seconds while on)."""
    now = time.monotonic()
    return sorted(n for n, t in _offered.items() if now - t < 60)


def choose(name):
    """Settings > Devices > Settings: her face comes from `name` ("off" = none). Kept as
    AVATAR_FACE_SOURCE; the avatar that had her lets go at its next look (a second or so)."""
    from core.settings_manager import settings
    name = str(name or "").strip()
    settings.set("AVATAR_FACE_SOURCE", name, persist=True)
    h = _hub
    if h and h.name != name:
        release(h.name)                          # its loop sees "off" and stops reading its renderer
    nudge()
    logger.info(f"[avatar_feed] her face comes from {name or 'the first avatar on'}")


def _glass():
    from core.devices import glass
    return glass


def _rarely(n):
    return n in (1, 10, 100) or n % 1000 == 0


# ── the source's side ───────────────────────────────────────────────────────
def register(name, stop=None, screens="talking", face_px=0, fps=0):
    """Become her face. `stop(sid)`: a stop at a board reaches the source (True when
    something was dropped). `screens`: off | talking | always. `face_px`: a smaller
    face than the glass's square. `fps`: no more frames a second than this, whatever a
    board's beat (a source whose whole picture moves: each frame is a whole face over the
    board's WiFi, beside her voice). The same name again takes its new settings.
    Returns "" or the words of why not."""
    global _hub
    screens = str(screens or "talking").lower()
    if screens not in SCREENS:
        screens = "talking"
    _offered[name] = time.monotonic()
    seat = _seat()
    if screens == "off" or seat == "off" or (seat and seat != name):
        return f"{seat if seat not in ('', 'off') else 'no avatar'} is her face (Settings > Devices > Settings)."
    try:
        face_px = int(face_px or 0)
    except (TypeError, ValueError):
        face_px = 0
    try:
        fps = max(0, min(TALK_FPS, int(fps or 0)))
    except (TypeError, ValueError):
        fps = 0
    with _lock:
        if _hub and _hub.name != name:
            return f"{_hub.name} is her face now. Turn it off first: one avatar at a time."
        if _hub and _hub.alive:
            # The same avatar again (its loop saw its mode change, a setting saved): its new settings, the
            # hub and her stream kept. Closing it here blanked an Always screen ~20 s (2026-11-12).
            h = _hub
            h.stop, h.screens, h.cap = stop, screens, fps
            if h.face_px != face_px:
                h.face_px = face_px
                h.looked = None                  # the boards' boxes follow face_px: looked at again
            h.put(("tick",))
            return ""
        _hub = _Hub(name, stop, screens, face_px, fps)
    logger.info(f"[avatar_feed] {name} is her face ({screens})")
    return ""


def release(name):
    global _hub
    with _lock:
        if not _hub or _hub.name != name:
            return
        old, _hub = _hub, None
    old.close()
    logger.info(f"[avatar_feed] {name} let her face go")


def source():
    h = _hub
    return h.name if h else None


def _mine(name):
    h = _hub
    return h if h and h.name == name else None


def _bytes(b):
    return b if isinstance(b, bytes) else b.tobytes() if hasattr(b, "tobytes") else bytes(b)


def packet(name, kind, frames, fps, pcm=b"", sr=24000, sid="", device="", taken=False, fresh=False):
    """A slice: `frames` (a list of JPEG bytes) evenly across it at `fps`, `pcm` (16-bit
    mono at `sr`) her voice for the same span or empty. `kind`: speech | idle. `device`:
    the board whose reply this is (it alone plays the voice); `taken`: a browser or a
    call already plays it. `fresh`: this slice does not follow the last one (she paused: the
    source drew nothing meanwhile), so it starts now, not as a late continuation. False
    when the source is not her face or the slice is not one."""
    h = _mine(name)
    if not h:
        return False
    try:
        kind, fps = str(kind).lower(), float(fps)
        if isinstance(frames, (bytes, bytearray, memoryview)):
            frames = [frames]                                   # one picture, not a list of its bytes
        frames = [bytes(f) for f in (frames if frames is not None else ())]
        pcm = _bytes(pcm) if pcm is not None else b""
        pcm = pcm[:len(pcm) // 2 * 2]
        sr = int(sr or 24000)
        if kind not in KINDS or not 0.5 <= fps <= 240 or not 8000 <= sr <= 48000 or len(frames) / fps > SLICE_MAX_S:
            raise ValueError(f"kind {kind!r}, fps {fps!r}, sr {sr!r}, {len(frames)} frames")
    except Exception as e:
        h.refused += 1
        if _rarely(h.refused):
            logger.warning(f"[avatar_feed] a slice from {name} refused ({e}), {h.refused} so far")
        return False
    h.put(("packet", {"kind": kind, "fps": fps, "sr": sr, "sid": str(sid or ""),
                      "device": str(device or ""), "taken": bool(taken), "fresh": bool(fresh)}, frames, pcm))
    return True


def flush(name, sid=""):
    """She was stopped: what is queued for that reply goes, here and on the boards, and
    what the source still hands in of it is not played."""
    h = _mine(name)
    if h:
        h.put(("flush", str(sid or "")))


def wake(name, wait=0.3):
    """A reply starts: have the boards open before its first words arrive. They stay
    open until her first word (WAKE_S at most), however long it takes."""
    h = _mine(name)
    if h:
        h.woke.clear()
        h.put(("wake",))
        h.woke.wait(0.3 if wait is None else wait)


def carries(device_id):
    """That board gets her voice and face as one stream from the hub right now: a reply
    starting now rides in it (the source claims it, glass.av_claim)."""
    h = _hub
    s = h.session if h else None
    return bool(s and h.alive and h.session is s and device_id in s.av_ids)


def claim(name, device_id, sid):
    """That board's own reply starts and the hub streams to it as one stream: the reply's
    voice rides in the stream, and core's sentence door skips it there (glass.av_claim).
    The hub keeps the claim itself: core lets its own go when the reply is MADE, which for a
    short reply is before its first slice is even drawn (a whole reply mouthed in silence,
    2026-11-10). True when claimed; False = core's door carries that reply."""
    h = _mine(name)
    s = h.session if h else None
    b = s.builders.get(device_id) if s and h.alive and sid else None
    if not b:
        return False
    h.claims[device_id] = str(sid)       # on the hub, not the builder: a session rebuilt mid-reply keeps it (scout 2026-11-12)
    try:
        _glass().av_claim(device_id, sid)
    except Exception as e:
        h.claims.pop(device_id, None)
        logger.info(f"[avatar_feed] {device_id}: reply not claimed ({e})")
        return False
    return True


def watching():
    """Who wants her picture: {"boards": [ids], "av": [ids], "fps": beat, "open": bool}.
    Empty boards = no screen to draw for. `open`: the boards show her this moment (in
    "talking" they open at a wake and rest after her last word: a renderer that only
    draws for the boards rests with them)."""
    h = _hub
    if not h:
        return {"boards": [], "av": [], "fps": TALK_FPS, "open": False}
    if h.stale(time.monotonic()):
        h.put(("tick",))                 # the one asking is the reason to look again
    return {"boards": [b[0] for b in h.boards], "av": sorted(h.av), "fps": h.fps, "open": bool(h.session)}


def status():
    h = _hub
    if not h:
        return {"source": None, "seat": _seat(), "offered": offered()}
    s = h.session
    try:
        g = _glass()
        air = {d: g.av_status(d) for d in (list(s.builders) if s else ())}     # glass's sender for each: sent, held (the board's queue was full), failed
    except Exception:
        air = {}
    return {"air": air, "fps": h.fps, "beats": dict(h.beats), "modes": dict(h.modes), "seat": _seat(), "offered": offered(),
            "source": h.name, "screens": h.mode(), "boards": [b[0] for b in h.boards],
            "av": sorted(h.av), "open": bool(s), "packets": h.packets,
            "dropped": h.dropped, "refused": h.refused, "errors": h.errors,
            "streams": {d: b.status() for d, b in (list(s.builders.items()) if s else ())},
            "frames": {d: {"sent": p.sent, "fails": p.fails} for d, p in (list(s.pushers.items()) if s else ())}}


# ── the hub ─────────────────────────────────────────────────────────────────
class _Hub:
    def __init__(self, name, stop, screens, face_px, cap=0):
        self.name, self.stop, self.screens, self.face_px, self.cap = name, stop, screens, face_px, cap
        self.q, self.cv, self.alive = collections.deque(), threading.Condition(), True
        self.session = None
        self.boards, self.av, self.fps = [], set(), TALK_FPS
        self.beats = {}                          # device id -> the beat its glass paints at
        self.modes = {}                          # device id -> talking | always (a board set Off is not among the boards)
        self.claims = {}                         # device id -> the reply whose voice its stream carries (claim())
        self.dirty = False                       # a nudge came while a look was out: look again when it lands
        self.looked, self.looking = None, False
        self.waking = None                       # when she was woken with no word yet
        self.woke = threading.Event()
        self.flushed = collections.deque(maxlen=8)   # replies that were stopped: a late slice of one is not played
        self.packets = self.dropped = self.refused = self.errors = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"avatar-feed-{name}")
        self.thread.start()

    def put(self, item):
        with self.cv:
            if item[0] == "flush":           # what is still waiting of that reply is stale already
                sid = item[1]
                self.q = collections.deque(i for i in self.q
                                           if i[0] != "packet" or (sid and i[1]["sid"] != sid))
            self.q.append(item)
            while len(self.q) > QUEUE_MAX:
                old = (next((i for i in self.q if i[0] == "packet" and i[1]["kind"] == "idle"), None)
                       or next((i for i in self.q if i[0] == "packet"), None))
                if old is None:
                    break
                self.q.remove(old)
                self.dropped += 1
                if _rarely(self.dropped):
                    logger.warning(f"[avatar_feed] the hub is behind: a slice dropped, {self.dropped} so far")
            self.cv.notify()

    def close(self):
        with self.cv:
            self.alive = False
            self.cv.notify()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def stop_source(self, sid):
        try:
            return bool(self.stop and self.stop(sid))
        except Exception as e:
            logger.info(f"[avatar_feed] {self.name} not stopped at the source ({e})")
            return False

    def mode(self):
        """The strongest mode among her boards now: always opens a session on her idle."""
        return _strongest(self.modes.values()) if self.boards else "off"

    def stale(self, now):
        return not self.looking and (self.looked is None or now - self.looked > (BOARDS_S if self.boards else NO_BOARDS_S))

    def _run(self):
        self._take(*self._ask())                 # the first look, before any slice: what waits meanwhile is queued, not lost
        while True:
            with self.cv:
                if self.alive and not self.q:
                    self.cv.wait(1.0)
                if not self.alive:
                    break
                item = self.q.popleft() if self.q else None
            try:
                self._step(item)
            except Exception as e:               # one bad slice is one slice: the boards and their claims stay
                self.errors += 1
                if _rarely(self.errors):
                    logger.warning(f"[avatar_feed] a slice not played ({e!r}), {self.errors} so far")
        self._rest()

    def _ask(self):
        """The boards as glass knows them. Slow (a health call a board, a timeout for each
        that sleeps): never on the hub thread after its first look."""
        g, boards, av, fps = _glass(), [], set(), TALK_FPS
        try:
            boards = list(g.face_boards())
        except Exception as e:
            logger.info(f"[avatar_feed] boards unknown: {e}")
        try:
            av = {d for d, _ in g.av_boards()} & {b[0] for b in boards}
        except Exception:
            pass
        beats, modes = {}, {}
        for b in boards:
            try:
                t = g.takes(b[0])
                beats[b[0]] = max(1, int(t["fps"]))
                modes[b[0]] = t.get("avatar") or "talking"
            except Exception:
                modes[b[0]] = "talking"
        boards = [b for b in boards if modes.get(b[0]) != "off"]           # a screen set Off in Devices never sees her
        av &= {b[0] for b in boards}
        modes = {d: m for d, m in modes.items() if m != "off"}
        beats = {d: f for d, f in beats.items() if d in modes}
        if beats:     # the fastest board's beat: each board takes its own from it (the slowest's dragged the pendant to a speaker's 3, 2026-11-12)
            fps = max(1, min(TALK_FPS, max(beats.values())))
        if self.cap:
            fps = min(fps, self.cap)
        self.beats, self.modes = beats, modes
        return boards, av, fps

    def _take(self, boards, av, fps):
        self.looked, self.looking = (None if self.dirty else time.monotonic()), False
        self.dirty = False
        self.boards, self.av, self.fps = boards, av, fps
        if self.session:
            if boards:
                self.session.sync()              # a board that came or went is its own affair: the others play on
                for b in self.session.builders.values():
                    b.respec = True              # and one that stayed may have been saved (its fit, its box): it reads itself again
            else:
                self._rest()

    def _rest(self):
        s, self.session = self.session, None
        if s:
            s.close()

    def _open(self, now):
        if not self.session and self.alive and self.boards:
            self.session = _Session(self, now)
        return self.session

    def _step(self, item):
        now = time.monotonic()
        # Never while she speaks: the look empties glass's memory of the boards, so the next
        # slice waits on a health call of its own while the board answers two more (a 700 ms
        # hole in her voice on the pendant, once a look, 2026-11-10). A board that came or
        # went is found when she pauses.
        speaking = self.session and now < self.session.last_word
        if item and not speaking and self.stale(now):
            self.looking = True
            threading.Thread(target=lambda: self.put(("boards",) + self._ask()), daemon=True,
                             name="avatar-feed-look").start()
        if self.session and self.session.done(now):
            self._rest()
        if item is None:
            return
        if item[0] == "boards":
            self._take(*item[1:])
            if self.waking and now - self.waking < WAKE_S:
                self._open(now)                  # woken while there was no board: one came
        elif item[0] == "flush":
            if item[1]:
                self.flushed.append(item[1])
            if self.session:
                self.session.flush(item[1], now)
            elif not item[1]:
                self.waking = None
        elif item[0] == "wake":
            self.waking = now
            self._open(now)
            self.woke.set()
        elif item[0] == "packet":
            head = item[1]
            if head["sid"] and head["sid"] in self.flushed:
                return
            self.packets += 1
            speech = head["kind"] == "speech"
            # her idle never opens the boards in "talking": a wake or a word does
            s = self.session or ((speech or self.mode() == "always") and self._open(now))
            if s:
                s.packet(head, item[2], item[3], now)
            elif speech:
                self.waking = None


class _Session:
    """The boards while her picture goes to them: one playout clock, a pusher per
    frames-only board, a builder per one-stream board."""

    def __init__(self, hub, now):
        self.hub = hub
        self.builders, self.pushers, self.av_ids = {}, {}, set()
        self.playout = _Playout()
        self.clock = self.grid = self.last_kind = None
        self.sid = ""
        self.last_word = self.last_packet = now      # when the last of it has PLAYED, not when it landed
        self.sync()

    def sync(self):
        hub, want = self.hub, {b[0]: b for b in self.hub.boards}
        was = (set(self.builders), set(self.pushers))
        for d in [d for d in self.builders if d not in want or d not in hub.av]:
            self.builders.pop(d).close()
        for d in [d for d in self.pushers if d not in want or d in hub.av]:
            self.pushers.pop(d).close()
        for d, b in want.items():
            if d in hub.av:
                if d not in self.builders:
                    self.builders[d] = _AvBuilder(hub, b)
            elif d not in self.pushers:
                self.pushers[d] = _Pusher(hub, b)
        self.av_ids = set(self.builders)
        self.playout.pushers = list(self.pushers.values())
        if was != (set(self.builders), set(self.pushers)):
            logger.info(f"[avatar_feed] her face on {', '.join(want)}")

    def done(self, now):
        w = self.hub.waking
        if w and now - w < WAKE_S:
            return False                         # woken: her first word is on its way
        if now - self.last_packet > REST_S:
            return True
        return self.hub.mode() == "talking" and now - self.last_word > HOLD_S

    def flush(self, sid, now):
        self.playout.clear(sid)
        for b in self.builders.values():
            b.flush(sid)
        if sid and self.sid and sid != self.sid:
            return                               # an older reply's stop: the one playing plays on
        self.hub.waking = None
        self.clock = self.grid = None            # the next reply starts now, not where this one would have ended
        self.last_word, self.last_packet = now - HOLD_S + 1, now

    def packet(self, head, frames, pcm, now):
        speech = head["kind"] == "speech"
        if speech:
            self.hub.waking = None
            if head["sid"]:
                self.sid = head["sid"]
        if not frames:
            if speech and pcm:
                # Her voice without a face (a render failed): the boards hear it anyway. Core
                # skipped its own copy of a claimed reply, so dropping this was a silent sentence.
                head["ms"] = len(pcm) * 1000 // (2 * head["sr"])
                self.last_word = self.last_packet = max(self.last_word, now + head["ms"] / 1000)
                for b in self.builders.values():
                    b.send(now, head, pcm, [])
            return
        if not speech and self.hub.mode() != "always":
            return                               # between her lines the last frame holds: no screen wants her idle
        # a one-stream board idles at its beat: a region costs by motion, not by rate
        fps = self.hub.fps if speech else min(self.hub.fps, IDLE_AV_FPS) if self.builders else IDLE_FPS
        # One clock across slices: a slice's frames are due where the last one left off. It is
        # anchored ONCE per reply and runs on: re-anchoring at every late slice pushed her face
        # ever later while her voice kept real time. A late slice's frames are due in the past
        # and the playout drops them for the newest: her face skips to stay with her voice.
        if (self.clock is None or head["kind"] != self.last_kind or now > self.clock + 3.0
                or (head.get("fresh") and now + AHEAD_S > self.clock)):
            self.clock = self.grid = now + AHEAD_S
            self.playout.cut(self.clock)         # what was due after this belonged to the other kind
        self.last_kind = head["kind"]
        # Frames are picked by TIME on a grid of 1/fps (not every nth), and the grid runs on
        # across slices; each tick takes the frame nearest it.
        start, span = self.clock, len(frames) / head["fps"]
        picked = []                              # (ms into the slice, frame)
        while self.grid < self.clock + span:
            i = min(len(frames) - 1, int((self.grid - self.clock) * head["fps"] + 0.5))
            self.playout.put(self.grid, frames[i], head["sid"], idle=not speech)
            picked.append((int((self.grid - self.clock) * 1000), frames[i]))
            self.grid += 1.0 / fps
        self.clock += span
        self.last_packet = max(now, self.clock)
        if speech:
            self.last_word = self.last_packet
        if self.builders:
            head["ms"] = int(span * 1000)
            audio = pcm if speech else b""       # her idle goes without its silence: the board paces it on the wall
            for d, b in self.builders.items():
                if speech or self.hub.modes.get(d) == "always":   # her idle only to a screen set Always
                    b.send(start, head, audio, picked)

    def close(self):
        self.playout.close()
        for b in self.builders.values():
            b.close()
        for p in self.pushers.values():
            p.close()
        logger.info("[avatar_feed] her screens rest")


class _Playout:
    """Frames due on one clock, given to every board's pusher when their time comes. The
    hub thread puts, this one sleeps: neither holds the other up. Behind by a frame or
    more, it skips to the newest frame that is due (steady over eager). A source ahead of
    her voice is held here, in order, until each frame's time."""

    MAX = 4000                                   # frames held: two minutes and more at any beat

    def __init__(self):
        self.pushers = []
        self.items = collections.deque()         # (due, frame, sid), in due order
        self.cv = threading.Condition()
        self.alive = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="avatar-feed-playout")
        self.thread.start()

    def put(self, due, frame, sid="", idle=False):
        with self.cv:
            if len(self.items) >= self.MAX:
                self.items.popleft()
            self.items.append((due, frame, sid, idle))
            self.cv.notify()

    def cut(self, after):
        with self.cv:
            while self.items and self.items[-1][0] >= after:
                self.items.pop()

    def clear(self, sid=""):
        with self.cv:
            if sid:
                self.items = collections.deque(i for i in self.items if i[2] != sid)
            else:
                self.items.clear()

    def close(self):
        with self.cv:
            self.alive = False
            self.items.clear()
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while self.alive and not self.items:
                    self.cv.wait()
                if not self.alive:
                    return
                wait = self.items[0][0] - time.monotonic()
                if wait > 0:
                    self.cv.wait(wait)                      # a put meanwhile is looked at again
                    continue
                _, frame, _, idle = self.items.popleft()
                while self.items and self.items[0][0] <= time.monotonic():
                    _, frame, _, idle = self.items.popleft()   # behind: the newest due frame wins
                pushers = self.pushers
            for p in pushers:
                if not idle or p.always():
                    p.give(frame)


def _side(hub, side):
    want = hub.face_px          # a smaller face than the glass's square: every link of the board's chain scales with pixels
    return want // 16 * 16 if 64 <= want < side else side


class _Pusher:
    """One frames-only board: the newest frame wins, sent one at a time, no faster than its
    own beat (the hub's clock runs at the fastest board's: a frame this glass would never
    paint is not even encoded for it)."""

    def __init__(self, hub, board):
        self.hub, self.id = hub, board[0]
        self.glass_side = board[1]
        self.side = _side(hub, board[1])
        self.frame, self.lock, self.ready = None, threading.Lock(), threading.Event()
        self.open, self.fails, self.sent = True, 0, 0
        threading.Thread(target=self._run, daemon=True, name=f"avatar-feed-{self.id}").start()

    def give(self, jpeg):
        with self.lock:
            self.frame = jpeg
        self.ready.set()

    def always(self):
        return self.hub.modes.get(self.id) == "always"

    def close(self):
        with self.lock:
            self.open, self.frame = False, None
        self.ready.set()

    def _run(self):
        from PIL import Image
        while True:
            self.ready.wait()
            self.ready.clear()
            with self.lock:
                jpeg, self.frame = self.frame, None
                if not self.open:
                    return
            if jpeg is None:
                continue
            began = time.monotonic()
            self.side = _side(self.hub, self.glass_side)          # face_px may have changed (register in place)
            try:
                b = io.BytesIO()
                Image.open(io.BytesIO(jpeg)).convert("RGB").resize((self.side, self.side)).save(b, "JPEG", quality=JPEG_Q)
                _glass().face_frame(self.id, b.getvalue(), seconds=3)
                self.fails = 0
                self.sent += 1
            except Exception as e:
                self.fails += 1
                if self.fails in (1, 20):
                    logger.info(f"[avatar_feed] {self.id}: frame not taken ({e})")
            rest = 1.0 / self.hub.beats.get(self.id, self.hub.fps) - (time.monotonic() - began)
            if rest > 0:
                time.sleep(rest)                 # frames given meanwhile wait as one: the newest


class _AvBuilder:
    """One board that takes her voice and her face as ONE stream (firmware/pendant/main/avatar.h):
    a slice's audio and the frames picked within it go as one packet, each frame as a key (the
    whole face) or as the few boxes that changed since the last one sent (core/avatar_diff.py),
    on the 16 px grid. The board paints each frame when its audio reaches the frame's moment:
    one clock, no drift. Her voice goes in the rate and codec the board plays (voice.fit_pcm),
    her face in the box it states (a source of another shape is letterboxed into it).

    A key goes when the board may not have what was sent (a packet lost on the way, or the
    board asking), and at least every SAFETY_KEY_S; never on a timer otherwise: over TCP a
    patch is not lost without someone saying so.

    A thread of its own: a board that sleeps costs glass a health timeout, and that is this
    board's wait, never the hub's. Slices wait here until AV_LEAD_S before their start, so a
    source ahead of her voice never overruns the five packets glass keeps (it drops the oldest,
    her voice with it)."""

    SAFETY_KEY_S = 10.0              # a whole face at least this often: a patch lost unsaid never lingers longer
    HIDDEN_KEY_S = 2.0               # her tile is down: a key this often keeps her fresh and asks whether she is shown
    RECTS = 4                        # boxes one frame's change may go as (more saved nothing measurable)
    JOBS_MAX = 600                   # slices held for one board; the oldest goes

    def __init__(self, hub, board):
        from core.devices.voice import StreamFit
        self.hub, self.id = hub, board[0]
        self.glass_side = board[1]   # the glass's square; the face's comes from the hub's face_px at each spec read
        self.spec = None
        self.spec = self._spec_cached()
        self.respec = True           # the real read on this board's own thread: never a health call on the hub's
        self.last = None             # the face as the board has it (the pixels sent)
        self.key_at = 0.0
        self.next = None             # when this board takes its next frame, at its own beat
        self.lost = self._lost()
        self.fit = StreamFit()
        self.sid = ""                # the last reply whose voice this board carried: a stop at the board flushes that reply, no other's

        self.hidden = False          # her tile was not up at the last slice: the first frame when it is, is a key
        self.respec = False          # the hub looked at the boards again (a device saved): read the spec before the next slice
        self.sent = self.voiced = self.keys = self.regions = self.still = self.failed = self.bad = 0
        self.bytes = {"audio": 0, "keys": 0, "regions": 0}
        self.window = collections.deque(maxlen=400)   # (when, bytes) of each packet: the kbps over RATE_S
        self.jobs, self.cv, self.alive = collections.deque(), threading.Condition(), True
        try:
            _glass().av_on_stop(self.id, self.stop_at_source)   # a stop at the board reaches the source's queue
        except Exception as e:
            logger.info(f"[avatar_feed] {self.id}: stop not registered ({e})")
        threading.Thread(target=self._run, daemon=True, name=f"avatar-feed-av-{self.id}").start()

    def _spec(self):
        """The board's face box, packet limits and sound, as glass knows them. When glass cannot say (a
        health call failed), the spec it had stands: defaults would send v1 in the wrong box (scout)."""
        square = _side(self.hub, self.glass_side)
        try:
            sp = _glass().av_spec(self.id)
        except Exception:
            if getattr(self, "spec", None):
                return self.spec
            sp = {}
        w, h = int(sp.get("w") or square), int(sp.get("h") or square)
        if w == h:
            w = h = min(w, square)               # a smaller face than the glass's square (face_px) stays a square
        return {"w": w, "h": h, "frames": max(1, int(sp.get("frames") or 32)), "v": int(sp.get("v") or 1),
                "plays": sp.get("plays") or {}, "tells": bool(sp.get("tells")), "fit": sp.get("fit") or "contain"}

    def _spec_cached(self):
        """A first spec without asking the board (glass's memory, else the square): the hub thread never waits."""
        side = _side(self.hub, self.glass_side)
        return {"w": side, "h": side, "frames": 32, "v": 1, "plays": {}, "tells": False, "fit": "contain"}

    def _lost(self):
        try:
            return int(_glass().av_lost(self.id))
        except Exception:
            return 0

    def status(self):
        now = time.monotonic()
        recent = sum(n for t, n in self.window if now - t < RATE_S)
        return {"sent": self.sent, "voiced": self.voiced, "keys": self.keys, "regions": self.regions, "still": self.still,
                "failed": self.failed, "bad": self.bad, "waiting": len(self.jobs), "voicing": self.sid, "lost": self.lost,
                "kbps": round(recent * 8 / RATE_S / 1000), "bytes": dict(self.bytes),
                "box": [self.spec["w"], self.spec["h"]], "packet": self.spec["v"],
                "beat": self.hub.beats.get(self.id, self.hub.fps)}

    def _image(self, jpeg):
        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        w, h = self.spec["w"], self.spec["h"]
        sw, sh = im.size
        if (sw, sh) != (w, h):
            if abs(sw * h - sh * w) <= max(sw, sh):          # the same shape: a plain resize
                im = im.resize((w, h))
            elif self.spec.get("fit") == "cover":            # another shape, filled: cropped HERE, so what the glass cannot show never crosses the air
                from PIL import ImageOps
                im = ImageOps.fit(im, (w, h), Image.Resampling.LANCZOS)
            else:                                            # another shape: all of her, on her own background
                k = min(w / sw, h / sh)
                fit = im.resize((max(1, round(sw * k)), max(1, round(sh * k))))
                box = Image.new("RGB", (w, h), im.getpixel((0, 0)))
                box.paste(fit, ((w - fit.width) // 2, (h - fit.height) // 2))
                im = box
        return np.asarray(im)

    @staticmethod
    def _encode(arr):
        from PIL import Image
        b = io.BytesIO()
        Image.fromarray(arr).save(b, "JPEG", quality=JPEG_Q, optimize=True)   # optimize: a region's header costs half
        return b.getvalue()

    def _frame(self, t_ms, jpeg, force_key=False, rects=RECTS):
        """[(entry, bytes)] for one frame: a key, the boxes that changed, or [] when nothing did."""
        from core import avatar_diff
        img = self._image(jpeg)
        now = time.monotonic()
        key = (force_key or self.last is None or self.last.shape != img.shape
               or now - self.key_at > self.SAFETY_KEY_S)
        boxes = None
        if not key:
            boxes = avatar_diff.plan(self.last, img, rects)
            if boxes == "still":
                self.still += 1
                return []                                     # nothing moved: the board keeps what it has
            key = boxes == "key"
        if key:
            self.last = img.copy()
            self.key_at = now
            self.keys += 1
            data = self._encode(img)
            self.bytes["keys"] += len(data)
            return [({"t": t_ms, "x": 0, "y": 0, "w": img.shape[1], "h": img.shape[0], "n": len(data), "key": True}, data)]
        out = []
        for x, y, w, h in boxes:
            self.last[y:y + h, x:x + w] = img[y:y + h, x:x + w]
            data = self._encode(img[y:y + h, x:x + w])
            self.bytes["regions"] += len(data)
            out.append(({"t": t_ms, "x": x, "y": y, "w": w, "h": h, "n": len(data), "key": False}, data))
        self.regions += len(out)
        return out

    def _thin(self, start, picked, speech):
        """The frames this board takes at its own beat, of those the hub picked at the fastest board's."""
        beat = self.hub.beats.get(self.id, self.hub.fps)
        if not speech:
            beat = min(beat, IDLE_AV_FPS)
        step, out = 1.0 / max(1, beat), []
        for t_ms, jpeg in picked:
            at = start + t_ms / 1000
            if self.next is None or at - self.next > step:
                self.next = at                                # a new run (a pause, another reply): from here
            if at >= self.next - 0.004:
                out.append((t_ms, jpeg))
                self.next += step
        return out

    # ── the hub thread's side: nothing here waits ──
    def send(self, start, head, audio, picked):
        with self.cv:
            self.jobs.append((start, dict(head), audio, picked))
            while len(self.jobs) > self.JOBS_MAX:
                self.jobs.popleft()
            self.cv.notify()

    def flush(self, sid=""):
        """She was stopped at the source: what waits here goes, what core holds for the board
        goes, the board drops its queue. A stop of some OTHER reply than the one this board is
        voicing only takes that reply's slices from here: the one it voices plays on."""
        with self.cv:
            if sid and self.sid and sid != self.sid:
                self.jobs = collections.deque(j for j in self.jobs if j == "clear" or j[1]["sid"] != sid)
                return
            self.jobs.clear()
            self.jobs.append("clear")
            self.cv.notify()

    def close(self):
        with self.cv:
            self.alive = False
            self.jobs.clear()
            self.cv.notify()
        try:
            g = _glass()
            g.av_on_stop(self.id, None, was=self.stop_at_source)   # its own only: a late close after a reload took the new builder's stopper
            g.av_release(device_id=self.id)                        # a reply it was carrying: core's sentence door takes the rest of it
        except Exception:
            pass
        logger.info(f"[avatar_feed] {self.id}: {self.sent} packets, {self.keys} keys, {self.regions} regions, {self.still} still, "
                    f"{sum(self.bytes.values()) // 1024} KB ({self.bytes['audio'] // 1024} of her voice)")

    def stop_at_source(self):
        """Core says the board's person stopped her: the source drops the rest of the reply this
        board was voicing. That reply only: a bare flush cut another chat's reply a browser was
        hearing. True when something was dropped there."""
        if not self.sid:
            return False                     # this board never carried a reply's voice: nothing of its own to drop
        return self.hub.stop_source(self.sid)

    # ── the board's own thread ──
    def _run(self):
        while True:
            with self.cv:
                while self.alive and not self.jobs:
                    self.cv.wait()
                if not self.alive:
                    return
                job = self.jobs[0]
                if job != "clear":
                    wait = job[0] - AV_LEAD_S - time.monotonic()
                    if wait > 0:
                        self.cv.wait(min(wait, 1.0))            # a flush meanwhile is looked at again
                        continue
                self.jobs.popleft()
            try:
                if job == "clear":
                    self.sid = ""                    # stopped: nothing more of that reply is this board's to say
                    self.hub.claims.pop(self.id, None)
                    self.last = self.next = None     # the next frame is a key: the board's face may be anywhere after the clear
                    self.fit.reset()
                    _glass().av_clear(self.id, upstream=False)
                else:
                    self._send(*job)
            except Exception as e:
                self.failed += 1
                if _rarely(self.failed):
                    logger.info(f"[avatar_feed] {self.id}: packet not taken ({e}), {self.failed} so far")

    def _voice(self, audio, head):
        """Her voice in what the board plays: (bytes, {'sr', 'codec', 'samples', 'block'})."""
        from core.devices.voice import fit_pcm
        if head.get("fresh") or head.get("sid") != self.sid:
            self.fit.reset()                 # not the voice that went before: no filter memory across the gap
        try:
            return fit_pcm(audio, int(head.get("sr") or 24000), self.spec["plays"], self.fit)
        except Exception as e:
            self.bad += 1
            if _rarely(self.bad):
                logger.warning(f"[avatar_feed] {self.id}: her voice not fitted ({e}); sent as it came")
            return audio, {"sr": int(head.get("sr") or 24000), "codec": "pcm", "samples": len(audio) // 2, "block": 0}

    def _send(self, start, head, audio, picked):
        g = _glass()
        # Her voice plays in ONE place: a browser or a call that took it, the board whose reply it
        # is (and only a reply this stream claimed at its start; else core's sentence door has its
        # voice), else her own speakers. Every other face only mouths it.
        # The claim is asked ONCE per reply. Core lets it go when the reply is MADE (tts_stream_end),
        # seconds into a paragraph she is still speaking, and its sentence door skipped every sentence
        # of it for this board: a reply this board began to voice, it voices to its end (Krem heard
        # one sentence and watched her mouth the rest, 2026-11-10).
        sid = head.get("sid") or ""
        if audio and (head.get("taken") or head.get("device") != self.id
                      or not (sid and sid in (self.hub.claims.get(self.id), self.sid) or g.av_carried(self.id, sid))):
            audio = b""
        voice, ms, force = bool(audio), int(head.get("ms") or 0), False
        if self.respec:                      # on this board's own thread: a health call here waits on nobody else
            self.respec = False
            spec, self.spec = self.spec, self._spec()
            if spec != self.spec:
                force = True                 # another fit or box: the next frame is a whole one in the new shape
                logger.info(f"[avatar_feed] {self.id}: {self.spec['w']}x{self.spec['h']} {self.spec.get('fit')}")
        lost = self._lost()
        if lost != self.lost:
            self.lost, force = lost, True    # the board may not have what was sent: a key, and its format asked again (a 415 = it plays another rate now)
            self.spec = self._spec()
        if voice:
            audio, said = self._voice(audio, head)
            if sid:
                self.sid = sid
        else:
            said = {"sr": int(head.get("sr") or 24000), "codec": "pcm", "samples": 0, "block": 0}
            self.fit.reset()
        shown = voice or g.av_shown(self.id)
        if not shown and self.spec.get("tells"):
            self.hidden = True               # it tells when she comes up (face/shown): nothing crosses its air till then
            return
        if not shown:
            # Her tile is not up, or the screen rests, on a board that does not tell: a key alone every
            # HIDDEN_KEY_S keeps the board's face fresh and asks again whether she is shown.
            self.hidden = True
            if time.monotonic() - self.key_at < self.HIDDEN_KEY_S or not picked:
                return
            picked, force, ms = [(0, picked[-1][1])], True, int(self.HIDDEN_KEY_S * 1000)
        else:
            if self.hidden:
                self.hidden, force = False, True    # she just came up: her face here may be minutes old
            picked = self._thin(start, picked, head["kind"] == "speech")
        limit = self.spec["frames"]
        if len(picked) > limit:                                  # more frames than the board takes in a packet: evenly fewer
            picked = [picked[i * len(picked) // limit] for i in range(limit)]
        rects = max(1, min(self.RECTS, (limit - len(picked)) // max(1, len(picked)) + 1))
        frames, bodies = [], []
        for t_ms, jpeg in picked:
            try:
                out = self._frame(t_ms, jpeg, force, rects)
                force = False
            except Exception as e:
                self.bad += 1
                if _rarely(self.bad):
                    logger.info(f"[avatar_feed] {self.id}: a frame not built ({e}), {self.bad} so far")
                continue
            for entry, data in out:
                frames.append(entry)
                bodies.append(data)
        packet = self._pack(head["kind"], ms, audio, said, frames, bodies)
        g.av_packet(self.id, packet, voice=voice)
        self.sent += 1
        self.voiced += voice
        self.bytes["audio"] += len(audio)
        self.window.append((time.monotonic(), len(packet)))

    def _pack(self, kind, ms, audio, said, frames, bodies):
        """The packet in the shape the board reads (avatar.h): v1 a JSON frame list, v2 a binary
        frame table of 16 bytes an entry (no JSON node a frame on the board's internal heap)."""
        tail = audio
        if self.spec["v"] >= 2:
            meta = {"v": 2, "kind": kind, "sr": said["sr"], "codec": said["codec"], "audio": len(audio),
                    "samples": said["samples"], "block": said["block"], "ms": ms, "nf": len(frames)}
            table = b"".join(struct.pack(">HHHHHHI", max(0, min(65535, f["t"])), f["x"], f["y"], f["w"], f["h"],
                                         1 if f["key"] else 0, f["n"]) for f in frames)
            tail = table + audio
        else:
            meta = {"kind": kind, "sr": said["sr"], "audio": len(audio), "ms": ms, "frames": frames}
            if said["codec"] != "pcm":
                meta.update(codec=said["codec"], samples=said["samples"], block=said["block"])
        meta = json.dumps(meta).encode()
        if (4 + len(meta)) % 4:
            meta += b" " * (4 - (4 + len(meta)) % 4)           # what follows lands aligned on the board
        return struct.pack(">I", len(meta)) + meta + tail + b"".join(bodies)
