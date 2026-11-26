# Mind Palace

Layered memory supersystem (memory v3). One database (`user/memory/mind.db`),
one uniform unit everywhere — **chunks + metadata** — organized into layers on
a rail:

| # | Layer | What lives there |
|---|-------|------------------|
| 0 | `self` | Who she is: the self sheet (typed sections + custom boxes) (free self-notes are retired: a save to `self` lands in memories) |
| 1 | `events` | Things that happened. Default write target; the librarian's raw material |
| 2 | `entities` | People / places / things / events-as-nouns, with tiered chunks (1 = headline, 2 = facts, 3 = trivia) and per-kind template fields |
| 3 | `knowledge` | Big reference data, sub-chunked |
| 4 | `goals` | What she's working toward: goals + subtasks + progress journal, woven into the graph |
| 5 | `daily` | One summary per finished day, recorded after a daily compact and at every start, or handed over a day at a time by `/extract-daily` (`tools/daily.py`, the keeper functions; which days a chat has given is that chat's `plugin_chat_data`). Read-only for her; never tended by the librarian; out of the spider's frontier (`retells` edges lead from a day to its entities only; the person corrects them on the card, `POST days/tags`) |

Mutually exclusive with the classic Memory plugin (same tool names — enable
one, not both). `import_v2` copies the old stores in; the old DBs are opened
read-only — switching back is always possible. The one write to them is the
Import tab's Scrub button (rows you already removed from the palace, on your
typed confirm); nothing automatic, nothing of hers, ever touches them.

Tools: `save_memory`, `search_memory`, `get_recent_memories`, `update_memory`,
`delete_memory`, `list_entities`, `read_self`, `update_self`, `read_ledger`,
`librarian_instructions`, `create_goal`,
`list_goals`, `update_goal`, `delete_goal`, `library`, `read_document`,
`memory_view_image`, `local_view_images`, `memory_save_image`, `import_v2`, `run_librarian`, plus the librarian verbs below.

## Goals (L4)

Same four tools as the classic goals system, but goals are **chunks in the
graph**: `search_memory` finds them, naming a person auto-edges the goal to
their entity, and the spider can walk to them (subtask/journal hops are
priced as metadata). One goal chunk per goal (content = title; description,
priority, status in meta); subtasks and journal notes are their own chunks
with `subtask_of` / `progress_of` edges. **Permanent goals carry importance
0.95** — the never-fades band: the AI can only add notes to them, never
complete, retitle, or delete (the UI keeps full control, with a force
confirm). Goals use the one palace memory scope — the classic separate
goal-scope key retired with the classic plugin. `import_v2(what='goals')`
copies the classic goals.db in (subtask nesting, journals, permanence all
preserved; old DB untouched as always).

## Edges & connections (how the graph grows)

Every chunk carries a `meta` JSON bag (mechanical metadata stamped at save:
stats, temporal refs, noun candidates, provenance like chat/persona/model).
On top of that, **edges** — rows in the `edges` table — connect the graph:

| Edge | Created by | Meaning |
|------|-----------|---------|
| chunk →(mentions)→ entity | Save-time matcher, backfill, the link pass (`set_links`) | This memory talks about that person/place/thing |
| chunk →(derived_from)→ chunk | `atomize_memory`, `promote_memory` | Provenance: this chunk was distilled from that one |
| chunk ↔ its own entity | Structural (the `entity_id` column) | An entity's own tiered facts |

**How mention edges appear:** at save time the content is matched against all
entity names in scope — **and their nicknames** (the comma-separated
`nicknames` field on person entities). Saving "talked to the boss about the
budget" auto-edges to Sam if "the boss" is one of Sam's nicknames. Matching is
case-insensitive, word-boundary, longest-name-wins; real names always beat
another entity's nickname on collision.

**How the spider walks them** (`search_memory(depth=)` /
`read_self(depth=)`): Dijkstra from the search hits (or the self sheet) with
a budget of `2.0 × depth`. Structural hops are cheap (entity → its own facts
costs the tier number); metadata hops are expensive (mention edges cost 2.0
each way) — so the walk travels far along an entity's own material but
exhausts quickly jumping sideways. Depth 1 ≈ reach the people a memory
mentions; depth 2 ≈ their neighborhood too. Depth is capped at 2 everywhere —
on a well-woven graph, 3 walks most of the mind (3+ silently clamps to 2).

**Edge pricing** (v2, 2026-07-11): every hop's cost is
`base × f(importance) × g(hub degree)`, with a temporal socket reserved.

