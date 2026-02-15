# functions/goals.py
"""
Goal tracking system for AI self-directed planning.
SQLite-backed with subtasks, progress journaling, and memory scope integration.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎯'

_db_path = None
_db_initialized = False

VALID_PRIORITIES = ('high', 'medium', 'low')
VALID_STATUSES = ('active', 'completed', 'abandoned')

AVAILABLE_FUNCTIONS = [
    'create_goal',
    'list_goals',
    'update_goal',
    'delete_goal',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "create_goal",
            "description": "Create a new goal or subtask. Use parent_id to nest under an existing goal. Goals persist across conversations and are scoped to your current memory slot by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short, clear goal title (max 200 chars)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional context, motivation, or success criteria (max 500 chars)"
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority level: high, medium, or low (default: medium)"
                    },
                    "parent_id": {
                        "type": "integer",
                        "description": "ID of parent goal to make this a subtask. Omit for top-level goal."
                    },
                    "scope": {
                        "type": "string",
                        "description": "Goal scope (defaults to current memory scope). Use to organize goals by context."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "list_goals",
            "description": "List goals or get details on a specific goal. Without goal_id: shows top 3 goals fully expanded (subtasks + recent progress) and summarizes the rest, sorted by most recently updated. With goal_id: deep view of that goal with all subtasks and full progress journal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {
                        "type": "integer",
                        "description": "Get full details for a specific goal. Omit for smart overview."
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status: active, completed, abandoned, or all (default: active)"
                    },
                    "scope": {
                        "type": "string",
                        "description": "Goal scope (defaults to current memory scope)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "update_goal",
            "description": "Update a goal's fields or log progress. Any field you pass gets updated. Use progress_note to journal what you did — it's timestamped and appended (not replaced). Updating anything bumps the goal to the top of list_goals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {
                        "type": "integer",
                        "description": "ID of the goal to update (shown in brackets like [5])"
                    },
                    "title": {
                        "type": "string",
                        "description": "New title (max 200 chars)"
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (max 500 chars)"
                    },
                    "priority": {
                        "type": "string",
                        "description": "New priority: high, medium, or low"
                    },
                    "status": {
                        "type": "string",
                        "description": "New status: active, completed, or abandoned"
                    },
                    "progress_note": {
                        "type": "string",
                        "description": "Journal entry about progress made (max 1024 chars). Timestamped and appended to history. Use for status updates, completion summaries, and lessons learned."
                    }
                },
                "required": ["goal_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "delete_goal",
            "description": "Permanently delete a goal. Use status 'abandoned' via update_goal to keep a record instead. Subtasks are deleted too by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {
                        "type": "integer",
                        "description": "ID of the goal to delete (shown in brackets like [5])"
                    },
                    "cascade": {
                        "type": "boolean",
                        "description": "Also delete subtasks (default: true). Set false to orphan subtasks into top-level goals."
                    }
                },
                "required": ["goal_id"]
            }
        }
    }
]


# ─── Database ─────────────────────────────────────────────────────────────────

def _get_db_path():
    global _db_path
    if _db_path is None:
        _db_path = Path(__file__).parent.parent / "user" / "goals.db"
    return _db_path


def _get_connection():
    _ensure_db()
    conn = sqlite3.connect(_get_db_path(), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return

    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'active',
            parent_id INTEGER REFERENCES goals(id),
            scope TEXT NOT NULL DEFAULT 'default',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goal_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            note TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_goals_scope ON goals(scope)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_progress_goal ON goal_progress(goal_id)')

    # Scope registry (mirrors memory_scopes pattern)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goal_scopes (
            name TEXT PRIMARY KEY,
            created DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO goal_scopes (name) VALUES ('default')")

    conn.commit()
    conn.close()
    _db_initialized = True
    logger.info(f"Goals database ready at {db_path}")


def _get_current_scope():
    try:
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_goal_scope
    except Exception:
        return 'default'


# ─── Public API (used by api_fastapi.py) ──────────────────────────────────────

def get_scopes():
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT scope, COUNT(*) FROM goals WHERE parent_id IS NULL GROUP BY scope')
        goal_counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute('SELECT name FROM goal_scopes ORDER BY name')
        registered = [row[0] for row in cursor.fetchall()]
        conn.close()
        all_scopes = set(registered) | set(goal_counts.keys()) | {'default'}
        return [{"name": name, "count": goal_counts.get(name, 0)} for name in sorted(all_scopes)]
    except Exception as e:
        logger.error(f"Error getting goal scopes: {e}")
        return [{"name": "default", "count": 0}]


def create_scope(name: str) -> bool:
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO goal_scopes (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to create goal scope '{name}': {e}")
        return False


def delete_scope(name: str) -> dict:
    """Delete a goal scope and ALL its goals, subtasks, and progress notes."""
    if name == 'default':
        return {"error": "Cannot delete the default scope"}
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM goals WHERE scope = ? AND parent_id IS NULL', (name,))
        goal_count = cursor.fetchone()[0]
        # Delete progress for all goals in scope
        cursor.execute('DELETE FROM goal_progress WHERE goal_id IN (SELECT id FROM goals WHERE scope = ?)', (name,))
        cursor.execute('DELETE FROM goals WHERE scope = ?', (name,))
        cursor.execute('DELETE FROM goal_scopes WHERE name = ?', (name,))
        conn.commit()
        conn.close()
        logger.info(f"Deleted goal scope '{name}' with {goal_count} goals")
        return {"deleted_goals": goal_count}
    except Exception as e:
        logger.error(f"Failed to delete goal scope '{name}': {e}")
        return {"error": str(e)}


# ─── Public API (used by api_fastapi.py) ─────────────────────────────────────

def get_goals_list(scope='default', status='active'):
    """Return structured goal data for the REST API."""
    conn = _get_connection()
    cursor = conn.cursor()

    if status == 'all':
        cursor.execute(
            'SELECT id, title, description, priority, status, parent_id, scope, created_at, updated_at, completed_at '
            'FROM goals WHERE parent_id IS NULL AND scope = ? ORDER BY updated_at DESC',
            (scope,)
        )
    else:
        cursor.execute(
            'SELECT id, title, description, priority, status, parent_id, scope, created_at, updated_at, completed_at '
            'FROM goals WHERE parent_id IS NULL AND scope = ? AND status = ? ORDER BY updated_at DESC',
            (scope, status)
        )
    rows = cursor.fetchall()

    goals = []
    for r in rows:
        gid = r[0]
        # Subtasks
        cursor.execute(
            'SELECT id, title, description, priority, status, created_at, updated_at FROM goals WHERE parent_id = ? ORDER BY created_at',
            (gid,)
        )
        subtasks = [{"id": s[0], "title": s[1], "description": s[2], "priority": s[3],
                      "status": s[4], "created_at": s[5], "updated_at": s[6]} for s in cursor.fetchall()]
        # Recent progress
        cursor.execute(
            'SELECT id, note, created_at FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC LIMIT 5',
            (gid,)
        )
        progress = [{"id": p[0], "note": p[1], "created_at": p[2]} for p in cursor.fetchall()]

        goals.append({
            "id": r[0], "title": r[1], "description": r[2], "priority": r[3],
            "status": r[4], "scope": r[6], "created_at": r[7], "updated_at": r[8],
            "completed_at": r[9], "subtasks": subtasks, "progress": progress
        })

    conn.close()
    return goals


def get_goal_detail(goal_id):
    """Get a single goal with all subtasks and full progress journal."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, title, description, priority, status, parent_id, scope, created_at, updated_at, completed_at '
        'FROM goals WHERE id = ?', (goal_id,)
    )
    r = cursor.fetchone()
    if not r:
        conn.close()
        return None

    cursor.execute(
        'SELECT id, title, description, priority, status, created_at, updated_at FROM goals WHERE parent_id = ? ORDER BY created_at',
        (goal_id,)
    )
    subtasks = [{"id": s[0], "title": s[1], "description": s[2], "priority": s[3],
                  "status": s[4], "created_at": s[5], "updated_at": s[6]} for s in cursor.fetchall()]

    cursor.execute(
        'SELECT id, note, created_at FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC',
        (goal_id,)
    )
    progress = [{"id": p[0], "note": p[1], "created_at": p[2]} for p in cursor.fetchall()]

    conn.close()
    return {
        "id": r[0], "title": r[1], "description": r[2], "priority": r[3],
        "status": r[4], "parent_id": r[5], "scope": r[6], "created_at": r[7],
        "updated_at": r[8], "completed_at": r[9], "subtasks": subtasks, "progress": progress
    }


