"""FlashHead's server.py, the parts that need no GPU: who is which session,
what a viewer gets, what a stop drops."""
import importlib
import os
import queue
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "user" / "plugins" / "flashhead"
pytestmark = pytest.mark.skipif(not (PLUGIN / "server.py").exists(), reason="FlashHead plugin not here")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SAPPHIRE_ROOT", str(tmp_path))
    monkeypatch.syspath_prepend(str(PLUGIN))
    sys.modules.pop("server", None)
    mod = importlib.import_module("server")
    yield mod
    sys.modules.pop("server", None)


def test_a_computer_named_local_is_never_this_computers_sapphire(server):
    mine, theirs = server._session("local"), server._session("local", door=True)
    assert mine is not theirs
    assert server._session("local") is mine and server._session("local", door=True) is theirs


def test_a_viewer_behind_loses_its_oldest_packet_and_a_stop_always_lands(server):
    s = server._session("vm", door=True)
    q = queue.Queue(maxsize=2)
    s.viewers[q] = True
    for pkt in (b"1", b"2", b"3"):
        server._broadcast(s, pkt)
    assert [q.get_nowait(), q.get_nowait()] == [b"2", b"3"]
    server._broadcast(s, b"1")
    server._broadcast(s, b"2")
    server._flush(s)
    assert q.get_nowait() == b"2" and b"flush" in q.get_nowait()


def test_a_chunk_rendered_before_a_stop_is_never_sent_after_it(server):
    import threading
    s = server._session("vm", door=True)
    q = queue.Queue()
    s.viewers[q] = True
    threading.Thread(target=server._packer, daemon=True).start()
    gen = s.gen
    server._flush(s)                                       # she was stopped while it rendered
    server._out.put((s, gen, "speech", None, None, b"old words", None))
    server._out.put((s, s.gen, "speech", None, None, b"new words", None))
    got = [q.get(timeout=2), q.get(timeout=2)]
    assert b"flush" in got[0] and got[1] == b"new words"


def _speak(server):
    """POST /speak from this computer's Sapphire, through the real handler."""
    import io
    h = server.Handler.__new__(server.Handler)
    h.server = type("Local", (), {})()
    h.path = "/speak?take=1"
    h.headers = {"Host": "127.0.0.1:5017", "X-FlashHead": "1", "Content-Length": "0"}
    h.rfile = io.BytesIO(b"")
    replies = []
    h._json = lambda obj, code=200: replies.append(obj)
    h.do_POST()
    return replies[0]


def test_speak_is_refused_without_a_face_or_with_a_stalled_loop(server):
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    s.face, s.want = "f@1", None                           # its face failed to load: nothing will render
    server._state["status"] = "ready"
    server.BEAT[0] = server.time.monotonic()
    assert _speak(server) == {"taken": False, "why": "no face"}
    s.want = "face.png"
    server.BEAT[0] = server.time.monotonic() - 60          # the loop is stuck
    assert _speak(server) == {"taken": False, "why": "stalled"}


def test_a_face_still_coming_takes_her_words(server, tmp_path, monkeypatch):
    """Teri 2026-10-23: the first reply after Watch was split between the panel
    and her speakers while the render loop had not applied the face yet."""
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    face = tmp_path / "face.png"
    face.write_bytes(b"x")
    s.face, s.want = None, str(face)
    server._state["status"] = "ready"
    server.BEAT[0] = server.time.monotonic()
    server.P.update(n16=15360, n24=23040)
    monkeypatch.setattr(server, "_decode", lambda data: (server.np.zeros(100, server.np.float32),
                                                         server.np.zeros(150, server.np.int16)))
    assert _speak(server) == {"taken": True, "why": ""}
    assert s.open


def test_a_gap_mid_reply_holds_her_face_and_the_end_lets_it_go(server):
    np = server.np
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.2)
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    s.want = "face.png"
    s.open, s.heard = True, server.time.monotonic()
    s.pending["a16"] = np.ones(100, np.float32)            # half a chunk: the next sentence is late
    assert s.held() and server._pick() is None             # no padded tail, no calm loop, no reset
    s.open = False                                         # /end
    assert server._pick() is s
    s.open, s.heard = True, server.time.monotonic() - server.HOLD_S - 1
    assert not s.held()                                    # /end never came: she idles after HOLD_S


def test_a_tail_is_exactly_one_chunk_long(server):
    np = server.np
    assert len(server._fit(np.ones(23041, np.int16), 23040)) == 23040   # a24 one sample long: np.pad threw
    assert len(server._fit(np.ones(10, np.int16), 23040)) == 23040


