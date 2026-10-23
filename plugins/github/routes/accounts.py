# GitHub plugin routes — token validation.
# Hits api.github.com/user with the supplied PAT and returns the username +
# token scopes. Used by the settings UI to confirm a token works AND auto-fill
# the username field so the user doesn't have to type it.

import logging

import requests
from core import net

logger = logging.getLogger(__name__)

API_USER = 'https://api.github.com/user'


def validate_token(body=None, **_):
    """POST /api/plugin/github/validate
    Body: {"pat": "ghp_...", "scope": "default" (optional)}
    Returns: {"valid": bool, "username": str, "scopes": str, "error": str?}

    On success, also auto-saves the username (and token if scope provided) to
    credentials_manager so the user only has to click one button.
    """
    body = body or {}
    pat = (body.get('pat') or '').strip()
    target_scope = (body.get('scope') or '').strip()
    persist = bool(body.get('persist'))
    label = (body.get('label') or '').strip()

    if not pat:
        return {"valid": False, "error": "No token provided"}

    try:
        resp = net.get(
            API_USER,
            headers={
                'Authorization': f'Bearer {pat}',
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'Sapphire-AI-github-plugin',
            },
            timeout=15,
        )
    except requests.RequestException as e:
        return {"valid": False, "error": f"Network error: {e}"}

    if resp.status_code == 401:
        return {"valid": False, "error": "Token is invalid or expired"}
    if resp.status_code != 200:
        return {"valid": False, "error": f"GitHub returned {resp.status_code}: {resp.text[:200]}"}

    try:
        user = resp.json()
    except Exception:
        return {"valid": False, "error": "GitHub returned non-JSON response"}

    username = user.get('login', '')
    scopes_header = resp.headers.get('X-OAuth-Scopes', '') or '(fine-grained PAT)'

    result = {
        "valid": True,
        "username": username,
        "scopes": scopes_header,
        "name": user.get('name', ''),
        "avatar_url": user.get('avatar_url', ''),
    }

    # Optional: persist directly on validate (saves a roundtrip)
    if persist and target_scope:
        from core.credentials_manager import credentials
        ok = credentials.set_github_account(target_scope, username, pat, label or target_scope)
        result["persisted"] = bool(ok)

    return result
