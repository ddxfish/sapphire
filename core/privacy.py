# core/privacy.py
"""
Privacy Mode - Endpoint validation and privacy state management.

When privacy mode is enabled:
- Only local LLM providers are used
- Only local tools are allowed
- Chat history is not persisted to disk
- All network calls are validated against the whitelist
"""

import ipaddress
import socket
import logging
from urllib.parse import urlparse
from typing import Optional

logger = logging.getLogger(__name__)

# Cache for hostname resolution (cleared when whitelist changes)
_hostname_cache = {}


def is_privacy_mode() -> bool:
    """Check if privacy mode is currently enabled."""
    from core.settings_manager import settings
    return settings.get('PRIVACY_MODE', False)


def set_privacy_mode(enabled: bool) -> bool:
    """
    Set privacy mode state (runtime only, not persisted).

    Returns True if state changed, False if already in requested state.
    """
    from core.settings_manager import settings
    current = settings.get('PRIVACY_MODE', False)
    if current == enabled:
        return False

    settings.set('PRIVACY_MODE', enabled, persist=False)
    logger.info(f"Privacy mode {'enabled' if enabled else 'disabled'}")

    # Clear hostname cache when toggling
    _hostname_cache.clear()

    return True


def get_whitelist() -> list:
    """Get the current privacy network whitelist."""
    from core.settings_manager import settings
    return settings.get('PRIVACY_NETWORK_WHITELIST', [
        '127.0.0.1',
        'localhost',
        '192.168.0.0/16',
        '10.0.0.0/8',
        '172.16.0.0/12'
    ])


def _resolve_hostname(hostname: str) -> Optional[str]:
    """
    Resolve hostname to IP address, with caching.
    Returns None if resolution fails.
    """
    if hostname in _hostname_cache:
        return _hostname_cache[hostname]

    try:
        ip = socket.gethostbyname(hostname)
        _hostname_cache[hostname] = ip
        logger.debug(f"Resolved '{hostname}' to {ip}")
        return ip
    except socket.gaierror as e:
        logger.warning(f"Failed to resolve hostname '{hostname}': {e}")
        _hostname_cache[hostname] = None
        return None


def _is_ip_in_whitelist(ip_str: str, whitelist: list) -> bool:
    """Check if an IP address is in the whitelist (supports CIDR notation)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        logger.warning(f"Invalid IP address: {ip_str}")
        return False

    for entry in whitelist:
        # Skip hostname entries (not CIDR)
        if not any(c in entry for c in './:'):
            continue

        try:
            # Try as network (CIDR)
            if '/' in entry:
                network = ipaddress.ip_network(entry, strict=False)
                if ip in network:
                    return True
            else:
                # Try as single IP
                if ip == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            # Not a valid IP/CIDR, might be a hostname - skip
            continue

    return False


def is_allowed_endpoint(url_or_ip: str) -> bool:
    """
    Check if an endpoint (URL or IP) is allowed under privacy mode.

    Always returns True if privacy mode is disabled.

    Args:
        url_or_ip: A URL (http://...), hostname, or IP address

    Returns:
        True if endpoint is allowed, False if blocked
    """
    if not is_privacy_mode():
        return True

    whitelist = get_whitelist()

    # Parse URL if provided
    if '://' in url_or_ip:
        try:
            parsed = urlparse(url_or_ip)
            host = parsed.hostname or ''
        except Exception as e:
            logger.warning(f"Failed to parse URL '{url_or_ip}': {e}")
            return False
    else:
        host = url_or_ip

    if not host:
        logger.warning(f"No host found in endpoint: {url_or_ip}")
        return False

    # Check if host is directly in whitelist (as hostname)
    host_lower = host.lower()
    if host_lower in [w.lower() for w in whitelist if not '/' in w]:
        logger.debug(f"Endpoint '{host}' allowed (hostname match)")
        return True

    # Try to parse as IP directly
    try:
        if _is_ip_in_whitelist(host, whitelist):
            logger.debug(f"Endpoint '{host}' allowed (IP match)")
            return True
    except ValueError:
        pass

    # Resolve hostname to IP and check
    resolved_ip = _resolve_hostname(host)
    if resolved_ip and _is_ip_in_whitelist(resolved_ip, whitelist):
        logger.debug(f"Endpoint '{host}' ({resolved_ip}) allowed (resolved IP match)")
        return True

    logger.info(f"Endpoint '{host}' blocked by privacy mode")
    return False


def get_privacy_status() -> dict:
    """Get current privacy mode status for API/UI."""
    return {
        'enabled': is_privacy_mode(),
        'whitelist': get_whitelist()
    }
