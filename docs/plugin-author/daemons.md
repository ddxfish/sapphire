# Daemons

Daemons are long-running background processes that listen for external events and feed them into Sapphire's task scheduler. When an event arrives (new Telegram message, incoming email, etc.), the daemon emits it and the scheduler fires any matching tasks.

## How It Works

```
Daemon thread (your code)
  → listens for external events (polling, websocket, etc.)
  → calls plugin_loader.emit_daemon_event(source_name, json_payload)
    → scheduler finds tasks matching that source
    → task executes (LLM chat with event data as context)
    → if auto-reply: reply_handler routes response back to source
```

## Manifest Declaration

```json
{
  "capabilities": {
    "daemon": {
      "entry": "daemon.py",
      "event_sources": [
        {
          "name": "my_event",
          "label": "My Event",
          "description": "When something happens in my service",
          "filter_fields": [
            {"key": "sender", "label": "Sender Name"},
            {"key": "channel", "label": "Channel"}
          ],
          "task_fields": [
            {
              "key": "account",
              "type": "select",
              "label": "Account",
              "required": true,
              "dynamic": "/api/plugin/my-plugin/accounts",
              "help": "Which account to monitor"
            },
            {
              "key": "auto_reply",
              "type": "boolean",
              "label": "Auto-reply",
              "default": false
            }
          ]
        }
      ]
    }
  }
}
```

### Manifest Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entry` | string | Yes | Path to daemon module (relative to plugin dir) |
| `event_sources` | array | No | Event types this daemon can emit |

