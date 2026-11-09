# core/service_health.py - is a plugin service READY, not just running?
#
# A process that is alive can still be downloading a model or compiling for
# minutes; "running" said nothing about that (2026-11-09: Audio8 on Prime
# downloaded and compiled for six minutes behind a "Speaking" that never
# spoke, and looked frozen). A service opts in with "health": "/health" in
# its manifest entry; core polls it and keeps one notification-center row
# per start: "Starting <plugin>" with the service's own phase words and bar,
# ending "<plugin> ready" or failed with the service's error. A start that is
# ready within GRACE makes no row. The plugins tab reads snapshot() through
# get_env_status.
#
# The contract, JSON from GET http://127.0.0.1:<port><health>:
#   status    "ready" | "error" | anything else (still starting)
#   phase     words for what it is doing now (optional)
#   progress  {"done": n, "total": n} for a bar (optional)
#   error     words for a failed start (with status "error")
# The service never imports core.notify: it is a subprocess, and the store
# file belongs to this one.
import logging
import threading
import time

logger = logging.getLogger(__name__)

GRACE = 3.0          # seconds: a start ready this fast makes no row
FAST, SLOW = 1.0, 5.0


def health_url(svc, port):
    """The service's health URL, or None when it declares none or a bad one."""
    path = svc.get("health")
    if not port or not isinstance(path, str) or not path.startswith("/") or "//" in path:
        return None
    return f"http://127.0.0.1:{int(port)}{path}"


class Watch:
    """Polls one service's health on a daemon thread until stop()."""

    def __init__(self, plugin, service, label, url, alive):
        self.plugin, self.label, self.url, self._alive = plugin, label, url, alive
        self.key = f"service:{plugin}:{service}"
        self.state = {"state": "starting", "phase": "", "progress": None, "error": ""}
        self._rid = None              # the active row, while one is open
        self._since = time.monotonic()
        self._told = False            # this start's end (or error) has been said
        self._halt = threading.Event()
        threading.Thread(target=self._run, daemon=True, name=f"health-{plugin}").start()

    def snapshot(self):
        return dict(self.state)

    def stop(self):
        """The service was stopped: an open row ends quietly (no toast, no count)."""
        self._halt.set()
        if self._rid:
            from core import notify
            notify.notify(f"{self.label} stopped", 'info', 'Stopped before it was ready.',
                          source=self.plugin, key=self.key, state='done')
            self._rid = None

    def _poll(self):
        if not self._alive():
            return {"state": "starting", "phase": "Starting the server", "progress": None, "error": ""}
        try:
            from core import net
            h = net.get(self.url, timeout=2).json()
        except Exception:
            return {"state": "starting", "phase": "Starting the server", "progress": None, "error": ""}
        status = str(h.get("status") or "")
        prog = h.get("progress") if isinstance(h.get("progress"), dict) else None
        if status == "ready":
            return {"state": "ready", "phase": "", "progress": None, "error": ""}
        if status == "error":
            return {"state": "error", "phase": "", "progress": None,
                    "error": str(h.get("error") or "failed to start")[:500]}
        return {"state": "loading", "phase": str(h.get("phase") or status or "Starting")[:200],
                "progress": prog, "error": ""}

    def _run(self):
        while not self._halt.is_set():
            st = self._poll()
            if self._halt.is_set():
                return
            was = self.state["state"]
            self.state = st
            try:
                self._tell(st, was)
            except Exception as e:
                logger.debug(f"[HEALTH] {self.plugin}: row update failed: {e}")
            self._halt.wait(SLOW if st["state"] in ("ready", "error") else FAST)

    def _tell(self, st, was):
        from core import notify
        if st["state"] in ("starting", "loading"):
            if was in ("ready", "error"):            # it went down: a new start
                self._since, self._told = time.monotonic(), False
            if not self._rid and time.monotonic() - self._since >= GRACE:
                self._rid = notify.begin(f"Starting {self.label}", source=self.plugin, key=self.key,
                                         go={'view': 'settings', 'tab': self.plugin}, body=st["phase"])
            if self._rid:
                p = st["progress"] or {}
                done, total = p.get("done"), p.get("total")
                if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
                    notify.tick(self._rid, done=done, total=total, text=st["phase"])
                else:
                    notify.tick(self._rid, text=st["phase"])
        elif st["state"] == "ready":
            if self._rid:
                notify.finish(self._rid, title=f"{self.label} ready", body="")
                self._rid = None
            self._told = True
        elif not self._told:                         # error, said once per start
            if self._rid:
                notify.finish(self._rid, failed=True, title=f"{self.label} failed to start", body=st["error"])
                self._rid = None
            else:
                notify.notify(f"{self.label} failed to start", 'error', st["error"], source=self.plugin,
                              key=self.key, go={'view': 'settings', 'tab': self.plugin})
            logger.warning(f"[HEALTH] {self.plugin}: {st['error']}")
            self._told = True
