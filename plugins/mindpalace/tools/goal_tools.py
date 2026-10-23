# plugins/mindpalace/tools/goal_tools.py
# Layer 4 — goals, palace edition (2026-07-11). Same four tool names and
# contracts as the classic goals system (mutually exclusive plugins), but
# chunks-backed so goals live INSIDE the graph: search_memory finds them,
# mention edges weave them to people/places, and the spider can walk to them.
#
# Shape (tolerant reads — a bare goals-layer chunk is a valid active goal):
#   goal chunk     layer='goals', content = title
#                  meta: description, priority, goal_status, permanent,
#                        completed_at
#   subtask chunk  meta.parent_goal = parent id + 'subtask_of' edge (1 level)
#   note chunk     meta.progress_of = goal id + 'progress_of' edge
# Permanent goals carry importance 0.95 — the never-fades band. The permanent
# flag and the librarian charter converge on the same mechanism.
#
# Scope: the ONE palace memory scope (classic's separate goal_scope key is
# retired with the classic plugin — fewer universes).

import json
import logging
import re

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎯'
GROUP = 'Mind Palace'   # Toolsets UI merges same-GROUP modules

TITLE_MAX = 200
DESC_MAX = 500
NOTE_MAX = 1024
INSTR_MAX = 2000
PERMANENT_IMPORTANCE = 0.95
VALID_PRIORITIES = ('high', 'medium', 'low')
VALID_STATUSES = ('active', 'in_progress', 'completed', 'abandoned')
PRIO_RANK = {'high': 0, 'medium': 1, 'low': 2}
_DUE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
OVERVIEW_CAP = 15

AVAILABLE_FUNCTIONS = ['create_goal', 'list_goals', 'update_goal', 'delete_goal']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "create_goal",
            "description": ("Create a goal.\n  parent_id=N — subtask under goal N\n"
                            "  (none) — top-level goal"),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": f"Title (max {TITLE_MAX} chars)"},
                    "description": {"type": "string", "description": f"Context / success criteria (max {DESC_MAX} chars)"},
                    "instructions": {"type": "string", "description": f"HOW to do it — step notes for whoever executes. Shown in the goal_id deep view (max {INSTR_MAX} chars)."},
                    "priority": {"type": "string", "description": "high | medium | low (default medium)"},
                    "due": {"type": "string", "description": "Optional due date, YYYY-MM-DD"},
                    "parent_id": {"type": "integer", "description": "Parent goal id for nesting"},
                    "permanent": {"type": "boolean", "description": "Standing goal — cannot be completed/deleted. For ongoing duties. Default false."}
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
            "description": ("List goals or deep-view one.\n  goal_id=N — full detail + "
                            "subtasks + journal\n  (none) — active overview"),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "integer", "description": "Goal id for deep view"},
                    "status": {"type": "string", "description": "active | in_progress | completed | abandoned | all (default active; active includes in_progress)"}
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "update_goal",
            "description": "Update a goal. Pass any fields to change. progress_note appends (not replaces).",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "integer", "description": "Goal id (shown as [N])"},
                    "title": {"type": "string", "description": f"New title (max {TITLE_MAX} chars)"},
                    "description": {"type": "string", "description": f"New description (max {DESC_MAX} chars)"},
                    "instructions": {"type": "string", "description": f"HOW to do it — step notes (max {INSTR_MAX} chars, empty string clears)"},
                    "priority": {"type": "string", "description": "high | medium | low"},
                    "status": {"type": "string", "description": "active | in_progress | completed | abandoned"},
                    "due": {"type": "string", "description": "Due date YYYY-MM-DD (empty string clears)"},
                    "progress_note": {"type": "string", "description": f"Timestamped journal entry — appended (max {NOTE_MAX} chars)"}
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
            "description": "Delete a goal and its subtasks/journal. Use update_goal(status='abandoned') to keep history instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "integer", "description": "Goal id"}
                },
                "required": ["goal_id"]
            }
        }
    },
]


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _md():
    from plugins.mindpalace.tools import metadata
    return metadata


