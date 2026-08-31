# core/plugin_verify.py — Plugin signature verification
#
# Verifies plugin.sig files against baked-in public key and authorized third-party keys.
# Each plugin.sig contains a manifest of SHA256 hashes + ed25519 signature.
#
# Verification tiers:
#   official        — signed by Krem's baked-in key
#   verified_author — signed by a key in the authorized keys list
#   unsigned        — no plugin.sig
#   failed          — signature exists but doesn't match any key

import json
import hashlib
import logging
import time
from pathlib import Path
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger(__name__)

# Baked-in public key — corresponds to the private key used for signing
SIGNING_PUBLIC_KEY = bytes.fromhex("b4e188e374c7ddc83544cda23f4818693441bc197068a41e745d54ddf1b3b1d3")

# File extensions to verify (must match what sign_plugin.py hashes)
SIGNABLE_EXTENSIONS = {".py", ".json", ".js", ".css", ".html", ".md"}

# Filenames that platform tooling drops into directories without the user's
# knowledge. If one of these is a signable extension (currently none are, but
# the check is cheap), it should NOT block plugin load — a `Thumbs.db` or
# `Desktop.ini` appearing in a plugin dir isn't a tampering event. Covers:
#   - Windows Explorer: Thumbs.db, desktop.ini (+ different casings)
#   - macOS Finder: .DS_Store, ._AppleDouble
#   - Linux trash: .directory
_IGNORABLE_FILENAMES = {
    "Thumbs.db", "thumbs.db",
    "desktop.ini", "Desktop.ini",
    ".DS_Store", ".directory",
}

# Cache for authorized keys (avoid hitting GitHub on every plugin scan)
_authorized_keys_cache: list | None = None
_authorized_keys_fetched_at: float = 0
_CACHE_TTL = 86400  # 24 hours

# Cache file path
_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_FILE = _PROJECT_ROOT / "user" / "authorized_plugin_keys.json"


def _build_signable_payload(manifest_data: dict) -> bytes:
    """Build the canonical bytes that were signed.

    Deterministic JSON of everything except the signature field itself.
    """
    payload = {k: v for k, v in manifest_data.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_file(path: Path) -> str:
    """SHA256 hex digest of a file, line-ending normalized (CRLF → LF)."""
    content = path.read_bytes().replace(b'\r\n', b'\n')
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _try_verify_signature(public_key_bytes: bytes, signature_bytes: bytes, payload: bytes) -> bool:
    """Try to verify a signature with a given public key. Returns True if valid."""
    try:
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature_bytes, payload)
        return True
    except InvalidSignature:
        return False
    except Exception as e:
        logger.debug(f"Key verify error: {e}")
        return False


