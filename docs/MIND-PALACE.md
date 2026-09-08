# Mind Palace — the layered v3 memory engine

Sapphire's opt-in next-generation memory: one database of layered, connected chunks with a librarian that tends it while you sleep. Ships disabled; the classic memory system stays the default.

## What it is

Sapphire ships two memory engines. **Classic memory** (the `memory` plugin) is the default — proven, simple, and what most installs should stay on. **Mind Palace** (the `mindpalace` plugin, "Mind" in the UI) replaces it with a layered design: one database of chunks and metadata organized into layers — events (memories), self, entities, knowledge (the Library), and goals — plus a graph of connections between them and a **librarian**: LLM passes that date, link, deduplicate, sort, and tend what accumulates.

**The two engines are mutually exclusive by design.** They register the same tool names (`save_memory`, `search_memory`, ...), so enabling the second refuses to load with an error toast. You run one or the other, never both.

## Using it

### The swap

1. Settings → Plugins → disable **Memory**, enable **Mind Palace**, restart Sapphire.
2. The nav rail's **Mind** group now opens the palace views. Your classic databases are untouched on disk — the palace only ever opens them read-only.
3. **Scopes keep their names across engines.** If your chats ran scope `home` before, point them at `home` after the swap (chat sidebar → Mind section). A search that suddenly returns nothing is almost always a chat pointed at the wrong scope, not lost data.
4. **Switching back** is the same toggle in reverse — classic memory resumes exactly where it left off. ⚠ One-way caveat: memories saved while the palace was active live in its own database and are invisible to classic memory (there is no reverse export yet). Trial accordingly.

### Importing your classic memories

Per scope, from **Mind → Admin** (viewing the target scope):

