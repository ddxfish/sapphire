# Devices

Add machines and gadgets once, then Sapphire uses them by name. Devices is part of
Sapphire itself. The device types come from plugins, so a type appears when its
plugin is enabled.

## Add a device

1. Settings > Devices > **+ Add Device**.
2. Name it. The name is what Sapphire calls it, for example `desktop`.
3. Pick a type. A greyed-out type names the plugin to enable.
4. Fill in the fields and press **Add**. The device window opens on its Status tab.

## The device window

- **Status** shows online or offline, the reason when it is offline, and any readings. **Test now** checks it fresh.
- One tab per thing the device can do. Each lists its actions with a **Try** button.
- **Save** keeps the window open. Try runs the saved version.

## What Sapphire can and cannot do

She has three tools: `device_list`, `device_status`, `device_action`. She can use a device. She cannot add, change, or remove one, and she never sees a stored password or key.

Turn a device off on its Status tab and she cannot see or use it.

## SSH machines

Four ways to log in:

| Login | Use it when |
|---|---|
| Auto | The user Sapphire runs as can already `ssh` to the machine |
| Key file | The key is a file on this machine |
| Pasted key | You want the key stored with Sapphire, scrambled |
| Password | The machine takes passwords. The weakest choice |

Keys locked with a passphrase are not supported yet.

**Premade commands** are a name and the command it runs. Put `{value}` where Sapphire's input goes. Keep `{value}` outside of quotes. It is quoted for you.

```
name: volume     command: pactl set-sink-volume @DEFAULT_SINK@ {value}%
```

**Allow any command** lets her run anything, checked against the SSH plugin's blacklist. Leave it off and she can run only your premade commands.

## Where things are stored

| What | Where | In backups |
|---|---|---|
| Devices and their settings | `user/plugin_state/devices.json` | yes |
| Passwords and keys | the Sapphire config folder, `device_secrets.json` | no |

After a restore onto another machine, devices come back but their passwords and keys must be entered again.

## WiFi gadgets

Small boards with a light, a screen, or a button. The gadget plugin ships the
program for the board and the setup steps. See its README.

## Hosted Sapphire

Devices are switched off on a hosted Sapphire. It has no home network to reach.

## For plugin authors

See [Device Drivers](plugin-author/devices.md).
