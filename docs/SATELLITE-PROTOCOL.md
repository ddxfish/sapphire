# Satellite Protocol

What a board has to speak to be a [Satellite](DEVICES.md#satellites). Any board
that speaks it is added in Settings > Devices as type **Satellite**. It needs no
driver and no plugin. A Raspberry Pi speaks it, and so can an ESP32.

```
board    --POST /api/devices/<name>/voice-->   Sapphire   what it heard
board    --POST /api/devices/<name>/press-->   Sapphire   a button set to send her a message
board    --GET  /api/devices/<name>/events-->  Sapphire   what its light should show
Sapphire --GET /health, POST /audio/speak--->  board      status, say, light, ...
```

Everything is plain HTTP with a bearer key, on the local network only.

| Key | Who sends it | Setting in Devices |
|---|---|---|
| The board's key | Sapphire, with every request to the board | Key Sapphire sends |
| The voice key | The board, with every request to Sapphire | Key the satellite sends |

## The board says what it is

`GET /health` is the one address every board must answer.

```json
{"ok": true, "name": "den", "board": "waveshare-s3-audio", "firmware": "0.1.0",
 "has": ["speaker", "mic", "wake"],
 "plays": {"type": "audio/wav", "rate": 16000, "channels": 1},
 "volume": 85, "uptime_s": 61,
 "wakeword": {"enabled": true, "running": true, "model": "hey_sapphire", "format": "tflite"},
 "link": {"connected": true}}
```

| Field | Meaning |
|---|---|
| `has` | What this board has. The device shows these tabs and no others, also while the board is offline |
| `plays` | The one sound format it plays. Sapphire converts her voice to it |
| `link.connected` | Its events stream to Sapphire is open |
| `volume`, `temp_c` | Optional readings |
| `storage` | `{"free_bytes", "total_bytes", "can": ["format"]}` with a card in; a board with a slot but no card may say `{"mounted": false}` (the page then reads "card: none") and leaves `storage` out of `has` |
| `sensors` | With `sensors` in `has`: the board's own sensors as `{"name": number}` (a light sensor's raw value, a battery). Each shows as a reading on the device's page |
| `screen` | When it has one: `{"w", "h", "format": "rgb565be"}`, what `/screen/picture` takes; with `chat` and `background`, the chat name and scene it shows now |
| `firmware`, `model`, `slot` | The program's version (`"0.3.0"`), which program it is (`"pocket"`, `"satellite"`: the firmware source's board id, so Sapphire knows what to send it), and the slot running it (`"ota_0"`). With `firmware` in `has` the board takes a new program over the air (below). A board with one program slot (a builder who kept the whole flash for the program, a small chip) leaves `firmware` out of `has`: the device then reads "updates: over USB only" and gets no Firmware tab. The person can also turn updates off per device in Sapphire (the Status tab's switch) whatever the board says |
| `mac` | The chip's own id, `"aa:bb:cc:dd:ee:01"`. The flasher reads the same over USB: a board is known by it whatever it is called |
| `led` | `{"state", "animation", "blackout"}`, what the ring shows now |
| `buttons` | With `buttons` in `has`: `{"list": [{"name": "boot", "short": "keyboard", "long": "clear"}], "can": {"keyboard": "Show or hide the keyboard", "clear": "Clear the chat window"}}`. `list` names each button and the job it does by itself for a short, long or double press (left out = nothing). An entry may state `"ways": ["short"]`: the only ways that button can be pressed (a place on a touch screen is tapped, never held), and the page offers no others. `can` is every job the board can do by itself, with the words the Devices page shows for it. See "A button was pressed" |
| `wakeword.format` | Which model family the board runs: `tflite` (microWakeWord, an ESP32) or `onnx` (openWakeWord, a Pi). The Wakeword Maker sends that family to it. A board that leaves it out is taken for a Pi unless its `board` name says ESP32 |
| `storage` | `{"free_bytes", "total_bytes", "can": ["format"]}` when the board has a card or a folder for backups. `can` names what it does beyond the four doors below: `format` on a board that can wipe its card (an ESP32); a Pi never says it. The device shows a Format button only then |

**`has`** takes these names: `speaker`, `mic`, `light`, `wake`, `camera`,
`power`, `storage`, `screen`, `keyboard`, `buttons`, `sensors`, `firmware`. A name Sapphire does not know is left out and logged.
`storage` is said only while the card is mounted: no card, no Backup tab.
`keyboard` is a board that types at her instead of listening (a pocket
terminal, `tmp/pocket-esp32`): it gets the mic's key and chat settings, and
her reply goes to its `screen` as she writes it (below).

**`plays`** has one type today, `audio/wav`, which is always 16 bit PCM. `rate`
is 8000 to 48000. `channels` is 1 or 2. A small board plays this with no
decoder and almost no memory.

A board that states neither is taken as an early Pi: it has everything, and it
gets the sound as the voice engine made it (ogg, wav or mp3), as a form file.

## What a board takes

Sapphire shows a setting or an action only on a board that says it takes it
(`_takes` in the satellite driver; the engine keeps the list with the device,
so it holds while the board is off):

- each key in `/health` `screen` is a screen setting it takes (`brightness`, `dim`, `dim_after_s`, `off_after_min`, `flip`)
- `screen` with `w`, `h` and `format`: it takes a picture
- `mic.max_s`: it takes the longest question
- `led.looks`, a list of look names: the only looks it takes. A board that lists none takes them all

## What Sapphire asks of the board

A board answers only the addresses of what it has.

| Has | Request | What it does |
|---|---|---|
| `speaker` | `POST /audio/speak` | Plays the sound. Answers when it has taken the sound, within 150 s - a board that plays as it goes may still be sounding its last second: `{"ok": true}`, or `{"ok": true, "stopped": true}` when the board's own button cut it short |
| `speaker` | `POST /audio/stop` | Cuts what is playing and refuses the rest of that reply for a few seconds: `{"stopped": true}`, or `{"stopped": false}` when nothing was playing. A board without it answers 404; Sapphire then only stops sending |
| `speaker` | `GET /sounds` | Its stored sounds: `{"sounds": [{"name": "ping"}]}` |
| `speaker` | `POST /audio/effect?name=ping` | Plays one stored sound |
| `speaker` | `GET /volume` | `{"volume": 85}`, 0 to 100 |
| `speaker` | `POST /volume?level=80` | Sets it, kept across restarts. A board without it answers 404 |
| `mic` | `GET /audio/listen?vad=true&max_seconds=10` | Records until the speaker stops talking. Answers the recording as a wav. A body may also take `lead_in` (seconds before the mic opens, default its own cue) and `tone=false` (no ping): the Wakeword Maker asks for `lead_in=0&tone=false` because it tells the person itself when to speak |
| `mic` | `GET /mic`, `POST /mic?gain=36`, `POST /mic?agc=on`, `POST /mic?max_s=30` | The microphones' gain in dB (`gain_db`, `gain_max_db`), the board's automatic gain control, and `max_s`: the seconds at which a question is cut off however long the talk (5 to 60, kept). A board that takes `max_s` states it in `/health` as `mic.max_s`, and only such a board is sent it, when the device is saved. Optional: a body without the door answers 404 and the driver says so |
| `light` | `POST /led` | `{"color", "animation", "duration_s"}`, or `{"state": "off"}`, or `{"state": "idle"}` |
| `light` | `GET /led/spec` | Its colors and animations |
| `light` | `GET` and `PUT /led/baseline` | Its resting light, kept after a restart |
| `light` | `PUT /led/looks` | After a save in Settings > Devices: `{"resting", "listening", "thinking", "tool", "speaking", "nolink", "night"}`, each a look, plus `"from"` and `"until"` clock times. A look that is not sent is kept. A board without the door answers 404 and keeps its own |
| `wake` | `GET /wakeword` | `{"enabled", "running", "model"}` |
| `wake` | `POST /wakeword?enabled=true` | Listen for the wake word, or stop |
| `wake` | `PUT /wakeword/model?name=hey_marcus&threshold=0.97&format=tflite&phrase=hey+marcus` | A new wake word model: the bytes are the request body (`application/octet-stream`). `format` is `tflite` (microWakeWord, for an ESP32; `sliding_window` and `step_ms` ride along) or `onnx` (openWakeWord, for a Pi). The body keeps it across restarts, listens for it from then on, and answers `{"model": name, "threshold"}`; `GET /wakeword` reports the new name. The Wakeword Maker's Install page sends this. A body without the door answers 404 and the page says to copy the files by hand |
| `camera` | `GET /camera/snap?b64=true` | `{"data_b64", "width", "height"}`, a JPEG |
| `power` | `POST /power?action=restart` | `restart` or `shutdown`. Answers first, then acts: `{"in_s": 3}` |
| `storage` | `GET /storage` | What is kept: `{"free_bytes", "total_bytes", "path", "files": [{"name", "size", "mtime"}], "can": []}`. A board that reads its backups back says `"check"` in `can`, adds `"ok": true` or `false` to each backup it holds a hash for (`false` = it no longer matches: damaged; Sapphire removes those before her next backup lands), and `"checks": {"running", "done", "of", "damaged", "unchecked"}` for the check it runs after every start. The same block, without `files`, is `storage` in `/health`, with `count`, `writing` and `failed` (the last backup sent did not land). The hash files themselves (`name.sha256`) are the board's own and never listed |
| `storage` | `PUT /storage/{name}` | A backup onto the card: the bytes are the request body (`application/octet-stream`, `Content-Length` required, `X-Sha256` optional). The board writes `name.partial`, renames when complete, answers `{"ok": true, "name", "size", "sha256"}`. It REFUSES with 400 any name that is not `sapphire_*.sapphirebak` carrying the `SAPPHIREBAK` magic in its first 12 bytes, except the four opener files (`README.txt`, `open-backup.sh`, `open-backup.bat`, `decrypt_backup.py`): plaintext backups never land on a satellite, even if Sapphire has a bug. 409 while another PUT runs, 411 without a length, 507 when it does not fit. A 70 MB backup takes a minute or two over WiFi; the board must still answer `/health` meanwhile |
| `storage` | `GET /storage/{name}` | The bytes back, for a restore |
| `storage` | `DELETE /storage/{name}` | Rotation: Sapphire keeps the counts set on the device page and drops the oldest |
| `storage` | `POST /storage/check` | Only a board whose `storage.can` lists `check`: reads every backup back now and compares it with its hash. Answers `{"ok": true, "checking": true}` at once and runs by itself (`storage.checks` in `/health` says how far); 409 while one is running |
| `storage` | `POST /storage/format` | Only a board whose `storage.can` lists `format`: wipes the card and formats it FAT32. The person at the Devices page runs it, never Sapphire |
| `screen` | `POST /screen` | `{"text", "seconds"}`: a line across the top of its screen for that long (20 s if left out), or `{"clear": true}`. Answers `{"ok": true, "seconds"}`. Her `screen` / `show` action |
| `screen` | `POST /screen/picture?w=&h=&seconds=` | The whole glass: the body is `w` x `h` pixels of RGB565, big-endian (`application/octet-stream`), up to the size `/health` states in `screen: {"w", "h", "format": "rgb565be"}`; the board centres it on black and paints it as it arrives, no frame buffer needed, until a tap or `seconds` (60). Sapphire fits and packs the image (`satellite.rgb565`). Her `screen` / `picture` action |
| `screen` | `POST /screen/background?w=&h=&name=` | The scene behind its clock: the body is `w` x `h` pixels of RGB565, big-endian, cropped by Sapphire to fill the glass; `name` is the scene's name and comes back in `/health` as `screen.background`. `{"clear": true}` as JSON takes it away. Sapphire sends it when the board links, and when its chat or that chat's scene changes (`core/devices/glass.py`). |
| `screen` | `POST /screen` with `{"chat", "brain", "trim"}` | The chat the board talks in: its name, shown on the glass (`""` hides it), the name of the model that answers in it, and its trim color `"#rrggbb"` (`""` = none). They come back as `screen.chat`, `.brain`, `.trim`; only the ones a board states are compared. A board that states no `screen.chat` in `/health` is sent neither the name nor a scene. A private chat is never named or pictured. |
| `screen` | `PUT /screen/settings` | `{"brightness", "dim", "dim_after_s", "off_after_min", "flip"}`, any of them: the backlight in use and when left alone (percent), when it dims (seconds, 0 = never) and when it goes dark (minutes, 0 = never), and `flip` (true or false) to turn the picture and its touch 180 degrees. A board with only `flip` (the pocket, the backup stick) answers `{"flip", "restarting"}` and restarts to draw that way; it refuses with 409 while its card is being written. Kept on the board across a restart; `/health` states them in `screen`. Sent when the device is saved. |
| `firmware` | `PUT /firmware` | A new program, over the air: the bytes are the body (`application/octet-stream`, `Content-Length` required, `X-Sha256` of the whole body). The board writes them into the slot not running, checks the image and the sha256, marks that slot to boot and answers `{"ok": true, "restarting": true, "slot": "ota_1", "sha256"}`, then restarts onto it. Rollback: the new program is on trial until it says it is fine (WiFi joined, or 90 s up); a program that crashes before that is replaced by the old one at the next boot. 413 when it does not fit the slot, 422 when it is not an ESP image or the sha differs (nothing is changed then), 501 on a board with one slot. Her `firmware` / `update` action sends the source's newest; the person at the Devices page runs it, never she on her own |
| `sensors` | `GET /sensors` | What it measures, now: `{"sensors": {"light": 1234, "temp_c": 23.5}}`. A name ends in its unit; a light sensor with no calibration sends its raw reading. The same object in `/health` feeds the readings on the device's page. Her `sensors` / `read` action |
| `keyboard` | nothing | Sapphire asks nothing of a keyboard. The board sends what was typed (below) and pulls her reply |
| `buttons` | nothing | Sapphire asks nothing of a button. The board is told on its events stream what each press is set to, does what it can by itself, and sends her the presses set to reach her (below) |

**`/audio/speak`** carries the sound as the request body, with its
`Content-Type`, when the board stated `plays`. The board can play it while it
arrives.

**Her reply comes one sentence at a time.** While she is still writing, each
finished sentence is rendered, trimmed to a breath of silence at each end
(a sentence rendered alone carries ~0.3 s before and ~0.4 s after; joined,
that was a hole at every joint) and sent as its own `/audio/speak`, in
order, the next one only after the board has answered the last. The first
sound arrives a second or two after her first sentence, not after her whole
reply.
A board keeps its speaker open across the sentences of one reply (the Pi
body feeds them into one `aplay`; opening a Bluetooth speaker costs half a
second, which was a hole between every two sentences) and answers each
`/audio/speak` once the sound is taken, so the next sentence is already
there before the last has finished. Between two sentences the turn is still
hers: when the sound has ended the board goes back to the brain's last cue
from the events stream (thinking, tool) and only to idle when that stream
says so. A `stopped` answer ends the rest of that reply; nothing more is
sent for it, and the board refuses what was already on its way. A board
whose driver cannot take sound this way hears the reply whole, at the end,
as before.

**A refusal** is any status from 400 up with `{"detail": "the reason"}`. The
reason is shown as it is. 401 or 403 means the key was wrong.

**What the light shows, first wins:** a state of the board (listening, thinking,
tool, speaking, error); then what she set with `/led`, held for its time, or
dark after `{"state": "off"}` until `{"state": "idle"}`; then, inside the hours,
the resting look, and outside them the `night` look, where the color `off` is
dark. A state always shows, through a blackout and at any hour, so the ring
says when she is listening. The hours are by the board's own local time; the
same `from` and `until` means always on. Built in, before any save: dark from
00:00 to 08:00, tool purple, speaking cyan. The Pi body (0.7.2) and the ESP32
firmware keep this order, and both state `plays`.

## What the board sends to Sapphire

Sapphire's address is HTTPS with her own certificate, for example
`https://192.168.1.101:8073`.

**Its own address is learned.** Every call below carries the board's key,
so the address it calls from is where it lives: Sapphire writes that into
the device's `url` when the device has none (a board set up from the
browser) or when the host changed (a new DHCP lease), keeping the scheme and
port it had. Only an address on the local network is believed, and a device
that still answers the health keeper at the address it has is not moved:
the keeper is asked to look again, and the new address is taken once two
probes in a row have missed (about a minute). So an address typed behind a
gateway or a proxy stays, and a caller that only holds the board's key
cannot draw Sapphire's traffic its way while the board is up. A board
therefore never needs its address typed, and a board that moves is
followed. Drivers opt in with `learns_address` in their SPEC.

