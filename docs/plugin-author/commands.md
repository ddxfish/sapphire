# Slash Commands

A plugin can add a command to the chat composer. It shows up in the `/` menu and in `/help`, and it never becomes a turn, so the LLM doesn't see it.

## Manifest Declaration

```json
"capabilities": {
  "commands": [
    {
      "name": "lights",
      "args": "on|off",
      "description": "switch the desk lights",
      "handler": "hooks/lights.py"
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `name` | Letters, digits, `-` and `_`, starting with a letter. A name core or another plugin already holds is refused, with a warning in the log |
| `args` | Shown in the menu. Leave it out and text after the name is refused for you |
| `description` | One line for the menu and `/help` |
| `handler` | Path to a file with a `command` or `handle` function |

## The Handler

```python
def command(chat, args, by):
    if args not in ("on", "off"):
        return {"ok": False, "say": "Say /lights on or /lights off."}
    return {"ok": True, "say": f"Lights {args}."}
```

`chat` is the chat being viewed. `args` is the text after the name. `by` is `"user"`. The handler runs on a worker thread, and `say` appears as a toast.

Plugin commands are for the person typing. To give Sapphire the same ability, ship a tool.

## Reference for AI

- Manifest: `capabilities.commands` = [{name, args?, description?, handler}].
- Handler: `command(chat, args, by)` or `handle(...)`, returns {"ok": bool, "say": str}. An exception becomes a refused toast.
- Plugin commands can't be called by the AI and can't open dialogs.
