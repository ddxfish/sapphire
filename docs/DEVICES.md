# Devices

Add machines and gadgets once, then Sapphire uses them by name. Devices is part of
Sapphire itself.

| Type | Comes with | What it is |
|---|---|---|
| This computer | Sapphire | The machine she runs on: volume, outputs, screen, power |
| Satellite | Sapphire | A room box with mic, speaker, light and camera: a Raspberry Pi, an ESP32 board |
| SSH machine | the SSH plugin | Any machine you can log in to |
| WiFi gadget | the gadget plugin | A small board with a light, a screen, a button |
| FM-1 synth, MIDI keyboard | the FM-1 plugin | Instruments she plays and hears |

Any plugin can bring more types. A type appears when its plugin is enabled.

## Add a device

1. Settings > Devices > **+ Add Device**.
2. Name it. The name is what Sapphire calls it, for example `desktop`.
3. Give it a **Location** if you like, for example `Living Room`. Sapphire sees it
   in her device list, and at the top of anything a device hears.
4. Pick a type. A greyed-out type names the plugin to enable.
5. Fill in the fields and press **Add**. The device window opens on its Status tab.

## The device window

- **Status** shows online or offline, the reason when it is offline, and any readings. **Test now** checks it fresh.
- One tab per thing the device can do. Each lists its actions with a **Try** button. The box beside it sends what you type; the grey text in it is only an example. Empty is allowed: an action that reads something, like `volume`, answers with it.
- **Save** keeps the window open. Try runs the saved version.

## What Sapphire can and cannot do

She has three tools: `device_list`, `device_status`, `device_action`. She can use a device. She cannot add, change, or remove one, and she never sees a stored password or key.

Turn a device off on its Status tab and she cannot see or use it.

## Power: restart, shut down, sleep

A device that can be restarted or switched off has a **Power** tab.

| Device | Restart | Off |
|---|---|---|
| Satellite | Yes, back in about a minute | Shutdown. Unplug and replug it to bring it back |
| WiFi gadget | Yes, back in a few seconds | Sleep. It has no true off while plugged in. Its button wakes it |
| SSH machine | Yes | Shutdown |

**Sapphire may use this** is a switch on the Power tab, one per device. Turn it
off and she is told that power is locked on that device, and where you can
change it. Your own buttons on the tab always work, and each asks first.

| Device | The switch starts out |
|---|---|
| Satellite, WiFi gadget | On |
| SSH machine | Off |

**Shut a satellite down before you touch its cables.** The camera connector
carries power. Moving the ribbon while the Pi runs can short it.

**SSH machines** use two commands that you can change on the Power tab. The
usual ones wait one minute, so a mistake can be cancelled on that machine
with `sudo shutdown -c`. They need `sudo` without a password for the login
user. Clear a command and that action is gone.

## This computer

The machine Sapphire runs on. It needs no address and no key. Add it with
**+ Add Device**, type **This computer**.

```
device_action("computer","sound","set","40")
device_action("computer","sound","up")
device_action("computer","sound","mute")
device_action("computer","sound","outputs")
device_action("computer","sound","use","2")
device_action("computer","screen","look")
```

| Setting | What it does |
|---|---|
| Volume step | How far `up` and `down` move the volume |
| Loudest Sapphire may set | She is held to this. Your own volume keys are not |

| Part | Linux | Windows |
|---|---|---|
| Volume and mute | Yes. Needs one of `wpctl`, `pactl`, `amixer` | Yes |
| Outputs | Yes, with `wpctl` or `pactl` | No |
| Screen | Yes, through the Screenshot plugin | Yes, through the Screenshot plugin |
| Power | Restart, shutdown, sleep | Restart, shutdown |

**Power starts out locked for her.** Turn on "Sapphire may use this" on the
Power tab if you want her to restart or shut down your computer. She goes
down with it, and says so.

**The screen** has the same switch. With the Screenshot plugin switched off,
this computer has no Screen tab.