**New keys take effect when the board brings them.** Setting a board up
again (`POST /api/devices/provision` for a name that exists) mints keys
that wait, in memory, for 15 minutes: the device keeps working on its old
ones. The first call that carries the new key (`voice.key_ok` →
`engine.promote`) makes them the device's keys, renames the device when
the board was given a new name, and records the board's id as its
fingerprint. A name held by a board that cannot be proven to be this one
needs `replace: true`, which the page asks for.

**Setting a board up.** Sapphire's own firmware takes its settings over
its USB console, one line in and one `>> {json}` line back: `setup {json}`
with `name`, `wifi_ssid`, `wifi_password`, `key` (the key Sapphire sends),
`voice_key` (the key the board sends), `sapphire` (her address) and `cert`
(her certificate, PEM); a field left out keeps its old value, and the board
saves and restarts. `show` answers what it has (`ready`, `ip`, `firmware`,
the names of what is set). `scan` answers `{"networks": [{"ssid", "rssi"}]}`
so a flasher can offer the WiFi as a list. The Devices page's flasher
speaks this over Web Serial after writing the firmware; `setup_board.py`
speaks it from a terminal.

### Its wake word fired

Two boards in one room hear the same wake word, and so does Sapphire's own
microphone. Before it has anything to send, a board may ask for the wake:

