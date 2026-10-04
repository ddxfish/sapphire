# MIDI

Play music with her over MIDI. A hardware synth linked to the machine she runs
on, or the computer's own synth, or both. Regular chat tools, two device types,
and a live tap any page can listen to. Linux only.

## What she can do

| Tool | What it does |
|---|---|
| `midi_play` | Plays notes in the background. Can loop a phrase as a backing beat, and can listen right after for trading phrases. |
| `midi_listen` | Listens to the keys without holding up her reply. What was played arrives as a new message in the same chat. |
| `midi_sound` | Picks a voice by number or name, switches a synth's effects and their levels. `voice="list"` names what the connected synth has. |
| `midi_stop` | Ends her phrase, her loop and any listening. Silences every note. |
| `song_save` | Saves a song she wrote as an MP3 and a MIDI file, and hands back play and download links. Needs no synth. |

The same things are hers through `device_action` once a device is added (below).
That is the better door for her: one tool, the fleet in its description. The
chat tools stay for a chat without devices, and `song_save` is a tool only.

## Setup

1. Install `alsa-utils` on the machine she runs on. Linux only.
2. Link a synth over Bluetooth or plug it in by USB, or plug in a plain MIDI
   keyboard and let the computer be the sound (below).
3. Enable the plugin. Add the device in Settings → Devices, or add the tools to
   the toolset her chat uses.

No Bluetooth address is configured anywhere. A synth is found by its MIDI port
name (`aconnect -l` lists them). Leave **Synth port names** empty and she looks
for every synth she has a profile for; name your own to pin the order.

## Synths she knows: profiles

What a synth calls its 128 voices, and how its effects are switched, is a
profile — one JSON file in `synths/`:

```json
{"id": "fm-1", "name": "M-VAVE FM-1", "port_names": ["FM-1_BLE", "FM-1"],
 "voices": ["BRASS 1", "..."], "fx_channel": 1,
 "effects": {"reverb": {"switch": 4, "levels": {"type": [5, 2], "decay": [6, 100], "mix": [7, 100]}}}}
```

The M-VAVE FM-1 ships as the first. Its voices are the banks loaded on the box
(it cannot report their names, so the list must match what is loaded) and its
six effects — filter, reverb, delay, distortion, chorus, phaser — are
controllers on channel 2.

A synth with no profile still plays everything: voices are General MIDI names
(`piano`, `organ`, `strings`, or a number 1-128, sent as a plain program change)
and `midi_sound` says plainly that it has no effects she knows how to switch.
Adding a synth is adding a file; pull requests with profiles are welcome.

## Other keyboards

She also listens to every other keyboard that is plugged into the same machine.
To hold her to certain ones, name them in **Other keyboards she hears**. When
you have a MIDI keyboard device, its list of what plays through it holds for
her ears too.

A keyboard makes no sound by itself. Add it as a MIDI keyboard device and the
computer makes its sound. Or patch it into a synth to hear it there:

```
aconnect AKM320:0 FM-1:0
```

## Saved songs

`song_save` writes each song to `user/plugin_state/midi_songs/` as `<id>.mid`,
`<id>.mp3` and `<id>.json`. Backups carry that folder. Files are served from
`/api/plugin/midi/song/` behind the normal login. In the chat window a saved
song shows a player and download buttons under the tool result.

Every take she hears is saved too, with the timing and touch it was played with
rather than the rounded notation. Takes render as a General MIDI FM electric
piano.

The MP3 needs `fluidsynth` with a SoundFont, plus `ffmpeg`. Without them the
song is still saved as MIDI. The MP3 is a General MIDI instrument, so it does
not sound like the synth in the room. In a private chat nothing is saved,
because these files live outside the vault.

## Notation

The same in both directions, so what she hears reads like what she plays.

```
C4 E4 G4 C5:2 | [C4 E4 G4]:4 R:1
```

A token is `note[:beats]`, `[chord notes][:beats]` or `R[:beats]` for a rest.
Length defaults to one beat. `C4` is middle C. Barlines are ignored.

## How listening works

A listen waits up to 60 seconds for the first key. It then records up to the
length she asked for and ends early after 5 seconds of quiet. The take is handed
to her as a new turn on the chat that asked. If that chat is mid-turn, delivery
waits up to 3 minutes for it to be free.

## Hearing the keys from a page

A page in the browser on another machine cannot hear a keyboard that is linked
to the machine she runs on. Two routes are its ear, behind the normal login:

