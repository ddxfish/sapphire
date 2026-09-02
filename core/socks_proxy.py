"""
SOCKS5 Proxy Session Factory
Simple core feature for routing HTTP requests through SOCKS5 proxy
"""

import logging
import os
import sys
import threading
import time
from urllib.parse import quote, urlsplit

import config
from core.setup import get_socks_credentials, CONFIG_DIR

logger = logging.getLogger(__name__)

_auth_checked = False   # SOCKS auth pre-flight latch (C fold; reset on settings change)
_env_lock = threading.Lock()   # apply_proxy_env is called from many threads (H6)


class SocksAuthError(Exception):
    """Raised when SOCKS5 authentication fails"""
    pass


def clear_session_cache():
    """Clear cached session AND re-derive process proxy env + run registered
    invalidators (httpx pools etc.). This is the one choke point every SOCKS
    settings change already calls (the N2 sites), so env + pools follow the
    session cache for free. SOCKS-for-all, 2026-08-31."""
    global _auth_checked
    _auth_checked = False
    _dh_cache_invalidate()
    try:
        apply_proxy_env()
    except Exception as e:
        logger.error(f"apply_proxy_env failed during cache clear: {e}")
    for fn in list(_invalidators):
        try:
            fn()
        except Exception as e:
            logger.warning(f"proxy invalidator {getattr(fn, '__name__', fn)} failed: {e}")
    logger.info("Session cache cleared")


def _test_socks_auth(host: str, port: int, username: str, password: str, timeout: float = 10.0) -> bool:
    """
    Quick SOCKS5 auth test. Returns True if auth succeeds, raises SocksAuthError if not.
    Uses short timeout to fail fast on bad credentials.
    """
    try:
        import socks
    except ImportError:
        logger.warning("PySocks not available for auth test, skipping")
        return True
    
    test_sock = socks.socksocket()
    test_sock.set_proxy(socks.SOCKS5, host, port, username=username, password=password)
    test_sock.settimeout(timeout)
    
    try:
        # Connect to a reliable, fast host
        test_sock.connect(('1.1.1.1', 80))
        test_sock.close()
        return True
    except socks.ProxyError as e:
        test_sock.close()
        if 'authentication failed' in str(e).lower():
            raise SocksAuthError(
                "SOCKS5 authentication failed - check username/password in Settings → SOCKS"
            )
        raise SocksAuthError(f"SOCKS5 proxy error: {e}")
    except Exception as e:
        test_sock.close()
        raise SocksAuthError(f"SOCKS5 connection failed: {type(e).__name__}: {e}")


def get_session():
    """
    Get configured requests session.
    Returns SOCKS5 session if enabled, plain session otherwise.
    Caches and reuses session for performance.

    Raises:
        SocksAuthError: If SOCKS5 auth fails after retry
        ValueError: If SOCKS5 enabled but credentials missing
    """
    global _auth_checked

    if config.SOCKS_ENABLED and not _auth_checked:
        username, password = get_socks_credentials()

        if not username or not password:
            raise ValueError(
                "SOCKS5 is enabled but credentials not found. "
                "Set them in Settings → SOCKS, or use environment variables "
                "SAPPHIRE_SOCKS_USERNAME and SAPPHIRE_SOCKS_PASSWORD"
            )

        logger.info(f"Testing SOCKS5 auth to {config.SOCKS_HOST}:{config.SOCKS_PORT}")

        # Test auth with one retry for transient proxy failures
        timeout = getattr(config, 'SOCKS_TIMEOUT', 10.0)
        try:
            _test_socks_auth(config.SOCKS_HOST, config.SOCKS_PORT, username, password, timeout)
        except SocksAuthError as e:
            import time
            logger.warning(f"SOCKS5 auth failed, retrying in 2s: {e}")
            time.sleep(2)
            _test_socks_auth(config.SOCKS_HOST, config.SOCKS_PORT, username, password, timeout)

        logger.info(f"SOCKS5 enabled: {config.SOCKS_HOST}:{config.SOCKS_PORT}")
        _auth_checked = True

    # C fold (2026-09-01): delegate to core.net's WAN browser session.
    # The process env (apply_proxy_env) is the single proxy source now
    # -- the old explicit session.proxies double-stamp dies here.
    # Chrome headers live in net._BROWSER_HEADERS. The SOCKS auth
    # pre-flight above is preserved (learn-once clear error on bad
    # creds). Plan: tmp/net-facade-plan.md
    # H5 fix: LATCH, not cache — caching the session here raced
    # net._invalidate and pinned a stale pool through a proxy the
    # user had turned OFF. Only the auth pre-flight is memoized.
    from core import net
    return net.wan_session(profile='browser')

