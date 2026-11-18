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
    s.open, s.heard = True, server.time.monotonic() + 3    # her last words play for 3 s more
    s.pending["a16"] = np.ones(100, np.float32)            # half a chunk: the next sentence is late
    assert s.held() and server._pick() is None             # no padded tail, no calm loop, no reset
    s.open = False                                         # /end
    assert server._pick() is s
    s.open, s.heard = True, server.time.monotonic() - server.HOLD_S - 1
    assert not s.held()                                    # /end never came: she idles after HOLD_S


def test_the_end_of_her_last_word_goes_out_before_her_viewers_run_out(server):
    """Held with the rest, it went out after her next words came: "sun", a pause, "ny"."""
    np = server.np
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.2)
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    s.want = "face.png"
    s.clock = [server.time.monotonic(), 0.2]
    s.open, s.heard = True, server.time.monotonic() + 0.2  # her words made so far end in 0.2 s, and no more have come
    s.pending["a16"] = np.ones(100, np.float32)            # the end of her last word
    assert not s.held() and server._pick() is s            # it is rendered now, padded
    s.pending["a16"] = np.zeros(0, np.float32)
    assert s.held()                                        # and she holds on as she is: no calm loop, no reset


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
    browser, board = queue.Queue(), queue.Queue()
    s.viewers.update({browser: True, board: False})
    server._queue_speech(s, b"x", mute=True, device="pendant")
    a16, a24, tag = s.speech.get_nowait()
    assert a16.any() and a24.any() and tag == {"mute": True, "device": "pendant", "taken": False, "sid": ""}
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
    s.viewers[queue.Queue()] = True
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
    s.calls.append(call)
    assert server._voice_of(s) is call                     # a webcam call hears her first
    s.calls.remove(call)
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


def test_a_call_stream_left_over_from_a_reconnect_never_takes_her_voice_from_the_call(server):
    """The webcam opened a second stream as it reconnected; the first, closing late, cleared the call
    and her voice went to a browser for the rest of it."""
    s = server._session("local")
    browser, cam, ghost = queue.Queue(), queue.Queue(), queue.Queue()
    for q in (browser, cam, ghost):
        s.viewers[q] = True
    s.calls += [cam, ghost]                                # the ghost joined last
    assert server._voice_of(s) is ghost
    s.calls.remove(ghost)                                  # ... and is gone: what the stream's end does
    del s.viewers[ghost]
    assert server._voice_of(s) is cam


def _get(server, path):
    """GET from this computer's Sapphire, through the real handler."""
    h = server.Handler.__new__(server.Handler)
    h.server = type("Local", (), {})()
    h.path = path
    h.headers = {"Host": "127.0.0.1:5017", "X-FlashHead": "1"}
    replies = []
    h._json = lambda obj, code=200: replies.append(obj)
    h.do_GET()
    return replies[0]


def test_the_count_says_who_is_watching(server):
    s = server._session("local")
    for q, who in ((queue.Queue(), "browser"), (queue.Queue(), "browser"), (queue.Queue(), "webcam"), (queue.Queue(), "viewer")):
        s.viewers[q], s.who[q] = True, who
    got = _get(server, "/health")
    assert got["viewers"] == 4 and got["who"] == {"browser": 2, "viewer": 1, "webcam": 1}


def test_a_stalled_loop_drops_the_words_of_that_reply_it_took_and_no_other_replys(server, monkeypatch, caplog):
    """Taken before the stall and never heard: they came out late, over the rest of the reply playing at home."""
    np = server.np
    server.P.update(n16=4, n24=6, fps=25)
    monkeypatch.setattr(server, "_decode", lambda data: (np.ones(4, np.float32), np.ones(6, np.int16)))
    s = server._session("local")
    q = queue.Queue()
    s.viewers[q] = True
    s.face, s.want = "f@1", "face.png"
    server._state["status"] = "ready"
    server._queue_speech(s, b"x", taken=True, sid="this-reply")
    server._queue_speech(s, b"x", sid="a-boards-reply", device="pendant")   # another lane: a new reply on the same one cuts the last itself
    server.BEAT[0] = server.time.monotonic() - 60          # the loop is stuck
    h = server.Handler.__new__(server.Handler)
    h.server = type("Local", (), {})()
    h.path = "/speak?take=1&sid=this-reply"
    h.headers = {"Host": "127.0.0.1:5017", "X-FlashHead": "1", "Content-Length": "0"}
    h.rfile = __import__("io").BytesIO(b"")
    replies = []
    h._json = lambda obj, code=200: replies.append(obj)
    with caplog.at_level("WARNING"):
        h.do_POST()
        h.do_POST()                                        # the next chunk of it: nothing left to drop, said once
    assert replies == [{"taken": False, "why": "stalled"}] * 2
    left = [(tag or {}).get("sid") for _, _, tag in server._drain(s)]
    assert left and set(left) == {"a-boards-reply"}
    assert sum("stalled" in r.message for r in caplog.records) == 1


