"""Her face in a browser: the avatar feed's browser door.

The one avatar that is her face (core/avatar_feed) hands the hub slices of frames and
voice. Her boards get them from the hub, on its clock. A browser gets them here, as
they come: the player (interfaces/web/static/shared/face-player.js) places each slice's
voice and frames together on its own audio clock, so her mouth cannot drift from her
voice. A source may hand slices in faster than she speaks; the player holds them.

    v = avatar_door.join(sound=True)     # a viewer; v.get(timeout) -> packet bytes
    avatar_door.leave(v)
    avatar_door.voiced()                 # a viewer that plays sound is here: her voice may be handed to her face
    avatar_door.viewers()                # a source with no board to draw for still draws for these

One voice: a slice the source marked `taken` (core's own player was told to skip it) is
played by the newest viewer with sound; every other viewer gets that slice's frames
with no voice, so two tabs in one room never echo. A slice not taken plays elsewhere
(this computer's speakers, a board): every viewer mouths it in silence.

The wire is FlashHead's own, so one player serves every avatar:
    >I header length, JSON header {kind, fps, sr, audio, frames: [sizes], sid, ...},
    s16 PCM (`audio` bytes), then the JPEG frames.
kind: speech | idle | flush (she was stopped: drop what is scheduled) | gone (no avatar
is her face now; `why` says it in words).
"""
import json
import logging
import queue
import struct
import threading
import time

logger = logging.getLogger(__name__)

QUEUE = 96                # slices a viewer may hold unread (a second each while she talks); the oldest goes

_lock = threading.Lock()
_viewers = []             # newest last


class Viewer:
    def __init__(self, sound):
        self.sound = bool(sound)
        self.q = queue.Queue(maxsize=QUEUE)
        self.since = time.monotonic()
        self.dropped = 0

    def get(self, timeout=1.0):
        """The next packet's bytes; raises queue.Empty when none came in time."""
        return self.q.get(timeout=timeout)

    def _put(self, pkt):
        try:
            self.q.put_nowait(pkt)
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(pkt)
            except (queue.Empty, queue.Full):
                pass
            self.dropped += 1


def join(sound=True):
    v = Viewer(sound)
    with _lock:
        _viewers.append(v)
    return v


def leave(v):
    with _lock:
        if v in _viewers:
            _viewers.remove(v)


def viewers():
    return len(_viewers)


def voiced():
    return any(v.sound for v in _viewers)


def _pack(head, pcm=b"", frames=()):
    h = json.dumps({**head, "audio": len(pcm), "frames": [len(f) for f in frames]}).encode()
    return struct.pack(">I", len(h)) + h + pcm + b"".join(frames)


def give(head, frames, pcm):
    """A slice from her source (avatar_feed.packet calls this). Never blocks, never raises."""
    try:
        with _lock:
            seen = list(_viewers)
        if not seen:
            return
        voice = None
        if head.get("kind") == "speech" and head.get("taken") and pcm:
            voice = next((v for v in reversed(seen) if v.sound), None)
        quiet = loud = None
        for v in seen:
            if v is voice:
                loud = loud or _pack(head, pcm, frames)
                v._put(loud)
            else:
                quiet = quiet or _pack({**head, "mute": True}, b"", frames)
                v._put(quiet)
    except Exception as e:
        logger.info(f"[avatar_door] a slice not handed on ({e})")


def tell(kind, **words):
    """A word to every viewer: flush (sid) or gone (why)."""
    pkt = _pack({"kind": kind, **words})
    with _lock:
        seen = list(_viewers)
    for v in seen:
        v._put(pkt)


def status():
    with _lock:
        seen = list(_viewers)
    return {"viewers": len(seen), "sound": sum(v.sound for v in seen), "dropped": sum(v.dropped for v in seen)}
