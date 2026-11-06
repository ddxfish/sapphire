# Plugin Lifecycle

## Startup

1. `plugin_loader.scan()` reads `core/plugins/` (system band) then `user/plugins/` (user band)
2. Each `plugin.json` is validated and signature-checked
3. Enabled plugins get hooks, tools, voice commands, routes, and schedules registered
4. Scheduler tasks are deferred if the scheduler hasn't initialized yet

**Same name in both bands:** the user-band copy wins — it's scanned second and replaces the system entry, with a warning in the log (`'<name>' (user band) shadows the system copy at … — only the user copy will load`). The store installer refuses a name that collides with a shipped plugin, so this only happens when a folder is placed in `user/plugins/` by hand (a local fork of a bundled plugin). Delete or rename the user copy to get the shipped one back.

**Old root `plugins/` folder:** the shipped band used to live in `plugins/` at the repo root. That folder is no longer scanned — if it still holds folders with a `plugin.json`, boot logs a warning naming them (`plugins/ at the repo root is no longer scanned … your own plugins go in user/plugins/`). Move them to `user/plugins/`. Boot also logs an error if a `core/user/` folder exists, which means some code computed the project root one level short (see [Finding the project root](tools.md#finding-the-project-root)).

## Live Toggle

Settings > Plugins calls `PUT /api/webui/plugins/toggle/{name}`:
- **Enable**: Loads immediately — all capabilities register. Before loading, the plugin's record is refreshed from disk: `plugin.json` re-read (a route, setting or hook added while it was off comes up) and the signature checked again
- **Enable / boot / reload**: each tool module's optional `on_load()` runs after its tools register, fresh or reused from `sys.modules`: the place to register with core (providers, sinks, timers), never import time
- **Disable**: Unloads immediately — device drivers let go (presence), each tool module's optional `shutdown()` runs, then hooks, tools, routes, schedules are removed and the plugin's modules leave `sys.modules`

Refusals:
- **403** — unsigned (the detail points at the unsigned-plugins switch on the Settings > Plugins banner), tampered, or a verified plugin refused at load (tool-name collision, …; the detail carries the real reason). Also: disabling a locked or core plugin.
- **409** — turning on one of an alternates pair (`essential: "<group>"`, e.g. memory / mindpalace) while its partner is on: turn the partner off first.
- **404** — unknown plugin name.

A refused enable leaves `plugins.json` holding your intent (enabled), so a fix + restart brings it up; the in-memory state shows it off.

Success returns `{status, plugin, enabled, reload_required, missing_deps, env, warning}`: `missing_deps` lists absent `pip_dependencies` after an enable, `env` is the plugin's environment status, `warning` is set when turning off an alternates member left its group with no engine.

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
| GET | `/api/plugins/{name}/check-deps` | Which `pip_dependencies` are missing, the environment, and the install command |
| POST | `/api/plugins/{name}/install-deps` | Install the plugin's packages — `pip_dependencies` and its optional `extra` — in the background (core/extras.py; conda/venv only; poll `GET /api/system/extras/plugin:{name}`), then auto-reload the plugin |
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
      "verify_tier": "official",
      "verified_author": null,
      "version": "1.0.0",
      "author": "sapphire",
      "url": "https://example.com",
      "band": "system",
      "missing_deps": [],
      "env": null
    }
  ],
  "locked": [],
  "surfaces": [{"id": "chat", "label": "Chat"}, {"id": "game", "label": "Game Room"}]
}
```

Each entry also carries `collapsible`, `showInSidebar`, `settings_schema`, `description`, `icon`, `has_script` (a `web/main.js` exists), `sidebar_accordion`, `surfaces` / `surfaces_active` (manifest default / effective list), `extra` (optional-package status), `essential`, and `has_prev` (a user plugin with a previous version kept for rollback).

## Reference for AI

- Boot: `plugin_loader.scan()` reads `core/plugins/` (system band) then `user/plugins/` (user band) — a same-named user-band copy SHADOWS the system one (warned in the log; the installer refuses such a name, so it means a hand-placed fork); a root-level `plugins/` is NOT scanned (warning if it holds plugin folders); `core/user/` existing logs an error (root computed one level short); each manifest is validated and signature-checked; enabled plugins register hooks, tools, voice commands, routes, schedules; scheduler tasks defer until the scheduler initializes.
- Live toggle: `PUT /api/webui/plugins/toggle/{name}` — enable first runs `refresh_from_disk` (manifest re-read + signature re-checked), then loads immediately; disable unloads immediately. 403 = unsigned/tampered/refused at load (detail = the reason) or locked/core disable; 409 = alternates partner (`essential` group string) is on; 404 unknown. A failed enable keeps `plugins.json` intent (enabled). Response `{status, plugin, enabled, reload_required, missing_deps, env, warning}`. List entries carry verify_tier, verified_author, band, surfaces/surfaces_active, env, extra, essential, missing_deps, has_prev, sidebar_accordion, settingsUI. Unload order: device presence release → each tool module's optional `shutdown()` (also at Sapphire's own shutdown) → hooks/tools/routes unregistered → the plugin's modules purged from `sys.modules`.
- Hot reload: `POST /api/plugins/{name}/reload`; a failed reload leaves the plugin unloaded — no half-loaded state. `SAPPHIRE_DEV=1` enables the file watcher (auto-reload on `.py`/`.json` changes, polled).
- Rescan: `POST /api/plugins/rescan` → `{"added": [...], "removed": [...]}` without restart.
- The `plugins_ready` hook fires after the boot scan AND after reload, rescan, and toggle-on — handlers must be idempotent (see hooks.md).
- Error isolation: a hook handler exception is logged and skipped (next handler fires); tool execution errors are returned to the AI as error messages.
- Chat-scoped rows (`plugin_loader.get_chat_state(name)`) are core-managed: carried through rename, encrypted on vault, deleted inside the chat-delete transaction BEFORE `chat_deleted` fires (no archive-on-delete possible from that hook). They survive a chat *clear*.
- **Purge data** (plugin card ⋮ menu, any band; also the first step of uninstall) deletes everything a plugin stores about the user in one call — `user/plugin_state/<name>.json`, its `<name>-*`/`<name>_*` siblings, and every chat-scoped row it holds — while the plugin stays installed and configured. Rows in sealed (locked-vault) chats are skipped and counted, never deleted blind. Hand-deleting the state file never reached the chat rows; this lever does.
- Key endpoints: `GET /api/webui/plugins` (list), `GET/PUT/DELETE /api/webui/plugins/{name}/settings`, `GET /plugin-web/{name}/{path}` (web assets), `/api/plugin/{name}/{path}` (custom routes, auth enforced).