# ============================================================================
# SOCKS-for-all: process-wide proxy env (2026-08-31)
# Philosophy: FAIL-CLOSED. When SOCKS is enabled the env is always stamped —
# a dead proxy means loud request failures, never silent direct traffic.
# httpx (all LLM SDK lanes) honors these at client construction (trust_env),
# requests at send, urllib at send. Plugin daemons inherit via dict(os.environ)
# at spawn and pick up changes on their next restart.
# ============================================================================
_PROXY_VARS = ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY")
_invalidators = []        # run on every proxy-config change (clear_session_cache)
_env_warnings = []        # surfaced via proxy_status() + the load_errors toast lane
_llm_hint_sent = set()    # learn-once proxy hint, per provider per boot

# Core SDK providers reach fixed hosts with no base_url in settings — needed
# for the SOCKS_ROUTE_LLM=off exemption list.
_CORE_LLM_HOSTS = {
    'claude': 'api.anthropic.com',
    'openai': 'api.openai.com',
    'gemini': 'generativelanguage.googleapis.com',
}


def register_invalidator(fn):
    """Register a zero-arg callable run whenever proxy config changes.
    openai_compat registers its httpx-pool drop here."""
    if fn not in _invalidators:
        _invalidators.append(fn)


# Direct-host registry (net facade, 2026-09-01): components register a
# zero-arg callable returning hostnames they must reach WITHOUT the proxy
# (LAN gear a remote proxy can't dial — the Prime blinds class). Consumed
# by build_no_proxy() (the env belt for unmigrated raw-requests callers)
# and core.net.classify() (the facade's lane pick). Providers are called
# lazily on every derivation, so they should read live settings each time.
_direct_host_providers = {}   # owner -> zero-arg callable. OWNER-KEYED so a
                              # plugin reload REPLACES its entry instead of
                              # appending a stale closure (+1 per reload,
                              # forever — longevity/day-ruiner find 2026-09-01)
_dh_warned = set()            # owners warned this config cycle
_dh_cache = set()
_dh_cache_ts = 0.0
_DH_TTL = 5.0                 # hot path: classify() consults per WAN request,
                              # and providers read settings FILES each call


def _dh_cache_invalidate():
    global _dh_cache, _dh_cache_ts
    _dh_cache = set()
    _dh_cache_ts = 0.0
    _dh_warned.clear()


def register_direct_hosts(fn, owner=None):
    """Register a zero-arg callable -> iterable of hostname STRINGS that
    must always bypass the proxy. Keyed by `owner` (plugin name): a
    reload replaces, never accumulates. unload_plugin and the refusal
    unwind call unregister_direct_hosts(owner)."""
    _direct_host_providers[owner or getattr(fn, '__module__', repr(fn))] = fn
    _dh_cache_invalidate()


def unregister_direct_hosts(owner):
    """Drop a plugin's provider (unload / refusal-unwind leg)."""
    if _direct_host_providers.pop(owner, None) is not None:
        _dh_cache_invalidate()


def direct_hosts() -> set:
    """Union of all providers' hosts, normalized lowercase, cached _DH_TTL
    seconds. A failing provider is skipped — fail-closed, its hosts ride
    the proxy — and WARNED once per config cycle (silence here caused a
    6-minute outage once). A bare-string return counts as ONE host, never
    iterated into characters (chaos F2: a string provider exploded into
    single-char NO_PROXY entries that bypassed whole TLDs)."""
    global _dh_cache, _dh_cache_ts
    now = time.monotonic()
    if now - _dh_cache_ts < _DH_TTL:
        return _dh_cache
    hosts = set()
    for owner, fn in list(_direct_host_providers.items()):
        try:
            raw = fn() or ()
            if isinstance(raw, (str, bytes)):
                raw = (raw,)
            for h in raw:
                if not isinstance(h, str):
                    if owner not in _dh_warned:
                        _dh_warned.add(owner)
                        logger.warning(f"direct-host provider '{owner}' yielded non-string {h!r} — skipped")
                    continue
                h = h.strip().lower().rstrip('.')
                if h:
                    hosts.add(h)
        except Exception as e:
            if owner not in _dh_warned:
                _dh_warned.add(owner)
                logger.warning(f"direct-host provider '{owner}' failed — its hosts will ride the proxy: {e}")
    _dh_cache = hosts
    _dh_cache_ts = now
    return hosts


