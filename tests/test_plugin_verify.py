"""
Tests for core/plugin_verify.py — Multi-key plugin signature verification.

Covers signature attacks, key trust attacks, CRLF cross-platform regression,
network fallback edge cases, and path traversal.

Run with: pytest tests/test_plugin_verify.py -v
"""
import pytest
import json
import sys
import hashlib
import base64
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.plugin_verify as pv
from core.plugin_verify import verify_plugin, _verify_file_integrity, _hash_file


# ── Helpers ──

def _generate_keypair():
    """Generate an ed25519 keypair. Returns (private_key, public_key_bytes)."""
    private = Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, pub


def _sign_plugin_dir(plugin_dir: Path, private_key: Ed25519PrivateKey, extra_sig_fields=None):
    """Sign all signable files in plugin_dir. Writes plugin.sig. Returns sig_data dict."""
    files = {}
    for f in sorted(plugin_dir.rglob("*")):
        if not f.is_file() or f.name == "plugin.sig":
            continue
        if f.suffix not in pv.SIGNABLE_EXTENSIONS:
            continue
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(plugin_dir).as_posix()
        content = f.read_bytes().replace(b"\r\n", b"\n")
        files[rel] = f"sha256:{hashlib.sha256(content).hexdigest()}"

    sig_data = {
        "plugin": "test-plugin",
        "version": "1.0.0",
        "files": files,
    }
    if extra_sig_fields:
        sig_data.update(extra_sig_fields)

    payload = json.dumps(
        {k: v for k, v in sig_data.items() if k != "signature"},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    signature = private_key.sign(payload)
    sig_data["signature"] = base64.b64encode(signature).decode("ascii")

    (plugin_dir / "plugin.sig").write_text(json.dumps(sig_data, indent=2), encoding="utf-8")
    return sig_data


def _make_plugin(tmp: Path, content="print('hello')", filename="main.py"):
    """Create a minimal plugin dir with plugin.json and one source file."""
    d = tmp / "test-plugin"
    d.mkdir(exist_ok=True)
    (d / "plugin.json").write_text(json.dumps({"name": "test-plugin"}))
    (d / filename).write_text(content)
    return d


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the authorized keys cache between tests."""
    orig_cache = pv._authorized_keys_cache
    orig_time = pv._authorized_keys_fetched_at
    orig_key = pv.SIGNING_PUBLIC_KEY
    yield
    pv._authorized_keys_cache = orig_cache
    pv._authorized_keys_fetched_at = orig_time
    pv.SIGNING_PUBLIC_KEY = orig_key


@pytest.fixture
def official_key():
    """Generate an 'official' keypair and patch it as baked-in."""
    priv, pub = _generate_keypair()
    pv.SIGNING_PUBLIC_KEY = pub
    return priv, pub


@pytest.fixture
def author_key():
    """Generate a third-party author keypair."""
    return _generate_keypair()


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


def _inject_authorized_keys(pub_bytes, name="Test Author"):
    """Inject an authorized key into the memory cache."""
    pv._authorized_keys_cache = [{"name": name, "public_key_hex": pub_bytes.hex()}]
    pv._authorized_keys_fetched_at = 9999999999


# ── 1. Signature Attacks ──

class TestSignatureAttacks:
    """Tests 1-5: Attacks that tamper with files, inject files, or corrupt signatures."""

    def test_01_tampered_file(self, tmp, official_key):
        """Sign plugin, modify one byte in a source file → failed."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        # Tamper: append one byte to main.py
        main = d / "main.py"
        main.write_text(main.read_text() + " ")

        passed, msg, meta = verify_plugin(d)
        assert not passed
        assert meta["tier"] == "failed"
        assert "hash mismatch" in msg

    def test_02_injected_file(self, tmp, official_key):
        """Sign plugin, drop a new .py file into the dir → failed."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        # Inject a new file
        (d / "evil.py").write_text("import os; os.system('rm -rf /')")

        passed, msg, meta = verify_plugin(d)
        assert not passed
        assert meta["tier"] == "failed"
        assert "unrecognized file" in msg

    def test_03_swapped_sig(self, tmp, official_key):
        """Copy plugin.sig from one plugin onto a different plugin → failed."""
        priv, _ = official_key

        # Sign plugin A
        a = tmp / "plugin-a"
        a.mkdir()
        (a / "plugin.json").write_text(json.dumps({"name": "plugin-a"}))
        (a / "code.py").write_text("# plugin A")
        _sign_plugin_dir(a, priv)

        # Create plugin B with different content
        b = tmp / "plugin-b"
        b.mkdir()
        (b / "plugin.json").write_text(json.dumps({"name": "plugin-b"}))
        (b / "code.py").write_text("# plugin B — totally different")

        # Swap: copy A's sig onto B
        shutil.copy(a / "plugin.sig", b / "plugin.sig")

        passed, msg, meta = verify_plugin(b)
        assert not passed
        assert meta["tier"] == "failed"

    def test_04_truncated_signature(self, tmp, official_key):
        """Signature that decodes to fewer than 64 bytes → failed."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        # Corrupt the signature — truncate the base64
        sig_data = json.loads((d / "plugin.sig").read_text())
        sig_data["signature"] = base64.b64encode(b"short").decode()
        (d / "plugin.sig").write_text(json.dumps(sig_data))

        passed, msg, meta = verify_plugin(d)
        assert not passed
        assert meta["tier"] == "failed"

    def test_05_empty_files_manifest(self, tmp, official_key):
        """Valid signature over empty files manifest but actual files exist → failed."""
        priv, _ = official_key
        d = _make_plugin(tmp)

        # Sign with empty files dict
        sig_data = {"plugin": "test-plugin", "version": "1.0.0", "files": {}}
        payload = json.dumps(sig_data, sort_keys=True, separators=(",", ":")).encode()
        signature = priv.sign(payload)
        sig_data["signature"] = base64.b64encode(signature).decode()
        (d / "plugin.sig").write_text(json.dumps(sig_data))

        passed, msg, meta = verify_plugin(d)
        assert not passed
        assert meta["tier"] == "failed"
        # Empty dict is falsy — caught by "missing files manifest" guard
        assert "missing files manifest" in msg


# ── 2. Key Trust Attacks ──

class TestKeyTrustAttacks:
    """Tests 6-9: Attacks on the authorized key list itself."""

    def test_06_revoked_author(self, tmp, official_key, author_key):
        """Plugin signed by author, then author's key removed → failed."""
        _, official_pub = official_key
        author_priv, author_pub = author_key

        d = _make_plugin(tmp)
        _sign_plugin_dir(d, author_priv)

        # First: author key is in the list → verified_author
        _inject_authorized_keys(author_pub, "Soon-To-Be-Fired")
        passed, msg, meta = verify_plugin(d)
        assert passed
        assert meta["tier"] == "verified_author"
        assert meta["author"] == "Soon-To-Be-Fired"

        # Now: revoke — empty the authorized keys list
        pv._authorized_keys_cache = []
        pv._authorized_keys_fetched_at = 9999999999

        passed, msg, meta = verify_plugin(d)
        assert not passed
        assert meta["tier"] == "failed"

    def test_07_key_hex_poisoning(self, tmp, official_key, author_key):
        """Malformed hex in authorized_keys.json → skip bad key, don't crash."""
        author_priv, author_pub = author_key

        d = _make_plugin(tmp)
        _sign_plugin_dir(d, author_priv)

        # Inject keys list with garbage + the real key
        pv._authorized_keys_cache = [
            {"name": "Garbage Key", "public_key_hex": "not-valid-hex-lol"},
            {"name": "Odd Length", "public_key_hex": "abc"},
            {"name": "Real Author", "public_key_hex": author_pub.hex()},
        ]
        pv._authorized_keys_fetched_at = 9999999999

        # Should skip garbage, find real key
        passed, msg, meta = verify_plugin(d)
        assert passed
        assert meta["tier"] == "verified_author"
        assert meta["author"] == "Real Author"

    def test_08_empty_keys_list(self, tmp, official_key, author_key):
        """Empty authorized keys list → only baked-in key works."""
        priv_official, _ = official_key
        author_priv, _ = author_key

        pv._authorized_keys_cache = []
        pv._authorized_keys_fetched_at = 9999999999

        # Official-signed plugin still works
        d1 = _make_plugin(tmp)
        _sign_plugin_dir(d1, priv_official)
        passed, msg, meta = verify_plugin(d1)
        assert passed
        assert meta["tier"] == "official"

        # Author-signed plugin fails (no authorized keys)
        d2 = tmp / "author-plugin"
        d2.mkdir()
        (d2 / "plugin.json").write_text(json.dumps({"name": "author-plugin"}))
        (d2 / "code.py").write_text("# author code")
        _sign_plugin_dir(d2, author_priv)

        passed, msg, meta = verify_plugin(d2)
        assert not passed
        assert meta["tier"] == "failed"

    def test_09_corrupted_cache_file(self, tmp, official_key):
        """Garbled cache file on disk → survive, fall back gracefully."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        # Force cache miss so _load_authorized_keys tries disk
        pv._authorized_keys_cache = None
        pv._authorized_keys_fetched_at = 0

        # Write garbage to the cache file location
        with patch.object(pv, "_CACHE_FILE", tmp / "bad_cache.json"):
            (tmp / "bad_cache.json").write_text("{{{{not json at all!!!!")
            with patch.object(pv, "_fetch_remote_keys", return_value=None):
                # Should not crash — falls through to empty keys
                passed, msg, meta = verify_plugin(d)
                # Official key is baked-in, so it still works
                assert passed
                assert meta["tier"] == "official"


# ── 3. CRLF Cross-Platform Regression ──

class TestCRLFRegression:
    """Tests 10-12: Ensure CRLF normalization works across the signing chain."""

    def test_10_crlf_in_plugin_files(self, tmp, official_key):
        """File signed with LF, verified on disk with CRLF → should still pass."""
        priv, _ = official_key
        d = _make_plugin(tmp, content="line1\nline2\nline3\n")

        # Sign with LF content (how sign_plugin.py works)
        _sign_plugin_dir(d, priv)

        # Now rewrite the file with CRLF (simulating Windows checkout)
        (d / "main.py").write_bytes(b"line1\r\nline2\r\nline3\r\n")

        # Should pass — _hash_file normalizes CRLF → LF
        passed, msg, meta = verify_plugin(d)
        assert passed
        assert meta["tier"] == "official"

    def test_11_crlf_in_plugin_sig(self, tmp, official_key):
        """plugin.sig itself has CRLF line endings → json.loads handles it."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        # Rewrite plugin.sig with CRLF
        sig_content = (d / "plugin.sig").read_text()
        (d / "plugin.sig").write_bytes(sig_content.encode().replace(b"\n", b"\r\n"))

        passed, msg, meta = verify_plugin(d)
        assert passed
        assert meta["tier"] == "official"

    def test_12_crlf_in_authorized_keys_json(self, tmp, official_key, author_key):
        """Authorized keys JSON fetched with CRLF line endings → still parses."""
        author_priv, author_pub = author_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, author_priv)

        # Simulate fetched JSON with CRLF
        keys_json = json.dumps({
            "keys": [{"name": "CRLF Author", "public_key_hex": author_pub.hex()}]
        }, indent=2)
        crlf_bytes = keys_json.encode().replace(b"\n", b"\r\n")

        pv._authorized_keys_cache = None
        pv._authorized_keys_fetched_at = 0

        # Mock the fetch to return CRLF JSON. Seam moved 2026-08-31
        # (negspace E1): _fetch_remote_keys rides core.socks_proxy
        # get_session() now, not urllib — .json() decodes exactly like
        # requests does, so the CRLF contract is still exercised.
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = lambda: json.loads(crlf_bytes.decode("utf-8"))
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("core.socks_proxy.get_session", return_value=mock_session):
            with patch("config.PLUGIN_KEYS_URL", "https://example.com/keys.json"):
                with patch.object(pv, "_CACHE_FILE", tmp / "cache.json"):
                    passed, msg, meta = verify_plugin(d)
                    assert passed
                    assert meta["tier"] == "verified_author"
                    assert meta["author"] == "CRLF Author"