def _load_authorized_keys() -> list:
    """Load authorized third-party signing keys.

    Fetches from GitHub raw URL (configured via PLUGIN_KEYS_URL), caches to disk.
    Falls back to cached copy if fetch fails. Returns list of key dicts.
    """
    global _authorized_keys_cache, _authorized_keys_fetched_at

    # Return memory cache if fresh
    if _authorized_keys_cache is not None and (time.time() - _authorized_keys_fetched_at) < _CACHE_TTL:
        return _authorized_keys_cache

    # Disk cache FIRST (negspace E1, 2026-08-31): this used to dial GitHub
    # network-first on every boot — the disk cache was only a failure
    # fallback and the TTL global dies with the process. A fresh disk
    # cache (24h) is now authoritative; boot with warm cache is silent.
    disk = _read_disk_cache()
    if disk is not None:
        keys, fetched_at = disk
        if (time.time() - fetched_at) < _CACHE_TTL:
            _authorized_keys_cache = keys
            _authorized_keys_fetched_at = fetched_at
            return keys

    # Stale or missing → refresh from remote (rides SOCKS when enabled)
    keys = _fetch_remote_keys()
    if keys is not None:
        _authorized_keys_cache = keys
        _authorized_keys_fetched_at = time.time()
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps({"keys": keys, "fetched_at": time.time()}, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[PLUGIN-VERIFY] Failed to write key cache: {e}")
        return keys

    # Remote failed → a stale disk cache beats nothing
    if disk is not None:
        keys, fetched_at = disk
        _authorized_keys_cache = keys
        _authorized_keys_fetched_at = fetched_at
        logger.info(f"[PLUGIN-VERIFY] Remote refresh failed; using stale cached keys ({len(keys)} keys)")
        return keys

    # No keys available
    _authorized_keys_cache = []
    _authorized_keys_fetched_at = time.time()
    return []


def _read_disk_cache() -> tuple | None:
    """(keys, fetched_at) from the disk cache, or None if absent/corrupt."""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        return data.get("keys", []), float(data.get("fetched_at", 0))
    except Exception as e:
        logger.warning(f"[PLUGIN-VERIFY] Failed to read key cache: {e}")
        return None


def _fetch_remote_keys() -> list | None:
    """Fetch authorized keys JSON from the configured URL. Returns list or None on failure."""
    try:
        import config
        url = config.PLUGIN_KEYS_URL
    except Exception:
        url = ""

    if not url:
        return None

    try:
        # Rides core.socks_proxy (negspace E1): raw urllib went DIRECT even
        # with SOCKS on. get_session raising (proxy enabled but broken) is
        # CORRECT fail-closed behavior — we fall back to the disk cache,
        # never to a direct connection.
        from core.socks_proxy import get_session
        resp = get_session().get(url, timeout=10,
                                 headers={"User-Agent": "Sapphire-PluginVerify/1.0"})
        resp.raise_for_status()
        if True:
            data = resp.json()
            keys = data.get("keys", [])
            # Validate each key has required fields
            valid = []
            for k in keys:
                if k.get("name") and k.get("public_key_hex"):
                    try:
                        bytes.fromhex(k["public_key_hex"])
                        valid.append(k)
                    except ValueError:
                        logger.warning(f"[PLUGIN-VERIFY] Invalid hex key for '{k.get('name')}', skipping")
                else:
                    logger.warning(f"[PLUGIN-VERIFY] Key missing name or public_key_hex, skipping")
            logger.info(f"[PLUGIN-VERIFY] Fetched {len(valid)} authorized keys from remote")
            return valid
    except Exception as e:
        logger.debug(f"[PLUGIN-VERIFY] Remote key fetch failed: {e}")
        return None


def _verify_file_integrity(plugin_dir: Path, sig_data: dict) -> Tuple[bool, str]:
    """Verify all file hashes in the signature manifest. Returns (passed, message)."""
    files_manifest = sig_data.get("files")
    if not files_manifest:
        return False, "plugin.sig missing files manifest"

    resolved_root = plugin_dir.resolve()
    for rel_path, expected_hash in files_manifest.items():
        file_path = plugin_dir / rel_path
        # Block path traversal (../ escaping plugin directory)
        try:
            file_path.resolve().relative_to(resolved_root)
        except ValueError:
            return False, f"path traversal attempt: {rel_path}"
        if not file_path.exists():
            return False, f"missing file: {rel_path}"
        actual_hash = _hash_file(file_path)
        if actual_hash != expected_hash:
            return False, f"hash mismatch: {rel_path} (file modified after signing)"

    # Check for new files not in manifest (could be injected)
    for f in plugin_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.name == "plugin.sig":
            continue
        if f.name in _IGNORABLE_FILENAMES:
            continue
        if f.suffix not in SIGNABLE_EXTENSIONS:
            continue
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(plugin_dir).as_posix()
        if rel not in files_manifest:
            return False, f"unrecognized file not in manifest: {rel}"

    return True, "ok"


def verify_plugin(plugin_dir: Path) -> Tuple[bool, str, dict]:
    """Verify a plugin's signature and file integrity.

    Returns:
        (passed, message, metadata)
        metadata = {"tier": "official"|"verified_author"|"unsigned"|"failed", "author": name|None}
    """
    sig_path = plugin_dir / "plugin.sig"

    # No signature file → unsigned
    if not sig_path.exists():
        return False, "unsigned", {"tier": "unsigned", "author": None}

    # Load the signature file
    try:
        sig_data = json.loads(sig_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"corrupt plugin.sig: {e}", {"tier": "failed", "author": None}

    signature_b64 = sig_data.get("signature")
    if not signature_b64:
        return False, "plugin.sig missing signature field", {"tier": "failed", "author": None}

    # Decode signature
    try:
        import base64
        signature_bytes = base64.b64decode(signature_b64)
    except Exception as e:
        return False, f"invalid signature encoding: {e}", {"tier": "failed", "author": None}

    payload = _build_signable_payload(sig_data)

    # 1. Try baked-in key (official)
    if _try_verify_signature(SIGNING_PUBLIC_KEY, signature_bytes, payload):
        # Signature valid — now verify file integrity
        ok, msg = _verify_file_integrity(plugin_dir, sig_data)
        if ok:
            return True, "verified", {"tier": "official", "author": None}
        else:
            return False, msg, {"tier": "failed", "author": None}

    # 2. Try authorized third-party keys
    authorized_keys = _load_authorized_keys()
    for key_entry in authorized_keys:
        try:
            key_bytes = bytes.fromhex(key_entry["public_key_hex"])
        except (ValueError, KeyError):
            continue
        if _try_verify_signature(key_bytes, signature_bytes, payload):
            # Signature valid — verify file integrity
            ok, msg = _verify_file_integrity(plugin_dir, sig_data)
            author_name = key_entry.get("name", "Unknown")
            if ok:
                return True, "verified", {"tier": "verified_author", "author": author_name}
            else:
                return False, msg, {"tier": "failed", "author": None}

    # 3. No key matched — tampered or signed with unknown key
    return False, "signature verification FAILED — possible tampering", {"tier": "failed", "author": None}
