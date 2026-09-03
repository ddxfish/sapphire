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
- **Memory scopes** — which memory/knowledge/goals boxes she reads and writes
- **Spice set, spice on/off, spice turns** — personality variation
- **Documents (RAG)** — files uploaded to this chat, plus a context level
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

### One turn per chat

Each chat runs one turn at a time. If you send a message while Sapphire is still replying in that chat — Enter past the Stop button, or a second browser tab on the same chat — the send is refused with a toast: *"Sapphire is still replying in this chat — wait for her to finish or press Stop."* (An HTTP 409 under the hood.) Other tabs viewing the same chat flip their Send button to Stop while a turn is live. This protects the history: two interleaved turns used to corrupt the transcript in ways that broke the next request. Press Stop, then send.

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

#### The five tabs

- **💬 Chats** — ordinary conversations.
- **🗝 Private** — private chats. Only visible when there are private chats to show; while the vault is sealed, private chats are hidden entirely (server-side) and the tab disappears.
- **🎲 Game Room** — game sessions and story playthroughs (badged 🎲 game or 📖 story). These chats don't appear in the sidebar picker at all — the Manager is the one surface that lists everything.
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

Trim keeps the first *A* and last *B* turns and deletes everything between. A turn is your message plus everything until your next one — tool chains never split. The modal shows a live preview: how many messages would go, roughly how many tokens that frees, how many remain. You must keep at least the last turn — deleting the recent end is message surgery, which belongs in the chat view itself (hover a message for its ✕). If the chat has fewer turns than you asked to keep, trim is a no-op and says so.

### Compress — summarize old history

Compress rewrites older history as AI-written summaries while the recent tail stays verbatim. Options in the modal:

- **One summary** — everything except the kept tail becomes a single summary pair.
- **Timeline chunks** — history is split into spans of a few thousand tokens; each span becomes one summary pair in place, so the chronological chain survives.
- **Model** — pick the provider/model that writes the summaries. Private chats only offer local providers.
- **Target size** — the approximate total size of the compressed output.
- **Keep last N turns verbatim** — the untouched tail. The server caps this below half the LLM history window; a bigger tail would push the summary out of what the model ever sees.
- **Back up full JSON first** — writes an export to `user/history/exports/` before touching anything. Forced off for private chats (nothing readable leaves the database).

The job runs in the background — minutes on a local model — one at a time; the Manager's summary line shows progress and a toast lands when it finishes. It's write-last: the chat isn't touched until every summarize call has succeeded, so a failure or a server restart mid-job leaves the chat exactly as it was.

### Archiving

Archive is a shade, not a freeze. An archived chat vanishes from the sidebar dropdown, but scheduled tasks, daemons, and heartbeats can still reach it, the Manager always lists it, and clicking its name still opens it. Fully reversible. Use it to unclog the picker without deleting anything.

### Degraded chats and repair

If some of a chat's stored messages can't be read at load time — corrupt rows, or encrypted rows whose key is gone — the chat loads **degraded**: everything readable displays, but the chat is read-only until repaired. New turns won't save (you get a warning toast), which protects the unreadable-but-recoverable rows from being clobbered. Degraded rows carry a **⚠ degraded** badge in the Manager.

The **🔧 Repair** action diagnoses first: how many rows are healthy, corrupt, undecryptable, plus any sequence gaps. Running the repair moves unreadable rows to a quarantine — kept verbatim, recoverable if a matching vault backup returns — renumbers the survivors, and the chat can save again.

One guard: if *most* of a chat can't be decrypted, that usually means the vault changed, and the right fix is restoring the matching vault backup — which recovers everything — not repair, which would quarantine most of the chat. The dialog warns you and asks for explicit confirmation in that case. Clearing a degraded chat is also allowed — that's the "just discard it" escape hatch, and it lifts the read-only latch too.

## Settings

There is no Chats section on the Settings page — per-chat settings *are* the sidebar, and each chat stores its own. Related knobs elsewhere:

- **Settings → LLM** — providers, default models, and the LLM history window (which also caps Compress's keep-last-turns).
- **Prompts, Toolsets, Spices pages** — the ↗ buttons beside sidebar dropdowns jump straight there.
- **Vault** — configured via the sidebar padlock; see [PRIVACY.md](PRIVACY.md).

## Quick Troubleshooting

- **Send refused: "still replying in this chat"** → a turn is live in this chat (maybe another tab) → wait, or press Stop, then send again.
- **Can't switch chats** → a reply is generating → Stop first; switching is blocked mid-generation.
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

Chats are the unit of configuration: each chat stores prompt, toolset, spice_set, voice/pitch/speed, llm_primary/llm_model, memory scopes, RAG docs + rag_context, custom_context, ghost_context, inject_datetime, spice_enabled/spice_turns. Switching chats applies the whole loadout.

CHAT MANAGER:
- Access: nav rail Chat → flyout → 🗂️ Manage, or sidebar chat picker → See more
- Frontend: views/chat-manage.js; routes: core/routes/chat.py
- Tabs: 💬 Chats / 🗝 Private / 🎲 Game Room / 📚 Librarian / 📦 Archive; one chat one tab; archived trumps all; Private tab hidden when empty or vault sealed
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
- Background job, one at a time (409 if busy); modes: whole (one summary pair) | chunked (one pair per ~6k-token span)
- target_tokens ≈ output size; keep_last_turns must be < half LLM_MAX_HISTORY; optional backup JSON to user/history/exports/ (forced off for private chats); private chats need a local provider
- Write-last: chat untouched until all LLM calls succeed; restart mid-job = chat untouched
- Status: GET /api/chats/compress/status (UI polls)

DEGRADED / REPAIR (POST /api/chats/{name}/repair):
- Unreadable rows skipped at load latch the chat read-only-degraded; saves refused until repaired; clear also unlatches (discard escape hatch)
- Repair preview classifies healthy/corrupt/undecryptable/sequence gaps; real run quarantines bad rows verbatim (recoverable via matching vault backup), re-sequences, unlatches
- Vault-mismatch guard: majority undecryptable → advises restoring vault backup; force:true to override

ONE TURN PER CHAT:
- POST /api/chat/stream is exclusive per chat; second send while live → 409 {"error": "...press Stop"}; other tabs mirror Send→Stop; cancel via POST /api/cancel

OTHER:
- Archive = UI shade not freeze: hidden from sidebar picker, daemons/cron still reach it, Manager lists it, reversible (POST /api/chats/{name}/archive)
- Clear wipes messages + tool images, keeps chat + settings (bulk-clear or sidebar ✕)
- Sidebar Import replaces current chat's messages (POST /api/history/import, accepts raw array or {messages}); sidebar Export = current chat raw messages
- Game/story chats never appear in the sidebar picker; Chat Manager lists everything
- New chat names sanitized server-side (lowercase, underscores); create echoes sanitized name
- Sidebar = chat settings, composer mic flyout = device settings, Settings page = global