def create_goal_api(title, description=None, priority='medium', parent_id=None, scope='default'):
    """Create a goal and return the new ID. Raises ValueError on validation failure."""
    if not title or not title.strip():
        raise ValueError("Title is required")
    title = title.strip()
    if len(title) > 200:
        raise ValueError("Title too long (max 200)")
    if description:
        description = description.strip()
        if len(description) > 500:
            raise ValueError("Description too long (max 500)")
    priority = (priority or 'medium').lower().strip()
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority '{priority}'")

    conn = _get_connection()
    cursor = conn.cursor()

    if parent_id is not None:
        cursor.execute('SELECT parent_id FROM goals WHERE id = ? AND scope = ?', (parent_id, scope))
        parent = cursor.fetchone()
        if not parent:
            conn.close()
            raise ValueError(f"Parent goal [{parent_id}] not found")
        if parent[0] is not None:
            conn.close()
            raise ValueError(f"Goal [{parent_id}] is already a subtask")

    cursor.execute(
        'INSERT INTO goals (title, description, priority, parent_id, scope) VALUES (?, ?, ?, ?, ?)',
        (title, description, priority, parent_id, scope)
    )
    goal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return goal_id


def update_goal_api(goal_id, **kwargs):
    """Update goal fields. Returns True on success. Raises ValueError on failure."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM goals WHERE id = ?', (goal_id,))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Goal [{goal_id}] not found")

    updates, params = [], []
    for field in ('title', 'description', 'priority', 'status'):
        val = kwargs.get(field)
        if val is not None:
            if field == 'priority' and val not in VALID_PRIORITIES:
                conn.close()
                raise ValueError(f"Invalid priority '{val}'")
            if field == 'status' and val not in VALID_STATUSES:
                conn.close()
                raise ValueError(f"Invalid status '{val}'")
            updates.append(f'{field} = ?')
            params.append(val)
            if field == 'status' and val == 'completed':
                updates.append('completed_at = ?')
                params.append(datetime.now().isoformat())
            elif field == 'status' and val == 'active':
                updates.append('completed_at = NULL')

    if not updates and 'progress_note' not in kwargs:
        conn.close()
        raise ValueError("Nothing to update")

    updates.append('updated_at = ?')
    params.append(datetime.now().isoformat())
    params.append(goal_id)
    cursor.execute(f'UPDATE goals SET {", ".join(updates)} WHERE id = ?', params)

    progress_note = kwargs.get('progress_note')
    if progress_note:
        cursor.execute('INSERT INTO goal_progress (goal_id, note) VALUES (?, ?)', (goal_id, progress_note.strip()))

    conn.commit()
    conn.close()
    return True


def add_progress_note(goal_id, note):
    """Add a progress note to a goal. Returns the note ID."""
    if not note or not note.strip():
        raise ValueError("Note cannot be empty")
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM goals WHERE id = ?', (goal_id,))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Goal [{goal_id}] not found")
    cursor.execute('INSERT INTO goal_progress (goal_id, note) VALUES (?, ?)', (goal_id, note.strip()))
    note_id = cursor.lastrowid
    cursor.execute('UPDATE goals SET updated_at = ? WHERE id = ?', (datetime.now().isoformat(), goal_id))
    conn.commit()
    conn.close()
    return note_id


def delete_goal_api(goal_id, cascade=True):
    """Delete a goal. Returns the deleted title. Raises ValueError if not found."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT title FROM goals WHERE id = ?', (goal_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Goal [{goal_id}] not found")
    title = row[0]

    if not cascade:
        cursor.execute('UPDATE goals SET parent_id = NULL WHERE parent_id = ?', (goal_id,))

    cursor.execute('DELETE FROM goal_progress WHERE goal_id = ?', (goal_id,))
    if cascade:
        cursor.execute('DELETE FROM goal_progress WHERE goal_id IN (SELECT id FROM goals WHERE parent_id = ?)', (goal_id,))
        cursor.execute('DELETE FROM goals WHERE parent_id = ?', (goal_id,))
    cursor.execute('DELETE FROM goals WHERE id = ?', (goal_id,))
    conn.commit()
    conn.close()
    return title


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _time_ago(timestamp_str):
    try:
        ts = datetime.fromisoformat(timestamp_str)
        diff = datetime.now() - ts
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        if days > 13:
            return f"{days // 7}w ago"
        if days > 0:
            return f"{days}d ago"
        if hours > 0:
            return f"{hours}h ago"
        if minutes > 0:
            return f"{minutes}m ago"
        return "just now"
    except Exception:
        return ""