def _scheme() -> str:
    """socks5h (DNS via proxy — no local leak) unless the proxy cannot resolve
    names server-side: SOCKS_REMOTE_DNS=false falls back to socks5 (local
    DNS; hostnames visible to this box's resolver, traffic still tunneled).
    Canonical no-remote-DNS proxy: PIA's standalone SOCKS5 — verdict B on
    Prime, 2026-08-31 (0x04 host-unreachable for every hostname request).
    NOTE: the LLM lane (httpx/socksio) ALWAYS sends hostnames to the proxy
    regardless of scheme — on a no-DNS proxy, LLMs need SOCKS_ROUTE_LLM off.
    """
    return 'socks5h' if getattr(config, 'SOCKS_REMOTE_DNS', False) else 'socks5'


def _host_is_lan(host: str) -> bool:
    """Loopback / RFC1918 / mDNS .local — hosts a remote proxy can't reach."""
    if not host:
        return False
    host = host.strip('[]').lower()
    if host == 'localhost' or host.endswith('.local'):
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _llm_hosts(include_cloud: bool) -> set:
    """Hosts of configured LLM providers. LAN hosts are ALWAYS exempt (a
    remote proxy can't dial back into the user's LAN); cloud hosts only when
    the user opted LLM traffic out of the proxy (SOCKS_ROUTE_LLM=false)."""
    hosts = set()
    try:
        from core.settings_manager import settings
        provs = dict(settings.get('LLM_PROVIDERS') or {})
        provs.update(settings.get('LLM_CUSTOM_PROVIDERS') or {})
    except Exception:
        return hosts
    for key, cfg in provs.items():
        if not isinstance(cfg, dict):
            continue
        try:
            host = urlsplit(cfg.get('base_url') or '').hostname
        except ValueError as e:
            # One malformed base_url aborting apply_proxy_env = env never
            # stamped = every lane DIRECT while the UI says SOCKS on
            # (day-ruiner CRIT, 2026-09-01). Skip the bad one, keep going.
            logger.warning(f"LLM provider '{key}' has unparseable base_url — skipped in NO_PROXY: {e}")
            continue
        if host and (include_cloud or _host_is_lan(host)):
            hosts.add(host)
        if include_cloud and key in _CORE_LLM_HOSTS:
            hosts.add(_CORE_LLM_HOSTS[key])
    return hosts


def build_no_proxy() -> str:
    """NO_PROXY value: loopback + LAN LLM endpoints + (all LLM hosts when LLM
    routing is opted out) + user extras (SOCKS_NO_PROXY_EXTRA, for LAN gear
    like Home Assistant that core can't enumerate)."""
    entries = ['localhost', '127.0.0.1', '::1']
    # LAN belt (2026-09-01, the Prime blinds class): a remote proxy can
    # never reach these. CIDRs are honored by the requests lane for
    # IPv4-literal URLs (requests/utils.py should_bypass_proxies); inert
    # but harmless in httpx/urllib. Suffixes match in requests (endswith)
    # AND httpx (wildcard mounts). Registered direct hosts cover LAN gear
    # with real names (e.g. 'sapphire-pi') for unmigrated callers.
    entries += ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                '169.254.0.0/16', '.local', '.lan', '.home.arpa']
    entries += sorted(direct_hosts())
    route_llm = bool(getattr(config, 'SOCKS_ROUTE_LLM', False))
    entries += sorted(_llm_hosts(include_cloud=not route_llm))
    extra = getattr(config, 'SOCKS_NO_PROXY_EXTRA', '') or ''
    entries += [h.strip() for h in str(extra).split(',') if h.strip()]
    seen, out = set(), []
    for e in entries:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return ','.join(out)


