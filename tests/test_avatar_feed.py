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
        self.beats, self.specs, self.lost, self.raw, self.modes, self.shown = {}, {}, {}, [], {}, {}
        self.frames, self.packets, self.cleared = [], [], []
        self.stoppers, self.claims, self.released = {}, {}, []
        self.got = threading.Event()

    def face_boards(self):
        return list(self.boards)

    def av_boards(self):
        return [b for b in self.boards if b[0] in self.av]

    def takes(self, device_id):
        return {"fps": self.beats.get(device_id, self.fps), "avatar": self.modes.get(device_id, "talking")}

    def av_spec(self, device_id):
        side = dict(self.boards).get(device_id, 240)
        return {"w": side, "h": side, "frames": 32, "v": 1, "plays": {}, "fps": self.fps, **self.specs.get(device_id, {})}

    def av_lost(self, device_id):
        return self.lost.get(device_id, 0)

    def face_frame(self, device_id, data, seconds=3):
        self.frames.append((device_id, time.monotonic(), data))
        self.got.set()

    def av_packet(self, device_id, packet, voice=True):
        n = struct.unpack(">I", packet[:4])[0]
        self.packets.append((device_id, json.loads(packet[4:4 + n]), voice))
        self.raw.append(packet)
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
        return self.shown.get(device_id, True)

    def av_carried(self, device_id, sid):
        return bool(sid) and self.claims.get(device_id) == sid

    def av_claim(self, device_id, sid):
        self.claims[device_id] = str(sid)

    def av_release(self, sid=None, device_id=None):
        self.released.append(device_id or sid)
        self.claims.pop(device_id, None)             # as glass: the claim ends with it


    def forget(self, device_id=None):
        pass


@pytest.fixture
def glass(monkeypatch):
    import config
    g = FakeGlass()
    monkeypatch.setattr(feed, "_glass", lambda: g)
    monkeypatch.setattr(config, "AVATAR_FACE_SOURCE", "", raising=False)    # never chosen: the first that asks
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


def test_a_screen_set_while_she_talks_skips_her_idle_and_one_set_always_shows_it(glass):
    glass.modes = {"kitchen": "talking"}
    feed.register("one")
    feed.packet("one", "idle", [jpeg()], 2)
    time.sleep(0.5)
    assert glass.frames == []
    glass.modes = {"kitchen": "always"}                              # its Devices page changed
    feed.nudge()
    assert settle(lambda: feed._hub.modes == {"kitchen": "always"})
    feed.packet("one", "idle", [jpeg()], 2)
    assert settle(lambda: glass.frames)


def test_a_screen_set_off_and_no_screens_send_nothing(glass):
    glass.modes = {"kitchen": "off"}
    feed.register("one")
    feed.packet("one", "speech", [jpeg()], 25)
    time.sleep(0.4)
    assert glass.frames == [] and feed.status()["open"] is False and feed.watching()["boards"] == []
    glass.modes, glass.boards = {}, []
    feed.nudge()
    feed.packet("one", "speech", [jpeg()], 25)
    time.sleep(0.4)
    assert glass.frames == [] and feed.watching()["boards"] == []


def test_her_idle_goes_only_to_the_screens_set_always(glass):
    glass.boards = [("kitchen", 240), ("hall", 240), ("den", 240)]
    glass.av = {"den"}
    glass.modes = {"kitchen": "always", "hall": "talking", "den": "talking"}
    feed.register("one")
    for i in range(6):
        feed.packet("one", "idle", [jpeg(i * 30)], 2)
    assert settle(lambda: glass.frames)
    time.sleep(0.5)
    assert {d for d, _, _ in glass.frames} == {"kitchen"} and glass.packets == []   # the hall and the den rest between her lines


def test_only_the_avatar_chosen_in_devices_is_her_face(glass, monkeypatch):
    import config
    monkeypatch.setattr(config, "AVATAR_FACE_SOURCE", "two")
    assert "two" in feed.register("one") and feed.source() is None
    assert feed.mode_for("one") == "off"
    assert feed.register("two") == "" and feed.source() == "two"
    monkeypatch.setattr(config, "AVATAR_FACE_SOURCE", "off")
    assert feed.mode_for("two") == "off"                              # its loop lets go at its next look
    assert {"one", "two"} <= set(feed.offered())


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
    glass.modes = {"kitchen": "always"}
    feed.register("one", screens="ALWAYS", face_px="big")           # settings from anywhere
    assert settle(lambda: feed.status()["screens"] == "always")
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