def _priority_marker(priority):
    return {'high': '!!!', 'medium': '!!', 'low': '!'}.get(priority, '!!')


def _format_goal_full(goal, subtasks, progress_notes):
    """Format a goal with full subtask list and recent progress."""
    gid, title, desc, priority, status, parent_id, scope, created, updated, completed = goal
    ago = _time_ago(updated)

    lines = [f"[{gid}] {title} ({priority}) — updated {ago}"]
    if desc:
        lines.append(f'    "{desc}"')

    if subtasks:
        lines.append("    Subtasks:")
        for s in subtasks:
            sid, stitle, spri, sstatus = s
            mark = 'x' if sstatus == 'completed' else '-' if sstatus == 'abandoned' else ' '
            lines.append(f"      [{mark}] [{sid}] {stitle} ({sstatus})")
    else:
        lines.append("    (no subtasks)")

    if progress_notes:
        lines.append("    Recent progress:")
        for note, note_time in progress_notes[:3]:
            lines.append(f"      * {_time_ago(note_time)}: {note}")
    else:
        lines.append("    (no progress logged)")

    return '\n'.join(lines)


def _format_goal_summary(goal, subtask_count, subtask_done):
    """One-line summary for the compact section."""
    gid, title, priority, status, updated = goal
    ago = _time_ago(updated)
    sub_info = f" — {subtask_count} subtasks, {subtask_done} done" if subtask_count else " — no subtasks"
    return f"[{gid}] {title} ({priority}){sub_info} — {ago}"


