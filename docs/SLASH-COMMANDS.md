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
| `/compact-daily [tokens] [instructions]` | Summarizes history into one labelled summary per day for the last few days (below) |
| `/extract-daily [tokens] [instructions]` | Saves this chat's finished days to memory as day summaries and leaves the chat as it is; for old chats (below) |
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
- **Size per day** is a cap on the written summary, about 2K tokens by default (a quiet day comes out shorter); give a size to change it, e.g. `/compact-daily 4K` (500 to 20K). A model's thinking is not counted: it has its own room. A long day is written in parts and merged, and the parts grow with the size you give.
- **Instructions for one run.** Any words after the command (after the size, if you give one) go to whoever writes the days, for this run only, on top of the usual wording: `/compact-daily 6K keep November 25 in full detail`. Where they disagree with the usual wording, they win. Each day is written on its own, so name a day by its date, not "yesterday". They can't lift the size (give a bigger one) and can't start with a bare number, which would be read as the size. Up to 8,000 characters.
- **A summary is kept whole or not at all.** One that comes back cut off, empty or over its size is asked for once more with more room. If that fails too, the job fails and says why. A cut-off almost always means a thinking model ran out of room: turn its thinking down or raise its max tokens.
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

### /extract-daily

For a chat that `/compact-daily` would empty: one from months ago, or any chat you want to keep whole. It writes the same day summaries, one per finished day of the whole chat however old, and saves them to memory (Mind → Daily) under each day's own date. **The chat is not changed.** It needs the Mind Palace.

- **It starts at once.** The answer says what it is saving: how many days, from when to when, which model writes them, how big and into which memory scope. Nothing in the chat changes, each day can be deleted in Mind → Daily, and `/extract-daily stop` ends it.
- **One entry per chat per day.** A day this chat has already given is left alone, so running it twice adds nothing and rewrites nothing. So is a day `/compact-daily` already summarized in this chat. Two chats that both have February 10 give two entries for that date, side by side, each naming its chat. Nothing is merged or overwritten.
- **Saved as it goes.** Each day is kept the moment it is written, oldest first. If it fails or you stop it (`/extract-daily stop`), the days so far stay and the next run takes up from there.
- **Left out:** today (it has not ended), and an older plain `/compact` summary, which covers many days under one date.
- **A day you deleted** in Mind → Daily can be extracted again while its turns are still in the chat. That is how a day is written again (with another model, a bigger size or other instructions): delete it there, then run the command.
- It uses the chat's own model, persona and memory scope, so set those on an old chat before you run it. Size per day, instructions for the run and the wording are `/compact-daily`'s: `/extract-daily 8K February 10 matters most, keep its details`, and the day summary instructions. Try the wording on one day first with **Mind → Daily → Dry run**.
- It is refused for private chats, rooms, game chats and chats with memory none, and it shares the one compress job slot.
- A chat from before scheduled turns were labelled reads its automatic turns as yours, so an old day may say "you asked me to" about a nightly task.

Compacting more than one chat? Give each task its own time, a few minutes apart. Only one compress job runs at a time, so two tasks at the same minute collide and the second one is refused that night.

---

## Sapphire can run them too

With the `slash_command` tool in her toolset she can run three commands herself: `/remove N`, `/compact-daily` and `/extract-daily`, the last two with a size and her own instructions for the days. Having the tool *is* the permission; there's no separate switch. It ships in the **personality** toolset ([TOOLSETS.md](TOOLSETS.md)).

- **Her `/remove N` waits for her turn to end**, then removes the N turns before it **and her own turn**. You get a notification, and `/undo` puts it all back.
- Stopping her turn cancels the removal.
- One pending command at a time. It's refused inside an agent's run.

So "forget the last two messages" works, and so does "compact our week".

## Plugins

A plugin can add its own commands (`capabilities.commands`). They're yours to type, never hers, and a core command's name always wins. See [plugin-author/commands.md](plugin-author/commands.md).

