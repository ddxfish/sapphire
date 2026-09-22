# Discord Cognitive — Operations Guide

Operator reference for running, monitoring, and troubleshooting the Discord plugin in production. For setup and feature overview, see [README.md](README.md).

All API routes are prefixed with `/api/plugin/discord/`.

## Runtime Architecture

### Daemon lifecycle

The plugin starts a background daemon automatically when enabled under **Settings → Plugins**. There is no separate daemon entry to create for the runtime itself.

| Health state | Meaning |
|--------------|---------|
| `created` | Container constructed, not yet bootstrapped |
| `starting` | Boot in progress (check `detail` for current step) |
| `ready` | Runtime healthy, accounts connecting |
| `stopping` | Graceful shutdown in progress |
| `stopped` | Daemon fully stopped |
| `error` | Startup failed — check Sapphire logs |

Check status:

- Settings UI banner: **Daemon is running** / **Daemon is offline**
- `GET /health` — `{ state, detail, daemon_running, connected_accounts }`

### Startup order

1. Load settings store from SQLite
2. Open SQLite + run migrations
3. Build repositories, Sapphire bridges, voice patches (py-cord + DAVE)
4. Connect Discord transport and the stored bot accounts that an **enabled daemon task selects** (accounts nothing selects stay logged out; the log says `no enabled daemon task selects it`)
5. Start message pipeline and internal scheduler loop (~15s tick)
6. Mark health `ready`

On startup, logs include a **Voice stack** line (`pycord`, `davey`, `dave_mode`, patch status). If voice receive is unavailable, transcription and conversational voice will not work.

### Shutdown order

Graceful shutdown (plugin disable/reload):

1. Message pipeline
2. Internal scheduler loop
3. Voice event bridge + conversation runner
4. Voice transport disconnects
5. Discord gateway transport close
6. SQLite close

Scheduler tick exceptions are logged and **do not** crash the daemon.

### Internal scheduler (15s tick)

Every ~15 seconds per connected account:

- **Proactive coordinator** — evaluates greeting/outreach/goodnight intentions, task follow-ups, presence updates
- **Voice auto-join** — opt-in per `Discord: Voice channel` rule (`auto_join`): joins covered voice channels when someone is there, leaves any covered channel when it empties; a deliberate leave (<<HANG UP>>, /voice leave, the tool) latches the channel until it has emptied once