def _drop_network_load_errors():
    """Remove this module's stale entries from the boot-toast lane (the list
    is non-destructive since N7 — a fixed proxy would toast its old failure
    forever). sys.modules guard: NEVER trigger the plugin_loader import from
    here — early boot calls simply skip (list doesn't exist yet)."""
    pl_mod = sys.modules.get('core.plugin_loader')
    if pl_mod is None:
        return
    try:
        pl_mod.plugin_loader._load_errors[:] = [
            e for e in pl_mod.plugin_loader._load_errors
            if e.get('plugin') != 'network']
    except Exception:
        pass


def _push_network_load_error(msg: str, hint: str):
    """Append to the boot-toast lane (same sys.modules guard as above)."""
    pl_mod = sys.modules.get('core.plugin_loader')
    if pl_mod is None:
        return
    try:
        pl_mod.plugin_loader._load_errors.append(
            {'plugin': 'network', 'error': msg, 'hint': hint})
    except Exception:
        pass


def apply_proxy_env() -> None:
    """Stamp process-wide proxy env from settings (see module note above).
    Called at boot (sapphire.py, before anything spawns or dials out),
    after plugin scan, and on every SOCKS settings change via
    clear_session_cache. Serialized: six live call sites on different
    threads could interleave a mixed env (H6)."""
    with _env_lock:
        _apply_proxy_env_inner()


def _apply_proxy_env_inner() -> None:
    _env_warnings.clear()
    _drop_network_load_errors()
    if not getattr(config, 'SOCKS_ENABLED', False):
        for var in _PROXY_VARS + ('NO_PROXY',):
            os.environ.pop(var, None)
            os.environ.pop(var.lower(), None)
        return
    try:
        username, password = get_socks_credentials()
    except Exception as e:
        # Fail-closed: unreadable creds -> stamp env WITHOUT auth (the
        # proxy refuses = loud), never abort derivation (= fail-open).
        logger.error(f"get_socks_credentials failed — stamping proxy env without auth: {e}")
        username, password = '', ''
    auth = ''
    if username or password:
        auth = f"{quote(username or '', safe='')}:{quote(password or '', safe='')}@"
    else:
        _env_warnings.append(
            "SOCKS enabled but credentials are missing — proxied requests will "
            "fail until set (traffic never falls back to direct)")
    proxy = f"{_scheme()}://{auth}{config.SOCKS_HOST}:{config.SOCKS_PORT}"
    no_proxy = build_no_proxy()
    # NO_PROXY FIRST: in the stamp window a belt-lane caller hitting LAN
    # gear must see the bypass before it can see a proxy (H6 — reverse
    # order reopened the blinds window for the raw-requests lane).
    os.environ['NO_PROXY'] = no_proxy
    os.environ['no_proxy'] = no_proxy
    for var in _PROXY_VARS:
        os.environ[var] = proxy
        os.environ[var.lower()] = proxy
    try:
        import socksio  # noqa: F401 — httpx's SOCKS backend
    except ImportError:
        _env_warnings.append(
            "httpx[socks] not installed — LLM/cloud requests will fail while "
            "the proxy is on. Fix: pip install 'httpx[socks]'")
    logger.info(f"Proxy env applied: {_scheme()}://{config.SOCKS_HOST}:"
                f"{config.SOCKS_PORT} · NO_PROXY={no_proxy}")
    for w in _env_warnings:
        logger.warning(f"[PROXY] {w}")
        _push_network_load_error(w, "Settings › Network")
        try:
            from core.event_bus import publish, Events
            publish(Events.PLUGIN_NOTICE,
                    {'plugin': 'network', 'message': w, 'severity': 'warning'})
        except Exception:
            pass