| Route | What it gives |
|---|---|
| `GET /api/plugin/midi/tap` | Server-sent events, one per key: `{"type":"note","on":true,"n":60,"v":90,"t":123456.7}`. The first event is `hello` with the clock and the port names. |
| `GET /api/plugin/midi/tap/clock` | `{"now": 123456.7, "ok": true, "ports": "FM-1_BLE"}` |

`t` and `now` are this machine's own clock in milliseconds. A page reads the
clock a few times, keeps the quickest answer, and judges timing in that clock,
so the time a note spends on the network changes when it is drawn and never
whether it counted. The keys heard are the same ones `midi_listen` hears. Up to
four pages may listen at once. Nothing is recorded. The Game Room's Piano
Protagonist uses this for its "Sapphire's keyboard" lane.

## Limits

- She hears notes, rhythm and how hard the keys were struck. She does not hear the sound.
- Timing is rounded to 16th notes on the tempo grid.
- A long note held under a melody is written down as a short note.
- A hardware synth has one sound at a time, shared by her and whoever plays its keys.
- A profile's voice list must match what is loaded on the synth; the synth can't say.
- A listen started from a scheduled task is not protected from overlapping that task's own turn.
- Saved songs are one instrument each, up to 10 minutes. Nothing prunes old songs.

## As devices

Two device types, in Settings → Devices → + Add Device:

**Hardware synth** — the synth linked to her machine. Every action runs the
matching tool above, so both ways share one state: a loop started by
`midi_play` is stopped by the device's `stop`.

```
device_action("synth","notes","play","C4 E4 G4 C5:2 bpm=120")
device_action("synth","notes","loop","C2 R C2 G2 minutes=2")
device_action("synth","notes","listen","30")
device_action("synth","notes","stop")
device_action("synth","sound","voice","list")
device_action("synth","sound","effect","reverb on mix=60 decay=40")
```

**MIDI keyboard (the computer makes its sound)** — a keyboard that makes no
sound of its own plays through the computer: `softsynth.py` runs fluidsynth
with a General MIDI SoundFont. Plugging a keyboard in is all it takes.

It runs only while it has something to play for — presence, a feature of the
device manager:

| What happens | What the synth does |
|---|---|
| A keyboard is plugged in, or a synth links over Bluetooth | Starts if it was off, and links that source. Under half a second |
| A second one arrives | Links it too. Both play together |
| One is pulled out | Every note is ended and every pedal lifted, so nothing rings on |
| The last one leaves | Stays on for 20 seconds, so a replug is not a restart. Then it stops |
| She plays on it with no keyboard here | Starts, and stops 20 seconds after she is done |
| It falls over, or another program cuts a link | Brought back within 15 seconds |
| The plugin is switched off or reloaded, or Sapphire stops | Stops first |

**Plays through it** on the device's Status tab says which sources count:
every MIDI source, or only the ones you tick. The list is live. Hardware goes
by a name that holds across a replug, so two keyboards of the same make are
two entries. Sapphire's own players and the system's own ports are never on
the list. `off` by hand holds until `on`, until she plays, or until a keyboard
is plugged in. One synth serves every MIDI keyboard device.

| Needs | Debian and Ubuntu package |
|---|---|
| `fluidsynth` | `fluidsynth` |
| `aconnect`, `aseqdump`, `aplaymidi`, `aseqsend` | `alsa-utils` |
| a SoundFont | `fluid-soundfont-gm` |

The tools play on the computer's synth when no hardware synth is linked. When
one is, it keeps first place.

The synth opens no network door. It is steered through its own command line,
which only this plugin holds.

**It is gentle on the audio system.** fluidsynth is started at the graph's
rate (48 kHz) with a 256-frame cycle (5 ms), chorus off, and a gain that
cannot clip (0.6 at loudness 100; fluidsynth's own default is 0.2 and it has
no limiter). fluidsynth's defaults — a 64-frame cycle at 44.1 kHz — make a
PipeWire desktop run *every* app at a 1.3 ms quantum while the synth is up,
and anything heavy on the graph (EasyEffects) crackles, keys pressed or not.
Loudness is a quiet slider by design: turn the room up, not the synth.

**The volume slider on the keyboard.** It sends MIDI controller 7, and a pedal
sends controller 11. Both reach the computer's synth scaled to 48-127 in place
of 0-127 (`softsynth.ROUTER`), so they still work and can never reach silence.
Every other controller, the pitch wheel and the sustain pedal pass unchanged.

## Coming from the FM-1 plugin

This plugin replaces `fm1` (0.1–0.7). Tools are `midi_*` in place of `fm1_*`;
the FM-1 is now a profile. Settings, device rows and saved songs do not carry
over: re-enter the port names if you had pinned them, re-add the two devices,
and the old `user/plugin_state/fm1_songs/` folder is yours to delete.