---

## Reference for AI

SLASH COMMANDS (core/commands.py): typed at the start of a chat message; menu on '/'; '//text' = literal slash; chat view only (not the room composer). Routes: GET /api/commands, POST /api/commands/run {line} (?chat=).

USER COMMANDS: /compact, /compact-daily [tokens] [instructions], /extract-daily [tokens] [instructions], /context, /status, /prompt, /continue, /retry, /replay, /remove N, /undo, /trim, /repair, /help [command]. /compact, /trim not on game chats; /compact-daily, /remove and /undo are refused in chat rooms ("…doesn't work in a chat room yet"), on a game or story chat ("…doesn't work on a game's chat") and during a live phone call.

YOUR TOOL: slash_command(command) — only /remove N, /compact-daily and /extract-daily are yours (ai=True). Toolset membership is the permission (ships in `personality`).
- /remove N: runs after your turn ends; removes the N turns before this one AND this turn (the request and your reply go too). To undo the last exchange, /remove 1. Only when the user asks. The user is notified; /undo restores. Stopping the turn drops it. One pending at a time. Refused in agent runs.
- /compact-daily [tokens] [instructions]: one summary pair per FINISHED day for the last COMPACT_DAILY_DAYS (5) days; days start at DAY_START_HOUR (4); today stays raw. Days already summarised are left alone (a second run the same day = "Nothing new", no LLM spend); days older than the window roll off the chat. The size caps each WRITTEN day: about 2K tokens by default (500-20K), quiet days shorter, your thinking not counted. Words after the size are your instructions to the writer for this run, on top of the usual wording and winning over it: `/compact-daily 6K keep November 25 in full detail`. Each day is written alone and knows only its own date, so name days by date. Instructions cannot lift the size (give a bigger one) or start with a bare number. A summary that comes back cut off, empty or over its size is tried once more, then the job fails with the reason and that summary is not kept. Runs after your turn, in the background, on the chat's own model and persona; a JSON backup first unless the chat is private (private = a local provider only). Write-last: if the chat changed mid-job or the summaries came out bigger than the source, it fails and the chat is untouched. Refused in rooms, game chats, live calls, and while a reply is still running.
- /extract-daily [tokens] [instructions]: every FINISHED day of this chat that is still raw turns, however old, written as a day summary and saved to the daily layer of the chat's memory scope under that day's date. The chat is NOT changed (no pairs, no backup, nothing rolls off). It starts at once (yours when your turn ends) and the answer says what it is saving: days, range, model, size, scope. Size and instructions as for /compact-daily: `/extract-daily 8K February 10 matters most, keep its details`. One entry per chat per day: days this chat already gave, or that /compact-daily summarized here, are skipped; another chat's same date is a second entry. To have a day written again, the user deletes it in Mind > Daily first. Each day is kept as it is written; `/extract-daily stop` ends it after the current day and a rerun continues. Today and plain /compact summaries are left out. Refused: private, room, game, memory scope none, palace off, another compress job running.
- Pair shape: user row `[Day summary: Monday, November 2, 2026 (N messages)]` + assistant row = the summary, first person as you; rows carry compressed, compressed_day (YYYY-MM-DD).
- After a daily compact the Mind Palace keeps each new day's summary in the chat's memory scope (layer daily, see MEMORY.md): a day that rolls off the chat is still reachable with search_memory(layer='daily') or get_recent_memories(layer='daily', day='YYYY-MM-DD').
- Summary wording: the compress instructions plus the day wording, both edited by the user in Settings > Tools > Daily Compact (rooms use fixed ones). A day's summary never includes tool results, only that a tool ran; for /compact, results of tool calls made with a private_key are withheld.
- Nightly: no built-in timer; a scheduled task in that chat with slash_command in its toolset, message "Run /compact-daily.", scheduled just AFTER DAY_START_HOUR (before it, yesterday is still today).