def _meta_of(raw):
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _is_goal(meta):
    """Top-level goal = goals-layer chunk that is neither a note nor a subtask."""
    return not meta.get('progress_of') and not meta.get('parent_goal')


def _status_of(meta):
    return meta.get('goal_status') or 'active'


def _status_match(want, st):
    """'active' folds in_progress under it — a started task is still open."""
    return st == want or (want == 'active' and st == 'in_progress')


def _prio_sort(items):
    """Stable: rows arrive in recency/created order; priority groups on top of
    that, so high-priority-recently-touched leads."""
    return sorted(items, key=lambda g: PRIO_RANK.get(
        g['meta'].get('priority') or 'medium', 1))


def _check_due(due):
    """Returns (normalized_or_None, error_or_None). Empty string = clear."""
    due = (due or '').strip()
    if not due:
        return None, None
    if not _DUE_RE.match(due):
        return None, f"Due date must be YYYY-MM-DD, got '{due}'."
    return due, None


# ─── Storage (shared with routes/goals_routes.py) ────────────────────────────

def _insert_goal_chunk(cursor, pt, scope, title, meta, created=None,
                       private_key=None):
    """One goal/subtask/note chunk: standard save-time metadata + mention
    edges (goals name people — the graph should know)."""
    md = _md()
    now = pt._now()
    created = created or now
    mention_ids = []
    try:
        arows = md.entity_aliases(cursor, scope)
        amap = md.alias_map(arows)
        hits = md.match_entities(title, [a for _, _, a in arows])
        seen = set()
        for h in hits:
            pair = amap.get(h.lower())
            if pair and pair[0] not in seen:
                seen.add(pair[0])
                mention_ids.append(pair[0])
        base = md.save_meta(title, exclude_names={h.lower() for h in hits})
    except Exception as e:
        logger.warning(f"[GOALS] Meta stamping failed (write continues): {e}")
        base = {}
    base['added_by'] = pt._added_by()
    base.update(meta)
    importance = PERMANENT_IMPORTANCE if meta.get('permanent') else None
    cursor.execute(
        "INSERT INTO chunks (layer, scope, content, importance, private_key, "
        "meta, created, updated) VALUES ('goals', ?, ?, ?, ?, ?, ?, ?)",
        (scope, title, importance, private_key,
         json.dumps(base, ensure_ascii=False), created, now))
    cid = cursor.lastrowid
    if mention_ids:
        try:
            md.seed_edges(cursor, cid, mention_ids, now)
        except Exception as e:
            logger.warning(f"[GOALS] Edge seeding failed (write continues): {e}")
    return cid


def _link(cursor, src_id, dst_id, kind, now):
    cursor.execute(
        "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
        "VALUES ('chunk', ?, 'chunk', ?, ?, 1.0, ?)", (src_id, dst_id, kind, now))


def _get_goal(cursor, gid, scope):
    row = cursor.execute(
        "SELECT id, content, meta, created, updated FROM chunks "
        "WHERE id = ? AND layer = 'goals' AND scope IN (?, 'global')",
        (int(gid), scope)).fetchone()
    if not row:
        return None
    return {'id': row[0], 'title': row[1], 'meta': _meta_of(row[2]),
            'created': row[3], 'updated': row[4]}


def _write_meta(cursor, pt, gid, meta, importance='keep'):
    now = pt._now()
    if importance == 'keep':
        cursor.execute('UPDATE chunks SET meta = ?, updated = ? WHERE id = ?',
                       (json.dumps(meta, ensure_ascii=False), now, gid))
    else:
        cursor.execute('UPDATE chunks SET meta = ?, importance = ?, updated = ? '
                       'WHERE id = ?',
                       (json.dumps(meta, ensure_ascii=False), importance, now, gid))