# ─── Validation ───────────────────────────────────────────────────────────────

def _validate_priority(priority):
    if priority and priority not in VALID_PRIORITIES:
        return f"Invalid priority '{priority}'. Choose from: {', '.join(VALID_PRIORITIES)}."
    return None


def _validate_status(status):
    if status and status not in VALID_STATUSES:
        return f"Invalid status '{status}'. Choose from: {', '.join(VALID_STATUSES)}."
    return None


def _validate_goal_exists(cursor, goal_id, scope=None):
    if not isinstance(goal_id, int) or goal_id < 1:
        return None, f"Invalid goal_id '{goal_id}'. Must be a positive integer (shown in brackets like [5])."
    if scope:
        cursor.execute('SELECT * FROM goals WHERE id = ? AND scope = ?', (goal_id, scope))
    else:
        cursor.execute('SELECT * FROM goals WHERE id = ?', (goal_id,))
    row = cursor.fetchone()
    if not row:
        scope_note = f" in scope '{scope}'" if scope else ""
        return None, f"Goal [{goal_id}] not found{scope_note}. Use list_goals to see available goals."
    return row, None


def _validate_length(value, field_name, max_len):
    if value and len(value) > max_len:
        return f"{field_name} too long ({len(value)} chars). Maximum is {max_len} characters. Please shorten it."
    return None


# ─── Operations ───────────────────────────────────────────────────────────────