def _run(late=(), render=(0.15, 0.15, 0.15), pack=(0.05,), drops=0):
    return {"n": len(render), "render": list(render), "lead": [1.0] * len(render), "waits": 0, "waiting": False,
            "resets": 1, "rebases": 0, "drops": drops, "late": list(late), "pack": list(pack),
            "quiet": float(sum(late)), "at": 0.0, "cpu": None}


def test_a_late_face_names_whose_trouble_it_is(server, monkeypatch):
    """The run's numbers were only numbers: a GPU busy with other work, full video memory, a GPU
    too slow alone and a busy CPU each read the same in the log, and nothing reached the panel."""
    server.P.update(chunk_s=0.96, render_s=0.15, fps=25)
    s = server._session("local")
    spare = [(20.0, "20.0 GB of video memory free")]
    monkeypatch.setattr(server, "_spare", lambda r: spare[0])
    say = lambda **kw: server._verdict(s, _run(**kw), [0, 0.0])
    assert say() is None                                                   # on time: nothing is said
    assert say(render=(0.9, 1.1, 1.3)) is None                             # slow, and nobody heard it: nothing is said
    busy = say(late=(0.4, 1.2), render=(0.5, 0.9, 1.4))
    assert "late 2x" in busy and "up to 1.2 s" in busy and "busy with other work" in busy and "0.15 s alone" in busy
    assert "20.0 GB of video memory free" in busy
    assert "CPU was busy" in say(late=(0.4,), pack=(0.5, 0.6))
    assert "not in these numbers" in say(late=(0.4,))
    spare[0] = (0.1, "0.1 GB of video memory free")
    assert "video memory is full" in say(late=(0.4,), render=(0.5, 0.9, 1.4))
    spare[0] = (20.0, "")
    server.P.update(render_s=0.9, best=None)
    assert "1.1x real time with nothing else running" in say(late=(0.4,), render=(0.9, 1.0, 1.1))
    other = server._session("vm", door=True)
    other.viewers[queue.Queue()] = True                    # watching her calm loop: no GPU, no blame
    server.P.update(render_s=0.15, best=None)
    assert "not in these numbers" in say(late=(0.4,))
    other.gpu_at = server.time.monotonic()                 # rendered during this reply
    assert "1 other Sapphire shared this GPU" in say(late=(0.4,))


def test_words_that_come_after_her_viewers_ran_out_are_late_and_her_face_is_not(server, monkeypatch, caplog):
    np = server.np
    server.P.update(n16=4, n24=6, fps=25, chunk_s=0.96, render_s=0.15)
    monkeypatch.setattr(server, "_spare", lambda r: (None, ""))
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    words = lambda sid="r1": server._queue_decoded(s, np.ones(4, np.float32), np.ones(6, np.int16), False, "", True, sid)
    ago = lambda sec: server.time.monotonic() - sec
    words()                                                # her reply's first words: nothing was made before them
    s.run, s.said_sid = _run(), "r1"                       # her face is in the middle of this reply
    words()                                                # more, while the first still wait to be rendered: the GPU's, not theirs
    assert s.dry == [0, 0.0]
    server._drain(s)
    s.said, s.busy = ago(3), True                          # the last chunk is late and still rendering: the GPU's again
    words()
    assert s.dry == [0, 0.0]
    server._drain(s)
    s.busy = False
    words("r2")                                            # another reply's first words (a stop that found nothing left her open): no 95 s of lateness
    assert s.dry == [0, 0.0]
    server._drain(s)
    s.run = None                                           # the hold is over and she idles (a tool at work): a pause, no stutter
    words()
    assert s.dry == [0, 0.0]
    server._drain(s)
    s.run, s.said = _run(), ago(3)                         # every whole chunk made, and played out 3 s ago (a tail may wait: live, 2026-11-17)
    words()
    assert s.dry[0] == 1 and 2.9 < s.dry[1] < 3.5
    with caplog.at_level("WARNING"):
        server._run_end(s)
    assert "her words came late 1x" in s.sync["text"] and "her voice engine or the LLM" in s.sync["text"]
    assert s.dry == [0, 0.0] and any(r.message.startswith("sync (local)") for r in caplog.records)
    got = _get(server, "/health")["sync"]
    assert got["text"] == s.sync["text"] and got["ago"] == 0
    s.sync["at"] -= server.SYNC_S + 1
    assert _get(server, "/health")["sync"] is None         # ten minutes on: the panel lets it go