def _subtasks_of(cursor, gid, scope):
    return _prio_sort(
        [{'id': r[0], 'title': r[1], 'meta': _meta_of(r[2]), 'created': r[3]}
         for r in cursor.execute(
             "SELECT id, content, meta, created FROM chunks "
             "WHERE layer = 'goals' AND scope IN (?, 'global') "
             "AND json_extract(meta, '$.parent_goal') = ? ORDER BY created",
             (scope, gid)).fetchall()])


def _notes_of(cursor, gid, scope, limit=None):
    sql = ("SELECT id, content, created FROM chunks "
           "WHERE layer = 'goals' AND scope IN (?, 'global') "
           "AND json_extract(meta, '$.progress_of') = ? ORDER BY created DESC")
    params = [scope, gid]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [{'id': r[0], 'note': r[1], 'created': r[2]}
            for r in cursor.execute(sql, params).fetchall()]


def _top_level(cursor, scope, status=None):
    rows = cursor.execute(
        "SELECT id, content, meta, created, updated FROM chunks "
        "WHERE layer = 'goals' AND scope IN (?, 'global') "
        "ORDER BY updated DESC", (scope,)).fetchall()
    out = []
    for r in rows:
        meta = _meta_of(r[2])
        if not _is_goal(meta):
            continue
        if status and status != 'all' and not _status_match(status, _status_of(meta)):
            continue
        out.append({'id': r[0], 'title': r[1], 'meta': meta,
                    'created': r[3], 'updated': r[4]})
    return _prio_sort(out)


def delete_goal_cascade(cursor, gid):
    """Hard delete: goal + its subtasks + all progress notes + their edges.
    (Soft path is update_goal(status='abandoned') — history kept.)"""
    ids = [gid]
    ids += [r[0] for r in cursor.execute(
        "SELECT id FROM chunks WHERE layer = 'goals' AND ("
        "json_extract(meta, '$.parent_goal') = ? OR "
        "json_extract(meta, '$.progress_of') = ?)", (gid, gid)).fetchall()]
    ph = ','.join('?' * len(ids))
    cursor.execute(f"DELETE FROM edges WHERE (src_type = 'chunk' AND src_id IN ({ph})) "
                   f"OR (dst_type = 'chunk' AND dst_id IN ({ph}))", ids + ids)
    cursor.execute(f"DELETE FROM chunks WHERE id IN ({ph})", ids)
    return len(ids)


# ─── Formatting ──────────────────────────────────────────────────────────────

_SUB_MARK = {'completed': 'x', 'abandoned': '-', 'in_progress': '~'}


def _fmt_goal(cursor, g, scope, full=False):
    """Goals render WHOLE — subtask descriptions included (they used to go to
    storage and die there). `full` adds instructions + the complete journal."""
    meta = g['meta']
    bits = [meta.get('priority') or 'medium']
    if meta.get('permanent'):
        bits.append('PERMANENT')
    st = _status_of(meta)
    if st != 'active':
        bits.append(st.replace('_', ' '))
    if meta.get('due'):
        bits.append(f"due {meta['due']}")
    subs = _subtasks_of(cursor, g['id'], scope)
    if subs:
        done = sum(1 for s in subs if _status_of(s['meta']) == 'completed')
        bits.append(f"{done}/{len(subs)} subtasks done")
    lines = [f"[{g['id']}] {g['title']} ({', '.join(bits)})"]
    if meta.get('description'):
        lines.append(f'    "{meta["description"]}"')
    if full and meta.get('instructions'):
        lines.append(f"    How: {meta['instructions']}")
    for s in subs:
        sm = s['meta']
        mark = _SUB_MARK.get(_status_of(sm), ' ')
        due = f" (due {sm['due']})" if sm.get('due') else ""
        pr = sm.get('priority')
        pr = f" ({pr})" if pr and pr != 'medium' else ""
        lines.append(f"      [{mark}] [{s['id']}] {s['title']}{pr}{due}")
        if sm.get('description'):
            lines.append(f'          "{sm["description"]}"')
        if full and sm.get('instructions'):
            lines.append(f"          How: {sm['instructions']}")
    notes = _notes_of(cursor, g['id'], scope, limit=None if full else 3)
    for n in notes:
        lines.append(f"      * {n['created'][:10]}: {n['note']}")
    return "\n".join(lines)