def _create_goal(title, description=None, priority='medium', parent_id=None, scope='default'):
    if not title or not title.strip():
        return "Cannot create a goal without a title. Provide a clear, short title.", False

    title = title.strip()
    err = _validate_length(title, 'Title', 200)
    if err:
        return err, False

    if description:
        description = description.strip()
        err = _validate_length(description, 'Description', 500)
        if err:
            return err, False

    priority = (priority or 'medium').lower().strip()
    err = _validate_priority(priority)
    if err:
        return err, False

    conn = _get_connection()
    cursor = conn.cursor()

    if parent_id is not None:
        parent, err = _validate_goal_exists(cursor, parent_id, scope)
        if err:
            conn.close()
            return f"Cannot create subtask: {err}", False
        if parent[5] is not None:  # parent's parent_id
            conn.close()
            return f"Goal [{parent_id}] is already a subtask. Subtasks can only be one level deep — nest under the top-level goal [{parent[5]}] instead.", False

    cursor.execute(
        'INSERT INTO goals (title, description, priority, parent_id, scope) VALUES (?, ?, ?, ?, ?)',
        (title, description, priority, parent_id, scope)
    )
    goal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    kind = "Subtask" if parent_id else "Goal"
    parent_note = f" under goal [{parent_id}]" if parent_id else ""
    logger.info(f"Created {kind.lower()} [{goal_id}] '{title}' ({priority}) in scope '{scope}'{parent_note}")
    return f"{kind} created: [{goal_id}] {title} ({priority}){parent_note}", True


def _list_goals(goal_id=None, status='active', scope='default'):
    conn = _get_connection()
    cursor = conn.cursor()

    # Deep view for a specific goal
    if goal_id is not None:
        goal, err = _validate_goal_exists(cursor, goal_id, scope)
        if err:
            conn.close()
            return err, False

        # Subtasks
        cursor.execute(
            'SELECT id, title, priority, status FROM goals WHERE parent_id = ? ORDER BY created_at',
            (goal_id,)
        )
        subtasks = cursor.fetchall()

        # All progress notes
        cursor.execute(
            'SELECT note, created_at FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC',
            (goal_id,)
        )
        progress = cursor.fetchall()
        conn.close()

        output = _format_goal_full(goal, subtasks, progress)
        if len(progress) > 3:
            # Full journal for deep view
            output += "\n    Full progress journal:"
            for note, note_time in progress:
                output += f"\n      * {_time_ago(note_time)}: {note}"
        return output, True

    # Smart listing
    status_filter = status.lower().strip() if status else 'active'
    if status_filter not in ('active', 'completed', 'abandoned', 'all'):
        conn.close()
        return f"Invalid status filter '{status_filter}'. Choose from: active, completed, abandoned, all.", False

    # For 'all' view: split into sections by status
    if status_filter == 'all':
        return _list_goals_all(cursor, conn, scope)

    cursor.execute(
        'SELECT * FROM goals WHERE parent_id IS NULL AND scope = ? AND status = ? ORDER BY updated_at DESC',
        (scope, status_filter)
    )
    top_level = cursor.fetchall()

    if not top_level:
        conn.close()
        label = f" ({status_filter})" if status_filter != 'active' else ""
        return f"No{label} goals in scope '{scope}'. Use create_goal to start planning.", True

    # Split: first 3 full, rest summarized
    full_goals = top_level[:3]
    summary_goals = top_level[3:10]

    lines = [f"=== {status_filter.capitalize()} Goals (scope: {scope}) ===\n"]

    for goal in full_goals:
        gid = goal[0]
        cursor.execute(
            'SELECT id, title, priority, status FROM goals WHERE parent_id = ? ORDER BY created_at',
            (gid,)
        )
        subtasks = cursor.fetchall()
        cursor.execute(
            'SELECT note, created_at FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC LIMIT 3',
            (gid,)
        )
        progress = cursor.fetchall()
        lines.append(_format_goal_full(goal, subtasks, progress))
        lines.append("")

    if summary_goals:
        lines.append(f"--- Also {status_filter} ({len(summary_goals)} more) ---")
        for goal in summary_goals:
            gid = goal[0]
            cursor.execute('SELECT COUNT(*) FROM goals WHERE parent_id = ?', (gid,))
            sub_count = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM goals WHERE parent_id = ? AND status = ?', (gid, 'completed'))
            sub_done = cursor.fetchone()[0]
            summary = (gid, goal[1], goal[3], goal[4], goal[8])  # id, title, priority, status, updated
            lines.append(_format_goal_summary(summary, sub_count, sub_done))

    remaining = len(top_level) - 10
    if remaining > 0:
        lines.append(f"... and {remaining} more (use list_goals with goal_id for details)")

    # Append recently completed goals when showing active view (the dashboard)
    if status_filter == 'active':
        _append_recently_completed(cursor, lines, scope)

    conn.close()
    return '\n'.join(lines), True


