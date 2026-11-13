"""Her 3D avatar on her screens: the plugin as a source of core's avatar feed.

render_server.py (a service in the plugin's own env) draws her; this reads what
it drew and hands every slice to core.avatar_feed, which plays it out to the
boards. It also tells the renderer what the browser tabs learn from the event
bus: the brain's rails, the active model, each sentence's mouth curve with its
voice.

`screens` (plugin state): off | talking | always. The renderer draws only while
this reads it, and this reads only while the feed has her boards open. One avatar
at a time is her face: while another plugin (FlashHead) holds the feed, this
waits and asks again.
"""
import json
import logging
import queue
import struct
import threading
import time
from urllib.parse import quote

import numpy as np
import requests

logger = logging.getLogger(__name__)

NAME = "avatar"
BASE = "http://127.0.0.1:5021"      # plugin.json: services[0].port
SR = 24000
FPS_MAX = 12                        # her hair and shoulders sway: nearly every frame is a whole face over a board's WiFi (1.4 Mbit/s at the pendant's 20)

_stop = threading.Event()
_wake = threading.Event()
_again = threading.Event()
_connected = threading.Event()
_voiced = False
_ours = False
_said = {}
_speech = queue.Queue(maxsize=1024)   # segments on their way to the renderer, in order (a reply made whole lands at once)
_http = requests.Session()
_threads = []


def _feed():
    from core import avatar_feed
    return avatar_feed


def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state("avatar")


def _mode():
    """off | talking | always, as core says: off unless Settings > Devices > Settings chose this avatar
    as her face, else the strongest mode her screens ask for (each screen's own, Devices page)."""
    try:
        if _state().get("enabled", True) is False:
            return "off"
        return _feed().mode_for(NAME)
    except Exception:
        return "off"


def _register():
    global _ours
    try:
        why = _feed().register(NAME, stop=_stop_at_source, screens=_mode(), fps=FPS_MAX)
    except Exception as e:
        why = f"no avatar feed ({e})"
    if why != _said.get("why"):                  # once, not at every ask
        _said["why"] = why
        if why:
            logger.info(f"[avatar] her screens are not this avatar's: {why}")
    _ours = not why
    return _ours


def _let_go():
    global _ours
    if _ours:
        _ours = False
        try:
            _feed().release(NAME)
        except Exception:
            pass


def start():
    _stop.clear()
    for target, name in ((_run, "avatar-screen"), (_say, "avatar-screen-voice")):
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        _threads.append(t)


def stop():
    _stop.set()
    _wake.set()
    _speech.put(None)
    _threads.clear()
    _let_go()                                    # always: or no other avatar could be her face


def changed():
    """A setting was saved: the feed takes the new mode, the renderer the new model."""
    _again.set()
    _wake.set()


def wake():
    """A reply starts: have the boards open and the renderer read before its first words."""
    if not _ours:
        return
    _wake.set()
    _feed().wake(NAME, wait=0.3)
    _connected.wait(0.3)


def carries(device_id):
    """The renderer is read with her voice and the feed gives that board one stream: a reply
    starting now rides in it (hooks/mouth.py claims it, glass.av_claim)."""
    return _connected.is_set() and _voiced and _feed().carries(device_id)


def flush(sid=""):
    """She was stopped: the renderer drops what it holds of that reply (its flush comes back
    down the stream and clears the boards). What still waits here for it goes first."""
    kept = []
    while True:
        try:
            item = _speech.get_nowait()
        except queue.Empty:
            break
        if item is None or (sid and item[0].get("stream_id") != sid):
            kept.append(item)
    for item in kept:
        _speech.put_nowait(item)
    _stop_at_source(sid)


def _stop_at_source(sid):
    try:
        r = _http.post(f"{BASE}/flush?sid={quote(str(sid or ''))}", timeout=2)
        return bool((r.json() if r.ok else {}).get("dropped"))
    except Exception as e:
        logger.info(f"[avatar] the renderer not flushed on a stop ({e})")
        return False


# ── her words to the renderer ────────────────────────────────────────────────
def speak(curve, audio_bytes):
    """One spoken segment (hooks/mouth.py): its curve and its voice, for the renderer to draw."""
    if not _connected.is_set():
        return                                   # nobody reads the renderer: nothing is drawn
    try:
        _speech.put_nowait((curve, audio_bytes))
    except queue.Full:
        logger.warning("[avatar] the renderer is far behind: a sentence is not drawn")


def _pcm(audio_bytes):
    from plugins.avatar import mouth
    data, sr = mouth._decode(audio_bytes)
    if sr != SR:                                 # a voice at another rate: linear is fine for a board's speaker
        n = int(len(data) * SR / sr)
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data)
    return (np.clip(data, -1, 1) * 32767).astype("<i2").tobytes()


