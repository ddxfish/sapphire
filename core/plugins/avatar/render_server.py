"""Her 3D avatar drawn with no browser: her face for the screens that show her.

A service in the plugin's own env (pygfx on the GPU). It draws the same GLB the
sidebar draws, from a camera in front of her face, and serves what it drew as
slices: a run of JPEG frames with the voice of the same span, the shape
core.avatar_feed takes (screen.py reads the stream and hands them over).

    GET  /health            {"status": "ready" | "starting" | "error", ...}
    GET  /stream            packets until the reader leaves: >I head length, the head
                            (JSON: kind, fps, sr, audio, frames [sizes], sid, device,
                            taken, fresh), her voice (16-bit mono), the JPEGs
    POST /speak             >I head length, the head (sid, device, taken, pause_ms,
                            the mouth curve of hooks/mouth.py), her voice (16-bit mono at SR)
    POST /state             {"model": path, "gain": n, "rails": {...}, "speaking": track}
    POST /flush?sid=        she was stopped: what waits of that reply goes

It draws only while someone reads /stream. Speech is drawn as fast as the GPU goes
(ahead of her voice: the feed holds each slice until its time); her idle is drawn
on the wall clock. Her body follows the brain's rails (brain.py), her mouth the
curve of each sentence through web/visemes.json, the one table the sidebar uses.

Stdlib + pygfx + numpy + pillow. No Sapphire imports: it runs in its own env.
"""
import collections
import io
import json
import logging
import math
import os
import random
import struct
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("avatar-render")

PORT = int(os.environ.get("SAPPHIRE_SERVICE_PORT") or 5021)
FPS, SIZE, SR = 25, 512, 24000
SLICE = 12                      # frames in a slice of her idle (about half a second)
TALK_SLICE = 25                 # ... and of her speech
FADE = 0.3                      # seconds one clip blends into the next
ATTACK, RELEASE, FOLLOW = 0.05, 0.09, 22    # her mouth, as web/mouth.js has it
HERE = Path(__file__).parent

_lock = threading.Lock()
_cv = threading.Condition(_lock)
_jobs = collections.deque()     # sentences waiting to be drawn
_viewers = []                   # a deque per reader of /stream
_want = {"model": "", "gain": 1.6, "rails": {}, "speaking": ""}
_health = {"status": "starting", "phase": "waiting for her model"}
_now = {"job": None, "flushed": False}      # the sentence being drawn; a flush since the draw thread last looked


def _visemes():
    raw = json.loads((HERE / "web" / "visemes.json").read_text(encoding="utf-8"))
    raw.pop("_about", None)
    out = {}
    for v, recipe in raw.items():
        out[v] = {}
        for k, w in recipe.items():
            for name in ((k[:-1] + "Left", k[:-1] + "Right") if k.endswith("*") else (k,)):
                out[v][name] = w
    return out


def _sample(arr, fps, t):
    f = t * fps
    i = int(f)
    if i < 0 or i >= len(arr):
        return 0.0
    b = arr[min(i + 1, len(arr) - 1)]
    return arr[i] + (b - arr[i]) * (f - i)


def mouth_target(curve, t, table):
    """Where her mouth wants to be at t seconds into a sentence: {shape: weight}."""
    out = {}
    env = _sample(curve.get("env") or [], curve.get("fps") or 50, t)
    vis = curve.get("vis") or []
    if not vis or not table:                 # no phonemes: the bands carry it
        lo = _sample(curve.get("lo") or [], curve.get("fps") or 50, t)
        hi = _sample(curve.get("hi") or [], curve.get("fps") or 50, t)
        out["jawOpen"] = env * 0.55
        out["mouthFunnel"] = env * min(1, lo * 0.8)
        out["mouthStretchLeft"] = out["mouthStretchRight"] = env * min(1, hi * 1.5)
        return out
    for start, v, dur in vis:
        if t < start - ATTACK:
            break
        end = start + dur
        if t > end + RELEASE:
            continue
        w = (t - start + ATTACK) / ATTACK if t < start else 1 - (t - end) / RELEASE if t > end else 1
        for k, x in (table.get(v) or {}).items():
            out[k] = max(out.get(k, 0), x * w)
    out["jawOpen"] = max(out.get("jawOpen", 0) * (0.35 + 0.65 * env), env * 0.18)   # loudness opens the jaw
    return out