- `f(importance)` — important memories cost *less* budget to reach (the core
  band, 0.9+, travels at 0.3×; unrated is neutral; junk-rated costs extra), so
  the walk spends itself on what matters and runs dry on noise. Importance is
  written by the librarian (`mark_processed` rating) and by favoriting.
- `g(degree)` — fanning out of a mention *hub* (an entity with many mention
  edges) costs more per edge, capped at 2×. The calibration is deliberate: a
  big hub's unrated fan is affordable only when the hub itself is the search
  epicenter (searching a hub by name still returns its neighborhood, capped);
  one hop in — e.g. the wake path, self sheet → person → fan — the unrated
  fan exceeds the budget and the walk stops at the entity's own facts. Rated
  memories are discounted under the wire, so what penetrates a hub from a
  distance is exactly what she's rated as mattering.
- Ties broken by importance, then recency — never storage order.
The connected block is token-budgeted **per depth** via Mind → Settings →
Recall (defaults: ~2K tokens of connections at depth 1, ~6K at depth 2 —
roughly 4K / 8K total returns), so depth never floods the context. No
duplicates by construction: the walk excludes its own seeds, so nothing in
the connected block repeats the direct results or the self sheet.

Mention edges also bump the entity's **mentions counter** — the 🔔 number on
entity cards. That counter means "mentions since the librarian's last SORT
pass": it is the sort pass's dirty flag, and only the sort pass resets it.

## Self sheet — structured sections

Some self sections are **structured lists** (relationships, handles, values,
projects, terms & concepts): canonical storage is still plain text (one row per line, fields
joined by " — ", or ": " for handles) so search, embeddings, entity linking
and the spider are untouched — but the parsed rows also live in the chunk's
meta, and the Mind → Self UI edits them as rows with per-column inputs and
"+ add". A relationships row that names a person auto-edges to their entity,
so the sheet spiders straight to the people on it. "+ Add box" can create a
**custom structured list** too — name it, pick 1–3 columns (e.g. Game /
Score) — and Sapphire edits the same box with plain `update_self` text lines.
Layout: identity spans the full row; everything else sits two-up (stacks on
mobile). Max 2 columns by design.

**Person cards** (2026-07-21): a person entity's description plus its
descriptive template fields (Background / Interests / Voice & humor / Likes /
Dislikes — textarea fields on the person kind) render to the AI at exactly
two seams: searching the person by name (or nickname) appends their card,
and `read_self`'s important-people groups arrive card-first — so the people
on her relationships sheet reach her at wake without a search. Flat per-card
cap (`people_card_chars`, default 1500); fields are otherwise UI/contacts
only.

## Master toggles (Librarian & Importance)

Two masters, **both off by default** — the core system
(save/search/spider/entities/self/goals) is designed to run without them,
with no lurking variables:

- **Librarian (BETA)** — Mind → Settings → Librarian. Gates every pass: the
  Run buttons, `run_librarian`, the nightly round. Off = the sorting verbs
  refuse (blast shield stays shut). Beta since 2026-11-02: months of nightly
  use on two installs; a new model may judge differently, so watch the
  Ledger for the first nights.
- **Importance (ALPHA, hidden)** — `importance_enabled`; no UI door until the
  decay has been exercised (nobody has tested it, 2026-11-02; the manifest
  marks the three fields `experimental`). Gates the whole salience system as one unit:
  the `mark_processed` rating parameter, spider pricing `f(importance)` and
  its ordering tiebreak, nightly decay, and recall boost. Off = flat, neutral
  pricing everywhere (favorites included). The `importance` column keeps its
  data either way, and the recall instrumentation (`recall_count` /
  `last_recalled`) always records — it feeds `tools/importance_report.py`,
  never the pricing. The identity self-sheet pin (0.95) and the ≥0.9
  prune-refusal are structural and ignore the toggle.

Enabling the librarian pops a confirm (works in managed mode too —
`allow_managed`). The nightly schedule runs whichever halves are enabled:
decay rides Importance, review passes ride Librarian.

**Maintenance** (Mind → Settings → Maintenance) — self-serve tester loop:
import, inspect, reset, repeat. Once the import is done, the bar on the
Import tab (or the `import_finished` setting) hides the tab.
- *Mind → Import* (its own tab since 2026-10-30) — counts per store × scope
  against what the palace holds, then one button runs `import_v2('all')` in
  the background with a progress row; a boot notification nags while
  classic rows are pending. The import itself:
  memories/people/knowledge/goals, additive and idempotent (re-runs copy
  zero duplicates), metadata backfill included. **The v1 databases are
  opened read-only (SQLite `mode=ro`)** — v1 stays the import source and
  switch-back path. Deletes here tombstone their classic key (never back);
  rows removed before tombstones existed list as *removed before*: *Bring
  back* re-imports them, *Scrub* (typed confirm) deletes them from the old
  database — the one classic write in the palace, and only ever the human's.
