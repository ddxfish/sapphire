# Voice Commands

Voice commands are keyword-triggered actions that bypass the LLM entirely. They fire in the `pre_chat` stage — fast, deterministic, no AI involved.

Use them for instant reactions: stop playback, toggle modes, trigger macros, control devices.

## Manifest Declaration

```json
"capabilities": {
  "voice_commands": [
    {
      "triggers": ["stop", "halt", "be quiet"],
      "match": "exact",
      "bypass_llm": true,
      "handler": "hooks/stop.py",
      "description": "Stop TTS and cancel generation"
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `triggers` | Phrases to match (case-insensitive) |
| `match` | `exact`, `starts_with`, `contains`, or `regex` |
| `bypass_llm` | If true, runs at the top of the plugin's priority band — 0–19 for bundled plugins, top of the user band for installed plugins |
| `handler` | Path to handler file |

Multiple voice commands per plugin is fine — they're an array.

## Match Modes

| Mode | Behavior | Example trigger | Matches |
|------|----------|----------------|---------|
| `exact` | Full input must match | `"stop"` | "stop" only |
| `starts_with` | Input begins with trigger | `"lights"` | "lights on", "lights off", "lights dim" |
| `contains` | Trigger appears anywhere | `"weather"` | "what's the weather", "weather today" |
| `regex` | Regular expression | `"lights? (on\|off)"` | "light on", "lights off" |

All matching is case-insensitive.

## Handler

Voice command handlers are `pre_chat` hooks. The key pattern: set `skip_llm`, `ephemeral`, and `stop_propagation` to handle the command without involving the AI.

```python
def pre_chat(event):
    system = event.metadata.get("system")
    if system and hasattr(system, "tts") and system.tts:
        system.tts.stop()
    event.skip_llm = True
    event.ephemeral = True
    event.response = "Stopped."
    event.stop_propagation = True
```

| Field | Purpose |
|-------|---------|
| `skip_llm = True` | Don't send to AI |
| `ephemeral = True` | Don't save to chat history |
| `response = "..."` | What to show/speak |
| `stop_propagation = True` | Don't run lower-priority hooks |

## Examples

**Stop command — cancel TTS and generation:**
```python
def pre_chat(event):
    system = event.metadata.get("system")
    if system:
        if hasattr(system, "tts") and system.tts:
            system.tts.stop()
        if hasattr(system, "cancel_generation"):
            system.cancel_generation()
    event.skip_llm = True
    event.ephemeral = True
    event.response = "Stopped."
    event.stop_propagation = True
```

**Goodnight macro — chain multiple actions:**
```json
{
  "capabilities": {
    "voice_commands": [{
      "triggers": ["goodnight", "good night"],
      "match": "exact",
      "bypass_llm": true,
      "handler": "hooks/goodnight.py"
    }]
  }
}
```

```python
import requests

def pre_chat(event):
    system = event.metadata.get("system")
    if system and system.tts:
        system.tts.set_voice("af_heart")
        system.tts.set_speed(0.8)

    try:
        requests.post("http://ha:8123/api/services/scene/turn_on",
                       json={"entity_id": "scene.goodnight"},
                       headers={"Authorization": "Bearer ..."})
    except Exception:
        pass

    event.skip_llm = True
    event.ephemeral = True
    event.response = "Goodnight. Lights dimmed, quiet mode on."
    event.stop_propagation = True
```

**Mute toggle — flip STT on/off:**
```json
{
  "capabilities": {
    "voice_commands": [{
      "triggers": ["mute", "go quiet"],
      "match": "exact",
      "bypass_llm": true,
      "handler": "hooks/mute.py"
    }]
  }
}
```

```python
from core.plugin_loader import plugin_loader

def pre_chat(event):
    system = event.metadata.get("system")
    if not system:
        return
    state = plugin_loader.get_plugin_state("mute-toggle")
    muted = not state.get("muted", False)
    state.save("muted", muted)

    if hasattr(system, "toggle_wakeword"):
        system.toggle_wakeword(not muted)

    event.skip_llm = True
    event.ephemeral = True
    event.response = "Muted." if muted else "Listening."
    event.stop_propagation = True
```

## How It Works Internally

Voice commands are registered as `pre_chat` hooks. With `bypass_llm: true` they're promoted to the top of their plugin's priority band (0–19 for bundled plugins; user-installed plugins are capped at the top of the user band, so bundled commands still win). When the user speaks or types, the hook runner checks triggers before the message reaches the LLM. If a trigger matches, the handler fires and — with `skip_llm = True` — the AI never sees the input.

This means voice commands always win over the AI. They're the fastest path through the pipeline.

## Reference for AI

VOICE COMMANDS:
- Manifest: `capabilities.voice_commands` = [{triggers: [phrases], match: "exact"|"starts_with"|"contains"|"regex" (default exact, case-insensitive), bypass_llm: bool, handler: path, description?}]. Multiple entries per plugin fine.
- Registered as pre_chat hooks with trigger pre-filtering; bypass_llm promotes to the top of the plugin's priority band (0-19 bundled; user-installed plugins cap at the top of the user band).
- Handler: `def pre_chat(event)` (or `def handle(event)` fallback — never `run`). Pattern to fully handle without the AI: `event.skip_llm = True` (don't send to LLM), `event.ephemeral = True` (don't save to history), `event.response = "..."` (shown/spoken), `event.stop_propagation = True` (skip lower-priority hooks).
- System access: `event.metadata.get("system")` -> VoiceChatSystem (tts.stop(), set_voice, set_speed, cancel_generation, toggle_wakeword...). Guard with hasattr — components may be None.
- Plugin state across commands: `plugin_loader.get_plugin_state(name)` .get/.save.
- Matching fires before the LLM ever sees the input — voice commands always win over the AI.
