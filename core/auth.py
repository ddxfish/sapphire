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
_last_prune = 0.0


def _prune_stale_keys():
    """Periodically remove stale entries from rate limit dicts (every 5 min)."""
    global _last_prune
    now = time.time()
    if now - _last_prune < 300:
        return
    _last_prune = now
    cutoff = now - max(RATE_LIMIT_WINDOW, 300)
    for d in (_rate_limits, _endpoint_limits):
        stale = [k for k, v in d.items() if not v or v[-1] < cutoff]
        for k in stale:
            del d[k]


def check_rate_limit(ip: str) -> bool:
    """Returns True if rate limited, False if OK."""
    _prune_stale_keys()
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
    """Dependency that requires login. Raises HTTPException if not logged in.

    Three accepted auth paths, tried in order:
      1. Session cookie         — browser users (request.session.logged_in)
      2. Authorization: Bearer  — external integrations (named API tokens; 2026-05-21)
      3. X-API-Key              — internal tools / legacy callers (bcrypt password hash)
    """
    from core.setup import is_setup_complete, get_password_hash

    # One hash read per request on the normal path; the setup gate is only
    # consulted when no hash came back (it re-reads — and tests mock the two
    # independently).
    stored_hash = get_password_hash()
    if stored_hash is None and not is_setup_complete():
        raise HTTPException(status_code=307, headers={"Location": "/setup"})
    stored_hash = stored_hash or ''

    # 1. Session auth (browser users). The cookie carries the SALT of the
    #    hash it was minted under (login_submit stamps it): a password change
    #    or a delete-secret_key reset invalidates every OTHER cookie — the one
    #    "log out other devices" primitive (pre-push hunt 2026-09-08, E4#3).
    #    A stale cookie is cleared here so /login can't bounce it back to /.
    if request.session.get('logged_in'):
        if stored_hash and request.session.get('pw') == stored_hash[:29]:
            return True
        request.session.clear()

    # 2. Bearer token (named API token; for external integrations like the
    #    Valheim mod, scripts, etc.). Tokens minted via Settings > System >
    #    API Keys. Match is constant-time across all tokens; on success the
    #    token's last_used_at is updated and persisted (best-effort).
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        candidate = auth_header.split(' ', 1)[1].strip()
        if candidate:
            try:
                from core.api_tokens import api_tokens
                if api_tokens.verify(candidate) is not None:
                    return True
            except Exception as e:
                # Don't let an api_tokens fault block auth-by-other-means
                logger.warning(f"api_tokens.verify raised: {e!r}")

    # 3. X-API-Key auth (internal/tool calls, e.g. meta.py) — bcrypt password hash
    api_key = request.headers.get('X-API-Key')
    if api_key and stored_hash and secrets.compare_digest(api_key, stored_hash):
        return True

    # Not authenticated
    if request.url.path.startswith('/api/'):
        raise HTTPException(status_code=401, detail="Unauthorized")
    raise HTTPException(status_code=307, headers={"Location": "/login"})


async def require_login_ws(websocket) -> bool:
    """WS analogue of require_login — session-cookie only (a browser-mic channel
    has no business with Bearer/API-key callers). SessionMiddleware populates
    sessions for websocket scopes too, so the cookie check is the same; the
    Origin header must match Host (CSRF tokens don't cover WS handshakes —
    same-origin fills that hole). Accepts then closes 4401 on failure so the
    client gets a readable close code instead of a raw handshake reject."""
    from urllib.parse import urlparse
    from core.setup import get_password_hash

    ok = False
    try:
        stored = get_password_hash()
        if stored and websocket.session.get('logged_in') \
                and websocket.session.get('pw') == stored[:29]:
            origin = websocket.headers.get('origin', '')
            host = websocket.headers.get('host', '')
            ok = bool(host) and urlparse(origin).netloc == host
    except Exception as e:
        logger.warning(f"WS auth check failed: {e!r}")
    if not ok:
        await websocket.accept()
        await websocket.close(code=4401)
        return False
    return True


async def require_setup(request: Request):
    """Dependency that requires setup to be complete."""
    from core.setup import is_setup_complete

    if not is_setup_complete():
        raise HTTPException(status_code=307, headers={"Location": "/setup"})

    return True


def get_client_ip(request: Request) -> str:
    """Get client IP from request. Uses direct connection IP only (X-Forwarded-For is spoofable)."""
    return request.client.host if request.client else '127.0.0.1'


# ── Endpoint rate limiting (per-session sliding window) ──────────────────────

_endpoint_limits: dict = defaultdict(list)


def check_endpoint_rate(request: Request, endpoint: str, max_calls: int,
                        window: int = 60, identity: str = None) -> None:
    """Raise 429 if identity exceeds max_calls within window seconds.

    Identity precedence (scout #13, 2026-04-20):
      1. Explicit `identity` arg — callers that authenticated via bearer
         token pass a hash of the token so MCP clients don't all collapse
         into one IP-based bucket when multiple tools hammer the same
         endpoint (MCP `initialize` + `tools/list` + first `tools/call`
         is 3 calls at session start alone).
      2. Session CSRF token if present (standard web flow).
      3. Client IP as last resort.
    """
    _prune_stale_keys()   # was only run from /login — this dict never shrank (E4#6)
    session_id = identity or request.session.get('csrf_token') or get_client_ip(request)
    key = f"{session_id}:{endpoint}"
    now = time.time()
    _endpoint_limits[key] = [t for t in _endpoint_limits[key] if now - t < window]
    if len(_endpoint_limits[key]) >= max_calls:
        raise HTTPException(status_code=429, detail=f"Rate limited — max {max_calls} requests per {window}s")
    _endpoint_limits[key].append(now)