```
POST /api/devices/den/wake
Authorization: Bearer <voice key>
```

| Answer | Meaning |
|---|---|
| `{"ok": true, "yours": true}` | Record and send as usual |
| `{"ok": true, "yours": false, "taken_by": "pi2"}` | Another listener heard it first. Stop recording, send nothing, show the resting light |

Ask alongside the recording, never before it: the round trip must not delay
the start of listening. A board that does not ask is still answered once
(a repeat of what another listener heard is dropped at `/voice`). If
Sapphire's own microphone takes the wake a moment after a board was told
`yours`, the board's light stream carries `standdown` (below).

### What it heard

After its wake word, the board records until the speaker stops, then sends it:

```
POST /api/devices/den/voice
Authorization: Bearer <voice key>
Content-Type: audio/wav

<the recording>
```

A form with the file field `audio` works too.

| Answer | Meaning |
|---|---|
| `{"ok": true, "heard": "what time is it", "accepted": true}` | A turn has started. Her answer arrives later at `/audio/speak` |
| `{"ok": true, "heard": "", "accepted": false}` | No speech was in it |
| `{"ok": true, "heard": "", "accepted": false, "taken_by": "pi2"}` | Another board, or Sapphire's own microphone, heard the same words first and is answering. Show the resting light; nothing is coming |
| `{"ok": false, "error": "..."}` | The reason. With `"busy": true`, three questions already wait |
| 401 | Wrong key, or no such device |
| 413 | Larger than 20 MB |