def start_boot_probe() -> None:
    """Background reachability probe when SOCKS is on. Call LATE in boot
    (after plugin scan) so the thread can reach the load_errors lane — a dead
    proxy at boot must toast; fail-closed means broken proxy = broken egress
    and the user must KNOW, not discover it tool by tool. Also re-syncs any
    warnings apply_proxy_env collected before the lane existed."""
    if not getattr(config, 'SOCKS_ENABLED', False):
        return

    def _probe():
        _drop_network_load_errors()
        for w in list(_env_warnings):
            _push_network_load_error(w, "Settings › Network")
        try:
            username, password = get_socks_credentials()
            _test_socks_auth(config.SOCKS_HOST, config.SOCKS_PORT,
                             username, password,
                             getattr(config, 'SOCKS_TIMEOUT', 10.0))
            # The auth test dials an IP — it PASSES on a proxy with no
            # server-side DNS while every hostname request dies with 0x04
            # (Prime freeze, 2026-08-31). With remote DNS on, prove a
            # hostname connect too.
            if getattr(config, 'SOCKS_REMOTE_DNS', False):
                import socks as _pysocks
                ts = _pysocks.socksocket()
                try:
                    ts.set_proxy(_pysocks.SOCKS5, config.SOCKS_HOST,
                                 config.SOCKS_PORT, rdns=True,
                                 username=username or None,
                                 password=password or None)
                    ts.settimeout(getattr(config, 'SOCKS_TIMEOUT', 10.0))
                    ts.connect(('api.anthropic.com', 443))
                except Exception as de:
                    msg = (f"SOCKS proxy cannot resolve hostnames server-side "
                           f"({type(de).__name__}: {de}) — most requests will fail. "
                           f"Proxies without remote DNS (e.g. PIA standalone SOCKS) "
                           f"need Settings › Network › 'DNS via proxy' turned OFF.")
                    logger.error(f"[PROXY] {msg}")
                    _env_warnings.append(msg)
                    _push_network_load_error(
                        msg, "Settings › Network › DNS via proxy → off")
                finally:
                    try:
                        ts.close()
                    except Exception:
                        pass
            logger.info("[PROXY] boot probe ok — SOCKS proxy reachable")
        except Exception as e:
            msg = (f"SOCKS proxy unreachable: {e} — proxied traffic will fail "
                   f"until it's back (nothing falls back to direct)")
            logger.error(f"[PROXY] {msg}")
            _env_warnings.append(msg)
            _push_network_load_error(
                msg, "Settings › Network — check host/port/credentials")

    threading.Thread(target=_probe, daemon=True, name='socks-boot-probe').start()


def maybe_llm_proxy_hint(provider_name: str, error_text: str) -> None:
    """Learn-once toast when an LLM request fails while riding the proxy —
    providers commonly 403/block datacenter proxy IPs and nothing else in the
    error says so. One per provider per boot; never raises."""
    try:
        if not (getattr(config, 'SOCKS_ENABLED', False)
                and getattr(config, 'SOCKS_ROUTE_LLM', False)):
            return
        if provider_name in _llm_hint_sent:
            return
        low = (error_text or '').lower()
        if not any(t in low for t in ('403', 'forbidden', 'connect', 'proxy',
                                      'blocked', 'unusual activity')):
            return
        _llm_hint_sent.add(provider_name)
        from core.event_bus import publish, Events
        publish(Events.PLUGIN_NOTICE, {
            'plugin': 'network',
            'message': (f"{provider_name} request failed while routed through the "
                        f"SOCKS proxy — some providers block proxy IPs. "
                        f"Settings › Network › 'Route LLM traffic via proxy' "
                        f"can exempt LLM traffic."),
            'severity': 'warning',
        })
    except Exception:
        pass


def proxy_status() -> dict:
    """Live truth for the Settings › Network trust strip."""
    enabled = bool(getattr(config, 'SOCKS_ENABLED', False))
    try:
        import socksio  # noqa: F401
        httpx_socks = True
    except ImportError:
        httpx_socks = False
    remote_dns = bool(getattr(config, 'SOCKS_REMOTE_DNS', False))
    return {
        'enabled': enabled,
        'route_llm': bool(getattr(config, 'SOCKS_ROUTE_LLM', False)),
        'remote_dns': remote_dns,
        'dns_via_proxy': enabled and remote_dns,
        'env_applied': bool(os.environ.get('ALL_PROXY')),
        'httpx_socks': httpx_socks,
        'no_proxy': [e for e in (os.environ.get('NO_PROXY') or '').split(',') if e],
        'warnings': list(_env_warnings),
    }
