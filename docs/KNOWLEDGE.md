# Knowledge Base

Sapphire can remember facts about your world — people you know, topics you care about, reference documents, and goals you're working toward. The Knowledge system organizes all of this and makes it searchable by the AI during conversations.

Everything lives in the **Mind** nav group (brain icon in the nav bar) — six views: Self, [Memories](MEMORY.md), [People](PEOPLE.md) (shown as Entities), Knowledge, [Goals](GOALS.md), and Admin. Self and Admin belong to the [Mind Palace](MIND-PALACE.md) engine; with classic memory they just point you to the plugin, and an **AI Knowledge** tab appears on the strip inside the classic Mind views. This guide covers the **Knowledge** tabs (human + AI) and the shared **scope** system.

When the Mind Palace plugin is on, its **Library** replaces this whole knowledge surface — see [MIND-PALACE.md](MIND-PALACE.md).

<img width="50%" alt="sapphire-memories" src="https://github.com/user-attachments/assets/348f1628-5f0c-4ce3-948e-2e0c1385bc75" />

---

## How It Works

The AI has tools to save and search knowledge automatically during conversations. You can also add knowledge manually through the Mind view in the web UI.

Knowledge is **scoped** — each chat can access a different set of knowledge. This lets you keep work knowledge separate from personal, or give different personas different contexts. See [Scopes](#scopes) below.

---

## Knowledge Tabs

Knowledge is organized into **tabs** (categories). Each tab holds entries — chunks of text that the AI can search.

<img width="50%" alt="sapphire-knowledge" src="https://github.com/user-attachments/assets/405a43e1-b542-4489-b213-6eb82c9226e4" />

### Adding Knowledge (Web UI)

1. Open the **Mind** view (brain icon)
2. Click the **Human Knowledge** tab
3. Click **+ New Tab** to create a category (e.g., "Cooking Recipes", "Work Projects")
4. Add entries manually, or upload files

### File Upload

You can upload text files to a knowledge tab. Sapphire automatically:
- Splits the content into chunks (paragraph → sentence → word boundaries)
- Generates vector embeddings for semantic search
- Stores with the source filename for reference

### AI Knowledge

The **AI Knowledge** tab shows knowledge tabs the AI created itself using the `save_knowledge` tool. These are marked as `type: ai` and can be deleted by the AI. User-created tabs are protected — the AI can't delete them.

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

Goals have their own guide now — see **[GOALS.md](GOALS.md)**.

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
- save_knowledge(category, content, description?) — store info in category (auto-chunks long content)
- search_knowledge(query?, category?, entry_id?, limit?) — search people + knowledge + RAG docs
- delete_knowledge(category?, entry_id?) — delete AI-created content only

DATABASES:
- user/knowledge.db — people, knowledge_tabs, knowledge_entries, knowledge_fts
- (memory → see MEMORY.md · goals → see GOALS.md)

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
