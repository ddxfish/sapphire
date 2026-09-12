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

Archive what she hears as text

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

### Emergency stop

- **Setting key:** `voice.emergency_disabled`
- **Type:** `boolean`
- **Default:** OFF

Kill switch — overrides all voice settings

### Addressing mode

- **Setting key:** `voice.addressing_mode`
- **Type:** `string`
- **Default:** `bot_name`

When she treats speech as meant for her

**Options:**

- `bot_name` — Name required each time
- `always` — Always respond

### Name aliases

- **Setting key:** `voice.addressing_aliases`
- **Type:** `list`
- **Default:** *(empty)*

Extra names that count as hers (sapph, saphire...)

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