def present_search_hits(cursor, scope, ids, cap=OVERVIEW_CAP):
    """search_memory layer='goals' detour: every hit (goal, subtask, or
    journal note) resolves to its top-level goal, rendered whole — she reached
    for the goals category; five bare titles would just cost her a second
    call. Order-preserving dedup, first hits win."""
    seen, out = set(), []
    for cid in ids:
        g = _get_goal(cursor, cid, scope)
        if not g:
            continue
        root = g['meta'].get('parent_goal') or g['meta'].get('progress_of')
        if root:
            g = _get_goal(cursor, root, scope)
            if not g:
                continue
        if g['id'] in seen:
            continue
        seen.add(g['id'])
        out.append(_fmt_goal(cursor, g, scope))
        if len(out) >= cap:
            break
    return out


# ─── Operations ──────────────────────────────────────────────────────────────

def _create(scope, title, description=None, priority='medium', parent_id=None,
            permanent=False, instructions=None, due=None, subtasks=None):
    """subtasks: optional list of titles created under the new goal in the
    same transaction — the UI's bulk-load path (one per textarea line)."""
    pt = _pt()
    title = (title or '').strip()
    if not title:
        return "Cannot create a goal without a title.", False
    if len(title) > TITLE_MAX:
        return f"Title too long ({len(title)} > {TITLE_MAX}).", False
    description = (description or '').strip() or None
    if description and len(description) > DESC_MAX:
        return f"Description too long ({len(description)} > {DESC_MAX}).", False
    instructions = (instructions or '').strip() or None
    if instructions and len(instructions) > INSTR_MAX:
        return f"Instructions too long ({len(instructions)} > {INSTR_MAX}).", False
    due, err = _check_due(due)
    if err:
        return err, False
    priority = (priority or 'medium').lower().strip()
    if priority not in VALID_PRIORITIES:
        return f"Invalid priority '{priority}' — high | medium | low.", False

    meta = {'goal_status': 'active', 'priority': priority}
    if description:
        meta['description'] = description
    if instructions:
        meta['instructions'] = instructions
    if due:
        meta['due'] = due
    if permanent:
        meta['permanent'] = True

    sub_titles = [s.strip()[:TITLE_MAX] for s in (subtasks or []) if s and s.strip()]
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        if parent_id is not None:
            parent = _get_goal(cursor, parent_id, scope)
            if not parent:
                return f"Parent goal [{parent_id}] not found.", False
            if parent['meta'].get('parent_goal'):
                return (f"Goal [{parent_id}] is already a subtask — subtasks "
                        f"nest one level only."), False
            meta['parent_goal'] = int(parent_id)
        gid = _insert_goal_chunk(cursor, pt, scope, title, meta)
        if parent_id is not None:
            _link(cursor, gid, int(parent_id), 'subtask_of', pt._now())
        if parent_id is None:
            for st_title in sub_titles:
                sid = _insert_goal_chunk(
                    cursor, pt, scope, st_title,
                    {'goal_status': 'active', 'priority': 'medium',
                     'parent_goal': gid})
                _link(cursor, sid, gid, 'subtask_of', pt._now())
        conn.commit()
    pt.reset_backfill_latch()
    pt._publish_mind('goals', scope, 'save')
    kind = "Subtask" if parent_id else "Goal"
    pt._ledger(scope, pt._added_by(), 'saved', layer='goals', target=gid,
               summary=f"{kind.lower()}: {title[:120]}")
    perm = " [PERMANENT]" if permanent else ""
    where = f" under [{parent_id}]" if parent_id else ""
    extra = f" +{len(sub_titles)} subtasks" if (sub_titles and parent_id is None) else ""
    return f"{kind} created: [{gid}] {title} ({priority}){perm}{where}{extra}", True


