# Continuity Mode

Your AI doesn't have to wait for you to talk first. Continuity lets Sapphire wake up on a schedule and do things on its own—greet you in the morning, check the weather, run a dream sequence while you sleep, or remind you about something important.

<img src="screenshots/continuity-scheduling.png" alt="Continuity task scheduling">

## What's It For?

- **Morning greeting** — "Good morning! Here's what's happening today..."
- **Dream mode** — Let the AI ramble to itself at 3 AM with a creative prompt
- **Home automation** — "Turn on the lights at sunset" (with Home Assistant tools)
- **Alarm clock** — Wake up to a personalized message instead of a beep
- **Random hellos** — 20% chance every hour to say something unexpected
- **Scheduled research** — Check news on a topic every morning
- **Journaling prompts** — Daily nudge to write

## How It Works

1. Create a task with a schedule (cron format)
2. When the time hits, Sapphire sends your "initial message" to the AI
3. AI responds using whatever prompt, tools, and voice you configured
4. Optionally speaks the response out loud via TTS

Tasks can run in the foreground (switches to that chat) or background (invisible, no UI change).

## Task Fields

| Field | What it does |
|-------|--------------|
| **Name** | Label for the task. Shows in the list and activity log. |
| **Schedule** | When to run (cron format—see below). |
| **Chance %** | Probability the task actually fires. 100 = always, 50 = coin flip. Good for random variety. |
| **Cooldown** | Minutes to wait before this task can run again. Prevents rapid re-triggers. |
| **Iterations** | How many back-and-forth exchanges. 1 = single response. Higher = conversation with itself. |
| **Initial Message** | What gets sent to the AI when the task triggers. "Good morning!" or "Continue the story." |
| **Chat Name** | Which chat to use. Blank = new dated chat each time. Filled = reuse same chat (keeps history). |
| **Prompt** | Which persona/prompt preset to use. |
| **Toolset** | Which tools the AI can access. "none" disables tools entirely. |
| **LLM Provider** | Which AI backend. "Auto" uses your default. |
| **Model** | Specific model override (optional). |
| **Memory Scope** | Which memory slot to read/write. "none" = no memory access. |
| **Enable TTS** | Speak the response out loud. |
| **Background** | Run silently without switching the UI to that chat. Good for invisible tasks. |
| **Inject datetime** | Add current date/time to the system prompt so the AI knows when it is. |

## Cron Basics

Cron format: `minute hour day month weekday`

| Pattern | When it runs |
|---------|--------------|
| `0 9 * * *` | 9:00 AM every day |
| `30 7 * * 1-5` | 7:30 AM weekdays only |
| `0 */2 * * *` | Every 2 hours |
| `0 0 * * *` | Midnight |
| `*/15 * * * *` | Every 15 minutes |
| `0 22 * * 0` | 10 PM on Sundays |

Use `*` for "any value". Use `*/N` for "every N". Use `1-5` for ranges (1=Monday, 0 or 7=Sunday).

## The UI

**Tasks tab** — List of all tasks. Toggle enabled/disabled, edit, run manually, delete.

**Timeline tab** — Shows what's scheduled for the next 24 hours. Chance percentages shown.

**Activity tab** — Log of recent task runs. See what fired, what was skipped (cooldown, chance roll), and any errors.

## Tips

- Start with high cooldowns (60+ min) while testing to avoid spam
- Use "Run Now" button to test without waiting for the schedule
- Background tasks are great for things you don't need to see
- Combine with Home Assistant tools for smart home automation
- Low chance % + frequent schedule = occasional surprises

## Reference for AI

Continuity runs scheduled autonomous tasks. Access via sidebar clock icon.

TASK CREATION:
- Open Continuity from sidebar
- Click "+ Add Task"
- Set schedule (cron), initial message, prompt, toolset
- Enable/disable TTS and background mode

KEY FIELDS:
- schedule: cron format (minute hour day month weekday)
- chance: 1-100 probability to actually run
- cooldown_minutes: prevent re-trigger within N minutes
- iterations: number of AI responses per trigger
- chat_target: blank = ephemeral, named = persistent chat
- background: true = no UI switching
- memory_scope: which memory slot to use

COMMON SCHEDULES:
- "0 9 * * *" = 9 AM daily
- "0 */2 * * *" = every 2 hours
- "30 7 * * 1-5" = 7:30 AM weekdays

MANUAL TRIGGER:
- Click ▶ button on any task to run immediately

TROUBLESHOOTING:
- Task not running: check enabled toggle, check cron syntax
- Skipped (cooldown): wait for cooldown to expire
- Skipped (chance): random roll failed, will try next scheduled time