def _append_recently_completed(cursor, lines, scope, limit=5):
    """Append a recently completed section to the output lines."""
    cursor.execute(
        'SELECT id, title, completed_at FROM goals WHERE parent_id IS NULL AND scope = ? AND status = ? ORDER BY completed_at DESC LIMIT ?',
        (scope, 'completed', limit)
    )
    completed = cursor.fetchall()
    if not completed:
        return
    lines.append("")
    lines.append("--- Recently Completed ---")
    for gid, gtitle, completed_at in completed:
        cursor.execute(
            'SELECT note FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC LIMIT 1',
            (gid,)
        )
        last_note = cursor.fetchone()
        ago = _time_ago(completed_at) if completed_at else ""
        note_preview = ""
        if last_note and last_note[0]:
            preview = last_note[0][:150] + ('...' if len(last_note[0]) > 150 else '')
            note_preview = f"\n      {preview}"
        lines.append(f"  [x] [{gid}] {gtitle} — completed {ago}{note_preview}")


def _list_goals_all(cursor, conn, scope):
    """Show all goals split into clear Active / Completed / Abandoned sections."""
    lines = [f"=== All Goals (scope: {scope}) ==="]

    # ── Active section ──
    cursor.execute(
        'SELECT * FROM goals WHERE parent_id IS NULL AND scope = ? AND status = ? ORDER BY updated_at DESC',
        (scope, 'active')
    )
    active = cursor.fetchall()

    if active:
        lines.append("")
        lines.append(f"# Active ({len(active)})")
        # Top 3 full
        for goal in active[:3]:
            gid = goal[0]
            cursor.execute('SELECT id, title, priority, status FROM goals WHERE parent_id = ? ORDER BY created_at', (gid,))
            subtasks = cursor.fetchall()
            cursor.execute('SELECT note, created_at FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC LIMIT 3', (gid,))
            progress = cursor.fetchall()
            lines.append(_format_goal_full(goal, subtasks, progress))
            lines.append("")
        # Rest summarized
        for goal in active[3:10]:
            gid = goal[0]
            cursor.execute('SELECT COUNT(*) FROM goals WHERE parent_id = ?', (gid,))
            sub_count = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM goals WHERE parent_id = ? AND status = ?', (gid, 'completed'))
            sub_done = cursor.fetchone()[0]
            summary = (gid, goal[1], goal[3], goal[4], goal[8])
            lines.append(_format_goal_summary(summary, sub_count, sub_done))
        if len(active) > 10:
            lines.append(f"... and {len(active) - 10} more active")
    else:
        lines.append("\n# Active (0)")

    # ── Completed section ──
    cursor.execute(
        'SELECT id, title, completed_at FROM goals WHERE parent_id IS NULL AND scope = ? AND status = ? ORDER BY completed_at DESC LIMIT 10',
        (scope, 'completed')
    )
    completed = cursor.fetchall()

    if completed:
        lines.append("")
        lines.append(f"# Completed ({len(completed)})")
        for gid, gtitle, completed_at in completed:
            cursor.execute('SELECT note FROM goal_progress WHERE goal_id = ? ORDER BY created_at DESC LIMIT 1', (gid,))
            last_note = cursor.fetchone()
            ago = _time_ago(completed_at) if completed_at else ""
            note_preview = ""
            if last_note and last_note[0]:
                preview = last_note[0][:150] + ('...' if len(last_note[0]) > 150 else '')
                note_preview = f"\n      {preview}"
            lines.append(f"  [x] [{gid}] {gtitle} — completed {ago}{note_preview}")

    # ── Abandoned section ──
    cursor.execute(
        'SELECT id, title, updated_at FROM goals WHERE parent_id IS NULL AND scope = ? AND status = ? ORDER BY updated_at DESC LIMIT 5',
        (scope, 'abandoned')
    )
    abandoned = cursor.fetchall()

    if abandoned:
        lines.append("")
        lines.append(f"# Abandoned ({len(abandoned)})")
        for gid, gtitle, updated_at in abandoned:
            ago = _time_ago(updated_at) if updated_at else ""
            lines.append(f"  [-] [{gid}] {gtitle} — {ago}")

    conn.close()
    return '\n'.join(lines), True


