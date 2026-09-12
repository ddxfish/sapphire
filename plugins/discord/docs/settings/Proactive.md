# Proactive

Unprompted posts: morning greetings, quiet outreach, sleep/goodnight, and birthday wish timing.

## Settings

### Morning greetings

- **Setting key:** `proactive.greeting_enabled`
- **Type:** `boolean`
- **Default:** OFF

ON: posts a morning greeting to each Greeting Channel at the greeting hour, written through the normal persona pipeline. OFF (default): no morning posts.

### Greeting hour (server local)

- **Setting key:** `proactive.greeting_utc_hour`
- **Type:** `number`
- **Default:** `9`

Hour (0-23, server local time) when the morning greeting posts. Also the sleep-schedule wake hour and the start of the birthday-wish window.

### AI-generated greeting

- **Setting key:** `proactive.greeting_use_llm`
- **Type:** `boolean`
- **Default:** ON

ON (default): the AI writes each greeting, guided by the Greeting instructions. OFF: posts the Greeting instructions text verbatim (or the fallback if blank).

### Greeting instructions

- **Setting key:** `proactive.greeting_message`
- **Type:** `textarea`
- **Default:** *(empty)*

With AI-generated greeting ON: instructions guiding the AI's greeting (blank = built-in default). With it OFF: this exact text is posted instead.

### Greeting fallback

- **Setting key:** `proactive.greeting_fallback`
- **Type:** `string`
- **Default:** `Good morning!`

Posted as-is when the AI can't generate a greeting, or when AI-generated greeting is OFF and no instructions are set.

### Quiet outreach

- **Setting key:** `proactive.outreach_enabled`
- **Type:** `boolean`
- **Default:** OFF

Start a conversation when configured channels go quiet.

### Outreach after quiet (minutes)

- **Setting key:** `proactive.outreach_stale_minutes`
- **Type:** `number`
- **Default:** `120`

Minutes of channel silence before she may start a conversation in a Greeting Channel. Checked every 15 minutes; skipped during sleep and just before the greeting hour.

### Sleep schedule

- **Setting key:** `proactive.sleep_schedule_enabled`
- **Type:** `boolean`
- **Default:** OFF

ON: posts a goodnight at the sleep hour, then sleeps until the greeting hour — presence shows sleeping, non-mention chat is ignored, and mentions are buffered (enough mentions force a temporary wake). OFF (default): always awake.

### Sleep hour (server local)

- **Setting key:** `proactive.sleep_utc_hour`
- **Type:** `number`
- **Default:** `22`

Hour (0-23, server local time) when sleep starts; runs until the greeting hour (wraps past midnight). Set equal to the greeting hour to disable the window.

### AI-generated goodnight

- **Setting key:** `proactive.goodnight_use_llm`
- **Type:** `boolean`
- **Default:** ON

ON (default): the AI writes the goodnight, guided by the Goodnight instructions. OFF: posts the Goodnight instructions text verbatim (or the fallback if blank).

### Goodnight instructions

- **Setting key:** `proactive.goodnight_message`
- **Type:** `textarea`
- **Default:** *(empty)*

With AI-generated goodnight ON: instructions guiding the AI's goodnight (blank = built-in default). With it OFF: this exact text is posted instead.

### Goodnight fallback

- **Setting key:** `proactive.goodnight_fallback`
- **Type:** `string`
- **Default:** `Goodnight everyone!`

Posted as-is when the AI can't generate a goodnight, or when AI-generated goodnight is OFF and no instructions are set.

### Forced-wake mentions

- **Setting key:** `proactive.forced_wake_mention_threshold`
- **Type:** `number`
- **Default:** `2`

Mentions while asleep that force a wake-up.

### Forced-wake window (minutes)

- **Setting key:** `proactive.forced_wake_minutes`
- **Type:** `number`
- **Default:** `30`

How long she stays awake and answers after repeated mentions wake her during sleep (default 30). Afterwards she sleeps again until the greeting hour.

### Birthday wish spread end hour

- **Setting key:** `proactive.birthday_wish_spread_end_hour`
- **Type:** `number`
- **Default:** `20`

Birthday wishes post at a per-person random time between the greeting hour and this hour (0-23, server local) instead of all at once.

### Wake replay max

- **Setting key:** `proactive.sleep_buffered_reply_max`
- **Type:** `number`
- **Default:** `3`

On waking (at the greeting hour), answer up to this many mentions she slept through, per channel. 0 = don't answer missed pings.

## Greeting Channels

Custom multi-select of text channels (stored as `proactive.greeting_targets`, values like `account:channelId`).

Used for:

- Morning greetings
- Quiet outreach
- Goodnight / sleep schedule
- Birthday wishes targeting

Connect a bot, then **Refresh from Discord**.

## Test proactive pathways

Dry-run or fire greeting / goodnight / outreach manually, plus diagnostics explaining why scheduled jobs may have skipped (wrong hour, sleep window, cooldowns, no targets, daemon offline).

### Related hidden / advanced keys

These are stored but not shown as primary tab fields (defaults still apply):

| Key | Default | Meaning |
|-----|---------|---------|
| `proactive.outreach_cooldown_hours` | `6` | Minimum hours between outreach posts in a channel (also influenced by Safety proactive cooldown) |
| `proactive.greeting_outreach_lead_hours` | `2` | Hours before greeting hour when outreach is suppressed |
| `proactive.birthday_use_llm` | `true` | AI-write birthday wishes |
| `proactive.birthday_wish_fallback` | `Happy birthday! 🎂` | Static birthday text when LLM is off/fails |
