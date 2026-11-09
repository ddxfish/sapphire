"""Her body's shared decisions: one brain on the server, every renderer follows.

The split (Krem 2026-11-09, option B): what the server knows, the server decides
once, so the sidebar, her screens and the webcam show the same her.

  rail 3  her own state      typing / toolcall / agent / cron, happy when done
  rail 4  a human reaching   listening / processing / wakeword
  rail 5  idle variety       a weighted gesture now and then (not in a quiet bucket)
  rail 6  base               a resting pose for the time of day, re-rolled each cycle

Each renderer keeps what only it knows: rails 1-2 (her `<<avatar:>>` tags,
fired on its own playback clock), `speaking` (its own voice clock), and in the
tab the browser-only reaches (typing in the box, a message sent).

Publishes `avatar_state` {seq, rails: {"3".."6": {track, mode, id} | null}} on
every change; GET /api/plugin/avatar/state is the same snapshot for a late
joiner. A rail's `id` changes on every write, so a renderer replays a oneshot
only when the brain wrote it again. Oneshots clear here on the clip's own
length (read from the GLB): renderers never report back.
"""
import logging
import queue
import random
import threading
import time
from datetime import datetime

from core.event_bus import Events, get_event_bus, publish

logger = logging.getLogger(__name__)

AI = {
    Events.AI_TYPING_START: "typing",
    Events.AI_TYPING_END: "happy",
    Events.TOOL_EXECUTING: "toolcall",
    Events.TOOL_COMPLETE: "typing",
    Events.AGENT_SPAWNED: "agent",
    Events.AGENT_COMPLETED: "happy",
    Events.AGENT_DISMISSED: None,
    Events.CONTINUITY_TASK_STARTING: "cron",
    Events.CONTINUITY_TASK_COMPLETE: None,
    Events.CONTINUITY_TASK_SKIPPED: None,
    Events.CONTINUITY_TASK_ERROR: None,
    Events.LLM_ERROR: None,
}
USER = {
    Events.STT_RECORDING_START: "listening",
    Events.STT_RECORDING_END: "processing",
    Events.STT_PROCESSING: "processing",
    Events.WAKEWORD_DETECTED: "wakeword",
}
USER_TTL = 30.0           # a reach nobody answered (an empty recording) lets go
ONESHOT_KNOWN = {"happy", "wave", "attention", "attention2"}
FALLBACK_TRACK_MAP = {
    "idle": "idle", "listening": "listening", "processing": "thinking",
    "typing": "defaultanim", "speaking": "attention", "toolcall": "attention2",
    "wakeword": "attention", "happy": "happy", "agent": "thinking", "cron": "thinking",
}
FALLBACK_POOL = [
    {"track": "idle", "weight": 60}, {"track": "defaultanim", "weight": 10},
    {"track": "listening", "weight": 10}, {"track": "attention", "weight": 5, "oneshot": True},
    {"track": "happy", "weight": 4, "oneshot": True}, {"track": "wave", "weight": 3, "oneshot": True},
]


