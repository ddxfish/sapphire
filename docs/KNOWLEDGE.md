# Knowledge Base

Sapphire can remember facts about your world — people you know, topics you care about, reference documents, and goals you're working toward. The Knowledge system organizes all of this and makes it searchable by the AI during conversations.

Everything lives in the **Mind** view (brain icon in the nav bar), which has five tabs: Memories, People, Knowledge, AI Notes, and Goals.

---

## How It Works

The AI has tools to save and search knowledge automatically during conversations. You can also add knowledge manually through the Mind view in the web UI.

Knowledge is **scoped** — each chat can access a different set of knowledge. This lets you keep work knowledge separate from personal, or give different personas different contexts. See [Scopes](#scopes) below.

---

## Knowledge Tabs

Knowledge is organized into **tabs** (categories). Each tab holds entries — chunks of text that the AI can search.

### Adding Knowledge (Web UI)

1. Open the **Mind** view (brain icon)
2. Click the **Knowledge** tab
3. Click **+ New Tab** to create a category (e.g., "Cooking Recipes", "Work Projects")
4. Add entries manually, or upload files

### File Upload

You can upload text files to a knowledge tab. Sapphire automatically:
- Splits the content into chunks (paragraph → sentence → word boundaries)
- Generates vector embeddings for semantic search
- Stores with the source filename for reference

### AI Notes

The **AI Notes** tab shows knowledge tabs the AI created itself using the `save_knowledge` tool. These are marked as `type: ai` and can be deleted by the AI. User-created tabs are protected — the AI can't delete them.

---

## Per-Chat Documents (RAG)

Each chat can have documents attached directly. These are separate from the Knowledge tabs — they're scoped strictly to that one chat.

In Chat Settings sidebar → **Documents** accordion:
- Upload files (auto-chunked and embedded)
- Set context level: Off, Light, Normal, Heavy
- View and remove attached documents

Useful for giving the AI reference material for a specific conversation without polluting the global knowledge base.

---

## Goals

Track what you're working toward. Goals support hierarchy (subtasks), priorities, and progress journaling.

### Creating Goals

The AI can create goals during conversation using `create_goal`, or you can add them in the Mind → Goals tab.

| Field | Purpose |
|-------|---------|
| Title | What you want to accomplish |
| Description | Details and context |
| Priority | high, medium, low |
| Status | active, completed, abandoned |
| Parent | Optional — makes this a subtask |

### Progress Notes

Goals have a timestamped progress journal. The AI can log progress with `update_goal`, or you can add notes in the UI. These are append-only — a running log of what happened.

---

## Scopes

Scopes isolate knowledge per-chat. Each chat has scope selectors in the sidebar under **Mind Scopes**:

| Scope | What it controls |
|-------|-----------------|
| Memory | Which memory slot the AI reads/writes |
| Knowledge | Which knowledge tabs are accessible |
| People | Which contacts the AI can see |
| Goals | Which goal set is visible |

Set a scope to **none** to disable that system for a chat entirely.

### Global Overlay

Knowledge and memory use a **global overlay** — when a chat uses scope "work", it sees both "work" entries AND entries in the "global" scope. This lets you share common knowledge across all chats while keeping specialized knowledge isolated.

### Creating Scopes

Click the **+** button next to any scope dropdown in the sidebar. Scopes are just names — create them to match your organizational style (e.g., "work", "personal", "gaming").

---

## Search

The AI searches knowledge using a cascading strategy (tries each method until it finds results):

1. **Filename match** — exact source file name
2. **Full-text search** (AND) — all search terms must match
3. **Full-text search** (OR + prefix) — any term, prefix matching
4. **Vector similarity** — semantic meaning (threshold: 0.40)
5. **Substring fallback** — LIKE query

This means the AI can find knowledge both by exact keywords and by meaning.

---

## Reference for AI

Knowledge base system for storing and searching structured information.

TOOLS:
- save_person(name, relationship?, phone?, email?, address?, notes?) — upsert contact
- save_knowledge(category, content, entry_title?) — store info in category (auto-chunks long content)
- search_knowledge(query, search_type?) — search people + knowledge + RAG docs
- delete_knowledge(category?, entry_id?) — delete AI-created content only

DATABASES:
- user/knowledge.db — people, knowledge_tabs, knowledge_entries, knowledge_fts
- user/goals.db — goals, progress_journal
- user/memory.db — memories, memories_fts, memory_scopes

SCOPES:
- Knowledge scoped via scope_knowledge ContextVar (default: 'default')
- People scoped via scope_people ContextVar (default: 'default')
- Goals scoped via scope_goal ContextVar (default: 'default')
- Global overlay: scope sees own data + 'global' scope entries (read-only for AI)
- AI cannot write to the 'global' scope — only users can via the UI
- RAG strict: __rag__:{chat_name} scope, no overlay

SEARCH STRATEGY:
1. Filename match (0.96 score)
2. FTS AND (0.95 score)
3. FTS OR + prefix
4. Vector similarity (0.40 threshold for knowledge, 0.55 for people)
5. LIKE fallback (0.35 score)

CONTENT PROTECTION:
- AI can delete entries from type='ai' tabs only
- User-created tabs (type='user') are protected
- AI cannot delete people

CHUNKING:
- Long content auto-split: paragraph → sentence → word boundaries
- Each chunk embedded independently for search
- Source filename tracked for reference

GOALS:
- create_goal(title, description?, priority?, parent_id?) — create goal or subtask
- list_goals(goal_id?, status?) — smart overview or detail view
- update_goal(goal_id, title?, description?, status?, priority?, progress_note?) — modify + journal
- delete_goal(goal_id, cascade?) — delete with optional subtask cascade
- Priorities: high, medium, low
- Statuses: active, completed, abandoned
- Progress journal: timestamped append-only entries
