# functions/network.py
# Utilitarian network tools - all SOCKS-compatible

import time
import logging
import requests
from core.socks_proxy import get_session
import config

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📡'

AVAILABLE_FUNCTIONS = [
    'get_external_ip',
    'check_internet',
    'website_status',
]

TOOLS = [
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "get_external_ip",
            "description": "Get your external/public IP address as seen by the internet",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "check_internet",
            "description": "Check if internet connection is working",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "network": True,
        "is_local": False,
        "function": {
            "name": "website_status",
            "description": "Check if a website is up and responding",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Website URL to check (e.g., reddit.com)"
                    }
                },
                "required": ["url"]
            }
        }
    }
]


def _get_external_ip() -> tuple:
    """Get external IP via SOCKS proxy if enabled."""
    services = [
        'https://icanhazip.com',
        'https://api.ipify.org',
        'https://ifconfig.me/ip',
    ]
    
    try:
        session = get_session()
    except ValueError as e:
        return f"Network unavailable: {e}", False
    
    for url in services:
        try:
            response = session.get(url, timeout=5)
            if response.status_code == 200:
                ip = response.text.strip()
                proxy_note = " (via SOCKS proxy)" if config.SOCKS_ENABLED else ""
                return f"External IP: {ip}{proxy_note}", True
        except requests.exceptions.ProxyError:
            return "Failed: SOCKS proxy error. Proxy may be down or credentials invalid.", False
        except requests.exceptions.ConnectionError:
            continue
        except Exception as e:
            logger.warning(f"[NET] {url} failed: {e}")
            continue
    
    return "Could not determine external IP - all services unreachable", False


def _check_internet() -> tuple:
    """Quick connectivity check with latency."""
    targets = [
        ('https://www.google.com', 'Google'),
        ('https://www.cloudflare.com', 'Cloudflare'),
        ('https://www.amazon.com', 'Amazon'),
    ]
    
    try:
        session = get_session()
    except ValueError as e:
        return f"Network unavailable: {e}", False
    
    for url, name in targets:
        try:
            start = time.time()
            response = session.head(url, timeout=5)
            latency = int((time.time() - start) * 1000)
            
            if response.status_code < 400:
                proxy_note = " via SOCKS proxy" if config.SOCKS_ENABLED else ""
                return f"Internet is UP. Reached {name} in {latency}ms{proxy_note}.", True
        except requests.exceptions.ProxyError:
            return "Failed: SOCKS proxy error. Proxy may be down or credentials invalid.", False
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            continue
        except Exception as e:
            logger.warning(f"[NET] check_internet {name} failed: {e}")
            continue
    
    return "Internet appears DOWN. Could not reach Google, Cloudflare, or Amazon.", False


def _website_status(url: str) -> tuple:
    """Check if a specific website is responding."""
    if not url:
        return "I need a URL to check.", False
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        session = get_session()
    except ValueError as e:
        return f"Network unavailable: {e}", False
    
    try:
        start = time.time()
        response = session.head(url, timeout=10, allow_redirects=True)
        latency = int((time.time() - start) * 1000)
        
        if response.status_code < 400:
            return f"{url} is UP (HTTP {response.status_code}, {latency}ms)", True
        else:
            return f"{url} returned HTTP {response.status_code} ({latency}ms)", True
            
    except requests.exceptions.ProxyError:
        return "Failed: SOCKS proxy error. Proxy may be down or credentials invalid.", False
    except requests.exceptions.SSLError:
        return f"{url} has SSL certificate problems", False
    except requests.exceptions.ConnectionError:
        return f"{url} is DOWN or unreachable", True
    except requests.exceptions.Timeout:
        return f"{url} timed out (>10s)", True
    except Exception as e:
        logger.error(f"[NET] website_status error: {e}")
        return f"Error checking {url}: {e}", False


def execute(function_name: str, arguments: dict, config) -> tuple:
    """Execute network function. Returns (result_string, success_bool)."""
    try:
        if function_name == "get_external_ip":
            return _get_external_ip()
        elif function_name == "check_internet":
            return _check_internet()
        elif function_name == "website_status":
            return _website_status(arguments.get("url", ""))
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"[NET] {function_name} error: {e}")
        return f"Network error: {e}", False