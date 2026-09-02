"""One proxy decision point — the net facade (2026-09-01).

Born from the Prime blinds outage: SOCKS-for-all env stamping sent
body/Home-Assistant calls to a datacenter proxy that can never dial into
the user's LAN (~2m41s per doomed call). NO_PROXY is a per-library
dialect (requests does CIDR, httpx/urllib don't), so the direct-vs-proxy
decision moves HERE, into Python, decided once per request:

    from core import net
    r = net.get(url, timeout=10)          # requests-shaped, drop-in
    s = net.session_for(url)              # pooled session for loops

Lanes (fail-closed both ways — errors NEVER fall back across lanes):
- 'lan'  -> direct session (trust_env=False; no proxy, no .netrc, env
            CA overrides ignored — LAN gear is plain HTTP/self-signed).
- 'wan'  -> env-honoring session: proxied while SOCKS is on (a dead
            proxy fails loudly), plain direct when SOCKS is off.

classify() is deliberately syntactic + registry — it NEVER resolves DNS
(resolving would leak every hostname to the local resolver, breaking the
socks5h promise, and would stall on dead DNS). Unknown shapes fail
toward 'wan': the proxy lane is the safe default.

Chaos-scout hardening (2026-09-01 hunt): dotless numeric/hex hosts
('16843009' is 1.1.1.1 — glibc parses it with zero DNS) are decoded as
IPv4 literals BEFORE the single-label rule so public IPs in disguise
ride the proxy; unicode dot lookalikes count as dots (wan); zero-padded
dotted quads ('192.168.001.005') are normalized; malformed URLs raise
requests.exceptions.InvalidURL like the library this facade fronts.

LAN redirects are refused by default (allow_redirects=False): a LAN
device's 30x pointing at a WAN URL must not hop off-proxy silently. The
caller sees the 30x response; pass allow_redirects=True to opt in.
(Krem's rulings 2026-09-01: single-label = LAN, redirects off, all
RFC1918 in the belt. Plan: tmp/net-facade-plan.md)

The env belt (socks_proxy.apply_proxy_env) stays in force for raw
`requests` callers that never adopt this facade — third-party plugins
lose nothing. This module is the exact lane; the belt is the floor.
"""
import http.cookiejar
import ipaddress
import logging
import re
import threading
from urllib.parse import urlsplit

import requests

from core import socks_proxy

logger = logging.getLogger(__name__)

_LAN_SUFFIXES = ('.lan', '.home.arpa')   # .local/localhost live in _host_is_lan
_UNICODE_DOTS = ('。', '．', '｡')   # 。 ． ｡ — IDNA maps to '.'
_PADDED_V4 = re.compile(r'\d{1,3}(\.\d{1,3}){3}')


def _dotless_ip(h: str):
    """Decode a dotless IPv4 literal ('16843009', '0x08080808', octal
    '017700000001') the way inet_aton does — WITHOUT touching DNS.
    Returns an IPv4Address or None."""
    try:
        if h.startswith('0x'):
            val = int(h, 16)
        elif h.isdigit():
            # inet_aton treats a leading zero as octal; match it so the
            # lane matches where the socket actually connects.
            val = int(h, 8) if (len(h) > 1 and h[0] == '0') else int(h)
        else:
            return None
        if 0 <= val <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(val)
    except ValueError:
        pass
    return None


def classify(host) -> str:
    """'lan' (remote proxy can't reach it -> direct) or 'wan' (proxy env).

    Rules, in order: loopback/RFC1918/link-local literal IPs, *.local and
    localhost (via socks_proxy._host_is_lan), *.lan / *.home.arpa,
    single-label hostnames (no dot, e.g. 'sapphire-pi' — home-LAN
    convention; dotless NUMERIC forms are decoded as IPv4 first),
    registered direct hosts (socks_proxy.register_direct_hosts),
    zero-padded dotted quads. Everything else — every real FQDN — is
    'wan'. Non-string/empty input fails toward 'wan'.
    """
    if not isinstance(host, str) or not host:
        return 'wan'
    h = host.strip('[]').lower().rstrip('.')
    for ud in _UNICODE_DOTS:
        h = h.replace(ud, '.')
    if not h:
        return 'wan'
    if socks_proxy._host_is_lan(h):
        return 'lan'
    if h.endswith(_LAN_SUFFIXES):
        return 'lan'
    if '.' not in h and ':' not in h:
        ip = _dotless_ip(h)
        if ip is not None:
            return 'lan' if ip.is_private else 'wan'
        return 'lan'
    if h in socks_proxy.direct_hosts():
        return 'lan'
    if _PADDED_V4.fullmatch(h):
        try:
            ip = ipaddress.ip_address(
                '.'.join(str(int(o)) for o in h.split('.')))
            return 'lan' if ip.is_private else 'wan'
        except ValueError:
            pass
    return 'wan'


