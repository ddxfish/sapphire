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

**The plugin has no database.** Bot accounts (your label → the bot token, plus the bot name and id
learned at login) live in core's credentials manager beside every other plugin's secrets:
`~/.config/sapphire/credentials.json`, section `discord_accounts`, token scrambled at rest, file mode
0600, included in Sapphire's backups. Channel history for the reply prompt is fetched live from Discord
when she answers (Conversation → *Give the AI the last X channel messages*); names come from py-cord's
cache. The only thing the plugin writes under `user/` is the greetings latch in core's plugin-state JSON.

**Upgrading from an install that had the SQLite file (2.0.0 and every 1.x):** on the first boot the
daemon reads the accounts out of `user/plugin_state/discord/discord.sqlite3` (or the pre-rename
`discord_cognitive/` file), writes them into the credentials manager, and renames the file aside as
`discord.sqlite3.imported`. The log line `[DISCORD] imported N bot account(s) …` confirms it. A file
that exists but cannot be read refuses the boot — move it aside or repair it. Once the bots are online,
delete the `.imported` file and any `.pre-2.0` file; they hold old channel chatter and plaintext tokens.

**Rollback to 1.x:** reinstall the 1.x plugin and add the bot tokens again on its accounts page.

**Voice and disk:** nothing about a voice session touches the plugin database. Audio for STT lives
in temp files deleted right after transcription; the voice chat's history is core's (the rule's
`keep_chat_history` decides, and idle voice chats are reaped).

## Observability

- **Logs:** everything is under the `[DISCORD]` prefix; boot prints the voice stack line and
  `Daemon started (health=ready)`.
- **`GET /api/plugin/discord/health`:** state (`starting` / `ready` / `error` / `stopped`), detail,
  connected accounts.
- **Recent decisions** (Settings → Discord → Debug): the last 20 messages she read and what she did —
  `sent` with the chunk count, or `rejected` with the stage (`safety`, `trigger`, `bot_gate`,
  `routing`, `policy`, `daemon`) and reason; also `skipped` (a tool already answered), `empty`,
  `error`. Ids only, in memory — no message text, no prompt. What she saw and said is the task's chat.
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
| GET | `debug/decisions` | the last 20 decisions (`?limit=`) | ids only |
| POST | `debug/clear` | empty the list | |

## Scheduled jobs

None. The plugin keeps nothing that needs pruning.

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

## Privacy

- Stored on disk: bot tokens. Nothing else — no messages, no names, no profiles, no traces, no
  transcripts (2.0.0). The reply prompt's channel window is fetched from Discord at reply time and
  forwarded; the only Sapphire-side record of what she heard and said is the task's chat, which core
  governs like any chat.
- DMs are off by default (Safety); when on, a per-person daily budget applies.
- The Debug tab holds decision ids only; no message text is held anywhere in the plugin.

## Slash commands

Registered per connected bot: `/voice join [channel]`, `/voice leave`.

## Recovery

| Symptom | Do |
|---|---|
| **Daemon offline after enabling** | the log around `Discord daemon crashed` — missing packages (install strip), a bad token, or the database (below) |
| **`the old account database … cannot be read`** | the daemon refused to boot rather than lose a bot; move `user/plugin_state/discord/discord.sqlite3` aside (or repair it) and restart, then re-add the bot if it is missing |
| **Bot connected, no message events** | Message Content Intent; the task's filter; Debug → Recent decisions names the stage that refused it |
| **Crash loop on login** | Discord's daily identify cap — leave it 24 h; the 5-minute backoff protects the token |
| **Stuck voice session** | `/voice leave`; if it does not answer, disable and re-enable the plugin (stop disconnects every voice connection) |
| **Wrong bots online** | only bots selected by an enabled Continuity task log in; the tick reconciles within 15 s of a task change |
