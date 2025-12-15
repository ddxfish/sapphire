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


def clear_session_cache():
    """Clear cached session - useful when headers change"""
    global _cached_session
    _cached_session = None
    logger.info("Session cache cleared")


def get_session():
    """
    Get configured requests session.
    Returns SOCKS5 session if enabled, plain session otherwise.
    Caches and reuses session for performance.
    """
    global _cached_session
    
    if _cached_session:
        return _cached_session
    
    session = requests.Session()
    
    if config.SOCKS_ENABLED:
        username, password = get_socks_credentials()
        
        if not username or not password:
            raise ValueError(
                "SOCKS5 is enabled in config but credentials not found. "
                "Set SAPPHIRE_SOCKS_USERNAME and SAPPHIRE_SOCKS_PASSWORD environment variables, "
                f"or create {CONFIG_DIR / 'socks_config'} with username on line 1, password on line 2"
            )
        
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