def test_another_computer_never_reads_this_computers_folders(server):
    text = f"[Errno 2] No such file: '{server.ROOT}/user/models/x' or '{Path.home()}/y'"
    shown = server._private(text)
    assert str(server.ROOT) not in shown and str(Path.home()) not in shown


def test_only_the_newest_browser_plays_her_voice(server):
    """Two tabs each played her voice on their own clock: an echo a beat apart."""
    s = server._session("local")
    old, new, board = queue.Queue(), queue.Queue(), queue.Queue()
    s.viewers.update({old: True, new: True, board: False})
    s.voice = new
    server._broadcast(s, b"loud", b"quiet")
    assert (old.get_nowait(), new.get_nowait(), board.get_nowait()) == (b"quiet", b"loud", b"quiet")
    server._broadcast(s, b"voice only", b"")               # no frames: nothing for the silent ones
    assert new.get_nowait() == b"voice only" and old.empty() and board.empty()


def test_words_played_elsewhere_are_mouthed_in_silence(server, monkeypatch):
    """A device plays her voice: the queue keeps it whole (a board stream carries it to the
    device named), and the browser that plays her voice gets its audio zeroed at pack time."""
    import json
    import struct
    import threading
    np = server.np
    server.P.update(n16=4, n24=6, fps=25)
    s = server._session("local")
    monkeypatch.setattr(server, "_decode", lambda data: (np.ones(4, np.float32), np.ones(6, np.int16)))
    server._queue_speech(s, b"x", mute=True, device="pendant")
    a16, a24, tag = s.speech.get_nowait()
    assert a16.any() and a24.any() and tag == {"mute": True, "device": "pendant", "taken": False, "sid": ""}
    browser, board = queue.Queue(), queue.Queue()
    s.viewers.update({browser: True, board: False})
    s.boards.add(board)
    s.voice = browser
    threading.Thread(target=server._packer, daemon=True).start()
    server._out.put((s, s.gen, "speech", [np.zeros((2, 2, 3), np.uint8)], a24, None, tag))
    got = {}
    for name, q in (("browser", browser), ("board", board)):
        pkt = q.get(timeout=2)
        hl = struct.unpack(">I", pkt[:4])[0]
        head = json.loads(pkt[4:4 + hl])
        got[name] = (head, np.frombuffer(pkt[4 + hl:4 + hl + head["audio"]], np.int16))
    assert got["browser"][0]["mute"] and len(got["browser"][1]) == 6 and not got["browser"][1].any()
    assert got["board"][0]["device"] == "pendant" and got["board"][1].all()


def test_a_viewers_numbers_make_one_line(server, caplog):
    import json
    s = server._session("local")
    with caplog.at_level("INFO"):
        server._viewer_log(s, json.dumps({"packets": 40, "gaps": 2, "gap_s": 0.4, "late": 30, "out": 0.04,
                                          "clock": 1, "evil": "x"}).encode())
        server._viewer_log(s, b"not json")
    line = [r.message for r in caplog.records if r.message.startswith("viewer")]
    assert len(line) == 1 and "late frames 30" in line[0] and "gaps 2 (0.40s)" in line[0] and "ahead avg/min" in line[0]


def test_a_tap_on_her_face_names_the_stream_and_is_logged(server, monkeypatch, caplog):
    """Every speech packet carries core's stream id (a viewer stops that pump through
    /api/tts/stop); a flush by a viewer says so in the log."""
    import io
    import json
    import struct
    np = server.np
    server.P.update(n16=4, n24=6, fps=25)
    s = server._session("local")
    monkeypatch.setattr(server, "_decode", lambda data: (np.ones(4, np.float32), np.ones(6, np.int16)))
    server._queue_speech(s, b"x", taken=True, sid="pump-7")
    _, a24, tag = s.speech.get_nowait()
    pkt = server._packet("speech", [], a24, tag)
    head = json.loads(pkt[4:4 + struct.unpack(">I", pkt[:4])[0]])
    assert (head["kind"], head["sid"], head["taken"]) == ("speech", "pump-7", True)
    server._queue_speech(s, b"x", taken=True, sid="pump-8")       # waiting when the tap comes
    h = server.Handler.__new__(server.Handler)
    h.server = type("Local", (), {})()
    h.path = "/flush?by=viewer"
    h.headers = {"Host": "127.0.0.1:5017", "X-FlashHead": "1", "Content-Length": "0"}
    h.rfile = io.BytesIO(b"")
    replies = []
    h._json = lambda obj, code=200: replies.append(obj)
    with caplog.at_level("INFO"):
        h.do_POST()
    # dropped: how many slices went (screens.py reads it: did a stop hit anything)
    assert replies == [{"ok": True, "dropped": 1}] and s.speech.empty() and not s.open
    assert any(r.message == "Stopped from the viewer (local)" for r in caplog.records)


