# Google Calendar OAuth2 routes
# Handles the authorization flow and token management.
# Scope-aware: uses credentials_manager for multi-account support.

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

import requests
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPES = 'https://www.googleapis.com/auth/calendar'
CALLBACK_PATH = '/api/plugin/google-calendar/callback'
# Easy-connect relay: holds the shared Google client so users skip the
# Console entirely. Overridable in settings for self-hosted relays.
DEFAULT_RELAY_URL = 'https://oauth.sapphireblue.dev'

# Serialize the CSRF load-mutate-save cycle. Two concurrent "Connect" clicks
# (second tab, or user + AI tool simultaneously) would otherwise clobber each
# other's CSRF token → one callback arrives with a token that's no longer
# valid → user loses their refresh-token (Google only issues it with
# prompt=consent access_type=offline, once). Day-ruiner scout finding.
_csrf_lock = threading.Lock()

# Temporary CSRF state stored in plugin_state (not credentials — it's ephemeral)
def _get_csrf_path():
    state_dir = Path(__file__).parent.parent.parent.parent / 'user' / 'plugin_state'
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / 'gcal-csrf.json'

def _load_csrf():
    path = _get_csrf_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            # Corrupt file (mid-write crash from pre-atomic-write era) — start
            # fresh. Log so the incident is visible.
            logger.warning(f"gcal CSRF file corrupt at {path}; starting empty")
    return {}

def _save_csrf(data):
    """Atomic write to prevent a mid-write crash (or concurrent second writer)
    from leaving half-written JSON on disk. Same tmp+rename pattern PluginState
    uses."""
    path = _get_csrf_path()
    tmp = path.with_suffix(f'.json.tmp.{os.getpid()}')
    try:
        tmp.write_text(json.dumps(data), encoding='utf-8')
        tmp.replace(path)
    finally:
        if tmp.exists():
            try: tmp.unlink()
            except Exception: pass


def _get_redirect_uri(request):
    """Build absolute callback URL from the current request's origin."""
    base = str(request.base_url).rstrip('/')
    return f"{base}{CALLBACK_PATH}"


def start_auth(request=None, query=None, settings=None, **_):
    """GET /api/plugin/google-calendar/auth — redirect to Google consent screen.
    Accepts ?scope=xxx query param to specify which account scope to connect.
    Syncs plugin settings to credentials_manager for the target scope."""
    from core.credentials_manager import credentials

    q = query or {}
    scope = q.get('scope', 'default')

    # Read credentials from per-scope account (saved via Settings editor)
    acct = credentials.get_gcal_account(scope)
    client_id = acct.get('client_id', '')
    client_secret = acct.get('client_secret', '')

    if not client_id:
        # No local client creds = easy connect via the relay. Pasting your
        # own client ID/secret (BYO) always wins over the relay path.
        return _start_easy_auth(request, scope, settings)

    # Generate CSRF state token that encodes the scope
    state_token = secrets.token_urlsafe(32)
    # Lock protects the load-mutate-save cycle from concurrent OAuth starts
    # (e.g. user clicks Connect twice, or two scopes connect simultaneously).
    with _csrf_lock:
        csrf = _load_csrf()
        csrf[state_token] = {'scope': scope, 'created': time.time()}
        # Clean up old CSRF tokens (>10 min)
        csrf = {k: v for k, v in csrf.items() if time.time() - v.get('created', 0) < 600}
        _save_csrf(csrf)

    params = {
        'client_id': client_id,
        'redirect_uri': _get_redirect_uri(request),
        'response_type': 'code',
        'scope': SCOPES,
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state_token,
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


def _start_easy_auth(request, scope, settings):
    """Easy connect: run the flow through the relay's shared Google client.
    The PKCE verifier never leaves this machine — only its SHA256 challenge
    travels, so nobody (relay included) can redeem the auth code without the
    verifier we present at claim time."""
    relay = ((settings or {}).get('GCAL_RELAY_URL') or DEFAULT_RELAY_URL).rstrip('/')
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()

    state_token = secrets.token_urlsafe(32)
    with _csrf_lock:
        csrf = _load_csrf()
        csrf[state_token] = {'scope': scope, 'created': time.time(),
                             'mode': 'relay', 'verifier': verifier, 'relay': relay}
        csrf = {k: v for k, v in csrf.items() if time.time() - v.get('created', 0) < 600}
        _save_csrf(csrf)

    # Our CSRF state rides inside return_url; the relay's own signed state
    # covers the Google leg and is invisible to us.
    return_url = f"{_get_redirect_uri(request)}?state={state_token}"
    try:
        resp = requests.post(f"{relay}/start", json={
            'return_url': return_url, 'code_challenge': challenge}, timeout=15)
    except requests.RequestException as e:
        return {"error": (f"Couldn't reach the connect relay ({type(e).__name__}). "
                          "Try again in a moment, or use your own client ID (Advanced).")}
    if resp.status_code != 200:
        try:
            detail = resp.json().get('error', '')
        except ValueError:
            detail = ''
        logger.error(f"[GCAL] Relay /start failed: HTTP {resp.status_code} {detail}")
        return {"error": f"Connect relay refused (HTTP {resp.status_code}{', ' + detail if detail else ''})"}
    try:
        auth_url = resp.json()['auth_url']
    except (ValueError, KeyError):
        return {"error": "Malformed response from connect relay"}
    return RedirectResponse(url=auth_url, status_code=302)


def handle_callback(request=None, query=None, settings=None, **_):
    """GET /api/plugin/google-calendar/callback — exchange code for tokens."""
    from core.credentials_manager import credentials

    q = query or {}
    code = q.get('code', '')
    ticket = q.get('ticket', '')
    state_token = q.get('state', '')
    error = q.get('error', '') or q.get('relay_error', '')

    if error:
        return RedirectResponse(url=f"/#settings?gcal_error={error}", status_code=302)

    if not code and not ticket:
        return {"error": "No authorization code received"}

    # Verify CSRF state and extract scope
    # Lock protects callback-time RMW against a concurrent auth-start writing
    # a new token into the same file.
    with _csrf_lock:
        csrf = _load_csrf()
        csrf_entry = csrf.pop(state_token, None)
        _save_csrf(csrf)

    if not csrf_entry:
        return {"error": "State mismatch — possible CSRF attack"}

    scope = csrf_entry.get('scope', 'default')

    if csrf_entry.get('mode') == 'relay':
        return _finish_easy_auth(scope, ticket, csrf_entry)

    acct = credentials.get_gcal_account(scope)
    client_id = acct.get('client_id', '')
    client_secret = acct.get('client_secret', '')

    if not client_id or not client_secret:
        return {"error": "Missing client ID or secret in account settings"}

    # Exchange code for tokens
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': _get_redirect_uri(request),
        'grant_type': 'authorization_code',
    }, timeout=15)

    if resp.status_code != 200:
        logger.error(f"[GCAL] Token exchange failed: {resp.text}")
        return {"error": f"Token exchange failed: {resp.status_code}"}

    try:
        tokens = resp.json()
        access_token = tokens['access_token']
    except (ValueError, KeyError) as e:
        logger.error(f"[GCAL] Invalid token response: {e}")
        return {"error": f"Invalid token response from Google: {e}"}
    refresh_token = tokens.get('refresh_token', acct.get('refresh_token', ''))
    expires_at = time.time() + tokens.get('expires_in', 3600)

    credentials.update_gcal_tokens(scope, refresh_token, access_token, expires_at)
    return _connected_redirect(scope)


