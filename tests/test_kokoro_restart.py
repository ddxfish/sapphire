"""Kokoro's self-restart waits for quiet and measures growth, not size; the
provider reads its server settings live and a blank fallback means the primary.

Field report 2026-11-06 (Prime, 4090 in a VM): voice dropped out now and then
in plain chat. Kokoro idled at 2.64 GB against an absolute 3 GB ceiling, so
under load every 10th request scheduled os._exit a second later, whatever it
was saying; the blank fallback setting then sent requests to '/tts'."""
import threading
import time

import pytest

from _kokoro_stub import import_tts_server

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")   # the fake exit ends its thread


@pytest.fixture
def ts(monkeypatch):
    ts = import_tts_server()
    exits = threading.Event()

    def fake_exit(code):
        exits.set()
        raise SystemExit             # ends the waiter thread, not pytest
    monkeypatch.setattr(ts.os, "_exit", fake_exit)
    monkeypatch.setattr(ts, "_restart_why", None)
    monkeypatch.setattr(ts, "_busy", 0)
    monkeypatch.setattr(ts, "QUIET_S", 0.3)
    ts.exits = exits
    yield ts
    with ts.request_count_lock:      # stand the waiter down before monkeypatch gives back the real os._exit
        ts._restart_why = None
    time.sleep(0.7)


def test_restart_waits_for_nothing_in_flight_then_quiet(ts, monkeypatch):
    monkeypatch.setattr(ts, "_busy", 1)
    monkeypatch.setattr(ts, "_last_done", time.monotonic())
    ts.schedule_restart("test")
    assert not ts.exits.wait(1.2)                       # a stream is speaking: never cut
    with ts.request_count_lock:
        ts._busy, ts._last_done = 0, time.monotonic()
    assert not ts.exits.wait(0.1)                       # just finished: a reply's next chunk may be coming
    assert ts.exits.wait(2)


def test_one_waiter_however_often_asked(ts, monkeypatch):
    monkeypatch.setattr(ts, "_busy", 1)
    before = threading.active_count()
    ts.schedule_restart("a")
    ts.schedule_restart("b")
    assert threading.active_count() == before + 1 and ts._restart_why == "a"


def test_growth_past_warm_size_not_absolute_size(ts, monkeypatch):
    asked = []
    monkeypatch.setattr(ts, "schedule_restart", asked.append)
    monkeypatch.setattr(ts, "_rss_base", 2.64)
    monkeypatch.setattr(ts, "request_count", 9)
    monkeypatch.setattr(ts, "_rss_gb", lambda: 3.4)     # over the old 3 GB line, 0.76 grown
    ts._count_request()
    assert asked == []
    monkeypatch.setattr(ts, "request_count", 19)
    monkeypatch.setattr(ts, "_rss_gb", lambda: 4.9)
    ts._count_request()
    assert asked and "grew" in asked[0]


def test_request_cap_still_restarts_when_quiet(ts, monkeypatch):
    asked = []
    monkeypatch.setattr(ts, "schedule_restart", asked.append)
    monkeypatch.setattr(ts, "_rss_base", 2.0)
    monkeypatch.setattr(ts, "_rss_gb", lambda: 2.1)
    monkeypatch.setattr(ts, "request_count", ts.MAX_REQUESTS - 1)
    ts._count_request()
    assert asked == [f"{ts.MAX_REQUESTS}/{ts.MAX_REQUESTS} requests"]


@pytest.fixture
def provider(monkeypatch):
    from core.tts.providers import kokoro
    vals = {"TTS_PRIMARY_SERVER": "http://p:5012", "TTS_FALLBACK_SERVER": "", "TTS_FALLBACK_TIMEOUT": 0.2}
    monkeypatch.setattr(kokoro, "config", type("C", (), {"__getattr__": lambda s, k: vals[k]})())
    p = kokoro.KokoroTTSProvider.__new__(kokoro.KokoroTTSProvider)
    p._doors, p._ok, p.served_by, p._health_why = {}, {}, None, "down"
    p.vals = vals
    return p


def test_blank_fallback_stays_on_the_primary(provider, monkeypatch):
    checked = []
    monkeypatch.setattr(provider, "_check_health", lambda url, timeout=None: checked.append(url) or False)
    assert provider._get_server_url() == "http://p:5012"
    assert provider.is_available() is False and "" not in checked


def test_server_settings_are_read_live(provider, monkeypatch):
    monkeypatch.setattr(provider, "_check_health", lambda url, timeout=None: url == "http://f:5012")
    provider.vals["TTS_FALLBACK_SERVER"] = " http://f:5012 "
    assert provider._get_server_url() == "http://f:5012"
    provider.vals["TTS_PRIMARY_SERVER"] = "http://f:5012"
    assert provider._get_server_url() == "http://f:5012" and provider.served_by["lane"] == "primary"
