# Plugin Signing

Sapphire verifies plugin integrity using ed25519 signatures. Every plugin can be signed by its author, and Sapphire checks these signatures on startup.

## Verification Tiers

| Tier | Meaning |
|------|---------|
| **official** | Signed by Sapphire's baked-in key (core maintainer) |
| **verified_author** | Signed by an authorized third-party key |
| **unsigned** | No `plugin.sig` file — allowed only when sideloading is enabled |
| **failed** | Signature exists but doesn't match any trusted key, or files were modified |

When `ALLOW_UNSIGNED_PLUGINS` is disabled, only `official` and `verified_author` plugins load — plus, in managed/Docker mode, unsigned plugins that pass strict file validation load with a `validated` tier.

---

## For Authorized Plugin Authors

You've been invited as a trusted plugin author. Here's how to set up signing.

### 1. Generate Your Signing Key

You need Python with `cryptography` installed (already included in Sapphire's environment).

```bash
python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

key = Ed25519PrivateKey.generate()

# Save private key (KEEP THIS SECRET)
with open('my_signing_key.pem', 'wb') as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ))

# Print your public key
pub_hex = key.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw
).hex()
print(f'Your public key (hex): {pub_hex}')
print('Private key saved to: my_signing_key.pem')
"
```

This creates two things:
- `my_signing_key.pem` — your **private key**. Keep this safe. Never share it.
- A 64-character hex string — your **public key**. Send this to the Sapphire maintainer.

### 2. Send Your Public Key

Send your public key hex string to the Sapphire maintainer, who will add it to the [authorized keys list](https://github.com/ddxfish/sapphire-plugin-keys). Once added, any plugin you sign will be recognized as `verified_author` by all Sapphire instances.

Include with your key:
- Your name or handle (displayed in the UI as the verified author)
- A link to your plugin repo (optional, for reference)

### 3. Sign Your Plugin

Use the signing tool included with Sapphire:

```bash
# Sign a single plugin
python tools/sign_plugin.py plugins/my-plugin/

# Sign with a specific key
python tools/sign_plugin.py plugins/my-plugin/ --key /path/to/my_signing_key.pem

# Sign all system plugins (core maintainer)
python tools/sign_plugin.py --all

# Sign all including user/ plugins
python tools/sign_plugin.py --all --include-user
```

Default key path: `user/plugin_signing_key.pem`. Override with `--key`.

The tool:
1. Hashes all signable files (`.py`, `.json`, `.js`, `.css`, `.html`, `.md`)
2. Normalizes line endings (CRLF to LF) for cross-platform consistency
3. Signs the hash manifest with your ed25519 private key
4. Writes `plugin.sig` to the plugin directory

### 4. Re-sign After Changes

Any time you modify a plugin file, the signature becomes invalid. Re-run the sign command after making changes.

---

## How Verification Works

On startup (and plugin rescan), Sapphire:

1. Reads `plugin.sig` from each plugin directory
2. Verifies file hashes — every signable file must match the manifest
3. Checks for unrecognized files not in the manifest (prevents injection)
4. Tries the baked-in official key first, then authorized third-party keys
5. Reports the verification tier in logs and the plugin list UI

Authorized keys are fetched from a remote URL (`PLUGIN_KEYS_URL` in settings), cached locally for 24 hours, with a disk fallback if the fetch fails.

## plugin.sig Format

```json
{
  "plugin": "my_plugin",
  "version": "1.0.0",
  "files": {
    "plugin.json": "sha256:abc123...",
    "tools/my_tool.py": "sha256:def456..."
  },
  "signature": "base64-encoded-ed25519-signature"
}
```

The signature covers the JSON-serialized `plugin`, `version`, and `files` fields (sorted keys, compact separators). The `signature` field itself is excluded from the signed payload.

## Reference for AI

Plugin signing = Ed25519 signature over plugin.json's serialized plugin/version/files fields (sorted keys, compact separators; signature field excluded). Files hashed individually; editing ANY hashed file invalidates the signature.

STATES: official (Sapphire key) / verified_author (authorized third-party key) / unsigned / failed. Tampered (failed) is ALWAYS blocked at boot — no override. Unsigned is blocked unless ALLOW_UNSIGNED_PLUGINS=true (default false). Managed installs add 'validated'.
KEYS: authorized third-party keys distributed via the central key list (PLUGIN_KEYS_URL, fetched with a 24h disk-cache-first policy; user/authorized_plugin_keys.json is only the fetch cache — there is no user-local key-add mechanism).
SIGNING: python tools/sign_plugin.py plugins/<name> [--key /path/to/private_key.pem] [--all] [--include-user]. Default key path user/plugin_signing_key.pem.
SYMPTOM: plugin silently missing after an edit = signature stale — re-sign, restart. Check Settings > Plugins for the trust badge.
