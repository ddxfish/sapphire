# Plugin Author Guide

## Tools vs Plugins — What's the Difference?

**Tools** live inside the LLM's world. They're functions the AI can call during a conversation — search the web, save a memory, check the weather. The AI decides when to use them. If all you want is to give your AI new abilities it can call, you don't need this guide — [TOOLMAKER.md](../TOOLMAKER.md) covers that. The AI can even create tools for itself at runtime.

**Plugins** control everything else.

A plugin is a package that can contain tools, yes, but also hooks into parts of the pipeline the LLM never touches. Plugins can:

- **Intercept voice input** after speech-to-text, before the LLM ever sees it (`post_stt`)
- **Filter or rewrite the AI's response** before it's saved to history (`post_llm`)
- **Inject context into the system prompt** every turn, silently (`prompt_inject`)
- **Block or modify tool arguments** before execution (`pre_execute`)
- **Control TTS** — change the voice, rewrite text, or cancel speech entirely (`pre_tts`)
- **React to wakeword detection** before recording starts (`on_wake`)
- **Run scheduled tasks** on cron timers, independent of any conversation
- **Register voice commands** — keyword triggers that bypass the LLM entirely
- **Ship a settings UI** that renders in the browser with zero JavaScript (or full custom JS)

A tool is a single function the AI can call. A plugin is an autonomous package that can reshape how Sapphire behaves at every stage — input, processing, output, and beyond.

**If you want to give the AI a new ability** → read [TOOLMAKER.md](../TOOLMAKER.md).
**If you want to tap into the pipeline itself** → you're in the right place.

---

## Contents

| Guide | What's Inside |
|-------|--------------|
| [Manifest](manifest.md) | `plugin.json` reference — fields, priority bands, directory structure |
| [Hooks](hooks.md) | All 10 hook points, HookEvent fields, system access, examples |
| [Voice Commands](voice-commands.md) | Keyword triggers that bypass the LLM — match modes, handlers, macros |
| [Tools](tools.md) | Tool file format, schema flags, scopes, reading settings, privacy patterns |
| [Routes](routes.md) | Custom HTTP endpoints — path params, auth enforcement, handler signature |
| [Schedule](schedule.md) | Cron tasks — manifest fields, handler contract, examples |
| [Settings](settings.md) | Manifest-declared settings, custom web UI, settings API, danger confirms |
| [Web UI](web-ui.md) | Shared JS modules, CSS variables, modals, CSRF, style injection |
| [Signing](signing.md) | Verification states, sideloading, signing your own plugins |
| [Lifecycle](lifecycle.md) | Startup, live toggle, hot reload, rescan, error isolation |
| [Publishing](publishing.md) | How to structure your repo and submit to the Sapphire Store |
| [AI Reference](ai-reference.md) | Compact reference for Sapphire's own use when building plugins |

---

## Quick Start

Minimal plugin — logs every chat:

```
plugins/my-plugin/
  plugin.json
  hooks/greet.py
```

**plugin.json**:
```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Logs every chat",
  "author": "you",
  "capabilities": {
    "hooks": {
      "post_chat": "hooks/greet.py"
    }
  }
}
```

**hooks/greet.py**:
```python
import logging
logger = logging.getLogger(__name__)

def post_chat(event):
    logger.info(f"User: {event.input}")
    logger.info(f"AI: {event.response}")
```

Enable in Settings > Plugins. It loads immediately — no restart.

---

## Where Plugins Live

| Path | Band | Priority Range | Tracked |
|------|------|----------------|---------|
| `plugins/` | System | 0-99 | Yes |
| `user/plugins/` | User | 100-199 | No (gitignored) |

User plugin priorities are automatically offset into the 100-199 range.

---

## Complete Example

A plugin combining hooks, tools, voice commands, and a scheduled task:

```
plugins/smart-home/
  plugin.json
  hooks/context.py
  hooks/quick_lights.py
  tools/devices.py
  routes/camera.py
  schedule/lock_check.py
  web/
    index.js
```

```json
{
  "name": "smart-home",
  "version": "2.0.0",
  "description": "Full smart home integration",
  "author": "you",
  "url": "https://example.com",
  "priority": 50,
  "capabilities": {
    "hooks": {
      "prompt_inject": "hooks/context.py"
    },
    "voice_commands": [
      {
        "triggers": ["lights on", "lights off"],
        "match": "exact",
        "bypass_llm": true,
        "handler": "hooks/quick_lights.py"
      }
    ],
    "tools": ["tools/devices.py"],
    "routes": [
      {
        "method": "POST",
        "path": "camera/{room}",
        "handler": "routes/camera.py:handle_snapshot"
      }
    ],
    "schedule": [
      {
        "name": "Nightly Lock Check",
        "cron": "0 23 * * *",
        "handler": "schedule/lock_check.py"
      }
    ],
    "web": { "settingsUI": "plugin" }
  }
}
```

See each guide above for the details on every capability.