Sapphire continuity cron jobs (hourly/15-min) also trigger proactive pathways — see [Scheduled jobs](#scheduled-jobs).

## Storage

### Database location

Default SQLite path:

```
user/plugin_state/discord/discord.sqlite3
```

Legacy installs kept the file at `user/plugin_state/discord_cognitive/discord.sqlite3`; since
2026-09-13 that file is moved into the path above on first boot (sidecars included), and the
plugin never silently starts an empty database beside it.

Override via plugin settings key `database_path` if needed.

### What is stored

| Data | Table / area | Default retention |
|------|--------------|-------------------|
| Message history | `messages` | 90 days |
| Debug traces | `traces` | 14 days |
| Voice transcripts | `voice_transcripts` | 30 days |
| Processed profile buffers | `profile_buffers` | 7 days |
| User profiles & affect | `user_profiles` / `profile_facts` | Until forget-user (facts support soft-forget) |
| Relationship milestones | `relationship_milestones` | Until forget-user |
| Interest topics | `interest_topics` | Until forget-user |
| Shared server lore | `server_lore` | Until soft-forget / delete |
| Pinned memories | `pinned_memories` | Until forget-user |
| World-model tasks | `tasks` | Until completed/purged |
| Proactive sleep state | `proactive_state` | Per channel |
| Bot tokens | `accounts` (plaintext in the plugin's SQLite under `user/plugin_state/discord/` — protect that directory like `settings.json`) | Until account deleted |
| Import audit | `import_audit` | Permanent (idempotency keys) |

Settings overlays (global, guild, channel, DM) are stored in the channel repository and loaded at runtime start.

## Observability

### Traces

`GET /traces?limit=50`

Returns recent structured traces, a summary count by type, and a small snapshot (pending world-model
tasks, active voice sessions). Add `?type=<trace_type>` to filter.

Traces explain **why** the agent acted, skipped, or was blocked. They intentionally exclude full prompt dumps.

#### Primary trace categories

| `trace_type` | When recorded |
|--------------|---------------|
| `intention_generated` | Cognitive layer produced a reply/proactive intention |
| `policy_rejected` | Safety/cooldown/sleep gate blocked an action |
| `memory_injected` | Profile/pinned memory added to prompt context |
| `proactive_action` | Proactive intention executed (via trace service helper) |
| `proactive_sent` | Greeting/outreach/goodnight/task follow-up delivered |
| `proactive_skipped` | Proactive blocked (cooldown, low energy, high irritability, no task) |
| `media_interpreted` | Image/GIF understanding result injected |
| `media_detected` / `media_fallback_used` | Attachment seen or fallback interpretation used |
| `voice_decision` | Speak/listen/block decision in voice pipeline |
| `voice_session_started` / `voice_session_closed` / `voice_session_health` | Voice session lifecycle |
| `voice_reconnect` | Voice session reconnect noted |
| `silent_reaction` | Autonomous emoji reaction without reply |
| `birthday_captured` / `birthday_wish_scheduled` | Birthday profile capture or wish queued |
| `birthday_capture_failed` | Could not parse a birthday from message text |
| `event_emitted` | Message queued to Sapphire continuity task |
| `event_dropped` | Message filtered before LLM (reply mode, sleep, bot gate, policy) |
| `delivery_sent` / `delivery_skipped` / `delivery_failed` / `delivery_empty` / `delivery_edit` | Reply delivery outcome |

### Operator summary

`GET /admin/summary`

Aggregated snapshot:

- Runtime health
- Trace summary (counts by type)
- Pending world-model tasks (up to 10)
- Active voice sessions
- Connected accounts

The settings UI **Operator debug** panel shows a subset of this data inline.

### Logs

Search Sapphire daemon logs for the prefix `[DISCORD]` (one prefix since 2026-09-13; the older `[discord_cognitive]` is gone).

Useful log lines:

- `Daemon started (health=…)`
- `Voice stack: pycord=… davey=…`
- `Failed to connect stored account …`
- `Scheduler tick failed` / `Voice auto-join tick failed`
- `Discord cognitive daemon crashed`

### Profiles

`GET /profiles?account=<name>`

Returns the account's user profiles (up to 50). Per-user facts, milestones, and interests have their
own routes under `/profiles/…`; the settings UI Memory tab wraps all of them.

### Cognition & LLM debug

`GET /debug/cognition` — recent channel situations, intention scores (reply / react / silent), gate
multipliers, and which Cognition switches are on.

`GET /debug/llm?limit=N` and `POST /debug/clear` — the debug ring. It holds the **last 10 full
prompts and replies in memory**, other people's messages included, and stays empty unless
`cognitive.llm_debug_enabled` is turned on. Nothing is written to disk; Clear empties it at once.

## API Reference

### Accounts

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/accounts` | List configured bots (token redacted) |
| `POST` | `/accounts` | Add bot — body: `{ "account_name", "token" }` |
| `POST` | `/accounts/test` | Validate a token before saving it |
| `DELETE` | `/accounts/{name}` | Remove bot and disconnect |
| `POST` | `/accounts/{name}/test` | Validate token and connectivity |

### Settings

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/settings` | Full settings store + resolved effective settings + daemon state |
| `POST` | `/settings` | Save overlay — body: `{ "scope_type", "scope_id", "settings" }` |

Scope types: `global`, `guild`, `channel`, `dm`. The web UI saves `global` only; API supports per-guild/channel/DM overrides.

Query params on GET: `guild_id`, `channel_id`, `dm_id` for resolved preview.

### Health & traces

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Daemon health and connected accounts |
| `GET` | `/traces?limit=N&type=<trace_type>` | Recent traces + counts by type |

### Proactive

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/proactive/diagnostics` | Why scheduled proactive jobs may skip (hour, targets, sleep, accounts) |
| `GET` | `/proactive/targets` | Text channels available for greeting/outreach targets |
| `POST` | `/proactive/test` | Manually fire proactive pathway |

**Test body:**

```json
{
  "kind": "greeting",
  "account_name": "mybot",
  "channel_id": "1234567890",
  "dry_run": false,
  "reset_sleep_state": true
}
```

`kind`: `greeting`, `goodnight`, or `outreach`. With `dry_run: true`, returns preview text without posting.

### Voice

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/voice/sessions?account=<name>` | Active voice sessions + chat names |
| `GET` | `/voice/diagnostics` | Voice stack, conversation runner, event bridge |
| `GET` | `/voice/auto-join?account=<name>` | Auto-join targets and current join state |
| `GET` | `/voice/targets` | Voice channels available for auto-join picker |

See [docs/discord_voice_conversation_operator.md](docs/discord_voice_conversation_operator.md) for conversational voice troubleshooting.

### Presence & bots

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/presence/presets` | Activity preset catalog for presence cycling |
| `GET` | `/bots/allowlist` | Other bots in connected servers (for bot-to-bot allowlist) |

### Admin

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/admin/summary` | Operator snapshot |
| `POST` | `/admin/purge` | Run retention cleanup immediately |
| `POST` | `/admin/forget-user` | GDPR-style user data removal |

### Debug

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/debug/llm?limit=N` | Opt-in LLM debug ring (`cognitive.llm_debug_enabled`) |
| `POST` | `/debug/clear` | Empty the ring |
| `GET` | `/debug/cognition` | Situations, intention scores, gates, and the Cognition switches |

## Scheduled Jobs

Two schedulers cooperate:

### Sapphire continuity cron (plugin.json)

Since 1.25.0 the manifest binds these to their settings (`time_setting` / `enabled_setting`), so a
schedule runs **once, at the configured hour, only while its feature is on** — no more sub-hourly
heartbeats that wake up just to learn the feature is off. Saving plugin settings re-times the live
task; no restart needed.

| Job | Cron | Bound to | Handler |
|-----|------|----------|---------|
| `morning_greeting` | `0 9 * * *` | hour ← `proactive.greeting_utc_hour` | Morning greetings + wake replay |
| `quiet_outreach` | `*/15 * * * *` | on ← `proactive.outreach_enabled` (off by default) | Conversation starters when channels go stale |
| `sleep_goodnight` | `0 22 * * *` | hour ← `proactive.sleep_utc_hour`, on ← `proactive.sleep_schedule_enabled` (off by default) | Goodnight + sleep state |
| `retention_purge` | `30 4 * * *` | always registered | Daily chunked purge of plugin data past the Retention day limits (does nothing until `retention.enabled`) |
| `ambient_distill` | `*/15 * * * *` | on ← `profile.ambient_distill_enabled` (off by default) | Opt-in ambient chat distill into profile facts (honours `profile.ambient_distill_interval_hours`) |

Hour comparisons ride **Sapphire's configured timezone** (`config.USER_TIMEZONE`, Settings → Identity)
— the same clock the continuity cron matches on. An unparsable hour setting is logged and the manifest
cron is kept.

These require the plugin daemon to be running and at least one connected bot account.

### Internal 15s tick

Handles proactive evaluation between cron runs, presence rotation, task follow-up timing, and voice auto-join polling.

## Proactive Diagnostics Runbook

When greetings, outreach, or goodnight do not fire:

1. `GET /proactive/diagnostics` — read `hints` under `greeting`, `outreach`, `goodnight`
2. Confirm daemon running: `GET /health`
3. Confirm greeting channels selected in settings (`greeting_targets`)
4. Confirm connected accounts match target account prefixes (`account:channel_id` format)
5. Check the hour on Sapphire's clock (`server_hour` in the response) vs configured greeting/sleep hours
6. Check sleep state per channel in diagnostics `channels[]` — asleep channels buffer mentions
7. Use `POST /proactive/test` with `dry_run: true` to preview message text
8. Check traces for `proactive_skipped`, and `policy_rejected` with `proactive_cooldown`

Common skip reasons:

| Hint | Fix |
|------|-----|
| `Morning greetings are disabled (proactive.greeting_enabled)` | Enable it |
| `Current hour on Sapphire's clock is X; greetings only fire at hour Y` | Wait for the hour, or change `greeting_utc_hour` (the schedule re-times itself on save) |
| `No greeting channels selected` | Pick targets in Proactive settings |
| `No connected Discord bot accounts` | Fix token / enable plugin |
| `Currently in sleep hours — outreach is skipped` | Expected — outreach suppressed overnight |
| `Sleep schedule is disabled (proactive.sleep_schedule_enabled)` | Enable it; the goodnight task stays switched off until you do |
| `… greeted N min ago — proactive cooldown (Nh) blocks another …` | Wait, or lower `safety.proactive_cooldown_hours` |
| `… no daemon task accepts a proactive post …` | Loosen the daemon task's filters, or add a task for that account/channel |

## Voice Operations Runbook

1. `GET /voice/diagnostics` — confirm `voice_stack.davey` and `voice_sinks` present
2. `GET /voice/auto-join` — confirm targets and join state
3. `GET /voice/sessions` — active sessions and `conversation_active` flag
4. Settings: `voice.enabled`, `voice.speaking_enabled`, `voice.mode`
5. Sapphire: TTS streaming must be enabled for conversational mode
6. Stop everything: turn `voice.enabled` off — she leaves every voice channel on the next tick (the separate "emergency stop" switch was removed; it only blocked new joins)

| Symptom | Check |
|---------|-------|
| Bot joins but never speaks | `speaking_enabled`, `addressing_mode=bot_name` (must say bot name), TTS streaming |
| Garbled transcripts | DAVE not ready — reinstall `davey`, check py-cord patches in diagnostics |
| Auto-join not working | Targets configured, not in sleep mode, daemon running |
| `conversation_slot_cap` in logs | Lower concurrent VCs or raise `max_conversation_sessions` |
| She cuts herself off on coughs, clicks, keyboard noise | Raise `voice.barge_hold_ms` (250 ms default) |
| She answers speech meant for someone else | `voice.solo_no_name` (alone = no name needed) and `voice.follow_up_seconds` decide when a name is optional |
| Nothing lands in `voice_transcripts` | Only `transcribe_only` / `summarize_only` always archive; every other mode needs `voice.transcription_enabled` |
| VC chat disappeared from the sidebar | Expected — VC chats are ephemeral and reap 30 min after the last session unless `voice.keep_chat_history` is on |

## Privacy & Retention

Configure under **Retention** tab or settings overlay key `retention`:

| Setting | Default | Effect |
|---------|---------|--------|
| `enabled` | `false` | Master switch for purge (off = nothing is ever purged) |
| `message_days` | 90 | Delete old `messages` rows, their `media_artifacts`, and processed `sleep_buffer` rows |
| `trace_days` | 14 | Delete old `traces` rows and finished `tasks` (completed / cancelled / failed / expired) |
| `transcript_days` | 30 | Delete old `voice_transcripts`, `voice_summaries`, and closed `voice_sessions` |
| `profile_buffer_days` | 7 | Delete unprocessed `profile_buffers` rows (processed ones are deleted the moment they are distilled) |

Deletes run in chunks of 5,000 rows with a short yield between chunks, so a first purge over a
long-lived database does not stall replies; the WAL is checkpointed afterwards.

### Purge

```http
POST /admin/purge
```

Runs retention cleanup immediately. Returns `{ status, results: { messages, media_artifacts, sleep_buffer, traces, tasks, voice_transcripts, voice_summaries, voice_sessions, profile_buffers } }` (row counts per table).

Skipped when `retention.enabled` is false.

### Forget user

```http
POST /admin/forget-user
Content-Type: application/json

{
  "account_name": "mybot",
  "user_id": "123456789012345678"
}
```

One door (`ForgetService`, since 2026-09-13) removes, in one transaction:

- Profile, facts, ambient buffers, interest topics, and milestones for that user on the account
- Pinned memories they authored
- Voice transcripts where they were the speaker
- Sleep-buffer rows they authored
- Messages authored by that user ID, and those messages' media artifacts (message rows are per
  channel and shared by every bot on the install, so they go regardless of `account_name`)
- Pending follow-up tasks that name them (social check-ins, reminders)

The response carries a count per table.

Not covered by this transaction: **voice session summaries** (`voice_summaries`, which quote speaker
names) and **trace rows**, which can carry an excerpt of what was said. Both age out through the
Retention purge (`transcript_days` / `trace_days`) — turn Retention on if you need them gone on a clock.

The Settings › Discord › Memory "forget user" action invokes the same profile and memory paths (there is no `/forget-me` slash command — it was never registered).

## Safety Controls

### Settings-based

| Setting | Effect |
|---------|--------|
| `voice.enabled` = off | Leaves every voice channel on the next tick and blocks all voice output |
| `safety.rate_limit_seconds` | Per-channel reply cooldown after approval |
| `safety.proactive_cooldown_hours` | Minimum gap between proactive actions per channel/action |
| `safety.quiet_hours_enabled` + start/end | Idle presence, skip proactive outreach (mentions still allowed) |
| `safety.allow_direct_messages` | **Off by default** — anyone sharing a server can DM a bot, so the DM line stays shut until you open it |
| `safety.dm_daily_budget` | With DMs on: replies per person per day before she goes quiet on them until tomorrow (default 30, `0` = unlimited) |
| `safety.tools_stay_in_server` | **On by default** — inside a server conversation her Discord tools can only read/post in *that* server, never another server or someone's DMs. Your own chats are unrestricted. |
| `cognitive.side_lanes_local_only` | **On by default** — greetings, goodnights, distill, and image captions on `auto` pick only providers marked local, so server chatter never rides to a cloud model by accident |
| `cognitive.llm_debug_enabled` | Off by default. On = last 10 full prompts/replies held in memory for the Debug panel |
| `channel.reply_mode` | Hard gate: `mentions_only`, `default`, `all`, `disabled` |
| `channel.human_response_chance` / `channel.bot_response_chance` | Organic (unaddressed) reply % in `default` mode; mentions/DMs bypass |
| `bot.reply_mode` + allowlist | Control bot-to-bot debate sessions |

### Policy engine (automatic)

Policy blocks are recorded as `policy_rejected` traces:

| Reason | Trigger |
|--------|---------|
| `cooldown` | `safety.rate_limit_seconds` not elapsed for that channel |
| `missing_author` | Observation arrived with no author id |
| `proactive_cooldown` | Same proactive action in the same channel inside `safety.proactive_cooldown_hours` (scheduled task follow-ups bypass this) |
| `voice_disabled` / `speaking_disabled` / `mode_*` | Voice off, speaking off, or the voice mode doesn't speak |

Sleep schedule gates reply delivery and suppresses silent reactions during overnight hours.

## Slash Commands

Registered Discord slash commands:

| Command | Behaviour |
|---------|-----------|
| `/voice join [channel]` | Bring her into a voice channel (defaults to yours) |
| `/voice leave` | Disconnect her from voice in this server |

`/voice join` has **no permission or allowlist gate** — any member who can use slash commands in a
server the bot is in can pull her into a voice channel, which starts an STT/LLM/TTS session on your
budget. Until that is gated, keep `voice.enabled` off on public servers, or restrict the command in
Discord's own **Server Settings → Integrations → your bot**.

(`/ask`, `/summarize`, `/remember`, `/forget-me` were documented but never registered — the
handler behind them was removed 2026-09-13. Text conversation needs no command: mention her or
talk in a channel she replies in; she leaves voice on her own with `<<HANG UP>>`.)

## Recovery Procedures

### Daemon offline after plugin enable

1. Reload plugin under Settings → Plugins
2. Check Sapphire logs for startup exception
3. Verify pip dependencies installed (py-cord from the pinned git commit, davey, PyNaCl, dateparser,
   vaderSentiment). `GET /api/plugins/discord/check-deps` lists what is missing plus the exact pip
   command; **Git must be on PATH** and `discord.py` must not be installed alongside py-cord
4. Check for a shadowing copy: `[PLUGINS] 'discord' (user band) shadows the system copy` means an old
   `user/plugins/discord` is running instead of the shipped one — delete that folder and restart
5. `GET /health` — if `error`, read `detail`

### Bot connected but no message events

1. Confirm Message Content Intent enabled in Discord Developer Portal
2. Confirm a Schedule daemon task exists with correct **Bot Account** and matching filters
3. `GET /traces` — look for `event_dropped` vs `event_emitted`
4. Try filter `{}` temporarily to confirm events arrive

### Daemon crash loop

1. Check logs for traceback in `_run_loop`
2. Common causes: invalid DB path permissions, corrupted SQLite, py-cord connection failure
3. Stop plugin, backup SQLite, reload
4. As last resort: move DB aside and let plugin recreate schema (loses history)

### Stuck voice session

1. `GET /voice/sessions` — identify session
2. Turn `voice.enabled` off temporarily
3. Reload plugin (triggers graceful voice disconnect)
4. Check `voice/diagnostics` after restart

### Proactive spam / unwanted outreach

1. Disable `proactive.outreach_enabled` or increase `outreach_cooldown_hours`
2. Reduce `greeting_targets` to fewer channels
3. Increase `safety.proactive_cooldown_hours`
4. Check traces for repeated `proactive_sent`

## Cognitive Configuration

The `cognitive` overlay is split across the **Models**, **Tasks**, and **Cognition** settings tabs:

| Setting | Default | Tab | Purpose |
|---------|---------|-----|---------|
| `llm_primary` / `llm_model` | `auto` / `''` | Models | Discord-specific LLM override (`auto` = whatever the daemon task uses) |
| `task_follow_up_enabled` | `true` | Tasks | Deliver scheduled world-model tasks |
| `commitment_followups_enabled` | `true` | Tasks | Parse and follow up on future promises |
| `reminder_followups_enabled` | `true` | Tasks | Handle "remind me in …" requests |
| `situation_enabled` | `true` | Cognition | Short-lived channel vibe snapshot (quiet / calm / lively / playful / heated) |
| `situation_in_prompt` | `true` | Cognition | Add that snapshot to reply instructions as a data fence |
| `intention_competition_enabled` | `false` | Cognition | Score reply vs react-only vs silence instead of the legacy organic % roll |
| `side_lanes_local_only` | `true` | Cognition | Side lanes on `auto` pick local providers only |
| `llm_debug_enabled` | `false` | Cognition | Keep the last 10 prompts/replies in memory for the Debug panel |

Task follow-ups and reminders require a Sapphire **discord_message** daemon task with **Auto-reply** enabled on the same bot account.

## Related Documentation

- [README.md](README.md) — setup, features, tools
- [docs/discord_voice_conversation_operator.md](docs/discord_voice_conversation_operator.md) — voice operator guide
- [docs/discord_voice_conversation_roadmap.md](docs/discord_voice_conversation_roadmap.md) — voice architecture roadmap
