# Plugin Signing & Verification

Sapphire uses ed25519 signatures to verify plugin integrity.

## Verification States

| State | Badge | Behavior |
|-------|-------|----------|
| **Signed** | Green "Signed" | Always loads |
| **Unsigned** | Yellow "Unsigned" | Blocked unless "Allow Unsigned Plugins" is on |
| **Tampered** | Red "Tampered" | Always blocked — no override |

## How It Works

Each signed plugin has a `plugin.sig` file containing:
- SHA256 hashes of every signable file (`.py`, `.json`, `.js`, `.css`, `.html`, `.md`)
- An ed25519 signature over the hash manifest

On scan, the loader verifies:
1. Signature matches the baked-in public key
2. Every file's hash matches the manifest
3. No unrecognized files were added after signing

## Sideloading (Unsigned Plugins)

`ALLOW_UNSIGNED_PLUGINS` defaults to **off**. Enable it in Settings > Plugins with the toggle. A danger dialog warns about the risks.

When enabled, unsigned plugins load with a warning. Tampered plugins are always blocked regardless of this setting.

## Signing Plugins (Official)

The signing tool lives at `user/tools/sign_plugin.py`. It requires `cryptography` (already in sapphire's deps) and the private key at `user/plugin_signing_key.pem`.

```bash
# Sign a single plugin
python user/tools/sign_plugin.py plugins/my-plugin/

# Sign multiple
python user/tools/sign_plugin.py plugins/ssh/ plugins/email/

# Sign all plugins in plugins/
python user/tools/sign_plugin.py --all
```

This hashes all signable files (`.py`, `.json`, `.js`, `.css`, `.html`, `.md`), builds a manifest, signs it with ed25519, and writes `plugin.sig` into the plugin directory.

**Re-sign after any change** to plugin files — even a one-character edit invalidates the signature and the plugin will show as "Tampered".

## Signing Your Own Plugins (Third-Party Authors)

For plugin developers distributing outside the official store:

```bash
# 1. Generate a keypair (one-time)
python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
print(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
print('Public key (hex):', key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
"
# Save the PEM output as your private key. Share the public key hex with users.

# 2. Sign your plugin (same tool, point at your key)
# Edit PRIVATE_KEY_PATH in sign_plugin.py or pass your key path

# 3. Ship plugin.sig with your plugin
```

Users add your public key hex to their authorized keys to verify your plugins. The official Sapphire public key is baked into `core/plugin_verify.py`.
