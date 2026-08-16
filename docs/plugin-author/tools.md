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
| `is_local` | bool/str | `True` | `True` = runs locally, `"endpoint"` = calls external API, `False` = network required |
| `network` | bool | `false` | Mark as network-dependent (tracked by function manager) |
| `hidden` | bool | `false` | Hide from the Toolsets UI and from `all`/module/custom selection. The tool still registers and executes; a saved toolset that names it resolves it normally. For internal verbs (e.g. gated sub-agent tools) that would clutter the picker |

```python
TOOLS = [{
    "type": "function",
    "is_local": "endpoint",   # calls Home Assistant API
    "network": True,           # needs network access
    "function": { ... }
}]
```

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
