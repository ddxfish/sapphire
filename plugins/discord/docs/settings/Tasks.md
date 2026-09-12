# Tasks

Scheduled and conversational follow-ups: reminders, commitments, task delivery, and birthday capture/wishes.

## Settings

### Task follow-ups

- **Setting key:** `cognitive.task_follow_up_enabled`
- **Type:** `boolean`
- **Default:** ON

Deliver due follow-ups from voice sessions, reminders, and commitments.

### Commitment follow-ups

- **Setting key:** `cognitive.commitment_followups_enabled`
- **Type:** `boolean`
- **Default:** ON

When someone says they will do something, follow up later.

### Reminder requests

- **Setting key:** `cognitive.reminder_followups_enabled`
- **Type:** `boolean`
- **Default:** ON

"Remind me in 5 minutes to…" queues a task and @mentions them when due.

### Learn birthdays

- **Setting key:** `profile.birthday_capture_enabled`
- **Type:** `boolean`
- **Default:** ON

Capture birthdays mentioned in chat onto user profiles.

### Birthday wishes

- **Setting key:** `profile.birthday_followups_enabled`
- **Type:** `boolean`
- **Default:** ON

Wish users a happy birthday on the day.

### Bulk birthday message

- **Setting key:** `profile.birthday_bulk_enabled`
- **Type:** `boolean`
- **Default:** ON

One combined message when several birthdays land on the same day.

### Bulk birthday threshold

- **Setting key:** `profile.birthday_bulk_threshold`
- **Type:** `number`
- **Default:** `2`

Combine into one message at this many same-day birthdays.
