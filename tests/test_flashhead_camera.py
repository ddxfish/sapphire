"""FlashHead's webcam (camera.py), the picture delay: her face waits for a
voice a virtual cable makes late. No camera, no sound device, no server: the
file is loaded by path under a name of its own, beside a stand-in `faces`, so
the plugin's real modules and the real install are never touched."""
import importlib.util
import io
import sys
import types
from collections import deque
from pathlib import Path

import numpy as np
import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "user" / "plugins" / "flashhead"
pytestmark = pytest.mark.skipif(not (PLUGIN / "camera.py").exists(), reason="FlashHead plugin not here")


@pytest.fixture
def camera(monkeypatch):
    saved = {"camera": True, "camera_delay": 0, "camera_mic": ""}
    faces = types.SimpleNamespace(settings=lambda: dict(saved), current=lambda s=None: (None, None))
    pkg = types.ModuleType("plugins.flashhead")
    pkg.faces = faces
    if "plugins" not in sys.modules:
        monkeypatch.setitem(sys.modules, "plugins", types.ModuleType("plugins"))
    monkeypatch.setitem(sys.modules, "plugins.flashhead", pkg)
    spec = importlib.util.spec_from_file_location("fh_camera_under_test", PLUGIN / "camera.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.saved = saved
    return mod


def test_her_picture_comes_out_as_many_ticks_late_as_asked(camera):
    line = deque()
    assert [camera._late(line, p, 3) for p in "abcdef"] == [None, None, None, "a", "b", "c"]
    line = deque()
    assert [camera._late(line, p, 0) for p in "abc"] == ["a", "b", "c"]      # no delay: as before


def test_a_tick_with_no_frame_holds_the_last_one_through_the_delay(camera):
    line = deque()
    assert [camera._late(line, p, 2) for p in ["a", None, "c", None, None]] == [None, None, "a", None, "c"]


def test_a_shorter_delay_jumps_to_the_newest_due_and_a_longer_one_holds(camera):
    line = deque()
    for p in "abcde":
        camera._late(line, p, 5)
    assert camera._late(line, "f", 2) == "d"         # a, b, c skipped: she catches up at once
    assert camera._late(line, "g", 4) is None        # longer: nothing new until the line has grown
    assert camera._late(line, "h", 4) is None
    assert camera._late(line, "i", 4) == "e"


def test_the_delay_is_whole_frames_between_none_and_two_seconds(camera):
    ticks = lambda v: camera._ticks({"camera_delay": v})
    assert [ticks(v) for v in (0, 40, 250, 2000, 9999, -80, "", None, "junk")] == [0, 1, 6, 50, 50, 0, 0, 0, 0]


def test_a_new_delay_alone_never_reopens_the_camera(camera):
    camera._opened = camera._rest(camera.saved)      # the camera is on with these
    camera.saved["camera_delay"] = 240
    camera.on_settings_saved(dict(camera.saved))
    assert camera._lag == 6 and not camera._again.is_set()
    camera.saved["camera_mic"] = "CABLE Input"       # anything else: open it again
    camera.on_settings_saved(dict(camera.saved))
    assert camera._again.is_set()


def test_a_save_while_the_camera_is_off_or_failed_tries_again(camera):
    assert camera._opened is None
    camera.on_settings_saved(dict(camera.saved))
    assert camera._again.is_set()


def test_the_camera_gets_each_frame_three_ticks_after_her_voice(camera, monkeypatch):
    """The whole loop, with a stand-in camera and feed: frame k leaves with tick k+3."""
    from PIL import Image
    sent, N, LAG = [], 8, 3

    def jpeg(level):
        b = io.BytesIO()
        Image.new("RGB", (64, 64), (level,) * 3).save(b, "JPEG")
        return b.getvalue()

    class Cam:
        device = "stand-in"
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def send(self, frame): sent.append(int(frame[360, 640, 0]))
        def sleep_until_next_frame(self): pass

    class Feed:
        live = True
        def __init__(self, voice): self.n = 0
        def next(self):
            self.n += 1
            if self.n > N + LAG:
                camera._stop.set()
                return None
            return (jpeg(40 + 20 * self.n), None) if self.n <= N else None
        def close(self): pass

    monkeypatch.setitem(sys.modules, "pyvirtualcam", types.SimpleNamespace(Camera=Cam))
    monkeypatch.setattr(camera, "_Feed", Feed)
    camera._serve({"camera": True, "camera_delay": LAG * 40})
    assert sent[:LAG] == [0] * LAG                                  # her still (none here: black) until the first is due
    assert all(abs(got - (40 + 20 * (k + 1))) <= 3 for k, got in enumerate(sent[LAG:LAG + N]))
    assert camera._opened is None and camera.status()["status"] == "off"


def _packet(head, body=b""):
    import json, struct
    h = json.dumps(head).encode()
    return struct.pack(">I", len(h)) + h + body


KEEPALIVE = _packet({})                              # server.py sends this after 5 s with nothing to show


def _feed(camera, monkeypatch, streams):
    """A feed with no thread of its own; each connect reads the next of these byte streams."""
    class Resp:
        def __init__(self, data): self.raw = io.BytesIO(data)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def close(self): pass

    streams = list(streams)
    http = types.SimpleNamespace(post=lambda *a, **kw: None, get=lambda *a, **kw: Resp(streams.pop(0)))
    camera.faces.base_url, camera.faces.http, camera.faces.forget = lambda s: "http://face", lambda s: http, lambda: None
    run = camera._Feed._run
    monkeypatch.setattr(camera._Feed, "_run", lambda self: None)
    return camera._Feed(True), run


def test_a_keepalive_is_skipped_and_the_stream_goes_on(camera, monkeypatch):
    voice = np.arange(2 * camera.TICK, dtype=np.int16)
    speech = _packet({"kind": "speech", "audio": voice.nbytes, "frames": [3, 4]}, voice.tobytes() + b"AAABBBB")
    feed, _ = _feed(camera, monkeypatch, [KEEPALIVE + speech + KEEPALIVE])
    with pytest.raises(EOFError):                    # the stand-in stream runs out; the keepalives raised nothing
        feed._watch()
    (a, va), (b, vb) = feed.units
    assert (a, b) == (b"AAA", b"BBBB") and len(va) == len(vb) == camera.TICK
    assert feed.fed


def test_the_wait_starts_over_after_a_stream_that_carried_her(camera, monkeypatch):
    class Done:
        waits = []
        def is_set(self): return len(self.waits) >= 4
        def wait(self, s): self.waits.append(s)

    good = _packet({"kind": "idle", "audio": 0, "frames": [3]}, b"AAA")
    feed, run = _feed(camera, monkeypatch, [b"", KEEPALIVE, good, b""])
    feed.done = Done()
    run(feed)
    assert Done.waits == [2, 4, 2, 4]                # keepalives alone are no stream; without the reset: 2, 4, 8, 16


def _speech(camera, n=24):
    """One of the server's packets of her words: n frames, each with its tick of voice."""
    voice = np.arange(n * camera.TICK, dtype=np.int16)
    return _packet({"kind": "speech", "audio": voice.nbytes, "frames": [3] * n}, voice.tobytes() + b"AAA" * n)


def test_the_webcam_keeps_every_word_the_server_sends_ahead(camera, monkeypatch):
    """A fast GPU sends her words up to TALK_AHEAD and a chunk early (server.py _pick): none of them is lost."""
    feed, _ = _feed(camera, monkeypatch, [_speech(camera) * 5])     # 4.8 s of her, at once
    with pytest.raises(EOFError):
        feed._watch()
    assert len(feed.units) == 5 * 24


def test_a_webcam_behind_in_the_quiet_skips_her_idle_and_keeps_her_words(camera, monkeypatch):
    idle = _packet({"kind": "idle", "audio": 0, "frames": [3] * 24}, b"III" * 24)
    feed, _ = _feed(camera, monkeypatch, [_speech(camera) * 3 + idle + _speech(camera)])
    with pytest.raises(EOFError):
        feed._watch()
    assert len(feed.units) == 4 * 24 and all(pic == b"AAA" for pic, _ in feed.units)   # 2.9 s deep: the idle second went, no word did


def test_a_webcam_late_in_the_quiet_still_keeps_her_whole_bank(camera, monkeypatch):
    """Live, 2026-11-17: a camera 2 s behind in the quiet met a full bank of her words and lost one at a cap of 6 s."""
    idle = _packet({"kind": "idle", "audio": 0, "frames": [3] * 24}, b"III" * 24)
    feed, _ = _feed(camera, monkeypatch, [idle * 4 + _speech(camera) * 5])     # as late as the quiet allows, then 4.8 s of her at once
    with pytest.raises(EOFError):
        feed._watch()
    assert sum(pic == b"AAA" for pic, _ in feed.units) == 5 * 24


def test_the_webcam_holds_more_than_the_server_sends_early(camera):
    """camera.BANK_S follows server.py's TALK_AHEAD: 1.8.1 raised the one and not the other, and words fell."""
    import re
    src = (PLUGIN / "server.py").read_text()
    ahead = float(re.search(r"^TALK_AHEAD = ([\d.]+)", src, re.M).group(1))
    assert camera.BANK_S >= ahead + 0.96 + camera.MAX_S + 0.96    # a chunk over it, behind a camera as late as the quiet allows


def test_a_quick_off_and_on_ends_the_camera_thread_from_before(camera, monkeypatch):
    started = []
    monkeypatch.setattr(camera.threading, "Thread", lambda **kw: types.SimpleNamespace(start=lambda: started.append(kw["args"][0])))
    camera.start(None, {})
    camera.start(None, {})                           # no stop() between, or one the old thread never saw
    old, new = started
    assert old.is_set() and not new.is_set() and camera._stop is new