def _lane_for_url(url) -> str:
    """Lane for a full URL. Malformed URLs raise requests' InvalidURL
    (a RequestException AND a ValueError) so existing except-clauses
    written for the requests lane keep working."""
    try:
        return classify(urlsplit(url).hostname or '')
    except (ValueError, AttributeError, TypeError) as e:
        raise requests.exceptions.InvalidURL(
            f'net: cannot parse URL {url!r}: {e}') from e


# Chrome-ish headers for the 'browser' profile (bot-evasion for web
# tools — moved from socks_proxy.get_session). LAN gear and APIs get the
# 'plain' profile: no masquerade noise.
_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Cache-Control': 'max-age=0',
}

_lock = threading.Lock()
_sessions = {}          # (lane, profile) -> requests.Session

# Cookie policy (longevity scout: the C fold left ONE process-wide jar with
# no cap and no reset — every site the web tools ever touch deposited
# cookies forever, ~4.4us/domain/request under a process-global lock, plus a
# hostile page could poison it for the life of the process). API callers
# (plain profile: github/store/gcal/wordpress/updater/geonames/embeddings)
# need no cookies at all; web tools (browser profile) keep a BOUNDED jar.
_BROWSER_COOKIE_CAP = 400


class _BlockAllCookies(http.cookiejar.DefaultCookiePolicy):
    """Refuse every Set-Cookie — the API lane is stateless."""
    def set_ok(self, cookie, request):
        return False


class _CappedCookies(http.cookiejar.DefaultCookiePolicy):
    """Stop storing new cookies once the jar hits the cap (existing cookies
    still update). Bounds the per-request O(n) jar scan + poisoning blast."""
    def __init__(self, jar, cap):
        super().__init__()
        self._jar = jar
        self._cap = cap
    def set_ok(self, cookie, request):
        if not super().set_ok(cookie, request):
            return False
        # Allow updates to an already-known (domain,name); block growth past cap.
        if len(self._jar) >= self._cap:
            existing = self._jar._cookies.get(cookie.domain, {}).get(cookie.path, {})
            return cookie.name in existing
        return True


def _make_session(lane: str, profile: str) -> requests.Session:
    s = requests.Session()
    if lane == 'lan':
        s.trust_env = False       # no env proxies, no .netrc, no env CA
    if profile == 'browser':
        s.headers.update(_BROWSER_HEADERS)
        s.cookies.set_policy(_CappedCookies(s.cookies, _BROWSER_COOKIE_CAP))
    else:
        s.cookies.set_policy(_BlockAllCookies())
    return s


def _pooled(lane: str, profile: str) -> requests.Session:
    key = (lane, profile)
    with _lock:
        s = _sessions.get(key)
        if s is None:
            s = _sessions[key] = _make_session(lane, profile)
    return s


def session_for(url: str, profile: str = 'plain') -> requests.Session:
    """Pooled session for the lane this URL classifies into. For callers
    that loop (polling etc.). NOTE: a session is lane-fixed — don't reuse
    one across differently-classified URLs; call again per URL."""
    return _pooled(_lane_for_url(url), profile)


def wan_session(profile: str = 'browser') -> requests.Session:
    """The WAN lane's pooled session with no URL in hand — the
    get_session() fold (C, 2026-09-01): web tools grab one session and
    make many calls. Env does the proxying; browser profile by default
    (that's what get_session always was)."""
    return _pooled('wan', profile)


def request(method: str, url: str, profile: str = 'plain', **kw) -> requests.Response:
    """requests.request drop-in with the lane decision applied — ONCE
    (a single classify feeds both the redirect rule and the session
    pick; the old double-classify opened a TOCTOU where a provider
    flake could hand out a LAN session with redirects enabled).
    Raises the normal requests exceptions."""
    lane = _lane_for_url(url)
    if lane == 'lan':
        kw.setdefault('allow_redirects', False)
    return _pooled(lane, profile).request(method, url, **kw)


def get(url: str, **kw) -> requests.Response:
    return request('GET', url, **kw)


def post(url: str, **kw) -> requests.Response:
    return request('POST', url, **kw)


def put(url: str, **kw) -> requests.Response:
    return request('PUT', url, **kw)


def delete(url: str, **kw) -> requests.Response:
    return request('DELETE', url, **kw)


def _invalidate():
    """Drop pooled sessions on any proxy-config change (registered on the
    socks_proxy invalidator chokepoint — same lifecycle as httpx pools)."""
    with _lock:
        _sessions.clear()


socks_proxy.register_invalidator(_invalidate)
