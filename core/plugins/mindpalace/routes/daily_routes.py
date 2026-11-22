# core/plugins/mindpalace/routes/daily_routes.py - the Daily tab's routes.
# The app is the person: it shows every day in the requested scope (exact
# scope, no overlay, private-model days included), and a day is read, marked
# and deleted WHOLE, never one piece at a time. tools/daily.py owns the layer.

import json
import logging
import re

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
    from plugins.mindpalace.tools import temporal
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
                    'pieces': [], 'dates': [], 'dates_off': [], 'entities': [], 'ids': []})
                if not d['pieces']:
                    d['dates_off'] = _dates_off(m)
                d['pieces'].append((content, daily._sep(m, idx or 0) if d['pieces'] else ''))
                d['ids'].append(cid)
                d['private'] = d['private'] or bool(private)
            by_id = {cid: d for d in days.values() for cid in d['ids']}
            ids = list(by_id)
            for at in range(0, len(ids), 400):
                part = ids[at:at + 400]
                for cid, eid, name in cur.execute(
                        "SELECT g.src_id, e.id, e.name FROM edges g JOIN entities e ON e.id = g.dst_id "
                        "WHERE g.src_type = 'chunk' AND g.dst_type = 'entity' "
                        f"AND g.src_id IN ({','.join('?' * len(part))}) ORDER BY g.id", part):
                    if not any(x['id'] == eid for x in by_id[cid]['entities']):
                        by_id[cid]['entities'].append({'id': eid, 'name': name})
    out = []
    for src in sources:
        d = days.get(src)
        if not d:
            continue
        pieces = d.pop('pieces')
        d.pop('ids')
        d['parts'] = len(pieces)
        d['text'] = daily.join(pieces)
        # Read from the text each time, not from what was stamped on the rows:
        # the stamps on days recorded before 2026-11-21 hold guesses, and a
        # recorded day is not rewritten to correct them.
        d['dates'] = _dates(temporal, d['text'], d['created'], d['day'], d['dates_off'])
        out.append(d)
    gap = daily.clock_gap()
    return {'days': out, 'total': total, 'limit': limit, 'offset': offset,
            'clock': dict(gap, words=daily.clock_words(gap)) if gap else None}


def _dates_off(meta) -> list:
    off = meta.get('dates_off')
    return [w for w in off if isinstance(w, str)] if isinstance(off, list) else []


def _dates(temporal, text, created, day, off) -> list:
    """The dates a day's card shows: whole dates only (a clock time on a date
    made a pill that could not be taken off, and the day's own date with a
    time showed as a date named), without the day itself and the hidden ones,
    twelve at most, counted after those are left out."""
    named = dict.fromkeys(w[:10] for w in temporal.named(text, created, limit=60))
    return sorted(w for w in named if w != day and w not in off)[:12]


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


_ISO_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')


def tag_day(body=None, **_):
    """POST days/tags {scope, source, action, ...}: the person's own corrections
    to what a day is linked to and which dates its card shows. The day's text
    is never touched.
      unlink {entity: id}   the day no longer links to that entity
      link {name}           link the day to an existing entity (scope, then global)
      hide_date {date}      a date the text names is left off the card
      show_dates            every hidden date is shown again
    A link is only ever made at record time or here: nothing links a day again
    after the person unlinked it (an export carries the links by name, and an
    import makes exactly those). Linking and unlinking are written to the
    ledger; what the card shows of dates is not."""
    from plugins.mindpalace.tools import metadata as md
    pt, daily = _pt(), _daily()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    scope, source = _target(b)
    action = b.get('action')
    if not scope or action not in ('unlink', 'link', 'hide_date', 'show_dates'):
        return {'error': 'scope, source and an action (unlink, link, hide_date, show_dates) are required'}, 400
    day = source[4:14]
    with pt._get_connection() as conn:
        cur = conn.cursor()
        rows = cur.execute("SELECT id, content, meta, wr_private, created FROM chunks WHERE layer = 'daily' "
                           "AND scope = ? AND source = ? ORDER BY chunk_index, id", (scope, source)).fetchall()
        if not rows:
            return {'error': 'Not found'}, 404
        ids, marks = [r[0] for r in rows], ','.join('?' * len(rows))
        private = any(r[3] for r in rows)
        if action == 'unlink':
            try:
                ent = cur.execute('SELECT id, name, wr_private FROM entities WHERE id = ?',
                                  (int(b.get('entity')),)).fetchone()
            except (TypeError, ValueError):
                ent = None
            if not ent:
                return {'error': 'That entity was not found'}, 404
            cur.execute("DELETE FROM edges WHERE src_type = 'chunk' AND dst_type = 'entity' AND dst_id = ? "
                        f"AND src_id IN ({marks})", [ent[0]] + ids)
            if not cur.rowcount:
                return {'error': f'"{ent[1]}" is not linked to this day'}, 404
            did, summary = 'unlinked', f'unlinked "{ent[1]}" from the day {day}'
        elif action == 'link':
            name = str(b.get('name') or '').strip()
            # By the card's name or one of its nicknames, this scope before global.
            hit = md.alias_map(md.entity_aliases(cur, scope)).get(name.lower())
            ent = cur.execute('SELECT id, name, wr_private FROM entities WHERE id = ?', (hit[0],)).fetchone() if hit else None
            if not ent:
                return {'error': f'No entity is called "{name}" in this scope or in global. '
                                 f'Make its card first (Mind > Entities).'}, 404
            if cur.execute("SELECT 1 FROM edges WHERE src_type = 'chunk' AND dst_type = 'entity' AND dst_id = ? "
                           f"AND src_id IN ({marks}) LIMIT 1", [ent[0]] + ids).fetchone():
                return {'error': f'"{ent[1]}" is already linked to this day'}, 400
            names = [alias for eid, _c, alias in md.entity_aliases(cur, scope) if eid == ent[0]] or [ent[1]]
            at = next((r[0] for r in rows if daily.named(r[1], names)), ids[0])   # the piece that names them
            cur.execute("INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'entity', ?, ?, ?, ?)",
                        (at, ent[0], daily.EDGE_KIND, md.MENTION_EDGE_WEIGHT, pt._now()))
            did, summary = 'linked', f'linked "{ent[1]}" to the day {day}'
        else:
            off = _dates_off(daily._meta(rows[0][2]))
            if action == 'hide_date':
                from plugins.mindpalace.tools import temporal
                when = str(b.get('date') or '')
                shown = _dates(temporal, ' '.join(r[1] for r in rows), rows[0][4], day, off)
                if not _ISO_DAY.fullmatch(when) or when not in shown:
                    return {'error': 'That date is not one this day\'s card shows'}, 400
                off = sorted(set(off + [when]))
            else:
                off = []
            # Only this one key is written: the rest of the piece's meta is as
            # it stands now, not as it was read a moment ago (a chat made
            # private in between has its name blanked there).
            if off:
                cur.execute("UPDATE chunks SET meta = json_set(COALESCE(meta, '{}'), '$.dates_off', json(?)) "
                            "WHERE id = ?", (json.dumps(off), ids[0]))
            else:
                cur.execute("UPDATE chunks SET meta = json_remove(meta, '$.dates_off') WHERE id = ?", (ids[0],))
            did = None      # what a card shows is not news for her wake sheet: no ledger line
        if did:
            # A private-model card's name stays out of the lines a cloud model reads.
            pt._ledger(scope, 'user', did, layer='daily', cursor=cur, private=private or bool(ent[2]),
                       summary=summary)
        conn.commit()
    _publish(scope, 'update')
    return {'success': True}
