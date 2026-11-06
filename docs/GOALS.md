# Goals

What you and Sapphire are working toward: goals with subtasks, priorities, due dates, step-by-step instructions and a progress journal. They live in **Mind → Goals**, as the goal layer of the [Mind Palace](MEMORY.md), so they're part of the same graph as her memories. A search finds them, and a goal that names a person links to that person's card.

<img width="50%" alt="sapphire-goals" src="https://github.com/user-attachments/assets/9ab3e309-5014-4d83-add0-4004d507085c" />

---

## Goal fields

| Field | Purpose |
|---|---|
| Title | What you want to accomplish |
| Description | Context and what counts as done |
| Instructions | *How* to do it: step notes for whoever does the work |
| Priority | high, medium, low |
| Status | active, in_progress, completed, abandoned |
| Due | A date (YYYY-MM-DD) |
| Parent | Makes this a subtask |
| Permanent | A standing goal (below) |

She creates goals with `create_goal` and logs progress with `update_goal(progress_note=…)`. You can do both in the Goals tab. The **journal** is append-only: a running log of what happened, not editable history.

**Permanent goals** are long-standing objectives: they never fade, and she can only add notes to them, never complete, retitle or delete them. You keep full control in the UI.

**Deleting** a goal removes its subtasks and journal too. To keep the history, set it to `abandoned` instead.

## Scopes

Goals ride the chat's memory scope (see [Scopes](MEMORY.md#scopes)). There's no separate goal scope.

## Daemons and heartbeats

A heartbeat or scheduled task can work through goals unattended: its prompt asks her to `list_goals`, pick one, and journal what she did. See [CONTINUITY.md](CONTINUITY.md).

---

## Reference for AI

GOALS = Mind Palace goal layer (Mind → Goals); chunks in the graph on memory_scope (no goal scope); search_memory finds them.

TOOLS:
- create_goal(title, description?, instructions?, priority?, due?, parent_id?, permanent?) — parent_id = subtask; permanent = standing goal.
- list_goals(goal_id?, status?) — overview; goal_id = deep view (detail, subtasks, journal). status active|in_progress|completed|abandoned|all (default active, includes in_progress).
- update_goal(goal_id, title?, description?, instructions?, priority?, status?, due?, progress_note?) — '' clears instructions/due; progress_note appends a timestamped journal line.
- delete_goal(goal_id) — always removes subtasks + journal; prefer status='abandoned' to keep history.
- Permanent goals: you may only add progress notes; never complete, retitle or delete.