def _update_goal(goal_id, scope='default', **kwargs):
    if not isinstance(goal_id, int) or goal_id < 1:
        return f"Invalid goal_id '{goal_id}'. Must be a positive integer (shown in brackets like [5]).", False

    conn = _get_connection()
    cursor = conn.cursor()

    goal, err = _validate_goal_exists(cursor, goal_id, scope)
    if err:
        conn.close()
        return err, False

    title = kwargs.get('title')
    description = kwargs.get('description')
    priority = kwargs.get('priority')
    status = kwargs.get('status')
    progress_note = kwargs.get('progress_note')

    # Validate all fields before touching anything
    if title is not None:
        title = title.strip()
        if not title:
            conn.close()
            return "Title cannot be empty. Provide a clear, short title.", False
        err = _validate_length(title, 'Title', 200)
        if err:
            conn.close()
            return err, False

    if description is not None:
        description = description.strip()
        err = _validate_length(description, 'Description', 500)
        if err:
            conn.close()
            return err, False

    if priority is not None:
        priority = priority.lower().strip()
        err = _validate_priority(priority)
        if err:
            conn.close()
            return err, False

    if status is not None:
        status = status.lower().strip()
        err = _validate_status(status)
        if err:
            conn.close()
            return err, False

    if progress_note is not None:
        progress_note = progress_note.strip()
        if not progress_note:
            conn.close()
            return "Progress note cannot be empty. Describe what was done or learned.", False
        err = _validate_length(progress_note, 'Progress note', 1024)
        if err:
            conn.close()
            return err, False

    # Check at least one field is being updated
    has_update = any(v is not None for v in [title, description, priority, status, progress_note])
    if not has_update:
        conn.close()
        return "Nothing to update. Pass at least one field: title, description, priority, status, or progress_note.", False

    # Apply field updates
    updates = []
    params = []
    if title is not None:
        updates.append('title = ?')
        params.append(title)
    if description is not None:
        updates.append('description = ?')
        params.append(description)
    if priority is not None:
        updates.append('priority = ?')
        params.append(priority)
    if status is not None:
        updates.append('status = ?')
        params.append(status)
        if status == 'completed':
            updates.append('completed_at = ?')
            params.append(datetime.now().isoformat())
        elif status == 'active':
            updates.append('completed_at = NULL')

    # Always bump updated_at
    updates.append('updated_at = ?')
    params.append(datetime.now().isoformat())
    params.append(goal_id)

    cursor.execute(f'UPDATE goals SET {", ".join(updates)} WHERE id = ?', params)

    # Append progress note
    if progress_note:
        cursor.execute(
            'INSERT INTO goal_progress (goal_id, note) VALUES (?, ?)',
            (goal_id, progress_note)
        )

    conn.commit()

    # Check if completing the last subtask of a parent goal
    parent_hint = ""
    if status == 'completed' and goal[5] is not None:  # goal[5] = parent_id
        parent_id = goal[5]
        cursor.execute(
            'SELECT COUNT(*) FROM goals WHERE parent_id = ? AND status != ?',
            (parent_id, 'completed')
        )
        remaining = cursor.fetchone()[0]
        if remaining == 0:
            cursor.execute('SELECT title FROM goals WHERE id = ?', (parent_id,))
            parent_row = cursor.fetchone()
            if parent_row:
                parent_hint = f"\n\nAll subtasks for [{parent_id}] \"{parent_row[0]}\" are now complete. If the goal is finished, mark it complete with update_goal(goal_id={parent_id}, status='completed')."

    conn.close()

    # Build response
    changes = []
    if title is not None:
        changes.append(f"title → '{title}'")
    if priority is not None:
        changes.append(f"priority → {priority}")
    if status is not None:
        changes.append(f"status → {status}")
    if description is not None:
        changes.append("description updated")
    if progress_note:
        changes.append(f"logged: {progress_note[:80]}{'...' if len(progress_note) > 80 else ''}")

    logger.info(f"Updated goal [{goal_id}]: {', '.join(changes)}")
    return f"Goal [{goal_id}] updated: {', '.join(changes)}{parent_hint}", True


