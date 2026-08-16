# Plugin Lifecycle

## Startup

1. `plugin_loader.scan()` reads `plugins/` and `user/plugins/`
2. Each `plugin.json` is validated and signature-checked
3. Enabled plugins get hooks, tools, voice commands, routes, and schedules registered
4. Scheduler tasks are deferred if the scheduler hasn't initialized yet

## Live Toggle

Settings > Plugins calls `PUT /api/webui/plugins/toggle/{name}`:
- **Enable**: Loads immediately — all capabilities register
- **Disable**: Unloads immediately — hooks, tools, routes, schedules removed

Unsigned/tampered plugins return 403 and the toggle reverts.

## Hot Reload (Dev)

`POST /api/plugins/{name}/reload` unloads and reloads a single plugin.

Set `SAPPHIRE_DEV=1` to enable file watching — plugins auto-reload when `.py` or `.json` files change (2s polling).

If reload fails, the plugin stays unloaded. No half-loaded state.

## Rescan

`POST /api/plugins/rescan` discovers new or removed plugin folders without restart. Returns `{"added": [...], "removed": [...]}`.

## Error Isolation

A buggy plugin never crashes the system. If a hook handler throws an exception, it's logged and skipped — the next handler fires normally. Tool execution errors are caught and returned as error messages to the AI.

## Chat Lifecycle

Plugin data can also be scoped to a *chat* instead of the plugin. Rows written
through `plugin_loader.get_chat_state(name)` live in the chat database, and core
owns their whole life: carried across a rename, encrypted when the chat goes
private, deleted with the chat (public and private alike, inside the delete
transaction). Plugins no longer register `chat_renamed` / `chat_deleted` to
shepherd that data — those hooks are for chat-keyed things kept elsewhere.
See [Chat-Scoped State](tools.md#chat-scoped-state).

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/webui/plugins` | List all plugins with metadata |
| PUT | `/api/webui/plugins/toggle/{name}` | Enable/disable (live) |
| POST | `/api/plugins/rescan` | Discover new/removed plugins |
| POST | `/api/plugins/{name}/reload` | Hot-reload (dev) |
| GET | `/api/webui/plugins/{name}/settings` | Read plugin settings |
| PUT | `/api/webui/plugins/{name}/settings` | Save plugin settings |
| DELETE | `/api/webui/plugins/{name}/settings` | Reset plugin settings |
| GET | `/plugin-web/{name}/{path}` | Serve plugin web assets |
| * | `/api/plugin/{name}/{path}` | Plugin custom routes (auth enforced) |

### Plugin List Response

```json
{
  "plugins": [
    {
      "name": "ssh",
      "enabled": true,
      "locked": false,
      "title": "SSH",
      "settingsUI": "plugin",
      "verified": true,
      "verify_msg": "verified",
      "version": "1.0.0",
      "author": "sapphire",
      "url": "https://sapphireblue.dev"
    }
  ],
  "locked": ["setup-wizard", "backup", "continuity"]
}
```
