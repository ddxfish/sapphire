# plugins/mindpalace/routes/self_routes.py
# Layer 0 app routes — the Self view's windows. Same trusted-surface stance as
# browse.py: the authenticated UI sees the requested scope plainly. All writes
# funnel through self_tools.write_section (one write path — cap, versioning,
# metadata stamping, entity linking identical to the tool).

import json
import logging

logger = logging.getLogger(__name__)


def _st():
    from plugins.mindpalace.tools import self_tools
    return self_tools


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def get_sheet(query=None, **_):
    """The whole self sheet: typed sections (present or empty), custom boxes,
    history counts, live dashboard. query: scope."""
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope = (query or {}).get('scope') or 'default'
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        current = st._current_sections(cursor, scope)
        history = st._history_counts(cursor, scope)
        dashboard = st.dashboard_data(cursor, scope)

    def _rows_of(row, fields, sep):
        """meta.rows, with read fallbacks: legacy handles meta.pairs, or rows
        parsed live from the canonical text (pre-structured data lights up
        in the editor without needing a rewrite)."""
        if not row or not fields:
            return None
        m = row['meta']
        return m.get('rows') or m.get('pairs') or \
            st.text_to_rows(row['content'], fields, sep)

    sections = []
    for sec, spec in st.SECTIONS.items():
        row = current.get(sec)
        fields = spec.get('fields')
        sep = spec.get('sep', st.DEFAULT_SEP)
        sections.append({
            'section': sec, 'title': spec['title'], 'hint': spec['hint'],
            'mode': spec['mode'], 'versioned': bool(spec.get('versioned')),
            'width': spec.get('width', 'half'),
            'fields': fields, 'max_rows': spec.get('max'),
            'rows': _rows_of(row, fields, sep) or ([] if fields else None),
            'content': row['content'] if row else '',
            'updated': row['updated'] if row else None,
            'history_count': history.get(sec, 0),
        })
    custom = []
    for sec, row in current.items():
        if sec in st.SECTIONS:
            continue
        fields = st.sanitize_fields_spec(row['meta'].get('fields_spec'))
        custom.append({
            'section': sec, 'content': row['content'],
            'fields': fields,
            'rows': _rows_of(row, fields, st.DEFAULT_SEP) if fields else None,
            'width': 'half' if fields else 'third',
            'updated': row['updated'], 'history_count': history.get(sec, 0),
        })
    return {'scope': scope, 'sections': sections, 'custom': custom,
            'dashboard': dashboard}


def put_section(section=None, body=None, **_):
    """Replace one section. body: {content}, or {rows:[{...}]} for structured
    sections (+ fields_spec:[{key,label}] when creating a structured custom
    box). Legacy {pairs} still lands on handles. Empty content clears typed /
    removes custom (user_bio semantics). App edition always replaces — no
    rolling-add magic here; the editor IS the list."""
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    content = b.get('content')
    rows = b.get('rows')
    if rows is None and b.get('pairs') is not None:
        rows = b['pairs']
    # A body carrying NEITHER content nor rows is a malformed request (JS
    # bug, aborted autosave) — not a clear. Explicit clears send content:''
    # (scout find, 2026-07-19: the old fallthrough archived the section on
    # any empty PUT with a success reply).
    if content is None and rows is None:
        return {'error': "Body needs 'content' (use '' to clear) or 'rows'."}, 400
    fields_spec = st.sanitize_fields_spec(b.get('fields_spec'))
    if rows is not None:
        sec = st._sanitize_section(section)
        spec = st.SECTIONS.get(sec) or {}
        fields = spec.get('fields') or fields_spec
        if not fields:
            # existing custom box: spec lives on its current chunk
            with pt._get_connection() as conn:
                row = st._current_sections(conn.cursor(), scope).get(sec)
            fields = st.sanitize_fields_spec(
                (row or {}).get('meta', {}).get('fields_spec'))
        if not fields:
            return {'error': 'No field spec for structured rows'}, 400
        content = st.rows_to_text(rows, fields, spec.get('sep', st.DEFAULT_SEP))
    msg, ok = st.write_section(scope, section, content or '',
                               projects_replace=True, fields_spec=fields_spec)
    if not ok:
        return {'error': msg}, 400
    return {'success': True, 'message': msg}


def _ledger_row(r, children=0, note_id=None, note_detail=None, report=None):
    detail = None
    if r[7]:
        try:
            detail = json.loads(r[7])
        except Exception:
            pass
    if note_id:
        # A 'noted' child overrides the effective reason (B+D: annotations
        # are append-only children; readers overlay the newest one).
        detail = dict(detail or {})
        nd = None
        try:
            nd = json.loads(note_detail) if note_detail else None
        except Exception:
            pass
        if nd and nd.get('reason'):
            detail['reason'] = nd['reason']
        else:
            detail.pop('reason', None)
    return {'id': r[0], 'ts': r[1], 'actor': r[2], 'action': r[3],
            'layer': r[4], 'target': r[5],
            'summary': report or r[6],   # a run's after-action replaces "running…"
            'detail': detail, 'children': children}


