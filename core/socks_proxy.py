"""
SOCKS5 Proxy Session Factory
Simple core feature for routing HTTP requests through SOCKS5 proxy
"""

import os
import logging
from pathlib import Path
import requests
import config

logger = logging.getLogger(__name__)

_cached_session = None

def clear_session_cache():
    """Clear cached session - useful when headers change"""
    global _cached_session
    _cached_session = None
    logger.info("Session cache cleared")

def _load_socks_credentials():
    """
    Load SOCKS5 credentials with priority:
    1. Environment variables (SAPPHIRE_SOCKS_USERNAME, SAPPHIRE_SOCKS_PASSWORD)
    2. Project file: user/.socks_config
    3. Home file: ~/.sapphire/.socks_config
    
    Returns (username, password) or (None, None) if not found.
    """
    # Try env vars first (production/deployment)
    username = os.environ.get('SAPPHIRE_SOCKS_USERNAME')
    password = os.environ.get('SAPPHIRE_SOCKS_PASSWORD')
    
    if username and password:
        logger.info("Using SOCKS credentials from environment variables")
        return username, password
    
    # Try project-local file (dev convenience)
    project_config = Path(__file__).parent.parent / 'user' / '.socks_config'
    if project_config.exists():
        try:
            with open(project_config, 'r') as f:
                lines = f.readlines()
            if len(lines) >= 2:
                username = lines[0].strip()
                password = lines[1].strip()
                if username and password:
                    logger.info(f"Using SOCKS credentials from {project_config}")
                    return username, password
        except Exception as e:
            logger.debug(f"Failed to read {project_config}: {e}")
    
    # Try home directory file (legacy)
    home_config = Path.home() / '.sapphire' / '.socks_config'
    if home_config.exists():
        try:
            with open(home_config, 'r') as f:
                lines = f.readlines()
            if len(lines) >= 2:
                username = lines[0].strip()
                password = lines[1].strip()
                if username and password:
                    logger.info(f"Using SOCKS credentials from {home_config}")
                    return username, password
        except Exception as e:
            logger.debug(f"Failed to read {home_config}: {e}")
    
    # Not found - that's fine, will use direct connection
    return None, None

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
        username, password = _load_socks_credentials()
        
        if not username or not password:
            raise ValueError(
                "SOCKS5 is enabled in config but credentials not found. "
                "Set SAPPHIRE_SOCKS_USERNAME and SAPPHIRE_SOCKS_PASSWORD environment variables, "
                "or create user/.socks_config or ~/.sapphire/.socks_config with credentials"
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
        # Accept-Encoding removed - requests library handles compression automatically
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