### Event Source Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique event identifier (used in `emit_daemon_event`) |
| `label` | string | Yes | Human-readable name (shown in the Triggers UI) |
| `description` | string | No | Tooltip/help text |
| `filter_fields` | array | No | Fields users can filter on when creating tasks |
| `task_fields` | array | No | Per-task configuration fields shown in the Triggers UI |
| `realtime` | bool | No | Gate-only source — its events never fire one-shot tasks (see [Realtime sources](#realtime-sources-gate-only)) |

### Filter Fields

Filter fields let users narrow which events trigger their task. Each filter is matched against the event payload JSON.

```json
{"key": "username", "label": "Username"}
```

The scheduler ANDs all filters — every filter must match for the task to fire.

### Task Fields

Task fields are per-task configuration shown in the Triggers UI (Daemons) when creating a daemon task. They support these types:

| Type | Description | Extra Properties |
|------|-------------|-----------------|
| `select` | Dropdown | `options` (static — strings or `{value, label}` objects) or `dynamic` (API URL for async loading) |
| `boolean` | Toggle | `default` |
| `number` | Number input | `min`, `max`, `default` |
| `string` | Text input (the default for any other type) | `default`, `placeholder`; add `"widget": "password"` to mask the input |

Every field also takes `key`, `label`, `required` (marks the field required), and `help` (rendered as a tooltip).

The `dynamic` property fetches options from an API endpoint at render time — useful for account lists that change.

### Realtime sources (gate-only)

Declare `"realtime": true` on an event source when your daemon handles
sessions **live** (a voice line, a persistent socket) rather than handing
discrete events to the scheduler. For a realtime source, tasks are **gates,
not triggers**: `emit_daemon_event()` deliberately fires nothing — a one-shot
task would double-handle on top of your live session. Instead, your daemon
reads its matching tasks to decide whether and how to answer:

- `plugin_loader.active_daemon_accounts(source)` — which accounts are armed.
- `plugin_loader.get_enabled_daemon_task(source, account, payload)` — the
  winning task's config (chat target, persona, toolset). With a `payload`
  (e.g. `{"caller": "+1555..."}`), selection is most-specific-wins: a task
  whose filter matches beats the no-filter catch-all; a failing filter
  excludes the task; no match returns `None` and the daemon should decline
  the session.

---

## Daemon Module

Your `daemon.py` must export `start()` and `stop()`:

```python
# daemon.py — Minimal daemon skeleton

import logging
import threading

logger = logging.getLogger(__name__)

_thread = None
_stop_event = threading.Event()
_plugin_loader = None


def start(plugin_loader, settings):
    """Called by plugin_loader when the plugin loads."""
    global _thread, _plugin_loader
    _plugin_loader = plugin_loader
    _stop_event.clear()

    _thread = threading.Thread(target=_run, daemon=True, name="my-daemon")
    _thread.start()

    # Register reply handler if you support auto-reply
    plugin_loader.register_reply_handler("my-plugin", _reply_handler)
    logger.info("[MY-PLUGIN] Daemon started")


def stop():
    """Called by plugin_loader when the plugin unloads."""
    global _thread
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    _thread = None
    logger.info("[MY-PLUGIN] Daemon stopped")
```

### start(plugin_loader, settings)

| Param | Type | Description |
|-------|------|-------------|
| `plugin_loader` | `PluginLoader` | The plugin loader instance — used for emitting events and reading state |
| `settings` | `dict` | Plugin settings (merged manifest defaults + user overrides) |

Called once when the plugin loads. If the scheduler isn't ready yet (early boot), the call is deferred automatically — you don't need to handle this.

### stop()

Called when the plugin unloads (toggle off, hot-reload, shutdown). Clean up threads, close connections.

---

## Emitting Events

When your daemon detects something interesting, emit an event:

```python
import json

def _run():
    while not _stop_event.is_set():
        events = poll_my_service()
        for event in events:
            payload = json.dumps({
                "account": "main",
                "sender": event.sender,
                "channel": event.channel,
                "text": event.text,
            })
            _plugin_loader.emit_daemon_event("my_event", payload)
        _stop_event.wait(60)  # poll interval
```

**`emit_daemon_event(source_name, event_data)`**

| Param | Type | Description |
|-------|------|-------------|
| `source_name` | `str` | Must match an `event_sources[].name` from your manifest |
| `event_data` | `str` | JSON string — the event payload passed to matching tasks |

The payload keys should include anything declared in your `filter_fields` so the scheduler can match filters. Include enough context for the LLM to understand what happened.

### Sending images (vision)

A daemon event can carry images so a vision-capable model *sees* them (e.g. a
Discord message with an attached screenshot). Add an **`images`** key to the
payload — a list of `{"data": <base64>, "media_type": ...}` objects:

```python
import base64

payload = json.dumps({
    "account": "main",
    "sender": event.sender,
    "text": event.text,
    "images": [
        {
            "data": base64.b64encode(image_bytes).decode(),  # raw base64, NOT a data: URI
            "media_type": "image/png",                        # png | jpeg | gif | webp
        },
    ],
})
_plugin_loader.emit_daemon_event("my_event", payload)
```

Contract and behavior:

- **Field name must be exactly `images`**, each entry exactly `{"data", "media_type"}`.
  `data` is plain base64 (no `data:image/...;base64,` prefix). Anything else is ignored.
- **Validated at the boundary:** non-image `media_type`, undecodable base64, or images
  over 10 MB are dropped with a log; max 8 images per event. Malformed entries never
  reach the model or the chat — they're skipped, not stringified.
- **Vision gate:** the image is sent to the model only if the task's provider supports
  vision. For OpenAI-compatible / Anthropic-compatible custom providers, enable the
  **👁 vision** checkbox on the provider (Settings → LLM). Non-vision providers get a
  text description of the image instead (local, no network).
- **Persistence:** the image is stored once in the chat DB and shown in history via a
  marker — the base64 is *not* re-sent every turn, so it won't bloat the chat.

---

## Reply Handlers

If your daemon supports auto-reply (sending the AI's response back to the source platform), register a reply handler in `start()`:

```python
plugin_loader.register_reply_handler("my-plugin", _reply_handler)
```

The handler is called when an event-triggered task completes:

```python
def _reply_handler(task, event_data, response_text):
    """Route LLM response back to the source.

    Args:
        task: The task dict that was triggered
        event_data: The original event payload (dict, already parsed)
        response_text: The LLM's response text
    """
    channel = event_data.get("channel")
    send_to_my_service(channel, response_text)
```

The reply handler name in `register_reply_handler` must match your plugin name, and the source name must be registered by your plugin — the system uses this to look up the correct handler.

---

## Account-Aware Daemons

If your daemon manages multiple accounts, use `active_daemon_accounts()` to only connect accounts that have active tasks:

```python
active = plugin_loader.active_daemon_accounts("my_event")
# Returns: {"account1", "account2"} — only accounts with enabled daemon tasks
```

This avoids connecting to accounts nobody is listening on.

---

## Lifecycle

1. Plugin loads → `start(plugin_loader, settings)` called
2. If scheduler not ready → start is deferred until scheduler registers
3. Daemon thread runs, polling/listening for events
4. Event detected → `emit_daemon_event()` → scheduler fires matching tasks
5. Task completes with auto-reply → reply handler routes response back
6. Plugin unloads → `stop()` called → clean up threads/connections

Daemons survive hot-reload — `stop()` is called before unload, then `start()` again after reload.

---

## Real Examples

| Plugin | Event Source | What It Does |
|--------|------------|--------------|
| `plugins/telegram/` | `telegram_message` | Telethon client, listens for incoming messages via asyncio event loop |
| `plugins/discord/` | `discord_message` | Discord.py bot, listens for channel messages |
| `plugins/email/` | `email_message` | IMAP polling (configurable interval), checks for UNSEEN mail |
| `plugins/mcp_client/` | — | MCP server connections (daemon without event sources) |

---

## Reference for AI

DAEMON SYSTEM:
- Plugin declares `capabilities.daemon` with `entry` (module path) and `event_sources` (what events it emits)
- Module exports `start(plugin_loader, settings)` and `stop()`
- Daemon thread listens for external events, calls `plugin_loader.emit_daemon_event(source_name, json_payload)`
- Scheduler finds tasks matching source, fires them with event data as context
- Reply handlers route LLM responses back to source platform
- `active_daemon_accounts(source_name)` returns set of accounts with active tasks
- Filter fields: AND-matched against event payload
- Task fields: per-task config — select (static `options` or `dynamic` API URL), boolean (`default`), number (`min`/`max`), string default (`placeholder`, `widget: "password"` masks); all take key/label/required/help
- `realtime: true` on an event source = gate-only: `emit_daemon_event` fires NO tasks; the daemon reads gates via `active_daemon_accounts(source)` + `get_enabled_daemon_task(source, account, payload)` (most-specific filter wins, no match => decline the session)
- Lifecycle: start on load, stop on unload, survives hot-reload
- Images: add `"images": [{"data": <base64>, "media_type": "image/png"}]` to the payload — sent to vision-capable providers (gated on the provider's vision support), stored once in the chat DB via a marker (no per-turn replay bloat); max 8 images, 10 MB each, png/jpeg/gif/webp
