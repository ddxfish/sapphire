# Conversation

When and how she replies in channels: address gates, organic chance, batching, typing pacing, and bot-to-bot debate rules.

## Settings

### Reply mode

- **Setting key:** `channel.reply_mode`
- **Type:** `string`
- **Default:** `default`

First gate before the LLM. Mentions only is the usual choice for public servers. Default also rolls Human/Bot organic reply chance on unaddressed channel messages.

**Options:**

- `default` — Default — reply when addressed, plus organic chance
- `mentions_only` — Mentions only — require @mention or name match
- `all` — Reply to all — answer every allowed message
- `disabled` — Disabled — never auto-reply in channels

### Ignored channels

- **Setting key:** `channel.ignored_channels`
- **Type:** `list` (`account:channel_id` entries)
- **Default:** empty

Conversation-tab picker (same channel list as Greeting Channels). Checked channels are fully ignored: no replies, reactions, or proactive posts into them. Inbound messages are dropped before batching.

### Human organic reply chance (%)

- **Setting key:** `channel.human_response_chance`
- **Type:** `number`
- **Default:** `15.0`

In Default reply mode only: percent chance (0-100) to reply to a human message that did not address her (@mention / name match). Mentions and DMs always reply. 0 = never chime in unprompted.

### Bot organic reply chance (%)

- **Setting key:** `channel.bot_response_chance`
- **Type:** `number`
- **Default:** `15.0`

In Default reply mode only: percent chance (0-100) to reply to an eligible bot message that did not address her. Still requires Bot-to-bot replies and allowlist/mode gates. Mentions bypass this roll. 0 = never.

### Respond to bot name (soft mention)

- **Setting key:** `channel.name_match_enabled`
- **Type:** `boolean`
- **Default:** OFF

Reply when a message contains the bot's name anywhere in the text, no @mention needed. OFF (default): only real @mentions count.

### Case-sensitive name match

- **Setting key:** `channel.name_match_case_sensitive`
- **Type:** `boolean`
- **Default:** OFF

ON: the name must match capitalization exactly. OFF (default): any capitalization counts. Only applies when name match is on.

### Batch window (seconds)

- **Setting key:** `channel.batching_seconds`
- **Type:** `number`
- **Default:** `8`

Collect rapid messages into one reply. Typing extends; questions halve.

### Strip thinking tags

- **Setting key:** `channel.strip_think_tags`
- **Type:** `boolean`
- **Default:** ON

ON (default): removes <think> reasoning blocks from replies before posting. OFF: raw thinking text posts to the channel.

### Typing indicator

- **Setting key:** `channel.typing_indicator_enabled`
- **Type:** `boolean`
- **Default:** ON

ON (default): shows typing… before each message, paced like a human typing the reply (roughly 0.5-12 seconds by length). OFF: posts instantly with no indicator.

### Human pause

- **Setting key:** `channel.human_pause_enabled`
- **Type:** `boolean`
- **Default:** ON

Brief pause before sending, scaled to reply length.

### Read delay

- **Setting key:** `channel.read_delay_enabled`
- **Type:** `boolean`
- **Default:** ON

Wait as if reading the message before typing starts.

### Bot-to-bot replies

- **Setting key:** `bot.enabled`
- **Type:** `boolean`
- **Default:** ON

ON (default): messages from other bots can get replies, governed by Bot reply mode. OFF: bot messages are always ignored. Humans are unaffected.

### Bot reply mode

- **Setting key:** `bot.reply_mode`
- **Type:** `string`
- **Default:** `allowlist`

Which bots can get a reply — a bot must always address her (@mention, name, or replying to her last message). Never: ignore all bots. Allowlist (default): only bots checked in Allowlisted Bots below. Mentions only: any bot that addresses her.

**Options:**

- `never` — Never reply to bots
- `allowlist` — Allowlist only
- `mentions_only` — Only when a bot @mentions

### Human debate window (seconds)

- **Setting key:** `bot.session_human_window_seconds`
- **Type:** `number`
- **Default:** `300`

After a human addresses her, bots may volley reply-chains freely for this many seconds (a debate window). Minimum 60. Direct @mentions/name calls are always allowed regardless.

### Session silence timeout (seconds)

- **Setting key:** `bot.session_silence_seconds`
- **Type:** `number`
- **Default:** `150`

A bot debate goes quiet after this many seconds without a volley — reply-chains stop until a human re-opens the window. Minimum 30.

### Safety cap (exchanges)

- **Setting key:** `bot.session_safety_max_exchanges`
- **Type:** `number`
- **Default:** `20`

Hard stop per debate: after this many replies to bots in a channel, further bot replies are blocked until a human re-engages her (which starts a fresh debate).

## Allowlisted Bots

Custom picker on this tab:

- Lists other bots visible in servers your bot is in (needs **Server Members Intent**).
- Checked bots are stored in `bot.allowlist_ids`.
- Used when **Bot reply mode** is `allowlist`.

Refresh after inviting new bots or enabling the members intent.
