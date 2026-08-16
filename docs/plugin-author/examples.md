# Plugin Examples — Party Sampler

Real working examples of every plugin capability type, stripped to the minimum.
Use these as templates. Each section is a complete working plugin.

---

## 1. Tools Plugin (simplest)

Two files. The AI gets new callable functions.

**plugin.json:**
```json
{
  "name": "my-tools",
  "version": "1.0.0",
  "description": "Example tools plugin",
  "author": "you",
  "capabilities": {
    "tools": ["tools/my_tools.py"]
  }
}
```

**tools/my_tools.py:**
```python
ENABLED = True
EMOJI = '🔧'
AVAILABLE_FUNCTIONS = ['greet', 'add_numbers']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "greet",
            "description": "Greet someone by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Who to greet"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "add_numbers",
            "description": "Add two numbers",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"}
                },
                "required": ["a", "b"]
            }
        }
    }
]

def execute(function_name, arguments, config):
    if function_name == "greet":
        return f"Hello, {arguments.get('name', 'world')}!", True
    elif function_name == "add_numbers":
        return f"{arguments['a']} + {arguments['b']} = {arguments['a'] + arguments['b']}", True
    return "Unknown function", False
```

---

## 2. Hook Plugin (intercept the chat pipeline)

**plugin.json:**
```json
{
  "name": "my-hook",
  "version": "1.0.0",
  "description": "Injects context into every prompt",
  "author": "you",
  "capabilities": {
    "hooks": {
      "prompt_inject": "hooks/inject.py"
    }
  }
}
```

**hooks/inject.py:**
```python
def prompt_inject(event):
    """Append extra context to the system prompt."""
    event.context_parts.append("The user's favorite color is blue.")
```

Hook points: `post_stt`, `pre_chat`, `prompt_inject`, `ghost_inject`, `post_llm`, `post_chat`, `pre_execute`, `post_execute`, `pre_tts`, `post_tts`, `on_wake`, `chat_renamed`, `chat_deleted`, `chat_vaulted`, `plugins_ready`

