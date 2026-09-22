# Discord Plugin

Connect Sapphire to Discord. The host plugin is the bot itself: accounts, the reply pipeline
(mentions, name match, organic replies, batching, typing), images in and out, GIFs, silent
reactions, and voice channels running on Sapphire's own conversation engine. **What she does on
Discord is configured in Continuity**: chat and greetings are daemon tasks, voice is a Realtime
rule, and each task's persona, provider, model and toolset are the brain behind it. Personality
modules (people memory, birthdays, reminders, typos, presence) live in the separate
`discord-personality` plugin.

When the plugin is enabled it starts its own background daemon. You never add a daemon entry for
the runtime itself — only Continuity tasks for what she should do.

Meticulously crafted by ddxfish and zeebie-the-zebra

## Requirements

The plugin needs a few Python packages, and they are **not** installed for you. When you enable it,
**Settings → Plugins → Discord** shows a **Missing: …** strip with an **Install** button (works in a
conda env or venv; on plain system Python it shows the pip command). Restart Sapphire after installing.

- **py-cord** (with voice support) — installs from a **pinned git commit**, so **Git must be on your PATH**.
- **davey** + **PyNaCl** — encrypted voice receive (DAVE).

> **If you ever installed `discord.py`, remove it first:** `pip uninstall discord.py`. It claims the
> same `discord` import name as py-cord and the two cannot live in one environment.

**ffmpeg is not required.** Her speech is decoded in-process. For voice channels you also need
**TTS streaming enabled** in Sapphire Settings.

## Upgrading

**From 1.x to 2.0:** on first boot the old plugin database is moved aside to
`user/plugin_state/discord/discord.sqlite3.pre-2.0` and a fresh one takes its place with your
**bot accounts (tokens) copied**. Everything else the old versions stored (profiles, memories,
traces, transcripts, per-server overlays) belongs to features that left the host; it stays in the
old file untouched. Your Continuity tasks keep working. See [CHANGELOG.md](CHANGELOG.md) for the
full list and the rollback recipe.

**A hand-installed copy in `user/plugins/discord`** always wins over the shipped one (the log says
`[PLUGINS] 'discord' (user band) shadows the system copy`). Delete that folder and restart.

## Quick Start

### 1. Create a Discord bot

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** tab → **Add Bot**
3. Under **Privileged Gateway Intents** enable **Message Content Intent** (required) and
   **Server Members Intent** (recommended — it fills the bot allowlist picker)
4. Copy the bot token

### 2. Invite the bot to your server

**OAuth2 → URL Generator**: scope `bot`; permissions `View Channels`, `Read Message History`,
`Send Messages`, plus `Add Reactions` (silent reactions) and `Connect`, `Speak`,
`Use Voice Activity` (voice). Open the generated URL and add the bot.

### 3. Enable and add the account

1. **Settings → Plugins → Discord** → enable (install the missing packages if prompted)
2. The settings page shows **Daemon is running** when healthy
3. **Bot Accounts** → name + token → **Add Bot**

A saved bot stays offline until an enabled Continuity task selects it — that is the switch that
decides which bots log in.

### 4. Give her a job in Continuity

**Settings → Continuity → Daemon → + New** with source **Discord: Chat only**, pick the bot, set
filters (start with `mentioned = true`), leave **Auto-reply** on, set the persona / provider /
toolset. She answers in that channel from the next message.

