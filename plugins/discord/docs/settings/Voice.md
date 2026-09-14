# Voice

Join, listen, transcribe, and optionally speak in Discord voice channels.

## Settings

### Voice

- **Setting key:** `voice.enabled`
- **Type:** `boolean`
- **Default:** OFF

Let her join voice channels

### Voice mode

- **Setting key:** `voice.mode`
- **Type:** `string`
- **Default:** `listen_only`

What she does in a channel

**Options:**

- `listen_only` — Listen only
- `transcribe_only` — Transcribe only
- `summarize_only` — Summarize only
- `conversational` — Conversational

### Transcription

- **Setting key:** `voice.transcription_enabled`
- **Type:** `boolean`
- **Default:** OFF

Archive what she hears as text. Off means off: conversational voice still hears you, it just keeps no transcript rows. The Transcribe-only and Summarize-only modes archive regardless — archiving is the mode.

### Speaking

- **Setting key:** `voice.speaking_enabled`
- **Type:** `boolean`
- **Default:** OFF

Let her talk back out loud

### Turn cues

- **Setting key:** `voice.turn_cues_enabled`
- **Type:** `boolean`
- **Default:** ON

Tick while she thinks, ding on barge-in

### Keep voice chat history

- **Setting key:** `voice.keep_chat_history`
- **Type:** `boolean`
- **Default:** OFF

Off = a voice channel's chat is deleted 30 min after her last session there. Each voice channel gets its own chat (`discord_<guild>_<channel>`); with this off it is marked ephemeral like a phone call and reaped once idle. A chat with a live conversation is never reaped. Set it per server (guild override) to keep the home server's history and let strangers' fade.

### Emergency stop

- **Setting key:** `voice.emergency_disabled`
- **Type:** `boolean`
- **Default:** OFF

Kill switch — overrides all voice settings

### Addressing: mode

- **Setting key:** `voice.addressing_mode`
- **Type:** `string`
- **Default:** `bot_name`

When she treats speech as meant for her

**Options:**

- `bot_name` — Name required each time
- `always` — Always respond

### Addressing: name aliases

- **Setting key:** `voice.addressing_aliases`
- **Type:** `list`
- **Default:** *(empty)*

Extra names that count as hers (sapph, saphire...)

### Addressing: alone = no name needed

- **Setting key:** `voice.solo_no_name`
- **Type:** `boolean`
- **Default:** ON

One person in the channel with her: everything said is for her. Occupancy is read live from the channel at each utterance. Turn it off for a streamer who talks to chat with her sitting in.

### Addressing: follow-up window (seconds)

- **Setting key:** `voice.follow_up_seconds`
- **Type:** `number`
- **Default:** `20`

After she answers someone, that person can keep talking without her name for this long, counted from the end of her reply (or from the moment she was cut off). Everyone else still needs her name — so in a channel of ten, only the one person she just spoke to holds the window, and only briefly. `0` turns it off.

### Barge-in: hold (ms)

- **Setting key:** `voice.barge_hold_ms`
- **Type:** `number`
- **Default:** `250`

How long you must keep talking over her before she stops. Speech is judged by core's Silero VAD (not raw level); this is the continuous window it must hold. A click or thump can't; a word can. Discord only — the phone and browser keep core's default.

Both addressing settings apply only in `bot_name` mode. In a group, only an addressed utterance interrupts her; two people talking to each other while she answers a third do not cut her off. Set these per server (guild override) when a public server needs stricter rules than your own.

## Leaving on her own

She is never forced to stay. In any voice conversation she can end it by saying her goodbye and writing `<<HANG UP>>` at the end of that reply — the same sentinel as the phone. The tag is never spoken; once her final words have played (and the goodbye chime, if turn cues are on) she leaves the channel through the same door as `/voice leave`. The instruction is always in her voice prompt, even under a custom template, and it works with no tools at all.

## Auto-Join Voice Channels

Custom picker stored as `voice.join_targets` (`account:channelId` voice channels).

While the daemon runs, the plugin polls ~every 15s: join when someone is present, leave when empty (subject to voice being enabled and not emergency-stopped).

## Voice Conversation Prompt

Optional override template for conversational voice (`voice.conversation_prompt_template`). Leaving it equal to the built-in default stores empty (= follow shipped default).

### Related hidden keys

| Key | Default | Meaning |
|-----|---------|---------|
| `voice.min_silence_seconds` | `1.5` | Silence before a speech turn is considered finished |
| `voice.speak_cooldown_seconds` | `2.0` | Minimum gap between spoken replies |
| `voice.rolling_summary_seconds` | `0` | Optional rolling summary window (0 = off) |
| `voice.streaming_playback_enabled` | `true` | Stream TTS playback instead of waiting for full audio |
| `voice.conversation_core_enabled` | `true` | Use Sapphire conversation-core path for voice replies |
