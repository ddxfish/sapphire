# functions/network.py
# Cross-platform network utilities

import socket
import logging
import requests

logger = logging.getLogger(__name__)

ENABLED = True

AVAILABLE_FUNCTIONS = [
    'get_external_ip',
    'dns_lookup',
    'check_port',
    'http_headers',
]

TOOLS = [
    {
        "type": "function",
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
        "function": {
            "name": "dns_lookup",
            "description": "Resolve a hostname to IP address(es)",
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {
                        "type": "string",
                        "description": "Hostname to resolve (e.g., google.com)"
                    }
                },
                "required": ["hostname"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_port",
            "description": "Check if a TCP port is open on a host",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Hostname or IP address"
                    },
                    "port": {
                        "type": "integer",
                        "description": "Port number to check"
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Connection timeout in seconds",
                        "default": 5
                    }
                },
                "required": ["host", "port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_headers",
            "description": "Get HTTP response headers from a URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to check (include http:// or https://)"
                    }
                },
                "required": ["url"]
            }
        }
    }
]


def _get_external_ip() -> tuple:
    """Get external IP from icanhazip.com"""
    try:
        # Try multiple services in case one is down
        services = [
            'https://icanhazip.com',
            'https://api.ipify.org',
            'https://ifconfig.me/ip',
        ]
        
        for url in services:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    ip = response.text.strip()
                    return f"External IP: {ip}", True
            except:
                continue
        
        return "Could not determine external IP - all services failed", False
        
    except Exception as e:
        logger.error(f"External IP error: {e}")
        return f"Failed to get external IP: {e}", False


def _dns_lookup(hostname: str) -> tuple:
    """Resolve hostname to IP addresses"""
    try:
        if not hostname:
            return "Hostname required", False
        
        # Clean up hostname
        hostname = hostname.strip().lower()
        if hostname.startswith(('http://', 'https://')):
            hostname = hostname.split('://')[1].split('/')[0]
        
        # Get all address info (IPv4 and IPv6)
        results = socket.getaddrinfo(hostname, None)
        
        # Extract unique IPs
        ipv4 = set()
        ipv6 = set()
        
        for result in results:
            family, _, _, _, sockaddr = result
            ip = sockaddr[0]
            if family == socket.AF_INET:
                ipv4.add(ip)
            elif family == socket.AF_INET6:
                ipv6.add(ip)
        
        lines = [f"DNS lookup for {hostname}:"]
        if ipv4:
            lines.append(f"  IPv4: {', '.join(sorted(ipv4))}")
        if ipv6:
            lines.append(f"  IPv6: {', '.join(sorted(ipv6))}")
        
        if not ipv4 and not ipv6:
            return f"No DNS records found for {hostname}", False
        
        return '\n'.join(lines), True
        
    except socket.gaierror as e:
        return f"DNS lookup failed for {hostname}: {e}", False
    except Exception as e:
        logger.error(f"DNS lookup error: {e}")
        return f"DNS lookup error: {e}", False


def _check_port(host: str, port: int, timeout: float = 5) -> tuple:
    """Check if a TCP port is open"""
    try:
        if not host:
            return "Host required", False
        if not port or port < 1 or port > 65535:
            return "Valid port (1-65535) required", False
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            return f"Port {port} is OPEN on {host}", True
        else:
            return f"Port {port} is CLOSED on {host}", True
            
    except socket.timeout:
        return f"Connection to {host}:{port} timed out after {timeout}s", True
    except socket.gaierror:
        return f"Could not resolve hostname: {host}", False
    except Exception as e:
        logger.error(f"Port check error: {e}")
        return f"Port check failed: {e}", False


def _http_headers(url: str) -> tuple:
    """Get HTTP headers from a URL"""
    try:
        if not url:
            return "URL required", False
        
        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        response = requests.head(url, timeout=10, allow_redirects=True)
        
        lines = [f"HTTP {response.status_code} {response.reason}"]
        lines.append(f"Final URL: {response.url}")
        lines.append("Headers:")
        
        # Show interesting headers
        interesting = ['server', 'content-type', 'content-length', 'date', 
                      'cache-control', 'x-powered-by', 'x-frame-options',
                      'strict-transport-security', 'content-security-policy']
        
        for key in interesting:
            if key in response.headers:
                value = response.headers[key]
                # Truncate long values
                if len(value) > 80:
                    value = value[:77] + '...'
                lines.append(f"  {key}: {value}")
        
        return '\n'.join(lines), True
        
    except requests.exceptions.SSLError:
        return f"SSL certificate error for {url}", False
    except requests.exceptions.ConnectionError:
        return f"Could not connect to {url}", False
    except requests.exceptions.Timeout:
        return f"Request timed out for {url}", False
    except Exception as e:
        logger.error(f"HTTP headers error: {e}")
        return f"Failed to get headers: {e}", False


def execute(function_name: str, arguments: dict, config) -> tuple:
    """Execute network function. Returns (result_string, success_bool)."""
    try:
        if function_name == "get_external_ip":
            return _get_external_ip()
        
        elif function_name == "dns_lookup":
            hostname = arguments.get("hostname", "")
            return _dns_lookup(hostname)
        
        elif function_name == "check_port":
            host = arguments.get("host", "")
            port = arguments.get("port", 0)
            timeout = arguments.get("timeout", 5)
            return _check_port(host, int(port), float(timeout))
        
        elif function_name == "http_headers":
            url = arguments.get("url", "")
            return _http_headers(url)
        
        else:
            return f"Unknown network function: {function_name}", False
            
    except Exception as e:
        logger.error(f"Network function error: {e}")
        return f"Network error: {e}", False