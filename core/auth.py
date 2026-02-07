# auth.py - Authentication utilities for FastAPI
import time
import secrets
import logging
from collections import defaultdict
from typing import Optional
from fastapi import Request, HTTPException, Depends
from starlette.responses import RedirectResponse

logger = logging.getLogger(__name__)

# Rate limiting state
_rate_limits: dict = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 5  # attempts per window


def check_rate_limit(ip: str) -> bool:
    """Returns True if rate limited, False if OK."""
    now = time.time()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limits[ip]) >= RATE_LIMIT_MAX:
        return True
    _rate_limits[ip].append(now)
    return False


def generate_csrf_token(request: Request) -> str:
    """Generate or retrieve CSRF token from session."""
    if 'csrf_token' not in request.session:
        request.session['csrf_token'] = secrets.token_hex(32)
    return request.session['csrf_token']


def validate_csrf(request: Request, token: Optional[str] = None) -> bool:
    """Validate CSRF token. Returns True if valid."""
    if token is None:
        return False
    session_token = request.session.get('csrf_token')
    return token is not None and token == session_token


async def require_login(request: Request):
    """Dependency that requires login. Raises HTTPException if not logged in."""
    from core.setup import is_setup_complete, get_password_hash

    if not is_setup_complete():
        raise HTTPException(status_code=307, headers={"Location": "/setup"})

    # Session auth (browser users)
    if request.session.get('logged_in'):
        return True

    # API key auth (internal/tool calls from same process, e.g. meta.py)
    api_key = request.headers.get('X-API-Key')
    if api_key:
        stored_hash = get_password_hash()
        if stored_hash and secrets.compare_digest(api_key, stored_hash):
            return True

    # Not authenticated
    if request.url.path.startswith('/api/'):
        raise HTTPException(status_code=401, detail="Unauthorized")
    raise HTTPException(status_code=307, headers={"Location": "/login"})


async def require_setup(request: Request):
    """Dependency that requires setup to be complete."""
    from core.setup import is_setup_complete

    if not is_setup_complete():
        raise HTTPException(status_code=307, headers={"Location": "/setup"})

    return True


def get_client_ip(request: Request) -> str:
    """Get client IP from request. Uses direct connection IP only (X-Forwarded-For is spoofable)."""
    return request.client.host if request.client else '127.0.0.1'
