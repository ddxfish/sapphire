"""core/service_health.py: a plugin service's readiness -> one notification row."""
import time

import pytest

from core import service_health as sh


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Rec:
    """Stands in for core.notify: records the calls the watcher makes."""

    def __init__(self):
        self.calls = []

    def begin(self, title, **kw):
        self.calls.append(("begin", title, kw.get("body")))
        return "r1"

    def tick(self, rid, done=None, total=None, text=None, **_):
        self.calls.append(("tick", done, total, text))

    def finish(self, rid, failed=False, title=None, body=None, **_):
        self.calls.append(("finish", failed, title, body))

    def notify(self, title, level='info', body='', **kw):
        self.calls.append(("notify", title, level, kw.get("state")))

    def kinds(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def rig(monkeypatch):
    rec, replies = _Rec(), []
    import core.notify, core.net
    for name in ("begin", "tick", "finish", "notify"):
        monkeypatch.setattr(core.notify, name, getattr(rec, name))

    def fake_get(url, timeout=None):
        if not replies:
            raise ConnectionError("refused")
        body = replies[0] if len(replies) == 1 else replies.pop(0)
        if isinstance(body, Exception):
            raise body
        return _Resp(body)

    monkeypatch.setattr(core.net, "get", fake_get)
    monkeypatch.setattr(sh, "FAST", 0.01)
    monkeypatch.setattr(sh, "SLOW", 0.01)
    return rec, replies


def _wait(pred, s=2.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_health_url_only_for_a_local_path():
    assert sh.health_url({"health": "/health"}, 5014) == "http://127.0.0.1:5014/health"
    assert sh.health_url({"health": "/health"}, None) is None
    assert sh.health_url({}, 5014) is None
    assert sh.health_url({"health": "http://evil/x"}, 5014) is None
    assert sh.health_url({"health": "//evil/x"}, 5014) is None


def test_ready_inside_grace_makes_no_row(rig):
    rec, replies = rig
    replies.append({"status": "ready"})
    w = sh.Watch("p", "svc", "P", "http://127.0.0.1:1/health", lambda: True)
    assert _wait(lambda: w.snapshot()["state"] == "ready")
    w.stop()
    assert rec.calls == []


def test_slow_start_is_one_row_with_a_bar_that_ends_ready(rig, monkeypatch):
    rec, replies = rig
    monkeypatch.setattr(sh, "GRACE", 0)
    replies.extend([{"status": "loading", "phase": "Downloading", "progress": {"done": 5, "total": 10}},
                    {"status": "loading", "phase": "Compiling"},
                    {"status": "ready"}])
    w = sh.Watch("p", "svc", "P", "http://127.0.0.1:1/health", lambda: True)
    assert _wait(lambda: "finish" in rec.kinds())
    w.stop()
    assert rec.kinds().count("begin") == 1
    assert ("tick", 5, 10, "Downloading") in rec.calls
    assert ("finish", False, "P ready", "") in rec.calls
    assert "notify" not in rec.kinds()           # stop after ready says nothing


def test_failed_start_says_its_error_once(rig, monkeypatch):
    rec, replies = rig
    monkeypatch.setattr(sh, "GRACE", 0)
    replies.extend([{"status": "loading", "phase": "Loading"}, {"status": "error", "error": "CUDA out of memory"}])
    w = sh.Watch("p", "svc", "P", "http://127.0.0.1:1/health", lambda: True)
    assert _wait(lambda: "finish" in rec.kinds())
    time.sleep(0.1)                              # several more error polls
    w.stop()
    fins = [c for c in rec.calls if c[0] == "finish"]
    assert fins == [("finish", True, "P failed to start", "CUDA out of memory")]
    assert w.snapshot()["error"] == "CUDA out of memory"


def test_stop_mid_start_ends_the_row_quietly(rig, monkeypatch):
    rec, replies = rig
    monkeypatch.setattr(sh, "GRACE", 0)
    replies.append({"status": "loading", "phase": "Downloading"})
    w = sh.Watch("p", "svc", "P", "http://127.0.0.1:1/health", lambda: True)
    assert _wait(lambda: "begin" in rec.kinds())
    w.stop()
    assert rec.calls[-1] == ("notify", "P stopped", "info", "done")


def test_down_after_ready_is_a_new_start(rig, monkeypatch):
    rec, replies = rig
    monkeypatch.setattr(sh, "GRACE", 0)
    replies.extend([{"status": "ready"}, ConnectionError("crashed"), {"status": "ready"}])
    w = sh.Watch("p", "svc", "P", "http://127.0.0.1:1/health", lambda: True)
    assert _wait(lambda: "finish" in rec.kinds())
    w.stop()
    assert rec.kinds()[0] == "begin" and rec.calls[0][1] == "Starting P"


def test_a_service_elsewhere_is_watched_through_its_own_fetch(monkeypatch):
    """A plugin's Client only worker: its first run downloads and compiles for
    minutes on another computer, and nothing here said so (2026-11-10)."""
    from core import service_health
    calls = []
    w = service_health.Watch.__new__(service_health.Watch)
    w.url, w._alive, w._fetch = None, (lambda: True), (lambda: calls.append(1) or
                                                       {"status": "loading", "phase": "Downloading", "progress": {"done": 1, "total": 4}})
    st = w._poll()
    assert calls and st["state"] == "loading" and st["phase"] == "Downloading" and st["progress"]["total"] == 4