Optional: **Realtime → + New → Discord: Voice channel** for voice (see [Voice](#voice)).

## Continuity sources

| Source | Kind | What it does | Task fields |
|---|---|---|---|
| **Discord: Chat only** | daemon | replies to messages that pass the filter | account, auto_reply |
| **Discord: Greetings** | daemon | posts a good-morning / goodnight at set times, no replies | account, channels, greeting_time, goodnight_time |
| **Discord: All interactions** | daemon | both of the above on one task | account, auto_reply, channels, greeting_time, goodnight_time |
| **Discord: Voice channel** | realtime | turns voice ON for the channels its filter covers | account, auto_join, keep_chat_history |

Chat filters: `mentioned`, `guild_name`, `channel_name`, `username`, `content_contains` and the
`_not` twins, plus `guild_id` / `channel_id`. Voice filters: server / channel name, their `_not`
twins, and the ids. Filters match case-insensitively; a comma list is an allowlist. A task with no
filter hears everything the bot can see.

A bot with a Chat-only task and an All-interactions task answers twice by design.

## Settings (Settings → Plugins → Discord)

| Tab | What it controls |
|---|---|
| **Conversation** | reply mode, human/bot organic chance, name match, batching window, think-tag stripping, typing / read / pause delays, ignored channels, bot-to-bot replies + allowlist |
| **Social** | the `[react:]` tag and silent reactions (chance, cooldown) |
| **Safety** | DMs (off by default) and the per-person daily DM budget, tools stay in server, reply cooldown |
| **Media** | images in (the task's model sees attachments), GIFs (provider, key, filter) |
| **Retention** | the daily purge of stored messages (off by default) |
| **Voice** | turn cues, silence, addressing mode + aliases, follow-up window, barge-in hold, the voice prompt |
| **Debug** | the LLM debug ring (opt-in, in memory) |

## Tools

`discord_get_servers`, `discord_list_channels`, `discord_read_messages`, `discord_send_message`,
`discord_send_image`, `discord_send_gif`, `discord_add_reaction`, `discord_join_voice`,
`discord_leave_voice`. Add them to the task's toolset. Inside a server event her tools reach only
that server (Safety → tools stay in server).

Reply tags she may use in a normal reply: `[react:🔥]` and `[gif:search words]`.

## Voice

Voice is a **Realtime** rule: **Discord: Voice channel** names the bot; its filter says which voice
channels it covers (no filter = all of them); its persona, provider, model and toolset run the
voice chat. She joins on `/voice join` or her join tool, and leaves on `<<HANG UP>>` in her reply,
`/voice leave`, or when the channel empties.

**Auto-join** is opt-in per rule (off by default). On, she walks into a covered channel when a
human is there. A channel she left on purpose is not auto-rejoined until it has emptied once.

Slash commands (registered per bot): `/voice join` (defaults to the channel you are in) and
`/voice leave`.

Voice needs TTS streaming on in Sapphire and a working DAVE stack; the log line
`[DISCORD] Voice stack:` at boot and `GET /api/plugin/discord/voice/status` say what applied.

## Add-ons

Other plugins extend the host through six hooks fired via core's hook runner —
`discord_message_observed`, `discord_prompt_context`, `discord_reply_planned`,
`discord_reply_sent`, `discord_voice_utterance`, `discord_tick` — each carrying
`metadata['api']`, a small facade (send, react, image, presence, join / leave voice, recent
messages). `hooks_out.py` documents the payloads. The `discord-personality` plugin is the
reference add-on.

## Operator & Diagnostics

Routes under `/api/plugin/discord/`:

| Endpoint | Purpose |
|---|---|
| `GET health` | daemon state + connected accounts |
| `GET/POST accounts`, `POST accounts/test`, `DELETE accounts/{name}`, `POST accounts/{name}/test` | bot accounts |
| `GET settings` | daemon state + the built-in voice prompt default |
| `GET channels/text`, `GET voice/targets`, `GET bots/allowlist` | what the connected bots can see (pickers) |
| `GET voice/status` | live sessions, connections, the auto-join view, the voice stack |
| `POST admin/forget-user` | `{user_id}` → delete that person's stored messages |
| `GET debug/llm`, `POST debug/clear` | the LLM debug ring |

See [OPERATIONS.md](OPERATIONS.md) for the runtime, storage, runbooks and recovery.

## Troubleshooting

| Problem | Things to check |
|---|---|
| **"Missing: py-cord…"** | click **Install**, or run the command shown; Git on PATH; `discord.py` not installed |
| **Settings changes do nothing** | an older copy in `user/plugins/discord` shadows the shipped plugin |
| **Bot shows disconnected** | no enabled Continuity task selects it; invalid token; missing portal intents (the account row shows `last_error`) |
| **She never replies** | task exists for that bot; filters match; Reply mode allows it; Debug → LLM debug shows the rejection stage |
| **She ignores DMs** | Safety → Allow DMs is off by default |
| **Voice join fails / "no rule covers it"** | a **Discord: Voice channel** rule for that bot whose filter covers the channel; Connect/Speak permissions |
| **Voice transcribes but silent** | TTS streaming on; addressing mode may require saying her name |
| **Bot allowlist empty** | Server Members Intent; refresh with the daemon running |
| **Reactions not working** | Add Reactions permission; Social → silent reactions on |
| **GIFs fail** | Media → GIF enabled + API key |
