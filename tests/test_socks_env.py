"""SOCKS-for-all env derivation tests (2026-08-31).

The invariant under test: FAIL-CLOSED. SOCKS enabled = proxy env stamped even
when broken (creds missing, socksio absent) — a dead proxy means loud request
failures, never silent direct traffic. Disabled = env scrubbed. NO_PROXY
carries loopback + LAN LLM endpoints + user extras; cloud LLM hosts join only
when routing LLMs is opted out (SOCKS_ROUTE_LLM=false).
"""
import os

import pytest

import config
from core import socks_proxy as sp

_ALL_VARS = [v for base in sp._PROXY_VARS + ("NO_PROXY",)
             for v in (base, base.lower())]


class FakeSettings:
    def __init__(self, d):
        self._d = d

    def get(self, key, default=None):
        return self._d.get(key, default)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Snapshot/restore proxy env; keep test writes out of the real
    load_errors lane and event bus; reset learn-once state."""
    keep = {v: os.environ.pop(v, None) for v in _ALL_VARS}
    monkeypatch.setattr(sp, "_drop_network_load_errors", lambda: None)
    monkeypatch.setattr(sp, "_push_network_load_error", lambda *a, **k: None)
    sp._llm_hint_sent.clear()
    yield
    for v, val in keep.items():
        if val is None:
            os.environ.pop(v, None)
        else:
            os.environ[v] = val


@pytest.fixture
def socks_on(monkeypatch):
    monkeypatch.setattr(config, "SOCKS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "SOCKS_HOST", "proxy.example", raising=False)
    monkeypatch.setattr(config, "SOCKS_PORT", 1080, raising=False)
    monkeypatch.setattr(config, "SOCKS_ROUTE_LLM", True, raising=False)
    monkeypatch.setattr(config, "SOCKS_REMOTE_DNS", True, raising=False)
    monkeypatch.setattr(config, "SOCKS_NO_PROXY_EXTRA", "", raising=False)
    monkeypatch.setattr(sp, "get_socks_credentials",
                        lambda: ("user", "p@ss:word"))
    monkeypatch.setattr("core.settings_manager.settings", FakeSettings({}))
    return monkeypatch


def test_disabled_scrubs_env(monkeypatch):
    monkeypatch.setattr(config, "SOCKS_ENABLED", False, raising=False)
    os.environ["ALL_PROXY"] = "socks5h://stale:1080"
    os.environ["no_proxy"] = "stale"
    sp.apply_proxy_env()
    for v in _ALL_VARS:
        assert v not in os.environ, v


def test_enabled_stamps_socks5h_with_quoted_creds(socks_on):
    sp.apply_proxy_env()
    expect = "socks5h://user:p%40ss%3Aword@proxy.example:1080"
    for base in sp._PROXY_VARS:
        assert os.environ[base] == expect
        assert os.environ[base.lower()] == expect
    assert os.environ["NO_PROXY"].startswith("localhost,127.0.0.1,::1")
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"]


def test_fail_closed_without_creds(socks_on):
    """Missing creds must NOT leave traffic direct — env still stamped
    (proxy will refuse = loud), and the gap is surfaced as a warning."""
    socks_on.setattr(sp, "get_socks_credentials", lambda: ("", ""))
    sp.apply_proxy_env()
    assert os.environ["ALL_PROXY"] == "socks5h://proxy.example:1080"
    assert any("credentials" in w for w in sp._env_warnings)


def test_no_proxy_lan_llm_always_exempt(socks_on):
    socks_on.setattr("core.settings_manager.settings", FakeSettings({
        "LLM_CUSTOM_PROVIDERS": {
            "lanbox": {"base_url": "http://192.168.1.20:1234/v1"},
            "cloudbox": {"base_url": "https://api.example.com/v1"},
        },
    }))
    sp.apply_proxy_env()
    entries = os.environ["NO_PROXY"].split(",")
    assert "192.168.1.20" in entries          # remote proxy can't reach LAN
    assert "api.example.com" not in entries   # cloud rides the proxy


def test_no_proxy_cloud_exempt_on_optout(socks_on):
    socks_on.setattr(config, "SOCKS_ROUTE_LLM", False, raising=False)
    socks_on.setattr("core.settings_manager.settings", FakeSettings({
        "LLM_PROVIDERS": {"claude": {"model": "claude-fable-5"}},
        "LLM_CUSTOM_PROVIDERS": {
            "cloudbox": {"base_url": "https://api.example.com/v1"},
        },
    }))
    sp.apply_proxy_env()
    entries = os.environ["NO_PROXY"].split(",")
    assert "api.example.com" in entries
    assert "api.anthropic.com" in entries     # SDK provider, no base_url


def test_no_proxy_extra_hosts(socks_on):
    socks_on.setattr(config, "SOCKS_NO_PROXY_EXTRA",
                     "homeassistant.local, 192.168.1.50", raising=False)
    sp.apply_proxy_env()
    entries = os.environ["NO_PROXY"].split(",")
    assert "homeassistant.local" in entries
    assert "192.168.1.50" in entries


def test_clear_session_cache_rederives_and_invalidates(monkeypatch):
    monkeypatch.setattr(config, "SOCKS_ENABLED", False, raising=False)
    os.environ["ALL_PROXY"] = "socks5h://stale:1080"
    calls = []
    fn = lambda: calls.append(1)
    sp.register_invalidator(fn)
    sp.register_invalidator(fn)  # dedup — registered once
    try:
        sp.clear_session_cache()
    finally:
        sp._invalidators.remove(fn)
    assert calls == [1]
    assert "ALL_PROXY" not in os.environ      # env re-derived (off = scrubbed)


def test_llm_hint_learn_once(socks_on):
    published = []
    socks_on.setattr("core.event_bus.publish",
                     lambda ev, payload: published.append(payload))
    sp.maybe_llm_proxy_hint("TestProv", "403 Forbidden from endpoint")
    sp.maybe_llm_proxy_hint("TestProv", "403 Forbidden from endpoint")
    assert len(published) == 1
    assert "TestProv" in published[0]["message"]
    sp.maybe_llm_proxy_hint("Other", "rate limit exceeded")  # not proxy-shaped
    assert len(published) == 1


def test_llm_hint_silent_when_proxy_off(monkeypatch):
    monkeypatch.setattr(config, "SOCKS_ENABLED", False, raising=False)
    published = []
    monkeypatch.setattr("core.event_bus.publish",
                        lambda ev, payload: published.append(payload))
    sp.maybe_llm_proxy_hint("TestProv", "403 Forbidden")
    assert published == []


def test_host_is_lan():
    assert sp._host_is_lan("localhost")
    assert sp._host_is_lan("127.0.0.1")
    assert sp._host_is_lan("192.168.1.20")
    assert sp._host_is_lan("10.0.0.5")
    assert sp._host_is_lan("homeassistant.local")
    assert not sp._host_is_lan("api.anthropic.com")
    assert not sp._host_is_lan("8.8.8.8")
    assert not sp._host_is_lan("")


def test_proxy_status_shape(socks_on):
    sp.apply_proxy_env()
    st = sp.proxy_status()
    assert st["enabled"] is True
    assert st["route_llm"] is True
    assert st["env_applied"] is True
    assert "127.0.0.1" in st["no_proxy"]
    assert isinstance(st["warnings"], list)


def test_scheme_follows_remote_dns(socks_on):
    sp.apply_proxy_env()
    assert os.environ["ALL_PROXY"].startswith("socks5h://")
    socks_on.setattr(config, "SOCKS_REMOTE_DNS", False, raising=False)
    sp.apply_proxy_env()
    assert os.environ["ALL_PROXY"].startswith("socks5://")


def test_conservative_defaults_when_keys_absent(socks_on):
    """A box whose settings lack the new keys must land on the
    works-everywhere posture: local DNS, LLMs direct (defaults-off flip,
    Krem's ruling after the PIA incident, 2026-08-31)."""
    socks_on.delattr(config, "SOCKS_REMOTE_DNS", raising=False)
    socks_on.delattr(config, "SOCKS_ROUTE_LLM", raising=False)
    socks_on.setattr("core.settings_manager.settings", FakeSettings({
        "LLM_PROVIDERS": {"claude": {"model": "claude-fable-5"}},
    }))
    sp.apply_proxy_env()
    assert os.environ["ALL_PROXY"].startswith("socks5://")          # local DNS
    assert "api.anthropic.com" in os.environ["NO_PROXY"].split(",")  # LLM direct
    st = sp.proxy_status()
    assert st["route_llm"] is False and st["remote_dns"] is False


def test_status_reports_remote_dns(socks_on):
    socks_on.setattr(config, "SOCKS_REMOTE_DNS", False, raising=False)
    sp.apply_proxy_env()
    st = sp.proxy_status()
    assert st["remote_dns"] is False
    assert st["dns_via_proxy"] is False
