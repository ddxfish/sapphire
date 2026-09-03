# Memory

Short snippets Sapphire saves about you and your world — facts, preferences, events — captured automatically during conversation and recalled when they're relevant. Memories live in the **Mind** view (brain icon in the nav bar) → **Memories** tab.

<img width="50%" alt="sapphire-memories" src="https://github.com/user-attachments/assets/348f1628-5f0c-4ce3-948e-2e0c1385bc75" />

---

## How It Works

During conversation the AI decides what's worth remembering and saves it with `save_memory` — a short line of text plus an optional **label**. Later it finds memories with `search_memory` (semantic + keyword) or `get_recent_memories`. You don't have to manage any of this, but you can review, edit, and curate everything in the Memories tab.

Memories are kept **short** — a hard cap of 512 characters each. A save that runs over the cap isn't refused: the text is **trimmed at the last word boundary and saved anyway**, and the tool reply reports the dropped text verbatim so the AI can save the tail as its own memory, delete and re-save tighter, or let it go. They're snippets, not documents. For longer reference material, use [Human Knowledge](KNOWLEDGE.md) instead.

---

## The Memories Tab

Open Mind → Memories to see every memory in the current scope as a card. From here you can:

- **Search** — type to filter by content, label, or private key (instant, no reload).
- **Sort** — newest, oldest, longest, shortest, or by label.
- **Label chips** — the most common labels surface as chips; click one to filter, click again to clear.
- **Edit / Delete** — hover a card for edit (✎) and delete (✕).
- **Find Duplicates** — scans for near-identical memories and walks you through combine / delete / skip.
- **Export / Import** — back up or move a scope's memories as JSON.

---

## Labels

Each memory can carry one or more **labels** (comma-separated) — lightweight categories like `preference`, `fact`, or `event`. Labels are freeform: the AI reuses common ones but can coin new ones. They drive the filter chips and colour-coding in the UI. A memory with no label shows as `unlabeled`.

---

## Keyed (Private-Key) Memories

A memory can be **gated** with a private key. Only AI tool calls that pass the matching key can see, recall, or delete that memory — useful for sensitive notes you don't want surfacing in every context. In the Memories tab you (the logged-in user) always see the plaintext key on the card; the gate is for the AI's tool calls, not for your own privileged view.

### With Mind Palace on

The Mind Palace engine honors the same gate and adds containment guarantees around its librarian (the background tending passes that sort, date, link, and dedup memories):

- **Never tended.** Keyed rows are excluded from every librarian selector — no pass ever reads them, so they can never be merged, retired, or promoted.
- **Ledger-safe.** The activity ledger records a keyed save only as `[keyed]`, with no content preview (ledger tails can ride into system prompts).
- **Not in wake pulls.** Wake-time memory previews pass no key, so keyed rows never surface in them.

### One honest disclosure

Keyed memory **text rides your chosen embedder at save time**. Embeddings power semantic search, and they're generated wherever your embedding provider runs: with a local embedder, keyed text never leaves your machine; with a remote/API embedder, the text of a keyed memory is sent to that server to be embedded — the key gates recall, not the embedding step. If fully-local matters to you, use a local embedding provider (Settings → embedding).

---

## Scopes

Memory is scoped like the rest of the Mind section: each chat reads and writes a chosen memory scope, and a scope you create exists across **all** Mind sections at once. Memory also uses a **global overlay** — a chat on scope "work" sees both "work" memories and anything in the shared "global" scope, so you can keep common facts everywhere while isolating the specialized ones. Pick or create a scope from the left sidebar. See [Scopes](KNOWLEDGE.md#scopes) for the full explanation.

---

## Mind Palace

Enabling the Mind Palace plugin swaps the whole memory tool surface — tools, Mind views, and the librarian — for the palace engine. [MIND-PALACE.md](MIND-PALACE.md) covers it.

---

## Reference for AI

Long-term memory with full-text (FTS5) + semantic (embedding) search and labels.

TOOLS:
- save_memory(content, label?, private_key?) — save a short memory (512-char cap; over-cap saves are trimmed at the last word boundary and the reply reports the dropped text verbatim; new labels OK; use 'self' for self-knowledge)
- search_memory(query, label?, private_key?) — semantic + full-text search, optional label filter
- get_recent_memories(count?, label?, private_key?) — most recent, optionally filtered by label
- delete_memory(memory_id, private_key?) — remove a memory

DATABASE:
- user/memory.db — memories, memories_fts (FTS5), memory_scopes

DETAILS:
- Memories scoped via scope_memory (default: 'default'); global overlay reads the shared 'global' scope (read-only for the AI)
- private_key gates a memory so only AI calls passing that key can see it
- Labels: comma-separated, freeform; 'unlabeled' when none
- Search cascade: full-text (content + keywords) then vector similarity (semantic meaning)
- Keyed memory text is embedded at save time by the configured embedding provider — a remote/API embedder means keyed text leaves the machine; the key gates recall only
- Mind Palace keyed containment: excluded from all librarian tending/promotion, ledger logs only [keyed], absent from wake pulls
- Enabling the Mind Palace plugin replaces these tools with the palace engine — see MIND-PALACE.md