The best recording is 16000 Hz, one channel, 16 bit. Limit: 30 requests a
minute.

### What was typed

A board with a keyboard sends the words instead of a recording:

```
POST /api/devices/pocket/text
Authorization: Bearer <voice key>
Content-Type: text/plain; charset=utf-8

what time is it
```

JSON `{"text": "..."}` works too. Up to 2000 characters.

| Answer | Meaning |
|---|---|
| `{"ok": true, "accepted": true, "chat": "default", "msg": "3f9a1c"}` | A turn has started. Her reply arrives on the screen through the stream below |
| `{"ok": false, "error": "..."}` | The reason. With `"busy": true`, three questions already wait |

### A button was pressed

A board with buttons says so in `/health` (`buttons`, above). In Settings >
Devices each way of pressing each button (`short`, `long`, `double`) is left
to the board, or set to one of the jobs the board said it `can` do, or to
`tell`: send Sapphire a message. The board learns what is set from its
events stream, when the stream opens and at every save:

```
data: {"state": "buttons", "bound": {"boot": {"short": "tell", "double": "listen"}}}
```

| A press that is | The board |
|---|---|
| not in `bound` | does its own job for it, the one `list` names |
| a name from its `can` | does that job, by itself, with no call |
| `tell` | calls the door below |
| any other word (`none`) | does nothing |