def _turns(server, monkeypatch, render=None):
    """A session whose turns render in 0.3 s (or as `render` does): turn(lead) runs one with that lead."""
    np = server.np
    server.P.update(n16=4, n24=6, fps=25, chunk_s=0.96, render_s=0.05)
    server._state["status"] = "ready"
    s = server._session("local")
    s.face, s.live = "f@1", True
    s.viewers[queue.Queue()] = True
    monkeypatch.setattr(server, "_pick", lambda: s)
    monkeypatch.setattr(server, "_apply_face", lambda _s: None)
    monkeypatch.setattr(server, "_render", render or (lambda _s, a16: (server.time.sleep(0.3), [])[1]))

    def turn(lead, waiting=False, fresh=False):
        server._drain(s)
        while not server._out.empty():
            server._out.get_nowait()
        s.speech.put((np.zeros(4, np.float32), np.zeros(6, np.int16), {"sid": "r1"}))
        s.lead, s.clock, s.fresh = lead, [server.time.monotonic() - 5.0 + lead, 5.0], fresh
        if s.run:
            s.run["waiting"] = waiting
        server._turn()
        return s.clock[1] - (server.time.monotonic() - s.clock[0])     # what her viewers hold now, by this clock

    return s, turn


def test_a_chunk_is_late_only_when_her_viewers_had_run_out_of_her(server, monkeypatch):
    s, turn = _turns(server, monkeypatch)
    turn(lead=1.0)                                         # 0.3 s to render, 1 s in hand: on time
    assert s.run["late"] == [] and s.run["quiet"] == 0
    turn(lead=0.0)                                         # nothing in hand: her viewers heard a pause
    assert len(s.run["late"]) == 1 and 0.25 < s.run["late"][0] < 0.6
    turn(lead=-9.0, waiting=True)                          # after a wait for her words: late for them, not for this
    assert len(s.run["late"]) == 1 and s.run["quiet"] > 9  # and quiet all the same, which the browser's pauses are set against
    turn(lead=0.0, fresh=True)                             # the first of a clock: nobody held anything before it
    assert len(s.run["late"]) == 1 and s.said_sid == "r1"


def test_her_viewers_start_again_from_a_late_chunk_and_so_does_the_clock(server, monkeypatch):
    """Live, 2026-11-17: one pause in her words left a viewer 1.9 s later for good. The clock stayed where it
    was, took the seconds her viewers had waited as still owed and sent them as idle."""
    s, turn = _turns(server, monkeypatch)
    assert 1.6 < turn(lead=1.0) < 1.7                      # on time: 1.0 - 0.3 + 0.96, the clock untouched
    assert 0.9 < turn(lead=-1.5) < 0.97                    # they had run out: they hold this chunk and no more, and it owes them nothing
    assert 0.9 < turn(lead=0.5, fresh=True) < 0.97         # the first of a clock: it starts when they do, at the hand-over


def test_a_chunk_stopped_while_it_rendered_is_no_part_of_the_clock_the_stop_started(server, monkeypatch):
    s, turn = _turns(server, monkeypatch, render=lambda _s, a16: (server._flush(_s), [])[1])   # she is stopped mid-render
    turn(lead=1.0)
    assert s.clock[1] == 0.0 and s.fresh                   # 0.96 s of a chunk nobody got held her stopped face longer, and read as late


def test_her_voice_stays_with_the_viewer_that_holds_her_words_until_they_are_out(server):
    """Live, 2026-11-17: a second browser took her voice mid-reply and played the next words over the 4 s the first still held."""
    server.P.update(n16=15360, n24=23040, chunk_s=0.96, render_s=0.2)
    s = server._session("local")
    a, b = queue.Queue(), queue.Queue()
    s.viewers[a], s.want = True, "face.png"
    server._revoice(s)
    assert s.voice is a
    s.heard = server.time.monotonic() + 3                  # 3 s of her are banked in a
    s.viewers[b] = True                                    # b joins
    server._revoice(s)
    assert s.voice is a and s.revoice
    s.heard = server.time.monotonic() - 0.1                # they are out
    server._pick()
    assert s.voice is b and not s.revoice
    s.heard = server.time.monotonic() + 3
    del s.viewers[b]                                       # the one that plays her leaves mid-reply: the other takes her at once
    server._revoice(s)
    assert s.voice is a