def _delete_goal(goal_id, cascade=True, scope='default'):
    if not isinstance(goal_id, int) or goal_id < 1:
        return f"Invalid goal_id '{goal_id}'. Must be a positive integer (shown in brackets like [5]).", False

    conn = _get_connection()
    cursor = conn.cursor()

    goal, err = _validate_goal_exists(cursor, goal_id, scope)
    if err:
        conn.close()
        return err, False

    title = goal[1]

    # Check for subtasks
    cursor.execute('SELECT COUNT(*) FROM goals WHERE parent_id = ?', (goal_id,))
    subtask_count = cursor.fetchone()[0]

    if subtask_count > 0 and not cascade:
        # Orphan subtasks → promote to top-level
        cursor.execute('UPDATE goals SET parent_id = NULL WHERE parent_id = ?', (goal_id,))
        logger.info(f"Promoted {subtask_count} subtasks of [{goal_id}] to top-level goals")

    # Delete progress notes (cascade handles this if FK is on, but be explicit)
    cursor.execute('DELETE FROM goal_progress WHERE goal_id = ?', (goal_id,))

    if subtask_count > 0 and cascade:
        # Delete subtask progress notes too
        cursor.execute(
            'DELETE FROM goal_progress WHERE goal_id IN (SELECT id FROM goals WHERE parent_id = ?)',
            (goal_id,)
        )
        cursor.execute('DELETE FROM goals WHERE parent_id = ?', (goal_id,))

    cursor.execute('DELETE FROM goals WHERE id = ?', (goal_id,))
    conn.commit()
    conn.close()

    sub_note = ""
    if subtask_count > 0:
        sub_note = f" and {subtask_count} subtask(s)" if cascade else f" ({subtask_count} subtasks promoted to top-level)"

    logger.info(f"Deleted goal [{goal_id}] '{title}'{sub_note}")
    return f"Deleted goal [{goal_id}] '{title}'{sub_note}", True


# ─── Executor ─────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        scope = _get_current_scope()
        if scope is None:
            return "Goals are disabled when memory is disabled for this chat.", False

        if function_name == "create_goal":
            return _create_goal(
                title=arguments.get('title'),
                description=arguments.get('description'),
                priority=arguments.get('priority', 'medium'),
                parent_id=arguments.get('parent_id'),
                scope=arguments.get('scope', scope),
            )

        elif function_name == "list_goals":
            goal_id = arguments.get('goal_id')
            if goal_id is not None:
                try:
                    goal_id = int(goal_id)
                except (ValueError, TypeError):
                    return f"Invalid goal_id '{goal_id}'. Must be an integer (shown in brackets like [5]).", False
            return _list_goals(
                goal_id=goal_id,
                status=arguments.get('status', 'active'),
                scope=arguments.get('scope', scope),
            )

        elif function_name == "update_goal":
            goal_id = arguments.get('goal_id')
            if goal_id is None:
                return "Missing goal_id. Which goal do you want to update? Use list_goals to see your goals.", False
            try:
                goal_id = int(goal_id)
            except (ValueError, TypeError):
                return f"Invalid goal_id '{goal_id}'. Must be an integer (shown in brackets like [5]).", False
            return _update_goal(
                goal_id=goal_id,
                scope=arguments.get('scope', scope),
                title=arguments.get('title'),
                description=arguments.get('description'),
                priority=arguments.get('priority'),
                status=arguments.get('status'),
                progress_note=arguments.get('progress_note'),
            )

        elif function_name == "delete_goal":
            goal_id = arguments.get('goal_id')
            if goal_id is None:
                return "Missing goal_id. Which goal do you want to delete? Use list_goals to see your goals.", False
            try:
                goal_id = int(goal_id)
            except (ValueError, TypeError):
                return f"Invalid goal_id '{goal_id}'. Must be an integer (shown in brackets like [5]).", False
            cascade = arguments.get('cascade', True)
            if not isinstance(cascade, bool):
                return f"Invalid cascade value '{cascade}'. Must be true or false.", False
            return _delete_goal(
                goal_id=goal_id,
                cascade=cascade,
                scope=arguments.get('scope', scope),
            )

        else:
            return f"Unknown goal function '{function_name}'. Available: {', '.join(AVAILABLE_FUNCTIONS)}.", False

    except Exception as e:
        logger.error(f"Goal function error in {function_name}: {e}", exc_info=True)
        return f"Goal system error: {str(e)}", False
