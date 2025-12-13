# Plugins

Plugins (also called modules) are keyword-triggered extensions. When the user says a trigger word, the plugin intercepts the message and handles it directly - optionally bypassing the LLM entirely.

Feed this whole file to an AI like Claude, it includes all the details it needs to make plugins.

---

## Plugins vs Tools

| Aspect | Plugins | Tools |
|--------|---------|-------|
| Trigger | User says keyword | AI decides to call |
| Control | Deterministic | AI-driven |
| Bypass LLM | Optional (`skip_llm`) | Never |
| Use case | Commands, shortcuts | AI capabilities |
| Script Launch | Can launch its own script/server | runs code when asked, not ahead |

**Example**: "What time is it" → Plugin intercepts, returns time instantly (no AI).

**Example**: User asks about weather → AI decides to call `web_search` tool.

---

## Locations

| Path | Purpose | Git Tracked |
|------|---------|-------------|
| `core/modules/` | Core system modules | Yes |
| `plugins/` | Shared plugins | Yes |
| `user/plugins/` | Private plugins | No |

Use `user/plugins/` for personal or sensitive plugins you don't want in version control.

---

## Plugin Structure

```
plugins/
└── my_plugin/
    ├── prompt_details.json    # Required: configuration
    ├── my_plugin.py           # Required: implementation
    └── events.json            # Optional: scheduled events
```

**Naming**: Folder name = file name = class name (snake_case for folder/file, PascalCase for class).

```
my_plugin/my_plugin.py → class MyPlugin
time_date/time_date.py → class TimeDate
captain_pike/captain_pike.py → class CaptainPike
```

---

## prompt_details.json

```json
{
    "title": "My Plugin",
    "description": "What this plugin does",
    "version": "1.0.0",
    "keywords": ["trigger", "another trigger", "multi word trigger"],
    "skip_llm": true,
    "exact_match": true,
    "save_to_history": true,
    "auto_start": false,
    "startup_script": null,
    "restart_on_failure": false,
    "startup_order": 0
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Display name |
| `description` | string | What it does |
| `version` | string | Semver version |
| `keywords` | array | Trigger phrases (case-insensitive) |
| `skip_llm` | bool | `true` = return directly, `false` = pass to LLM after |
| `exact_match` | bool | `true` = exact phrase only, `false` = starts-with match |
| `save_to_history` | bool | Log interaction to chat history |
| `auto_start` | bool | Start background service on Sapphire launch |
| `startup_script` | string | Python file to run if `auto_start` is true |
| `restart_on_failure` | bool | Auto-restart crashed background service |
| `startup_order` | int | Startup sequence (lower = earlier) |

### Keyword Matching

**exact_match: true**
- "time" triggers only on exactly "time"
- "what time is it" does NOT trigger

**exact_match: false**
- "time" triggers on "time", "time please", "time in tokyo"
- Remaining text after keyword passed to `process()`

**Multiple keywords**: List alternatives
```json
"keywords": ["what time is it", "time", "what's the time", "current time"]
```

---

## Python Implementation

### Minimal Plugin

```python
# plugins/my_plugin/my_plugin.py

import logging

logger = logging.getLogger(__name__)


class MyPlugin:
    """Brief description."""
    
    def __init__(self):
        self.keyword_match = None   # Set by module_loader
        self.full_command = None    # Set by module_loader
    
    def process(self, user_input, llm_client=None):
        """Handle the triggered command.
        
        Args:
            user_input: Text after keyword (if exact_match=false), or full text
            llm_client: Optional LLM client (if skip_llm=false)
        
        Returns:
            String response to user
        """
        return "Plugin response here"
