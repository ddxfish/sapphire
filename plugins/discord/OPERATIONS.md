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
4. Connect Discord transport and stored bot accounts
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
- **Voice auto-join** — joins/leaves configured voice channels based on occupancy (skipped during sleep)

Sapphire continuity cron jobs (hourly/15-min) also trigger proactive pathways — see [Scheduled jobs](#scheduled-jobs).

## Storage

### Database location

Default SQLite path:

```
user/plugin_state/discord/discord.sqlite3
```

Legacy installs may use:

```
user/plugin_state/discord_cognitive/discord.sqlite3
```

Override via plugin settings key `database_path` if needed.

### What is stored

| Data | Table / area | Default retention |
|------|--------------|-------------------|
| Message history | `messages` | 90 days |
| Debug traces | `traces` | 14 days |
| Voice transcripts | `voice_transcripts` | 30 days |
| Processed profile buffers | `profile_buffers` | 7 days |
| User profiles & affect | `profiles` | Until forget-user |
| Pinned memories | `memories` | Until forget-user |
| World-model tasks | `tasks` | Until completed/purged |
| Proactive sleep state | `proactive_state` | Per channel |
| Bot tokens | `accounts` (encrypted at rest via Sapphire storage) | Until account deleted |
| Import audit | `import_audit` | Permanent (idempotency keys) |

Settings overlays (global, guild, channel, DM) are stored in the channel repository and loaded at runtime start.

## Observability

### Traces

`GET /traces?limit=50`

Returns recent structured traces plus a summary count by type and current cognitive snapshot (affect, activation, pending tasks, voice sessions).

Traces explain **why** the agent acted, skipped, or was blocked. They intentionally exclude full prompt dumps.

#### Primary trace categories

| `trace_type` | When recorded |
|--------------|---------------|
| `intention_generated` | Cognitive layer produced a reply/proactive intention |
| `policy_rejected` | Safety/cooldown/sleep gate blocked an action |
| `memory_injected` | Profile/pinned memory added to prompt context |
| `affect_modulated` | Relationship/mood scores adjusted activation thresholds |
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
- Account affect state
- Pending world-model tasks (up to 10)
- Active voice sessions
- Connected accounts

The settings UI **Operator debug** panel shows a subset of this data inline.

### Logs

Search Sapphire daemon logs for the prefix `[discord_cognitive]`.

Useful log lines:

- `Daemon started (health=…)`
- `Voice stack: pycord=… davey=…`
- `Failed to connect stored account …`
- `Scheduler tick failed` / `Voice auto-join tick failed`
- `Discord cognitive daemon crashed`

### Profiles & affect

`GET /profiles?account=<name>`

Returns user profiles, account-level affect (energy, sociability, irritability, fondness), and top activation entities for debugging cognitive gating.

## API Reference

### Accounts

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/accounts` | List configured bots (token redacted) |
| `POST` | `/accounts` | Add bot — body: `{ "account_name", "token" }` |
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
| `GET` | `/traces?limit=N` | Recent traces + cognitive snapshot |

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
| `POST` | `/admin/import-leona` | Import from leona_discord database |

## Scheduled Jobs

Two schedulers cooperate:

### Sapphire continuity cron (plugin.json)

| Job | Cron | Handler |
|-----|------|---------|
| `morning_greeting` | `0 * * * *` | Hourly check; fires when server-local hour matches `proactive.greeting_utc_hour` |
| `quiet_outreach` | `*/15 * * * *` | Conversation starters when channels go stale |
| `sleep_goodnight` | `*/15 * * * *` | Goodnight + sleep state at `proactive.sleep_utc_hour` (minutes 0/15/30/45) |

These require the plugin daemon to be running and at least one connected bot account.

### Internal 15s tick

Handles proactive evaluation between cron runs, presence rotation, task follow-up timing, and voice auto-join polling.

## Proactive Diagnostics Runbook

When greetings, outreach, or goodnight do not fire:

1. `GET /proactive/diagnostics` — read `hints` under `greeting`, `outreach`, `goodnight`
2. Confirm daemon running: `GET /health`
3. Confirm greeting channels selected in settings (`greeting_targets`)
4. Confirm connected accounts match target account prefixes (`account:channel_id` format)
5. Check server-local hour vs configured greeting/sleep hours
6. Check sleep state per channel in diagnostics `channels[]` — asleep channels buffer mentions
7. Use `POST /proactive/test` with `dry_run: true` to preview message text
8. Check traces for `proactive_skipped` with reasons: `proactive_cooldown`, `low_energy`, `high_irritability`

Common skip reasons:

| Hint | Fix |
|------|-----|
| `Morning greetings are disabled` | Enable `proactive.greeting_enabled` |
| `Current server hour is X; greetings only fire at hour Y` | Wait for correct hour or adjust `greeting_utc_hour` |
| `No greeting channels selected` | Pick targets in Proactive settings |
| `No connected Discord bot accounts` | Fix token / enable plugin |
| `Currently in sleep hours` | Expected — outreach suppressed overnight |
| `Goodnight only fires at minutes (0, 15, 30, 45)` | Wait for next 15-min boundary |

## Voice Operations Runbook

1. `GET /voice/diagnostics` — confirm `voice_stack.davey` and `voice_sinks` present
2. `GET /voice/auto-join` — confirm targets and join state
3. `GET /voice/sessions` — active sessions and `conversation_active` flag
4. Settings: `voice.enabled`, `voice.speaking_enabled`, `voice.mode`
5. Sapphire: TTS streaming must be enabled for conversational mode
6. Emergency stop: set `voice.emergency_disabled` — blocks all voice output immediately

| Symptom | Check |
|---------|-------|
| Bot joins but never speaks | `speaking_enabled`, `addressing_mode=bot_name` (must say bot name), TTS streaming |
| Garbled transcripts | DAVE not ready — reinstall `davey`, check py-cord patches in diagnostics |
| Auto-join not working | Targets configured, not in sleep mode, daemon running |
| `conversation_slot_cap` in logs | Lower concurrent VCs or raise `max_conversation_sessions` |

## Privacy & Retention

Configure under **Retention** tab or settings overlay key `retention`:

| Setting | Default | Effect |
|---------|---------|--------|
| `enabled` | `true` | Master switch for purge |
| `message_days` | 90 | Delete old `messages` rows |
| `trace_days` | 14 | Delete old `traces` rows |
| `transcript_days` | 30 | Delete old `voice_transcripts` rows |
| `profile_buffer_days` | 7 | Delete processed `profile_buffers` rows |

### Purge

```http
POST /admin/purge
```

Runs retention cleanup immediately. Returns `{ status, results: { messages, traces, voice_transcripts, profile_buffers } }`.

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

Removes:

- Profile data for that user on the account
- Pinned memories
- Messages authored by that user ID

The `/forget-me` slash command invokes the same profile and memory paths for the calling user.

## Import from leona_discord

Optional migration — does **not** modify `plugins/leona_discord/`. The `discord` and `leona_discord` plugins use independent storage; import is the supported bridge.

```http
POST /admin/import-leona
Content-Type: application/json

{
  "leona_db_path": "/path/to/leona.sqlite3",
  "include": ["pinned_memories", "profile_facts", "profile_summaries", "settings"],
  "leona_settings": { "global": { ... } }
}
```

- Imports are **idempotent** via the `import_audit` table — re-running skips already-imported records
- Settings mapping translates Leona keys to Discord plugin overlay keys (greeting, sleep, GIF, presence, etc.)
- Pass `leona_settings` when importing settings without reading from the Leona DB file

## Safety Controls

### Settings-based

| Setting | Effect |
|---------|--------|
| `voice.emergency_disabled` | Immediately blocks all voice output |
| `safety.rate_limit_seconds` | Per-channel reply cooldown after approval |
| `safety.proactive_cooldown_hours` | Minimum gap between proactive actions per channel/action |
| `safety.quiet_hours_enabled` + start/end | Idle presence, skip proactive outreach (mentions still allowed) |
| `safety.allow_direct_messages` | Drop DM observations when false |
| `channel.reply_mode` | Hard gate: `mentions_only`, `default`, `disabled` |
| `bot.reply_mode` + allowlist | Control bot-to-bot debate sessions |

### Policy engine (automatic)

Policy blocks are recorded as `policy_rejected` traces:

| Reason | Trigger |
|--------|---------|
| `cooldown` | Reply rate limit not elapsed |
| `proactive_cooldown` | Proactive action too soon |
| `high_irritability` | Affect irritability > 0.85 blocks proactive |
| `low_energy` | Affect energy < 0.15 blocks outreach/greeting |
| `low_fondness` | Blocks meme/media sends |
| `voice_disabled` / `speaking_disabled` / `mode_*` | Voice policy blocks |

Sleep schedule gates reply delivery and suppresses silent reactions during overnight hours.

## Slash Commands

Registered Discord slash commands (when configured on the bot):

| Command | Behaviour |
|---------|-----------|
| `/ask` | Queue a question through the conversation pipeline |
| `/summarize` | Queue a channel summary request |
| `/remember <text>` | Pin a fact to caller's profile + pinned memory |
| `/forget-me` | Remove caller's profile and pinned memories |

Commands require the conversation service and appropriate storage to be available.

## Recovery Procedures

### Daemon offline after plugin enable

1. Reload plugin under Settings → Plugins
2. Check Sapphire logs for startup exception
3. Verify pip dependencies installed (py-cord, davey, PyNaCl)
4. `GET /health` — if `error`, read `detail`

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
2. Set `voice.emergency_disabled` temporarily
3. Reload plugin (triggers graceful voice disconnect)
4. Check `voice/diagnostics` after restart

### Proactive spam / unwanted outreach

1. Disable `proactive.outreach_enabled` or increase `outreach_cooldown_hours`
2. Reduce `greeting_targets` to fewer channels
3. Increase `safety.proactive_cooldown_hours`
4. Check traces for repeated `proactive_sent`

## Cognitive Configuration

Configure under **Cognitive** settings tab (`cognitive` overlay):

| Setting | Default | Purpose |
|---------|---------|---------|
| `enabled` | `true` | Route messages through intention engine before LLM |
| `mode` | `integrated` | `conservative` / `integrated` / `expressive` |
| `llm_primary` / `llm_model` | `auto` | Discord-specific LLM override |
| `task_follow_up_enabled` | `true` | Deliver scheduled world-model tasks |
| `commitment_followups_enabled` | `true` | Parse and follow up on future promises |
| `reminder_followups_enabled` | `true` | Handle "remind me in …" requests |
| `affect_modulation_enabled` | `true` | Mood/relationship adjust reply thresholds |

Task follow-ups and reminders require a Sapphire **discord_message** daemon task with **Auto-reply** enabled on the same bot account.

## Related Documentation

- [README.md](README.md) — setup, features, tools
- [docs/discord_voice_conversation_operator.md](docs/discord_voice_conversation_operator.md) — voice operator guide
- [docs/discord_voice_conversation_roadmap.md](docs/discord_voice_conversation_roadmap.md) — voice architecture roadmap
