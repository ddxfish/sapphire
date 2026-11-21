# core/plugins/mindpalace/routes/daily_routes.py - the Daily tab's routes.
# The app is the person: it shows every day in the requested scope (exact
# scope, no overlay, private-model days included), and a day is read, marked
# and deleted WHOLE, never one piece at a time. tools/daily.py owns the layer.

import json
import logging

logger = logging.getLogger(__name__)

MAX_LIMIT = 100


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _daily():
    from plugins.mindpalace.tools import daily
    return daily


def _publish(scope, action):
    try:
        from core.mind_events import publish_mind_changed
        publish_mind_changed('memory', scope, action)
    except Exception:
        pass


def list_days(query=None, **_):
    """GET days?scope&q&limit&offset: kept days, newest first. q matches the
    text or the date (2026-11, 2026-11-02)."""
    pt, daily = _pt(), _daily()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    q = query or {}
    scope = q.get('scope') or 'default'
    try:
        limit = max(1, min(int(q.get('limit', 20)), MAX_LIMIT))
        offset = max(int(q.get('offset', 0)), 0)
    except ValueError:
        return {'error': 'limit/offset must be integers'}, 400
    search = (q.get('q') or '').strip()
    where, params = "c.layer = 'daily' AND c.scope = ? AND c.source IS NOT NULL", [scope]
    if search:
        like = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where += (" AND c.source IN (SELECT source FROM chunks WHERE layer = 'daily' AND scope = ? "
                  "AND (content LIKE ? ESCAPE '\\' OR label LIKE ? ESCAPE '\\'))")
        params += [scope, like, like]
    with pt._get_connection() as conn:
        cur = conn.cursor()
        total = cur.execute(f'SELECT COUNT(DISTINCT c.source) FROM chunks c WHERE {where}', params).fetchone()[0]
        sources = [r[0] for r in cur.execute(
            f'SELECT c.source FROM chunks c WHERE {where} GROUP BY c.source '
            f'ORDER BY MIN(c.label) DESC, c.source DESC LIMIT ? OFFSET ?', params + [limit, offset])]
        days = {}
        if sources:
            marks = ','.join('?' * len(sources))
            for cid, content, meta, src, created, model, provider, private, origin, idx in cur.execute(
                    'SELECT id, content, meta, source, created, wr_model, wr_provider, wr_private, '
                    f"origin_name, chunk_index FROM chunks WHERE layer = 'daily' AND scope = ? "
                    f'AND source IN ({marks}) ORDER BY chunk_index, id', [scope] + sources):
                m = daily._meta(meta)
                d = days.setdefault(src, {
                    'source': src, 'day': m.get('day') or src[4:14], 'chat': m.get('chat'),
                    'persona': m.get('persona'), 'messages': m.get('messages'), 'created': created,
                    'writer': pt.writer_dict(model, provider, False, origin), 'private': False,
                    'pieces': [], 'dates': [], 'people': [], 'ids': []})
                d['pieces'].append((content, daily._sep(m, idx or 0) if d['pieces'] else ''))
                d['ids'].append(cid)
                d['private'] = d['private'] or bool(private)
                for when in m.get('event_dates') or []:
                    if when not in d['dates'] and when != d['day']:
                        d['dates'].append(when)
            by_id = {cid: d for d in days.values() for cid in d['ids']}
            ids = list(by_id)
            for at in range(0, len(ids), 400):
                part = ids[at:at + 400]
                for cid, name in cur.execute(
                        "SELECT g.src_id, e.name FROM edges g JOIN entities e ON e.id = g.dst_id "
                        "WHERE g.src_type = 'chunk' AND g.dst_type = 'entity' "
                        f"AND g.src_id IN ({','.join('?' * len(part))})", part):
                    if name not in by_id[cid]['people']:
                        by_id[cid]['people'].append(name)
    out = []
    for src in sources:
        d = days.get(src)
        if not d:
            continue
        pieces = d.pop('pieces')
        d.pop('ids')
        d['parts'] = len(pieces)
        d['text'] = daily.join(pieces)
        d['dates'].sort()
        out.append(d)
    return {'days': out, 'total': total, 'limit': limit, 'offset': offset}


def _target(payload):
    scope = (payload or {}).get('scope') or ''
    source = (payload or {}).get('source') or ''
    if not scope or not isinstance(source, str) or not source.startswith('day:'):
        return None, None
    return scope, source


def delete_day(query=None, **_):
    """DELETE days?scope&source: the whole day goes and stays gone (the next
    compact of its chat does not record it again)."""
    pt, daily = _pt(), _daily()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope, source = _target(query)
    if not scope:
        return {'error': 'scope and source are required'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        private = cur.execute("SELECT MAX(wr_private) FROM chunks WHERE layer = 'daily' AND scope = ? "
                              "AND source = ?", (scope, source)).fetchone()[0]
        gone = daily.forget(cur, scope, [source])
        if not gone:
            return {'error': 'Not found'}, 404
        pt._ledger(scope, 'user', 'deleted', layer='daily', cursor=cur, private=bool(private),
                   summary=f"deleted the day {source[4:14]} from daily memory ({gone} pieces)")
        conn.commit()
    pt.bump_matrix_gen()
    _publish(scope, 'delete')
    return {'success': True, 'deleted': gone}


def set_day_private(body=None, **_):
    """POST days/private {scope, source, private}: the person's switch for a
    whole day, the same mark a single memory carries."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope, source = _target(body)
    private = (body or {}).get('private')
    if not scope or not isinstance(private, bool):
        return {'error': 'scope, source and private (true or false) are required'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE chunks SET wr_private = ?, updated = ? WHERE layer = 'daily' AND scope = ? "
                    "AND source = ?", (int(private), pt._now(), scope, source))
        if not cur.rowcount:
            return {'error': 'Not found'}, 404
        pt._ledger(scope, 'user', 'private-model' if private else 'released', layer='daily', cursor=cur,
                   private=private,
                   summary=(f"marked the day {source[4:14]} for private models only" if private
                            else f"released the day {source[4:14]} to every model"))
        conn.commit()
    _publish(scope, 'update')
    return {'success': True, 'private': private}