# ── changes only, the fitter, the packet's shapes (2026-11-12) ─────────────────────

def face(marks=(), w=240, h=240):
    """Her 'face': a dark field with bright boxes (x, y, side) on it, as JPEG."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (20, 26, 36))
    d = ImageDraw.Draw(im)
    for x, y, side in marks:
        d.rectangle((x, y, x + side - 1, y + side - 1), fill=(240, 200, 120))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95)
    return b.getvalue()


@pytest.fixture
def builder(glass):
    glass.av = {"kitchen"}
    feed.register("one")
    b = feed._AvBuilder(feed._hub, ("kitchen", 240))
    yield b
    b.close()


def test_a_still_face_sends_nothing_and_two_far_changes_go_as_two_boxes(builder):
    first = builder._frame(0, face([(20, 20, 32), (180, 190, 32)]))
    assert len(first) == 1 and first[0][0]["key"] is True                       # the first is whole
    assert builder._frame(40, face([(20, 20, 32), (180, 190, 32)])) == []      # nothing moved
    moved = builder._frame(80, face([(36, 20, 32), (180, 206, 32)]))            # an eye and a shoulder, far apart
    assert len(moved) == 2 and not any(e["key"] for e, _ in moved)
    assert {e["t"] for e, _ in moved} == {80}                                   # one moment, two boxes
    assert all(e["x"] % 16 == 0 and e["y"] % 16 == 0 and e["w"] % 16 == 0 and e["h"] % 16 == 0 for e, _ in moved)
    assert sum(e["w"] * e["h"] for e, _ in moved) < 240 * 240 // 4              # one box round both was most of the face


def test_a_change_over_most_of_the_face_is_a_key(builder):
    builder._frame(0, face())
    out = builder._frame(40, face([(0, 0, 240)]))
    assert len(out) == 1 and out[0][0]["key"] is True


def test_a_packet_lost_on_the_way_brings_a_key(builder, glass):
    import numpy as np
    builder._frame(0, face())
    glass.claims["kitchen"] = "r"
    head = {"kind": "speech", "sr": 24000, "sid": "r", "device": "kitchen", "taken": False, "ms": 40}
    builder._send(time.monotonic(), dict(head), b"\0\0" * 960, [(0, face([(20, 20, 32)]))])
    assert glass.packets[-1][1]["frames"][0]["key"] is False
    glass.lost["kitchen"] = 1                                                   # glass dropped one, or the board asked
    builder.next = None
    builder._send(time.monotonic() + 1, dict(head), b"\0\0" * 960, [(0, face([(20, 36, 32)]))])
    assert glass.packets[-1][1]["frames"][0]["key"] is True
    assert builder.status()["lost"] == 1 and builder.status()["kbps"] > 0


def test_her_voice_is_fitted_to_the_rate_the_board_plays(builder, glass):
    glass.specs["kitchen"] = {"plays": {"type": "audio/wav", "rate": 16000, "channels": 1}}
    builder.spec = builder._spec()
    glass.claims["kitchen"] = "r"
    head = {"kind": "speech", "sr": 24000, "sid": "r", "device": "kitchen", "taken": False, "ms": 1000}
    builder._send(time.monotonic(), dict(head), b"\1\0" * 24000, [])
    meta = glass.packets[-1][1]
    assert meta["sr"] == 16000 and meta["audio"] == 2 * 16000 and "codec" not in meta     # plain PCM keeps the old head


def ima_decode(body, block, samples):
    """IMA ADPCM blocks (mono) to int16: the reference the boards' C decoder mirrors."""
    steps = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
             107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
             876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
             5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
             27086, 29794, 32767]
    moves = [-1, -1, -1, -1, 2, 4, 6, 8]
    out = []
    for at in range(0, len(body), block):
        blk = body[at:at + block]
        pred = struct.unpack("<h", blk[:2])[0]
        idx = min(88, blk[2])
        out.append(pred)
        for byte in blk[4:]:
            for nib in (byte & 15, byte >> 4):
                st = steps[idx]
                diff = st >> 3
                if nib & 4: diff += st
                if nib & 2: diff += st >> 1
                if nib & 1: diff += st >> 2
                pred = max(-32768, min(32767, pred - diff if nib & 8 else pred + diff))
                idx = max(0, min(88, idx + moves[nib & 7]))
                out.append(pred)
    return out[:samples]