def _say():
    while not _stop.is_set():
        item = _speech.get()
        if item is None or _stop.is_set():
            return
        curve, audio = item
        try:
            head = json.dumps({"sid": curve.get("stream_id") or "", "device": curve.get("device") or "",
                               "taken": False, "pause_ms": curve.get("pause_after_ms") or 0,
                               "curve": {k: curve.get(k) for k in ("dur", "fps", "env", "lo", "hi", "vis", "cues")}}).encode()
            r = _http.post(f"{BASE}/speak", data=struct.pack(">I", len(head)) + head + _pcm(audio), timeout=5)
            if not (r.ok and r.json().get("taken")):
                raise RuntimeError(r.text[:80])
        except Exception as e:
            logger.info(f"[avatar] a sentence not drawn ({e})")
            try:                                 # never a silent sentence: core's own door takes the rest of that reply
                from core.devices import glass
                glass.av_release(sid=curve.get("stream_id"))
            except Exception:
                pass


def _tell():
    """What the renderer draws from: the model, her mouth's gain, the brain's rails."""
    from plugins.avatar import brain
    from plugins.avatar.routes.api import AVATAR_DIR, _get_config
    cfg = _get_config()
    active = cfg.get("active_model") or ""
    m = (cfg.get("models") or {}).get(active) or {}
    snap = brain.snapshot()
    talk = (m.get("track_map") or {}).get("speaking") or ""
    return {"model": str(AVATAR_DIR / active) if active else "", "gain": float(m.get("mouth_gain") or 1.6),
            "rails": snap.get("rails") or {}, "speaking": talk}


# ── the reader ───────────────────────────────────────────────────────────────
def _run():
    while not _stop.is_set():
        mode = _mode()
        if mode == "off":
            _let_go()                            # the seat is free for another avatar
            _wake.wait(5)
            _wake.clear()
            _again.clear()
            continue
        if not _ours or _again.is_set() or _feed().source() != NAME:   # the seat moved away and back behind our back (Devices picker): take it again
            _again.clear()
            if not _register():
                _stop.wait(10)                   # another avatar is her face: ask again when it may have let go
                continue
        if mode != "always":
            _wake.wait(5)
            if not _wake.is_set() or _stop.is_set() or _again.is_set():
                continue
        _wake.clear()
        feed = _feed()
        if mode == "always":
            feed.wake(NAME, wait=1)
        w = feed.watching()
        if not w["boards"]:
            _stop.wait(1)                        # no screen to draw for: the renderer rests
            continue
        try:
            _watch(feed, mode, bool(w["av"]))
        except Exception as e:
            if str(e) != _said.get("down"):
                _said["down"] = str(e)
                logger.info(f"[avatar] her screen renderer is not read: {e}")
            _stop.wait(3)


def _watch(feed, mode, voiced):
    global _voiced
    told = None
    with _http.get(f"{BASE}/stream", stream=True, timeout=(3, 30)) as r:
        r.raise_for_status()
        _said.pop("down", None)
        raw = r.raw

        def read(n):
            b = b""
            while len(b) < n:
                got = raw.read(n - len(b))
                if not got:
                    raise EOFError("the renderer closed the stream")
                b += got
            return b

        _voiced = voiced
        _connected.set()
        logger.info(f"[avatar] her 3D face goes to the feed ({mode}{', with her voice' if voiced else ''})")
        checked = 0.0
        try:
            while not _stop.is_set() and not _again.is_set():
                now = time.monotonic()
                if now - checked > 0.3:
                    checked = now
                    state = _tell()
                    if state != told:            # the rails moved, or her model changed
                        _http.post(f"{BASE}/state", json=state, timeout=3)
                        told = state
                    w = feed.watching()
                    if _mode() != mode or feed.source() != NAME:
                        return
                    if mode == "talking" and not w["open"] and not _wake.is_set():
                        return                   # the feed let her boards rest: the renderer may too
                    if not w["boards"] or bool(w["av"]) != voiced:
                        return
                head = json.loads(read(struct.unpack(">I", read(4))[0]))
                kind = head.get("kind")
                if not kind:
                    continue                     # the renderer's keepalive
                if kind == "flush":
                    feed.flush(NAME, head.get("sid") or "")
                    continue
                body = read(head.get("audio", 0) + sum(head.get("frames", [])))
                at = head.get("audio", 0)
                frames = []
                for n in head.get("frames", []):
                    frames.append(body[at:at + n])
                    at += n
                if kind == "speech":
                    _wake.clear()
                feed.packet(NAME, kind, frames, head.get("fps") or 25,
                            pcm=body[:head.get("audio", 0)] if kind == "speech" else b"",
                            sr=head.get("sr") or SR, sid=head.get("sid") or "",
                            device=head.get("device") or "", taken=bool(head.get("taken")),
                            fresh=bool(head.get("fresh")))
        finally:
            _connected.clear()
            _voiced = False
            logger.info("[avatar] her 3D face rests")
