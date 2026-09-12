# Presence

Discord online status and activity line (playing / listening / custom), including optional cycling.

## Settings

### Awake status

- **Setting key:** `presence.status`
- **Type:** `string`
- **Default:** `online`

Discord status shown while awake: Online, Idle, Do Not Disturb, or Invisible.

**Options:**

- `online` — Online
- `idle` — Idle
- `dnd` — Do not disturb
- `invisible` — Invisible

### Default activity

- **Setting key:** `presence.activity`
- **Type:** `string`
- **Default:** *(empty)*

Activity line under the bot's name while awake — 'playing: chess', 'listening: lo-fi', 'watching: the server', 'competing: trivia', or plain text for a custom status. Empty = none. Ignored while cycling is on.

### Quiet / sleep status

- **Setting key:** `presence.quiet_status`
- **Type:** `string`
- **Default:** `idle`

Status shown during quiet hours and while asleep.

**Options:**

- `online` — Online
- `idle` — Idle
- `dnd` — Do not disturb
- `invisible` — Invisible

### Cycle activities

- **Setting key:** `presence.cycling_enabled`
- **Type:** `boolean`
- **Default:** OFF

ON: while awake, rotate a random activity from the checked presets plus Custom activities every cycle interval. OFF (default): keep the single Default activity.

### Cycle interval (seconds)

- **Setting key:** `presence.cycle_interval_seconds`
- **Type:** `number`
- **Default:** `300`

Seconds between activity changes while cycling. Minimum 60.

### Custom activities

- **Setting key:** `presence.activities_custom`
- **Type:** `list`
- **Default:** *(empty)*

Extra activities added to the cycling pool alongside the checked presets. Same format as Default activity; enter '-' for a no-activity turn.

### Situation-aware presence

- **Setting key:** `presence.situation_presence_enabled`
- **Type:** `boolean`
- **Default:** OFF

OFF (default): presence from sleep/quiet/cycling presets. ON: bias awake status/activity from the primary greeting channel's situation (sleep and quiet hours still override).

## Presence Activity Presets

Custom checklist of shipped activity lines (from `statuses/` catalogs). Checked items enter the cycling pool when **Cycle activities** is on.

- Selection equal to the built-in defaults is stored as “follow defaults”.
- Reload the plugin after editing preset JSON files on disk.