def get_ledger(query=None, **_):
    """The Self page's Ledger panel (Ledger v1). Top-level rows newest-first
    with librarian-pass child counts; ?parent_id=N fetches one pass's
    children. Read-only surface — the UI never writes or stamps anything
    here (the read watermark belongs to HER read_self, not to Krem looking)."""
    from plugins.mindpalace.tools import ledger as lg
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    q = query or {}
    scope = q.get('scope') or 'default'
    try:   # close any in-flight prompt-edit session — readers see current truth
        from plugins.mindpalace.tools import prompt_audit
        prompt_audit.flush(scope, force=True)
    except Exception:
        pass
    try:
        limit = min(int(q.get('limit', 30)), 200)
        offset = max(int(q.get('offset', 0)), 0)
        parent_id = int(q['parent_id']) if q.get('parent_id') else None
    except (ValueError, TypeError):
        return {'error': 'limit/offset/parent_id must be integers'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        if parent_id:
            # Children can have children of their own (run → batches →
            # items) — carry the counts so the UI can drill all the way.
            rows = cur.execute(
                'SELECT l.id, l.ts, l.actor, l.action, l.layer, l.target, '
                '  l.summary, l.detail, '
                '  (SELECT COUNT(*) FROM ledger c WHERE c.parent_id = l.id) '
                'FROM ledger l WHERE l.parent_id = ? AND l.scope = ? '
                'ORDER BY l.id', (parent_id, scope)).fetchall()
            return {'rows': [_ledger_row(r[:8], r[8]) for r in rows]}
        rows = cur.execute(
            'SELECT l.id, l.ts, l.actor, l.action, l.layer, l.target, l.summary, l.detail, '
            '  (SELECT COUNT(*) FROM ledger c WHERE c.parent_id = l.id), '
            "  (SELECT n.id FROM ledger n WHERE n.parent_id = l.id "
            "   AND n.action = 'noted' ORDER BY n.id DESC LIMIT 1), "
            "  (SELECT n.detail FROM ledger n WHERE n.parent_id = l.id "
            "   AND n.action = 'noted' ORDER BY n.id DESC LIMIT 1), "
            "  (SELECT rp.summary FROM ledger rp WHERE rp.parent_id = l.id "
            "   AND rp.action = 'report' ORDER BY rp.id DESC LIMIT 1) "
            'FROM ledger l WHERE l.scope = ? AND l.parent_id IS NULL '
            'ORDER BY l.id DESC LIMIT ? OFFSET ?', (scope, limit, offset)).fetchall()
        total = cur.execute(
            'SELECT COUNT(*) FROM ledger WHERE scope = ? AND parent_id IS NULL',
            (scope,)).fetchone()[0]
        week = cur.execute(
            'SELECT COUNT(*) FROM ledger WHERE scope = ? AND parent_id IS NULL '
            'AND ts >= ?', (scope, st._cutoff(7))).fetchone()[0]
        last_read = lg.last_read_ts(cur, scope)
        # Unread uses the same exclusion rule as her sheet tail: her own 'ai'
        # rows don't count (she was there), pass children fold into parents.
        unread_where = f'scope = ? AND {lg.SHEET_WHERE}'
        if last_read:
            unread = cur.execute(
                f'SELECT COUNT(*) FROM ledger WHERE {unread_where} AND ts > ?',
                (scope, last_read)).fetchone()[0]
        else:
            unread = cur.execute(
                f'SELECT COUNT(*) FROM ledger WHERE {unread_where}',
                (scope,)).fetchone()[0]
    return {'rows': [_ledger_row(r[:8], r[8], r[9], r[10], r[11]) for r in rows],
            'total': total, 'week': week, 'unread': unread,
            'last_read_ts': last_read}


def patch_ledger_reason(lid=None, body=None, **_):
    """PUT ledger/{lid}/reason {scope, reason} — annotate a row after the
    fact. HUMAN-ONLY surface (no tool path). APPEND-ONLY (B+D ruling,
    2026-07-22): the annotation is a CHILD row (action 'noted') — the target
    row is never touched, so the ledger stays hash-chain-ready. Readers
    overlay the newest note as the effective reason. Guard: non-'ai' target
    rows only; her own reasons arrive with her actions."""
    st, pt = _st(), _pt()
    from plugins.mindpalace.tools import ledger as lg
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        lid = int(lid)
    except (TypeError, ValueError):
        return {'error': 'invalid id'}, 400
    b = body or {}
    scope = (b.get('scope') or '').strip() or 'default'
    reason = ' '.join(str(b.get('reason') or '').split())[:500]
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute(
            'SELECT actor, layer FROM ledger WHERE id = ? AND scope = ?',
            (lid, scope)).fetchone()
        if not row:
            return {'error': 'not found'}, 404
        if row[0] == 'ai':
            return {'error': "her own rows keep the reason she gave"}, 403
        lg.record(scope, 'user', 'noted', layer=row[1], target=lid,
                  parent_id=lid,
                  summary=(f'reason: {reason}' if reason else 'reason cleared'),
                  detail={'reason': reason} if reason else None, cursor=cur)
        conn.commit()
    return {'success': True, 'reason': reason or None}


def section_history(section=None, query=None, **_):
    """Archived versions of one section, newest first — the becoming trail."""
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope = (query or {}).get('scope') or 'default'
    sec = st._sanitize_section(section)
    if not sec:
        return {'error': 'Invalid section'}, 400
    with pt._get_connection() as conn:
        rows = conn.execute(
            "SELECT id, content, created, json_extract(meta, '$.superseded_at') "
            "FROM chunks WHERE layer = 'self' AND scope = ? "
            "AND json_extract(meta, '$.section') = ? "
            "AND json_extract(meta, '$.superseded_at') IS NOT NULL "
            "ORDER BY created DESC LIMIT 100", (scope, sec)).fetchall()
    return {'section': sec, 'versions': [
        {'id': r[0], 'content': r[1], 'created': r[2], 'superseded_at': r[3]}
        for r in rows]}


# ─── Wake tools (2026-07-16) — user-armed live checks at wake ────────────────
# HUMAN-ARMED ONLY, by design: these routes are the sole write path (the
# update_self tool refuses the section), so a poisoned conversation can't
# persist an autorun payload. Same trust class as user-configured cron.

def _wake_clamp_chars(v):
    st = _st()
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(st.WAKE_TOOL_CHARS_MIN, min(n, st.WAKE_TOOL_CHARS_MAX))


def _known_tools():
    """{name: description} of globally-enabled tools, or None when the system
    isn't up (tests / early boot) — then name validation is skipped; the
    exec path re-validates through execute_function anyway."""
    try:
        from core.api_fastapi import get_system
        fm = get_system().llm_chat.function_manager
        out = {}
        for info in fm.function_modules.values():
            for tool in info['tools']:
                fn = tool['function']
                out[fn['name']] = fn
        enabled = set(fm.get_enabled_function_names())
        return {k: v for k, v in out.items() if k in enabled}
    except Exception:
        return None


def list_wake_tools(query=None, **_):
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope = (query or {}).get('scope') or 'default'
    with pt._get_connection() as conn:
        rows = st._wake_tool_rows(conn.cursor(), scope)
    return {'scope': scope, 'max_rows': st.WAKE_TOOLS_MAX_ROWS,
            'default_chars': st.WAKE_TOOL_DEFAULT_CHARS,
            'tools': [{'id': r[0], 'tool': r[1],
                       'params': json.loads(r[2]) if r[2] else {},
                       'max_chars': r[3], 'enabled': bool(r[4])}
                      for r in rows]}


def wake_tool_options(**_):
    """Dropdown feed: enabled tools with their param schemas (name, type,
    description, required) so the UI can render blanks per param."""
    st = _st()
    known = _known_tools()
    if known is None:
        return {'tools': []}
    out = []
    for name, fn in sorted(known.items()):
        if name in st.WAKE_TOOL_BLOCKED:
            continue
        props = (fn.get('parameters') or {}).get('properties') or {}
        required = set((fn.get('parameters') or {}).get('required') or [])
        out.append({
            'name': name,
            'description': (fn.get('description') or '')[:200],
            'params': [{'key': k, 'type': p.get('type', 'string'),
                        'description': (p.get('description') or '')[:120],
                        'required': k in required}
                       for k, p in props.items()],
        })
    return {'tools': out}


def create_wake_tool(body=None, **_):
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    scope = (b.get('scope') or '').strip() or 'default'
    tool = (b.get('tool') or '').strip()
    if not tool:
        return {'error': 'tool name required'}, 400
    if tool in st.WAKE_TOOL_BLOCKED:
        return {'error': f"'{tool}' cannot be a wake tool (recursion guard)"}, 400
    known = _known_tools()
    if known is not None and tool not in known:
        return {'error': f"'{tool}' is not an enabled tool"}, 400
    params = b.get('params') if isinstance(b.get('params'), dict) else {}
    max_chars = _wake_clamp_chars(b.get('max_chars'))
    with pt._get_connection() as conn:
        cur = conn.cursor()
        st._ensure_wake_table(cur)
        n = cur.execute('SELECT COUNT(*) FROM wake_tools WHERE scope = ?',
                        (scope,)).fetchone()[0]
        if n >= st.WAKE_TOOLS_MAX_ROWS:
            return {'error': f'wake tool limit reached ({st.WAKE_TOOLS_MAX_ROWS})'}, 409
        cur.execute(
            'INSERT INTO wake_tools (scope, tool, params, max_chars, enabled, '
            'position, created) VALUES (?, ?, ?, ?, 1, ?, ?)',
            (scope, tool, json.dumps(params, ensure_ascii=False), max_chars,
             n, pt._now()))
        wid = cur.lastrowid
        from plugins.mindpalace.tools import ledger as lg
        lg.record(scope, 'user', 'saved', layer='self', target=f'wake_tool/{wid}',
                  summary=f'armed "{tool}" at wake', cursor=cur,
                  detail={'params': params, 'max_chars': max_chars}
                  if params or max_chars else None)
        conn.commit()
    return {'success': True, 'id': wid}


def update_wake_tool(wid=None, body=None, **_):
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        wid = int(wid)
    except (TypeError, ValueError):
        return {'error': 'invalid id'}, 400
    b = body or {}
    sets, params = [], []
    if 'enabled' in b:
        sets.append('enabled = ?')
        params.append(1 if b['enabled'] else 0)
    if 'params' in b and isinstance(b['params'], dict):
        sets.append('params = ?')
        params.append(json.dumps(b['params'], ensure_ascii=False))
    if 'max_chars' in b:
        sets.append('max_chars = ?')
        params.append(_wake_clamp_chars(b['max_chars']))
    if 'position' in b:
        try:
            sets.append('position = ?')
            params.append(int(b['position']))
        except (TypeError, ValueError):
            return {'error': 'invalid position'}, 400
    if not sets:
        return {'error': 'nothing to update'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        st._ensure_wake_table(cur)
        old = cur.execute('SELECT scope, tool, params, max_chars, enabled '
                          'FROM wake_tools WHERE id = ?', (wid,)).fetchone()
        if not old:
            return {'error': 'not found'}, 404
        cur.execute(f'UPDATE wake_tools SET {", ".join(sets)} WHERE id = ?',
                    params + [wid])
        # Ledger (tamper evidence at wake): what actually changed; pure
        # position reorders stay silent — drag noise isn't a change to her.
        changed, diff = [], {}
        if 'enabled' in b and bool(b['enabled']) != bool(old[4]):
            changed.append('enabled' if b['enabled'] else 'disabled')
            diff['enabled'] = ['on' if old[4] else 'off',
                               'on' if b['enabled'] else 'off']
        if 'params' in b and isinstance(b['params'], dict):
            try:
                old_p = json.loads(old[2]) if old[2] else {}
            except Exception:
                old_p = {}
            if b['params'] != old_p:
                changed.append('params')
                diff['params'] = [json.dumps(old_p, ensure_ascii=False),
                                  json.dumps(b['params'], ensure_ascii=False)]
        if 'max_chars' in b:
            new_c = _wake_clamp_chars(b['max_chars'])
            if new_c != old[3]:
                changed.append(f'output cap → {new_c or "default"}')
                diff['max_chars'] = [str(old[3] or ''), str(new_c or '')]
        if changed:
            from plugins.mindpalace.tools import ledger as lg
            lg.record(old[0], 'user', 'edited', layer='self',
                      target=f'wake_tool/{wid}', cursor=cur,
                      summary=f'wake tool "{old[1]}": ' + ', '.join(changed),
                      detail={'fields': diff})
        conn.commit()
    return {'success': True}


def delete_wake_tool(wid=None, **_):
    st, pt = _st(), _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        wid = int(wid)
    except (TypeError, ValueError):
        return {'error': 'invalid id'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        st._ensure_wake_table(cur)
        old = cur.execute('SELECT scope, tool, params, max_chars '
                          'FROM wake_tools WHERE id = ?', (wid,)).fetchone()
        if not old:
            return {'error': 'not found'}, 404
        cur.execute('DELETE FROM wake_tools WHERE id = ?', (wid,))
        from plugins.mindpalace.tools import ledger as lg
        lg.record(old[0], 'user', 'deleted', layer='self',
                  target=f'wake_tool/{wid}', cursor=cur,
                  summary=f'disarmed "{old[1]}" wake tool',
                  detail={'params': old[2] or '{}', 'max_chars': old[3]})
        conn.commit()
    return {'success': True}
