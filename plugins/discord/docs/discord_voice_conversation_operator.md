# Discord Voice Conversation — Operator Guide

Conversational voice uses Sapphire core's `ConversationDriver` (streaming LLM + TTS) via a **plugin-local** runner. No core file edits required.

## Prerequisites

1. **py-cord + davey** — voice receive/playback (see plugin logs for `Voice stack:` line on startup)
2. **TTS streaming enabled** in Sapphire Settings — without it the bot stays silent in conversational mode
3. **Voice enabled** in Discord plugin settings: voice on, speaking on, mode `conversational`
4. **`conversation_core_enabled`** — default `true` (uses streaming conversation path)

## Addressing modes

| Mode | Behavior |
|------|----------|
| `bot_name` (default) | Replies only when someone says the bot's display name or an entry in **addressing aliases**. Side chatter is still transcribed. |
| `always` | Replies to every completed speech turn (after VAD endpoint). |

Start-word / wakeword from Sapphire global conversation settings does **not** apply to Discord.

## Chat history

Each voice channel gets a dedicated Sapphire chat: `discord:{guild_id}:{channel_id}`. Voice turns persist there — not in the guild's text channel chat.

## Diagnostics

`GET /api/plugin/discord/voice/diagnostics`

Returns voice stack info, active conversation runner sessions, and event bridge status.

`GET /api/plugin/discord/voice/sessions`

Each session includes `chat_name` and `conversation_active`.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Bot joins but never speaks | `speaking_enabled` off, or `addressing_mode=bot_name` and nobody said its name |
| Bot transcribes but silent replies | TTS streaming disabled in Sapphire |
| Garbled / empty transcripts | DAVE not ready — check `davey` install and py-cord patches in diagnostics |
| `conversation_slot_cap` in logs | Too many concurrent conversational VCs — raise `max_conversation_sessions` or leave a channel |
| Legacy batch replies | Set `conversation_core_enabled: false` to use old utterance → batch TTS path (deprecated) |

## Related docs

- [Roadmap](./discord_voice_conversation_roadmap.md)
- [Phase 01 — Streaming TTS](./discord_voice_conversation_phase_01_streaming_tts.md)
- [Phase 02 — Core conversation](./discord_voice_conversation_phase_02_core_conversation.md)
- [Phase 03 — Integration](./discord_voice_conversation_phase_03_integration_cleanup.md)
