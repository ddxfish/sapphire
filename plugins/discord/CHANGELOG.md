# Changelog

## 2.0.0 — 2026-09-22

The consolidation release. Between 1.25 and 1.33 the host shed every feature that was not the
bot itself (the S1–S6 strip); 2.0.0 rewrites the wiring that was left behind, moves the plugin
database to a fresh schema, and trims the manifest, routes, settings, web tab, tests and docs to
what remains.

### What left the host, and where it went

| Feature | Now |
|---|---|
| Greetings, goodnights, proactive schedule | the **Discord: Greetings** / **All interactions** daemon tasks (times in Source Settings) |
| Birthdays, presence cycling | `discord-personality` |
| People memory (profiles, facts, pinned memories) | `discord-personality` (`discord_people`) |
| Reminders ("remind me in 2h") | `discord-personality` (`discord_remind`) |
| Typos, post-send edits, quote chance | `discord-personality` |
| Ambient distill, lore, milestones, interests, quiet outreach, sleep schedule, bot-to-bot debates, channel situation, intention scoring, relationship policy | gone |
| Reply LLM / Vision LLM pickers, the Models tab, side lanes | gone — the task's provider is the brain; images ride the payload |
| Per-guild / channel / DM settings overlays | gone — daemon tasks + filters |
| The voice fallback lane, transcript + summary tables | gone — one lane on Sapphire's conversation engine, gated by the **Discord: Voice channel** Realtime rule |
| Decision traces (table, panel, route) and the LLM debug ring | gone — the Debug tab shows recent decisions, ids only |

### 2.0.0 itself

- **No database.** Bot accounts (label → token) live in core's credentials manager beside every
  other plugin's secrets, scrambled at rest; the plugin's SQLite file and its 13 stacked migrations are
  gone. **The plugin no longer keeps any Discord message.** The reply prompt's channel window is fetched
  live from Discord when she answers (Conversation → *Give the AI the last X channel messages*, default
  20); retention settings, the purge cron and forget-user went with the store. The LLM debug ring
  (full prompts in memory) is gone too; the Debug tab lists recent decisions, ids only. On first boot the
  accounts are imported out of the old file into the credentials manager and the file is renamed
  `discord.sqlite3.imported`; a file that cannot be read refuses the boot rather than lose a bot.
- **Wiring.** Container, lifecycle, daemon state, scheduler loop and health collapse into
  `runtime/container.py` + `daemon.py`. The conversation service is rewritten with one rejection
  path. The `<<HANG UP>>` door is wired at build (it used to arm on the first tick, so a hang-up in
  the first 15 s after a restart did nothing).
- **Settings** (45 → 33): removed every `retention.*` key, `safety.proactive_cooldown_hours`,
  `safety.quiet_hours_*`, `debug.llm_debug_enabled` (the ring is gone), `channel.strip_think_tags`
  (reasoning blocks are always stripped), the three pacing booleans (typing indicator, human pause,
  read delay → one `channel.natural_delay`), `bot.enabled` (→ `bot.allow_all`: ON answers any bot,
  OFF the allowlist, empty list = no bot); added `channel.context_messages`.
- **Routes** (19 → 14): removed `admin/summary`, `admin/purge`, `traces`, `voice/sessions`,
  `voice/diagnostics`, `voice/auto-join`; added `voice/status` (sessions + connections + auto-join
  view + voice stack). `admin/forget-user` and the retention cron are gone with the message store.
- **Dependencies:** `dateparser` and `vaderSentiment` are no longer required.
- **Web tab:** the Decision traces panel and the operator JSON dump are gone; the two chip pickers
  share one widget; the Bot Accounts widget is back (it had been lost in an earlier strip).
- **Voice on disk:** nothing about a voice session is written to the plugin database any more.
- **Docs:** README and OPERATIONS rewritten; the old roadmap and per-tab docs are archived outside
  the plugin.
- **Post-audit cleanup (same day):** the inbound-message event-bus publish is gone (it carried every
  message body into core's replay ring with no subscriber); ~300 lines of dead code removed (unused
  sync/async twins, a gap-repair experiment, the debug ring's typo fields, intention-scoring vestiges);
  the streaming-playback forwarder folded into the voice transport; the `Social` tab is now
  `Reactions`; the silence knob's help text tells the truth;
  the STT quality gates run on every STT provider through core's new segment door instead of
  reaching into faster-whisper; the bot token check goes through core's egress door; a per-window
  diagnostic names any voice packet the transport refuses.

### Upgrading an existing install (the click-list)

1. Update, restart. The log shows `imported N bot account(s) … into the credentials manager`; the old
   database is renamed `discord.sqlite3.imported`. Delete it once your bots are online.
2. Settings → Plugins → Discord: your bots are listed; the daemon says running. Stale keys in the
   saved settings file are ignored.
3. Your Continuity daemon tasks keep working unchanged.
4. **Media → Images in** is a new switch (S5), off by default; the old "image understanding" switch no
   longer counts. Turn it on if she should see attachments — the task's own model must have vision.
5. Voice: create one **Discord: Voice channel** Realtime rule per bot (filter blank = every voice
   channel) or she never joins.
6. Greetings: put times on a **Greetings** / **All interactions** task; otherwise nothing posts.
7. Enable `discord-personality` for people memory, birthdays, reminders, typos and presence. People
   facts from 1.x do not migrate (other people's chatter — start clean).

### Rolling back to 1.x

Reinstall the 1.x plugin and add the bot tokens again on its accounts page.

## 1.25 – 1.33 — 2026-09-21 … 22

The strip, one step per version: S0 doors (hooks + facade), S1 proactive family, S2 memory family,
S3 cognition, S4 delivery, S5 LLM routing + vision, S6 overlays + voice lane, S6.1 voice filters +
opt-in auto-join + the deliberate-leave latch.