1. **Import & Migration → Import from Memory v1.** The import is additive, idempotent (re-runs skip what's already there), and strictly read-only toward the old databases. Watch for the summary line.
2. **Wait for embeddings** before anything else — the log reports when the backfill completes. No embedder configured means search degrades to keyword-only and dedup can't see duplicates; fix the embedder first.
3. Knowledge and documents ride a separate one-time Library migration (Admin → "Copy knowledge...") plus per-scope export/import zips on the Knowledge tab. Watch-folder definitions come across as entries you re-add by hand — a zip never auto-scans your disk.

### The Mind views

- **Memories** — the event layer: chunks with importance, dates, and connections. Merged and retired memories move to a recoverable archive, never deleted outright.
- **Self** — her self-sheet and wake configuration (below).
- **Entities** — people, places, projects, things. Each has a card: kind, facts, nicknames, connections, mention counts, recurring dates (birthdays show with ↻ and return every year). Kinds come with templates; drop your own JSON kind templates in `user/mind_palace/templates/` and they win over the built-ins.
- **Knowledge** — the Library: documents, annotations, search.
- **Goals** — goals live as first-class chunks in the same graph; permanence rides importance rather than a separate database.
- **Admin** — the librarian's cards, import tools, and the danger zone.

### The Self page and waking up

The **self-sheet** is who she says she is — short sections she writes and tends herself, under a fixed character budget. Edits are archived, never lost.

- **`read_self` is the wake-up call.** It serves the sheet, her wake tools, and recent ledger lines; a `depth` argument pulls more history. Chats and rituals typically open with it.
- **`(important)` marks** — append `(important)` to any sheet line and waking pulls the top memories behind that item along with the sheet. The per-item count is the `self_important_per_item` setting (0–10; 0 turns the feature off).
- **Wake tools** — structured boxes edited on the Self page: standing notes and instructions she receives every time she wakes, separate from the sheet itself.
- **The ledger** is the librarian's journal — every pass verdict, every merge, every sheet revision. Readable from the Admin/Self surfaces or via `read_ledger`.
- **Resident strip** — per scope: which prompt/provider/model tends this mind at night, and per-pass nightly toggles (default **off**).

### The librarian — what each pass does

Five passes, run per scope from the Admin cards. Each works a queue of untended chunks in batches:

- **Dates** — stamps when things happened. Powers the Self page's *Upcoming / Just happened* strips and recurring-date reminders.
- **Link** — wires connections: memory↔memory, memory↔entity. Junk gets ruled connection-free rather than force-linked.
- **Dedup** — clusters near-duplicates and soft-merges them (originals retire to the archive, recoverable). The 🔍 dry-run is free — no LLM — and shows the clusters first. ⭐ favorite anything that must never merge; favorites refuse merges in code and are excluded from clusters entirely.
- **Sort** — the judgment pass, and the expensive one: per-item verdicts to mark, split, promote (minting new entities), or retire. Splits and promotions deliberately re-enter the dates/link queues; the nightly grazes them back down.
- **Self** — three stages: *first tending* writes an empty sheet from everything sort promoted; *tend* is the sheet hour (compress and replace, not append); *verify* reads the finished sheet back to her for a final pass.

During a pass she works with librarian verbs (`atomize_memory`, `merge_memories`, `promote_memory`, `prune_memory`, `set_links`, `set_event_dates`, `mark_processed`), and `run_librarian` lets her start a pass herself when she's awake.

**Run now** works one batch. **⚠ Run ALL** drains the queue — it shows a token estimate before you commit, your confirmation bypasses the daily pass cap, and it can be stopped between batches. Every verdict lands in the ledger.

### Charters — her night instructions

Every pass she runs is assembled from three parts: the **standing note** + the stage's **charter** + the data payload.

- **Charters** are the instruction prose for each of seven stages — Dates, Link, Dedup, Sort, and the three Self stages — editable **per scope**. The ⚙ gear on each Admin card opens the charter editor: edit the text freely; data slots are guaranteed (a charter can't lose its payload), a live preview shows the assembled result, and a **test run** executes the stage on a handful of items cap-free and reports to the ledger so you can judge the new charter before trusting it with a full night. An empty charter restores the shipped default.
- **The standing note** is one global note injected into every pass, whatever the stage — the place for "always sign the ledger"-grade instructions.
- **She can edit her own charters awake** via the `librarian_instructions` tool — deliberately absent from the pass toolsets, so instructions change between rituals, never mid-ritual.

### The shakedown (recommended after import)

Imported memories arrive raw. The proven order, one scope at a time:

1. **Dates** — one batch, skim the verdicts, then Run ALL.
2. **Link** — same rhythm.
3. **Dedup** — dry-run first and *read the clusters*: similar-sounding is not duplicate. Favorite the unmergeables, then run.
4. **Sort** — one batch, read the verbs and retirement reasons in the ledger; if they read sane, Run ALL. Expect a few new entities (categorize them afterwards) and a small dates/link queue refill — that's by design.
5. **Self ×2** — run the Self pass twice: first tending writes the sheet, the second run revises it with the finished sheet behind her eyes. Add custom sheet sections only *after* these runs — any existing section suppresses the first-tending flow.

Dates/link/dedup are cheap; sort is the heavy one. A ~500-memory scope drains end-to-end in well under an hour on a mid-size model. Afterwards, arm the nightly toggles on the resident strip and the queues stay at zero on their own.

### Keyed (private) memories

Memories saved under a private key are **never tended**: the librarian excludes them from every pass, they are never promoted or merged, wake previews skip them, and the ledger logs them only as `[keyed]`. One embedding caveat applies at save time — see the keyed section in [MEMORY.md](MEMORY.md).

### Reset

Wipes and layer clears live in **Admin → Danger**, each gated by typing the scope name. Palace deletions never touch the classic databases — a bad experiment is always recoverable by wiping the palace scope and re-importing.

## Settings

| Setting | Where | What it does |
|---|---|---|
| Memory scope (per chat) | Chat sidebar → Mind | Which mind this chat reads and writes |
| `librarian_model` | Settings → Plugins → Mind Palace | Default model for tending when the resident strip doesn't override it |
| Resident (per scope) | Mind → Self → resident strip | Prompt/provider/model for nightly tending + per-pass nightly toggles (default off) |
| `self_important_per_item` | Settings → Plugins → Mind Palace | Memories pulled per `(important)` sheet item at wake (0–10, 0 = off) |
| Charters + standing note | Admin cards → ⚙ | Per-scope pass instructions (see Charters) |

## Quick Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Search returns nothing after the swap | Chat sidebar → Mind scope | Point the chat at the same scope name it used before |
| Dedup finds no duplicates / search feels keyword-only | Is an embedder configured? | Set one up, wait for the backfill, re-run |
| Palace won't enable | Is classic Memory still enabled? | Disable one engine — they refuse to run together |
| Imported memories feel raw and disconnected | Ledger empty? | Run the shakedown order above |
| Self pass wrote nothing | Does the sheet already have custom sections? | Existing sections suppress first tending — run the Self pass before adding your own sections |
| Nightly tending never runs | Resident strip toggles | They default off — arm the passes you want per scope |
| A merge ate something precious | Memories view → archive | Merges are soft; originals are recoverable. Favorite it after restoring |

## See also

- [MEMORY.md](MEMORY.md) — classic memory, and keyed-memory privacy shared by both engines
- [KNOWLEDGE.md](KNOWLEDGE.md) / [GOALS.md](GOALS.md) / [PEOPLE.md](PEOPLE.md) — the classic surfaces the palace replaces
- [plugin-author/memory-layers.md](plugin-author/memory-layers.md) — plugins adding their own layers to the palace

## Reference for AI

Mind Palace = opt-in v3 memory engine (plugin `mindpalace`); mutually exclusive with plugin `memory` (same tool names; second engine refuses to load). Data: one palace DB per install under user/memory/; layers: events/self/entities/knowledge/goals; connection graph. Classic DBs opened read-only; palace-era rows invisible to classic (no reverse export). Scopes keep names across engines; per-chat scope in sidebar Mind section.

TOOLS (palace surface):
- save_memory / search_memory / get_recent_memories / update_memory / delete_memory — over-cap saves TRIM at last whitespace + return receipt with dropped text (see MEMORY.md); entity= resolves nicknames to the card, a partial overlap saves + asks "did you mean"
- list_entities(kind?, limit?) — read-only roster (name/kind/nicknames/fact count; with a kind, that kind's filled-in fields). Merge/delete stay UI-only.
- read_self(section?, depth?) = wake-up call: sheet + wake tools + recent ledger; update_self edits sheet (edits archived); read_ledger
- librarian_instructions(stage, instructions) — edit own per-scope charter awake; '' restores default; stages: dates, link, dedup, sort, self_first, self_tend, self_verify; NOT available during passes (instructions change between rituals, never mid-ritual)
- Pass-time verbs (librarian toolset): atomize_memory, merge_memories, promote_memory, prune_memory, set_links, set_event_dates, mark_processed; run_librarian starts a pass
- Goals: create_goal / list_goals / update_goal / delete_goal (goals = graph chunks; permanence via importance)
- Library: library / read_document / view_image; import: import_v2

SELF: sheet cap SELF_MAX_CHARS 2000; '(important)' suffix on a sheet line pulls top memories per item at wake (self_important_per_item 0-10, default 5, 0=off); wake tools = structured standing boxes served at wake; resident strip = per-scope nightly prompt/provider/model + per-pass toggles (default OFF).

LIBRARIAN: 5 passes (dates/link/dedup/sort/self), 7 charter stages; every pass = standing note + charter + guaranteed DATA slots; ⚙ charter editor has live preview + cap-free ~5-item test run reporting to ledger; Run ALL = drain with token estimate + confirm (bypasses daily cap); dedup dry-run is LLM-free; ⭐ favorites refuse merges; merges soft (archive, recoverable); sort refills dates/link queues by design.

KEYED: private_key rows never tended/promoted/merged; excluded from wake previews; ledger logs '[keyed]' only; embed-time disclosure in MEMORY.md.

ENTITIES: kinds + templates (user templates user/mind_palace/templates/*.json override built-ins); cards carry facts/nicknames/connections/mentions/recurring dates (feed Upcoming strips).
