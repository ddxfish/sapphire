# Discord Plugin

Connect Sapphire to your Discord server as a **cognitive agent** — not just a message relay. The bot can read and reply in channels, react to mentions, remember people, run proactive schedules (morning greetings, quiet outreach, goodnight), manage presence, understand images and GIFs, and join voice channels for transcription or full conversational voice.

When the plugin is enabled, it starts its own background **daemon** automatically. You do not create a separate daemon entry for the runtime itself — only for message-triggered AI tasks in Schedule.

## Requirements

The plugin installs these dependencies automatically when enabled:

- **py-cord** (with voice support) — Discord gateway and voice
- **davey** + **PyNaCl** — encrypted voice receive (DAVE)
- **dateparser** — local parsing for reminders and commitments
- **vaderSentiment** — better autonomous reaction emoji picks (optional but recommended)

For **conversational voice**, you also need **TTS streaming enabled** in Sapphire Settings. Without it the bot can listen and transcribe but stays silent in conversational mode.

## Quick Start

### 1. Create a Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** and give it a name
3. Open the **Bot** tab → **Add Bot**
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent** (required — the bot cannot read message text without this)
   - **Server Members Intent** (recommended — needed to populate the bot allowlist picker for bot-to-bot debates)
5. Copy the bot token

### 2. Invite the bot to your server

In **OAuth2 → URL Generator**:

- **Scopes:** `bot`
- **Permissions (minimum for text):** `View Channels`, `Read Message History`, `Send Messages`
- **Additional permissions** if you use those features:
  - `Add Reactions` — silent/autonomous reactions
  - `Attach Files` — file uploads via tools
  - `Connect`, `Speak`, `Use Voice Activity` — voice features

Open the generated URL and add the bot to your server.

### 3. Enable and configure in Sapphire

1. Go to **Settings → Plugins → Discord** and enable the plugin
2. Reload the plugin if prompted — the settings page shows **Daemon is running** when healthy
3. Under **Bot Accounts**, enter a name and paste the token → **Add Bot**
4. Confirm the account shows **connected**

### 4. Create a message daemon task

