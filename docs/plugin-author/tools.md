# Plugin Tools

Plugin tools are registered with the function manager and the AI calls them like any built-in tool. The format is the same across all plugins (see `plugins/memory/tools/` for examples of core tools, or `plugins/email/tools/` for a plugin tool).

For simple tool creation without a full plugin, see [TOOLMAKER.md](../TOOLMAKER.md).

---

## Tool File Format

```python
ENABLED = True
EMOJI = '🔧'
AVAILABLE_FUNCTIONS = ['my_tool_do_thing']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "my_tool_do_thing",
            "description": "Does the thing",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "What to do it to"
                    }
                },
                "required": ["target"]
            }
        }
    }
]

def execute(function_name, arguments, config, plugin_settings=None, credentials=None):
    """Called by function manager.

    The function manager inspects your signature and passes what you accept:
    3 args (function_name, arguments, config), or add a 4th (plugin_settings —
    this plugin's stored settings dict) and/or a 5th (credentials — the
    credentials manager). Declare only what you need; 3-arg is the minimum.

    Args:
        function_name: Which function was called
        arguments: Dict of parameters
        config: System config
        plugin_settings: This plugin's saved settings (4th arg, optional)
        credentials: Credentials manager for resolving secrets (5th arg, optional)

    Returns:
        (message: str, success: bool) tuple
    """
    if function_name == "my_tool_do_thing":
        target = arguments.get("target", "")
        return f"Did the thing to {target}", True
    return "Unknown function", False
```

### Required Exports