def _list(scope, goal_id=None, status='active'):
    pt = _pt()
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        if goal_id is not None:
            g = _get_goal(cursor, goal_id, scope)
            if not g or not _is_goal(g['meta']):
                return f"Goal [{goal_id}] not found — use list_goals to see them.", False
            return _fmt_goal(cursor, g, scope, full=True), True
        status = (status or 'active').lower().strip()
        if status not in VALID_STATUSES + ('all',):
            return (f"Invalid status '{status}' — active | in_progress | "
                    f"completed | abandoned | all."), False
        goals = _top_level(cursor, scope, status)
        if not goals:
            lines = [f"No {status if status != 'all' else ''} goals in scope "
                     f"'{scope}'. create_goal starts one."]
            if status == 'active':
                done = _top_level(cursor, scope, 'completed')[:3]
                if done:
                    lines.append("--- Recently completed ---")
                    lines += [f"  [x] [{g['id']}] {g['title']}" for g in done]
            return "\n".join(lines), True
        lines = [f"=== {status.capitalize()} goals (scope: {scope}) ==="]
        for g in goals[:OVERVIEW_CAP]:
            lines.append(_fmt_goal(cursor, g, scope))
        if len(goals) > OVERVIEW_CAP:
            lines.append(f"... and {len(goals) - OVERVIEW_CAP} more")
        if status == 'active':
            done = _top_level(cursor, scope, 'completed')[:3]
            if done:
                lines.append("--- Recently completed ---")
                for g in done:
                    lines.append(f"  [x] [{g['id']}] {g['title']}")
        return "\n".join(lines), True


def _update(scope, goal_id, title=None, description=None, priority=None,
            status=None, progress_note=None, permanent=None, ai=True,
            instructions=None, due=None):
    pt = _pt()
    if goal_id is None:
        return "Missing goal_id.", False
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        g = _get_goal(cursor, goal_id, scope)
        if not g:
            return f"Goal [{goal_id}] not found.", False
        meta = g['meta']
        if meta.get('progress_of'):
            return f"[{goal_id}] is a progress note, not a goal.", False
        # Permanent guard: the AI may only add notes (UI has full control).
        if ai and meta.get('permanent') and any(
                v is not None for v in (title, description, priority, status,
                                        instructions, due)):
            return f"Goal [{goal_id}] is permanent — only progress notes can be added.", False
        changes = []
        now = pt._now()
        if title is not None:
            title = title.strip()
            if not title or len(title) > TITLE_MAX:
                return f"Title must be 1-{TITLE_MAX} chars.", False
            cursor.execute('UPDATE chunks SET content = ?, embedding = NULL '
                           'WHERE id = ?', (title, g['id']))
            changes.append(f"title → '{title}'")
        if description is not None:
            description = description.strip()
            if len(description) > DESC_MAX:
                return f"Description too long (max {DESC_MAX}).", False
            if description:
                meta['description'] = description
            else:
                meta.pop('description', None)
            changes.append("description updated")
        if instructions is not None:
            instructions = instructions.strip()
            if len(instructions) > INSTR_MAX:
                return f"Instructions too long (max {INSTR_MAX}).", False
            if instructions:
                meta['instructions'] = instructions
            else:
                meta.pop('instructions', None)
            changes.append("instructions updated")
        if due is not None:
            due_val, err = _check_due(due)
            if err:
                return err, False
            if due_val:
                meta['due'] = due_val
                changes.append(f"due → {due_val}")
            else:
                meta.pop('due', None)
                changes.append("due cleared")
        if priority is not None:
            priority = priority.lower().strip()
            if priority not in VALID_PRIORITIES:
                return f"Invalid priority '{priority}'.", False
            meta['priority'] = priority
            changes.append(f"priority → {priority}")
        if status is not None:
            status = status.lower().strip()
            if status not in VALID_STATUSES:
                return f"Invalid status '{status}'.", False
            meta['goal_status'] = status
            meta['completed_at'] = now if status == 'completed' else None
            if status != 'completed':
                meta.pop('completed_at', None)
            changes.append(f"status → {status}")
        importance = 'keep'
        if permanent is not None:
            meta['permanent'] = bool(permanent)
            if not permanent:
                meta.pop('permanent', None)
            importance = PERMANENT_IMPORTANCE if permanent else None
            changes.append(f"permanent → {bool(permanent)}")
        note_id = None
        if progress_note is not None:
            progress_note = progress_note.strip()
            if not progress_note or len(progress_note) > NOTE_MAX:
                return f"Progress note must be 1-{NOTE_MAX} chars.", False
            note_id = _insert_goal_chunk(cursor, pt, scope, progress_note,
                                         {'progress_of': g['id']})
            _link(cursor, note_id, g['id'], 'progress_of', now)
            changes.append(f"logged: {progress_note[:80]}")
        if not changes:
            return ("Nothing to update — pass title, description, instructions, "
                    "priority, status, due, or progress_note.", False)
        _write_meta(cursor, pt, g['id'], meta, importance)
        conn.commit()
    if note_id or title is not None:
        pt.reset_backfill_latch()
    pt._publish_mind('goals', scope, 'update')
    pt._ledger(scope, pt._added_by(), 'updated', layer='goals', target=g['id'],
               summary=f"{g['title'][:80]}: {', '.join(changes)[:120]}")
    return f"Goal [{goal_id}] updated: {', '.join(changes)}", True


