# Goals

Track what you're working toward. Goals support hierarchy (subtasks), priorities, and a timestamped progress journal. They live in the **Mind** view (brain icon in the nav bar) → **Goals** tab.

<img width="50%" alt="sapphire-goals" src="https://github.com/user-attachments/assets/9ab3e309-5014-4d83-add0-4004d507085c" />

---

## Creating Goals

The AI can create goals during conversation using `create_goal`, or you can add them yourself in the Mind → Goals tab.

| Field | Purpose |
|-------|---------|
| Title | What you want to accomplish |
| Description | Details and context |
| Priority | high, medium, low |
| Status | active, completed, abandoned |
| Parent | Optional — makes this a subtask |

Subtasks roll up under their parent goal. A goal can be marked **permanent** — the AI cannot complete or delete a permanent goal, only you can (useful for long-standing objectives you don't want the AI tidying away).

---

## Progress Notes

Goals have a timestamped **progress journal**. The AI logs progress with `update_goal`, or you can add notes in the UI. The journal is append-only — a running log of what happened, not editable history.

---

## Scopes

Goals are scoped like the rest of the Mind section: each chat can see a different goal set, and a scope you create exists across **all** Mind sections at once (memories, people, knowledge, goals). Pick or create a scope from the left sidebar in the Goals tab. See [Scopes](KNOWLEDGE.md#scopes) for the full explanation.

---

With the Mind Palace plugin enabled, goals work differently (due dates, per-goal instructions, an in-progress status) — see [MIND-PALACE.md](MIND-PALACE.md).

---

## Reference for AI

Goal tracking with subtasks, priorities, and a progress journal.

TOOLS:
- create_goal(title, description?, priority?, parent_id?, permanent?) — create a goal or subtask (permanent=true: standing goal the AI cannot complete/delete)
- list_goals(goal_id?, status?) — smart overview or detail view
- update_goal(goal_id, title?, description?, status?, priority?, progress_note?) — modify + journal a note
- delete_goal(goal_id, cascade?) — delete, optionally cascading to subtasks

DATABASE:
- user/goals.db — goals, progress_journal

DETAILS:
- Priorities: high, medium, low
- Statuses: active, completed, abandoned
- Progress journal: timestamped, append-only
- Goals scoped via scope_goal (default: 'default')
- Permanent goals are protected from AI complete/delete (user-only)
