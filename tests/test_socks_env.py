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
    # clear_session_cache() now re-probes; stub it so no test spawns a real
    # proxy-dialing thread. The reprobe test overrides this with its own spy.
    monkeypatch.setattr(sp, "start_boot_probe", lambda: None)
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


def test_disabled_scrubs_proxies_keeps_no_proxy_belt(monkeypatch):
    """SOCKS off: every *_PROXY var is scrubbed, but the NO_PROXY belt stays
    stamped — on Windows, an env with no proxy vars at ALL makes urllib fall
    back to the WinINET registry proxy with zero bypass entries, sending LAN
    gear through a corporate proxy (hunt 2026-09-04 S4-01)."""
    monkeypatch.setattr(config, "SOCKS_ENABLED", False, raising=False)
    os.environ["ALL_PROXY"] = "socks5h://stale:1080"
    os.environ["no_proxy"] = "stale"
    sp.apply_proxy_env()
    for base in sp._PROXY_VARS:
        assert base not in os.environ, base
        assert base.lower() not in os.environ, base.lower()
    # Belt re-derived (not the stale value), present in both cases
    assert "127.0.0.1" in os.environ.get("NO_PROXY", "")
    assert os.environ.get("no_proxy") == os.environ.get("NO_PROXY")
    assert "stale" not in os.environ["NO_PROXY"]


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


# ── LAN belt + direct-host registry (net facade, 2026-09-01) ─────────────

def test_no_proxy_lan_belt(socks_on):
    """RFC1918 CIDRs + LAN suffixes always ride NO_PROXY while SOCKS is on
    (the Prime blinds class: a remote proxy can't dial into the LAN)."""
    sp.apply_proxy_env()
    entries = os.environ["NO_PROXY"].split(",")
    for e in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
              "169.254.0.0/16", ".local", ".lan", ".home.arpa"):
        assert e in entries, e


def test_no_proxy_includes_registered_direct_hosts(socks_on):
    fn = lambda: {"sapphire-pi", "Camera-Hub."}
    sp.register_direct_hosts(fn, owner='t-hosts')
    try:
        sp.apply_proxy_env()
        entries = os.environ["NO_PROXY"].split(",")
        assert "sapphire-pi" in entries
        assert "camera-hub" in entries        # normalized: lowercase, dot-stripped
    finally:
        sp.unregister_direct_hosts('t-hosts')


def test_direct_hosts_provider_failure_tolerated(socks_on):
    bad = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    good = lambda: ["pi-two"]
    sp.register_direct_hosts(bad, owner='t-bad')
    sp.register_direct_hosts(good, owner='t-good')
    try:
        assert "pi-two" in sp.direct_hosts()
        sp.apply_proxy_env()                  # derivation survives the bad one
        assert "pi-two" in os.environ["NO_PROXY"].split(",")
    finally:
        sp.unregister_direct_hosts('t-bad')
        sp.unregister_direct_hosts('t-good')


def test_direct_hosts_string_provider_counts_as_one(socks_on):
    """Chaos F2: a bare-string return must be ONE host, never iterated
    into single characters (which bypassed whole TLDs via endswith)."""
    sp.register_direct_hosts(lambda: "Sapphire-Pi", owner='t-str')
    try:
        assert sp.direct_hosts() == {"sapphire-pi"}
    finally:
        sp.unregister_direct_hosts('t-str')


def test_register_same_owner_replaces_not_accumulates(socks_on):
    """Longevity find: plugin reload re-registers a NEW function object;
    owner-keying must replace, not append a stale closure forever."""
    sp.register_direct_hosts(lambda: {"a-host"}, owner='t-re')
    sp.register_direct_hosts(lambda: {"b-host"}, owner='t-re')   # reload sim
    try:
        assert sp.direct_hosts() == {"b-host"}
        assert list(sp._direct_host_providers).count('t-re') == 1
    finally:
        sp.unregister_direct_hosts('t-re')


