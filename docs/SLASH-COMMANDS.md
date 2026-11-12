# Slash Commands

Type `/` at the start of a message in the chat box and a menu of commands opens. A command does something to the chat (summarize it, show what's filling its context, remove the last few turns) instead of sending a message to Sapphire.

- `//text` sends a message that really starts with a slash.
- A path like `/home/you/notes.txt` is an ordinary message, not a command.
- Commands work in the main chat view. The Chat Room's composer doesn't take them.

---

## The commands

| Command | What it does |
|---|---|
| `/compact` | Opens the compress dialog: summarize older history, keep the recent tail. See [CHATS.md](CHATS.md#compress--summarize-old-history) |
| `/compact-daily [tokens]` | Summarizes history into one labelled summary per day for the last few days (below) |
| `/context` | What's filling this chat's context, biggest first. See [CONTEXT.md](CONTEXT.md) |
| `/status` | This chat's persona, prompt, model, toolset, size, context use and private flag |
| `/prompt` | Shows the prompt the next turn would send |
| `/continue` | Keep writing the last reply |
| `/retry` | Redo the last reply |
| `/replay` | Read the last reply aloud again |
| `/remove N` | Remove the last N turns (a turn is one message of yours and every reply to it) |
| `/undo` | Put back the turns the AI last removed |
| `/trim` | Opens the trim dialog: keep the first and last turns |
| `/repair` | Checks for unreadable messages (opens the repair dialog) |
| `/help [command]` | Lists the commands, or explains one |

`/compact` and `/trim` aren't offered on game and story chats. `/compact-daily`, `/remove` and `/undo` refuse in chat rooms.

### /remove and /undo

Typed with no number, `/remove` opens a dialog that warns it can't be undone. With a number it removes that many turns right away.

`/undo` only restores turns **the AI** removed (below). It's refused if the earlier history changed since.

### /compact-daily

Turns the chat's history into one labelled summary per day, for the last **5** days (`COMPACT_DAILY_DAYS`). A day starts at **4 AM** (`DAY_START_HOUR`), so a late night counts toward the day it began. Both settings are on **Settings → Tools → Daily Compact**.

- **Today is never summarized.** It stays as it is until it ends, so each day is always one summary.
- **Earlier summaries pass through untouched.** A run only writes the finished days that have no summary yet. Run it twice in a day and the second run says there is nothing new, and spends nothing.
- **Older days roll off.** Anything older than the window leaves the chat, summarized or not. The JSON backup still has it.
- **What she reads afterwards.** Each day is a pair: a marker, `[Day summary: Monday, November 2, 2026 (48 messages)]`, then the summary, written in the first person as her own memory of that day. In the chat each one carries the **Compressed summary** badge.
- **Size per day** defaults to about 2.5K tokens; give a size to change it, e.g. `/compact-daily 4K` (500 to 20K).
- **What a summary keeps** comes from the compress instructions, shared with `/compact`: the **Advanced** box in the compress dialog ([CHATS.md](CHATS.md#compress--summarize-old-history)).
- It runs in the background with the chat's own model and persona and keeps the last turn as it is. One compress job runs at a time.
- A JSON backup is written first to `user/history/exports/`, except for private chats. A private chat is summarized by a local provider only.
- **Write-last.** The chat isn't touched until every day's summary has succeeded. If the chat changed while the job ran, or the summaries came out bigger than what they replace, the job fails and the chat stays as it was.
- It's refused in rooms, game chats, live phone calls, and while she's replying.

#### Every night

There is no built-in timer: a scheduled task runs it ([CONTINUITY.md](CONTINUITY.md)).

1. Make a task whose **Chat Name** is the chat to compact, with a toolset that carries `slash_command`.
2. **Initial Message**: something like `Run /compact-daily.`
3. **Schedule** it a little *after* the day-start hour, e.g. 4:05 AM for a 4 AM day. Before that hour, yesterday is still "today" and stays raw.

The compact starts when the task's turn ends. That turn is part of the new day, so it stays raw until the next night.

---

## Sapphire can run them too

With the `slash_command` tool in her toolset she can run two commands herself: `/remove N` and `/compact-daily`. Having the tool *is* the permission; there's no separate switch. It ships in the **personality** toolset ([TOOLSETS.md](TOOLSETS.md)).

- **Her `/remove N` waits for her turn to end**, then removes the N turns before it **and her own turn**. You get a notification, and `/undo` puts it all back.
- Stopping her turn cancels the removal.
- One pending command at a time. It's refused inside an agent's run.

So "forget the last two messages" works, and so does "compact our week".

## Plugins

A plugin can add its own commands (`capabilities.commands`). They're yours to type, never hers, and a core command's name always wins. See [plugin-author/commands.md](plugin-author/commands.md).

---

## Reference for AI

SLASH COMMANDS (core/commands.py): typed at the start of a chat message; menu on '/'; '//text' = literal slash; chat view only (not the room composer). Routes: GET /api/commands, POST /api/commands/run {line} (?chat=).

USER COMMANDS: /compact, /compact-daily [tokens], /context, /status, /prompt, /continue, /retry, /replay, /remove N, /undo, /trim, /repair, /help [command]. /compact, /trim not on game chats; /compact-daily, /remove, /undo refused in rooms.

YOUR TOOL: slash_command(command) — only /remove N and /compact-daily are yours (ai=True). Toolset membership is the permission (ships in `personality`).
- /remove N: runs after your turn ends; removes the N turns before this one AND this turn (the request and your reply go too). To undo the last exchange, /remove 1. Only when the user asks. The user is notified; /undo restores. Stopping the turn drops it. One pending at a time. Refused in agent runs.
- /compact-daily [tokens]: one labelled summary per day for the last COMPACT_DAILY_DAYS (5) days, days start DAY_START_HOUR (4); today stays raw (one pair per day); earlier pairs pass through untouched, only finished days without a pair are written, a second run the same day = "Nothing new" and no LLM spend; older than the window rolls off; default ~2.5K tokens/day (500-20K); background, chat's own model + persona; keeps the last turn; JSON backup unless private (private = local provider only); write-last (chat changed mid-job or summaries bigger than the source = fails, chat untouched); refused in rooms, game chats, live calls, mid-reply. Runs after your turn ends, in a scheduled task's turn too.
- Pair shape: user row `[Day summary: Monday, November 2, 2026 (N messages)]` + assistant row = the summary, first person as you; rows carry compressed, compressed_day (YYYY-MM-DD).
- Summary wording = the compress instructions (shared with /compact; user/prompts/compress_instructions.txt, default in core/chat/compress.py); rooms use fixed ones.
- Nightly: no built-in timer; a scheduled task in that chat with slash_command in its toolset, message "Run /compact-daily.", scheduled just AFTER DAY_START_HOUR (before it, yesterday is still today).