| Export | Type | Description |
|--------|------|-------------|
| `ENABLED` | bool | Whether tool is active |
| `EMOJI` | str | Display icon |
| `AVAILABLE_FUNCTIONS` | list | Function names this file provides |
| `TOOLS` | list | OpenAI-compatible function schemas |
| `execute()` | function | Dispatcher — returns `(message, success)` |
| `get_tools()` | function | *Optional.* Returns `TOOLS`-shaped schemas built from current settings — enables [dynamic descriptions](#dynamic-tool-descriptions) |

### Manifest Declaration

```json
"capabilities": {
  "tools": ["tools/my_tool.py"]
}
```

---

## Schema Flags

Inside each tool's schema dict:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `is_local` | bool/str | — (unset) | Locality declaration: `True` = touches only this machine or the LAN, `"endpoint"` = calls an external API, `False` = network required. **This flag gates private chats**: in a private chat only `is_local: True` tools run; `False`/`"endpoint"` are refused with a message to the AI, and an *unset* flag is refused too unless the user opts in (Settings > Privacy → allow unflagged tools). Declare it honestly on every tool |
| `network` | bool | `false` | UI labeling: marks the tool as network-dependent so toolset lists and system status can show a "network tools" indicator. Informational — it doesn't block or route anything |
| `hidden` | bool | `false` | Hide from the Toolsets UI and from `all`/module/custom selection. The tool still registers and executes; a saved toolset that names it resolves it normally. For internal verbs (e.g. gated sub-agent tools) that would clutter the picker |

```python
TOOLS = [{
    "type": "function",
    "is_local": "endpoint",   # calls an external API — refused in private chats
    "network": True,           # shown as a network tool in the UI
    "function": { ... }
}]
```

**Neither flag routes traffic.** Routing is decided by the network facade: when the SOCKS proxy is on, all WAN traffic from the process rides the proxy regardless of these flags, and LAN traffic goes direct. See [Networking from Plugins](#networking-from-plugins).

---

## Returning Images

A tool that wants the model (and the user) to see a picture returns the images contract instead of a string:

```python
from core import images as ci

def execute(function_name, arguments, config):
    raw = capture()                       # bytes of any image format
    return ci.result("Here's the camera.", [ci.for_chat(raw)]), True
```

- `ci.result(text, images, display_only=False)` builds `{"text", "images": [{data, media_type, display_only}]}`. `display_only=True` = the user sees it, the model never does.
- The user sees every image forever; the model sees an image the turn it arrives, and again for the next **Images › Image memory turns** turns (default 3 — the vision window replays exactly what it saw live). Older images it re-views by handle.
- Core saves each image to the chat's `tool_images` table (encrypted for vaulted chats), prepends the UI marker, and appends an **`(image img:<id>)` receipt line** to your text. Any image tool accepts that handle — `ci.resolve("img:<id>")` gives you the bytes; she hands it to `memory_view_image(image_id=)`, `memory_save_image`, `telegram_send_image`.
- `ci.for_chat(raw)` is the one resize (EXIF-upright, ≤1536px, JPEG); `ci.contact_sheet(list_of_raw)` the one numbered grid. Don't write your own.
- `ci.resolve(source)` understands `img:<id>`, `doc:<N>` (Mind Palace library image), an absolute path, or an `http(s)` URL (fetched through `core.net`, 20MB cap). Raises `ci.ImageError` with a user-readable message.
- **Many pictures, one image**: show the model ONE contact sheet and keep the individuals as handles — `h = ci.stash(raw)` stores the bytes in the chat's image store now (same table, vault, cascade and route as every tool image; `visible=False` so the vision window never replays it) and returns `img:<id>`. Name the handle in your numbered text (`3. title — img:abc.jpg`) so she can look again (`memory_view_image(image_id=)`) or `memory_save_image` any one of them. Never cache image bytes anywhere else.
- Tiles for the user without spending model tokens: append ONE marker, `<!--GALLERY:{"title": "…", "items": [{"handle": "img:<id>", "full": "<lightbox url>", "title": "…", "page": "<source url>"}, ...]}-->`. Tiles with a `handle` are served from the store (vault-aware, offline); an item may carry `thumb` instead for a URL you already serve. Items render as a numbered row — number them the same way as your sheet. The older array forms (`[{thumb, full, title, page}]`, `["url", …]`) still render. Browser-facing external image URLs go through `ci.proxied(url)` — the browser never hot-links a third party. The marker is stripped from the model's copy.

## Networking from Plugins

Make HTTP calls through the network facade instead of bare `requests`:

```python
from core import net

r = net.get(url, timeout=10)       # requests-shaped: get / post / put / delete / request
s = net.session_for(url)           # pooled session for loops (lane-fixed — one per URL)
s = net.wan_session()              # WAN-lane session when you have no URL yet (browser profile)
```

The facade classifies each URL's host as LAN or WAN — syntactically, without ever resolving DNS:

- **LAN** (loopback, private-range IPs, `*.local` / `*.lan` / `*.home.arpa`, single-label hostnames, registered direct hosts) goes direct: no proxy, environment ignored. Redirects are refused by default on the LAN lane so a LAN device's 30x can't hop off-proxy silently — pass `allow_redirects=True` to opt in.
- **WAN** (every real FQDN and public IP) honors the proxy environment: proxied while SOCKS is on, plain direct when it's off. Errors never fall back across lanes — a dead proxy fails loudly instead of leaking direct.

If your plugin talks to LAN gear through a public-looking hostname (a DDNS name, an FQDN that resolves inside the house), register it so it classifies as LAN:

```python
from core.socks_proxy import register_direct_hosts

register_direct_hosts(lambda: ["ha.my-house.example.net"], owner="my-plugin")
```

The provider is a zero-arg callable returning hostname strings, keyed by `owner` (use your plugin name) — re-registering replaces rather than accumulates, and core unregisters it when your plugin unloads. Raw `requests` callers still work (a process-wide proxy-environment belt covers them while SOCKS is on), but the facade is the exact lane; the belt is the floor.

---

## Multi-Account Scope Support

Tools that support multiple accounts (email, bitcoin, etc.) can read the active scope:

```python
from core.chat.function_manager import scope_email

def execute(function_name, arguments, config):
    account = scope_email.get()  # returns active account name (ContextVar)
    creds = load_credentials(account)
    # ... use account-specific credentials
```

Available scope ContextVars: `scope_rag` and `scope_private` are always present (core). The rest — `scope_email`, `scope_bitcoin`, `scope_knowledge`, `scope_memory`, `scope_people`, `scope_goal`, `scope_github`, etc. — resolve via `__getattr__` against the scope registry and only exist while the owning plugin (memory, email, bitcoin, github…) is loaded, so importing one is safe from a tool in that same plugin.

---

## Reading Plugin Settings

Tools can load their own plugin's settings:

```python
import json
from pathlib import Path

def _load_settings():
    path = Path("user/webui/plugins/my-plugin.json")
    if path.exists():
        return json.loads(path.read_text())
    return {}
```

Or via the plugin loader (merges with manifest defaults):

```python
from pathlib import Path
import json

DEFAULTS = {"timeout": 30, "max_results": 10}

def _load_settings():
    path = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "plugins" / "my-plugin.json"
    settings = DEFAULTS.copy()
    if path.exists():
        try:
            user = json.loads(path.read_text())
            settings.update(user)
        except Exception:
            pass
    return settings
```

---

## Dynamic Tool Descriptions

A tool's `description` is what the AI reads to decide how to use it — so it's often
useful to build it from the plugin's own settings (a user-configured name, mode, target,
etc.). Define an optional **`get_tools()`** function that returns the same shape as
`TOOLS`, built from current settings:

```python
def _build_description(cfg):
    base = "Generate an image. Describe the scene or action in ~20 words."
    name = (cfg.get("ai_name") or "").strip()
    if name:
        base += f" Write '{name}' for yourself — the appearance is filled in automatically."
    return base

def get_tools():
    cfg = _load_settings()                      # your settings reader (see above)
    return [{"type": "function", "function": {
        "name": "generate_image",
        "description": _build_description(cfg),
        "parameters": { ... },
    }}]

# Static fallback — used if get_tools() is absent or raises.
TOOLS = get_tools()
```

How it behaves:

- **At load**, the function manager calls `get_tools()` (when present) instead of reading
  the static `TOOLS` list, so the schema is correct from the first request.
- **On a settings save**, the function manager re-runs `get_tools()` and copies the fresh
  `description` / `parameters` onto the **live tool objects in place** — the AI sees the
  new description on its next turn with **no plugin reload and no restart**. Nothing is
  re-exec'd, so module-level state (DB handles, locks, ContextVars) is preserved.
- Only `description` and `parameters` are refreshed; the tool **name never changes**
  (toolset membership and dispatch are keyed on it). Adding or removing tools still
  requires a full reload.

Always keep a static `TOOLS` as the fallback. Plugins without `get_tools()` are
unaffected — the settings-save refresh is a clean no-op for them.

---

## Plugin State

Each plugin gets a persistent JSON key-value store at `user/plugin_state/{name}.json`:

```python
from core.plugin_loader import plugin_loader

state = plugin_loader.get_plugin_state("my-plugin")
state.get("counter", 0)        # read
state.save("counter", 42)      # write (auto-persists)
state.delete("counter")        # remove key
state.all()                    # entire dict
state.clear()                  # wipe everything
```

PluginState is thread-safe — daemon threads, continuity tasks, and API handlers can all read/write the same plugin's state concurrently without data loss.

### Chat-Scoped State

For data that belongs to one *chat* — a playthrough, a per-conversation cache, a journal — use the chat-scoped store instead. Its rows live in the chat database and follow the chat: renamed with it, **encrypted with it** when it goes private, and deleted with it.

```python
state = plugin_loader.get_chat_state("my-plugin")

state.put(chat, "save", {...})       # single slot per key
state.get(chat, "save", default)
state.append(chat, "journal", event) # ordered rows — O(1) append, returns seq
state.append_many(chat, "journal", evs) # several rows in ONE transaction — all land or none; returns their seqs
state.read_all(chat, "journal")      # every row for that key, in order
state.replace(chat, "journal", rows) # atomic renumber (revert/rewrite)
state.delete(chat, "journal")        # or delete(chat) for everything
state.keys(chat)
state.get_all_chats("save")          # {chat: value} across visible chats
state.meta(chat, "journal")          # {'rows': n, 'updated_at': iso} or None
```

Values are JSON-serializable objects. On a **hidden** chat (private + vault sealed), reads come back empty and writes **raise** — never a silent drop, so handle the exception rather than falling back to a file. Use this instead of writing chat-keyed files under `user/`: file paths leak chat names and don't encrypt.

Deleting a chat deletes these rows with it, for public and private chats alike — core does it inside the delete transaction, before `chat_deleted` fires. Don't rebuild an archive-on-delete around it: a private chat's data must not outlive the chat. Keep never-erase behaviour *inside* a living chat (extra keys, extra rows) instead. See [Private chats & `privacy_aware`](hooks.md#private-chats--privacy_aware).

For heavier storage, plugins can create their own SQLite database.

---

## Advanced Patterns

### Privacy-First Design

Never expose raw credentials (emails, keys, addresses) to the AI. Resolve at execution time:

```python
# BAD — AI sees raw email addresses
def execute(function_name, arguments, config):
    return f"Contacts: alice@example.com, bob@example.com", True

# GOOD — AI only sees names and IDs
def execute(function_name, arguments, config):
    contacts = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    return json.dumps(contacts), True
```

### Command Blacklists

For tools that execute commands (SSH, shell):

```python
BLACKLIST = ["rm -rf /", "mkfs", "dd if=/dev", ":(){ :|:& };:"]

def _check_blacklist(command):
    for pattern in BLACKLIST:
        try:
            if re.search(pattern, command):
                return f"Blocked: matches '{pattern}'"
        except re.error:
            if pattern in command:
                return f"Blocked: contains '{pattern}'"
    return None
```

### Caching with Scope Keys

For tools that fetch external data, cache per-scope with TTL:

```python
_cache = {}
CACHE_TTL = 60

def _get_cached(scope):
    entry = _cache.get(scope)
    if entry and time.time() - entry["timestamp"] < CACHE_TTL:
        return entry["data"]
    return None

def _invalidate(scope):
    _cache.pop(scope, None)
```

Tools are added to toolsets and the AI calls them contextually. See [TOOLS.md](../TOOLS.md) for the user-facing tools guide.

---

## Reference for AI

- Tool file exports: `ENABLED`, `EMOJI`, `AVAILABLE_FUNCTIONS`, `TOOLS`, `execute(function_name, arguments, config, plugin_settings=None, credentials=None)` → `(message: str, success: bool)`. Signature is inspected — declare 3, 4, or 5 params; extras are passed only if accepted. Optional `get_tools()` returns TOOLS-shaped schemas from current settings.
- Schema flags: `is_local` `True|False|"endpoint"` gates PRIVATE chats only (True runs; False/"endpoint" refused; unset refused unless `PRIVATE_ALLOW_UNFLAGGED_TOOLS`); `network: true` = UI "network tools" labeling only; `hidden: true` = out of the Toolsets picker but still registered and callable. No flag routes traffic.
- Networking: `from core import net` — `net.get/post/put/delete/request(url, ...)` (requests-shaped), `net.session_for(url)` (pooled, lane-fixed), `net.wan_session()` (WAN lane, browser profile). Host classification is syntactic, never resolves DNS: LAN = loopback / RFC1918 / link-local / `*.local` / `*.lan` / `*.home.arpa` / single-label names / registered direct hosts → direct, redirects refused by default; WAN = everything else → proxy env when SOCKS is on. Register LAN FQDNs: `core.socks_proxy.register_direct_hosts(zero_arg_callable, owner=plugin_name)`; keyed by owner (replace-on-reregister), auto-unregistered on plugin unload.
- `get_tools()` live refresh: re-run on settings save; only `description`/`parameters` update in place; tool names never change; add/remove needs a reload. Keep a static `TOOLS` fallback.
- PluginState (`plugin_loader.get_plugin_state(name)`): `get/save/delete/all/clear`; thread-safe; backed by `user/plugin_state/{name}.json`.
- PluginChatState (`plugin_loader.get_chat_state(name)`): `put/get` (slot), `append` (returns seq), `append_many` (one transaction, all-or-none, returns seqs), `read_all`, `replace` (atomic renumber), `delete(chat[, key])`, `keys`, `get_all_chats(key)`, `meta`. Rows ride rename/vault/delete with the chat; on a hidden (sealed) chat reads return empty and writes RAISE. Rows survive a chat *clear* (register the `chat_cleared` hook to drop turn-anchored ones).
- Scopes: `from core.chat.function_manager import scope_email` (etc.) — resolves via `__getattr__` against the scope registry; exists only while the owning plugin is loaded. `scope_rag` and `scope_private` are always present (core).
