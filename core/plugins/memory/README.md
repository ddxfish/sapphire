# Memory (legacy)

The classic memory engine, from before the [Mind Palace](../../../docs/MEMORY.md). It's kept so installs that still hold classic data keep working, and so the Mind Palace's Import tab can read it. **It's being retired**: new installs get the Mind Palace, and an upgrade holding classic data stays here until you switch (Settings → Plugins, then Mind → Import).

The two engines can't run together, because they register the same tool names.

## What it keeps

| Store | File | Tab |
|---|---|---|
| Memories: short snippets with labels | `user/memory.db` | Mind → Memories |
| People: contacts | `user/knowledge.db` | Mind → People |
| Knowledge: tabs of entries, uploaded files | `user/knowledge.db` | Mind → Human Knowledge / AI Knowledge |
| Goals: subtasks, priorities, journal | `user/goals.db` | Mind → Goals |

- **Memories** cap at 512 characters (the AI is told 450), are trimmed rather than refused, and carry freeform labels. The Memories tab sorts, filters by label chips, and has Find Duplicates and JSON export/import.
- **People** have name, relationship, phone, email, address, notes and an "Allow AI to send email" box. **Import VCF** bulk-imports a vCard file (duplicates by name + email are skipped).
- **Knowledge** tabs you make are protected; tabs the AI makes (`type: ai`) it may delete. Files are chunked and embedded.
- **Goals** have title, description, priority, status (active, completed, abandoned), parent and permanent. Permanent goals can't be completed or deleted by the AI.

## Scopes

Four selectors in the chat sidebar: memory, knowledge, people, goals. Each has a `global` overlay that is read-only for the AI, and `none` turns that store off for a chat.

## Search

Knowledge search cascades: filename match, full-text AND, full-text OR with prefixes, vector similarity (0.40), then a substring fallback. People use vector similarity (0.55) and a substring match only.

## Moving to the Mind Palace

1. Settings → Plugins: turn off **Memory (legacy)**, turn on **Mind Palace**.
2. Mind → Import: review, then import everything. It's additive and read-only on these databases.
3. Point each chat at the same scope name it used before.

## Reference for AI

LEGACY engine (plugin `memory`). Only applies when this plugin is active instead of Mind Palace; the default engine is in docs MEMORY.md.

TOOLS:
- save_memory(content, label?, private_key?) — 512 cap (aim 450), over-cap trimmed; receipt "Skip a re-save unless it's critical". search_memory(query, label?, private_key?), get_recent_memories(count?, label?, private_key?), delete_memory(memory_id, private_key?). No update_memory.
- save_person(name, id?, relationship?, phone?, email?, address?, notes?) — upsert by name (or id) per scope.
- save_knowledge(category, content, description?), search_knowledge(query?, category?, id?, limit?) — people + knowledge; delete_knowledge(id?, category?) — AI-made tabs only, never people.
- create_goal(title, description?, priority?, parent_id?, permanent?), list_goals(goal_id?, status?), update_goal(goal_id, …, progress_note?), delete_goal(goal_id, cascade?).

SCOPES: scope_memory, scope_knowledge, scope_people, scope_goal; 'global' overlay read-only for the AI.
DBS: user/memory.db, user/knowledge.db, user/goals.db.