The board keeps `bound` in memory only: before its stream has opened, and
on a board Sapphire never reached, every press is the board's own. A long
press is one held 0.8 s and fires while still held. A short press fires on
release; only on a button with a `double` in `bound` does it wait 0.3 s for
a second press first. Never give a press a job that restarts the board: a
button held through a restart is the chip's download mode on an ESP32.

```
POST /api/devices/pocket/press
Authorization: Bearer <voice key>
Content-Type: application/json

{"button": "boot", "how": "short"}
```

| Answer | Meaning |
|---|---|
| `{"ok": true, "accepted": true, "chat": "default", "text": "Goodnight", "msg": "3f9a1c"}` | A turn has started with the message set for that press (`text`). Her reply goes to the board's screen when it has a keyboard (`msg`, pulled like a typed question's), else to its speaker, else it stays in the chat |
| `{"ok": true, "accepted": false}` | That press is not set to send anything |
| `{"ok": true, "accepted": true, "job": "backup", "said": "Backup is on its way"}` | The press was a job that is Sapphire's to do, and it has started. `said` is a line short enough for a small screen. No chat turn |

**Jobs that are Sapphire's.** Some work a press asks for cannot be done on
the board: a backup is made by Sapphire. The board lists such a job in `can`
like any other, and when the press is that job it posts
`{"button": "boot", "how": "short", "job": "backup"}`. What the Devices page
set for the press wins; `job` counts only for a press left to the board. The
one job so far is `backup`: a fresh backup, sealed, onto that device's own
card (it needs `storage`). Any other word is `accepted: false`.
| `{"ok": false, "error": "..."}` | The reason. With `"busy": true`, three questions already wait |

