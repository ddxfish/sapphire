# Device Drivers

Teach Sapphire to use a machine or a gadget. **Devices** is part of core: it owns the Settings > Devices page, the device list, and the three tools Sapphire uses. Your plugin brings a **driver**: one manifest declaration and one small module. Hardware code and its dependencies stay in your plugin, never in core. User guide: [Devices](../DEVICES.md).

Anything can be a device: a thermostat, a television, a robot arm. If your code can reach it, Sapphire can use it, and it gets the same page, the same three tools and the same help as every built-in device.

Reference drivers: **`plugins/ssh`** (stored logins, user-written commands), the FM-1 synth plugin (two device types, every action runs one of the plugin's own tools), and the two that ship inside core, `core/devices/drivers/satellite.py` and `computer.py`.

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

## Your first driver, start to finish

This is a whole driver: a lamp on your network that takes three commands. Copy
the two files, change what `_send` does, and you have a device.

```
my-lamp/
  plugin.json           # the manifest, with capabilities.devices
  device_driver.py      # describe, status, run, and optionally validate
```

**plugin.json**

```json
{
  "name": "my-lamp",
  "version": "1.0.0",
  "description": "A desk lamp Sapphire can switch and dim",
  "author": "you",
  "capabilities": {
    "devices": [
      {
        "driver": "lamp",
        "label": "Desk lamp",
        "icon": "💡",
        "module": "device_driver.py",
        "capabilities": ["light"],
        "config_schema": [
          {"key": "host", "type": "string", "label": "Address", "placeholder": "192.168.0.20"},
          {"key": "token", "type": "string", "widget": "password", "secret": true, "label": "Token"}
        ]
      }
    ]
  }
}
```

**device_driver.py**

```python
import requests


class Problem(Exception):
    """A reason fit to show as it is."""


def _send(config, secrets, path):
    """One request to the lamp. The only part that knows the hardware."""
    try:
        r = requests.get(f"http://{config['host']}{path}",
                         headers={"Authorization": "Bearer " + secrets.get("token")}, timeout=5)
    except requests.exceptions.RequestException:
        raise Problem(f"Could not reach the lamp at {config['host']}. Is it plugged in?")
    if r.status_code != 200:
        raise Problem(f"The lamp answered HTTP {r.status_code}.")
    return r.json()


def validate(config):
    """Rules at save time. Returns (config, error). Optional."""
    config["host"] = str(config.get("host") or "").strip()
    return config, "" if config["host"] else "The lamp's address is needed."


def describe(device, config):
    """What this device can do. Called often: keep it cheap and offline."""
    return {
        "light": {
            "label": "Light",                                # the tab name
            "help": "switch and dim it",                     # under 120 characters
            "actions": {
                "on":  {"help": "switch it on",       "example": ""},
                "off": {"help": "switch it off",      "example": ""},
                "dim": {"help": "brightness, 0-100",  "example": "40"},
            },
        },
    }


def status(device, config, secrets):
    """Is it reachable right now? Never raise for a device that is off."""
    try:
        state = _send(config, secrets, "/state")
    except Problem as e:
        return {"online": False, "detail": str(e)}
    return {"online": True, "detail": config["host"],
            "readings": {"brightness": f"{state['brightness']}%"}}


def run(device, capability, action, value, config, secrets, call_tool):
    """Do one thing. Returns (text, ok). value is always a string, maybe empty."""
    try:
        if action == "on":
            _send(config, secrets, "/on")
            return f"{device['label']} is on.", True
        if action == "off":
            _send(config, secrets, "/off")
            return f"{device['label']} is off.", True
        if action == "dim":
            if not value.strip().isdigit() or not 0 <= int(value) <= 100:
                return f"dim takes a number from 0 to 100, not '{value}'.", False
            _send(config, secrets, f"/dim?to={int(value)}")
            return f"{device['label']} is at {int(value)}%.", True
    except Problem as e:
        return str(e), False
    return f"The lamp has no {capability} / {action}.", False
```

This example is run by `tests/test_docs_device_driver_example.py`, straight from
this page, through the real engine. If it stops working, that test fails.

### Try it

1. Put the folder in `user/plugins/`.
2. Settings > Plugins: enable it. A plugin that is not signed needs sideloading switched on.
3. Settings > Devices > **+ Add Device**. "Desk lamp" is in the Type list.
4. Open the device. The **Light** tab has a **Try** button for each action.
5. Ask Sapphire to dim the lamp. She finds it with `device_list`.

A change to `device_driver.py` takes effect when the plugin is reloaded. No
restart is needed.

### What the manifest fields mean

| Field | Required | Description |
|-------|----------|-------------|
| `driver` | yes | Slug, `[a-z0-9][a-z0-9_-]{0,32}`, unique across plugins. First plugin to register a name keeps it |
| `label` | no | Name in the Add Device picker |
| `icon` | no | Emoji |
| `module` | yes | Plugin-relative path to a `.py` file. It may not leave the plugin folder |
| `capabilities` | yes | 1 to 16 slugs. Each becomes a tab in the device window and the second argument of `device_action` |
| `config_schema` | no | Per-device fields, in the [settings field shape](settings.md#field-schema). Up to 40 |
| `locked_by_default` | no | Capabilities Sapphire may not use until the user allows it. See Power below |
| `presence` | no | `true`: its things come and go, and the driver is kept told. See Things that come and go |

`devices` is a list. One plugin may bring several device types, each with its
own module. The FM-1 plugin brings two: the synth, and a plain MIDI keyboard.

Fields land on the tab of the driver's first capability. Add `"capability": "sound"` to a field to put it on another tab, or `"tab": "Status"` to put it with the device's health. How to reach a device belongs on Status when the driver has several capabilities.

**Secrets.** A field with `"secret": true` is never stored in the device row. It goes to the device secrets file, scrambled with the machine-bound key, outside `user/` and outside backups. The page shows "Set", never the value. Your driver receives it at call time.

### What the functions are given

| Argument | What it is |
|----------|------------|
| `device` | `{"id", "label", "location"}`. Location is the room or place the user gave it, or empty |
| `config` | This device's values for your non-secret `config_schema` fields |
| `secrets` | This device's secret fields. `secrets.get("token")` returns the value. Printing the object never shows one |
| `value` | Her input for this action, as text. Parse it yourself and say plainly what is wrong |
| `call_tool` | `call_tool(name, args)` runs one of **your own plugin's** tools and returns `(text, ok)` |

`describe` may differ per device. The SSH driver lists the commands the user wrote for that one machine.

`example` is the value she would pass, not the whole call. Core builds the call. Every example must run exactly as written.

`describe` may return fewer capabilities than the manifest lists. A gadget that reports only a light gets only a Light tab.

### A device that says what it has

One driver often serves boards that differ: one has a light, the next has none.
Ask the device, and pass its answer on in `status()`:

```python
def status(device, config, secrets):
    info = ask_the_board(config)             # {"has": ["light", "button"], ...}
    return {"online": True, "detail": info["name"], "has": info["has"]}
```

Core keeps the list with the device. From then on the device shows only those
capabilities, to her and on the page, also while it is offline. A settings
field with `"capability": "light"` is hidden on a device without a light, and
its value is kept.

| `has` | Result |
|---|---|
| Not answered, ever | The device has everything `describe` returns |
| `["light"]` | Only `light` |
| `[]` | Nothing |
| A name that is not in your manifest | Left out, and logged once |

Do not cache the list yourself. `describe` is given no secrets and must not
call the device.

### Settings that live on the device

Some settings belong on the hardware: the looks of a ring, the hours it is
lit. Give your driver `apply(device, config, secrets)` and core calls it
after every save. Hand the settings over there:

```python
def apply(device, config, secrets):
    r = requests.put(config["url"] + "/led/looks", json={...}, headers=auth(secrets), timeout=6)
    if r.status_code >= 400:
        raise DeviceError("the board did not take the looks: " + r.text[:100])
```

The save holds either way. What you raise is shown beside "Saved": *Saved, but
the device did not take its settings (satellite: ...)*. A device that is
turned off is not told. Keep `apply` short; it runs while the user waits.

## Rules

- **Do not reimplement your plugin.** If a tool of yours already does the work, run it with `call_tool`. It goes through the function manager, so it uses the same state, settings, and privacy gates as the tool itself.
- **Do not import your modules under a second name.** A module imported twice is two copies of its state. If you must read a helper, take it from the module the loader already holds (`plugins.<your-plugin>.tools.<file>`).
- **Secrets stay out of text.** Never put a secret in a result, a log line, or a URL. Core scrubs results as a second line of defense, not a first.
- **Quote what she types.** If her value reaches a shell, quote it (`shlex.quote`). Refuse templates that would put it inside quotes.
- **Anything that moves needs a bound.** An action that starts something must end without a second call: a duration, a watchdog, or a device-side timeout. A dead controller means OFF.
- **A keeper calls `status()` on its own clock** (every 30 s while a device is online, every 90 s while it is offline) and keeps one belief per device: online or offline. One answer makes it online; two misses in a row make it offline; the last state survives a restart. The page and `device_list` read the belief and never wait on you. `device_status` and the Test button ask fresh. Keep `status()` under a few seconds for an offline device.

## Testing your driver

A driver is three plain functions, so it is tested like any other code. No
Sapphire has to run. Fake the one function that touches the hardware:

```python
from unittest.mock import patch
import device_driver as lamp

DEVICE = {"id": "desk", "label": "Desk lamp", "location": "Office"}
CONFIG = {"host": "192.168.0.20"}

def test_dim():
    with patch.object(lamp, "_send", lambda config, secrets, path: {"ok": True}):
        assert lamp.run(DEVICE, "light", "dim", "40", CONFIG, {}, None) == ("Desk lamp is at 40%.", True)
        assert lamp.run(DEVICE, "light", "dim", "bright", CONFIG, {}, None)[1] is False
```

Two tests are worth having in every driver:

- **Every example in your help really runs.** Loop over `describe()` and run each action with its own `example`. Sapphire copies those examples.
- **What is missing is said plainly.** An unplugged device must answer with a sentence, never a stack trace.

## Names that mean the same everywhere

Use these capability names when they fit. Sapphire then reads every device the
same way, and core knows what to do with them.

| Capability | Actions | What core adds |
|---|---|---|
| `power` | `restart`, `shutdown`, `sleep` | A "Sapphire may use this" switch |
| `camera` | `look` | Pictures reach her eyes. The same switch |
| `screen` | `look` | The same |
| `mic` | `listen` | The same switch. With `chat` and `voice_key` fields, wake word questions |
| `speaker` | `say` | Her answers are spoken there |
| `light` | `set`, `off` | |
| `sound` | `read`, `set`, `up`, `down`, `mute` | |

Any other name works too. It gets a tab and a place in her help like the rest.

## Power: a device that can restart or switch off

Name the capability `power`. Use these action names where they fit, so every
device reads the same to her: `restart`, `shutdown`, `sleep`.

- Answer first, act a moment later. The answer has to leave the device.
- Say in the result how the device comes back. After a shutdown, someone has
  to walk over to it.
- `power` carries a switch on the Devices page, **Sapphire may use this**.
  Core enforces it. Your driver does nothing for it. `camera`, `screen` and
  `mic` carry the same switch.
- To have the switch start out off, add `"locked_by_default": ["power"]` to
  your driver's declaration. Do that for anything the user would miss, like
  their own computer.

## Pictures: a device with a camera

An action may answer with pictures. Return this in place of the text:

```python
return {"text": "A picture from the camera of kitchen.",
        "images": [{"data": base64_text, "media_type": "image/jpeg"}]}, True
```

She sees the pictures if her model can see. If it cannot, core describes them
to her in words. The **Try** button shows them on the page.

| Limit | Value |
|---|---|
| Pictures in one answer | 4 |
| Types | `image/jpeg`, `image/png`, `image/webp` |
| Size of one picture | 12 MB of base64 text |

Anything outside those limits is dropped. Name the capability `camera` and the
action `look` so every camera reads the same to her. Warn the room before you
capture: a light, a sound, or both.

## Voice: a device with a microphone or a speaker

Core's voice pipeline uses devices through a few conventions. Follow them and
your device becomes a satellite with no other code.

**A speaker.** Declare a capability named `speaker` with an action `say`. Its
value is the text. Get her voice as bytes from core, then play it:

```python
from core.devices import voice
audio, kind = voice.render(text)        # (bytes, "audio/ogg") or (None, reason)
```

`render` applies the privacy gate for the chat that produced the text. When it
returns `None`, return the reason as your result and send nothing.

A small board plays one format. Let it state that format, and have core
convert:

```python
plays = {"type": "audio/wav", "rate": 16000, "channels": 1}     # as the board stated it
audio, kind = voice.fit(audio, kind, plays)     # (bytes, "audio/wav") or (None, reason)
```

`audio/wav` is always 16 bit PCM. It is the only type today. `rate` is 8000 to
48000, `channels` is 1 or 2.

**A microphone.** Declare a capability named `mic`, and these config fields:

| Field | Purpose |
|---|---|
| `chat` | The chat this device's questions land in. Empty means the last chat used |
| `voice_key` (secret) | The key the device sends with each question |

The device then posts what it heard:

```
POST /api/devices/<device name>/voice
Authorization: Bearer <voice_key>
Content-Type: audio/wav

<the recording>
```

A form with the file field `audio` works too. The full contract of a satellite
board is in [Satellite Protocol](../SATELLITE-PROTOCOL.md).

Core transcribes it and answers at once with `{ok, heard, accepted, chat}`.
`accepted: true` means a turn has started. Its answer comes later, through
your `speaker` / `say`. The device must not wait on this response for her
answer.

She reads one line above what was heard, naming the device and its location:

```
[Voice from device "kitchen" (Kitchen Pi). Location: Kitchen. Your reply is spoken aloud there.]
```

A chat that is mid-turn is waited for, up to 20 seconds. One device may have
3 questions waiting. More are refused with `busy: true`.

**A light.** The device may hold one stream open to learn what its light
should show:

```
GET /api/devices/<device name>/events
Authorization: Bearer <voice_key>
```

It is a server-sent event stream. Each `data:` line is JSON with a `state`:

| `state` | Meaning |
|---|---|
| `connected` | The stream is open |
| `thinking` | She is working on what this device heard |
| `tool` | She is running a tool for it. `tool_name` names it |
| `idle` | The turn is over. Sent after the answer has played |
| `error` | The person got no answer |

A device hears only about its own turns. A stream that opens in the middle of
a turn is sent the current state first. Lines that start with `:` keep the
stream alive and carry nothing. The stream ends when the key changes.

Show `thinking` yourself from the moment `accepted` arrives, and put a bound
on it. If core restarts in the middle of a turn, no `idle` will come.

To transcribe inside an action of your own, use `voice.stt_refusal()` first,
then `voice.transcribe(audio_bytes)`.

## Things that come and go

A keyboard is plugged in. A board is pulled out. A Bluetooth speaker walks out
of range. A driver can ask to be **kept told**, so its heavy part (a synth, an
open port, a connection) runs only while it has something to run for.

Add `"presence": true` to the manifest entry, and up to three functions to the
module. All three are optional.

| Function | What it is for |
|---|---|
| `discover(config)` | What you can see right now. Returns `[{"id", "name", "kind"}]` |
| `watch(changed, stopped)` | Call `changed()` when that changes. Runs on a thread of its own until `stopped` is set |
| `tend(device, config, secrets, present, leaving)` | Make the device match what is here |

**`tend` is handed the whole truth every time.** Never "this one arrived".
`present` is everything that is here and counts as this device. It is called
when something changed, when the user saved the device, and every 15 seconds
regardless. So `tend` must be safe to repeat: start what is not running, leave
alone what is. In return a missed event is repaired by the next call, and so is
a process of yours that fell over.

`leaving` is true once, when the device is switched off or removed, when your
plugin unloads, and when Sapphire stops. `present` is empty then. Let go of
everything.

**`id` must hold across a replug.** Use the hardware's own name or serial
number, not a number the system hands out afresh each time. `name` is what the
user reads. `kind` is a short word like USB or Bluetooth.

### The pick list

A field of type `found` lets the user say which of the found things count. Core
draws it from `discover()`, stores the choice, and applies it. Your driver
never sees the filter, only `present`.

```
(•) Whichever is plugged in          "many": false shows one dropdown
( ) Only these                       "many": true (the default) shows ticks
      [x] Board A    USB
      [ ] Board B    USB
```

| Field key | What it does |
|---|---|
| `"type": "found"` | The pick list. One per driver |
| `"many": false` | The device is ONE thing: `present` holds at most one |
| `"all_label"`, `"only_label"` | The words for the two choices |

### A board on USB

Two boards are plugged in. Each is its own device, and each device picks its
board. The manifest entry:

```json
{
  "driver": "board",
  "label": "USB board",
  "module": "device_driver.py",
  "capabilities": ["light"],
  "presence": true,
  "config_schema": [
    {"key": "which", "type": "found", "many": false, "tab": "Status",
     "label": "Which board", "all_label": "Whichever is plugged in"}
  ]
}
```

What the module adds to `describe`, `status` and `run`:

```python
import os

BY_ID = "/dev/serial/by-id"       # the system lists boards here by make and serial number
_held = {}                        # device id -> {"path", "port"}


def discover(config):
    """Every board that is plugged in right now."""
    try:
        names = sorted(os.listdir(BY_ID))
    except OSError:
        return []                 # no folder: nothing is plugged in
    return [{"id": name, "name": name.split("_")[1] if "_" in name else name, "kind": "USB"}
            for name in names]


def watch(changed, stopped):
    """Look once a second. If your system can tell you when something changed,
    read that in place of looking."""
    seen = None
    while not stopped.wait(1):
        now = discover({})
        if now != seen:
            seen = now
            changed()


def tend(device, config, secrets, present, leaving):
    """Hold the board's port open while it is here. Let go when it is not."""
    want = "" if leaving or not present else os.path.join(BY_ID, present[0]["id"])
    held = _held.get(device["id"])
    if held and held["path"] != want:
        held["port"].close()
        del _held[device["id"]]
    if want and device["id"] not in _held:
        _held[device["id"]] = {"path": want, "port": open_port(want)}
```

`open_port` is yours to write. `run` then uses `_held[device["id"]]["port"]`,
and answers "The board is not plugged in." when there is none.

### What core does for you

- One watcher per driver, however many devices it has. `changed()` costs
  nothing to call twice: a burst is gathered into one look.
- Each device is tended by itself. One that is slow does not hold the others.
  The same device is never tended twice at once.
- A `watch` that ends or fails is started again, a little later each time. The
  15 second look covers until then.
- Status gains a `connected` line, said the same way for every driver.
- Before your plugin unloads, every device of yours is tended with
  `leaving=True`. You have 8 seconds.

### Rules

- **One thing shared by several devices is yours to add up.** The MIDI
  keyboard driver has one synth for all its devices. It keeps what each device
  wants and plays for all of them together.
- **`discover` must be quick and must not change anything.** It runs on every
  look and when the user opens the pick list.
- **Start nothing at import.** A thread that starts when your module loads
  runs in tests, and runs when no device exists. `tend` is where things start.

## Drivers that ship inside core

Two drivers live in `core/devices/drivers/` because every install should have
them: `satellite` (a room box with mic, speaker, light and camera) and
`computer` (the machine Sapphire runs on). Their ids are reserved: a plugin
cannot register a driver with one of those names, and cannot call itself
`core`. Everything in this guide applies to them unchanged. Their manifest
entry is a `SPEC` dict in the module.

One thing only a core driver may do: name tools of other plugins in its SPEC
(`uses_tools`) and run them through `call_tool`. The computer sees its screen
through the screenshot plugin's own tool that way, so Sapphire has one
screenshot program, not two. A plugin's driver runs its own plugin's tools and
no others.

## Settings fields added for device forms

These work in any plugin's settings schema.

| Field | What it does |
|-------|--------------|
| `"widget": "rows"` with `"columns": ["name", "command"]` | A list of objects with add and delete. Columns may also be `{key, label, placeholder}`. Optional `add_label` |
| `"show_if": {"auth": "password"}` | The field shows only while another field has that value. Hidden fields still save |
| `"widget": "textarea"` with `"secret": true` | A multi-line secret (a pasted key). Never echoed. Empty means keep what is stored |
| `"type": "found"` with `"found_url"` | A live pick list. The URL answers `{found: [{id, name, kind}]}`. The value is `{all, only: [{id, name}]}`. Device forms set the URL themselves |

## Reference for AI

- Manifest: `capabilities.devices: [{driver, label?, icon?, module, capabilities[], config_schema[]?, locked_by_default[]?, presence?}]`. Registered by the loader into `core/devices/registry.py`; unregistered on unload. Bad declarations are skipped with a log line, never a failed load.
- Module functions: `describe(device, config) -> {capability: {label, help, actions: {name: {help, example}}}}`, `status(device, config, secrets) -> {online, detail, readings?, has?}`, optional `apply(device, config, secrets)` (after a save; raise `DeviceError` to be heard), `run(device, capability, action, value, config, secrets, call_tool) -> (text, ok)`, optional `validate(config) -> (config, error)`.
- Engine: `core/devices/engine.py`, loaded on first use. Tools: `functions/devices.py`. Rows in `user/plugin_state/devices.json` under key `devices`: `{id, label, location, enabled, created, locked: [capability], parts: [{driver, plugin, config}]}`. `locked` = what she may not use; only capabilities in `engine.LOCKABLE` can be locked; `engine.run(..., owner=True)` is the user's own button and passes the lock. Secrets in `core/devices/secret_store.py` under `<driver>.<field>`.
- The engine re-checks that the owning plugin is enabled and loaded on every call and drops cached driver modules when the registry generation changes.
- Hosted (managed) installs: the tools refuse, the routes answer 404, the tab is hidden.
- `call_tool` refuses tools owned by another plugin. It calls `execute_function(name, args, allowed_tools={name}, with_success=True)`.
- Voice: `core/devices/voice.py` (`hear`, `say`, `cue`, `render`, `transcribe`, `key_ok`, `origin_line`). `hear` answers once the words are known and runs the turn on its own thread. Reply lane: `cadence.run_turn(chat, text, speak='device:<id>', on_event=...)`. Doors: `POST /api/devices/{id}/voice` and `GET /api/devices/{id}/events`, device key, each 30 a minute per caller address, counted before the key is checked.
- Core drivers: `registry.CORE_DRIVERS`, registered by the engine on first use with `builtin=True`.
- Self-report: `status()` may answer `has: [capability]`. `engine._learn` keeps it on the row as `parts[].has` (written only on a change, names outside the manifest dropped with one warning). `engine.capabilities(part, spec)` is the manifest list held to it; `describe`, `public`, `run` and `voice._voice_part` all use it. `public` hides schema fields whose `capability` the device lacks. No key = never said = everything.
- Sound format: `voice.fit(audio, kind, plays)` and `voice.wanted(plays)`. numpy and soundfile only. Going down in rate it low-pass filters first.
- After a save: `engine.tell(row)` calls each part's `apply` and returns problems in words; the routes put them in the save's `warning`. `voice.clock()` = `{now, tz}` (POSIX zone from `/etc/localtime`'s footer) rides on the events stream's `connected` line.
- Routes (session auth, `core/routes/devices.py`): `GET|POST /api/devices`, `GET|PUT|DELETE /api/devices/{id}`, `POST /api/devices/{id}/test`, `POST /api/devices/{id}/run`, `GET /api/devices/found/{driver}?device=`. Limits: 240 reads and 60 writes a minute. `PUT` takes `locked` as a map `{capability: bool}` that changes only what it names (a list replaces the whole set). `found` is a reserved device name.
- Presence: `core/devices/presence.py`, started after the plugin scan (`sapphire.py`), stopped before plugin services. Manifest `"presence": true` plus optional module functions `discover(config) -> [{id, name, kind}]`, `watch(changed, stopped)`, `tend(device, config, secrets, present, leaving)`. `tend` is level triggered: called on a poke (watcher, device saved, plugin loaded), on the 15 s heartbeat, and once with `leaving=True, present=[]` when the device is disabled or removed, the plugin unloads (`presence.release(plugin)`, called by the loader before it unregisters the drivers) or the app stops. One keeper thread, one watcher thread per driver with an enabled device, tends on a pool of 4, never two at once for one device. Pokes are gathered for 0.3 s.
- Filter: a `config_schema` field of `"type": "found"` (one per driver). Stored in the part's config as `{all: bool, only: [{id, name}]}`. `engine.found(driver, config)` cleans what `discover` returns (64 at most). `engine.passing(spec, config, things)` applies the filter. `"many": false` keeps one.
