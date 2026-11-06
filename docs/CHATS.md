# Chats — creating, configuring, and managing conversations

## What it is

In Sapphire, the chat is the unit of everything. Each chat carries its own prompt, toolset, voice, model, memory scopes, and spice set — switching chats swaps Sapphire's entire loadout in one move. This guide covers everyday chat work in the sidebar, plus the **Chat Manager**: the admin surface for searching, archiving, exporting, trimming, compressing, repairing, and bulk-deleting chats.

## Using it

### Chats carry their own configuration

A chat is not just a transcript. Stored on each chat:

- **Prompt** — which persona/system prompt drives replies
- **Toolset** — which tools Sapphire can use
- **Provider and model** — which LLM answers (or Auto)
- **Voice, pitch, speed** — how she sounds in this chat
- **Memory scope and contacts scope** — which mind she reads and writes, and whose contacts the email/phone tools see ([MEMORY.md](MEMORY.md#scopes))
- **Spice set, spice on/off, spice turns** — personality variation
- **Documents** — files attached with the + by the message box, plus their budget level (see [Documents](#documents) below)
- **Custom Context and Ghost Message** — extra per-chat prompt injections
- **Date/Time toggle** — whether the current date rides the prompt

Switch to a work chat and she's a focused assistant with work tools; switch to a story chat and she's a character with story tools. The chat *is* the save.

One distinction worth internalizing: the **sidebar is chat settings, the composer is device settings**. Everything in the right sidebar (prompt, toolset, voice, scopes...) belongs to the current chat and travels with it. The mic flyout on the message bar (volume, mute, conversation mode) belongs to the device you're sitting at. Global app configuration lives on the Settings page.

### Everyday basics (sidebar)

The sidebar header handles daily chat work:

- **Chat picker** — the chat name at the top is a dropdown; click it to switch chats. The **See more** item at the bottom opens the Chat Manager.
- **+** — new chat. Names get sanitized (lowercased, spaces become underscores).
- **🗑** — delete the current chat, history and settings both.
- **✕** — clear the current chat: wipes all messages (and their tool images) but keeps the chat and its settings.
- **⋮ menu** — **Import** and **Export** for the current chat.
- **🔒 padlock** — the vault/private-mode toggle, right-aligned. Sealed shows 🔒, open shows 🔓. See [PRIVACY.md](PRIVACY.md).

You can't switch chats while a reply is generating — press Stop first.

### Sending while she's replying: the queue

Each chat runs one turn at a time, and everything else waits its turn. Send while she's still replying (or from a second tab, a satellite, a daemon) and the message **queues**: a **⧖ N queued** chip appears, and it runs when the current turn ends. Several messages you type while she's busy **fold into one turn**, so she answers them together. Click the chip to see what's waiting and take an item out with ×.

Two interleaved turns used to corrupt the transcript; the queue is why that can't happen. Stop still stops the live turn; queued messages then run.

### Slash commands

Type `/` in the message box for the command menu: `/compact`, `/compact-daily`, `/context`, `/status`, `/remove`, `/undo` and more. See [SLASH-COMMANDS.md](SLASH-COMMANDS.md).

### The context bar

The thin bar above the message box shows how full the model's window was on the last turn. Hover it for the breakdown, or type `/context`. See [CONTEXT.md](CONTEXT.md).

### Documents

Each chat can have files attached directly. They belong strictly to that one chat, separate from the Library ([KNOWLEDGE.md](KNOWLEDGE.md)). Attach with the **+** beside the message box, by dropping a file on it, or by pasting a copied file. Text files, PDF and EPUB work, up to 25MB.

Attached files appear as a row of pills above the message box, with an X to remove each one. Pictures you send show there too, counting down the turns the AI can still see them.

**The Docs dropdown** in that row sets how much she's handed each turn, across all the chat's documents: Off, Light (~2K tokens), Normal (~8K) or Heavy (~24K), never more than a quarter of the model's window. Everything that fits arrives whole. Otherwise she gets the best matching excerpts and a list of what she isn't seeing.

**She can always read more.** `read_attachment` walks a file part by part or opens a PDF page, and `search_attachments` looks for meaning and exact words. They're offered whenever the chat has documents and any tools at all, whatever the toolset. Off withholds them too.

**Scanned PDFs** have no text, so search can't reach them. There's no OCR: she looks at the pages as pictures, which takes a model that can see. On one that can't, the image describer speaks for it, so its quality depends on Settings > Images. Some pages can't be shown, like vector charts (their text still reads) and JBIG2 scans unless `jbig2dec` is installed.

A pill marked in amber is found by exact words only until an embedding provider has processed it. Chat export doesn't include documents.

**In a private chat, documents aren't protected.** Sealing the chat encrypts its messages and images but not its documents, so anyone with access to the disk can read their text and the stored PDFs. They're allowed, and their pills turn red with a warning. While sealed, the app hides them.

### Message actions — edit, regenerate, continue

Hover a message for its little toolbar:

- **🗑️ Delete from here** — removes that message and everything after it. The tooltip counts what would go.
- **🔄 Regenerate** — throw away that reply and ask for a fresh one.
- **▶️ Continue** — offered on the **last reply only**, and only useful when that reply ended mid-prose rather than in a tool call. She picks up where the message stopped and keeps writing into the same bubble; nothing is deleted first, and a Stop that produced nothing leaves the message exactly as it was. Some providers can't resume a reply in place — they ignore a trailing half-written message — and you get a plain notice saying so rather than a silent no-op.
- **✏️ Edit** — rewrite the text in place. Only that one bubble redraws, so the rest of the chat doesn't jump. The editor holds the **prose only**: her stored thinking and the turn's tool results stay put and survive the save. **Esc** cancels, **Ctrl/Cmd+Enter** saves.
- **🔊 Replay** — speak the message again.

### If your connection drops mid-reply

Her turn runs on the server, not in your browser. Lock your phone, let a tab sleep, walk behind a flaky proxy — the reply keeps being written.

The page notices the feed died, says *"Connection dropped — she's still working, reconnecting…"*, and rejoins the live turn on its own, picking up exactly where it stopped. The bubble keeps filling in place and Stop still works. Reconnect attempts back off over roughly a minute.

Two other endings:

- **She finished while you were away** — there's nothing live to rejoin, so the page just reloads the history with the completed reply in it.
- **Reconnecting never worked** — you get *"Couldn't reconnect — her reply lands in the chat when she finishes"*. It does; reload the page later and it's there.

Audio is never replayed on a reconnect — you didn't hear the part you missed and it isn't re-read. With TTS on, the finished reply is spoken once when it lands.

### Import and export (single chat)

- **Export** (sidebar ⋮ menu) downloads the current chat's messages as JSON.
- **Import** (sidebar ⋮ menu) reads a JSON file — either a raw message array or an object with a `messages` field — and **replaces the current chat's history** with it. It does not append. If you want to keep what's there, create a new chat first and import into that.

Chat Manager exports are richer: a per-row export includes the chat's name, settings, and full untrimmed message history; those files import fine through the sidebar Import (the messages are read out of them). For bulk export, see below. Exporting personas, prompts, toolsets, and other configuration is a separate system — see [IMPORT-EXPORT.md](IMPORT-EXPORT.md).

### The Chat Manager

Open it from the nav rail — hover/long-press the **Chat** icon and pick **🗂️ Manage** — or via **See more** at the bottom of the sidebar chat picker. It's a table of every chat with message counts, turn counts, size on disk, and dates, all sortable by clicking column headers.

Two counts worth knowing:

- **Msgs** — stored messages: yours, hers, and every tool result. This is what Size tracks.
- **Turns** — your message plus her full reply, tool work included. This is the unit Trim and Compress operate on.

Click a chat's name to open it in the Chat view (works for archived chats too).

#### The six tabs

- **💬 Chats** — ordinary conversations.
- **🗝 Private** — private chats. Only visible when there are private chats to show; while the vault is sealed, private chats are hidden entirely (server-side) and the tab disappears.
- **🎲 Game Room** — game sessions and story playthroughs (badged 🎲 game or 📖 story). These chats don't appear in the sidebar picker at all — the Manager is the one surface that lists everything.
- **🪑 Chat Rooms** — [chat rooms](CHAT-ROOM.md), and each seat's own chat under its room.
- **📚 Librarian** — chats minted by the librarian's tending sessions.
- **📦 Archive** — archived chats, from any category.

Every chat lives in exactly one tab, and archived trumps everything — an archived private chat sits in Archive with a 🗝 badge, not in Private.

#### Search

- **Typing in the search box** filters by name as you type. Esc clears.
- **Enter, or the 🔍 Search all messages button**, runs a deep content search across every chat's messages. Matching rows show a hit count; tab labels show matched/total.

#### Selecting and bulk actions

Click rows (or checkboxes) to select. The toolbar has **All**, **None**, and an **Older than…** picker (select everything untouched for 30/60/90 days — a quick way to find dead weight). Selection only ever includes visible rows: switching tabs or narrowing a search drops anything now hidden, so a bulk action never touches a chat you can't see.

With rows selected, the bulk bar offers:

- **⬇️ Export** — one zip, one JSON file per chat, each importable. Private chats are skipped from the zip by design (nothing readable leaves the database in bulk) — export those one at a time with the row button; the toast tells you if any were skipped.
- **🧹 Clear** — wipe the histories; the chats and their settings survive. Hidden on the Game Room tab (clearing a story strands its journal) and the Archive tab.
- **🗑️ Delete** — delete the chats.

Bulk results are per-chat: a refused chat (a live phone call owns it, agents attached) surfaces as its own toast and never blocks the rest.

#### Row actions

- **🗝 / 🔓 Private toggle** — make a chat private (local models only, hides when the vault locks) or public again. Only offered while the vault is open or absent. One paragraph is all this doc gives it — the full story, including on-the-spot encryption of the history, lives in [PRIVACY.md](PRIVACY.md).
- **📦 / 📂 Archive** — tuck the chat into the Archive tab, or bring it back.
- **✏️ Rename** — renames the chat and carries everything along: message rows, tool images, the chat's RAG documents, and the active-chat pointer. Refused while a live phone call owns the chat or agents are attached (both key on the name).
- **🔧 Repair** — only appears on degraded chats (see below).
- **⬇️ Export** — full raw JSON: name, settings, untrimmed messages.
- **✂️ Trim** — delete the middle of a long chat (see below). Not offered on game/story chats.
- **🗜️ Compress** — summarize old history with an LLM (see below). Not offered on game/story chats.
- **🗑️ Delete** — delete the chat. Cleanup rides along: its RAG documents go, attached agents are dismissed, and plugins are told (story journals and game saves die with their chat).

### Trim — cut the middle out

Trim keeps the first *A* and last *B* turns and deletes everything between. A turn is your message plus everything until your next one — tool chains never split. The modal shows a live preview: how many messages would go, roughly how many tokens that frees, how many remain. You must keep at least the last turn — deleting the recent end is message surgery, which belongs in the chat view itself (hover a message for its 🗑️ Delete from here, or `/remove N`). If the chat has fewer turns than you asked to keep, trim is a no-op and says so.

### Compress — summarize old history

Compress rewrites older history as AI-written summaries while the recent tail stays verbatim. Options in the modal:

- **One summary** — everything except the kept tail becomes a single summary pair.
- **Timeline chunks** — history is split into spans of a few thousand tokens; each span becomes one summary pair in place, so the chronological chain survives.
- **By day** — the `/compact-daily` command: one labelled summary per day for the last few days. See [SLASH-COMMANDS.md](SLASH-COMMANDS.md).
- **Model** — pick the provider/model that writes the summaries. Private chats only offer local providers.
- **Speaks as** — the persona the summary is written as, so it reads as her own memory ("I") and wears her avatar. Starts on the chat's persona. Only the name goes to the summarizer, never the persona's prompt. Room chats have no selector: their summaries are third person, attributed by speaker name.
- **Advanced** — the instructions for what summaries keep and how they're written. Your edit is saved to `user/prompts/compress_instructions.txt` when you press Compress and applies to every later compress; **Reset to default** removes it. Rooms use fixed instructions.
- **Target size** — the approximate total size of the compressed output.
- **Keep last N turns verbatim** — the untouched tail. When an LLM history window is set (Settings → LLM, default 0 = unlimited), the server caps this below half of it; a bigger tail would push the summary out of what the model ever sees.
- **Back up full JSON first** — writes an export to `user/history/exports/` before touching anything. Forced off for private chats (nothing readable leaves the database).

The job runs in the background — minutes on a local model — one at a time; the Manager's summary line shows progress and a toast lands when it finishes. It's write-last: the chat isn't touched until every summarize call has succeeded, so a failure or a server restart mid-job leaves the chat exactly as it was. In the chat, each summary carries a **Compressed summary** badge. Summaries made before these options stay as written.

### Archiving

Archive is a shade, not a freeze. An archived chat vanishes from the sidebar dropdown, but scheduled tasks, daemons, and heartbeats can still reach it, the Manager always lists it, and clicking its name still opens it. Fully reversible. Use it to unclog the picker without deleting anything.

### Degraded chats and repair

If some of a chat's stored messages can't be read at load time — corrupt rows, or encrypted rows whose key is gone — the chat loads **degraded**: everything readable displays, but the chat is read-only until repaired. New turns won't save (you get a warning toast), which protects the unreadable-but-recoverable rows from being clobbered. Degraded rows carry a **⚠ degraded** badge in the Manager.

The **🔧 Repair** action diagnoses first: how many rows are healthy, corrupt, undecryptable, plus any sequence gaps. Running the repair moves unreadable rows to a quarantine — kept verbatim, recoverable if a matching vault backup returns — renumbers the survivors, and the chat can save again.

One guard: if *most* of a chat can't be decrypted, that usually means the vault changed, and the right fix is restoring the matching vault backup — which recovers everything — not repair, which would quarantine most of the chat. The dialog warns you and asks for explicit confirmation in that case. Clearing a degraded chat is also allowed — that's the "just discard it" escape hatch, and it lifts the read-only latch too.

## Settings

There is no Chats section on the Settings page — per-chat settings *are* the sidebar, and each chat stores its own. Related knobs elsewhere:

- **Settings → LLM → General** — the LLM history window (`LLM_MAX_HISTORY`, 0 = unlimited), the context limit (`CONTEXT_LIMIT`, see [CONTEXT.md](CONTEXT.md)), tool decay (`TOOL_EXPIRES_AFTER`), the idle notice (`IDLE_NOTICE_MINUTES`, see [GHOST_MESSAGES.md](GHOST_MESSAGES.md)), the request timeout (`LLM_REQUEST_TIMEOUT`, 240 s) and spice delivery. Providers and models are on the same page.
- **Prompts, Toolsets, Spices pages** — the ↗ buttons beside sidebar dropdowns jump straight there.
- **Vault** — configured via the sidebar padlock; see [PRIVACY.md](PRIVACY.md).

## Quick Troubleshooting

- **My message didn't send, a "⧖ queued" chip appeared** → a turn is live in this chat → it runs when that turn ends; click the chip to see or drop it.
- **Send refused (409)** → the chat is private and the vault is sealed, or its inbox is full → unlock the vault, or wait.
- **Can't switch chats** → a reply is generating → Stop first; switching is blocked mid-generation.
- **Reply seemed to die when my phone locked** → it didn't — the turn runs on the server → wake the tab and it reconnects itself; if it can't, the finished reply is in the chat after a reload.
- **"Nothing to add — this provider doesn't continue a reply in place"** → that provider ignores a half-written reply (OpenAI, Fireworks, GLM/Z.AI behave this way) → the message is untouched; ask her to carry on in a new message, or switch that chat to a provider that accepts one.
- **No ▶️ Continue button on a message** → it's only offered on the chat's *last* reply, and it follows the last reply as new messages land.
- **"Nothing to continue — that reply ended in a tool call"** → there's no half-written sentence to pick up → send a normal message instead.
- **Chat missing from the sidebar picker** → check the Chat Manager's Archive and Game Room tabs — archived and game/story chats never show in the picker → unarchive it, or open it by clicking its name in the Manager.
- **Private chats vanished everywhere** → the vault is sealed → unlock via the sidebar 🔒 padlock; sealed means hidden, not gone.
- **⚠ degraded badge / "read-only until repaired"** → some rows are unreadable → use the row's 🔧 Repair; if the dialog says most rows can't be decrypted, restore the matching vault backup instead.
- **Trim/Compress buttons missing on a row** → it's a game or story chat → those transcripts are load-bearing (journals anchor into them); trim/compress stay off by design.
- **Rename or delete refused** → a live phone call owns the chat, or agents are attached → hang up / dismiss the agents, then retry.
- **Bulk export toast says private chats were skipped** → intended — private chats don't ride bulk zips → export each privately-flagged chat with its own row ⬇️ button while the vault is open.
- **Compress toast never arrived** → the job vanishes if the server restarts mid-job → the chat is untouched (write-last); just re-run the compress.
- **Import didn't add to my chat** → import replaces the current chat's history → create a fresh chat and import there.

## See also

- [PRIVACY.md](PRIVACY.md) — the vault, private chats, encryption, and what "private" enforces
- [IMPORT-EXPORT.md](IMPORT-EXPORT.md) — exporting personas, prompts, toolsets, and other configuration
- [PERSONAS.md](PERSONAS.md) — persona bundles the sidebar faces strip applies to a chat
- [TOOLSETS.md](TOOLSETS.md) — per-chat tool groups
- [PROMPTS.md](PROMPTS.md) — the prompts chats select
- [SPICE.md](SPICE.md) — spice sets and turns
- [BACKUPS.md](BACKUPS.md) — whole-database backups (chats included)

## Reference for AI

Chats are the unit of configuration: each chat stores prompt, toolset, spice_set, voice/pitch/speed, llm_primary/llm_model, memory_scope + people (contacts) scope, documents + their level, custom_context, ghost_context, inject_datetime, spice_enabled/spice_turns. Switching chats applies the whole loadout. Slash commands: SLASH-COMMANDS.md. Context bar, budget, decay: CONTEXT.md. Rooms: CHAT-ROOM.md.

CHAT MANAGER:
- Access: nav rail Chat → flyout → 🗂️ Manage, or sidebar chat picker → See more
- Frontend: views/chat-manage.js; routes: core/routes/chat.py
- Tabs: 💬 Chats / 🗝 Private / 🎲 Game Room / 🪑 Chat Rooms (rooms + seat chats) / 📚 Librarian / 📦 Archive; one chat one tab; archived trumps all; Private tab hidden when empty or vault sealed
- Columns sortable: Name, Msgs (stored messages incl. tool results), Turns (user msg + full reply incl. tool work), Size, Last active, Created
- Search: typing = live name filter; Enter/🔍 = deep content search (GET /api/chats/search)
- Selection: All / None / Older than 30/60/90 days; only visible rows selectable
- Bulk: export zip (POST /api/chats/bulk-export-zip, private chats skipped), clear (bulk-clear; hidden on Game/Archive tabs), delete (bulk-delete); per-chat results, refusals don't block the batch

ROW ACTIONS:
- Private toggle 🗝/🔓 (PUT settings private_chat; vault must be open), archive 📦/📂, rename ✏️, repair 🔧 (degraded only), export ⬇️ (full raw JSON {name, settings, messages}), trim ✂️, compress 🗜️, delete 🗑️
- Trim/compress/clear withheld on game/story chats (journals anchor into transcripts)
- Rename carries rows, tool images, RAG scope, active pointer; refused on live-call chats or attached agents
- Delete cleans RAG docs, dismisses agents, fires chat_deleted hook (plugin chat data dies with chat)

TRIM (POST /api/chats/{name}/trim):
- Turn-snapped middle cut: keep first A + last B turns, delete between; keep_last min 1; preview:true = report only; no-op when turns <= A+B

COMPRESS (POST /api/chats/{name}/compress, core/chat/compress.py):
- Background job, one at a time (409 if busy); modes: whole (one summary pair) | chunked (one pair per ~6k-token span) | daily (/compact-daily: one labelled pair per day, see SLASH-COMMANDS)
- target_tokens ≈ output size; keep_last_turns must be < half LLM_MAX_HISTORY when that is > 0 (default 0 = unlimited, no cap); optional backup JSON to user/history/exports/ (forced off for private chats); private chats need a local provider
- Write-last: chat untouched until all LLM calls succeed; restart mid-job = chat untouched
- Status: GET /api/chats/compress/status (UI polls)
- Voice: optional `persona` in the POST (omitted = the chat's own; "" = none); instructions override via GET/PUT /api/chats/compress/instructions (blank or default removes user/prompts/compress_instructions.txt); rooms ignore both

DEGRADED / REPAIR (POST /api/chats/{name}/repair):
- Unreadable rows skipped at load latch the chat read-only-degraded; saves refused until repaired; clear also unlatches (discard escape hatch)
- Repair preview classifies healthy/corrupt/undecryptable/sequence gaps; real run quarantines bad rows verbatim (recoverable via matching vault backup), re-sequences, unlatches
- Vault-mismatch guard: majority undecryptable → advises restoring vault backup; force:true to override

ONE TURN PER CHAT, EVERYTHING ELSE QUEUES:
- A send while a turn is live QUEUES in the chat's inbox (first SSE line `ticket`, then `queued`, body closes); typed sends fold into one turn (`merged`); UI chip "⧖ N queued" (GET /api/chat/queue, drop one with ×). 409 only = sealed private chat or inbox refusal (full). Cancel the live turn via POST /api/cancel.

DOCUMENTS (per chat, core/documents.py):
- Attach via + / drop / paste; text, PDF, EPUB ≤25MB; docs bar level off 0 / light 2K / normal 8K / heavy 24K tokens per turn, ≤25% of the window
- read_attachment(file, part/page) + search_attachments(query) are added to the turn whenever the chat has documents and any tools; Off withholds them
- Scanned PDF = pages as pictures (vision model, else the image describer); not encrypted in private chats; not in chat export
- Not the Library (KNOWLEDGE.md)

SERVER-OWNED TURNS (core/chat/turn.py, 2026-09-15):
- The engine runs on its own server thread; every HTTP body is a VIEWER of it. A viewer leaving (phone lock, tab sleep, proxy timeout) never ends the turn — only POST /api/cancel does
- Every SSE line carries `seq`. The stream's first line is the turn's `ticket`; a send on a busy chat gets `queued` and the body CLOSES (no socket is held while a turn waits — six held sockets hit the browser's per-host cap). The bus says `inbox_ticket {ticket, state: started|merged|dropped}` and the owning tab follows by ticket. POST /api/chat/attach {ticket, since?, audio?} replays the turn's ring exact-once after `since`, then follows live on the same wire format (first line `attached {audio}`); 202 = still queued, 204 = it ran and is gone (refresh history), 404 unknown ticket (a restart), 409 sealed chat. By chat alone: 204 = no live turn, 404 unknown chat
- Ring holds ~256KB of text; past it the viewer gets a `resync` event and repaints from history before streaming the remainder
- A reattached feed hears her only if nothing has been spoken yet on the turn (`attached: audio=true`); otherwise it is text-only and the client speaks the finished reply once if TTS is on. Every live wire heartbeats (`: keepalive`, 15 s); a reader silent for 45 s cancels itself and the owner follows its ticket (iOS keeps a dead fetch pending forever)
- Client: handlers/send-handlers.js — one owner per send (`pending` by ticket, `follow()` with backoff 1s→15s), api.js attachTurn

MESSAGE ACTIONS (per-message toolbar):
- 🗑️ delete-from-here, 🔄 regenerate, ▶️ continue (last assistant row only), ✏️ edit, 🔊 replay TTS
- Edit swaps one bubble (ui.replaceMessage, no full transcript rebuild); editor is prose-only so stored thinking and the turn's tool half survive; Esc cancels, Ctrl/Cmd+Enter saves
- Continue = POST /api/chat/stream {continue_from: <assistant timestamp>}: no user row sent or saved, engine resumes from the tail row as prefill, edits that row in place on success, leaves it untouched on Stop; refused unless it's the chat's last prose row
- Providers that ignore a trailing assistant message (OpenAI, Fireworks, GLM/Z.AI) yield a `notice` event and the row is left alone — honest no-op, never a silent one

OTHER:
- Archive = UI shade not freeze: hidden from sidebar picker, daemons/cron still reach it, Manager lists it, reversible (POST /api/chats/{name}/archive)
- Clear wipes messages + tool images, keeps chat + settings (bulk-clear or sidebar ✕)
- Sidebar Import replaces current chat's messages (POST /api/history/import, accepts raw array or {messages}); sidebar Export = current chat raw messages
- Game/story chats never appear in the sidebar picker; Chat Manager lists everything
- New chat names sanitized server-side (lowercase, underscores); create echoes sanitized name
- Sidebar = chat settings, composer mic flyout = device settings, Settings page = global
