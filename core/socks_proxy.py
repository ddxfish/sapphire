"""
SOCKS5 Proxy Session Factory
Simple core feature for routing HTTP requests through SOCKS5 proxy
"""

import logging
import requests
import config
from core.setup import get_socks_credentials, CONFIG_DIR

logger = logging.getLogger(__name__)

_cached_session = None


class SocksAuthError(Exception):
    """Raised when SOCKS5 authentication fails"""
    pass


def clear_session_cache():
    """Clear cached session - useful when headers change"""
    global _cached_session
    _cached_session = None
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

        proxy_url = f"socks5://{username}:{password}@{config.SOCKS_HOST}:{config.SOCKS_PORT}"

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