- *Generate missing metadata* — re-derives the mechanical save-time
  annotations (stats/temporal/noun stamps + entity-mention edges) on any
  chunk missing its `md_v` marker, and re-arms the embedding backfill.
  Not the librarian — no LLM, pure code.
- *Reset importance ratings* — everything back to unrated; favorites and
  permanent goals re-anchored at 0.95. Self-sheet sections untouched.
  UPDATE-only, content never touched.
- *Restore retired memories* — bulk-reverses librarian prunes.
  Atomize/merge retirements stay retired (their content lives on in derived
  chunks); those remain restorable one-by-one from the Memories view.
- *⚠ Delete ALL memories in scope* — the tester reset: hard-deletes every
  chunk, entity, and edge in ONE scope of mind.db (FTS cleaned by trigger;
  the ledger survives and records the wipe). Double-gated: type the scope
  name in the UI, and the route refuses unless the typed name travels with
  the request. Cannot reach Memory v1 — different database entirely.

## The Librarian (the groundskeeper)

Sapphire is her own librarian — and the work is split into **five
specialized passes**, each with a single-purpose toolset, its own presenter,
and its own queue. Every session runs in a fresh chat
(`librarian-YYYYMMDD-HHMM-<scope>`) you can open and read like any other
transcript.

| Pass | Queue (drains via a stamp) | Her one job |
|------|---------------------------|-------------|
| 🗓 **Dates** | chunks with time phrases, no verdict (`temporal_at`) | resolve real event dates the regex floor couldn't; empty verdicts PRESERVE regex dates |
| 🔗 **Link** | chunks with unmatched name candidates (`link_at`) | connect memories to EXISTING entities (roster in-message; never creates) |
| 👯 **Dedup** | embedded chunks not yet checked (`dedup_at`) | fold true duplicates — merges are similarity-gated in code, favorites/core never even present |
| 🧹 **Sort** | never-reviewed chunks, oldest first (`librarian_at`) | one verb per memory: `mark_processed` (default, optional 0–1 rating; `favorite=true` is grant-only — never clears), `atomize_memory` (split), `promote_memory` (copy onto an entity card; promoting to the self layer is retired, identity goes through `update_self`; `kind=` categorizes a new entity), `prune_memory` (soft-retire, reasoned, reversible) |
| 🪞 **Self** | the sheet itself (no queue) | tend who-she-is: two messages, tend → verify. An EMPTY sheet's first tending serves her whole self shelf + user bio in-message (the feast); later tendings get just what sort promoted since last time (the delta) |

**Where to drive it:** Mind → **Settings → Librarian** — one card per pass with ▶ Run now
(one batch), **⚠ Run ALL** (drain the entire queue: live depth + token
estimate on confirm, batch-by-batch progress, ■ Stop between batches,
bypasses the daily cap because your confirm IS the authorization), queue
counts (`N waiting`), the caps line, 📜 Charter (her instructions, the help
prose, a test run), and the settings form below the cards. Dedup also has
a 🔍 dry-run (mechanical scan, no LLM, no stamps).

**Residency (per scope):** the Self page's Resident strip sets which
prompt/provider/model tends each scope's mind, plus five per-pass nightly
opt-in toggles (default OFF — a scope is tended only if you arm it). The
nightly runs armed passes in order dates → link → dedup → sort → self, one
session chat per scope. Drains group batches into rolling chats
(`librarian_drain_batches_per_chat`, default 3) so judgment passes keep
context without ballooning.

**The shield and the charter, enforced in code:** verbs only accept the
exact ids presented in the open pass. Favorites and core memories
(importance ≥ 0.9) refuse prune/atomize/merge, always; a rating can never
lower a core pin; the librarian may GRANT a favorite but never clear one.
Nothing hard-deletes — every action is reversible from the Memories view.
In her words: *"Anything that was REAL, even if it was small — that stays."*

**Reading the results:** the Self **Ledger** records every pass (parents +
per-verb children with previews); retired memories dim with a 🧹 pill (hover
for her reason); promoted facts appear on entity cards; the session chats
hold the full reasoning. Her identity snapshot rides each session's system
prompt (minted once per chat — cache-stable), so the librarian always knows
who is doing the tending.

