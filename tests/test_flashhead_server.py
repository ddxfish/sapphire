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
    server._out.put((s, gen, "speech", None, None, b"old words"))
    server._out.put((s, s.gen, "speech", None, None, b"new words"))
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
    monkeypatch.setattr(server, "_queue_speech", lambda s, data: None)
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