**Her reply, for a screen.** Her words never ride the events stream: a
stream that falls behind drops a line, and a dropped line of prose is garbage
on a screen. The stream carries a doorbell, `{"state": "text", "msg", "rev",
"have", "done"}`: which reply, its revision, how many characters there are,
whether she is finished. The board pulls what it lacks:

```
GET /api/devices/pocket/text?msg=3f9a1c&from=120&max=700
Authorization: Bearer <voice key>

{"msg": "3f9a1c", "rev": 0, "from": 120, "text": "...", "have": 410, "done": false}
```

A doorbell comes at most four times a second while she writes, and once more
with `done`. A missed doorbell is made good by the next one. `rev` goes up
when her final text differs from what streamed (a tool round joined, thinking
stripped): the board starts that reply over from `from=0`. `max` is held to
2000. With no `msg`, the latest reply: what a board that just connected
should show. The board keeps the last four replies' worth of pulls honest;
an older `msg` answers with the latest, whose id the board will not match.

### What its light should show

The board holds one stream open:

```
GET /api/devices/den/events
Authorization: Bearer <voice key>
```

It answers server-sent events. Each is one line of JSON after `data: `.

| `state` | Meaning |
|---|---|
| `connected` | The stream is open. `now` is the time in seconds since 1970 and `tz` the zone as a POSIX string (`EST5EDT,M3.2.0,M11.1.0`): a board keeps local time from them |
| `thinking` | She is working on what this board heard |
| `standdown` | Sapphire's own microphone took the wake this board is recording: end the recording, send nothing, resting light |
| `tool` | She is using a tool. `tool_name` names it |
| `idle` | The turn is over |
| `error` | This board got no answer |
| `text` | A doorbell for a board with a keyboard: `msg`, `rev`, `have`, `done`. Pull the words, see "What was typed". Not a light state |
| `buttons` | What each press is set to: `bound`, see "A button was pressed". Sent only to a board that said it has buttons. Not a light state |