def test_bad_llm_base_url_never_aborts_derivation(socks_on):
    """Day-ruiner CRIT: one malformed base_url raising out of
    apply_proxy_env = env never stamped = every lane DIRECT while the
    UI says SOCKS on. The bad provider is skipped; env still stamps."""
    socks_on.setattr("core.settings_manager.settings", FakeSettings({
        "LLM_CUSTOM_PROVIDERS": {
            "broken": {"base_url": "http://[::1"},          # unparseable
            "lanbox": {"base_url": "http://192.168.1.20:1234/v1"},
        },
    }))
    sp.apply_proxy_env()                                    # must not raise
    entries = os.environ["NO_PROXY"].split(",")
    assert "192.168.1.20" in entries                        # good one survives
    assert os.environ["ALL_PROXY"].startswith("socks5")     # env STAMPED


def test_no_proxy_stamped_before_proxy_vars(socks_on, monkeypatch):
    """H6: in the stamp window a belt-lane LAN call must see the bypass
    before it can see a proxy. Record env-write order."""
    order = []
    real_setitem = os.environ.__class__.__setitem__
    def spy(self, k, v):
        order.append(k)
        real_setitem(self, k, v)
    monkeypatch.setattr(os.environ.__class__, '__setitem__', spy)
    sp.apply_proxy_env()
    keys = [k for k in order if k.upper() in ('NO_PROXY', 'ALL_PROXY')]
    assert keys.index('NO_PROXY') < keys.index('ALL_PROXY')


def test_invalid_port_does_not_stamp_env(socks_on):
    """Wave 2: a bad SOCKS_PORT stamped a dead ALL_PROXY while the strip
    said env_applied=True. Now it refuses to stamp and warns."""
    socks_on.setattr(config, "SOCKS_PORT", "not-a-port", raising=False)
    sp.apply_proxy_env()
    assert "ALL_PROXY" not in os.environ
    assert any("SOCKS_PORT invalid" in w for w in sp._env_warnings)


def test_scheme_pasted_in_host_is_stripped(socks_on):
    """A 'socks5://proxy' pasted into the HOST field must not nest."""
    socks_on.setattr(config, "SOCKS_HOST", "socks5://proxy.example", raising=False)
    sp.apply_proxy_env()
    assert os.environ["ALL_PROXY"].count("://") == 1
    assert "proxy.example:1080" in os.environ["ALL_PROXY"]


def test_status_reports_system_proxy_when_socks_off(monkeypatch):
    """Windows scout F1: SOCKS off scrubs env, but requests+httpx fall
    through to urllib.getproxies() (WinINET registry / shell HTTP_PROXY).
    The strip must surface that instead of claiming 'direct'."""
    monkeypatch.setattr(config, "SOCKS_ENABLED", False, raising=False)
    monkeypatch.setattr("urllib.request.getproxies",
                        lambda: {"https": "http://corp-proxy:8080"})
    st = sp.proxy_status()
    assert st["system_proxy"] == "http://corp-proxy:8080"


def test_cache_clear_reprobes_when_enabled(socks_on, monkeypatch):
    """Wave 2: boot-probe warnings were wiped by the first settings save
    and never returned — a settings change must re-probe."""
    probed = []
    monkeypatch.setattr(sp, "start_boot_probe", lambda: probed.append(1))
    sp.clear_session_cache()
    assert probed == [1]


def test_auth_latch_resets_on_cache_clear(socks_on, monkeypatch):
    """H5: get_session memoizes the AUTH CHECK (a latch), never the
    session object — caching the session raced net._invalidate and
    pinned a pool through a disabled proxy."""
    calls = []
    monkeypatch.setattr(sp, "_test_socks_auth",
                        lambda *a, **k: calls.append(1) or True)
    monkeypatch.setattr(sp, "_auth_checked", False)
    s1 = sp.get_session()
    s2 = sp.get_session()
    assert len(calls) == 1 and s1 is s2       # latched, pooled
    sp.clear_session_cache()
    sp.get_session()
    assert len(calls) == 2                    # re-checked exactly once
