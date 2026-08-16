# Continuity Mode

Your AI doesn't have to wait for you to talk first. Continuity lets Sapphire wake up on a schedule and do things on its own—greet you in the morning, check the weather, run a dream sequence while you sleep, or remind you about something important.

<img width="50%" alt="sapphire-heartbeat" src="https://github.com/user-attachments/assets/4f12989f-96d2-4407-bf0a-5309738415ad" />


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
| **Initial Message** | What gets sent to the AI when the task triggers. "Good morning!" or "Continue the story." |
| **Chat Name** | Which chat to use. Blank = new dated chat each time. Filled = reuse same chat (keeps history). |
| **Prompt** | Which persona/prompt preset to use. |
| **Toolset** | Which tools the AI can access. "none" disables tools entirely. |
| **LLM Provider** | Which AI backend. "Auto" uses your default. |
| **Model** | Specific model override (optional). |
| **Memory Scope** | Which memory slot to read/write. "none" = no memory access. |
| **Enable TTS** | Speak the response out loud. |
| **Background** | Controlled by **Chat Name**: leave it blank for background mode (no UI switching), or name a chat for foreground mode. |

If a task targets a private chat and the vault is locked, the Chat Name shows **🔒 locked vault** instead of the name — saving the task keeps the real target, or type a name to retarget. A task that fires at a sealed chat fails loudly rather than creating a plaintext chat with the same name. See [PRIVACY.md](PRIVACY.md).
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

The **Triggers** view (sidebar) has two tabs:

**⏰ Time** — schedule-driven triggers: **Tasks** (cron-fired prompts) and **Heartbeats**. Toggle enabled/disabled, edit, run manually (▶), or delete. A timeline strip shows what's coming up next, with chance percentages.

**⚡ Events** — event-driven triggers: **Daemons** (long-running plugin event sources like Discord/email/Telegram) and **Webhooks** (external HTTP triggers). These fire on incoming events rather than the clock — see [Daemons & Webhooks](DAEMONS-WEBHOOKS.md).

## Tips

- Start with infrequent schedules while testing to avoid spam
- Use "Run Now" button to test without waiting for the schedule
- Background tasks are great for things you don't need to see
- Combine with Home Assistant tools for smart home automation
- Low chance % + frequent schedule = occasional surprises

## Reference for AI

Continuity runs scheduled autonomous tasks. Access via the Triggers view in the sidebar.

TASK CREATION:
- Open the Triggers view from the sidebar (⏰ Time tab)
- Click "+ Add Task"
- Set schedule (cron), initial message, prompt, toolset
- Enable/disable TTS and background mode

KEY FIELDS:
- type: task | heartbeat | daemon | webhook
- schedule: cron format (minute hour day month weekday) — for time-based types
- chance: 1-100 probability to actually run
- chat_target: blank = ephemeral, named = persistent chat; '__locked__' = target is a private chat in a sealed vault (display mask; saving keeps the real target)
- background: blank chat_target = background (no UI switching)
- memory_scope: which memory slot to use

COMMON SCHEDULES:
- "0 9 * * *" = 9 AM daily
- "0 */2 * * *" = every 2 hours
- "30 7 * * 1-5" = 7:30 AM weekdays

MANUAL TRIGGER:
- Click ▶ button on any task to run immediately

TROUBLESHOOTING:
- Task not running: check enabled toggle, check cron syntax
- Skipped (chance): random roll failed, will try next scheduled time
