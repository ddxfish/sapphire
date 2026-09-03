# Scheduled Tasks

Plugins can declare cron tasks that run on a timer, independent of any conversation.

## Manifest Declaration

```json
"capabilities": {
  "schedule": [
    {
      "name": "Daily Digest",
      "cron": "0 9 * * *",
      "handler": "schedule/digest.py",
      "description": "Morning email summary",
      "enabled": true,
      "chance": 100
    }
  ]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | Required | Display name |
| `cron` | string | `0 9 * * *` | Standard 5-field cron |
| `handler` | string | Required | Path to handler file |
| `description` | string | — | What the task does — also becomes the task's `initial_message` (the prompt sent to the AI when it fires) |
| `enabled` | bool | true | Whether it runs |
| `chance` | int | 100 | Percent chance to fire (1-100) |
| `time_setting` | string | — | Plugin-settings key holding `"HH:MM"` — builds a daily cron from the user's setting. Falls back to `cron` if unset/unparsable |
| `enabled_setting` | string | — | Plugin-settings key holding a boolean — lets users toggle the task from the plugin's settings panel |

Tasks appear in the Triggers UI and are removed when the plugin is unloaded (disable, uninstall, or hot-reload).

### Settings-linked schedules

`time_setting` / `enabled_setting` make a task user-tunable without code: declare a settings field in the manifest, name its key here, and the loader resolves it at registration and re-syncs the live task whenever the plugin's settings are saved. Edits made directly in the Triggers UI do NOT persist for plugin tasks — they're re-registered from the manifest (and settings) on every boot, so settings are the durable source of truth.

---

## Handler Contract

```python
def run(event):
    """Called by continuity scheduler on cron match.

    event dict:
        system:       VoiceChatSystem instance
        config:       System config module
        task:         Task definition dict
        plugin_state: PluginState instance
    """
    system = event["system"]
    state = event["plugin_state"]

    # Do work...
    state.save("last_run", "2025-01-01")
    return "Done"  # Optional — logged to activity
```

The handler file must export a `run(event)` function. The return value is optional — if provided, it's logged to the schedule activity feed.

### Event Fields

| Key | Type | Description |
|-----|------|-------------|
| `system` | VoiceChatSystem | Full system access (TTS, STT, LLM, etc.) |
| `config` | module | System config (settings) |
| `task` | dict | The task definition from the manifest |
| `plugin_state` | PluginState | Persistent key-value store for this plugin |

### Using Plugin State

Track state across scheduled runs:

```python
def run(event):
    state = event["plugin_state"]
    last = state.get("last_check")

    # ... do work ...

    state.save("last_check", "2025-06-15T09:00:00")
    state.save("run_count", state.get("run_count", 0) + 1)
    return "Check complete"
```

### Sending a Message via LLM

Scheduled tasks can trigger the AI to speak or respond:

```python
def run(event):
    system = event["system"]
    if system and system.llm_chat:
        response = system.llm_chat.chat("Give me a morning briefing")
        if system.tts:
            system.tts.speak(response)
    return "Briefing delivered"
```

---

## Important: `run()` vs Hook Handlers

Schedule handlers use `def run(event)`. **Hook handlers do NOT** — they use the hook point name (e.g., `def pre_chat(event)`) or `def handle(event)` as fallback. If you name a hook handler `run()`, it will silently fail to register.

| Context | Function Name |
|---------|--------------|
| Schedule handler | `def run(event)` |
| Hook handler | `def pre_chat(event)`, `def prompt_inject(event)`, etc. |
| Hook fallback | `def handle(event)` |

---

## Note on Webhook Tasks

Webhook tasks are created by users in the Triggers UI (Webhooks), not declared in plugin manifests. They trigger when an HTTP request hits `/api/events/webhook/{path}`. Every webhook task gets an auto-generated secret — callers must include it in the `x-webhook-secret` header.

For full webhook documentation, see [DAEMONS-WEBHOOKS.md](../DAEMONS-WEBHOOKS.md).

## Reference for AI

PLUGIN SCHEDULED TASKS:
- Manifest: `capabilities.schedule` = [{name (required), cron (5-field, default "0 9 * * *"), handler (required, path relative to plugin dir), description (also becomes the task's initial_message), enabled (default true), chance (1-100, default 100), time_setting? (plugin-settings key holding "HH:MM" -> daily cron, falls back to `cron`), enabled_setting? (plugin-settings key holding a bool)}].
- Tasks appear under Triggers (Scheduled); removed when the plugin unloads (disable/uninstall/hot-reload). Plugin tasks are re-registered from the manifest+settings on every boot — Triggers-UI edits to them do NOT persist; settings-linked fields are the durable knobs, and the loader re-syncs the live task on every plugin settings save.
- Handler contract: the file must export `def run(event)`. event = {"system": VoiceChatSystem, "config": config module, "task": task dict, "plugin_state": PluginState}. Optional return value is logged to the schedule activity feed.
- PluginState: `state.get(key, default)` / `state.save(key, value)` — persistent per-plugin key-value store.
- Naming rule: `run(event)` is ONLY for schedule handlers. Hook handlers use the hook-point name (`def pre_chat(event)`, `def prompt_inject(event)`, ...) or `def handle(event)` as fallback — a hook handler named `run` silently fails to register.
- Webhook tasks are user-created in Triggers (Webhooks), not manifest-declared: HTTP hits `/api/events/webhook/{path}` with the auto-generated secret in the `x-webhook-secret` header. Full docs: docs/DAEMONS-WEBHOOKS.md.
