"""API tokens for external integrations.

A separate auth surface from the user's password (bcrypt hash in
secret_key) — admins generate named bearer tokens that external apps
(Valheim mod, scripts, integrations) use to call Sapphire's /api/* surface.

Tokens:
- Named (so admins know which integration each one belongs to)
- Stored plain (local-first app; user owns the disk; same posture as credentials.json)
- Shown ONCE at creation time. The frontend reveals it once; after that only
  the name + last4 are visible. Lost token = revoke + recreate.
- Independent of the user's password — surviving a password change is a feature.
- Revocable per-token without touching anything else.

Storage: ~/.config/sapphire/api_tokens.json (alongside credentials.json + secret_key).
"""
import json
import logging
import os
import secrets
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.setup import CONFIG_DIR
from core.settings_manager import _fsync_file, _fsync_dir
from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

API_TOKENS_FILE = CONFIG_DIR / 'api_tokens.json'

# Prefix for visual identification in logs / config files. "sk" = "secret key",
# matching the convention many SaaS APIs use (Stripe, OpenAI, etc.).
_TOKEN_PREFIX = "sk_"
_TOKEN_RANDOM_BYTES = 32  # → ~43 url-safe chars after b64encode


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _generate_token() -> str:
    """sk_<43 url-safe random chars>. ~256 bits of entropy."""
    return _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_RANDOM_BYTES)


def _last4(token: str) -> str:
    return token[-4:] if len(token) >= 4 else token


class ApiTokensManager:
    """Singleton; matches the shape of credentials_manager."""

    def __init__(self):
        self._lock = threading.RLock()
        self._tokens: List[dict] = []
        self._load()

    # ─── Persistence ────────────────────────────────────────────────────────

    def _load(self):
        if not API_TOKENS_FILE.exists():
            self._tokens = []
            return

        try:
            with open(API_TOKENS_FILE, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._tokens = data.get('tokens', []) or []
            elif isinstance(data, list):
                # Earlier shape (if any) was a bare list. Tolerate.
                self._tokens = data
            else:
                logger.error(f"api_tokens.json has unexpected shape; ignoring")
                self._tokens = []
            logger.info(f"Loaded {len(self._tokens)} API token(s) from {API_TOKENS_FILE}")
        except Exception as e:
            logger.critical(f"api_tokens.json corrupted: {e}")
            # Back up the corrupt file (same pattern credentials_manager uses)
            try:
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup = API_TOKENS_FILE.with_suffix(f'.json.corrupt.{ts}')
                shutil.copy2(API_TOKENS_FILE, backup)
                logger.critical(f"Corrupt api_tokens backed up to {backup}")
            except Exception as backup_err:
                logger.error(f"Could not back up corrupt api_tokens: {backup_err}")
            self._tokens = []
            # Don't auto-save defaults here — we don't want to clobber on read err

    def _save(self) -> bool:
        """Atomic write. Same pattern as credentials_manager._save()."""
        with self._lock:
            try:
                # The file's OWN parent, not the import-time CONFIG_DIR: a
                # test that patches API_TOKENS_FILE alone must not mkdir
                # the real config dir (order-dependent PermissionError).
                API_TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = API_TOKENS_FILE.with_suffix('.tmp')
                payload = {"version": 1, "tokens": self._tokens}
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, indent=2)
                    _fsync_file(f)
                if sys.platform != 'win32':
                    os.chmod(tmp_path, 0o600)
                replace_with_retry(tmp_path, API_TOKENS_FILE)
                _fsync_dir(API_TOKENS_FILE.parent)
                return True
            except Exception as e:
                logger.error(f"Failed to save api_tokens.json: {e}")
                return False

    # ─── Public API ─────────────────────────────────────────────────────────

    def create(self, name: str) -> dict:
        """Create a new token. Returns the full record INCLUDING the plaintext
        token — this is the ONLY moment the full token is exposed by the
        manager. Subsequent reads use list_safe() which masks it."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Token name required")
        if len(name) > 64:
            raise ValueError("Token name too long (max 64 chars)")

        with self._lock:
            if any(t.get('name') == name for t in self._tokens):
                raise ValueError(f"Token name '{name}' already in use")

            entry = {
                "id": str(uuid.uuid4()),
                "name": name,
                "token": _generate_token(),
                "created_at": _now_iso(),
                "last_used_at": None,
            }
            self._tokens.append(entry)
            if not self._save():
                # Roll back in-memory state to keep parity with disk
                self._tokens.remove(entry)
                raise RuntimeError("Failed to persist new API token")
            logger.info(f"API token '{name}' created (id={entry['id'][:8]})")
            return entry

    def revoke(self, token_id: str) -> bool:
        """Revoke by ID. Returns True if found and removed."""
        with self._lock:
            for i, t in enumerate(self._tokens):
                if t.get('id') == token_id:
                    name = t.get('name', '?')
                    del self._tokens[i]
                    if not self._save():
                        # Re-insert on save failure so disk and memory match
                        self._tokens.insert(i, t)
                        raise RuntimeError("Failed to persist revocation")
                    logger.info(f"API token '{name}' revoked (id={token_id[:8]})")
                    return True
        return False

    def verify(self, candidate_token: str) -> Optional[dict]:
        """Constant-time check across all tokens. Returns the record (with the
        plaintext token field included — caller must not log it) or None.

        Side effect: on a successful verify, updates last_used_at and persists.
        Atomic write, ~few ms — fine for typical auth rate. If you need to
        verify without the side-effect, use _verify_silent."""
        if not candidate_token:
            return None

        with self._lock:
            matched = None
            for t in self._tokens:
                if secrets.compare_digest(candidate_token, t.get('token', '')):
                    matched = t
                    break
            if matched is None:
                return None

            # Update last_used_at + persist. Best-effort: if persistence fails,
            # we still return the match (auth shouldn't fail because the disk
            # is full). Log + carry on.
            matched['last_used_at'] = _now_iso()
            self._save()  # ignore return; auth is the primary concern
            return matched

    def list_safe(self) -> List[dict]:
        """List tokens with full token value MASKED. For UI display."""
        with self._lock:
            return [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "last4": _last4(t.get("token", "")),
                    "created_at": t.get("created_at"),
                    "last_used_at": t.get("last_used_at"),
                }
                for t in self._tokens
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._tokens)


# Module-level singleton — same pattern as credentials_manager.credentials
api_tokens = ApiTokensManager()