A line that starts with `:` arrives every 20 seconds and means nothing. When
the stream ends, open it again. A board only ever hears about its own turns.

## Reference for AI

- Driver: `core/devices/drivers/satellite.py`. It asks `/health` on every status, and at most once a minute before it speaks (`ABOUT_FRESH`).
- `has` is kept by the engine on the device row (`parts[].has`, `engine.capabilities`). `status()` of any driver may answer it.
- Conversion: `core/devices/voice.py` `fit(audio, kind, plays)`, checked by `wanted(plays)`. The only place her voice is converted for a device.
- A board from the browser: `interfaces/web/static/views/settings-tabs/device-flash.js` (two lanes: esptool-js over Web Serial with `shared/md5.js` for the write check, or `core/devices/flasher.py` on Sapphire's computer through `/api/devices/flash/{ports,chip,start,status,ask,close}`; then the console line), the chip's MAC as the device `fingerprint` (`engine.provision`, `/health.mac`, console `show.mac`), `POST /api/devices/provision` (`engine.provision`: the row with no url, two keys, her address: the body's `sapphire` or `GET /api/devices/here`'s guess from `net.local_ips`, tunnels and bridges excluded; `ssl_utils.cert_pem`), `GET /api/devices/firmware[/{board}/{part}]` (`core/devices/firmware.py`, index + ESP Web Tools manifests, cached). The address: `engine.learned`, called by `_device_key` on every device door.
- Doors: `core/routes/devices.py` `devices_voice` (body or form, `AUDIO_BODIES`) and `devices_events`; typed words `devices_text` (POST) and `devices_text_read` (GET), which ride `voice.typed`, `voice.Reply` and `voice.reply_text`.
- Buttons: the `buttons` setting is a `bindings` field (`engine._bound`, `engine._menu`), its menu is the driver's `bindings()` from the board's `/health`; `voice.bound` is what the stream carries (`routes.light_stream` at open, the driver's `apply` at a save), `voice.pressed` the press door (`devices_press`). The chat every lane uses: `voice.chat_for`, which makes a chat that is named and does not exist. Firmware: `main/buttons.c` in each board.
- Backups onto a board: `core/devices/storage.py` makes a `SatelliteTarget` (in the driver) for every device whose `has` says `storage`; `core/backup_targets.ship` seals once, PUTs, rotates by the device's keep fields, drops the openers. Never plaintext: `Target.check` (Sapphire) + the board's own magic check.
- `tests/test_docs_satellite_protocol.py` runs this page's health example through the real driver and checks every address in the table.
