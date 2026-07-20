# Mind Palace (memory v2) — the quiet upgrade

Sapphire ships with two memory systems. **Memory v1** (the `memory` plugin)
is the default and what most installs should stay on — it works, it's
proven, nothing about it changes. **Mind Palace** (the `mindpalace` plugin,
"Mind" in the UI) is the next-generation layered memory: one database of
chunks + metadata organized into layers (events, self, entities, goals,
knowledge/Library), with a graph of connections and a librarian that tends
it. It ships **disabled**. If you don't enable it, you will never notice it
exists. This page is for the people who go looking.

**The two are mutually exclusive by design** — they register the same tool
names (`save_memory`, `search_memory`, …), so enabling the second one
refuses to load with an error toast. You run one or the other, never both.

## The swap (v1 → Mind)

1. Settings → Plugins → disable **Memory**, enable **Mind Palace**.
2. Restart Sapphire.
3. The nav rail's Mind entry now opens the palace views (Memories, Self,
   Entities, Goals, Knowledge, Admin). Your v1 data is untouched on disk —
   `user/memory.db`, `knowledge.db`, `goals.db` stay exactly as they were,
   and the palace only ever opens them read-only.

**Switching back** is the same toggle in reverse, and v1 resumes exactly
where it left off. ⚠ One-way door caveat: memories saved while Mind was
active live in `user/memory/mind.db` and are NOT visible to v1 — there is
no v2→v1 export yet. Trial accordingly.

**Scopes carry the same names across both systems** — if your chats ran
scope `sapphire` under v1, point them at `sapphire` after the swap too
(chat sidebar → memory scope). A search that suddenly returns nothing is
almost always a chat pointed at the wrong scope, not lost data.

## Importing your v1 memories

The importer is additive, idempotent (re-runs skip what's already there),
and strictly read-only toward v1. Per scope you want to migrate:

1. **Mind → Admin** (viewing the target scope) → Import & Migration →
   **Import from Memory v1**. Watch for the summary ("N imported ·
   metadata backfill: N stamped").
2. **Wait for embeddings** before anything else: the log line
   `Backfill complete: N/N chunks embedded` (or the Knowledge tab going
   quiet). No embedder configured = search degrades to keyword-only and
   dedup can't see duplicates — fix the embedder first.
3. Knowledge/documents ride a separate one-time Library migration
   (Admin → "Copy knowledge …") and per-scope Export/Import zips on the
   Knowledge tab toolbar (files + annotations; watch folders come across
   as definitions you re-add by hand — a zip never auto-scans your disk).

## The librarian shakedown (recommended after import)

Imported memories arrive raw. The librarian's five passes (see the plugin
README for what each does) turn them into a tended mind. The proven order,
one scope at a time, from **Mind → Admin**:

1. **Dates** — ▶ Run now once; skim the session chat (did the verdicts
   look sane?), then **⚠ Run ALL** to drain the queue.
2. **Link** — same rhythm: one batch, sanity-check that connections went
   to real entities and junk was ruled connection-free, then Run ALL.
3. **Dedup** — run the 🔍 dry-run first (free, no LLM) and READ the
   clusters: similar-sounding is not duplicate. ⭐ favorite anything that
   must never merge (favorites refuse merges in code and are excluded from
   the presented clusters entirely). Then ▶ Run now, then Run ALL. Merges
   are soft — originals retire to the archive, recoverable.
4. **Sort** — the judgment pass (mark / split / promote / retire). One
   batch first: read the verbs she chose and the retirement reasons in the
   Ledger. If they read sane, Run ALL. Expect promotes to mint a few new
   entities (categorize them on the Entities tab afterwards) and expect
   the dates/link queues to refill a little — split parts and promoted
   copies re-enter those queues on purpose; the nightly grazes them down.
5. **Self ×2** — run the Self pass twice. The first tending writes her
   self sheet from everything sort promoted (served in-message — the
   feast); the second lets her revise it with the finished sheet behind
   her eyes. Add custom sheet sections only AFTER these runs (any existing
   section suppresses the first-tending flow).

Costs: dates/link/dedup are cheap and fast; sort is the heavy one
(per-item judgment). A ~500-memory scope drains end-to-end in well under
an hour on a mid-size model. Run ALL shows a token estimate before you
commit, bypasses the daily 3-passes cap (your confirm is the
authorization), and can be stopped between batches.

## After the shakedown

- **Residency** (Self page → Resident strip): set which prompt/provider/
  model tends this scope, and arm the per-pass nightly toggles (default
  OFF). The nightly then keeps the queues at zero as new memories arrive.
- **Upcoming / Just happened** on the Self page start working the moment
  dates exist; recurring dates (birthdays on entity cards, holidays) show
  with ↻ and come back every year.
- Keep v1 disabled but installed. Your v1 databases are your fallback —
  the palace never wrote to them.

## Reset button

Wipes and clears live in Admin → Danger, all gated by typing the scope
name. Deleting a scope (or clearing a layer) in the palace never touches
the v1 databases, so a bad experiment is always recoverable by wiping the
palace scope and re-importing from v1.
