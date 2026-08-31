"""
SOCKS5 Proxy Session Factory
Simple core feature for routing HTTP requests through SOCKS5 proxy
"""

import logging
import os
import sys
import threading
from urllib.parse import quote, urlsplit

import requests
import config
from core.setup import get_socks_credentials, CONFIG_DIR

logger = logging.getLogger(__name__)

_cached_session = None


class SocksAuthError(Exception):
    """Raised when SOCKS5 authentication fails"""
    pass


def clear_session_cache():
    """Clear cached session AND re-derive process proxy env + run registered
    invalidators (httpx pools etc.). This is the one choke point every SOCKS
    settings change already calls (the N2 sites), so env + pools follow the
    session cache for free. SOCKS-for-all, 2026-08-31."""
    global _cached_session
    _cached_session = None
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
    global _cached_session

    if _cached_session:
        return _cached_session

    session = requests.Session()

    if config.SOCKS_ENABLED:
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

        # socks5h — DNS resolves THROUGH the proxy. Plain socks5:// leaked every
        # hostname to local DNS while the payload rode the tunnel (fork-4 scope,
        # 2026-08-31). Creds URL-quoted: an @ or : in a password broke the parse.
        proxy_url = (f"socks5h://{quote(username, safe='')}:{quote(password, safe='')}"
                     f"@{config.SOCKS_HOST}:{config.SOCKS_PORT}")

        session.proxies = {
            'http': proxy_url,
            'https': proxy_url
        }

        logger.info(f"SOCKS5 enabled: {config.SOCKS_HOST}:{config.SOCKS_PORT}")
    else:
        logger.info("SOCKS5 disabled, using direct connection")
    
    # Realistic Chrome headers to avoid bot detection
    session.headers.update({
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
        'Cache-Control': 'max-age=0'
    })
    
    _cached_session = session
    return session

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
        host = urlsplit(cfg.get('base_url') or '').hostname
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
    route_llm = bool(getattr(config, 'SOCKS_ROUTE_LLM', True))
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
    Called at boot (sapphire.py, before anything spawns or dials out) and on
    every SOCKS settings change via clear_session_cache."""
    _env_warnings.clear()
    _drop_network_load_errors()
    if not getattr(config, 'SOCKS_ENABLED', False):
        for var in _PROXY_VARS + ('NO_PROXY',):
            os.environ.pop(var, None)
            os.environ.pop(var.lower(), None)
        return
    username, password = get_socks_credentials()
    auth = ''
    if username or password:
        auth = f"{quote(username or '', safe='')}:{quote(password or '', safe='')}@"
    else:
        _env_warnings.append(
            "SOCKS enabled but credentials are missing — proxied requests will "
            "fail until set (traffic never falls back to direct)")
    proxy = f"socks5h://{auth}{config.SOCKS_HOST}:{config.SOCKS_PORT}"
    no_proxy = build_no_proxy()
    for var in _PROXY_VARS:
        os.environ[var] = proxy
        os.environ[var.lower()] = proxy
    os.environ['NO_PROXY'] = no_proxy
    os.environ['no_proxy'] = no_proxy
    try:
        import socksio  # noqa: F401 — httpx's SOCKS backend
    except ImportError:
        _env_warnings.append(
            "httpx[socks] not installed — LLM/cloud requests will fail while "
            "the proxy is on. Fix: pip install 'httpx[socks]'")
    logger.info(f"Proxy env applied: socks5h://{config.SOCKS_HOST}:"
                f"{config.SOCKS_PORT} (DNS via proxy) · NO_PROXY={no_proxy}")
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
                and getattr(config, 'SOCKS_ROUTE_LLM', True)):
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
    return {
        'enabled': enabled,
        'route_llm': bool(getattr(config, 'SOCKS_ROUTE_LLM', True)),
        'dns_via_proxy': enabled,
        'env_applied': bool(os.environ.get('ALL_PROXY')),
        'httpx_socks': httpx_socks,
        'no_proxy': [e for e in (os.environ.get('NO_PROXY') or '').split(',') if e],
        'warnings': list(_env_warnings),
    }
