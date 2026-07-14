# Manifest Reference

Every plugin needs a `plugin.json` in its root folder.

## Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | — | Unique identifier (overrides the folder name if set; folder name is the fallback) |
| `short_display_name` | string | No | — | **The plugin's display title** in lists & Settings. Keep it **2–4 words** (≤40 chars; clipped past that). This is the ONE field for the name — detail goes in `description`, NOT here. |
| `version` | string | No | — | Semver (`1.0.0`) |
| `description` | string | No | — | One-line summary shown UNDER the title — free prose. **Never used as the title** (that's `short_display_name`). |
| `author` | string | No | — | Author name |
| `url` | string | No | — | Project URL (shown in Settings) |
| `icon` | string | No | — | Emoji icon shown in Settings UI and plugin lists |
| `emoji` | string | No | — | Alias for `icon` (legacy — prefer `icon`) |
| `display_name` | string | No | — | Legacy fallback for `short_display_name` (also an app label). Prefer `short_display_name`. |
| `short_name` | string | No | — | Legacy fallback for `short_display_name`. Prefer `short_display_name`. |
| `priority` | int | No | 50 | Execution order within band (lower = first) |
| `default_enabled` | bool | No | false | Auto-enable on fresh install |
| `essential` | bool\|string | No | false | `true` = core plugin: boot alarms if it fails to load, hidden from the plugin manager, toggle refused. A **string** names an alternates group (e.g. `"memory"` on both memory and mindpalace): visible and switchable, boot alarms only if NO plugin in the group loaded |
| `managed_hide` | bool | No | false | Hide plugin entirely in managed/resale mode |
| `settingsUI` | string\|null | No | `"auto"` | Controls settings panel: `"auto"` (from manifest schema), `"plugin"` (custom JS), `"core"` (hardcoded), or `null` (none) |
| `pip_dependencies` | string[] | No | `[]` | Python packages required (pip specifiers, e.g. `["telethon>=1.34", "requests"]`). Checked before loading; missing deps shown in UI with install option |
| `environment` | object | No | — | Dedicated conda env for heavy deps that can't live in Sapphire's env: `{python, pip, conda, channels}`. Built on user consent as `sapphire-plugin-<name>`; pairs with `capabilities.services`. See [Subprocesses](subprocesses.md) |
| `capabilities` | object | No | — | What the plugin provides (see below) |

### Plugin display title — set `short_display_name`, don't dump prose

The title shown in plugin lists & Settings resolves in this order:
`short_display_name` → `display_name` → `short_name` → (first clause of `description`, **truncated to 40 chars**) → `name`.

**Set `short_display_name`** (2–4 words, e.g. `"Weather"`, `"Home Assistant"`). The `description` fallback is deliberately truncated — it exists only so legacy plugins don't render a paragraph, NOT as a place to name your plugin. If you set just a long `description`, your title becomes a clipped sentence indistinguishable from the next plugin's; set `short_display_name` and it's a clean, short name.

## Capabilities

The `capabilities` object declares what the plugin provides:

```json
{
  "capabilities": {
    "hooks": { ... },
    "voice_commands": [ ... ],
    "tools": [ ... ],
    "scopes": [ ... ],
    "routes": [ ... ],
    "schedule": [ ... ],
    "settings": [ ... ],
    "providers": { ... },
    "memory_layers": [ ... ],
    "web": { ... },
    "daemon": { ... },
    "services": [ ... ],
    "app": { ... },
    "themes": [ ... ],
    "widgets": [ ... ],
    "sidebar_accordion": { ... }
  }
}
```

Each capability is documented in its own guide:
- [Hooks & Voice Commands](hooks.md) — including the `ghost_inject` hook for cache-friendly per-turn context (since 2.6.4)
- [Tools](tools.md)
- Scopes — see below
- [Routes](routes.md)
- [Schedule](schedule.md)
- [Settings & Web UI](settings.md)
- [Providers (TTS, STT, Embedding, LLM)](providers.md)
- [Memory Layers](memory-layers.md) — register your own layer in the Mind Palace memory system
- [Apps](APPS.md)
- [Themes](THEMES.md)
- [Widgets](widgets.md) — dashboard action-panel cards (rendered in Settings → Dashboard)
- [Daemons](daemons.md) — long-running background threads with event sources (e.g. Telegram, Discord listeners)
- [Services & Subprocesses](subprocesses.md) — declared subprocess servers, run by core (optionally in the plugin's own conda env)
- Sidebar Accordion — inject custom HTML panels into the chat sidebar

### Scopes

Scopes let a plugin register data-isolation selectors that appear in the Chat Settings sidebar. Each scope creates a `ContextVar` that your tools can read to know which data partition the current chat is using.

**When to use:** If your plugin stores per-user data (memories, contacts, wallet addresses, accounts) and users might want different data sets for different chats (e.g., "work" vs "personal" email accounts).

**Declare scopes in your manifest:**

```json
{
  "capabilities": {
    "scopes": [
      {
        "key": "email",
        "label": "email",
        "endpoint": "/api/email/accounts",
        "data_key": "accounts",
        "value_field": "address",
        "name_field": "address",
        "label_template": "{address}"
      }
    ]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `key` | Yes | Scope identifier — must be a valid Python identifier. Creates `scope_{key}` ContextVar and `{key}_scope` setting key |
| `label` | Yes | Display label in the Chat Settings sidebar dropdown header |
| `endpoint` | Yes | API endpoint that returns the list of available scope values (for the dropdown) |
| `data_key` | No | Key in the endpoint response JSON that contains the list (default: root array) |
| `value_field` | No | Field in each list item to use as the scope value |
| `name_field` | No | Field in each list item to use as the display name |
| `label_template` | No | Format string for dropdown labels (e.g., `"{name} ({count})"`) |
| `nav_target` | No | View to navigate to when the "+" button is clicked (e.g., `"mind:memories"`) |
| `default` | No | Default scope value (default: `"default"`) |

**Reading the scope in your tool code:**

```python
def _get_current_email_scope():
    from core.chat.function_manager import scope_email
    return scope_email.get()  # returns the value set in Chat Settings
```

The import resolves via `function_manager.__getattr__` — no need to import the ContextVar directly. The scope is automatically set by the chat pipeline before your tool's `execute()` is called.

**Real example:** See `plugins/memory/plugin.json` for 4 scopes (memory, goal, knowledge, people) and `plugins/email/plugin.json` for the email scope.

### Cleanup Paths (Uninstall)

On uninstall, Sapphire automatically deletes:
- `user/plugin_state/{name}.json` and any sibling files/dirs prefixed with `{name}-` or `{name}_`
- `user/webui/plugins/{name}.json` (plugin settings)
- `user/plugins/{name}/` (the plugin itself, user plugins only)

If your plugin writes state files that don't follow the `{name}-*` naming convention, declare them in `capabilities.cleanup_paths`:

```json
"capabilities": {
  "cleanup_paths": ["plugin_state/gcal-csrf.json"]
}
```

Paths are relative to `user/`. **Namespace-restricted** — only paths under `user/plugin_state/` (with a filename starting with the plugin's name), `user/webui/plugins/`, or `user/plugins/{name}/` are honored. Anything else (`chats/`, `memory.db`, `credentials.json`, etc.) is refused with a `REFUSED` warning in the logs — a malicious or buggy manifest cannot delete cross-plugin or top-level user data.

## Priority Bands

Lower fires first. Within each band:

| Range | Purpose |
|-------|---------|
| 0-19 | Critical intercepts (stop, security) |
| 20-49 | Input modification (translation, formatting) |
| 50-79 | Context enrichment (prompt injection, state) |
| 80-99 | Observation (logging, analytics) |

User plugins use the same ranges but shifted to 100-199.

## Directory Structure

```
plugins/                          # System plugins (0-99)
  voice-commands/
    plugin.json
    plugin.sig
    hooks/stop.py
    hooks/reset.py
  ssh/
    plugin.json
    plugin.sig
    tools/ssh_tool.py
    web/index.js

user/
  plugins/                        # User plugins (100-199)
    my-plugin/
      plugin.json
      hooks/handler.py
  plugin_state/                   # Per-plugin JSON state
    ssh.json
  webui/
    plugins.json                  # Enabled list: {"enabled": [...]}
    plugins/                      # Per-plugin settings
      ssh.json
      image-gen.json
```