# ── 4. Network Fallback Edge Cases ──

class TestNetworkFallbacks:
    """Tests 13-15: What happens when the network is hostile or down."""

    def test_13_unreachable_url(self, tmp, official_key):
        """PLUGIN_KEYS_URL points to garbage → graceful fallback, baked-in still works."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        pv._authorized_keys_cache = None
        pv._authorized_keys_fetched_at = 0

        # Seam moved 2026-08-31 (negspace E1): fetch rides get_session().
        # Mock it to raise so the suite never emits a real DNS query.
        mock_session = MagicMock()
        mock_session.get.side_effect = ConnectionError("unreachable")
        with patch("core.socks_proxy.get_session", return_value=mock_session):
          with patch("config.PLUGIN_KEYS_URL", "https://this-does-not-exist.invalid/keys.json"):
            with patch.object(pv, "_CACHE_FILE", tmp / "nonexistent_cache.json"):
                passed, msg, meta = verify_plugin(d)
                assert passed
                assert meta["tier"] == "official"

    def test_14_url_returns_html(self, tmp, official_key):
        """GitHub returns HTML error page (404, rate limit) → no crash."""
        priv, _ = official_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, priv)

        pv._authorized_keys_cache = None
        pv._authorized_keys_fetched_at = 0

        # Seam moved 2026-08-31 (negspace E1): get_session, not urllib.
        # requests' .json() raises on HTML — mock does the same.
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(side_effect=ValueError("No JSON could be decoded"))
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("core.socks_proxy.get_session", return_value=mock_session):
            with patch("config.PLUGIN_KEYS_URL", "https://example.com/keys.json"):
                with patch.object(pv, "_CACHE_FILE", tmp / "no_cache.json"):
                    # Should not crash — HTML won't parse as JSON
                    passed, msg, meta = verify_plugin(d)
                    # Official key still baked in
                    assert passed
                    assert meta["tier"] == "official"

    def test_15_url_returns_wrong_schema(self, tmp, official_key, author_key):
        """URL returns valid JSON but wrong schema → no keys loaded, no crash."""
        author_priv, _ = author_key
        d = _make_plugin(tmp)
        _sign_plugin_dir(d, author_priv)

        pv._authorized_keys_cache = None
        pv._authorized_keys_fetched_at = 0

        # Seam moved 2026-08-31 (negspace E1): get_session, not urllib.
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(return_value={"not_keys": "lol", "schema": 42})
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("core.socks_proxy.get_session", return_value=mock_session):
            with patch("config.PLUGIN_KEYS_URL", "https://example.com/keys.json"):
                with patch.object(pv, "_CACHE_FILE", tmp / "no_cache.json"):
                    # Wrong schema → empty keys → author-signed plugin fails
                    passed, msg, meta = verify_plugin(d)
                    assert not passed
                    assert meta["tier"] == "failed"


# ── 5. Path Traversal ──

class TestPathTraversal:
    """Test 16: Malicious paths in the files manifest."""

    def test_16_path_traversal_in_manifest(self, tmp, official_key):
        """../../../etc/passwd in files manifest → caught and blocked."""
        priv, _ = official_key
        d = _make_plugin(tmp)

        # Manually craft a sig with a traversal path
        evil_path = "../../../etc/passwd"
        sig_data = {
            "plugin": "test-plugin",
            "version": "1.0.0",
            "files": {
                "plugin.json": _hash_file(d / "plugin.json"),
                "main.py": _hash_file(d / "main.py"),
                evil_path: "sha256:deadbeef",
            },
        }
        payload = json.dumps(
            {k: v for k, v in sig_data.items() if k != "signature"},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        signature = priv.sign(payload)
        sig_data["signature"] = base64.b64encode(signature).decode()
        (d / "plugin.sig").write_text(json.dumps(sig_data))

        passed, msg, meta = verify_plugin(d)
        assert not passed
        assert meta["tier"] == "failed"
        assert "path traversal" in msg
