# Discord — Operations Guide

How the host runs, what it stores, how to read it, and how to recover it. Feature setup is in
[README.md](README.md); the 2.0 upgrade story is in [CHANGELOG.md](CHANGELOG.md).

## Runtime

### Daemon lifecycle

Core loads `daemon.py` and calls `start(plugin_loader, settings)` when the plugin is enabled and
`stop()` when it is disabled or unloaded. `start` runs the runtime on its own thread with its own
asyncio loop (`discord-daemon`), waits up to 10 s for it to come up, then registers the reply
handler that delivers task replies to Discord. `on_settings_saved` nudges the one construct-time
scalar (the batch window); everything else reads core's settings live.

### The container (`runtime/container.py`)

One object builds every service in `_build()`, in dependency order: settings store → repositories
→ Sapphire bridges (events, task selection, speech) → the voice gate → mention map → transport →
the reply pipeline (event adapter, batching, conversation service, message pipeline) → the greetings
clock → the voice stack (transport, sessions, playback, conversation runner, listener, voice service,
auto-join). The two voice doors are wired here: every leave reaches the auto-join latch, and
`<<HANG UP>>` leaves through the same door as `/voice leave`.

**Start order:** open storage (schema v2, v1 adoption) → apply the py-cord voice patches and log the
stack → start the message pipeline → start the tick → mark ready → connect the stored accounts an
enabled task selects (5 s stagger between bots).

**Stop order:** message pipeline → tick → conversation runner → voice connections → voice worker
pool → gateway clients → storage.

### The tick (default 15 s)

1. Reconcile accounts: bots no enabled task selects are disconnected; selected bots not online are
   connected (5-minute backoff after a failed login — Discord rate-bans repeated failures).
2. The greetings clock (worker thread) fires due greeting / goodnight events into their tasks.
3. Per connected account: the voice auto-join pass, then the `discord_tick` hook (worker thread).
4. Once a minute: reap idle voice chats.

A failing step is logged; the tick continues.

## Storage

**Location:** `user/plugin_state/discord/discord.sqlite3` (WAL mode, one locked connection shared by
the daemon loop, the voice pool, tool threads and API threads). A pre-rename database under
`discord_cognitive/` is moved into place once.

**Schema v2 tables:** `accounts` (bot tokens), `guilds` / `channels` / `users` (name caches for the
pickers, the mention map and the transcript's author labels — refilled by traffic), `messages` (recent
channel history for the reply prompt), `schema_version`.

**v1 adoption (2.0.0):** a v1 file (the stacked-migrations schema, recognised by its `observations`
table) is moved aside to `discord.sqlite3.pre-2.0` (with its `-wal` / `-shm`), a fresh v2 file is
created, and the `accounts` rows are copied. If the copy does not land, the move is undone and the
daemon refuses to boot rather than coming up with no bots. The log line
`[DISCORD] database upgraded to schema v2: N account(s) copied` confirms it.

**Rollback to 1.x:** stop Sapphire, delete `discord.sqlite3` (+ `-wal`, `-shm`), rename
`discord.sqlite3.pre-2.0` back to `discord.sqlite3`, reinstall the 1.x plugin.

**Voice and disk:** nothing about a voice session touches the plugin database. Audio for STT lives
in temp files deleted right after transcription; the voice chat's history is core's (the rule's
`keep_chat_history` decides, and idle voice chats are reaped).

## Observability

- **Logs:** everything is under the `[DISCORD]` prefix; boot prints the voice stack line and
  `Daemon started (health=ready)`.
- **`GET /api/plugin/discord/health`:** state (`starting` / `ready` / `error` / `stopped`), detail,
  connected accounts.
- **LLM debug ring** (Settings → Discord → Debug, opt-in, in memory): the last 10 exchanges with
  the task's model and the prompt breakdown, plus every rejection with its stage (`safety`,
  `trigger`, `bot_gate`, `routing`, `policy`, `daemon`) and reason. This is the "why did she stay
  quiet" surface. Off = nothing retained.