## MIDI keyboard

A plain MIDI keyboard makes no sound of its own. This device gives it one: the
computer becomes the instrument. It comes with the FM-1 plugin. Linux only.

1. Plug the keyboard in.
2. **+ Add Device**, type **MIDI keyboard**.
3. Play.

Every MIDI source plays through it: a keyboard on USB, a synth linked over
Bluetooth, several at once. One that is plugged in later joins in under half a
second.

**The sound runs only while something is there to play.** It starts when the
first source arrives. It stops 20 seconds after the last one leaves, so a
replug is not a restart. A source that is pulled out with a key or the pedal
down never leaves a note ringing.

**Plays through it**, on the Status tab, says which sources count:

```
(•) Every MIDI source
( ) Only these
      [x] AKM320      USB
      [x] FM-1_BLE    Bluetooth
      [ ] Old keys    not here now
                          [Look again]
```

The list is live. A source you ticked stays on the list while it is away.

```
device_action("keyboard","sound","instrument","organ")
device_action("keyboard","sound","loudness","60")
device_action("keyboard","notes","play","C4 E4 G4 C5:2 bpm=120")
device_action("keyboard","notes","listen","30")
```

Sapphire plays on the same instrument, so you can play together. She hears
what you play with `listen`.

**The keyboard's own volume slider works.** Its lowest position is quiet, not
silent. A keyboard whose slider sits at zero would otherwise make no sound at
all, with nothing on the screen to say why. A pedal that sends expression is
treated the same way.

| Setting | What it does |
|---|---|
| Instrument | A name like piano, organ, strings, or a General MIDI number |
| Loudness | The instrument's own level. The computer's volume comes on top |
| Plays through it | Every MIDI source, or only the ones you tick |
| SoundFont file | Optional. Your own file with other instrument sounds |

It needs `fluidsynth`, `alsa-utils` and a SoundFont. On Debian and Ubuntu the
packages are `fluidsynth`, `alsa-utils` and `fluid-soundfont-gm`. The Status
tab says which one is missing.

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

## Satellites

A satellite is a small box in another room with a microphone, a speaker and a
light. Say its wake word, ask your question, and Sapphire answers from that
same box. The Raspberry Pi bodies are satellites. This type is built in.

**A satellite says what it has.** A board with no light shows no Light tab, and
Sapphire is not offered one. Press **Test now** after you change what is on a
board. Any board that speaks the [Satellite Protocol](SATELLITE-PROTOCOL.md)
can be added here. Sapphire ships a program for the Waveshare ESP32-S3 audio
board: see `firmware/satellite-esp32/README.md`.

Add one with **+ Add Device**, type **Satellite**:

| Field | What to enter |
|---|---|
| Address | Where the satellite listens, like `http://192.168.0.221:8090` |
| Key Sapphire sends | The satellite's own key |
| Has a camera | Turn off for a satellite with no camera. Sapphire is then not offered one |
| Talks in chat | The chat its questions land in. Empty means the last chat used. A name means always that chat |
| Key the satellite sends | The satellite's own key for talking to Sapphire. See "Give a Pi its own key" below |

What Sapphire can do with it:

```
device_action("kitchen","speaker","say","Dinner is ready")
device_action("kitchen","speaker","volume","80")
device_action("kitchen","speaker","sound","ping")
device_action("kitchen","mic","listen","10")
device_action("kitchen","light","set","cyan blink 5s")
device_action("kitchen","light","rest","sapphire heartbeat bpm=33")
device_action("kitchen","wake","off")
device_action("kitchen","camera","look")
device_action("kitchen","power","restart")
```

**The camera.** `look` takes one picture and she sees it. The ring spins red and
the satellite pings for 2 seconds first, so nobody in the room is caught
unaware. The **Try** button on the Camera tab shows you the same picture.

