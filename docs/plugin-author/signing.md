# Plugin Signing & Verification

Sapphire uses ed25519 signatures to verify plugin integrity. This page is the plugin-author summary — the full authorized-author walkthrough (key generation, getting on the key list) lives in [docs/SIGNING.md](../SIGNING.md).

## Verification States

| State | Badge | Behavior |
|-------|-------|----------|
| **official** | "Official" | Signed by Sapphire's baked-in key (core maintainer). Always loads |
| **verified_author** | Author-name badge | Signed by an authorized third-party key from the central key list. Always loads |
| **unsigned** | "Unsigned" | No `plugin.sig` file — blocked unless "Allow Unsigned Plugins" is on |
| **failed** | "Tampered" | `plugin.sig` exists but matches no trusted key, or files were modified after signing. Always blocked — no override |

In managed/Docker mode only, an unsigned plugin that passes strict file validation loads with a **validated** tier instead of being blocked.

## How It Works

Each signed plugin has a `plugin.sig` file containing:
- SHA256 hashes of every signable file (`.py`, `.json`, `.js`, `.css`, `.html`, `.md`)
- An ed25519 signature over the hash manifest

On scan (and rescan), the loader verifies:
1. Every file's hash matches the manifest
2. No unrecognized signable files were added after signing
3. The signature verifies against the baked-in official key first, then against each authorized third-party key

### Where trusted keys come from

The official public key is baked into `core/plugin_verify.py`. Authorized third-party keys are distributed through the **central key list**: Sapphire fetches it from a remote URL (`PLUGIN_KEYS_URL` in settings) and caches it — in memory, and on disk at `user/authorized_plugin_keys.json` as a fallback when the fetch fails. That file is only a cache, not a trust store you edit: there is no user-local "add a key" mechanism. To have your signatures recognized, your public key must be added to the central list (see [docs/SIGNING.md](../SIGNING.md)).

## Sideloading (Unsigned Plugins)

`ALLOW_UNSIGNED_PLUGINS` defaults to **off**. Enable it in Settings > Plugins with the toggle. A danger dialog warns about the risks.

When enabled, unsigned plugins load with a warning. Tampered (`failed`) plugins are always blocked regardless of this setting.

## Signing Plugins

The signing tool lives at `tools/sign_plugin.py`. It requires `cryptography` (already in Sapphire's deps). The default key path is `user/plugin_signing_key.pem`; override it with `--key`.

```bash
# Sign a single plugin (uses the default key)
python tools/sign_plugin.py plugins/my-plugin/

# Sign multiple
python tools/sign_plugin.py plugins/ssh/ plugins/email/

# Sign with a specific key
python tools/sign_plugin.py plugins/my-plugin/ --key /path/to/my_signing_key.pem

# Sign all plugins in plugins/
python tools/sign_plugin.py --all

# ...including user/plugins/
python tools/sign_plugin.py --all --include-user
```

This hashes all signable files (line endings normalized CRLF → LF for cross-platform consistency), builds a manifest, signs it with ed25519, and writes `plugin.sig` into the plugin directory.

**Re-sign after any change** to plugin files — even a one-character edit invalidates the signature and the plugin will show as Tampered.

## Signing Your Own Plugins (Third-Party Authors)

Verified-author signing has two halves: your keypair, and the central key list.

**1. Generate a keypair** (one-time):

```bash
python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
with open('my_signing_key.pem', 'wb') as f:
    f.write(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
print('Public key (hex):', key.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
"
```

Keep `my_signing_key.pem` secret. The hex string is your public key.

**2. Get on the central key list.** Send your public key hex to the Sapphire maintainers, along with the name or handle to display as the verified author (see [docs/SIGNING.md](../SIGNING.md) for where). Once your key is on the list, every Sapphire instance recognizes plugins you sign as `verified_author` — users don't add anything by hand.

**3. Sign with your key:**

```bash
python tools/sign_plugin.py path/to/your-plugin/ --key /path/to/my_signing_key.pem
```

**4. Ship `plugin.sig`** in your repo, and re-sign after every change.

Until your key is on the central list, a signature from it verifies against nothing and your plugin shows as **Tampered** — which always blocks, and is strictly worse than shipping unsigned. Ship no `plugin.sig` until your key is accepted.

## Reference for AI

- Verification states: `official` (baked-in key), `verified_author` (key on the central authorized list), `unsigned` (no plugin.sig), `failed` (signature matches no trusted key OR files modified — shown as "Tampered"). Managed/Docker mode only: `validated` (unsigned + passed strict file validation).
- Load gate: official / verified_author / validated always load; unsigned needs `ALLOW_UNSIGNED_PLUGINS` on (Settings > Plugins); failed never loads, no override.
- Signable files: `.py .json .js .css .html .md`; hashes computed with CRLF→LF normalization; extra signable files not in the manifest fail verification.
- Signer: `python tools/sign_plugin.py <dirs...>` | `--all` [`--include-user`] [`--key path.pem`]; default key `user/plugin_signing_key.pem`; writes `plugin.sig`. Re-sign after ANY edit to a signable file. There is no PRIVATE_KEY_PATH constant to edit — the key is chosen by `--key`.
- Keys: official public key baked into `core/plugin_verify.py`; third-party keys come ONLY from the central key list fetched from `PLUGIN_KEYS_URL` (`user/authorized_plugin_keys.json` is a disk cache with in-memory TTL, not a user-editable trust store). No local add-a-key mechanism exists.
- `plugin.sig` = `{plugin, version, files: {relpath: "sha256:..."}, signature}`; signature = base64 ed25519 over the sorted-keys, compact-separator JSON of the other fields.
