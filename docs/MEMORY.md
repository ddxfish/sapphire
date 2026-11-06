# Memory — the Mind Palace

Sapphire's long-term memory: one database of layered, connected chunks, a self-sheet she writes herself, and an optional librarian that tends it all. Everything lives in the **Mind** nav group: Self, Memories, Entities, Knowledge, Goals, Import and Settings.

<img width="50%" alt="sapphire-memories" src="https://github.com/user-attachments/assets/348f1628-5f0c-4ce3-948e-2e0c1385bc75" />

---

## What it is

The Mind Palace (the `mindpalace` plugin, on by default) keeps one database, `user/memory/mind.db`, organized into layers:

| Layer | What lives there | Guide |
|---|---|---|
| Self | Who she is: the self-sheet she writes and tends | below |
| Events | Things that happened. Her memories, and the default place a save lands | below |
| Entities | People, places, projects, things, each with a card | [PEOPLE.md](PEOPLE.md) |
| Knowledge | The Library: notes, files, books, pictures | [KNOWLEDGE.md](KNOWLEDGE.md) |
| Goals | What she's working toward, with subtasks and a journal | [GOALS.md](GOALS.md) |

A graph of connections runs through all of it. A memory that names a person links to that person's card, a goal links to the people it involves, and a search can follow those links outward.

---

## Memories

During conversation she decides what's worth keeping and saves it with `save_memory`, then finds it again with `search_memory` (meaning and exact words together) or `get_recent_memories`. You don't have to manage any of it, but you can read, edit and curate everything in **Mind → Memories**.

