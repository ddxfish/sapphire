"""core/avatar_feed: one source at a time, slices played out to the boards' glass."""
import io
import json
import struct
import threading
import time

import pytest

from core import avatar_feed as feed


def jpeg(shade=0, side=64):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (side, side), (shade, shade, shade)).save(b, "JPEG")
    return b.getvalue()


class FakeGlass:
    def __init__(self, boards=(("kitchen", 240),), av=(), fps=5):
        self.boards, self.av, self.fps = list(boards), set(av), fps
        self.frames, self.packets, self.cleared = [], [], []
        self.stoppers, self.claims, self.released = {}, {}, []
        self.got = threading.Event()

    def face_boards(self):
        return list(self.boards)

    def av_boards(self):
        return [b for b in self.boards if b[0] in self.av]

    def takes(self, device_id):
        return {"fps": self.fps}

    def face_frame(self, device_id, data, seconds=3):
        self.frames.append((device_id, time.monotonic(), data))
        self.got.set()

    def av_packet(self, device_id, packet, voice=True):
        n = struct.unpack(">I", packet[:4])[0]
        self.packets.append((device_id, json.loads(packet[4:4 + n]), voice))
        self.got.set()

    def av_on_stop(self, device_id, fn, was=None):
        if fn is None:
            if was is None or self.stoppers.get(device_id) == was:
                self.stoppers.pop(device_id, None)
        else:
            self.stoppers[device_id] = fn

    def av_clear(self, device_id, upstream=True):
        self.cleared.append((device_id, upstream))
        self.claims.pop(device_id, None)

    def av_shown(self, device_id):
        return True

    def av_carried(self, device_id, sid):
        return bool(sid) and self.claims.get(device_id) == sid

    def av_claim(self, device_id, sid):
        self.claims[device_id] = str(sid)

    def av_release(self, sid=None, device_id=None):
        self.released.append(device_id or sid)
        self.claims.pop(device_id, None)             # as glass: the claim ends with it


@pytest.fixture
def glass(monkeypatch):
    g = FakeGlass()
    monkeypatch.setattr(feed, "_glass", lambda: g)
    yield g
    for name in ("one", "two"):
        feed.release(name)