```

### With System Access

```python
class MyPlugin:
    def __init__(self):
        self.keyword_match = None
        self.full_command = None
        self.voice_chat_system = None
    
    def attach_system(self, voice_chat_system):
        """Called by module_loader to provide system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info("MyPlugin attached to system")
    
    def process(self, user_input, llm_client=None):
        # Access TTS
        if self.voice_chat_system and hasattr(self.voice_chat_system, 'tts'):
            self.voice_chat_system.tts.speak("Hello")
        
        return "Done"
```

### With Active Chat Support

```python
def process(self, user_input, llm_client=None, active_chat=None):
    """Process with chat context.
    
    Args:
        active_chat: Name of current chat session (e.g., "default")
    """
    if active_chat:
        logger.info(f"Processing in chat: {active_chat}")
    
    return "Response"
```

The module_loader auto-detects if your `process()` accepts `active_chat`.

---

## Background Services

For plugins that need persistent processes (servers, watchers, etc.):

```json
{
    "auto_start": true,
    "startup_script": "my_server.py",
    "restart_on_failure": true,
    "startup_order": 1
}
```

**startup_order**: Lower numbers start first. Use this for dependencies (e.g., database before API).

The `startup_script` runs as a subprocess managed by ProcessManager. It should be a standalone Python script in your plugin folder.

---

## Scheduled Events

Plugins can define scheduled actions in `events.json`:

```json
{
    "events": [
        {
            "name": "Daily Report",
            "module": "my_plugin",
            "action": "generate_report",
            "parameters": "daily",
            "schedule": {
                "hours": [9, 17],
                "minutes": [0],
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            },
            "append_to_history": false
        }
    ]
}
```

### Schedule Format

| Field | Type | Description |
|-------|------|-------------|
| `hours` | array | Hours to trigger (0-23). Empty = every hour |
| `minutes` | array | Minutes to trigger (0-59). Empty = every minute |
| `days` | array | Day names. Empty = every day |

**Examples**:
- `{"hours": [6], "minutes": [0], "days": []}` → 6:00 AM daily
- `{"hours": [], "minutes": [0, 30], "days": []}` → Every half hour
- `{"hours": [12], "minutes": [0], "days": ["Monday"]}` → Noon on Mondays

### Event Fields

| Field | Description |
|-------|-------------|
| `name` | Display name for logging |
| `module` | Plugin folder name |
| `action` | Passed to `process()` as `keyword_match` |
| `parameters` | Passed to `process()` as `user_input` |
| `append_to_history` | Add to chat history |

---

## Complete Example

### Folder Structure

```
plugins/
└── greeter/
    ├── prompt_details.json
    ├── greeter.py
    └── events.json
```

### prompt_details.json

```json
{
    "title": "Greeter",
    "description": "Responds to greetings with time-appropriate messages",
    "version": "1.0.0",
    "keywords": ["hello", "hi", "hey", "good morning", "good evening"],
    "skip_llm": true,
    "exact_match": false,
    "save_to_history": true,
    "auto_start": false,
    "startup_script": null,
    "restart_on_failure": false,
    "startup_order": 0
}
```

### greeter.py

```python
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Greeter:
    """Responds to greetings with time-appropriate messages."""
    
    def __init__(self):
        self.keyword_match = None
        self.full_command = None
    
    def process(self, user_input, llm_client=None):
        """Generate a greeting based on time of day."""
        hour = datetime.now().hour
        
        if hour < 12:
            period = "morning"
        elif hour < 17:
            period = "afternoon"
        else:
            period = "evening"
        
        # Check what keyword triggered us
        if self.keyword_match:
            logger.info(f"Triggered by: {self.keyword_match}")
        
        return f"Good {period}! How can I help you?"
```

### events.json (optional)

```json
{
    "events": [
        {
            "name": "Morning Greeting",
            "module": "greeter",
            "action": "morning",
            "parameters": "",
            "schedule": {
                "hours": [8],
                "minutes": [0],
                "days": []
            },
            "append_to_history": false
        }
    ]
}
```

---

## Creating Plugins

### Step 1: Create Folder

```bash
# Shared plugin
mkdir -p plugins/my_plugin

# Private plugin (not tracked by git)
mkdir -p user/plugins/my_plugin
```

### Step 2: Create prompt_details.json

```json
{
    "title": "My Plugin",
    "description": "What it does",
    "version": "1.0.0",
    "keywords": ["trigger word"],
    "skip_llm": true,
    "exact_match": true,
    "save_to_history": true,
    "auto_start": false,
    "startup_script": null,
    "restart_on_failure": false,
    "startup_order": 0
}
```

### Step 3: Create Python File

```python
# my_plugin.py
import logging

logger = logging.getLogger(__name__)


class MyPlugin:
    def __init__(self):
        self.keyword_match = None
        self.full_command = None
    
    def process(self, user_input, llm_client=None):
        return "Response"
```

### Step 4: Restart Sapphire

Plugins load at startup. Restart to pick up new plugins.

---

## Files Reference

| Path | Purpose |
|------|---------|
| `core/modules/` | Core system modules |
| `plugins/` | Shared user plugins |
| `user/plugins/` | Private plugins (gitignored) |
| `core/chat/module_loader.py` | Plugin loading system |
| `core/event_handler.py` | Scheduled event system |

---

## AI Reference: Creating Plugins

When asked to create a plugin, use this template:

### prompt_details.json

```json
{
    "title": "{Title}",
    "description": "{Description}",
    "version": "1.0.0",
    "keywords": ["{keyword1}", "{keyword2}"],
    "skip_llm": true,
    "exact_match": {true|false},
    "save_to_history": true,
    "auto_start": false,
    "startup_script": null,
    "restart_on_failure": false,
    "startup_order": 0
}
```

### {plugin_name}.py

```python
# plugins/{plugin_name}/{plugin_name}.py
"""
{Brief description}
"""

import logging

logger = logging.getLogger(__name__)


class {PluginName}:
    """{Description}"""
    
    def __init__(self):
        self.keyword_match = None
        self.full_command = None
        self.voice_chat_system = None
    
    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
    
    def process(self, user_input, llm_client=None):
        """Process the command.
        
        Args:
            user_input: Text after keyword (or full command if exact_match)
            llm_client: LLM client if skip_llm is false
        
        Returns:
            String response to speak/display
        """
        logger.info(f"{PluginName} processing: '{user_input}'")
        
        # Implementation here
        
        return "Response"
```

### Checklist

- [ ] Folder name matches file name (snake_case)
- [ ] Class name is PascalCase of folder name
- [ ] `prompt_details.json` has all required fields
- [ ] `process()` method exists and returns string
- [ ] Keywords are lowercase (matching is case-insensitive)
- [ ] `exact_match` set appropriately for use case
- [ ] `skip_llm` set based on whether AI processing needed
- [ ] Logging uses module logger