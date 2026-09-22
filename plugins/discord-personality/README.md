# Discord Personality

Personality for Sapphire's Discord bot, rebuilt small on the Discord plugin's
hooks. Zeebie the Zebra wrote these features first, inside the Discord plugin
itself (1.x); this plugin carries them forward as five independent modules.

Requires the `discord` plugin (manifest `requires_plugins`). Off = nothing
registers; on, each module is its own toggle at the top of the Personality
settings page.

| module | what | hooks |
|---|---|---|
| People memory | remember / recall / forget facts per user, asker-bound in channel; the speaker's facts ride into her reply prompt | `discord_prompt_context`, tool `discord_people` |
| Birthdays | an LLM-written wish on the day (needs People) | own `discord_birthday` daemon task |
| Reminders | "remind me in 2h" → @mention post, no LLM | `discord_tick`, tool `discord_remind` |
| Typos & edits | a typo, fixed by edit; a quote-reply chance | `discord_reply_planned`, `discord_reply_sent` |
| Presence cycling | status list, cycle minutes, night status | `discord_tick` |

## Writing a module

One file under `discord_personality/modules/`, plain functions named after the
host hooks you want. Every handler gets core's `HookEvent`; `event.metadata`
carries the host payload and `metadata['api']`, the Discord facade
(`send_message`, `react`, `send_image`, `set_presence`, `recent_messages`,
`channel_info`, `join_voice`, `leave_voice`). Never import the Discord plugin.
Add `<module>.enabled` to the manifest settings and the module to `MODULES`.

Host hooks (fired by `plugins/discord/hooks_out.py`):

| hook | when | you may change |
|---|---|---|
| `discord_message_observed` | every inbound message | nothing |
| `discord_prompt_context` | building a reply's prompt | append to `metadata['context_parts']` |
| `discord_reply_planned` | reply chunked, not yet sent | `chunks`, `quote_reply`, `reaction`, `delay_s` |
| `discord_reply_sent` | after send | nothing |
| `discord_voice_utterance` | post-STT text in a voice channel | nothing |
| `discord_tick` | per connected account, ~15 s | nothing |
