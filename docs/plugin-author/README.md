# Plugin Author Guide

## What Is a Plugin?

A plugin is a self-contained package that extends Sapphire — a folder with a single `plugin.json` manifest, dropped into `plugins/` (built-in) or `user/plugins/` (yours). It can do far more than add abilities the AI calls; it hooks into parts of the pipeline the LLM never touches:

- **Add tools** — functions the AI can call during a conversation (search the web, save a memory, control a device)
- **Intercept voice input** after speech-to-text, before the LLM ever sees it (`post_stt`)
- **Filter or rewrite the AI's response** before it's saved to history (`post_llm`)
- **Inject context into the system prompt** every turn (`prompt_inject` for long-lived state; `ghost_inject` for cache-friendly per-turn ephemera like time, weather, or mood)
- **Block or modify tool arguments** before execution (`pre_execute`)
- **Control TTS** — change the voice, rewrite text, or cancel speech entirely (`pre_tts`)
- **React to wakeword detection** before recording starts (`on_wake`)
- **Provide custom inference providers** — TTS, STT, Embedding, or LLM backends that appear in settings dropdowns ([guide](providers.md))
- **Run scheduled tasks** on cron timers, independent of any conversation
- **Run background daemons** — listen for external events (messages, emails) and trigger AI responses ([guide](daemons.md))
- **Register voice commands** — keyword triggers that bypass the LLM entirely
- **Ship a settings UI, a dashboard widget, a full-page app, or a theme**

In short, a plugin is an autonomous package that can reshape how Sapphire behaves at every stage — input, processing, output, and beyond. This guide covers building that whole spectrum.

## How Plugins Get Made

- **By hand** — a developer writes the manifest and code. The full toolkit is documented here in this guide.
- **By a coding agent** — point an agent like Claude Code (see the bundled `claude-code` plugin) at this guide and the codebase, and it can author a complete plugin end to end.
- **By Toolmaker (one slice)** — Sapphire's built-in [Toolmaker](../TOOLMAKER.md) lets the AI create a **single tool** at runtime. It's deliberately simplified so small local models can use it reliably — it makes one slice (a tool packaged as a minimal plugin), not the full thing. For anything beyond a lone tool — hooks, daemons, providers, UI — you want a real plugin, which is what this guide is for.

---

## Contents

| Guide | What's Inside |
|-------|--------------|
| [Manifest](manifest.md) | `plugin.json` reference — fields, priority bands, directory structure |
| [Hooks](hooks.md) | All hook points (incl. `ghost_inject`, streaming-TTS, chat lifecycle), HookEvent fields, system access, the `privacy_aware` gate for private chats, examples |
| [Voice Commands](voice-commands.md) | Keyword triggers that bypass the LLM — match modes, handlers, macros |
| [Tools](tools.md) | Tool file format, schema flags, scopes, reading settings, plugin + chat-scoped state, privacy patterns |
| [Routes](routes.md) | Custom HTTP endpoints — path params, auth enforcement, handler signature |
| [Schedule](schedule.md) | Cron tasks — manifest fields, handler contract, examples |
| [Daemons](daemons.md) | Background event listeners — Telegram, Discord, Email, custom sources |
| [Subprocesses](subprocesses.md) | Running & supervising external programs — ProcessManager lifecycle, process-group cleanup, advanced async helper |
| [Providers](providers.md) | Custom TTS, STT, Embedding, LLM backends — base classes, manifest, lifecycle |
| [Memory Layers](memory-layers.md) | Plugins adding layers to the Mind Palace |
| [Prompt Packs](prompts.md) | Shipping prompts with a plugin |
| [Widgets](widgets.md) | Dashboard panels — manifest, render contract, settings schema, sample plugin |
| [Games](games.md) | Game Room games — engine contract, sealed seat, board modules, sessions-are-chats |
| [Stories](stories.md) | Story packs — rooms, referee, dice, sealed blanks, identity modes, backdrops |
| [Apps](APPS.md) | Full-page plugin UIs in the Apps nav — render/cleanup contract, navrail + flyout promotion |
| [Settings](settings.md) | Manifest-declared settings, custom web UI, settings API, danger confirms |
| [Web UI](web-ui.md) | Shared JS modules, CSS variables, modals, CSRF, style injection |
| [Themes](THEMES.md) | Custom themes — CSS, animated JS backgrounds, per-theme settings (Settings > Visual) |
| [Signing](signing.md) | Verification states, sideloading, signing your own plugins |
| [Lifecycle](lifecycle.md) | Startup, live toggle, hot reload, rescan, error isolation, chat lifecycle |
| [Publishing](publishing.md) | How to structure your repo and submit to the Sapphire Store |
| [Examples](examples.md) | Minimal working examples of every capability type — copy-paste templates |
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

## Plugin Tests

Tests for a plugin live alongside it, at `plugins/<name>/tests/test_*.py`. They travel with the plugin on install/uninstall so authors can maintain them next to the code they cover.

```
plugins/my-plugin/
  plugin.json
  tools/my_tool.py
  tests/
    test_my_tool.py        # no __init__.py
```

Run from the repo root with `pytest` — the root `pytest.ini` sets `testpaths = tests plugins` and skips `user/`, `infra/`, and `tmp/`.

**Do not add an `__init__.py` to `tests/`.** pytest collects tests across every plugin's `tests/` dir simultaneously; an `__init__.py` turns each one into the same import name (`tests`) and triggers import-name collisions. Leave the directory as plain, non-package — pytest's rootdir-relative collection handles it fine.

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