def settle(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_one_source_at_a_time(glass):
    assert feed.register("one") == ""
    words = feed.register("two")
    assert "one" in words and feed.source() == "one"
    assert feed.packet("two", "speech", [jpeg()], 25) is False      # not her face: not played
    feed.release("two")                                             # nor can it let go of another's
    assert feed.source() == "one"
    feed.release("one")
    assert feed.source() is None and feed.register("two") == ""


def test_a_slice_reaches_a_board_at_its_beat(glass):
    feed.register("one")
    feed.packet("one", "speech", [jpeg(i * 10) for i in range(25)], 25)     # one second at 25
    assert settle(lambda: len(glass.frames) >= 4, 4)
    time.sleep(0.4)
    assert 4 <= len(glass.frames) <= 6                              # the board's 5 a second (a tick on the slice's edge may fall either side), not 25
    assert all(d == "kitchen" for d, _, _ in glass.frames)
    from PIL import Image
    assert Image.open(io.BytesIO(glass.frames[0][2])).size == (240, 240)    # the glass's square
    assert feed.watching() == {"boards": ["kitchen"], "av": [], "fps": 5, "open": True}


def test_talking_screens_skip_her_idle_and_always_show_it(glass):
    feed.register("one", screens="talking")
    feed.packet("one", "idle", [jpeg()], 2)
    time.sleep(0.5)
    assert glass.frames == []
    feed.register("one", screens="always")                          # the same source again: its new mind
    feed.packet("one", "idle", [jpeg()], 2)
    assert settle(lambda: glass.frames)


def test_off_and_no_boards_send_nothing(glass):
    feed.register("one", screens="off")
    feed.packet("one", "speech", [jpeg()], 25)
    time.sleep(0.4)
    assert glass.frames == [] and feed.status()["open"] is False
    glass.boards = []
    feed.register("one", screens="always")
    feed.packet("one", "speech", [jpeg()], 25)
    time.sleep(0.4)
    assert glass.frames == [] and feed.watching()["boards"] == []


def test_one_stream_board_gets_voice_only_for_its_own_claimed_reply(glass):
    glass.av = {"kitchen"}
    stopped = []
    feed.register("one", stop=lambda sid: stopped.append(sid) or True)
    feed.wake("one", wait=2)
    assert feed.carries("kitchen")
    pcm = b"\0\0" * 24000
    feed.packet("one", "speech", [jpeg(i) for i in range(25)], 25, pcm=pcm, sid="r1", device="kitchen")
    assert settle(lambda: glass.packets)
    assert glass.packets[-1][2] is False                            # not claimed: core's door has its voice, the face mouths
    glass.claims["kitchen"] = "r2"
    feed.packet("one", "speech", [jpeg(255 - i * 10) for i in range(25)], 25, pcm=pcm, sid="r2", device="kitchen")   # a face that moves
    assert settle(lambda: glass.packets[-1][2] is True)
    head = glass.packets[-1][1]
    assert head["audio"] == len(pcm) and head["frames"] and head["frames"][0]["key"] in (True, False)
    assert glass.frames == []                                       # never the frames-only path as well
    # a stop at the board reaches the source, for that reply
    assert glass.stoppers["kitchen"]() is True and stopped == ["r2"]
    # the source's own flush clears the board without calling back up
    feed.flush("one", "r2")
    assert settle(lambda: glass.cleared == [("kitchen", False)])
    feed.release("one")
    assert "kitchen" not in glass.stoppers and "kitchen" in glass.released


def test_a_flush_drops_what_waits_for_that_reply_only(glass):
    feed.register("one")
    h = feed._hub
    with h.cv:                                                      # the hub thread waits while we queue
        for sid in ("a", "b", "a"):
            h.q.append(("packet", {"kind": "speech", "fps": 25, "sr": 24000, "sid": sid, "device": "", "taken": False}, [], b""))
    h.put(("flush", "a"))
    kinds = [(i[0], i[1]["sid"] if i[0] == "packet" else i[1]) for i in list(h.q)]
    assert ("packet", "a") not in kinds


def test_voice_without_a_face_still_reaches_a_one_stream_board(glass):
    glass.av = {"kitchen"}
    glass.claims["kitchen"] = "r1"
    feed.register("one")
    feed.packet("one", "speech", [], 25, pcm=b"\0\0" * 2400, sid="r1", device="kitchen")
    assert settle(lambda: glass.packets)
    dev, head, voice = glass.packets[0]
    assert voice is True and head["ms"] == 100 and head["frames"] == []


# ── the bug hunt of 2026-11-10: what a real source does that the first cut lost ──
def test_a_source_ahead_of_her_voice_is_held_not_dropped(glass):
    feed.register("one")
    t0 = time.monotonic()
    for i in range(3):                                              # three seconds handed in at once
        feed.packet("one", "speech", [jpeg(i * 50 + k) for k in range(25)], 25, sid="r")
    assert settle(lambda: len(glass.frames) >= 14, 5)
    at = [t - t0 for _, t, _ in glass.frames]
    assert at[0] < 0.5 and 2.3 < at[13] < 3.3                       # on the beat across all three, none thrown away


def test_a_stop_then_a_new_reply_starts_now(glass):
    feed.register("one")
    for i in range(5):
        feed.packet("one", "speech", [jpeg(k) for k in range(25)], 25, sid="r1")
    time.sleep(0.4)
    feed.flush("one", "r1")
    time.sleep(0.3)
    n = len(glass.frames)
    time.sleep(0.5)
    assert len(glass.frames) == n                                   # the stopped reply's frames are gone
    feed.packet("one", "speech", [jpeg(k) for k in range(25)], 25, sid="r1")
    time.sleep(0.5)
    assert len(glass.frames) == n                                   # and what the source still hands in of it
    t0 = time.monotonic()
    feed.packet("one", "speech", [jpeg(200)] * 25, 25, sid="r2")
    assert settle(lambda: len(glass.frames) > n, 2) and glass.frames[n][1] - t0 < 0.5


def test_her_idle_never_opens_the_boards_in_talking(glass):
    feed.register("one", screens="talking")
    feed.wake("one", wait=1)
    feed.flush("one")                                               # the reply had no words after all
    for _ in range(4):
        feed.packet("one", "idle", [jpeg()], 2)
        time.sleep(0.1)
    feed._hub.session.last_word -= 60                               # long after
    assert settle(lambda: not feed.status()["open"])
    feed.packet("one", "idle", [jpeg()], 2)
    time.sleep(0.4)
    assert not feed.status()["open"] and glass.frames == []


def test_woken_boards_wait_for_a_slow_first_word_and_keep_the_claim(glass, monkeypatch):
    monkeypatch.setattr(feed, "HOLD_S", 0.3)
    monkeypatch.setattr(feed, "REST_S", 0.3)
    glass.av = {"kitchen"}
    feed.register("one")
    feed.wake("one", wait=2)
    assert feed.carries("kitchen")
    glass.claims["kitchen"] = "r1"
    time.sleep(1.6)                                                 # a thinking model: well past the hold
    assert feed.status()["open"] and glass.claims.get("kitchen") == "r1"
    pcm = b"\0\0" * 2400
    feed.packet("one", "speech", [], 25, pcm=pcm, sid="r1", device="kitchen")    # voice alone is a word too
    assert settle(lambda: glass.packets and glass.packets[-1][2] is True)
    assert settle(lambda: not feed.status()["open"], 3)             # then the hold runs, from the end of her word


def test_a_slice_that_is_not_one_is_refused_at_the_door(glass):
    import array
    feed.register("one", screens="ALWAYS", face_px="big")           # settings from anywhere
    assert feed.status()["screens"] == "always"
    one = jpeg()
    for kind, frames, fps in (("speech", [one], 0), ("speech", [one], None), ("speech", [one], float("nan")),
                              ("talk", [one], 25), ("speech", [None], 25), ("speech", "abc", 25)):
        assert feed.packet("one", kind, frames, fps) is False
    assert feed.packet("one", "speech", [one], 25, sr=0, pcm=array.array("h", [0] * 7)) is True
    assert feed.packet("one", "Speech", one, 25, pcm=None) is True  # one picture, not a list of its bytes
    assert settle(lambda: glass.frames) and feed.status()["refused"] == 6
    assert feed._hub.thread.is_alive() and feed.status()["open"]    # and no bad slice closed the boards


def test_a_board_that_leaves_is_its_own_affair(glass):
    glass.boards = [("kitchen", 240), ("den", 240)]
    feed.register("one")
    feed.packet("one", "speech", [jpeg(k) for k in range(25)], 25, sid="r")
    assert settle(lambda: {d for d, _, _ in glass.frames} == {"kitchen", "den"})
    s = feed._hub.session
    glass.boards = [("kitchen", 240)]
    feed._hub.put(("boards",) + feed._hub._ask())
    assert settle(lambda: feed.watching()["boards"] == ["kitchen"])
    assert feed._hub.session is s and list(s.pushers) == ["kitchen"]    # the same session: kitchen never blinked


def test_a_one_stream_board_is_handed_slices_near_their_time(glass):
    glass.av = {"kitchen"}
    glass.claims["kitchen"] = "r"
    feed.register("one")
    pcm = b"\0\0" * 24000
    for i in range(6):                                              # six seconds at once; glass keeps five and drops the oldest
        feed.packet("one", "speech", [jpeg(i * 40 + k) for k in range(25)], 25, pcm=pcm, sid="r", device="kitchen")
    time.sleep(0.8)
    assert 2 <= len(glass.packets) <= 3                             # what starts within AV_LEAD_S; the rest waits with its voice
    assert settle(lambda: len(glass.packets) == 6, 6) and all(v for _, _, v in glass.packets)


def test_a_reply_this_board_began_to_voice_it_voices_to_its_end(glass):
    glass.av = {"kitchen"}
    glass.claims["kitchen"] = "r1"
    feed.register("one")
    pcm = b"\0\0" * 2400
    feed.packet("one", "speech", [], 25, pcm=pcm, sid="r1", device="kitchen")
    assert settle(lambda: glass.packets and glass.packets[-1][2] is True)
    glass.av_release(sid="r1"); glass.claims.clear()                # core: the reply is made (she is still speaking it)
    feed.packet("one", "speech", [], 25, pcm=pcm, sid="r1", device="kitchen")
    assert settle(lambda: len(glass.packets) == 2) and glass.packets[-1][2] is True
    feed.packet("one", "speech", [], 25, pcm=pcm, sid="r2", device="kitchen")    # another reply, never claimed: core's door has it
    assert settle(lambda: len(glass.packets) == 3) and glass.packets[-1][2] is False
    feed.flush("one", "r1")
    assert settle(lambda: glass.cleared)


def test_a_short_reply_is_made_before_its_first_slice_and_is_still_voiced(glass):
    glass.av = {"kitchen"}
    feed.register("one")
    feed.wake("one", wait=2)
    assert feed.claim("one", "kitchen", "r1") and glass.claims["kitchen"] == "r1"
    assert feed.claim("one", "den", "r1") is False                  # no stream to that board: core's door has it
    glass.av_release(sid="r1"); glass.claims.clear()                # core: the reply is made, before a slice of it was drawn
    feed.packet("one", "speech", [], 25, pcm=b"\0\0" * 2400, sid="r1", device="kitchen")
    assert settle(lambda: glass.packets) and glass.packets[-1][2] is True