def _delete(scope, goal_id):
    pt = _pt()
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        g = _get_goal(cursor, goal_id, scope)
        if not g:
            return f"Goal [{goal_id}] not found.", False
        if g['meta'].get('permanent'):
            return f"Goal [{goal_id}] is permanent and cannot be deleted.", False
        n = delete_goal_cascade(cursor, g['id'])
        conn.commit()
    pt._publish_mind('goals', scope, 'delete')
    pt._ledger(scope, pt._added_by(), 'deleted', layer='goals', target=g['id'],
               summary=f"goal: {g['title'][:120]}" + (f" (+{n - 1} rows)" if n > 1 else ""))
    extra = f" (+{n - 1} subtasks/notes)" if n > 1 else ""
    return f"Deleted goal [{goal_id}] '{g['title']}'{extra}", True


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        pt = _pt()
        scope = pt._get_current_scope()
        if scope is None:
            return "Goals are disabled when memory is disabled for this chat.", False
        if scope == 'global' and function_name != 'list_goals':
            return "The global overlay is read-only for the AI.", False

        if function_name == 'create_goal':
            return _create(scope, arguments.get('title'),
                           description=arguments.get('description'),
                           priority=arguments.get('priority', 'medium'),
                           parent_id=arguments.get('parent_id'),
                           permanent=bool(arguments.get('permanent', False)),
                           instructions=arguments.get('instructions'),
                           due=arguments.get('due'))
        if function_name == 'list_goals':
            return _list(scope, goal_id=arguments.get('goal_id'),
                         status=arguments.get('status', 'active'))
        if function_name == 'update_goal':
            return _update(scope, arguments.get('goal_id'),
                           title=arguments.get('title'),
                           description=arguments.get('description'),
                           priority=arguments.get('priority'),
                           status=arguments.get('status'),
                           progress_note=arguments.get('progress_note'),
                           instructions=arguments.get('instructions'),
                           due=arguments.get('due'))
        if function_name == 'delete_goal':
            return _delete(scope, arguments.get('goal_id'))
        return f"Unknown goal function: {function_name}", False
    except Exception as e:
        logger.error(f"[GOALS] {function_name} error: {e}", exc_info=True)
        return f"Goal system error: {e}", False
