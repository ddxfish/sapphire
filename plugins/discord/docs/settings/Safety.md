# Safety

DM allow, reply rate limits, proactive cooldowns, and presence-only quiet hours.

## Settings

### Allow DMs

- **Setting key:** `safety.allow_direct_messages`
- **Type:** `boolean`
- **Default:** ON

ON (default): the bot replies to direct messages. OFF: she won't reply to DMs (they are still stored in the plugin database, and scheduled reminders she owes someone may still deliver by DM).

### Reply cooldown (seconds)

- **Setting key:** `safety.rate_limit_seconds`
- **Type:** `number`
- **Default:** `30`

Minimum seconds between replies in the same channel, DMs included (default 30). 0 = no limit. This is the only throttle on the reply pipeline.

### Proactive cooldown (hours)

- **Setting key:** `safety.proactive_cooldown_hours`
- **Type:** `number`
- **Default:** `6`

Minimum hours between proactive posts of the same kind (greeting, outreach, goodnight, birthday) in one channel. Reminder and commitment follow-ups bypass this.

### Quiet hours

- **Setting key:** `safety.quiet_hours_enabled`
- **Type:** `boolean`
- **Default:** OFF

Presence only: during the quiet window her status switches to the Quiet / sleep status. Replies and proactive posts still send — use the Sleep schedule to actually go quiet.

### Quiet start (server local hour)

- **Setting key:** `safety.quiet_hours_start`
- **Type:** `number`
- **Default:** `0`

Hour (0-23, server local) quiet hours begin. Start later than end wraps past midnight; start equal to end disables.

### Quiet end (server local hour)

- **Setting key:** `safety.quiet_hours_end`
- **Type:** `number`
- **Default:** `0`

Hour (0-23, server local) quiet hours end. Example: start 22, end 7 covers overnight.
