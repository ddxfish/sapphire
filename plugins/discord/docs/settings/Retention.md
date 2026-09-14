# Retention

Optional daily purge of old plugin-stored messages (with their media artifacts and processed sleep-buffer rows), traces and finished tasks, voice transcripts / summaries / closed sessions, and unprocessed ambient-distill buffers. Deletes run in chunks so a first purge over a long-lived database does not stall replies.

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

Purge deletes voice-channel transcripts, session summaries, and closed session rows older than this many days (default 30). 0 = keep forever.

### Related hidden key

| Key | Default | Meaning |
|-----|---------|---------|
| `retention.profile_buffer_days` | `7` | When purge is enabled, delete ambient-distill buffer rows that were never distilled and are older than this many days (`0` = keep forever). Distilled rows are deleted the moment their facts are drawn. |