1. Go to **Schedule → + New Task → Daemon**
2. Set source to **Discord Message**
3. Select which **Bot Account** to listen on
4. Set filters (see [Daemon filters](#daemon-auto-reply)) — start with `{"mentioned": "true"}` for a safe default
5. Enable **Auto-reply in channel** if you want responses posted to Discord
6. Set a prompt, toolset, and optionally a named chat

## Bot Accounts & Scopes

You can connect multiple Discord bots. Each account gets its own scope in the Chat Settings sidebar dropdown (`{bot_name}`).

1. Add accounts under **Settings → Plugins → Discord**
2. Switch between them using the Discord scope dropdown in Chat Settings
3. Each daemon task binds to one bot account — only that bot receives events for that task

Tools and auto-replies route to the active scope, the daemon event's account, or the only connected account when unambiguous.

## Plugin Settings

Open **Settings → Plugins → Discord** for the full settings UI. Settings are saved globally (guild/channel/DM overrides are supported by the API for advanced use).

| Tab | What it controls |
|-----|------------------|
| **Cognitive** | Intention engine before replies; LLM provider for Discord text/voice; task follow-ups, commitments, reminders, birthday wishes; affect modulation |
| **Conversation** | Reply mode (mentions only, etc.); name match without @; message batching; typing/read delays; bot-to-bot debate allowlist and session limits |
| **Social** | Silent sentiment reactions; human delivery (auto typos, quote-replies, post-send edits) |
| **Proactive** | Morning greetings, quiet outreach, sleep/goodnight schedule; greeting channel targets; forced-wake mention buffering; test buttons and diagnostics |
| **Presence** | Discord status and activity while awake, quiet, or sleeping; activity cycling |
| **Safety** | DM policy, per-channel reply cooldown, proactive cooldown, quiet hours |
| **Media** | GIF search (Klipy/Giphy/Tenor), image understanding (vision API), meme responses |
| **Voice** | Voice modes, transcription, speaking, auto-join targets, conversational prompt template |
| **Retention** | SQLite retention for messages, traces, and voice transcripts |

### Reply gating (two layers)

1. **Conversation → Reply mode** is the hard gate (`mentions_only`, `default`, or `disabled`)
2. **Cognitive mode** filters further (`conservative`, `integrated`, `expressive`) — it cannot override a blocked reply mode

With **Mentions only**, the bot only replies when @mentioned or when **Respond to bot name** (soft mention) is enabled.

## Daemon (Auto-Reply)

The daemon listens for Discord messages in the background and can trigger Sapphire continuity tasks automatically.

### Task fields

| Field | Description |
|-------|-------------|
| **Bot Account** | Which bot to listen on (required) |
| **Auto-reply in channel** | ON: AI response posts to Discord. OFF: listen-only (observations/memory still run) |
| **Reply cooldown (seconds)** | Minimum time between replies in the same channel (0 = no limit) |

### Filters

All filters are AND'd — every condition must match.

| Filter | What it matches |
|--------|-----------------|
| `mentioned` | `"true"` or `"false"` — was the bot @mentioned |
| `guild_name` | Server name |
| `guild_id` | Server ID (exact match) |
| `channel_name` | Channel name |
| `channel_id` | Channel ID (exact match) |
| `username` | Message author |
| `content_contains` | Substring in message text |
| `channel_name_not` | Exclude a channel |
| `guild_name_not` | Exclude a server |
| `username_not` | Exclude a user (useful for ignoring other bots) |

### Examples

**Respond to mentions (except #rules):**

```json
{"mentioned": "true", "channel_name_not": "rules"}
```

**Watch a channel silently:**

```json
{"channel_name": "announcements"}
```

Set **Auto-reply in channel** to OFF. The AI reads messages and can save observations without posting.

## Scheduled Jobs

These run via Sapphire's continuity scheduler (not the message daemon):

| Job | Schedule | Description |
|-----|----------|-------------|
| `morning_greeting` | Every hour (`0 * * * *`) | Posts configured morning greetings when the server-local hour matches |
| `quiet_outreach` | Every 15 min | Conversation starters when selected channels go quiet |
| `sleep_goodnight` | Every 15 min | Goodnight messages and sleep state at the configured sleep hour |

Configure targets and hours under **Proactive** in plugin settings. Use the **Test proactive pathways** panel to dry-run or fire greetings, goodnight, and outreach manually.

Proactive behaviour uses **server local time** for hour checks.

## Available Tools

Add Discord tools to your active toolset. If you omit `channel`, tools use the current scope or daemon event channel.

| Tool | What it does |
|------|--------------|
| `discord_get_servers` | List servers the bot is in |
| `discord_read_messages` | Read recent messages from a channel (1–50, default 20) |
| `discord_send_message` | Send a message (max 2000 chars); supports quote-replies via `reply_to_message_id` |
| `discord_upload_file` | Upload a file with optional caption |
| `discord_send_gif` | Send a GIF by search query or URL (requires GIF API key in Media settings) |
| `discord_add_reaction` | Add an emoji reaction to a message |

Channel arguments accept a numeric channel ID or `#channel-name`.

## Voice

Voice features require py-cord + davey and appropriate Discord permissions. Enable under **Voice** in plugin settings.

### Voice modes

| Mode | Behaviour |
|------|-----------|
| `listen_only` | Join and listen; minimal processing |
| `transcribe_only` | Transcribe speech to text |
| `summarize_only` | Transcribe and summarize |
| `conversational` | Full two-way voice using Sapphire's streaming LLM + TTS |

**Auto-join** polls selected voice channels every ~15s and joins when someone is present (disabled during sleep hours).

### Conversational voice

Prerequisites:

1. Voice enabled, **Speaking** on, mode set to **Conversational**
2. TTS streaming enabled in Sapphire Settings
3. Bot has Connect + Speak permissions

**Addressing modes** (configured in settings model; see operator doc for details):

- `bot_name` (default) — replies when someone says the bot's display name or an alias
- `always` — replies to every completed speech turn

Each voice channel gets a dedicated Sapphire chat: `discord:{guild_id}:{channel_id}`. Voice turns persist there, not in the guild text channel chat.

For diagnostics and troubleshooting, see [docs/discord_voice_conversation_operator.md](docs/discord_voice_conversation_operator.md).

## Memory, Profiles & Cognitive Features

The plugin maintains local SQLite storage for messages, traces, profiles, and voice transcripts.

- **Profiles** — user facts and relationship/affect scores; optional birthday capture from natural language
- **Birthday wishes** — scheduled individually per person with spread across the day; bulk mode for busy days
- **Commitments** — detects future promises in channel messages and schedules follow-ups
- **Reminders** — "remind me in 5 minutes" style requests queue world-model tasks
- **Task follow-ups** — scheduler delivers due tasks via the message daemon (requires auto-reply task)

Retention defaults: messages 90 days, traces 14 days, transcripts 30 days. Purge via operator admin API or retention settings.

## Example Chat Commands

- "What's happening in the general channel?"
- "Send a message to #dev saying the deploy is done"
- "Read the last 30 messages in #support"
- "What servers are you in?"
- "React with 👍 to the last message in #general"

## Operator & Diagnostics

The settings page includes an **Operator debug** panel with health, summary, and trace counts.

Useful API endpoints (under `/api/plugin/discord/`):

| Endpoint | Purpose |
|----------|---------|
| `GET health` | Daemon health state |
| `GET traces` | Recent plugin traces |
| `GET voice/diagnostics` | Voice stack, active sessions, event bridge |
| `GET voice/sessions` | Active voice sessions with chat names |
| `GET proactive/diagnostics` | Why scheduled proactive jobs may have skipped |
| `POST proactive/test` | Manually test greeting/goodnight/outreach |
| `GET admin/summary` | Operator summary (health, affect, tasks, voice) |
| `POST admin/purge` | Apply retention purge immediately |
| `POST admin/forget-user` | GDPR-style user data removal |
| `POST admin/import-leona` | Import memories/profiles from a Leona Discord database |

## Troubleshooting

| Problem | Things to check |
|---------|-----------------|
| **Daemon offline** | Enable the plugin under Settings → Plugins and reload. Check token validity. |
| **Bot shows disconnected** | Invalid or revoked token; network; check account row for `last_error` |
| **Can't see messages** | Message Content Intent enabled; bot has View Channels + Read Message History |
| **Bot never auto-replies** | Daemon task exists with correct bot account; filters match; Reply mode allows the message; cognitive layer not blocking |
| **Tools not available** | Add Discord tools to the active toolset |
| **Daemon not firing** | Try empty filter `{}` first; confirm the selected bot account is connected |
| **Reactions not working** | Add Reactions permission; check Social → Reactions enabled |
| **GIFs fail** | Media → GIF enabled + API key configured |
| **Voice join fails** | Connect/Speak permissions; voice not emergency-disabled; not in sleep mode |
| **Voice transcribes but silent** | Speaking enabled; TTS streaming on in Sapphire; addressing mode may require saying the bot's name |
| **Bot allowlist empty** | Enable Server Members Intent; refresh picker with daemon running |
| **Proactive skipped** | Check Proactive diagnostics — wrong hour, no greeting targets, daemon offline, or channel asleep |

## Related Documentation

- [Voice operator guide](docs/discord_voice_conversation_operator.md)
- [Voice roadmap](docs/discord_voice_conversation_roadmap.md)
- Phase docs: [01 streaming TTS](docs/discord_voice_conversation_phase_01_streaming_tts.md), [02 core conversation](docs/discord_voice_conversation_phase_02_core_conversation.md), [03 integration](docs/discord_voice_conversation_phase_03_integration_cleanup.md)
