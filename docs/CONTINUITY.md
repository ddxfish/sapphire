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

The **Chat Name** field decides where that happens. Name a chat and the task runs inside it with saved history—she remembers previous runs, and you can read the conversation later. The UI never switches chats on its own. Leave it blank and the task runs in an invisible, ephemeral background context that leaves no chat behind.

Time-based tasks are half the picture: Sapphire can also react to outside events—Discord messages, email, live phone calls, HTTP requests. Those lanes (daemons, realtime rules, webhooks) share the same task machinery and are covered in [Daemons & Webhooks](DAEMONS-WEBHOOKS.md).

## Task Fields

**Schedule and firing**

| Field | What it does |
|-------|--------------|
| **Name** | Label for the task. Shows in the list and activity log. |
| **Schedule** | When to run (cron format—see below). Heartbeats use a simple interval ("beats every 30 minutes") instead. |
| **Active hours** | Only fire inside an hour window—cron matches outside it are skipped. Overnight windows work (e.g. 8 PM–4 AM). |
| **Chance %** | Probability the task actually fires. 100 = always, 50 = coin flip. Good for random variety. |
| **Max runs** | Auto-disable after this many runs. 1 = one-shot task, 0 = unlimited. The editor shows progress toward the cap. |
| **Delete after run** | Remove the task itself once it finishes its runs—for one-shots that should leave no trace. |

**AI and chat**

| Field | What it does |
|-------|--------------|
| **Persona** | Auto-fills prompt, voice, toolset, model, scopes, and more from a persona profile. You can still override individual settings. |
| **Prompt** | Which prompt preset to use. |
| **Toolset** | Which tools the AI can access. "none" disables tools entirely. |
| **LLM Provider** | Which AI backend. "Auto" uses your default. |
| **Model** | Specific model override (optional). |
| **Private** | Force this task onto local providers only. Auto-checked when the pinned prompt requires privacy. |
| **Chat Name** | Blank = invisible background run, no chat saved. Filled = run in that chat and keep history. |
| **Initial Message** | What gets sent to the AI when the task triggers. "Good morning!" or "Continue the story." |
| **Inject datetime** | Add current date/time to the system prompt so the AI knows when it is. |

**Voice**

| Field | What it does |
|-------|--------------|
| **Speak on server speakers** | Play the response through TTS on the machine Sapphire runs on. |
| **Play in browser** | Send the TTS audio to open browser tabs instead—one tab claims and plays it. |
| **Voice / Pitch / Speed** | Override the TTS voice for this task. Pitch and speed range 0.5–2.0; blank = current defaults. |

**Mind and limits**

| Field | What it does |
|-------|--------------|
| **Mind scopes** | Per-task memory, knowledge, people, and goals scopes (plus any plugin scopes). Default "none"—isolated unless you say otherwise. |
| **Context window** | Token limit for conversation history. 0 = app default. |
| **Max parallel tools / Max tool rounds** | Per-task tool execution limits. 0 = app default. |

If a task targets a private chat and the vault is locked, the Chat Name shows **🔒 locked vault** instead of the name — saving the task keeps the real target, or type a name to retarget. A task that fires at a sealed chat fails loudly rather than creating a plaintext chat with the same name. See [PRIVACY.md](PRIVACY.md).

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

**Triggers** in the sidebar is a group of five views:

| View | What lives there |
|------|------------------|
| 💓 **Heartbeat** | Recurring self-pulses on a simple interval ("beats every 30 minutes"). Vitals cards plus a heartbeat timeline. |
| 📅 **Scheduled** | Tasks at set times—one-off or recurring. Two columns: what **you** scheduled, and what **Sapphire** scheduled herself (via her `schedule_task` tool) or her plugins declared. |
| 📡 **Daemons** | Event listeners (Discord, email, Telegram) that wake her when something happens. |
| ⚡ **Realtime** | Live inbound session rules—phone lines she answers and holds open. |
| 🔗 **Webhooks** | HTTP triggers so outside services can poke her. |

Heartbeat and Scheduled are the time-driven views this doc covers. Each item can be toggled, edited, exported, run manually (▶), stopped mid-run (⏹ appears on a running card; toggling off stops it too), or deleted, and a timeline strip shows what ran and what's coming up, with chance percentages. Daemons, Realtime, and Webhooks fire on incoming events rather than the clock—see [Daemons & Webhooks](DAEMONS-WEBHOOKS.md).

## Tips

- Start with infrequent schedules while testing to avoid spam
- Use the ▶ Run now button to test without waiting for the schedule
- Background tasks (blank Chat Name) are great for things you don't need to see
- Combine with Home Assistant tools for smart home automation
- Low chance % + frequent schedule = occasional surprises
- Max runs 1 + Delete after run = a clean fire-and-forget reminder

## Reference for AI

Continuity runs scheduled autonomous tasks. UI: **Triggers** nav group with five views — Heartbeat, Scheduled, Daemons, Realtime, Webhooks.

TASK CREATION (UI):
- Triggers > Scheduled > "+ Task" (heartbeats: Triggers > Heartbeat > "+ Heartbeat")
- Set schedule (cron or simple time picker), initial message, prompt, toolset; picking a Persona auto-fills prompt/voice/toolset/model/scopes
- Accordions: AI, Chat, Voice, Mind (scopes), Execution Limits

SELF-SCHEDULING (tool):
- schedule_task(description, time) — time like '5pm'/'17:00' = one-shot that auto-deletes after firing; a 5-field cron string = recurring. AI-scheduled tasks are capped and appear in the Scheduled view's "Sapphire scheduled" column.

KEY FIELDS:
- type: task | heartbeat | daemon | webhook. Realtime rules are daemon tasks whose event source is realtime — they gate live sessions instead of firing; see DAEMONS-WEBHOOKS.md
- schedule: cron (minute hour day month weekday) — time-based types
- chance: 1-100 probability to actually run
- active_hours_start/end: hour integers; cron matches outside the window are skipped; overnight wrap supported (e.g. 20→4)
- chat_target: blank = ephemeral background, named = persistent chat history (never switches the UI); '__locked__' = display mask for a private chat sealed in the locked vault (saving keeps the real target)
- persona: persona name; resolved at run time to fill prompt/voice/toolset/model/scopes
- initial_message: what the AI receives when the task fires
- inject_datetime: true = date+time stamp in the system prompt; the string 'date' = day-only stamp (prompt-cache-friendly for multi-run sessions)
- tts_enabled: server speakers; browser_tts: route audio to an open browser tab instead
- voice / pitch / speed: TTS overrides; pitch/speed 0.5-2.0, null = current default
- privacy_required: local providers only; auto-set when the pinned prompt requires privacy
- context_limit / max_parallel_tools / max_tool_rounds: per-task execution limits, 0 = app default
- max_runs: auto-disable after N runs (0 = unlimited); delete_after_run: delete the task once its runs finish
- *_scope keys (memory_scope, knowledge_scope, ...): per-task mind scopes, default 'none'

MANUAL TRIGGER:
- ▶ Run now on any task in the Heartbeat/Scheduled views

TROUBLESHOOTING:
- Task not running: check enabled toggle, cron syntax, active-hours window
- Skipped (chance): random roll failed, will try next scheduled time
- Task went quiet after N runs: max_runs reached — it auto-disabled (editor shows runs done)