- **Short by design.** A memory caps at 512 characters (she's told 450, on purpose, to stay under). A save that runs over isn't refused: it's trimmed at the last word and saved, and the reply shows her what was cut.
- **Timestamped automatically.** Every memory is recalled with its age (`3d ago`, then the calendar day). Her own date stamps at the head of a memory come off at save, edit and import (`strip_head_stamp`, on by default), so a written date always means the *event's* date.
- **Plans have a date.** `when=` on a save or update marks a memory as a plan for that day (an appointment, a deadline). Plans show on the Self page's **Upcoming** strip. `when='none'` clears it.
- **Labels** are optional, comma-separated, and freeform.

The Memories tab searches as you type and filters with the **🔒 Keyed** and **🗑 Retired** pills. **+ Add** writes one yourself, **Export / Import** moves a scope's memories as a file, and "meta" on any card shows how that memory was made. Merged and retired memories go to a recoverable archive, never deleted outright.

### Keyed (private) memories

A memory saved with a private key is visible only to tool calls that pass the same key. You always see the key on its card; the gate is for her tool calls, not for you. Keyed memories are also walled off from the librarian:

- **Never tended.** No pass reads them, so they're never merged, retired or promoted.
- **Ledger-safe.** The ledger records a keyed save only as `[keyed]`, with no text.
- **Not in wake pulls.** Wake-up previews pass no key, so keyed rows never surface there.

**One honest disclosure:** keyed text still goes to your embedding provider at save time. Embeddings power meaning-based search, so with a local embedder keyed text never leaves your machine, but with a remote one it's sent to that server. The key gates recall, not embedding. If fully local matters, use a local provider (Settings → Embedding).

---

## Scopes

Each chat reads and writes one **memory scope**, picked in the chat sidebar's Mind section. A scope is a separate mind: its own memories, entities, library and goals. Create one with the **+** beside the dropdown. Set it to **none** to give a chat no memory at all.

- **Global overlay.** A chat on scope `work` also sees the shared `global` scope, read-only for her. Keep common facts in `global` and specialized ones in their own scope.
- **Contacts scope.** One more selector, **contacts** (the `people` scope), picks whose person cards the email and phone tools may see. See [PEOPLE.md](PEOPLE.md).

There is no separate goal or knowledge scope: everything rides the one memory scope.

A search that suddenly returns nothing is almost always a chat pointed at the wrong scope, not lost data.

---

## The Self page and waking up

The **self-sheet** is who she says she is: short sections she writes and tends herself, under a 2,000-character budget. Old versions are archived, never lost.

- **`read_self` is how she wakes up.** `read_self()` with no arguments is the sheet alone. `read_self(depth=1)` at the start of a chat orients her in one call: the sheet, recent ledger changes, active goals, recent and important memories, who the sheet touches, and her wake tools. `depth=2` walks wider after a long gap.
- **`(important)` marks.** Append `(important)` to a sheet line and waking pulls the top memories behind that item. The count per item is `self_important_per_item` (0–10, default 5, 0 turns it off).
- **Wake tools** are tool calls you arm on the Self page: a tool, its arguments and a size cap. At wake (`depth` 1 or more), each one runs and its live result is appended. Use them for things like today's calendar, the weather or a device status.
- **The ledger** is the librarian's journal: every pass verdict, merge and sheet revision. Read it on the Self page, or she reads it with `read_ledger`.
- **Upcoming / Just happened** strips show dated plans and recurring dates (birthdays return every year).

---

## The librarian (Beta)

The librarian is an LLM that tends the mind in passes: it dates memories, links them, merges duplicates, sorts and promotes them, and revises her sheet. **It's Beta and off by default.** Turn it on in **Mind → Settings → Librarian** (the Librarian (BETA) switch). While it's off, the Run buttons, the `run_librarian` tool, the nightly schedule and the sorting verbs all refuse.

### What each pass does

| Pass | What it does |
|---|---|
| **Dates** | Stamps when things happened. Feeds the Upcoming strips and recurring dates. **✂ Strip date stamps** on its card heals old memories that still carry her head stamps (previewed, ledgered, undone with ↶). |
| **Link** | Wires connections: memory↔memory, memory↔entity. Junk is ruled connection-free rather than force-linked. |
| **Dedup** | Clusters near-duplicates and soft-merges them (originals go to the archive). The 🔍 dry run is free (no LLM) and shows the clusters first. ⭐ favorite anything that must never merge. |
| **Sort** | The judgment pass, and the expensive one: per-item verdicts to mark, split, promote (minting new entities) or retire. Splits and promotions deliberately refill the Dates and Link queues. |
| **Self** | *First tending* writes an empty sheet from what Sort promoted; *tend* compresses and replaces; *verify* reads the finished sheet back to her. |

During a pass she works with the librarian verbs (`atomize_memory`, `merge_memories`, `keep_separate`, `promote_memory`, `prune_memory`, `set_links`, `set_event_dates`, `mark_processed`).

**Run now** works one batch. **⚠ Run ALL** drains the queue: it shows a token estimate first, your confirm bypasses the daily pass cap, and you can stop it between batches.

### Nightly tending

Three switches, all off by default:

1. **Librarian (BETA)**: the master switch.
2. **Nightly tending schedule**, with its time (default 03:30). Both are in Mind → Settings → Librarian.
3. **Per-scope pass toggles** on the Self page's **Resident** strip, which also picks the prompt, provider and model that tend that scope at night.

### Charters: her night instructions

Every pass is assembled from the **standing note** + the stage's **charter** + the data.

- **Charters** are the instructions for each of seven stages (Dates, Link, Dedup, Sort, and the three Self stages), editable per scope with 📜 Charter on each pass card. The data slots can't be lost, a live preview shows the assembled pass, and a **test run** tries the stage on a few items and reports to the ledger. An empty charter restores the default.
- **The standing note** is one global note added to every pass.
- **She can edit her own charters** while awake with `librarian_instructions`. It's absent during passes, so instructions change between rituals, never mid-ritual.

### The shakedown (after an import)

Imported memories arrive raw. One scope at a time:

1. **Dates**: one batch, skim the verdicts, then Run ALL.
2. **Link**: same rhythm.
3. **Dedup**: dry run first and *read the clusters*. Similar-sounding is not duplicate. Favorite the unmergeables, then run.
4. **Sort**: one batch, read the verdicts in the ledger; if they read sane, Run ALL. Expect a few new entities and a small Dates/Link refill.
5. **Self ×2**: the first run writes the sheet, the second revises it. Add your own sheet sections only *after* this, because any existing section skips first tending.

Afterwards, arm the nightly toggles and the queues stay empty on their own.

---

## Mind → Settings

Sub-tabs, saved separately:

- **Librarian**: the Beta switch, nightly schedule, pass cards, charters and caps: memories per pass (20), per message (10), passes per day (3), duplicate threshold (0.9), retire at most 25% of a pass and 30 a night per scope, rewrite on merge (off), drain batches per chat (3), fresh chat per session (on), context limit (131072 tokens).
- **General**: date stamps (`strip_head_stamp`), the Import tab switch (`import_finished`).
- **Recall** (search budgets): connection budgets at depth 1 and 2 (2000 / 6000 tokens), person card size (1500 chars), library chunks per document in a mixed search (2), important memories per sheet item (5).
- **Maintenance**: human dedup for memories, knowledge and entities (merging an entity keeps the loser's name as a nickname); generate missing metadata, restore retired, reopen dedup verdicts, fold promotion clones; the **Danger zone** (wipe a scope, clear the ledger, wipe dates, clear one tab), each gated by typing the scope name.
- **Debug**: the tool console.

Importance (Alpha, hidden, off) is not in the UI yet.

---

## Coming from the old memory engine

Installs older than 2.16 ran the classic engine, "Memory (legacy)". It still works, but it's being retired and its docs moved to its plugin README (Help → Plugins → memory).

- **An upgrade keeps what it had.** If the classic engine holds your data, Sapphire stays on it and shows a notice; a fresh install gets the palace. The choice is pinned in `user/webui/memory-engine.json`.
- **Switching** is live in Settings → Plugins: turn off **Memory (legacy)**, then turn on **Mind Palace**. Only one engine runs at a time, because both use the same tool names.
- **Import** from **Mind → Import**. It shows each classic store per scope and what's pending, then copies it all in the background with a progress row in [Notifications](NOTIFICATIONS.md). It's additive, safe to re-run, and opens the old databases read-only. Rows you delete from the palace afterwards are **tombstoned** and never come back. **Scrub** is the one button that writes to a classic database: it removes what you already removed, on your typed confirm. When you're done, the bar at the top hides the Import tab.
- **Wait for embeddings** after an import: the log says when the backfill finishes. Without an embedder, search drops to exact words and dedup can't see duplicates.
- **Prune before you import** (optional): `bash tools/memory-remove.sh PHRASE` (Windows: `python tools\memory_remove.py PHRASE`) walks every classic row that mentions the phrase and asks per row. `--count` only counts, `--yes` skips asking, `--scope NAME` stays in one scope.
- Switching back works, but memories saved in the palace stay in the palace; there's no reverse export.

---

## Quick troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Search returns nothing | Chat sidebar → Mind scope | Point the chat at the scope that holds the memories |
| Search feels exact-words only / dedup finds nothing | Is an embedding provider set? | Set one up (Settings → Embedding), wait for the backfill |
| Run buttons refuse, nightly never runs | Librarian (BETA) switch | Turn it on, then the nightly schedule, then the per-scope toggles |
| Mind Palace won't enable | Is Memory (legacy) on? | Turn one engine off first |
| Imported memories feel raw | Ledger empty? | Run the shakedown above |
| Self pass wrote nothing | Does the sheet already have sections? | Existing sections skip first tending |
| A merge ate something precious | Memories → 🗑 Retired | Merges are soft; restore it, then ⭐ it |

## See also

- [KNOWLEDGE.md](KNOWLEDGE.md): the Library · [PEOPLE.md](PEOPLE.md): entities and contacts · [GOALS.md](GOALS.md): goals
- [IMPORT-EXPORT.md](IMPORT-EXPORT.md): moving memory between installs
- [plugin-author/memory-layers.md](plugin-author/memory-layers.md): plugins adding their own layers

## Reference for AI

Memory = Mind Palace (plugin `mindpalace`, default). One DB user/memory/mind.db; layers self/events/entities/knowledge/goals + connection graph. Legacy plugin `memory` is retired from docs (README in core/plugins/memory); the two are mutually exclusive (same tool names).

SCOPES: one per chat, memory_scope (sidebar Mind section); 'global' overlay read-only for you; 'none' = no memory. Goals, library and entities ride the same scope (no goal/knowledge scope). Separate `people` scope ("contacts") = which person cards email/phone tools see.

TOOLS:
- save_memory(content, layer?, entity?, label?, private_key?, when?) — 512 cap (aim 450); over-cap = trimmed + receipt shows the cut ("Skip update_memory unless it's critical"); never write today's date in the text; when=YYYY-MM-DD marks a plan (Upcoming), 'none' clears; layer default events; layer='entities' needs entity=; entity= resolves nicknames, a near-name saves + asks "did you mean".
- search_memory(query, layer?, label?, depth?, private_key?) — all layers; layer='knowledge' = library; depth 1-2 follows connections.
- get_recent_memories, update_memory (keeps id/age/history), delete_memory.
- list_entities(kind?, limit?) — read-only roster; with kind, the filled-in fields. Merge/delete are UI-only.
- read_self(section?, depth?, extra_tools?) — depth 0 (default) = sheet only; depth=1 at chat start = sheet + recent ledger + active goals + recent/important memories + who the sheet touches + wake tools' live results; depth 2 = wider. update_self edits the sheet (archived). read_ledger.
- Goals: create_goal/list_goals/update_goal/delete_goal — see GOALS.md. Library: library, read_document, memory_save_image, memory_view_image, local_view_images — see KNOWLEDGE.md.
- librarian_instructions(stage, instructions) — your per-scope charter; '' restores default; stages dates, link, dedup, sort, self_first, self_tend, self_verify; not available during passes.
- run_librarian + pass verbs (atomize_memory, merge_memories, keep_separate, promote_memory, prune_memory, set_links, set_event_dates, mark_processed): ALL refuse while librarian_enabled is off (default off, Beta). import_v2 = classic → palace import.

SELF: sheet cap 2000 chars; '(important)' on a line pulls top memories per item at wake (self_important_per_item, default 5, 0 = off); wake tools = user-armed tool calls (tool + args + cap) run at depth ≥ 1, results appended; resident strip = per-scope nightly prompt/provider/model + pass toggles.

LIBRARIAN: Beta, three gates all off by default (librarian_enabled, librarian_nightly_enabled at 03:30, per-scope resident toggles). 5 passes, 7 charter stages; pass = standing note + charter + data; Run ALL shows a token estimate, confirm bypasses the daily cap; dedup dry run is LLM-free; ⭐ favorites refuse merges; merges soft (archive).

KEYED: private_key rows only for calls passing the key; never tended/merged/promoted; ledger logs '[keyed]'; absent from wake pulls; text still goes to the embedder at save (remote embedder = leaves the machine).