class Stage:
    """Her model, lit, with a camera on her face. Everything here runs on the draw thread."""

    def __init__(self):
        import pygfx as gfx
        from rendercanvas.offscreen import RenderCanvas
        self.gfx = gfx
        self.canvas = RenderCanvas(size=(SIZE, SIZE), pixel_ratio=1)
        self.renderer = gfx.renderers.WgpuRenderer(self.canvas)
        self.camera = gfx.PerspectiveCamera(20, 1, depth_range=(0.05, 200))
        self.table = _visemes()
        self.model = ""
        self.scene = None
        self.canvas.request_draw(lambda: self.renderer.render(self.scene, self.camera))
        info = self.renderer.device.adapter.info
        logger.info(f"Drawing on {info.get('device')} ({info.get('backend_type')})")

    def load(self, path):
        gfx = self.gfx
        t0 = time.monotonic()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            g = gfx.load_gltf(path, quiet=True)
        scene = gfx.Scene()
        scene.add(gfx.Background.from_color("#141a24", "#2a3448"))
        scene.add(g.scene)
        scene.add(gfx.AmbientLight(intensity=1.6))
        key = gfx.DirectionalLight(intensity=2.6)
        key.local.position = (1.0, 2.5, 3.0)
        scene.add(key)
        self.meshes, self.head = [], None

        def walk(o):
            if isinstance(o, gfx.Mesh) and getattr(o, "morph_target_names", None):
                self.meshes.append((o, {n: i for i, n in enumerate(o.morph_target_names)}))
            if isinstance(o, gfx.Bone) and o.name == "Head":
                self.head = o
            for c in o.children:
                walk(c)
        walk(g.scene)
        box = g.scene.get_world_bounding_box()
        self.height = float(box[1][1] - box[0][1]) or 1.7
        self.mixer = gfx.AnimationMixer()
        self.clips = {a.name: a for a in g.animations or []}
        self.actions, self.playing, self.once_until = {}, None, 0.0
        self.shapes = collections.defaultdict(float)
        self.face_at = None
        self.blink_at, self.blink = random.uniform(2, 5), 0.0
        self.t = 0.0
        self.scene, self.model = scene, path
        logger.info(f"Her model: {Path(path).name}, {len(self.clips)} clips, {len(self.meshes)} meshes with shapes, {time.monotonic() - t0:.1f}s")

    def play(self, track, once=False):
        """Blend to that clip. `once`: a gesture, then whatever the rails say."""
        clip = self.clips.get(track)
        if not clip or (self.playing == track and not once):
            return bool(clip)
        act = self.actions.get(track) or self.mixer.clip_action(clip)
        self.actions[track] = act
        old = self.actions.get(self.playing)
        act.reset()
        act.set_loop(1 if once else math.inf)
        act.play()
        if old and old is not act:
            act.cross_fade_from(old, FADE, False)
        self.playing = track
        self.once_until = self.t + max(0.4, clip.duration - FADE) if once else 0.0
        return True

    def frame(self, dt, mouth=None, gain=1.6):
        """One frame, dt later: RGB array."""
        self.t += dt
        self.mixer.update(dt)
        self.blink_at -= dt
        if self.blink_at <= 0:
            self.blink_at, self.blink = random.uniform(2.5, 6), 0.14
        self.blink = max(0.0, self.blink - dt)
        tgt = {k: min(1.0, v * gain) for k, v in (mouth or {}).items()}
        tgt["eyeBlinkLeft"] = tgt["eyeBlinkRight"] = 1.0 if self.blink > 0 else 0.0
        k = 1 - math.exp(-FOLLOW * dt)
        for name in set(self.shapes) | set(tgt):
            self.shapes[name] += (tgt.get(name, 0.0) - self.shapes[name]) * (1.0 if name.startswith("eyeBlink") else k)
        for mesh, names in self.meshes:
            inf = mesh.morph_target_influences
            for name, w in self.shapes.items():
                i = names.get(name)
                if i is not None:
                    inf[i] = w
            mesh.morph_target_influences = inf
        if self.head is not None:
            at = np.array(self.head.world.position, dtype=float)
            at[1] += 0.045 * self.height
            # Locked to her head: on a board a face that drifts in its frame changes every pixel,
            # so every frame goes as a whole one (215 keys to 48 regions on the pendant) and her
            # voice shares that air. Locked, only what moves in her face is sent.
            self.face_at = at
            self.camera.local.position = (self.face_at[0], self.face_at[1], self.face_at[2] + 0.62 * self.height)   # closer than the sidebar's 0.8: a board's glass is small
            self.camera.look_at(tuple(self.face_at))
        return np.asarray(self.canvas.draw())[..., :3]


