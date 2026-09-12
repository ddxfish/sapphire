# Retention

Optional daily purge of old plugin-stored messages, traces, and voice transcripts.

## Settings

### Retention purge

- **Setting key:** `retention.enabled`
- **Type:** `boolean`
- **Default:** OFF

OFF (default): nothing is ever purged. ON: a daily purge (4:30 AM server time) deletes plugin rows older than the day limits below — enabling this is a conscious choice; the first run on a long-lived database deletes everything beyond the windows.

### Keep messages (days)

- **Setting key:** `retention.message_days`
- **Type:** `number`
- **Default:** `90`

Purge deletes the plugin's stored copies of Discord messages older than this many days (default 90). 0 = keep forever. Nothing is deleted on Discord itself.

### Keep traces (days)

- **Setting key:** `retention.trace_days`
- **Type:** `number`
- **Default:** `14`

Purge deletes decision traces (the Traces view) older than this many days (default 14). 0 = keep forever.

### Keep transcripts (days)

- **Setting key:** `retention.transcript_days`
- **Type:** `number`
- **Default:** `30`

Purge deletes voice-channel transcripts older than this many days (default 30). 0 = keep forever.

### Related hidden key

| Key | Default | Meaning |
|-----|---------|---------|
| `retention.profile_buffer_days` | `7` | When purge is enabled, delete processed ambient-distill buffer rows older than this many days (`0` = keep forever) |