def _post(server, path):
    """POST from this computer's Sapphire, through the real handler."""
    import io
    h = server.Handler.__new__(server.Handler)
    h.server = type("Local", (), {})()
    h.path = path
    h.headers = {"Host": "127.0.0.1:5017", "X-FlashHead": "1", "Content-Length": "1"}
    h.rfile = io.BytesIO(b"x")
    replies = []
    h._json = lambda obj, code=200: replies.append(obj)
    h.do_POST()
    return replies[0]


def test_a_stopped_reply_takes_no_more_words(server, monkeypatch):
    """One on its way when she was stopped landed after the stop, and was spoken."""
    np = server.np
    server.P.update(n16=4, n24=6, fps=25)
    server._state["status"] = "ready"
    server.BEAT[0] = server.time.monotonic()
    monkeypatch.setattr(server, "_decode", lambda data: (np.ones(4, np.float32), np.ones(6, np.int16)))
    s = server._session("local")
    s.viewers[queue.Queue()] = True
    s.face, s.want = "f@1", "face.png"
    assert _post(server, "/speak?take=1&sid=r1") == {"taken": True, "why": ""} and s.speech.qsize() == 1
    assert _post(server, "/flush?sid=r1")["dropped"] == 1
    assert _post(server, "/speak?take=1&sid=r1") == {"taken": True, "why": "stopped"} and s.speech.empty()
    assert _post(server, "/speak?take=1&sid=r2")["why"] == "" and s.speech.qsize() == 1     # the next reply speaks
    assert _post(server, "/flush")["dropped"] == 1                                         # stopped whole: what waited is known by its id
    assert _post(server, "/speak?take=1&sid=r2")["why"] == "stopped"
    server._flush(s, sid="r3", dead=False)                 # the stalled loop's own drop: the rest of that reply may still come
    assert _post(server, "/speak?take=1&sid=r3")["why"] == ""


def test_words_that_come_when_nobody_watches_wait_for_no_one(server):
    """The last viewer left while they were decoded: they sat there, and the next Watch began with an old sentence."""
    np = server.np
    server.P.update(n16=4, n24=6, fps=25)
    s = server._session("local")
    server._queue_decoded(s, np.ones(4, np.float32), np.ones(6, np.int16), False, "", True, "r1")
    assert s.speech.empty() and not s.open


def test_her_idle_says_how_far_ahead_it_is_sent(server):
    import json
    import struct
    server.P.update(fps=25, render_s=0.9)
    head = lambda kind: (lambda p: json.loads(p[4:4 + struct.unpack(">I", p[:4])[0]]))(server._packet(kind, [], None))
    assert head("idle")["lead"] == 1.35 and "lead" not in head("speech")


def test_the_browsers_numbers_say_what_only_it_saw(server):
    server.P.update(chunk_s=0.96, fps=25)
    s = server._session("local")
    now = server.time.monotonic
    gaps = {"packets": 40, "gaps": 3, "gap_s": 2.6}
    assert server._viewer_verdict(s, gaps) is None                                 # no run of ours to set it against: nothing is said
    s.ran = (now(), 0.0, False)
    said = server._viewer_verdict(s, gaps)
    assert "paused 3x (2.6 s)" in said and "2.6 s of it with her sent on time" in said
    s.ran = (now(), 2.4, False)                            # her words came late in that reply, or a render did: that is the pause
    assert server._viewer_verdict(s, gaps) is None
    s.ran = (now(), 1.0, False)                            # some of it was ours
    assert "1.6 s of it with her sent on time" in server._viewer_verdict(s, gaps)
    s.ran = (now(), 0.0, True)                             # a viewer joined in the middle: it held nothing ahead
    assert server._viewer_verdict(s, gaps) is None
    assert server._viewer_verdict(s, {"packets": 40, "late": 20}) is None          # a few frames: nothing
    busy = server._viewer_verdict(s, {"packets": 40, "late": 300, "stalls": 2})
    assert "page was busy" in busy and "300 of her frames" in busy and "froze 2x" in busy
    assert "audio stopped 1x" in server._viewer_verdict(s, {"packets": 40, "paused": 1})
    assert server._viewer_verdict(s, {"packets": 40, "skipped": 0.96}) is None     # one second of idle: nothing
    assert "skipped ahead in the quiet" in server._viewer_verdict(s, {"packets": 40, "skipped": 5.8})
    assert server._viewer_verdict(s, {"packets": 40, "ahead_max": 5.5}) is None    # her whole bank: as it should be
    assert "about 7 s behind her" in server._viewer_verdict(s, {"packets": 40, "ahead_max": 12.0})
