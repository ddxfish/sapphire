# Social Providers

The **Social** plugin (`core/plugins/social`) owns profiles, the chat's `social`
scope, the Outbox and the tools (`social_post`, `social_inbox`, `social_reply`,
`social_stats`, `social_delete`, `social_meme`, `social_list`). A **provider** is the
one platform-specific piece: a block in your manifest and one Python module. The five
built-ins (Facebook, X, YouTube, Instagram, LinkedIn) are declared and written exactly
this way inside the Social plugin; `core/plugins/social-bluesky` is the same thing as
a separate plugin, the one to copy.

## Manifest

```json
{
  "name": "social-bluesky",
  "requires_plugins": ["social"],
  "capabilities": {
    "social": [
      {
        "provider": "bluesky",
        "label": "Bluesky",
        "icon": "🦋",
        "module": "provider.py",
        "auth": "fields",
        "capabilities": ["post", "image", "link", "inbox", "reply", "delete", "stats"],
        "max_chars": 300,
        "max_images": 4,
        "config_schema": [
          {"key": "handle", "type": "string", "label": "Handle"},
          {"key": "app_password", "type": "password", "secret": true, "label": "App password"}
        ],
        "notes": "One line for the card."
      }
    ]
  }
}
```

| Field | Meaning |
|-------|---------|
| `provider` | slug, unique across plugins (`[a-z0-9][a-z0-9_-]{0,32}`) |
| `module` | plugin-relative `.py` file exporting the functions below |
| `auth` | `fields`: the form is all; `oauth`: a **Connect** button runs your `auth_start`/`auth_finish` |
| `capabilities` | subset of `post image link inbox reply delete stats` — name only what the module exports |
| `config_schema` | plugin-settings field dicts; `"secret": true` (or `type: password`) fields are stored by core's credentials manager, scrambled, never in plugin state or backups |
| `max_chars`, `max_images` | the platform's limits; the engine refuses longer text and trims extra images (`max_images: 0` = text only) |
| `public_image_url` | `true` when the platform fetches images by URL (Instagram): `post()` receives URLs, which the engine publishes to the drop folder first |
| `notes` | one line shown under the provider in the card |

The loader does not know the `social` key; the Social plugin reads every loaded
manifest at its own load and at every `plugins_ready`, so order never matters.
`requires_plugins: ["social"]` keeps your plugin from loading while Social is off.

## The module

```python
from plugins.social.errors import SocialError      # the one thing you raise

def validate(conn) -> (ok, message)                 # the Test button; may conn.save_meta({'identity': ...})
def post(conn, text, images, link, disclose) -> {'id': ..., 'url': ...}
def inbox(conn, since) -> [{'id', 'kind', 'author', 'text', 'ts', 'url'}]   # since = epoch seconds
def reply(conn, item_id, text) -> {'id': ..., 'url': ...}
def delete(conn, post_id) -> bool
def stats(conn) -> {'followers': 12, ...}
def refresh(conn) -> True                           # optional: renew the token (called when meta.expires_at nears)

# auth: "oauth" only
def auth_start(conn, redirect_uri, state) -> url | (url, stash)   # stash: what auth_finish needs back (a PKCE verifier)
def auth_finish(conn, redirect_uri, query, stash) -> {'secrets': {...}, 'meta': {...}}
```

`images` is a list of `(bytes, media_type)` — JPEG, PNG or GIF under a few MB, the
engine re-encodes anything else — or of URLs when `public_image_url` is on. `link` is
`None` unless you declared the `link` capability (otherwise the engine appends it to the
text). `disclose` is the connection's AI-label switch: set the platform's flag when it
has one (Instagram's `is_ai_generated`), ignore it otherwise.

### The Connection

| Member | What |
|--------|------|
| `conn.name`, `conn.profile`, `conn.provider`, `conn.label`, `conn.mode`, `conn.disclose` | the wiring |
| `conn.config` | the non-secret schema fields |
| `conn.secrets` | the secret fields and what `auth_finish` stored, in the clear |
| `conn.meta` | plain facts you learned: identity, ids, `expires_at` |
| `conn.save_secrets({...})` | merge: a str replaces, `''` clears, `None` keeps |
| `conn.save_meta({...})` | merge into the wiring |
| `conn.connected` | oauth: a finished round trip; fields: every secret set |

Printing a Connection never shows a secret. The engine checks `conn.connected` and
runs `refresh()` before every call, so your functions can assume a usable token;
raise `SocialError` with the platform's own words when it refuses.
Never put a token or secret into a `SocialError` message. Anything else you raise
(a network error, a bug) reaches the model, the log and the UI only through
`core.redact.describe()`: kind and host for network errors, and every value in
`conn.secrets` blanked. Send tokens in a header when the platform allows it.

### OAuth

The redirect URI every vendor console must know is
`https://<host>/api/plugin/social/callback/<provider>` — the card shows it next to the
form. Core keeps the CSRF state (`plugin_state/social-csrf.json`, locked, 10 minutes)
and your `stash`; `auth_finish` gets the vendor's query back plus the stash, trades the
code for tokens, and returns what to keep. The engine marks the connection connected.

## What the engine does for you

Profile and scope resolution, picking connections by name or platform, the **mode gate**
(`draft` connections queue in the Outbox with a notification; `auto` posts now), the
log row with the platform's id and URL (or the error), image re-encoding and the drop
folder, `max_chars`/`max_images`, token refresh and expiry messages, and the "plugin is
off" refusal (checked on every call — no unload event exists).

## Reference for AI

SOCIAL PROVIDERS:
- A provider plugin = manifest `capabilities.social: [{provider, label, icon, module, auth: fields|oauth, capabilities ⊆ {post,image,link,inbox,reply,delete,stats}, config_schema (secret:true → core credentials), max_chars, max_images, public_image_url?, notes}]` + `requires_plugins: ["social"]` + one module. The Social plugin scans manifests itself (own load + every plugins_ready); the loader ignores the key. Example: `core/plugins/social-bluesky`.
- Module exports (only the capabilities declared): `validate(conn)→(ok,msg)`, `post(conn,text,images,link,disclose)→{id,url}`, `inbox(conn,since_epoch)→[{id,kind,author,text,ts,url}]`, `reply(conn,item_id,text)→{id,url}`, `delete(conn,post_id)→bool`, `stats(conn)→dict`, optional `refresh(conn)`; oauth adds `auth_start(conn,redirect_uri,state)→url|(url,stash)` and `auth_finish(conn,redirect_uri,query,stash)→{secrets,meta}`. Raise `plugins.social.errors.SocialError`.
- `images` = [(bytes, media_type)] (JPEG/PNG/GIF) or [url] when `public_image_url`; `link` is None unless the `link` capability is declared; `disclose` = set the platform's AI flag if it has one.
- Connection: `.name .profile .provider .label .mode .disclose .config .secrets .meta .connected`, `.save_secrets(dict)` (str replaces, '' clears, None keeps), `.save_meta(dict)`; repr never shows secrets. Secrets live in core `credentials.plugin_accounts['social']['<profile>/<conn>']`, wiring in `user/plugin_state/social.json`.
- Redirect URI: `/api/plugin/social/callback/<provider>`; CSRF state + stash kept by core for 10 minutes.
- The engine owns: scope/profile resolution, connection picking, draft→Outbox vs auto gate, the log, image re-encoding + drop folder (`SOCIAL_DROP_SCP`/`SOCIAL_DROP_URL`), limits, refresh/expiry, plugin-off refusal.
