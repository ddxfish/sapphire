# Toolmaker

Lets her write her own tools at runtime: she writes a Python tool module, it is
validated and saved as a plugin, and it loads live with no restart. Off by
default. The format she writes in is `docs/TOOLMAKER.md`.

## Tools

| Tool | What it does |
|---|---|
| `tool_save(name, code)` | Validates the code and saves it as `user/plugins/<name>/`, with a generated `plugin.json`. Updates a tool she made before |
| `tool_load()` | Rescans and loads new tools, reloads updated ones, and refreshes the toolset |
| `tool_read(name?)` | A tool's source; no name lists the tools she has made |

She can only overwrite plugins Toolmaker made itself, never one you installed,
and never a name a shipped plugin uses.

## Settings

`validation` (Settings > Plugins > **Toolmaker**), default `strict`:

| Level | Allows |
|---|---|
| `strict` | About 60 safe modules (requests, json, numpy, PIL, datetime, ...) |
| `moderate` | Adds subprocess and any import not on the dangerous list |
| `system_killer` | No restrictions; a syntax check only |

The two looser levels ask you to confirm first.

## Good to know

Her tools carry no signature, so they load only with unsigned plugins allowed
(the switch on the Settings > Plugins banner). For full plugins (hooks, web
UI, schedules) see `docs/plugin-author/README.md`.
