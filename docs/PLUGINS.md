# Backend Plugins

Plugins are keyword-triggered extensions that intercept messages when you say a trigger word. They can bypass the AI entirely for instant responses (like "what time is it") or do custom processing before passing to the AI.

This page covers **backend plugins** (Python modules that run server-side). Frontend plugins are separate JavaScript extensions in the web UI.

## What Are Plugins?

When you say a trigger keyword, Sapphire checks if any plugin is listening for that word. If so, the plugin handles it directly instead of (or before) the AI.

**Example:** Say "example" → the example plugin intercepts, returns the time instantly, no AI involved.

**Plugins vs Tools:**
- **Plugins**: YOU trigger them by saying keywords. Deterministic, predictable.
- **Tools**: The AI decides to call them. AI-driven, contextual.

## Managing Plugins

### Locations

| Path | Purpose | Git Tracked |
|------|---------|-------------|
| `plugins/` | Shared plugins | Yes |
| `user/plugins/` | Your private plugins | No |

### Enable/Disable

Plugins load automatically on startup. To disable a plugin:
- **Temporary**: Move its folder out of `plugins/`
- **Permanent**: Delete the folder
- **Global off**: Set `PLUGINS_ENABLED = False` in config.py

### Included Plugins

- **example** - Says "Example received at [time]" when you say "example"

You can delete it - it's just an example. Sapphire works fine without any plugins.

## Creating Plugins with AI

Copy this prompt to Claude, ChatGPT, or any AI assistant:

**AI Prompt for Plugin Creation:**

> Create a Sapphire plugin that [describe what you want].
>
> **Plugin requirements:**
> - Folder: `plugins/{plugin_name}/` containing `prompt_details.json` and `{plugin_name}.py`
> - Class name: PascalCase of folder name (e.g., `my_plugin` → `MyPlugin`)
> - Must have `process(self, user_input, llm_client=None)` method that returns a string
> - `prompt_details.json` needs: title, description, version, keywords array, skip_llm (bool), exact_match (bool), save_to_history (bool)
>
> **Example prompt_details.json:**
> ```json
> {
>     "title": "My Plugin",
>     "description": "What it does",
>     "version": "1.0.0",
>     "keywords": ["trigger", "another trigger"],
>     "skip_llm": true,
>     "exact_match": true,
>     "save_to_history": true,
>     "auto_start": false,
>     "startup_script": null,
>     "restart_on_failure": false,
>     "startup_order": 0
> }
> ```
>
> **Example Python:**
> ```python
> import logging
> from datetime import datetime
>
> logger = logging.getLogger(__name__)
>
> class MyPlugin:
>     def __init__(self):
>         self.keyword_match = None
>         self.full_command = None
>     
>     def process(self, user_input, llm_client=None):
>         return "Plugin response here"
> ```
>
> Give me both files complete, ready to drop in.

---

After the AI gives you the files:
1. Create the folder: `plugins/your_plugin_name/`
2. Save the two files inside
3. Restart Sapphire

## Technical Reference

This section is for developers and AI assistants creating plugins. It's condensed - modern AIs don't need verbose docs.

### Structure

```
plugins/
└── my_plugin/
    ├── prompt_details.json    # Config (required)
    └── my_plugin.py           # Implementation (required)
```

**Naming convention:** Folder name = file name = class name.
- `my_plugin/my_plugin.py` → `class MyPlugin`
- `time_date/time_date.py` → `class TimeDate`

### prompt_details.json Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| title | string | Yes | Display name |
| description | string | Yes | What it does |
| version | string | Yes | Semver |
| keywords | array | Yes | Trigger phrases (case-insensitive) |
| skip_llm | bool | Yes | true=return directly, false=pass to AI after |
| exact_match | bool | Yes | true="time" only, false="time in tokyo" also works |
| save_to_history | bool | Yes | Log to chat history |
| auto_start | bool | No | Start background service on launch |
| startup_script | string | No | Python file for auto_start |
| restart_on_failure | bool | No | Auto-restart crashed service |
| startup_order | int | No | Lower = earlier startup |

### Python Class Requirements

**Minimum:**
```python
class MyPlugin:
    def __init__(self):
        self.keyword_match = None    # Set by loader (which keyword triggered)
        self.full_command = None     # Set by loader (full user text)
    
    def process(self, user_input, llm_client=None):
        """Returns string response."""
        return "Response"
```

**With system access:**
```python
def attach_system(self, voice_chat_system):
    """Called by loader. Access TTS, settings, etc."""
    self.voice_chat_system = voice_chat_system
```

**With active chat:**
```python
def process(self, user_input, llm_client=None, active_chat=None):
    """Loader auto-detects if you accept active_chat."""
    return "Response"
```

### Keyword Matching

- **exact_match: true** - "time" triggers ONLY on exactly "time"
- **exact_match: false** - "time" triggers on "time please", "time in tokyo", etc.
  - Remaining text after keyword is passed to `process()` as `user_input`

Multiple keywords: `["what time is it", "time", "current time"]`

### Background Services

For plugins needing persistent processes:

```json
{
    "auto_start": true,
    "startup_script": "my_server.py",
    "restart_on_failure": true,
    "startup_order": 1
}
```

The script runs as a subprocess via ProcessManager. Lower `startup_order` = starts first.

### Files Reference

| Path | Purpose |
|------|---------|
| `plugins/` | Shared plugins |
| `user/plugins/` | Private plugins (gitignored) |
| `core/modules/` | Core system modules (same structure) |
| `core/chat/module_loader.py` | Plugin loading |

## Reference for AI

Plugins are keyword-triggered extensions. Different from tools (AI-called functions).

PLUGIN VS TOOL:
- Plugin: USER triggers with keyword ("backup", "time")
- Tool: AI decides to call based on context

BUILT-IN PLUGINS:
- backup: Keyword "backup" - creates user data backup
- stop: Keyword "stop" - halts TTS playback
- reset: Keyword "reset" - clears chat history
- time_date: Keyword "what time is it" - returns current time

PLUGIN LOCATIONS:
- plugins/ - Shared plugins (git tracked)
- user/plugins/ - Custom plugins (gitignored)
- core/modules/ - Core system modules (same structure)

ENABLE/DISABLE:
- Settings > System > PLUGINS_ENABLED (global toggle)
- Individual plugins: set "enabled": false in prompt_details.json

CREATING PLUGINS:
- Need: plugin_name.py + prompt_details.json in folder
- prompt_details.json defines keywords and metadata
- plugin.py has process(user_input) function
- Feed PLUGINS.md to AI to generate new ones

TROUBLESHOOTING:
- Plugin not triggering: Check keyword in prompt_details.json, check PLUGINS_ENABLED=true
- "Module not found": Check plugin folder structure, look for import errors in logs
- Keyword conflict: Multiple plugins with same keyword - first loaded wins