class Brain:
    def __init__(self):
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.rails = {3: None, 4: None, 5: None, 6: None}
        self.seq = 0
        self._ids = 0
        self._due = {}                    # name -> monotonic deadline
        self._load()

    # ── config ───────────────────────────────────────────────────────────────
    def _load(self):
        from plugins.avatar.routes.api import AVATAR_DIR, _get_config
        from plugins.avatar.glb_parser import extract_tracks
        cfg = _get_config()
        active = cfg.get("active_model") or "sapphire.glb"
        m = (cfg.get("models") or {}).get(active) or {}
        self.track_map = {**FALLBACK_TRACK_MAP, **(m.get("track_map") or {})}
        pool = m.get("idle_pool") or FALLBACK_POOL
        self.pool = [{"track": v.get("track"), "weight": v.get("weight", 10),
                      "oneshot": v.get("oneshot", False), "variety": v.get("variety", True),
                      "base": v.get("base") if isinstance(v.get("base"), list) else []}
                     for v in pool if v.get("track")]
        self.base_state = m.get("base_state")
        self.buckets = m.get("time_buckets") or [{"name": "day", "start": 7}, {"name": "night", "start": 21}]
        self.interval = max(0.5, float(m.get("variety_interval_min") or 2)) * 60
        self.oneshots = ONESHOT_KNOWN | {v["track"] for v in self.pool if v["oneshot"]}
        self.clips = {t["name"]: t["duration"] for t in extract_tracks(AVATAR_DIR / active)}
        self.enabled = cfg.get("enabled", True) is not False

    def _has(self, track):
        return bool(track) and (not self.clips or track in self.clips)

    # ── picks (ported from sidebar.js) ───────────────────────────────────────
    def _bucket(self):
        b = sorted(self.buckets, key=lambda x: x.get("start", 0))
        if not b:
            return None
        hour, pick = datetime.now().hour, b[-1]           # before the first start = the last bucket
        for x in b:
            if hour >= x.get("start", 0):
                pick = x
        return pick

    def _quiet(self):
        b = self._bucket()
        return bool(b) and b.get("quiet", b.get("name") == "night")

    def _base(self):
        name = (self._bucket() or {}).get("name")
        ok = [v["track"] for v in self.pool if name in v["base"] and self._has(v["track"])]
        if ok:
            return random.choice(ok)
        if self._has(self.base_state):
            return self.base_state
        return self.track_map.get("idle") or "idle"

    def _variety(self):
        pool = [v for v in self.pool if v["variety"] and self._has(v["track"])]
        if not pool:
            return None
        return random.choices([v["track"] for v in pool], [max(0, v["weight"]) for v in pool])[0]

    # ── rails ────────────────────────────────────────────────────────────────
    def _write(self, rail, track, mode=None):
        if not track:
            self.rails[rail] = None
            self._due.pop(f"r{rail}", None)
            return
        self._ids += 1
        mode = mode or ("oneshot" if track in self.oneshots else "loop")
        self.rails[rail] = {"track": track, "mode": mode, "id": self._ids}
        if mode == "oneshot":
            self._due[f"r{rail}"] = time.monotonic() + (self.clips.get(track) or 3.0)
        elif rail == 4:
            self._due["r4"] = time.monotonic() + USER_TTL
        else:
            self._due.pop(f"r{rail}", None)

    def _winning(self):
        return next((r for r in (3, 4, 5, 6) if self.rails[r]), None)

    def _arm(self):
        """Variety runs only while she rests on her base, as before."""
        if self._winning() == 6:
            self._due.setdefault("variety", time.monotonic() + self.interval * random.uniform(0.7, 1.3))
        else:
            self._due.pop("variety", None)

    def _on_event(self, etype, data):
        if etype in AI:
            if data.get("foreign") or data.get("surface") == "phone":
                return False              # a phone call's turn is not hers here
            state = AI[etype]
            self._write(3, self.track_map.get(state, state) if state else None)
            if etype in (Events.AI_TYPING_START, Events.LLM_ERROR):
                self._write(4, None)      # the reach was answered
            return True
        if etype in USER:
            state = USER[etype]
            self._write(4, self.track_map.get(state, state))
            return True
        return False

    def _tick(self, name):
        self._due.pop(name, None)
        if name.startswith("r"):
            self._write(int(name[1:]), None)
            return
        # the variety cycle: a fresh base for this hour, and a gesture unless it's quiet
        fresh = self._base()
        self._write(6, fresh, "loop")
        track = None if self._quiet() else self._variety()
        if track:
            self._write(5, track, "oneshot")   # a gesture, even from a looping clip

    # ── thread ───────────────────────────────────────────────────────────────
    def _listen(self, event):
        self._q.put((event.get("type"), event.get("data") or {}))

    def start(self):
        with self._lock:
            if self._thread:
                return
            self._stop.clear()
            self._write(6, self._base(), "loop")
            self._arm()
            get_event_bus().on(list(AI) + list(USER), self._listen)
            self._thread = threading.Thread(target=self._run, name="avatar-brain", daemon=True)
            self._thread.start()
        self._publish()

    def stop(self):
        with self._lock:
            get_event_bus().off(list(AI) + list(USER), self._listen)
            self._stop.set()
            self._q.put(None)
            t, self._thread = self._thread, None
        if t and t is not threading.current_thread():
            t.join(timeout=2)

    def reload(self):
        self._q.put(("_reload", {}))

    def snapshot(self):
        with self._lock:
            return {"seq": self.seq, "rails": {str(k): v for k, v in self.rails.items()}}

    def _run(self):
        while not self._stop.is_set():
            wait = min(self._due.values(), default=time.monotonic() + 60) - time.monotonic()
            try:
                item = self._q.get(timeout=max(0.01, min(60, wait)))
            except queue.Empty:
                item = ()
            if item is None:
                return
            changed = False
            try:
                with self._lock:
                    if item and item[0] == "_reload":
                        self._load()
                        if self.rails[6] and not self._has(self.rails[6]["track"]):
                            self._write(6, self._base(), "loop")
                        changed = True
                    elif item:
                        changed = self._on_event(*item)
                    now = time.monotonic()
                    for name in [n for n, t in self._due.items() if t <= now]:
                        self._tick(name)
                        changed = True
                    if changed:
                        self._arm()
                        self.seq += 1
            except Exception as e:
                logger.warning(f"[avatar] brain step failed: {e!r}")
            if changed:
                self._publish()

    def _publish(self):
        if self.enabled:
            publish("avatar_state", self.snapshot(), ephemeral=True)


_brain = None


def start():
    global _brain
    if _brain is None:
        _brain = Brain()
    _brain.start()


def stop():
    global _brain
    if _brain:
        _brain.stop()
        _brain = None


def reload():
    if _brain:
        _brain.reload()


def snapshot():
    return _brain.snapshot() if _brain else {"seq": 0, "rails": {}}