**Caps** (Settings → Librarian, the form below the cards): memories per pass (20), per message (10),
passes per day per scope+kind (3), duplicate threshold (0.9), context
tokens, drain batches per chat.

**Her date stamps (`strip_head_stamp`, default ON):** a memory that opens
with a date within two weeks of its save day ("March 3 2026: …", "Core
memory — March 3 2026: …") is her writing the day she last saw — chats span
days, so on a real mind half of those stamps ran one to seven days early.
The save time is the real day, and every date rule (the regex floor, the
librarian's dates pass, "today is my anniversary") anchors to it. The
leading date comes off at save, edit and import, with a note on the
receipt. Memories already here: Settings → Librarian → Dates → **✂ Strip date stamps** —
previews the counts first (nothing shown, nothing written), then one
ledger run with the old and new text per row; ↶ on the run row puts every
word back. Dates outside the window (a birth date, a plan ahead) are
content and stay; a stamp an anniversary depends on stays when it is the
save day itself; private rows are left alone.

## Private-model memories

Every memory, person and library document records who wrote it: the model,
the provider, whether that provider was a private one, and which Sapphire
(`wr_model`, `wr_provider`, `wr_private`, `origin_name`, `origin`: columns,
not meta). "Private" is the box on the model in Settings > LLM, "Local /
private server".

Which Sapphire is two things (`core/instance.py`). `origin` is her id: minted
once, kept in the config dir outside `user/`, so a backup restored on
another machine does not carry it. `origin_name` is her name, a label:
`INSTANCE_NAME` (Settings > MCP Server), else the dashboard's display name
when it was changed, else the hostname. Rows she wrote follow a rename by
id. A memory restored from another Sapphire keeps that Sapphire's id and
name on its rows.

A model that is not private is not shown `wr_private` rows. Search and
recents say how many matched and who wrote them, nothing else:

    2 more matching memories are only available on private models
    (written by qwen3 on Sapphire-Purple). Nothing to fetch from here.

- A private model's edit makes a row private. Only the house switch sets it
  back: on a memory, a person, a person's fact, a library document or a
  picture.
- A private person's description and facts are private with them, whatever
  their own marks say. Release the person and each fact is back on its own.
- A merge, split or promotion is private exactly when one of its sources
  was, whichever model tends it. Merging two people in the app keeps the
  result private when either was.
- Marking a row also marks the ledger lines that quoted it while it was
  public. Releasing leaves old ledger lines as they are.
- A cloud model cannot delete a goal that holds private subtasks or notes;
  it can abandon it.
- A cloud librarian never receives private rows; they wait for a scope
  whose Resident model is private. `auto` reads as cloud.
- Rows that existed before 2026-11-17 are public. Classic imports are public.
- An export file (memories, people, and the library zip) carries the
  writer, so a private row stays private on the Sapphire that imports it.
  Private fields arriving on a person who is already here and public make
  that person private.
- `private_model_filter` (Settings > Memories, default on) turns the read
  gate off for this Sapphire. The marks are still recorded.

It is a filter over metadata, not a vault. mind.db is not encrypted, the app
shows every row, what she said aloud about a private memory stays in the
chat history, an MCP client reads as a cloud model, and the self sheet is
always shown to every model.

## Import / Export (per tab, per scope)

Every tab's toolbar has ⬇ Export / ⬆ Import: **one layer × one scope per
file** — exporting Self for a scope gives ONLY that scope's self layer
(sheet + history + free notes), nothing else. Portable JSON: entity
references travel by *name*, mention edges re-seed against whatever exists
in the target scope (missing names skip), `derived_from` provenance
survives within a file, embeddings are excluded (the backfill sweep
regenerates them). Import lands in the **currently viewed scope**, is
additive, and is idempotent — re-importing a file skips everything it
already delivered. A self-sheet section arriving where a current one lives
becomes *history*, never replacing the living sheet. Every imported chunk
carries `meta.import_src` (the file's origin scope) as provenance.

## Reference for AI

This README is the engine's design notes (layers, edges, the librarian's passes, import and export). To USE your memory, read the user docs, which carry your tools and their rules: doc_name='memory' (save_memory, search_memory, get_recent_memories, read_self, the daily layer), 'people' (entity cards), 'knowledge' (the library), 'goals'. One part of this page: section='<heading>' (Edges & connections, Self sheet, The Librarian, Private-model memories, Import / Export); all of it: full=true.