def test_her_speaker_keeps_her_voice_when_a_browser_watches_too(server):
    """The standalone viewer as her speaker: the app page's Speak (a browser that
    starts watching) took her voice from it (Teri's Pi, 2026-11-10)."""
    s = server._session("local")
    pi, browser, newer, call = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
    s.viewers[pi] = True
    s.speakers.add(pi)
    s.viewers[browser] = True
    assert server._voice_of(s) is pi
    s.viewers[call] = True
    s.call = call
    assert server._voice_of(s) is call                     # a webcam call hears her first
    s.call = None
    del s.viewers[call]
    s.viewers.pop(pi)
    s.speakers.discard(pi)
    s.viewers[newer] = True
    assert server._voice_of(s) is newer                    # no speaker: the newest browser


def test_her_words_waiting_are_rendered_seconds_ahead_her_idle_is_not(server):
    """A ~1 s cushion turned every GPU hiccup on a near-real-time card into a gap."""
    np = server.np
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.7)
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    s.want = "face.png"
    now = server.time.monotonic()
    s.clock = [now, 2.5]                                   # 2.5 s made ahead of her viewers
    assert server._pick() is None                          # idle: past its ~1 s lead
    s.speech.put((np.zeros(15360, np.float32), np.zeros(23040, np.int16), None))
    assert server._pick() is s                             # talking: banks up to TALK_AHEAD
    s.clock = [now, server.TALK_AHEAD + 0.5]
    assert server._pick() is None


def test_the_hold_counts_from_when_her_words_end_not_when_they_came(server):
    """A 20 s group lands in one burst: counted from its arrival, the hold was over
    before it finished playing and the next group came after a reset."""
    np = server.np
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.2)
    s = server._session("local")
    s.open = True
    s.heard = server.time.monotonic() + 3                  # her last chunk ends in 3 s on her viewers' clock
    s.pending["a16"] = np.ones(100, np.float32)
    assert s.held()


def test_a_face_that_fails_never_wedges_the_render_loop(server, monkeypatch):
    """Words drained to the packer while holding s.lock: _out holds two and the
    packer needs s.lock to send, so 3+ queued slices froze both for good."""
    import threading
    np = server.np
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.2)
    server._state["status"] = "ready"
    s = server._session("local")
    for _ in range(5):
        s.speech.put((np.zeros(15360, np.float32), np.zeros(23040, np.int16), None))
    monkeypatch.setattr(server, "_pick", lambda: s)
    monkeypatch.setattr(server, "_apply_face", lambda _s: (_ for _ in ()).throw(RuntimeError("no face")))
    threading.Thread(target=server._turn, daemon=True).start()   # blocks on _out (no packer here)
    server.time.sleep(0.2)
    assert s.lock.acquire(timeout=1)                       # the packer could take it and drain _out
    s.lock.release()


def test_a_new_reply_on_her_lane_cuts_the_last_one(server):
    """Made whole, a reply is over at core before it's heard: the next one queued
    behind all of it while the browser had already moved on."""
    np = server.np
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.2)
    s = server._session("local")
    slice_ = lambda sid, device="": (np.zeros(15360, np.float32), np.zeros(23040, np.int16),
                                     {"sid": sid, "device": device, "mute": False, "taken": True})
    for _ in range(3):
        s.speech.put(slice_("old"))
    s.speech.put(slice_("board", "pendant"))
    assert server._older_replies(s, "new", "") == {"old"}
    server._flush(s, "old")
    assert [t["sid"] for *_, t in list(s.speech.queue)] == ["board"]   # another lane plays on


def test_her_voice_on_every_viewer_when_asked(server):
    """Teri: the app page's Speak to her desktop AND her Pi viewer at once."""
    s = server._session("local")
    desk, pi, board = queue.Queue(), queue.Queue(), queue.Queue()
    s.viewers.update({desk: True, pi: True, board: False})
    s.voice, s.everyone = pi, True
    server._broadcast(s, b"loud", b"quiet")
    assert (desk.get_nowait(), pi.get_nowait(), board.get_nowait()) == (b"loud", b"loud", b"quiet")
