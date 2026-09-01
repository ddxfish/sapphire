"""net facade tests (2026-09-01) — the one proxy decision point.

Born from the Prime blinds outage: LAN-bound plugin calls rode the SOCKS
proxy (a datacenter proxy can't dial into the user's LAN). core/net.py is
the fix's exact lane; these tests are deliberately paranoid because
classify() is load-bearing for privacy — a WAN host misjudged as LAN
exits the user's real IP silently. Plan: tmp/net-facade-plan.md.
"""
import re
from pathlib import Path

import pytest

from core import net
from core import socks_proxy as sp

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_state():
    """Sessions + registry are module globals — leave no residue."""
    providers = list(sp._direct_host_providers)
    net._invalidate()
    yield
    sp._direct_host_providers[:] = providers
    net._invalidate()


# ── classify: the load-bearing 20 lines ─────────────────────────────────

LAN_HOSTS = [
    'sapphire-pi',            # single-label = LAN (Krem's ruling 2026-09-01)
    'SAPPHIRE-PI',            # case-insensitive
    'nas.',                   # trailing dot stripped -> single-label
    'localhost',
    '127.0.0.1',
    '192.168.1.5',
    '10.0.0.5',
    '172.16.0.1',
    '172.31.255.255',         # RFC1918 upper edge
    '169.254.1.1',            # link-local
    '::1',
    '[::1]',                  # bracketed v6
    'fe80::1',                # v6 link-local
    'homeassistant.local',
    'printer.lan',
    'box.home.arpa',
]

WAN_HOSTS = [
    'api.example.com',
    'example.com',
    '8.8.8.8',
    '1.1.1.1',
    '172.32.0.1',             # just OUTSIDE RFC1918 — the off-by-one trap
    '192.169.0.1',            # looks LAN-ish, is not
    'sub.deep.domain.io',
    'notlocal.localx',        # suffix near-miss
    'fe80::zz',               # invalid v6, has colon — not single-label
    '2001:4860:4860::8888',   # public v6
]


@pytest.mark.parametrize('host', LAN_HOSTS)
def test_classify_lan(host):
    assert net.classify(host) == 'lan', host


@pytest.mark.parametrize('host', WAN_HOSTS)
def test_classify_wan(host):
    assert net.classify(host) == 'wan', host


def test_classify_empty_fails_toward_proxy():
    """Unknown shape must land on the proxy lane — fail-closed."""
    assert net.classify('') == 'wan'
    assert net.classify(None) == 'wan'


def test_classify_registry_hit():
    fn = lambda: {'ha.mydomain.com'}          # FQDN-but-LAN (split DNS)
    sp.register_direct_hosts(fn)
    assert net.classify('ha.mydomain.com') == 'lan'
    assert net.classify('other.mydomain.com') == 'wan'


def test_classify_never_resolves_dns(monkeypatch):
    """The socks5h promise: no hostname ever touches the local resolver."""
    import socket
    def boom(*a, **k):
        raise AssertionError('classify resolved DNS')
    monkeypatch.setattr(socket, 'getaddrinfo', boom)
    monkeypatch.setattr(socket, 'gethostbyname', boom)
    for host in LAN_HOSTS + WAN_HOSTS:
        net.classify(host)


# ── lanes: session properties ───────────────────────────────────────────

def test_lan_session_ignores_proxy_env():
    s = net.session_for('http://192.168.1.2:8090/x')
    assert s.trust_env is False

def test_wan_session_honors_proxy_env():
    s = net.session_for('https://api.example.com/x')
    assert s.trust_env is True

def test_sessions_pooled_and_lane_separated():
    a = net.session_for('http://sapphire-pi:8090/')
    b = net.session_for('http://192.168.1.2/')
    c = net.session_for('https://api.example.com/')
    assert a is b            # same lane+profile -> same pooled session
    assert a is not c

def test_browser_profile_headers():
    b = net.session_for('https://example.com/', profile='browser')
    p = net.session_for('https://example.com/')
    assert 'Chrome' in b.headers['User-Agent']
    assert 'Sec-Ch-Ua' in b.headers
    assert 'Sec-Ch-Ua' not in p.headers      # LAN gear gets no masquerade

def test_invalidator_drops_sessions():
    a = net.session_for('https://example.com/')
    net._invalidate()
    assert net.session_for('https://example.com/') is not a

def test_invalidator_registered_on_chokepoint():
    assert net._invalidate in sp._invalidators


# ── redirect rule: LAN 30x must not hop off-proxy ───────────────────────

class _Recorder:
    def __init__(self):
        self.kw = None
    def request(self, method, url, **kw):
        self.kw = kw
        return 'resp'


def test_lan_redirects_refused_by_default(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(net, 'session_for', lambda url, profile='plain': rec)
    net.get('http://sapphire-pi:8090/audio/speak')
    assert rec.kw['allow_redirects'] is False

def test_wan_redirects_untouched(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(net, 'session_for', lambda url, profile='plain': rec)
    net.get('https://api.example.com/x')
    assert 'allow_redirects' not in rec.kw

def test_lan_redirect_opt_in(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(net, 'session_for', lambda url, profile='plain': rec)
    net.get('http://sapphire-pi:8090/x', allow_redirects=True)
    assert rec.kw['allow_redirects'] is True


# ── guard rail: no new raw requests callers in first-party code ─────────

# Allowlist — phase-2 WAN migration + C fold + alias sweep landed
# 2026-09-01, so this is down to the irreducible three. user/plugins is
# NOT scanned (third-party band rides the env belt by design).
_ALLOWED = {
    'core/net.py',                            # the facade itself
    'plugins/remembrance/tests/test_ops.py',  # test double
    'plugins/email/daemon.py',                # out-of-process daemon — can't
                                              # import core; env belt lane
}
_CALL = re.compile(r'\brequests\.(get|post|put|patch|delete|head|request|Session)\s*\(')
_ALIAS = re.compile(r'\bimport requests as (\w+)')
_VERBS = r'\.(get|post|put|patch|delete|head|request|Session)\s*\('


def test_no_raw_requests_outside_allowlist():
    """New first-party HTTP callers must go through core.net (or be added
    here consciously). This is what keeps the Prime-blinds class from
    quietly returning with the next plugin. Aliased imports (`import
    requests as req`) are hunted too — the original sweep missed six of
    those in routes/plugins.py, including HA test routes (the exact
    blinds class, hiding in an alias)."""
    offenders = []
    for band in ('core', 'functions', 'plugins'):
        for py in (ROOT / band).rglob('*.py'):
            if '__pycache__' in py.parts:
                continue
            rel = py.relative_to(ROOT).as_posix()
            if rel in _ALLOWED:
                continue
            try:
                text = py.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            hit = bool(_CALL.search(text))
            if not hit:
                for alias in set(_ALIAS.findall(text)):
                    if re.search(r'\b' + re.escape(alias) + _VERBS, text):
                        hit = True
                        break
            if hit:
                offenders.append(rel)
    assert not offenders, (
        f'raw requests.* calls outside core.net in: {offenders} — '
        f'use `from core import net` (tmp/net-facade-plan.md) or extend '
        f'the allowlist consciously')