def _jpeg(rgb):
    from PIL import Image
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "JPEG", quality=85)
    return b.getvalue()


def _packet(kind, jpegs, pcm=b"", **tag):
    head = json.dumps({"kind": kind, "fps": FPS, "sr": SR, "audio": len(pcm),
                       "frames": [len(j) for j in jpegs], **tag}).encode()
    return struct.pack(">I", len(head)) + head + pcm + b"".join(jpegs)


def _broadcast(pkt):
    with _lock:
        for q in _viewers:
            if len(q) > 400:                 # a reader that stopped reading: it loses the oldest
                q.popleft()
            q.append(pkt)
        _cv.notify_all()


def _rail(rails):
    for r in ("3", "4", "5", "6"):
        if rails.get(r):
            return rails[r]
    return None


def draw():
    """The one thread that touches the GPU."""
    try:
        stage = Stage()
    except Exception as e:
        logger.exception("no way to draw here")
        _health.update(status="error", error=f"No GPU this can draw on: {e}")
        return
    _health.update(status="ready", phase="resting")              # it can draw: her model comes with the first reader
    virt = time.monotonic()                  # how far her drawn time has run; ahead of the wall while she speaks
    rail_id = None
    while True:
        with _cv:
            while not _jobs and not _viewers and _want["model"] == stage.model:
                _cv.wait(1.0)
            want = dict(_want)
            job = _jobs.popleft() if _jobs and stage.scene is not None else None
            _now["job"] = job
            flushed, _now["flushed"] = _now["flushed"], False
            watched = bool(_viewers)
        if flushed:
            virt = min(virt, time.monotonic())   # she was stopped: what was drawn ahead is gone, her idle starts NOW (the boards went black for the rest of the sentence, 2026-11-10)
        if want["model"] != stage.model:
            if not want["model"] or not Path(want["model"]).exists():
                time.sleep(0.5)
                continue
            try:
                stage.load(want["model"])
                rail_id = None
                _health.update(status="ready", phase="", error="")
            except Exception as e:
                logger.exception("her model would not load")
                _health.update(phase="her model would not load", error=str(e))
                with _lock:
                    _want["model"] = stage.model
                continue
        if stage.scene is None:
            time.sleep(0.2)
            continue
        now = time.monotonic()
        try:
            # her body: a gesture runs out, then the brain's winning rail
            rail = _rail(want["rails"])
            if stage.once_until and stage.t >= stage.once_until:
                stage.once_until, rail_id = 0.0, None
            if rail and not stage.once_until and rail.get("id") != rail_id:
                rail_id = rail.get("id")
                stage.play(rail["track"], once=rail.get("mode") == "oneshot")
            if job:
                fresh = now >= virt                              # her drawn time ran out before these words: they start now
                virt = max(virt, now)
                if want["speaking"] and not stage.once_until:
                    stage.play(want["speaking"])
                    rail_id = None                               # back to the rails after her words
                curve, pcm = job["curve"], job["pcm"]
                dur = float(curve.get("dur") or len(pcm) / 2 / SR)
                n = max(1, round((dur + job["pause_ms"] / 1000) * FPS))
                cues = sorted(curve.get("cues") or [], key=lambda q: q.get("t", 0))
                per = 2 * SR // FPS
                pcm = pcm.ljust(n * per, b"\0")[:n * per]        # her pause after it is silence in the same stream
                for a in range(0, n, TALK_SLICE):
                    if job["gone"]:
                        break
                    jpegs = []
                    for i in range(a, min(n, a + TALK_SLICE)):
                        t = i / FPS
                        while cues and cues[0].get("t", 0) <= t:
                            q = cues.pop(0)
                            if not str(q.get("track", "")).lower().startswith("go_"):
                                stage.play(q["track"], once=True)
                        jpegs.append(_jpeg(stage.frame(1 / FPS, mouth_target(curve, t, stage.table) if t <= dur else None, want["gain"])))
                    _broadcast(_packet("speech", jpegs, pcm[a * per:(a + len(jpegs)) * per], sid=job["sid"],
                                       device=job["device"], taken=job["taken"], fresh=fresh and a == 0))
                    virt += len(jpegs) / FPS
            elif watched and now >= virt - 0.25:
                if now > virt + 1.0:
                    virt = now                                   # nobody watched for a while: her time starts again
                jpegs = [_jpeg(stage.frame(1 / FPS, None, want["gain"])) for _ in range(SLICE)]
                _broadcast(_packet("idle", jpegs))
                virt += SLICE / FPS
            else:
                time.sleep(0.02)
        except Exception:
            logger.exception("a frame not drawn")
            time.sleep(0.5)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"            # /stream runs until the reader leaves

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            with _lock:
                return self._json({**_health, "viewers": len(_viewers), "waiting": len(_jobs)})
        if path != "/stream":
            return self._json({"error": "no such page"}, 404)
        q = collections.deque()
        with _cv:
            _viewers.append(q)
            _cv.notify_all()
        logger.info("A reader joined")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        try:
            idle = time.monotonic()
            while True:
                with _cv:
                    if not q:
                        _cv.wait(1.0)
                    pkt = q.popleft() if q else None
                if pkt is None:
                    if time.monotonic() - idle > 5:
                        idle = time.monotonic()
                        pkt = struct.pack(">I", 2) + b"{}"       # keepalive: a reader that left is noticed
                    else:
                        continue
                else:
                    idle = time.monotonic()
                self.wfile.write(pkt)
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with _lock:
                if q in _viewers:
                    _viewers.remove(q)
            logger.info("A reader left")

    def do_POST(self):
        url = urlparse(self.path)
        body = self._body()
        try:
            if url.path == "/state":
                new = json.loads(body or b"{}")
                with _cv:
                    for k in ("model", "gain", "rails", "speaking"):
                        if k in new:
                            _want[k] = new[k]
                    _cv.notify_all()
                return self._json({"ok": True})
            if url.path == "/speak":
                n = struct.unpack(">I", body[:4])[0]
                head = json.loads(body[4:4 + n])
                job = {"sid": str(head.get("sid") or ""), "device": str(head.get("device") or ""),
                       "taken": bool(head.get("taken")), "pause_ms": max(0, min(5000, int(head.get("pause_ms") or 0))),
                       "curve": head.get("curve") or {}, "pcm": body[4 + n:], "gone": False}
                with _cv:
                    if not _viewers:
                        return self._json({"taken": False, "why": "no reader"})
                    _jobs.append(job)
                    _cv.notify_all()
                return self._json({"taken": True})
            if url.path == "/flush":
                sid = (parse_qs(url.query).get("sid") or [""])[0]
                with _cv:
                    gone = [j for j in _jobs if not sid or j["sid"] == sid]
                    for j in gone:
                        _jobs.remove(j)
                    cur = _now["job"]
                    if cur and (not sid or cur["sid"] == sid):
                        gone.append(cur)             # the one being drawn stops at its next slice
                    for j in gone:
                        j["gone"] = True
                    _now["flushed"] = True
                    _cv.notify_all()
                head = json.dumps({"kind": "flush", "sid": sid}).encode()
                _broadcast(struct.pack(">I", len(head)) + head)
                return self._json({"dropped": len(gone)})
        except Exception as e:
            logger.warning(f"{url.path}: {e!r}")
            return self._json({"error": str(e)}, 400)
        self._json({"error": "no such page"}, 404)


if __name__ == "__main__":
    threading.Thread(target=draw, daemon=True, name="draw").start()
    logger.info(f"Her avatar's screen renderer on 127.0.0.1:{PORT}")
    # Windows lets a second process bind a port that is in use when address
    # reuse is on (the default here): two renderers, one port, requests split
    # between them. Off on Windows, so the second one fails to start instead.
    ThreadingHTTPServer.allow_reuse_address = (os.name != "nt")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