**The light.** `set` holds a color and animation for a time, 5 minutes if you
give none. `clear` ends it early. `rest` sets the resting light the ring
returns to, and it is kept after a restart. Fine settings are written
`name=value`: `bpm=40 speed=fast floor=0.05 ceiling=0.4`.

**Its looks** are settings on the Light tab, in the same words, one for each
state: Resting, Listening, Thinking, Speaking, No link to Sapphire. `rainbow`
is a color too, and `rainbow` is an animation.

```
Thinking   rainbow spin
Speaking   green spin bpm=90 ceiling=0.6
```

**Lights on from / until** are clock times. Outside them the ring rests dark.
A state still shows while she is answering, and what she `set`s shows too, so
"a white light for the night" works at 2 am. Both empty = always on. The
board keeps the time from Sapphire, no internet clock. (A Pi body keeps its
own looks and night light; these settings are for boards that take them.)

**She knows where a voice came from.** Every question a satellite hears
arrives with one line above it, and you see that line in the chat too:

```
[Voice from device "kitchen" (Kitchen Pi). Location: Kitchen. Your reply is spoken aloud there.]
What time is it?
```

**The satellite does not wait for her answer.** It is told at once that the
question was heard. Her answer is spoken when she has one, however long that
takes.

**The light ring follows its own question only.**

| Ring shows | Meaning |
|---|---|
| Thinking | She is working on what this satellite heard |
| Tool | She is using a tool for it |
| Speaking | Her answer is playing here |
| Error, a short red blink | This satellite got no answer. The log says why |

A question typed in the browser, or asked at another satellite, does not move
this ring.

**A busy chat.** If the chat is in the middle of a turn, the question waits up
to 20 seconds for it. After that it is dropped and the ring blinks red.

**Private chats.** A private chat never sends audio to a cloud speech engine,
and never sends its reply to a cloud voice engine. The satellite stays silent
and the reason is logged.

### Give a Pi its own key

1. Make up a long key. Enter it as **Key the satellite sends** on the
   satellite's Mic tab, and save.
2. On the Pi, add two lines to `/etc/default/sapph-body`:

   ```
   SAPPH_DEVICE_ID=kitchen
   SAPPH_BRAIN_TOKEN=the-same-key
   ```

   `SAPPH_DEVICE_ID` is the device's name in Settings > Devices.
3. Restart the Pi: `sudo reboot`. Restarting only the service can reset a Pi
   whose sound card driver has not been fixed.

The Status tab then reads `link to Sapphire: connected`.

A satellite without a key cannot reach Sapphire at all.

**Addresses a satellite can call.**

| Address | What it is for |
|---|---|
| `POST /api/devices/<name>/voice` | What it heard. Proven with its own key |
| `GET /api/devices/<name>/events` | What its light should show. Proven with its own key |

### Coming from the Body plugin

The Body plugin and its `/api/body/` addresses are retired. Satellites replace
them. A Pi that still runs the old setup must be moved over before it can be
heard again:

1. Update Sapphire first. The new addresses only exist in the new version.
2. Add the Pi in Settings > Devices as a **Satellite**, with a key.
3. Put the new `main.py` and `body_events.py` on the Pi, and give it its two
   settings as described in "Give a Pi its own key".
4. Put `device_list`, `device_status` and `device_action` in the toolsets she
   uses, in place of the `body_` tools.

| Body tool | Now |
|---|---|
| `body_speak` | `speaker` / `say`, `speaker` / `sound` |
| `body_hear` | `mic` / `listen` |
| `body_see` | `camera` / `look` |
| `body_ring` | `light` / `set`, `clear`, `off`, `options` |
| `body_set_baseline` | `light` / `rest` |
| `body_health` | `device_status` |

## WiFi gadgets

Small boards with a light, a screen, or a button. The gadget plugin ships the
program for the board and the setup steps. See its README.

## Hosted Sapphire

Devices are switched off on a hosted Sapphire. It has no home network to reach.

## For plugin authors

See [Device Drivers](plugin-author/devices.md).