def test_ima_adpcm_on_the_stream_door_decodes_back_to_her_voice(builder, glass):
    import numpy as np
    glass.specs["kitchen"] = {"plays": {"type": "audio/wav", "rate": 16000, "channels": 1, "codec": "ima_adpcm"}, "v": 2}
    builder.spec = builder._spec()
    glass.claims["kitchen"] = "r"
    t = np.arange(24000) / 24000
    pcm = (0.4 * np.sin(2 * np.pi * 300 * t) * 32767).astype("<i2").tobytes()
    head = {"kind": "speech", "sr": 24000, "sid": "r", "device": "kitchen", "taken": False, "ms": 1000}
    builder._send(time.monotonic(), dict(head), pcm, [(0, face())])
    raw, meta = glass.raw[-1], glass.packets[-1][1]
    assert meta["v"] == 2 and meta["codec"] == "ima_adpcm" and meta["sr"] == 16000 and meta["samples"] == 16000
    assert meta["audio"] < 16000 * 2 // 3                                       # about 4:1 against 16-bit
    n = struct.unpack(">I", raw[:4])[0]
    table = raw[4 + n:4 + n + 16 * meta["nf"]]
    entry = struct.unpack(">HHHHHHI", table[:16])
    assert meta["nf"] == 1 and entry[:6] == (0, 0, 0, 240, 240, 1)              # t, x, y, w, h, key
    audio = raw[4 + n + 16 * meta["nf"]:4 + n + 16 * meta["nf"] + meta["audio"]]
    assert raw[4 + n + 16 * meta["nf"] + meta["audio"]:][:2] == b"\xff\xd8"   # the JPEG follows the voice
    got = np.array(ima_decode(audio, meta["block"], meta["samples"]), dtype=float)
    want = 0.4 * np.sin(2 * np.pi * 300 * np.arange(16000) / 16000) * 32767
    lag = int(np.argmax(np.correlate(got[2000:4000], want[2000:2100], "valid")))  # the filter's fixed delay
    assert len(got) == 16000 and np.corrcoef(got[2000 + lag:14000 + lag], want[2000:14000])[0, 1] > 0.99


def test_a_landscape_board_gets_her_whole_in_its_own_box(builder, glass):
    glass.specs["kitchen"] = {"w": 320, "h": 224}
    builder.spec = builder._spec()
    out = builder._frame(0, face())                                             # a square source
    assert out[0][0]["w"] == 320 and out[0][0]["h"] == 224 and out[0][0]["key"] is True
    from PIL import Image
    assert Image.open(io.BytesIO(out[0][1])).size == (320, 224)


def test_each_board_takes_its_own_beat(glass):
    glass.boards = [("pendant", 240), ("speaker", 240)]
    glass.av = {"pendant"}
    glass.beats = {"pendant": 12, "speaker": 3}
    glass.claims["pendant"] = "r"
    feed.register("one")
    feed.wake("one", wait=2)
    assert settle(lambda: feed._hub.fps == 12)                                  # the fastest, not the slowest
    feed.packet("one", "speech", [jpeg(i * 9) for i in range(25)], 25, pcm=b"\0\0" * 24000, sid="r", device="pendant")
    assert settle(lambda: glass.packets)
    assert 11 <= len(glass.packets[-1][1]["frames"]) <= 13                      # the pendant's 12, not the speaker's 3


