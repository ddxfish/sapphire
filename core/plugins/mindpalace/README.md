# Mind Palace

Layered memory supersystem (memory v3). One database (`user/memory/mind.db`),
one uniform unit everywhere — **chunks + metadata** — organized into layers on
a rail:

| # | Layer | What lives there |
|---|-------|------------------|
| 0 | `self` | Who she is: the self sheet (typed sections + custom boxes) and free self-notes |
| 1 | `events` | Things that happened. Default write target; the librarian's raw material |
| 2 | `entities` | People / places / things / events-as-nouns, with tiered chunks (1 = headline, 2 = facts, 3 = trivia) and per-kind template fields |
| 3 | `knowledge` | Big reference data, sub-chunked |
| 4 | `goals` | What she's working toward: goals + subtasks + progress journal, woven into the graph |

Mutually exclusive with the classic Memory plugin (same tool names — enable
one, not both). `import_v2` copies the old stores in; the old DBs are opened
read-only and never touched — switching back is always possible.

Tools: `save_memory`, `search_memory`, `get_recent_memories`, `update_memory`,
`delete_memory`, `read_self`, `update_self`, `read_ledger`, `create_goal`,
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
boat" auto-edges to Krem if "the boss" is one of Krem's nicknames. Matching is
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
The connected block is token-budgeted **per depth** via Settings → Plugins →
Mind Palace (defaults: ~2K tokens of connections at depth 1, ~6K at depth 2 —
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

## Alpha toggles (Librarian & Importance)

Two masters in Settings → Plugins → Mind Palace, **both off by default** —
the core system (save/search/spider/entities/self/goals) is designed to run
without them, with no lurking variables:

- **Librarian (ALPHA)** — gates every pass: Tidy buttons, `run_librarian`,
  the nightly round. Off = the sorting verbs refuse (blast shield stays shut).
- **Importance (ALPHA)** — gates the whole salience system as one unit:
  the `mark_processed` rating parameter, spider pricing `f(importance)` and
  its ordering tiebreak, nightly decay, and recall boost. Off = flat, neutral
  pricing everywhere (favorites included). The `importance` column keeps its
  data either way, and the recall instrumentation (`recall_count` /
  `last_recalled`) always records — it feeds `tools/importance_report.py`,
  never the pricing. The identity self-sheet pin (0.95) and the ≥0.9
  prune-refusal are structural and ignore the toggle.

Enabling either pops an alpha warning (works in managed mode too —
`allow_managed`). The nightly schedule runs whichever halves are enabled:
decay rides Importance, review passes ride Librarian.

**Maintenance** (Mind → Self → Dashboard → 🛠) — self-serve tester loop:
import, inspect, reset, repeat.
- *Import from Memory v1 (all scopes)* — one-click `import_v2('all')`:
  memories/people/knowledge/goals, additive and idempotent (re-runs copy
  zero duplicates), metadata backfill included. **The v1 databases are
  opened read-only (SQLite `mode=ro`) and are never modified** — v1 stays
  the pristine import source and switch-back path, always.
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
| 🧹 **Sort** | never-reviewed chunks, oldest first (`librarian_at`) | one verb per memory: `mark_processed` (default, optional 0–1 rating; `favorite=true` is grant-only — never clears), `atomize_memory` (split), `promote_memory` (copy to self layer or an entity card, `kind=` categorizes a new entity), `prune_memory` (soft-retire, reasoned, reversible) |
| 🪞 **Self** | the sheet itself (no queue) | tend who-she-is: two messages, tend → verify. An EMPTY sheet's first tending serves her whole self shelf + user bio in-message (the feast); later tendings get just what sort promoted since last time (the delta) |

**Where to drive it:** Mind → **Admin** — one card per pass with ▶ Run now
(one batch), **⚠ Run ALL** (drain the entire queue: live depth + token
estimate on confirm, batch-by-batch progress, ■ Stop between batches,
bypasses the daily cap because your confirm IS the authorization), queue
counts (`N waiting`), the caps line, ⚙ settings, and a ? help modal per
pass. Dedup also has a 🔍 dry-run (mechanical scan, no LLM, no stamps).

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

**Caps** (⚙ on any Admin card): memories per pass (20), per message (10),
passes per day per scope+kind (3), duplicate threshold (0.9), context
tokens, drain batches per chat.

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
