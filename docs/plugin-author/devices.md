# Device Drivers

Teach Sapphire to use a machine or a gadget. **Devices** is part of core: it owns the Settings > Devices page, the device list, and the three tools Sapphire uses. Your plugin brings a **driver**: one manifest declaration and one small module. Hardware code and its dependencies stay in your plugin, never in core. User guide: [Devices](../DEVICES.md).

Reference drivers: **`plugins/ssh`** (one capability, stored logins, user-written commands) and the FM-1 synth plugin (two capabilities, no settings, every action runs one of the plugin's own tools).

## What Sapphire sees

Three tools. Their schemas never change as devices come and go.

```
device_list()                                   every device, online or offline, what it can do
device_status("desktop")                        one device, checked right now
device_action("desktop")                        its capabilities, one example each
device_action("desktop","ssh")                  that capability's actions, one example each
device_action("desktop","ssh","volume","40")    runs one
```

A wrong guess answers with the list one level up. Core writes all of this help from what your driver's `describe()` returns, so every device reads the same to her.

Sapphire cannot add, change, or remove a device. Only the Devices page can.

## Anatomy

```
my-plugin/
  plugin.json           # manifest with capabilities.devices
  device_driver.py      # describe, status, run (and optionally validate)
```

## Manifest declaration

```json
"capabilities": {
  "devices": [
    {
      "driver": "lamp",
      "label": "Desk lamp",
      "icon": "💡",
      "module": "device_driver.py",
      "capabilities": ["light"],
      "config_schema": [
        {"key": "host", "type": "string", "label": "Host"},
        {"key": "token", "type": "string", "widget": "password", "secret": true, "label": "Token"}
      ]
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `driver` | yes | Slug, `[a-z0-9][a-z0-9_-]{0,32}`, unique across plugins. First plugin to register a name keeps it |
| `label` | no | Name in the Add Device picker |
| `icon` | no | Emoji |
| `module` | yes | Plugin-relative path to a `.py` file. It may not leave the plugin folder |
| `capabilities` | yes | 1 to 16 slugs. Each becomes a tab in the device window and the second argument of `device_action` |
| `config_schema` | no | Per-device fields, in the [settings field shape](settings.md#field-schema). Up to 40 |

Fields land on the tab of the driver's first capability. Add `"capability": "sound"` to a field to put it on another tab.

**Secrets.** A field with `"secret": true` is never stored in the device row. It goes to the device secrets file, scrambled with the machine-bound key, outside `user/` and outside backups. The page shows "Set", never the value. Your driver receives it at call time.

## The driver module

Three functions. No classes.

```python
def describe(device, config):
    """What this device can do. Called often; keep it cheap and offline."""
    return {
        "light": {
            "label": "Light",                      # the tab name
            "help": "switch and dim it",           # under 120 characters
            "actions": {
                "on":  {"help": "switch it on",  "example": ""},
                "dim": {"help": "brightness",    "example": "40"},
            },
        },
    }

def status(device, config, secrets):
    """Is it reachable right now? Never raise for an offline device."""
    return {"online": True, "detail": "192.168.0.20", "readings": {"temp": "41C"}}

def run(device, capability, action, value, config, secrets, call_tool):
    """Do one thing. Returns (text, ok). value is always a string, maybe empty."""
    return f"Dimmed to {value}%.", True

def validate(config):                              # optional
    """Driver-specific rules at save time. Returns (config, error)."""
    return config, "" if config.get("host") else "Host is needed."
```

| Argument | What it is |
|----------|------------|
| `device` | `{"id", "label"}` |
| `config` | This device's values for your non-secret `config_schema` fields |
| `secrets` | This device's secret fields. `secrets.get("token")` returns the value. Printing the object never shows one |
| `value` | Her input for this action, as text. Parse it yourself and say plainly what is wrong |
| `call_tool` | `call_tool(name, args)` runs one of **your own plugin's** tools and returns `(text, ok)` |

`describe` may differ per device. The SSH driver lists the commands the user wrote for that one machine.

`example` is the value she would pass, not the whole call. Core builds the call. Every example must run exactly as written.

`describe` may return fewer capabilities than the manifest lists. A gadget that reports only a light gets only a Light tab.

## Rules

- **Do not reimplement your plugin.** If a tool of yours already does the work, run it with `call_tool`. It goes through the function manager, so it uses the same state, settings, and privacy gates as the tool itself.
- **Do not import your modules under a second name.** A module imported twice is two copies of its state. If you must read a helper, take it from the module the loader already holds (`plugins.<your-plugin>.tools.<file>`).
- **Secrets stay out of text.** Never put a secret in a result, a log line, or a URL. Core scrubs results as a second line of defense, not a first.
- **Quote what she types.** If her value reaches a shell, quote it (`shlex.quote`). Refuse templates that would put it inside quotes.
- **Anything that moves needs a bound.** An action that starts something must end without a second call: a duration, a watchdog, or a device-side timeout. A dead controller means OFF.
- **Status is cached for 30 seconds.** `device_status` always asks fresh. Keep `status()` under a few seconds for an offline device.

## Settings fields added for device forms

These work in any plugin's settings schema.

| Field | What it does |
|-------|--------------|
| `"widget": "rows"` with `"columns": ["name", "command"]` | A list of objects with add and delete. Columns may also be `{key, label, placeholder}`. Optional `add_label` |
| `"show_if": {"auth": "password"}` | The field shows only while another field has that value. Hidden fields still save |
| `"widget": "textarea"` with `"secret": true` | A multi-line secret (a pasted key). Never echoed. Empty means keep what is stored |

## Reference for AI

- Manifest: `capabilities.devices: [{driver, label?, icon?, module, capabilities[], config_schema[]?}]`. Registered by the loader into `core/devices/registry.py`; unregistered on unload. Bad declarations are skipped with a log line, never a failed load.
- Module functions: `describe(device, config) -> {capability: {label, help, actions: {name: {help, example}}}}`, `status(device, config, secrets) -> {online, detail, readings?}`, `run(device, capability, action, value, config, secrets, call_tool) -> (text, ok)`, optional `validate(config) -> (config, error)`.
- Engine: `core/devices/engine.py`, loaded on first use. Tools: `functions/devices.py`. Rows in `user/plugin_state/devices.json` under key `devices`: `{id, label, enabled, created, parts: [{driver, plugin, config}]}`. Secrets in `core/devices/secret_store.py` under `<driver>.<field>`.
- The engine re-checks that the owning plugin is enabled and loaded on every call and drops cached driver modules when the registry generation changes.
- Hosted (managed) installs: the tools refuse, the routes answer 404, the tab is hidden.
- `call_tool` refuses tools owned by another plugin. It calls `execute_function(name, args, allowed_tools={name}, with_success=True)`.
- Routes (session auth, `core/routes/devices.py`): `GET|POST /api/devices`, `GET|PUT|DELETE /api/devices/{id}`, `POST /api/devices/{id}/test`, `POST /api/devices/{id}/run`. Limits: 240 reads and 60 writes a minute.