def _finish_easy_auth(scope, ticket, csrf_entry):
    """Claim tokens from the relay with our PKCE verifier and store them."""
    from core.credentials_manager import credentials

    relay = csrf_entry.get('relay') or DEFAULT_RELAY_URL
    if not ticket:
        return {"error": "No ticket received from connect relay"}

    try:
        resp = requests.post(f"{relay}/claim", json={
            'ticket': ticket,
            'code_verifier': csrf_entry.get('verifier', ''),
        }, timeout=15)
    except requests.RequestException as e:
        return {"error": f"Couldn't reach the connect relay ({type(e).__name__}). Try connecting again."}

    if resp.status_code != 200:
        logger.error(f"[GCAL] Relay claim failed: HTTP {resp.status_code} {resp.text[:200]}")
        return {"error": f"Token claim failed (HTTP {resp.status_code}). Try connecting again."}

    try:
        tokens = resp.json()
        access_token = tokens['access_token']
    except (ValueError, KeyError) as e:
        logger.error(f"[GCAL] Invalid relay token response: {e}")
        return {"error": f"Invalid token response from relay: {e}"}

    acct = credentials.get_gcal_account(scope)
    refresh_token = tokens.get('refresh_token', acct.get('refresh_token', ''))
    expires_at = time.time() + tokens.get('expires_in', 3600)

    # (Re)write the account shell: no local client creds — that's the point.
    # relay_url is frozen at connect time, exactly like byo freezes client
    # creds. Preserves calendar_id/label on reconnect.
    credentials.set_gcal_account(scope, '', '', acct.get('calendar_id', 'primary'),
                                 '', acct.get('label', scope),
                                 auth_mode='relay', relay_url=relay)
    credentials.update_gcal_tokens(scope, refresh_token, access_token, expires_at)
    return _connected_redirect(scope)


def _connected_redirect(scope):
    # Tell the UI a gcal scope/account appeared so sidebar scope dropdowns refresh
    # without a reload. Defensive — never let a publish failure break the redirect.
    try:
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {"kind": "gcal", "action": "connected", "name": scope})
    except Exception:
        pass

    logger.info(f"[GCAL] OAuth2 authorization successful for scope '{scope}'")
    return RedirectResponse(url="/#settings", status_code=302)


def get_status(query=None, settings=None, **_):
    """GET /api/plugin/google-calendar/status — check if connected.
    Accepts ?scope=xxx to check a specific account."""
    from core.credentials_manager import credentials

    q = query or {}
    scope = q.get('scope', 'default')
    return {"connected": credentials.has_gcal_account(scope)}


def disconnect(query=None, body=None, settings=None, **_):
    """POST /api/plugin/google-calendar/disconnect — remove stored tokens for a scope."""
    from core.credentials_manager import credentials

    # Scope can come from body or query
    scope = 'default'
    if body and isinstance(body, dict):
        scope = body.get('scope', scope)
    elif query:
        scope = query.get('scope', scope)

    acct = credentials.get_gcal_account(scope)
    if acct.get('client_id') or acct.get('auth_mode') == 'relay':
        # Keep the account config, actually clear the tokens. `update_gcal_tokens`
        # with empty strings deliberately preserves the refresh_token (routine
        # refresh semantics) — use `clear_gcal_tokens` for disconnect.
        credentials.clear_gcal_tokens(scope)
        return {"status": "disconnected"}
    return {"status": "no_account"}
