#!/usr/bin/env python3
"""Plugin signing tool — signs Sapphire plugins with an ed25519 private key.

Usage:
    python tools/sign_plugin.py plugins/my-plugin/
    python tools/sign_plugin.py plugins/ssh/ plugins/email/
    python tools/sign_plugin.py --all
    python tools/sign_plugin.py --all --include-user
    python tools/sign_plugin.py plugins/my-plugin/ --key /path/to/private_key.pem

The tool hashes all signable files (.py, .json, .js, .css, .html, .md),
builds a manifest, signs it with ed25519, and writes plugin.sig.

Default key path: user/plugin_signing_key.pem (override with --key)
"""

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Must match core/plugin_verify.py
SIGNABLE_EXTENSIONS = {".py", ".json", ".js", ".css", ".html", ".md"}


def hash_file(path: Path) -> str:
    """SHA256 hex digest, line-ending normalized (CRLF -> LF)."""
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def build_file_manifest(plugin_dir: Path) -> dict:
    """Build {relative_path: hash} for all signable files."""
    manifest = {}
    for f in sorted(plugin_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.name == "plugin.sig":
            continue
        if f.suffix not in SIGNABLE_EXTENSIONS:
            continue
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(plugin_dir).as_posix()
        manifest[rel] = hash_file(f)
    return manifest


def sign_plugin(plugin_dir: Path, private_key_pem: bytes) -> dict:
    """Sign a plugin directory. Returns the plugin.sig dict."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    # Load private key
    private_key = load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Key is not an Ed25519 private key")

    # Verify plugin has a manifest
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No plugin.json in {plugin_dir}")

    plugin_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plugin_name = plugin_manifest.get("name", plugin_dir.name)

    # Build file hash manifest
    files = build_file_manifest(plugin_dir)
    if not files:
        raise ValueError(f"No signable files found in {plugin_dir}")

    # Build signature payload (everything except the signature itself)
    sig_data = {
        "plugin": plugin_name,
        "version": plugin_manifest.get("version", "0.0.0"),
        "files": files,
    }

    payload = json.dumps(sig_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload)
    sig_data["signature"] = base64.b64encode(signature).decode("ascii")

    return sig_data


def main():
    parser = argparse.ArgumentParser(description="Sign Sapphire plugins with ed25519")
    parser.add_argument("plugins", nargs="*", help="Plugin directories to sign")
    parser.add_argument("--key", default=None, help="Path to ed25519 private key PEM (default: user/plugin_signing_key.pem)")
    parser.add_argument("--all", action="store_true", help="Sign all plugins in plugins/")
    parser.add_argument("--include-user", action="store_true", help="With --all, also sign user/plugins/")
    args = parser.parse_args()

    # Resolve key path
    key_path = Path(args.key) if args.key else PROJECT_ROOT / "user" / "plugin_signing_key.pem"
    if not key_path.exists():
        print(f"Error: Private key not found at {key_path}", file=sys.stderr)
        print("Generate one with:", file=sys.stderr)
        print('  python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; '
              'from cryptography.hazmat.primitives import serialization; '
              'k=Ed25519PrivateKey.generate(); '
              'open(\'my_key.pem\',\'wb\').write(k.private_bytes(serialization.Encoding.PEM, '
              'serialization.PrivateFormat.PKCS8, serialization.NoEncryption())); '
              'print(\'Public key (hex):\', k.public_key().public_bytes(serialization.Encoding.Raw, '
              'serialization.PublicFormat.Raw).hex())"', file=sys.stderr)
        sys.exit(1)

    # Defensive perms tightening on load. Leak of this key = an attacker can
    # sign arbitrary plugins as the user; signed-valid is the only trust
    # anchor for plugin exec() (ALLOW_UNSIGNED_PLUGINS defaults to False).
    # Generation happens via copy-pasted one-liner that doesn't restrict
    # perms — so tightening every time we load is the belt that keeps a
    # forgotten umask from being the whole story. 2026-04-24.
    if sys.platform != 'win32':
        try:
            cur_mode = key_path.stat().st_mode & 0o777
            if cur_mode != 0o600:
                os.chmod(key_path, 0o600)
                print(f"  Tightened key perms: {oct(cur_mode)} → 0o600 on {key_path}", file=sys.stderr)
        except (OSError, PermissionError) as e:
            print(f"  Warning: could not chmod key file ({e})", file=sys.stderr)
    else:
        # Windows: NTFS ACLs aren't touched here. The key file inherits
        # parent-dir permissions. On a single-user Windows machine this is
        # fine; on shared machines, see docs/SIGNING.md for ACL guidance.
        pass

    private_key_pem = key_path.read_bytes()

    # Collect plugin dirs
    plugin_dirs = []
    if args.all:
        system_dir = PROJECT_ROOT / "plugins"
        if system_dir.exists():
            plugin_dirs.extend(
                d for d in sorted(system_dir.iterdir())
                if d.is_dir() and (d / "plugin.json").exists()
            )
        if args.include_user:
            user_dir = PROJECT_ROOT / "user" / "plugins"
            if user_dir.exists():
                plugin_dirs.extend(
                    d for d in sorted(user_dir.iterdir())
                    if d.is_dir() and (d / "plugin.json").exists()
                )
    else:
        for p in args.plugins:
            d = Path(p)
            if not d.is_absolute():
                d = PROJECT_ROOT / d
            if not d.is_dir():
                print(f"Warning: {p} is not a directory, skipping", file=sys.stderr)
                continue
            plugin_dirs.append(d)

    if not plugin_dirs:
        print("No plugins to sign. Pass plugin directories or use --all.", file=sys.stderr)
        sys.exit(1)

    # Show public key for reference
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat
        pk = load_pem_private_key(private_key_pem, password=None)
        pub_hex = pk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        print(f"Signing with key: {key_path.name} (public: {pub_hex[:16]}...)")
    except Exception:
        pass

    # Sign each plugin
    signed = 0
    for plugin_dir in plugin_dirs:
        try:
            sig_data = sign_plugin(plugin_dir, private_key_pem)
            sig_path = plugin_dir / "plugin.sig"
            sig_path.write_text(json.dumps(sig_data, indent=2), encoding="utf-8")
            n_files = len(sig_data["files"])
            print(f"  Signed: {plugin_dir.name} ({n_files} files)")
            signed += 1
        except Exception as e:
            print(f"  FAILED: {plugin_dir.name} — {e}", file=sys.stderr)

    print(f"\nDone: {signed}/{len(plugin_dirs)} plugins signed")


if __name__ == "__main__":
    main()
