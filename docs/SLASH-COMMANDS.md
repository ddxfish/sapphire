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

`/compact` and `/trim` aren't offered on game and story chats. `/compact-daily`, `/remove` and `/undo` refuse in chat rooms, on game and story chats, and while the chat has a live phone call.

### /remove and /undo

Typed with no number, `/remove` opens a dialog that warns it can't be undone. With a number it removes that many turns right away.

`/undo` only restores turns **the AI** removed (below). It's refused if the earlier history changed since.

### /compact-daily

Turns the chat's history into one labelled summary per day, for the last **5** days (`COMPACT_DAILY_DAYS`). A day starts at **4 AM** (`DAY_START_HOUR`), so a late night counts toward the day it began. Both settings are on **Settings → Tools → Daily Compact**.

- **Today is never summarized.** It stays as it is until it ends, so each day is always one summary.
- **Earlier summaries pass through untouched.** A run only writes the finished days that have no summary yet. Run it twice in a day and the second run says there is nothing new, and spends nothing.
- **Older days roll off.** Anything older than the window leaves the chat, summarized or not. The JSON backup still has it, and with the Mind Palace on, each day's summary is also kept in memory (Mind → Daily), except for private, room and game chats and chats with memory none ([MEMORY.md](MEMORY.md#daily-a-summary-of-every-day)).
- **What she reads afterwards.** Each day is a pair: a marker, `[Day summary: Monday, November 2, 2026 (48 messages)]`, then the summary, written in the first person as her own memory of that day. In the chat each one carries the **Compressed summary** badge.
- **Size per day** is a ceiling, about 2K tokens by default (a quiet day comes out shorter); give a size to change it, e.g. `/compact-daily 4K` (500 to 20K).
- **What a summary keeps** comes from the compress instructions, shared with `/compact`: the **Advanced** box in the compress dialog ([CHATS.md](CHATS.md#compress--summarize-old-history)).
- **How she writes a day** is its own short text: **Settings → Tools → Daily Compact → Day summary instructions**. By default it asks for tight first-person prose with you as "you", no shorthand, a paragraph per topic that names its people and things, dates written out (never "tomorrow" or "next week"), nothing added that was not said, and nothing that happens every day (the night routine, her sheet read back, memory saves, wake-ups, scenes, heartbeats) unless it failed, brought news or you answered it. A day on which nothing else happened is one sentence. Edit it, or Reset to default. **Mind → Daily → Dry run** writes one day of a chat you pick with the wording in its box, saved or not, and changes nothing: the chat and her memory stay as they are. It works on ordinary chats only (not a private chat, a room or a game), and today so far can be tried too. It is one real model call with that chat's own model, and it can only try a day that has no summary yet. A day is written from what was said: what a tool returned is not shown to the summary. What the size means and what the transcript labels mean are fixed and can't be lost by an edit.
- **Keyed memories stay keyed.** What a memory tool returned for a call made with a `private_key` is withheld from the summarizer (in `/compact` too). Her own words about it in the chat are ordinary text and can still be summarized.
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

Compacting more than one chat? Give each task its own time, a few minutes apart. Only one compress job runs at a time, so two tasks at the same minute collide and the second one is refused that night.

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

USER COMMANDS: /compact, /compact-daily [tokens], /context, /status, /prompt, /continue, /retry, /replay, /remove N, /undo, /trim, /repair, /help [command]. /compact, /trim not on game chats; /compact-daily, /remove and /undo are refused in chat rooms ("…doesn't work in a chat room yet"), on a game or story chat ("…doesn't work on a game's chat") and during a live phone call.

YOUR TOOL: slash_command(command) — only /remove N and /compact-daily are yours (ai=True). Toolset membership is the permission (ships in `personality`).
- /remove N: runs after your turn ends; removes the N turns before this one AND this turn (the request and your reply go too). To undo the last exchange, /remove 1. Only when the user asks. The user is notified; /undo restores. Stopping the turn drops it. One pending at a time. Refused in agent runs.
- /compact-daily [tokens]: one summary pair per FINISHED day for the last COMPACT_DAILY_DAYS (5) days; days start at DAY_START_HOUR (4); today stays raw. Days already summarised are left alone (a second run the same day = "Nothing new", no LLM spend); days older than the window roll off the chat. About 2K tokens a day by default (500-20K), quiet days shorter. Runs after your turn, in the background, on the chat's own model and persona; a JSON backup first unless the chat is private (private = a local provider only). Write-last: if the chat changed mid-job or the summaries came out bigger than the source, it fails and the chat is untouched. Refused in rooms, game chats, live calls, and while a reply is still running.
- Pair shape: user row `[Day summary: Monday, November 2, 2026 (N messages)]` + assistant row = the summary, first person as you; rows carry compressed, compressed_day (YYYY-MM-DD).
- After a daily compact the Mind Palace keeps each new day's summary in the chat's memory scope (layer daily, see MEMORY.md): a day that rolls off the chat is still reachable with search_memory(layer='daily') or get_recent_memories(layer='daily', day='YYYY-MM-DD').
- Summary wording: the compress instructions plus the day wording, both edited by the user in Settings > Tools > Daily Compact (rooms use fixed ones). A day's summary never includes tool results, only that a tool ran; for /compact, results of tool calls made with a private_key are withheld.
- Nightly: no built-in timer; a scheduled task in that chat with slash_command in its toolset, message "Run /compact-daily.", scheduled just AFTER DAY_START_HOUR (before it, yesterday is still today).