def test_a_board_that_tells_gets_nothing_while_she_is_hidden_and_a_key_when_she_shows(builder, glass):
    glass.specs["kitchen"] = {"tells": True}
    builder.spec = builder._spec()
    head = {"kind": "idle", "sr": 24000, "sid": "", "device": "", "taken": False, "ms": 1000}
    builder._send(time.monotonic(), dict(head), b"", [(0, face([(20, 20, 32)]))])
    assert len(glass.packets) == 1                                   # shown: her idle goes
    glass.shown["kitchen"] = False                                    # her tile went (the board said so)
    for i in range(5):
        builder._send(time.monotonic() + i + 1, dict(head), b"", [(0, face([(20 + 16 * i, 20, 32)]))])
    assert len(glass.packets) == 1                                    # nothing at all while hidden (no key every 2 s)
    glass.shown["kitchen"] = True
    builder._send(time.monotonic() + 9, dict(head), b"", [(0, face([(100, 100, 32)]))])
    assert len(glass.packets) == 2 and glass.packets[-1][1]["frames"][0]["key"] is True   # her face here was stale: whole


def test_cover_fills_a_tall_glass_and_crops_in_the_hub(builder, glass):
    import numpy as np
    from PIL import Image
    stripe = face([(0, y, 48) for y in range(0, 512, 48)], w=512, h=512)   # a bright stripe down the left edge, 48 px wide
    glass.specs["kitchen"] = {"w": 240, "h": 288, "fit": "contain"}      # the pendant, overscanned past its 284 rows
    builder.spec = builder._spec()
    shown = np.asarray(Image.open(io.BytesIO(builder._frame(0, stripe)[0][1])))
    assert shown.shape[:2] == (288, 240) and shown[100:180, 8:16].mean() > 150   # contain: all of her, the stripe kept
    glass.specs["kitchen"]["fit"] = "cover"
    builder.spec, builder.last = builder._spec(), None
    shown = np.asarray(Image.open(io.BytesIO(builder._frame(0, stripe)[0][1])))
    assert shown.shape[:2] == (288, 240) and shown[100:180, 8:16].mean() < 80     # cover: the glass filled, her edge cropped here


def test_a_saved_fit_reaches_a_stream_already_running(builder, glass):
    glass.claims["kitchen"] = "r"
    head = {"kind": "speech", "sr": 24000, "sid": "r", "device": "kitchen", "taken": False, "ms": 40}
    builder._send(time.monotonic(), dict(head), b"\0\0" * 960, [(0, face(w=512, h=512))])
    glass.specs["kitchen"] = {"w": 320, "h": 240, "fit": "cover"}       # the Devices page saved: the hub looked again
    builder.respec, builder.next = True, None
    builder._send(time.monotonic() + 1, dict(head), b"\0\0" * 960, [(0, face([(20, 20, 32)], w=512, h=512))])
    entry = glass.packets[-1][1]["frames"][0]
    assert entry["key"] is True and (entry["w"], entry["h"]) == (320, 240) and builder.spec["fit"] == "cover"


def test_the_same_avatar_registering_again_keeps_her_stream(glass):
    feed.register("one")
    h = feed._hub
    feed.wake("one", wait=2)
    assert settle(lambda: h.session is not None)
    s = h.session
    assert feed.register("one", screens="always", fps=12) == ""
    assert feed._hub is h and h.session is s and h.cap == 12            # kept: no screens rest, no rebuild


def test_a_claim_lives_on_the_hub_and_survives_a_rebuilt_session(glass):
    glass.av = {"kitchen"}
    feed.register("one")
    feed.wake("one", wait=2)
    assert feed.claim("one", "kitchen", "r1")
    h = feed._hub
    h._rest()                                                          # the session rested before her first slice (a long think)
    feed.packet("one", "speech", [jpeg(1)], 25, pcm=b"\0\0" * 2400, sid="r1", device="kitchen")
    assert settle(lambda: glass.packets) and glass.packets[-1][2] is True     # the new builder voices it


def test_face_px_registered_again_reaches_a_running_board(glass):
    glass.av = {"kitchen"}
    feed.register("one")
    feed.wake("one", wait=2)
    assert settle(lambda: feed._hub.session and "kitchen" in feed._hub.session.builders)
    b = feed._hub.session.builders["kitchen"]
    feed.register("one", face_px=160)
    assert settle(lambda: b.respec is False and b.spec["w"] == 160, 3) or b._spec()["w"] == 160


def test_mode_for_follows_the_boards_that_answered(glass):
    glass.modes = {"kitchen": "talking"}
    feed.register("one")
    assert settle(lambda: feed._hub.looked is not None)
    assert feed.mode_for("one") == "talking"                             # not the rows' strongest (an offline Always board)

