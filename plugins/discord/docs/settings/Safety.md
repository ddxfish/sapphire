# Safety

DM allow, reply rate limits, proactive cooldowns, and presence-only quiet hours.

## Settings

### Allow DMs

- **Setting key:** `safety.allow_direct_messages`
- **Type:** `boolean`
- **Default:** OFF

OFF (default, since 2026-09-13): she won't reply to direct messages. Anyone who shares a server with the bot can DM it, so a DM is an outside line until you open it. ON: she replies to DMs (they are stored in the plugin database; scheduled reminders she owes someone may still deliver by DM). Existing installs that had DMs on keep their saved value.

### DM budget per person per day

- **Setting key:** `safety.dm_daily_budget`
- **Type:** `number`
- **Default:** `30`

When DMs are on: how many DM messages from one person she answers per day before going quiet on them until tomorrow (a dropped message leaves an `event_dropped` trace). `0` = unlimited.

### Tools stay in the server

- **Setting key:** `safety.tools_stay_in_server`
- **Type:** `boolean`
- **Default:** ON

While she is replying inside a Discord server, her Discord tools (send, GIF, image, reaction, read) can only act in that server; DM channels are never a target from inside an event. A message in one server cannot make her post into another. From the operator's own chats every channel is reachable. OFF removes the limit.

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
