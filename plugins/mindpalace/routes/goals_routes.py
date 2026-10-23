# plugins/mindpalace/routes/goals_routes.py
# Layer 4 app routes — the Goals view's windows. Same trusted-surface stance
# as browse.py. Writes funnel through goal_tools' storage helpers (one write
# path: metadata stamping + mention edges identical to the tools). The UI
# has full control (permanent toggle, force delete) — the AI-facing guards
# live in goal_tools.execute.

import logging

logger = logging.getLogger(__name__)


def _gt():
    from plugins.mindpalace.tools import goal_tools
    return goal_tools


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _goal_dict(cursor, gt, g, scope):
    meta = g['meta']
    return {
        'id': g['id'], 'title': g['title'],
        'description': meta.get('description'),
        'instructions': meta.get('instructions'),
        'priority': meta.get('priority') or 'medium',
        'status': gt._status_of(meta),
        'permanent': bool(meta.get('permanent')),
        'due': meta.get('due'),
        'completed_at': meta.get('completed_at'),
        'created': g['created'], 'updated': g['updated'],
        'subtasks': [{'id': s['id'], 'title': s['title'],
                      'description': s['meta'].get('description'),
                      'instructions': s['meta'].get('instructions'),
                      'priority': s['meta'].get('priority') or 'medium',
                      'due': s['meta'].get('due'),
                      'created': s['created'],
                      'status': gt._status_of(s['meta'])}
                     for s in gt._subtasks_of(cursor, g['id'], scope)],
        'progress': gt._notes_of(cursor, g['id'], scope),
    }


def list_goals(query=None, **_):
    gt, pt = _gt(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    q = query or {}
    scope = q.get('scope') or 'default'
    status = q.get('status') or 'active'
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        goals = [_goal_dict(cursor, gt, g, scope)
                 for g in gt._top_level(cursor, scope,
                                        None if status == 'all' else status)]
    return {'scope': scope, 'status': status, 'goals': goals}


def create_goal(body=None, **_):
    gt, pt = _gt(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    msg, ok = gt._create(scope, b.get('title'),
                         description=b.get('description'),
                         priority=b.get('priority', 'medium'),
                         parent_id=b.get('parent_id'),
                         permanent=bool(b.get('permanent', False)),
                         instructions=b.get('instructions'),
                         due=b.get('due'),
                         subtasks=b.get('subtasks'))
    if not ok:
        return {'error': msg}, 400
    return {'success': True, 'message': msg}


def update_goal(gid=None, body=None, **_):
    gt, pt = _gt(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    msg, ok = gt._update(scope, gid,
                         title=b.get('title'), description=b.get('description'),
                         priority=b.get('priority'), status=b.get('status'),
                         progress_note=b.get('progress_note'),
                         permanent=b.get('permanent'),
                         instructions=b.get('instructions'), due=b.get('due'),
                         ai=False)   # UI edition: full control, incl. permanent
    if not ok:
        return {'error': msg}, 400
    return {'success': True, 'message': msg}


def delete_goal(gid=None, query=None, **_):
    """UI delete. Permanent goals need ?force=1 — an informed override, not a
    stray trash-click (classic contract carried over)."""
    gt, pt = _gt(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    q = query or {}
    scope = (q.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        g = gt._get_goal(cursor, gid, scope)
        if not g:
            return {'error': 'Not found'}, 404
        if g['meta'].get('permanent') and q.get('force') not in ('1', 'true'):
            return {'error': 'This goal is permanent — confirm force delete'}, 409
        n = gt.delete_goal_cascade(cursor, g['id'])
        conn.commit()
    pt._publish_mind('goals', scope, 'delete')
    return {'success': True, 'deleted': n}