- **`GET voice/status`:** live sessions with their chat names, voice connections, the auto-join view
  (every covered channel with humans / latched / status), the runner's active chats, the voice stack.

## API reference

All under `/api/plugin/discord/`, authenticated like every plugin route.

| Method | Path | Handler | Notes |
|---|---|---|---|
| GET | `accounts` | list accounts | tokens never returned |
| POST | `accounts` | add `{account_name, token}` | connects when a task selects it |
| POST | `accounts/test` | validate a raw `{token}` | before saving |
| DELETE | `accounts/{name}` | remove the account | disconnects first |
| POST | `accounts/{name}/test` | validate the saved token | retries a failed connection |
| GET | `settings` | daemon state + voice prompt default | values live in core |
| GET | `health` | runtime health | |
| GET | `channels/text` | text channels the bots see | picker feed |
| GET | `voice/targets` | voice channels the bots see | picker feed |
| GET | `voice/status` | sessions, connections, auto-join, stack | `?account=` optional |
| GET | `bots/allowlist` | other bots in shared servers | needs Server Members Intent |
| POST | `admin/forget-user` | `{user_id}` → delete their messages | privacy door |
| GET | `debug/llm` | the debug ring (`?limit=`) | |
| POST | `debug/clear` | empty the ring | |

## Scheduled job

`retention_purge` (cron `30 4 * * *`): when Retention is ON, deletes stored messages older than
**Keep messages (days)** in chunks that yield between rounds, then folds the WAL back into the file.
Off by default. Bot accounts are never touched.

## Voice runbook

1. **The rule.** Settings → Continuity → Realtime → **Discord: Voice channel**: account, filter
   (blank = every voice channel the bot sees), Auto-join (off by default), keep chat history, and
   the persona / provider / model / toolset that run the voice chat. No enabled rule = voice off.
2. **Join.** `/voice join` (in a voice channel, or name one) or her `discord_join_voice` tool. A
   channel the rule does not cover answers *"Voice is off for this channel"*.
3. **Talk.** Addressing mode `bot_name`: say her name (or an alias); one human alone needs no name;
   the person she just answered may keep talking nameless for the follow-up window. `always`
   answers everyone.
4. **Leave.** `<<HANG UP>>` in her reply (she leaves once her last words drain), `/voice leave`, the
   leave tool, or the channel empties. A deliberate leave latches the channel: auto-join waits
   until it has emptied once.
5. **Auto-join ON:** she joins a covered channel when a human is in it and leaves when it empties.
6. **Diagnose:** `voice/status`; the boot line `[DISCORD] Voice stack: pycord=… davey=True …`; a
   `patches NOT applied` warning means py-cord moved something and receive will be silent.

## Privacy & retention

- Stored: bot tokens; guild / channel / user names; message text per channel (for the reply prompt).
  Nothing else — no profiles, no traces, no transcripts (2.0.0).
- Retention (off by default) prunes messages by age; `POST admin/forget-user` removes one person's
  rows now.
- DMs are off by default (Safety); when on, a per-person daily budget applies.
- The LLM debug ring holds full prompts (other people's messages) only while it is on, in memory.

## Slash commands

Registered per connected bot: `/voice join [channel]`, `/voice leave`.

## Recovery

| Symptom | Do |
|---|---|
| **Daemon offline after enabling** | the log around `Discord daemon crashed` — missing packages (install strip), a bad token, or the database (below) |
| **`database upgrade to schema v2 failed`** | the v1 file was restored; the log names the cause; fix it (usually file permissions or a corrupt v1 file — run `sqlite3 discord.sqlite3 "PRAGMA integrity_check"`) and restart |
| **Bot connected, no message events** | Message Content Intent; the task's filter; Debug → LLM debug shows the rejection stage |
| **Crash loop on login** | Discord's daily identify cap — leave it 24 h; the 5-minute backoff protects the token |
| **Stuck voice session** | `/voice leave`; if it does not answer, disable and re-enable the plugin (stop disconnects every voice connection) |
| **Wrong bots online** | only bots selected by an enabled Continuity task log in; the tick reconciles within 15 s of a task change |
