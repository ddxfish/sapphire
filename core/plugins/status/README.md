# Sapphire Status

Her self-awareness tool and your status page. On by default.

## Tool

`get_self_info(detail?, include_errors?)`

- `detail="brief"` (default): time and date, version and uptime, this chat
  (prompt, persona, model, toolset, context use, scopes), TTS/STT/wakeword,
  daemons, tasks, backups, memory stats, recent activity, tasks due in the
  next 4 hours, and your custom commands.
- `detail="full"`: adds hardware, disk, providers, plugins and tools.
- `include_errors=true`: the last 20 WARN/ERROR log lines.

When she wonders what model she is on, what is running, or why something
failed, this is the tool for it.

## Custom status commands

Setting `custom_status_commands` (Settings > Plugins > **Status**), empty by
default. One per line, `LABEL ::: COMMAND`:

```
CPU temp ::: sensors | grep Package
GPU ::: nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader
```

Each runs in a shell on every `get_self_info` call, brief or full, and its
output is shown to her. 5 s timeout, 500 characters each, `sudo` refused,
lines starting with `#` skipped. To filter output, pipe through `grep`.

## Where in the UI

**Apps > Sapphire Status**: live system stats, diagnostics, service health and
a log viewer. Behind it, `GET /api/plugin/status/full` and
`GET /api/plugin/status/logs?lines=200&level=WARNING&search=...`.