In a private chat your hooks are withheld unless the manifest declares top-level `"privacy_aware": true` — see [Private chats](hooks.md#private-chats--privacy_aware).

Key fields on HookEvent:
- `event.input` — user message (mutable in pre_chat)
- `event.response` — AI response (mutable in post_llm)
- `event.skip_llm = True` — bypass LLM entirely
- `event.ephemeral = True` — don't persist to chat history
- `event.context_parts` — list of strings appended to system prompt
- `event.stop_propagation = True` — prevent lower-priority hooks from firing
- `event.metadata.get("system")` — access to VoiceChatSystem (pre_chat, post_chat, pre_execute only)

---

## 3. Voice Command Plugin (instant keyword response)

**plugin.json:**
```json
{
  "name": "my-voice-cmd",
  "version": "1.0.0",
  "description": "Custom voice commands",
  "author": "you",
  "capabilities": {
    "voice_commands": [
      {
        "triggers": ["lights on", "turn on lights"],
        "match": "exact",
        "bypass_llm": true,
        "handler": "hooks/lights.py"
      }
    ]
  }
}
```

**hooks/lights.py:**
```python
def pre_chat(event):
    # Do something (call an API, toggle a device, etc.)
    event.skip_llm = True
    event.ephemeral = True
    event.response = "Lights turned on."
```

Match types: `exact`, `starts_with`, `contains`, `regex`

---

## 4. TTS Provider Plugin (add a speech engine)

**plugin.json:**
```json
{
  "name": "my-tts",
  "version": "1.0.0",
  "description": "Custom TTS provider",
  "author": "you",
  "settingsUI": null,
  "capabilities": {
    "providers": {
      "tts": {
        "key": "my_tts",
        "display_name": "My TTS Engine",
        "entry": "provider.py",
        "class_name": "MyTTSProvider",
        "requires_api_key": false
      }
    }
  }
}
```

**provider.py:**
```python
import logging
from core.tts.providers.base import BaseTTSProvider

logger = logging.getLogger(__name__)

class MyTTSProvider(BaseTTSProvider):
    audio_content_type = 'audio/wav'
    SPEED_MIN = 0.5
    SPEED_MAX = 2.0

    def generate(self, text, voice=None, speed=1.0, **kwargs):
        """Return audio bytes or None on failure."""
        if not text or not text.strip():
            return None
        try:
            # Your TTS generation here — subprocess, API call, etc.
            # Must return raw audio bytes (WAV, MP3, etc.)
            import subprocess
            result = subprocess.run(
                ['your-tts-command', '--text', text],
                capture_output=True, timeout=30
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            logger.error(f"TTS generation failed: {e}")
            return None

    def is_available(self):
        """Return True if the TTS engine is installed/accessible."""
        try:
            import subprocess
            subprocess.run(['your-tts-command', '--version'],
                          capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def list_voices(self):
        """Return available voices as [{"voice_id": "id", "name": "Display Name"}].
        voice_id is what gets passed to generate(voice=). name is for the UI."""
        return [
            {"voice_id": "default", "name": "Default Voice"},
        ]
```

**Important rules for TTS providers:**
- `voice_id` in `list_voices()` must be the actual identifier your engine uses (language code, model name, etc.) — NOT a display name. The `name` field is what the UI shows.
- Audio output goes through an Opus encoder. **WAV audio MUST be at an Opus-compatible sample rate: 8000, 12000, 16000, 24000, or 48000 Hz.** If your engine outputs a different rate (e.g., 22050Hz), you must resample to 24000Hz before returning the bytes. Use `audioop.ratecv()` + `wave` module for resampling.
- MP3 audio (like gTTS/ElevenLabs) is handled separately and does not have this constraint.
- The `voice` parameter passed to `generate()` comes from the voice picker UI. It will be a `voice_id` from your `list_voices()` return. Make sure the mapping is correct.

Provider types: `tts`, `stt`, `embedding`, `llm` — same manifest pattern, different base classes.

---

## 5. Settings (manifest-declared, auto-rendered)

Add to any plugin's capabilities. No JS needed — Sapphire renders the form automatically.

```json
{
  "capabilities": {
    "settings": [
      {
        "key": "api_url",
        "type": "string",
        "label": "API URL",
        "default": "http://localhost:8080",
        "help": "The endpoint to connect to"
      },
      {
        "key": "timeout",
        "type": "number",
        "label": "Timeout (seconds)",
        "default": 30,
        "min": 5,
        "max": 120
      },
      {
        "key": "mode",
        "type": "string",
        "label": "Mode",
        "widget": "select",
        "default": "normal",
        "options": [
          {"label": "Normal", "value": "normal"},
          {"label": "Fast", "value": "fast"},
          {"label": "Precise", "value": "precise"}
        ]
      },
      {
        "key": "instructions",
        "type": "string",
        "widget": "textarea",
        "label": "Custom Instructions",
        "default": "",
        "rows": 6
      }
    ]
  }
}
```

Read settings in your code:
```python
from core.plugin_loader import plugin_loader
settings = plugin_loader.get_plugin_settings('my-plugin')
url = settings.get('api_url', 'http://localhost:8080')
```

---

## 6. Daemon (background thread with events)

**plugin.json:**
```json
{
  "name": "my-daemon",
  "version": "1.0.0",
  "description": "Background monitor with event triggers",
  "author": "you",
  "capabilities": {
    "daemon": {
      "entry": "daemon.py",
      "event_sources": [
        {
          "name": "my_daemon_event",
          "label": "My Daemon Event",
          "description": "Fires when something happens"
        }
      ]
    }
  }
}
```

**daemon.py:**
```python
import logging
import threading
import time

logger = logging.getLogger(__name__)
_thread = None
_stop = threading.Event()

def start(plugin_loader, settings):
    """Called when plugin loads. Start your background thread."""
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_monitor_loop, args=(plugin_loader,), daemon=True)
    _thread.start()
    logger.info("[my-daemon] Started")

def stop():
    """Called when plugin unloads. Clean shutdown."""
    _stop.set()
    logger.info("[my-daemon] Stopped")

def _monitor_loop(plugin_loader):
    while not _stop.is_set():
        # Check for something interesting
        something_happened = False  # your detection logic

        if something_happened:
            import json
            plugin_loader.emit_daemon_event(
                "my_daemon_event",
                json.dumps({"text": "Something happened!", "detail": "..."})
            )

        _stop.wait(30)  # check every 30 seconds
```

Daemon events trigger continuity tasks. Users create a task with the daemon event source, and Sapphire responds when the event fires.

---

## 7. Routes (custom API endpoints)

**plugin.json:**
```json
{
  "name": "my-routes",
  "version": "1.0.0",
  "description": "Custom API endpoints",
  "author": "you",
  "capabilities": {
    "routes": [
      {"method": "GET", "path": "status", "handler": "routes/api.py:get_status"},
      {"method": "POST", "path": "action", "handler": "routes/api.py:do_action"}
    ]
  }
}
```

**routes/api.py:**
```python
import json

def get_status(body=None, settings=None, **_):
    """GET /api/plugin/my-routes/status — auto-authenticated, CSRF-protected."""
    return {"status": "ok", "uptime": 12345}

def do_action(body=None, settings=None, **_):
    """POST /api/plugin/my-routes/action"""
    action = (body or {}).get("action", "default")
    return {"result": f"Did {action}"}
```

Routes are auto-mounted at `/api/plugin/{plugin-name}/{path}`. Auth and CSRF enforced by the framework.

---

## 8. Schedule (cron tasks)

**plugin.json:**
```json
{
  "name": "my-schedule",
  "version": "1.0.0",
  "description": "Scheduled tasks",
  "author": "you",
  "capabilities": {
    "schedule": [
      {
        "name": "Daily Check",
        "cron": "0 9 * * *",
        "description": "Run a daily check at 9am",
        "handler": "schedule/daily.py"
      }
    ]
  }
}
```

**schedule/daily.py:**
```python
import logging
logger = logging.getLogger(__name__)

def run(event):
    """Called by the continuity scheduler on cron match."""
    system = event.get("system")
    config = event.get("config")
    task = event.get("task")
    state = event.get("plugin_state")  # persistent key-value store

    # Do your work here
    logger.info("[my-schedule] Daily check running")

    # Save state for next run
    if state:
        state.save("last_run", "2026-04-12")
```
