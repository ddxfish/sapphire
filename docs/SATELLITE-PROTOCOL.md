# Satellite Protocol

What a board has to speak to be a [Satellite](DEVICES.md#satellites). Any board
that speaks it is added in Settings > Devices as type **Satellite**. It needs no
driver and no plugin. A Raspberry Pi speaks it, and so can an ESP32.

```
board    --POST /api/devices/<name>/voice-->   Sapphire   what it heard
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
 "wakeword": {"enabled": true, "running": true, "model": "hey_sapphire"},
 "link": {"connected": true}}
```

| Field | Meaning |
|---|---|
| `has` | What this board has. The device shows these tabs and no others, also while the board is offline |
| `plays` | The one sound format it plays. Sapphire converts her voice to it |
| `link.connected` | Its events stream to Sapphire is open |
| `volume`, `temp_c` | Optional readings |
| `led` | `{"state", "animation", "blackout"}`, what the ring shows now |

**`has`** takes these names: `speaker`, `mic`, `light`, `wake`, `camera`,
`power`. A name Sapphire does not know is left out and logged.

**`plays`** has one type today, `audio/wav`, which is always 16 bit PCM. `rate`
is 8000 to 48000. `channels` is 1 or 2. A small board plays this with no
decoder and almost no memory.

A board that states neither is taken as an early Pi: it has everything, and it
gets the sound as the voice engine made it (ogg, wav or mp3), as a form file.

## What Sapphire asks of the board

A board answers only the addresses of what it has.

| Has | Request | What it does |
|---|---|---|
| `speaker` | `POST /audio/speak` | Plays the sound. Answers when it has finished playing, within 150 s |
| `speaker` | `GET /sounds` | Its stored sounds: `{"sounds": [{"name": "ping"}]}` |
| `speaker` | `POST /audio/effect?name=ping` | Plays one stored sound |
| `speaker` | `GET /volume` | `{"volume": 85}`, 0 to 100 |
| `speaker` | `POST /volume?level=80` | Sets it, kept across restarts. A board without it answers 404 |
| `mic` | `GET /audio/listen?vad=true&max_seconds=10` | Records until the speaker stops talking. Answers the recording as a wav |
| `light` | `POST /led` | `{"color", "animation", "duration_s"}`, or `{"state": "off"}`, or `{"state": "idle"}` |
| `light` | `GET /led/spec` | Its colors and animations |
| `light` | `GET` and `PUT /led/baseline` | Its resting light, kept after a restart |
| `light` | `PUT /led/looks` | After a save in Settings > Devices: `{"resting", "listening", "thinking", "speaking", "nolink", "night"}`, each a look, plus `"from"` and `"until"` clock times. A look that is not sent is kept. A board without the door answers 404 and keeps its own |
| `wake` | `GET /wakeword` | `{"enabled", "running", "model"}` |
| `wake` | `POST /wakeword?enabled=true` | Listen for the wake word, or stop |
| `camera` | `GET /camera/snap?b64=true` | `{"data_b64", "width", "height"}`, a JPEG |
| `power` | `POST /power?action=restart` | `restart` or `shutdown`. Answers first, then acts: `{"in_s": 3}` |

**`/audio/speak`** carries the sound as the request body, with its
`Content-Type`, when the board stated `plays`. The board can play it while it
arrives.

**A refusal** is any status from 400 up with `{"detail": "the reason"}`. The
reason is shown as it is. 401 or 403 means the key was wrong.

**What the light shows, first wins:** a state of the board (listening, thinking,
tool, speaking, error); then what she set with `/led`, held for its time, or
dark after `{"state": "off"}` until `{"state": "idle"}`; then, inside the hours,
the resting look, and outside them the `night` look, where the color `off` is
dark. A state always shows, through a blackout and at any hour, so the ring
says when she is listening. The hours are by the board's own local time; the
same `from` and `until` means always on. Built in, before any save: dark from
00:00 to 08:00. The Pi body (0.7.0) and the ESP32 firmware keep this order.

## What the board sends to Sapphire

Sapphire's address is HTTPS with her own certificate, for example
`https://192.168.0.69:8073`.

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
| `{"ok": false, "error": "..."}` | The reason. With `"busy": true`, three questions already wait |
| 401 | Wrong key, or no such device |
| 413 | Larger than 20 MB |

The best recording is 16000 Hz, one channel, 16 bit. Limit: 30 requests a
minute.

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
| `tool` | She is using a tool. `tool_name` names it |
| `idle` | The turn is over |
| `error` | This board got no answer |

A line that starts with `:` arrives every 20 seconds and means nothing. When
the stream ends, open it again. A board only ever hears about its own turns.

## Reference for AI

- Driver: `core/devices/drivers/satellite.py`. It asks `/health` on every status, and at most once a minute before it speaks (`ABOUT_FRESH`).
- `has` is kept by the engine on the device row (`parts[].has`, `engine.capabilities`). `status()` of any driver may answer it.
- Conversion: `core/devices/voice.py` `fit(audio, kind, plays)`, checked by `wanted(plays)`. The only place her voice is converted for a device.
- Doors: `core/routes/devices.py` `devices_voice` (body or form, `AUDIO_BODIES`) and `devices_events`.
- `tests/test_docs_satellite_protocol.py` runs this page's health example through the real driver and checks every address in the table.
