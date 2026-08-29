# plugins/mindpalace/routes/browse.py
# Palace web UI routes — the windows. Serves the authenticated web UI (Krem's
# human surface), so unlike the tool executors these are NOT private_key- or
# global-write-gated: the UI shows everything in the requested scope, with
# private keys rendered as visible lock pills (classic Mind view precedent).
# Browsing is exact-scope (no global overlay) — you see what's in the box.

import json
import logging
import re

logger = logging.getLogger(__name__)

MAX_LIMIT = 500

_MIND_DOMAIN = {'events': 'memory', 'self': 'memory',
                'entities': 'people', 'knowledge': 'knowledge'}


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _config():
    """Root config module — anchors the classic (v1) source-DB paths for
    import, exactly like the tool executor's config argument. Seam kept
    separate so tests can point it at fixture DBs."""
    import config
    return config


def _tpl():
    from plugins.mindpalace.tools import templates
    return templates


def _publish(layer, scope, action):
    try:
        from core.mind_events import publish_mind_changed
        publish_mind_changed(_MIND_DOMAIN.get(layer, 'memory'), scope, action)
    except Exception:
        pass


def _parse_meta(raw):
    """Dict or None, always. Valid-JSON-non-object ('[1,2]', '"x"', '42')
    gets the same _raw wrap as malformed JSON — every consumer calls .get()
    on the result, and a parsed list 500'd nine routes (Lane-5 scout)."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {'_raw': raw}
    except Exception:
        return {'_raw': raw}


def _chunk_row(row):
    (cid, layer, content, entity_id, entity_name, tier, label,
     favorite, private_key, meta, created, source, chunk_index) = row
    return {
        'id': cid, 'layer': layer, 'content': content,
        'entity_id': entity_id, 'entity_name': entity_name, 'tier': tier,
        'label': label, 'favorite': bool(favorite), 'private_key': private_key,
        'meta': _parse_meta(meta), 'created': created,
        'source': source, 'chunk_index': chunk_index,
    }


_CHUNK_COLS = ('c.id, c.layer, c.content, c.entity_id, e.name, c.tier, '
               'c.label, c.favorite, c.private_key, c.meta, c.created, '
               'c.source, c.chunk_index')


def status(query=None, **_):
    """Dispatcher probe + header counts. 200 here == palace active."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope = (query or {}).get('scope')
    with pt._get_connection() as conn:
        cur = conn.cursor()
        if scope:
            rows = cur.execute('SELECT layer, COUNT(*) FROM chunks WHERE scope = ? '
                               'GROUP BY layer', (scope,)).fetchall()
            ents = cur.execute('SELECT COUNT(*) FROM entities WHERE scope = ?',
                               (scope,)).fetchone()[0]
        else:
            rows = cur.execute('SELECT layer, COUNT(*) FROM chunks GROUP BY layer').fetchall()
            ents = cur.execute('SELECT COUNT(*) FROM entities').fetchone()[0]
        edges = cur.execute('SELECT COUNT(*) FROM edges').fetchone()[0]
    return {'active': True, 'layers': dict(rows), 'entities': ents, 'edges': edges}


def list_layers(query=None, **_):
    """Registered PLUGIN layers for the UI (tab strip + NavRail flyout).
    Core layers aren't listed — the frontend owns those statically. Dark
    layers aren't listed either: off means off."""
    pt = _pt()
    return {'layers': [
        {'key': key, 'label': spec['label'], 'icon': spec['icon'],
         'description': spec['description'], 'plugin': spec['plugin_name'],
         'librarian': spec['librarian'], 'writable': spec.get('writable', False)}
        for key, spec in pt._plugin_layers().items()
    ]}


def list_chunks(query=None, **_):
    """Browse/search chunks. query: scope, layer (single or CSV — the
    Memories tab asks for 'events,self' so its count matches its list),
    added_by, q, limit, offset."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    q = query or {}
    scope = q.get('scope') or 'default'
    layers = []
    for part in (q.get('layer') or '').split(','):
        layer, err = pt._validate_layer(part.strip())
        if err:
            return {'error': err}, 400
        if layer:
            layers.append(layer)
    try:
        limit = min(int(q.get('limit', 100)), MAX_LIMIT)
        offset = max(int(q.get('offset', 0)), 0)
    except ValueError:
        return {'error': 'limit/offset must be integers'}, 400
    search = (q.get('q') or '').strip()

    where = ['c.scope = ?']
    params = [scope]
    if layers:
        where.append(f"c.layer IN ({','.join('?' * len(layers))})")
        params.extend(layers)
    else:
        # Unfiltered browse excludes dark plugin layers (named dark layers
        # already died at _validate_layer).
        dark_sql, dark_params = pt._dark_clause('c')
        if dark_sql:
            where.append(dark_sql)
            params.extend(dark_params)
    # Author filter (knowledge tab): human and AI knowledge are one layer,
    # added_by is the metadata that tells them apart.
    added_by = (q.get('added_by') or '').strip()
    if added_by in ('ai', 'user'):
        where.append("json_extract(c.meta, '$.added_by') = ?")
        params.append(added_by)
    # "One thing: memories" (Krem's ruling, 2026-07-12): the Memories tab
    # excludes self-SHEET chunks (current + superseded history) — their home
    # is the Self page, the 📜 history modals, and the ledger. Deliberate
    # save_memory(layer='self') entries stay in the stream.
    if str(q.get('exclude_sheet') or '') in ('1', 'true'):
        # COALESCE: a NULL label must not NULL the whole NOT() (3-valued logic).
        where.append("NOT (c.layer = 'self' AND COALESCE(c.label, '') = 'self-sheet')")
    # 🔒 Keyed filter (Krem 2026-07-19): only memories carrying a private key.
    if str(q.get('keyed') or '') in ('1', 'true'):
        where.append('c.private_key IS NOT NULL')

    with pt._get_connection() as conn:
        cur = conn.cursor()
        if search:
            rows = _search_rows(pt, cur, search, where, params, limit, offset)
        else:
            rows = cur.execute(
                f'SELECT {_CHUNK_COLS} FROM chunks c '
                f'LEFT JOIN entities e ON e.id = c.entity_id '
                f'WHERE {" AND ".join(where)} '
                f'ORDER BY c.created DESC LIMIT ? OFFSET ?',
                params + [limit, offset]).fetchall()
        total = cur.execute(
            f'SELECT COUNT(*) FROM chunks c WHERE {" AND ".join(where)}',
            params).fetchone()[0]
    return {'chunks': [_chunk_row(r) for r in rows], 'total': total,
            'limit': limit, 'offset': offset}


def _search_rows(pt, cur, search, where, params, limit, offset):
    """FTS AND → FTS OR+prefix → LIKE cascade, browse edition (no vectors —
    the UI browses text; the AI keeps the full cascade in the tools)."""
    for fts_q in (pt._sanitize_fts_query(search),
                  pt._sanitize_fts_query(search, use_or=True, use_prefix=True)):
        if not fts_q:
            continue
        try:
            rows = cur.execute(
                f'SELECT {_CHUNK_COLS} FROM chunks_fts f '
                f'JOIN chunks c ON c.id = f.rowid '
                f'LEFT JOIN entities e ON e.id = c.entity_id '
                f'WHERE chunks_fts MATCH ? AND {" AND ".join(where)} '
                f'ORDER BY f.rank LIMIT ? OFFSET ?',
                [fts_q] + params + [limit, offset]).fetchall()
            if rows:
                return rows
        except Exception as e:
            logger.debug(f"[MINDPALACE] Browse FTS failed ({e}), falling back")
    return cur.execute(
        f'SELECT {_CHUNK_COLS} FROM chunks c '
        f'LEFT JOIN entities e ON e.id = c.entity_id '
        f'WHERE c.content LIKE ? AND {" AND ".join(where)} '
        f'ORDER BY c.created DESC LIMIT ? OFFSET ?',
        [f'%{search}%'] + params + [limit, offset]).fetchall()


def create_chunk(body=None, **_):
    """Add from the UI. body: content, scope, layer, entity, label, favorite,
    private_key. Funnels through _save_memory so caps, entity upsert, metadata
    stamping, and edge seeding all apply (tool_context is unset on this thread
    → meta is honestly thinner: no chat/persona/model, added_by='user').
    layer='knowledge' lands whole in the Library (v3) — it chunks for itself."""
    b = body or {}
    content = (b.get('content') or '').strip()
    if not content:
        return {'error': 'Content is required'}, 400
    # Writes require an explicit scope — silent 'default' fallback is how one
    # persona's data lands in another's room (scout class, 2026-07-19).
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    pt = _pt()
    # Humans keep the hard wall — it's their text in the box to edit. The
    # tool path trims instead (she can't see a box; refusal = rewrite loop).
    if ((b.get('layer') or '').strip().lower() != 'knowledge'
            and len(content) > pt.MAX_CHUNK_LENGTH):
        return {'error': f'Max {pt.MAX_CHUNK_LENGTH} chars — long reference '
                         f'text belongs in the Library (layer: knowledge)'}, 400
    kw = dict(layer=b.get('layer'), entity=b.get('entity'),
              label=b.get('label'), favorite=bool(b.get('favorite')),
              private_key=b.get('private_key'))
    # Knowledge no longer splits here — _save_memory reroutes layer='knowledge'
    # whole into the Library, which chunks for itself (v3, 2026-07-17).
    msg, ok = pt._save_memory(content, scope, **kw)
    if not ok:
        return {'error': msg}, 400
    return {'success': True, 'message': msg}


def create_entity(body=None, **_):
    """+ New entity from the UI. body: name, kind?, scope. Kind must be a
    known template kind (or empty = unsorted)."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    name = (b.get('name') or '').strip()[:80]
    if not name:
        return {'error': 'Name is required'}, 400
    kind = (b.get('kind') or '').strip().lower() or None
    if kind and kind not in _tpl().valid_kinds():
        return {'error': f"Unknown kind '{kind}'"}, 400
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        eid = pt.upsert_entity(cur, name, scope, kind=kind)
        conn.commit()
    _publish('entities', scope, 'save')
    return {'success': True, 'id': eid, 'name': name}


def delete_chunk(cid=None, query=None, **_):
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return {'error': 'Invalid chunk id'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT layer, scope, content FROM chunks WHERE id = ?',
                          (cid,)).fetchone()
        if not row:
            return {'error': 'Not found'}, 404
        cur.execute("DELETE FROM edges WHERE (src_type = 'chunk' AND src_id = ?) "
                    "OR (dst_type = 'chunk' AND dst_id = ?)", (cid, cid))
        cur.execute('DELETE FROM chunks WHERE id = ?', (cid,))
        pt._ledger(row[1], 'user', 'deleted', layer=row[0], target=cid, cursor=cur,
                   summary=(f"deleted [{cid}] from {row[0]}: "
                            f"\"{row[2][:60]}{'…' if len(row[2]) > 60 else ''}\""))
        conn.commit()
    _publish(row[0], row[1], 'delete')
    return {'success': True}


def toggle_favorite(cid=None, body=None, **_):
    """Favorite is the qualitative lever; 0.95 = never-fades band (same
    mapping as save — the number stays behind the curtain in tool responses,
    but favorite state itself is visible everywhere)."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return {'error': 'Invalid chunk id'}, 400
    fav = bool((body or {}).get('favorite'))
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT layer, scope, content FROM chunks WHERE id = ?',
                          (cid,)).fetchone()
        if not row:
            return {'error': 'Not found'}, 404
        cur.execute('UPDATE chunks SET favorite = ?, importance = ?, updated = ? '
                    'WHERE id = ?',
                    (1 if fav else 0, 0.95 if fav else None, pt._now(), cid))
        pt._ledger(row[1], 'user', 'favorite' if fav else 'unfavorite',
                   layer=row[0], target=cid, cursor=cur,
                   summary=(f"{'favorited' if fav else 'unfavorited'} [{cid}]: "
                            f"\"{row[2][:60]}{'…' if len(row[2]) > 60 else ''}\""))
        conn.commit()
    _publish(row[0], row[1], 'update')
    return {'success': True, 'favorite': fav}


def list_entities(query=None, **_):
    """L2 spine for the Entities view: per-entity chunk + edge counts."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope = (query or {}).get('scope') or 'default'
    with pt._get_connection() as conn:
        cur = conn.cursor()
        rows = cur.execute(
            'SELECT e.id, e.name, e.kind, e.mentions, e.meta, e.created, e.updated, '
            '  (SELECT COUNT(*) FROM chunks c WHERE c.entity_id = e.id) AS chunk_count, '
            "  (SELECT COUNT(*) FROM edges d WHERE d.dst_type = 'entity' AND d.dst_id = e.id) AS edge_count, "
            '  (SELECT content FROM chunks c WHERE c.entity_id = e.id AND c.tier = 1 '
            "   ORDER BY COALESCE(json_extract(c.meta, '$.headline'), 0) DESC, "
            '   c.created DESC LIMIT 1) AS headline '
            'FROM entities e WHERE e.scope = ? '
            'ORDER BY edge_count DESC, e.name COLLATE NOCASE', (scope,)).fetchall()
    return {'entities': [
        {'id': r[0], 'name': r[1], 'kind': r[2], 'mentions': r[3],
         'meta': _parse_meta(r[4]), 'created': r[5], 'updated': r[6],
         'chunk_count': r[7], 'edge_count': r[8], 'headline': r[9]} for r in rows]}


def entity_detail(eid=None, **_):
    """One entity: tiered chunks + 'mentioned in' (edges → source chunks)."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        return {'error': 'Invalid entity id'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        e = cur.execute('SELECT id, name, scope, kind, mentions, meta, created, updated '
                        'FROM entities WHERE id = ?', (eid,)).fetchone()
        if not e:
            return {'error': 'Not found'}, 404
        chunks = cur.execute(
            'SELECT id, content, tier, label, favorite, private_key, meta, created '
            'FROM chunks WHERE entity_id = ? '
            'ORDER BY COALESCE(tier, 9), created DESC', (eid,)).fetchall()
        # Dark plugin layers stay out of 'mentioned in' too.
        dark_sql, dark_params = pt._dark_clause('c')
        dark_and = f"AND {dark_sql} " if dark_sql else ''
        mentions = cur.execute(
            f"SELECT c.id, c.layer, c.content, c.created, d.weight "
            f"FROM edges d JOIN chunks c ON c.id = d.src_id "
            f"WHERE d.dst_type = 'entity' AND d.dst_id = ? AND d.src_type = 'chunk' "
            f"{dark_and}"
            f"ORDER BY c.created DESC LIMIT 100", [eid, *dark_params]).fetchall()
    return {
        'entity': {'id': e[0], 'name': e[1], 'scope': e[2], 'kind': e[3],
                   'mentions': e[4], 'meta': _parse_meta(e[5]),
                   'created': e[6], 'updated': e[7]},
        'chunks': [{'id': c[0], 'content': c[1], 'tier': c[2], 'label': c[3],
                    'favorite': bool(c[4]), 'private_key': c[5],
                    'meta': _parse_meta(c[6]), 'created': c[7]} for c in chunks],
        'mentioned_in': [{'id': m[0], 'layer': m[1], 'content': m[2],
                          'created': m[3], 'weight': m[4]} for m in mentions],
    }


def update_entity(eid=None, body=None, **_):
    """L2 rework, UI edition: kind (template-driven, incl. user templates),
    template fields, and name (pencil in the modal header, 2026-07-16) are
    human-editable. Fields replace meta.fields wholesale — the form sends
    the complete set. body.headline edits the tier-1 short description
    (updates the newest tier-1 chunk in place, or creates it — every entity
    kind carries one, Krem's ruling 2026-07-11). Renames are safe by
    construction: edges bind by id, and the alias matcher reads the live
    name. Scope stays tool/import territory."""
    pt = _pt()
    tpl = _tpl()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        return {'error': 'Invalid entity id'}, 400
    b = body or {}
    kind = b.get('kind')
    if kind is not None:
        kind = str(kind).strip().lower() or None
    valid = tpl.valid_kinds()
    if kind is not None and kind not in valid:
        return {'error': f"kind must be one of {sorted(valid)} or empty"}, 400
    fields = b.get('fields')
    if fields is not None and not isinstance(fields, dict):
        return {'error': 'fields must be an object'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT scope, kind, meta, name FROM entities WHERE id = ?',
                          (eid,)).fetchone()
        if not row:
            return {'error': 'Not found'}, 404
        new_name = None
        if 'name' in b:
            candidate = str(b.get('name') or '').strip()[:80]
            if not candidate:
                return {'error': 'name cannot be empty'}, 400
            if candidate != row[3]:
                dup = cur.execute(
                    'SELECT id FROM entities WHERE scope = ? AND id != ? '
                    'AND name = ? COLLATE NOCASE', (row[0], eid, candidate)).fetchone()
                if dup:
                    return {'error': f'"{candidate}" already exists in this scope'}, 409
                new_name = candidate
        new_kind = kind if 'kind' in b else row[1]
        meta = _parse_meta(row[2]) or {}
        # Every edit lands in the ledger (2026-07-22 — she asked "who wrote
        # my Background?" and the ledger had never heard of field edits):
        # one combined row per save, field diffs by name, values in detail.
        changed, field_diff = [], None
        if 'kind' in b and new_kind != row[1]:
            changed.append(f"kind → {new_kind or 'none'}")
        if fields is not None:
            old_f = meta.get('fields') or {}
            spec = {f['key']: f for f in
                    tpl.get_templates().get(new_kind, {}).get('fields', [])}
            clean = {}
            for k, v in fields.items():
                k = str(k).strip()[:64]
                if not k:
                    continue
                if spec.get(k, {}).get('type') == 'bool' or isinstance(v, bool):
                    clean[k] = bool(v)
                else:
                    v = str(v).strip()
                    if v:
                        clean[k] = v[:512]
            diff_keys = sorted(k for k in {**old_f, **clean}
                               if old_f.get(k) != clean.get(k))
            if diff_keys:
                changed.append('fields: ' + ', '.join(diff_keys))
                field_diff = {k: [str(old_f.get(k, ''))[:200],
                                  str(clean.get(k, ''))[:200]]
                              for k in diff_keys}
            meta['fields'] = clean
        now = pt._now()
        headline_changed = False
        if 'headline' in b:
            # Only the CURATED chunk (meta.headline) is edit-in-place; a
            # librarian-promoted fact must never be clobbered by a
            # description edit — before this guard, editing the description
            # overwrote whatever tier-1 fact happened to be newest (Krem's
            # jank find, 2026-07-24). No curated chunk yet → create one.
            text = str(b.get('headline') or '').strip()[:512]
            head = cur.execute(
                "SELECT id FROM chunks WHERE entity_id = ? AND tier = 1 "
                "AND json_extract(meta, '$.headline') IS NOT NULL "
                "ORDER BY created DESC LIMIT 1", (eid,)).fetchone()
            if text:
                if head:
                    cur.execute('UPDATE chunks SET content = ?, updated = ?, '
                                'embedding = NULL WHERE id = ?', (text, now, head[0]))
                else:
                    hm = {'added_by': pt._added_by(), 'headline': True}
                    cur.execute(
                        "INSERT INTO chunks (layer, scope, content, entity_id, tier, "
                        "meta, created, updated) VALUES ('entities', ?, ?, ?, 1, ?, ?, ?)",
                        (row[0], text, eid, json.dumps(hm), now, now))
                headline_changed = True
                changed.append('description')
            elif head:
                cur.execute('DELETE FROM chunks WHERE id = ?', (head[0],))
                headline_changed = True
                changed.append('description cleared')
        cur.execute('UPDATE entities SET kind = ?, meta = ?, updated = ? WHERE id = ?',
                    (new_kind, json.dumps(meta) if meta else None, now, eid))
        if new_name:
            cur.execute('UPDATE entities SET name = ? WHERE id = ?', (new_name, eid))
            pt._ledger(row[0], 'user', 'update', layer='entities', target=eid,
                       cursor=cur,
                       summary=f'renamed entity "{row[3]}" → "{new_name}"')
        if changed:
            pt._ledger(row[0], 'user', 'update', layer='entities', target=eid,
                       cursor=cur,
                       detail={'fields': field_diff} if field_diff else None,
                       summary=(f'updated entity "{new_name or row[3]}" — '
                                + '; '.join(changed)))
        conn.commit()
    if headline_changed:
        pt.reset_backfill_latch()   # headline re-embeds on the next sweep
    _publish('entities', row[0], 'update')
    return {'success': True, 'kind': new_kind, 'fields': meta.get('fields'),
            'name': new_name or row[3]}


def delete_entity(eid=None, **_):
    """Delete an entity, its own chunks (headline/facts/trivia), and every
    edge touching either. Memories that MENTIONED it survive — they just
    lose the link. Trusted UI surface; the AI has no entity-delete verb."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        return {'error': 'Invalid entity id'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT name, scope FROM entities WHERE id = ?',
                          (eid,)).fetchone()
        if not row:
            return {'error': 'Not found'}, 404
        chunk_ids = [r[0] for r in cur.execute(
            'SELECT id FROM chunks WHERE entity_id = ?', (eid,)).fetchall()]
        if chunk_ids:
            marks = ','.join('?' * len(chunk_ids))
            cur.execute(f"DELETE FROM edges WHERE (src_type = 'chunk' AND src_id IN ({marks})) "
                        f"OR (dst_type = 'chunk' AND dst_id IN ({marks}))",
                        chunk_ids + chunk_ids)
            cur.execute(f'DELETE FROM chunks WHERE id IN ({marks})', chunk_ids)
        cur.execute("DELETE FROM edges WHERE (src_type = 'entity' AND src_id = ?) "
                    "OR (dst_type = 'entity' AND dst_id = ?)", (eid, eid))
        cur.execute('DELETE FROM entities WHERE id = ?', (eid,))
        pt._ledger(row[1], 'user', 'deleted', layer='entities', target=row[0],
                   cursor=cur,
                   summary=(f"deleted entity '{row[0]}'"
                            + (f" and its {len(chunk_ids)} chunks" if chunk_ids else '')))
        conn.commit()
    _publish('entities', row[1], 'delete')
    return {'success': True, 'name': row[0], 'deleted_chunks': len(chunk_ids)}


def list_templates(**_):
    """Entity kind templates, ordered — drives the kind pills, the kind
    selector, and the per-kind fields form."""
    tpl = _tpl()
    ts = sorted(tpl.get_templates().values(), key=lambda t: (t['order'], t['kind']))
    return {'templates': ts}


def librarian_run(body=None, **_):
    """Kick a librarian pass (the Admin-card Run buttons). body: scope, what,
    pass ('sort' default | 'dates' | 'link' | 'dedup'; legacy names resolve).
    Run is explicit human intent — the per-pass Admin toggles only shape the
    nightly recipe, never this."""
    from plugins.mindpalace.tools import librarian
    b = body or {}
    msg, ok = librarian.start(b.get('scope'), what=b.get('what', 'all'),
                              kind=b.get('pass', 'sort'))
    return ({'success': True, 'message': msg} if ok
            else ({'success': False, 'error': msg}, 409))


def librarian_status(query=None, **_):
    from plugins.mindpalace.tools import librarian
    out = librarian.get_status((query or {}).get('scope'))
    out['enabled'] = librarian._enabled()   # alpha toggle → UI hides the buttons
    return out


def get_resident(query=None, **_):
    """GET resident?scope= → the scope's residency: prompt, model, and the
    librarian pass opt-ins (Self page Resident strip)."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    scope = (query or {}).get('scope') or 'default'
    return {'scope': scope, 'resident': pt.scope_resident(scope)}


def put_resident(body=None, **_):
    """PUT resident {scope, prompt?, model?, passes?, watched_prompt?} —
    omitted fields keep their value; '' clears prompt/model/watched_prompt;
    passes replaces the opt-in dict."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    old = pt.scope_resident(scope)
    ok = pt.set_scope_resident(scope, prompt=b.get('prompt'),
                               provider=b.get('provider'),
                               model=b.get('model'), passes=b.get('passes'),
                               watched_prompt=b.get('watched_prompt'),
                               prompt_ledger=b.get('prompt_ledger'))
    if not ok:
        return {'error': 'save failed'}, 500
    new = pt.scope_resident(scope)
    # Ledger (tamper evidence): who speaks as this scope is exactly the kind
    # of change she must be able to see. No-op saves stay silent.
    changed, diff = [], {}
    for k in ('prompt', 'provider', 'model', 'watched_prompt'):
        if old[k] != new[k]:
            changed.append(f"{k} → {new[k] or 'none'}")
            diff[k] = [old[k] or '', new[k] or '']
    if old['prompt_ledger'] != new['prompt_ledger']:
        # Turning recording OFF still lands this row: the blind window's
        # first line is the fact that it began.
        state = 'on' if new['prompt_ledger'] else 'off'
        changed.append(f'prompt ledger → {state}')
        diff['prompt_ledger'] = ['on' if old['prompt_ledger'] else 'off', state]
    if old['passes'] != new['passes']:
        on = sorted(k for k, v in new['passes'].items() if v)
        changed.append('passes → ' + (', '.join(on) or 'none'))
        diff['passes'] = [json.dumps(old['passes']), json.dumps(new['passes'])]
    if changed:
        pt._ledger(scope, 'user', 'edited', layer='self', target='resident',
                   summary='resident: ' + '; '.join(changed),
                   detail={'fields': diff})
    return {'success': True, 'resident': new}


def librarian_queue_depth(query=None, **_):
    """GET librarian/queue-depth?scope=&kind= → how many items this pass still
    has to process, batch math, and a ROUGH token range — feeds the Run ALL
    confirm dialog with real numbers."""
    import math
    from plugins.mindpalace.tools import librarian
    from core.plugin_loader import plugin_loader
    q = query or {}
    scope = (q.get('scope') or '').strip()
    kind = (q.get('kind') or 'sort').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    depth = librarian._queue_depth(scope, kind)
    try:
        batch = max(1, int(plugin_loader.get_plugin_settings('mindpalace')
                           .get('librarian_batch_size', 20)))
    except (TypeError, ValueError):
        batch = 20
    batches = math.ceil(depth / batch) if depth else 0
    # Rough per-batch token cost by kind (sort is the hog — one verb per item).
    per = {'dates': 4000, 'link': 5000, 'dedup': 4000, 'sort': 12000}.get(kind, 6000)
    est = batches * per
    return {'scope': scope, 'kind': kind, 'depth': depth, 'batch_size': batch,
            'batches': batches, 'est_tokens_low': int(est * 0.6),
            'est_tokens_high': int(est * 2.0)}


def librarian_drain(body=None, **_):
    """POST librarian/drain {scope, pass, what?} — Run ALL: empty the queue in
    back-to-back batches (own chat each), cap-bypassed. Human intent only."""
    from plugins.mindpalace.tools import librarian
    b = body or {}
    msg, ok = librarian.drain(b.get('scope'), kind=b.get('pass', 'sort'),
                              what=b.get('what', 'all'))
    return ({'success': True, 'message': msg} if ok
            else ({'success': False, 'error': msg}, 409))


def librarian_drain_stop(body=None, **_):
    """POST librarian/drain-stop — ask a running drain to stop after its
    current batch. Finished batches stay done."""
    from plugins.mindpalace.tools import librarian
    msg, ok = librarian.drain_stop()
    return {'success': ok, 'message': msg}


def librarian_dedup_preview(query=None, **_):
    """Dry-run the dedup similarity scan — the mechanical half only. No LLM,
    no dedup_at stamps, no daily-cap spend: shows exactly what a real dedup
    pass WOULD present (clusters + members at the current threshold), so the
    threshold can be judged against the actual corpus before the model ever
    holds merge_memories."""
    from plugins.mindpalace.tools import librarian, librarian_tools as lt
    q = query or {}
    scope = (q.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    thr = lt._merge_threshold()
    with pt._get_connection() as conn:
        cur = conn.cursor()
        cfg = librarian._settings()
        batch = librarian.build_dedup_batch(cur, scope, cfg['batch'])
        dups = librarian.find_duplicates(cur, scope, batch, thr)
        partner_ids = sorted({d for hits in dups.values() for d, _ in hits}
                             - {b[0] for b in batch})
        partners = {}
        if partner_ids:
            ph = ','.join('?' * len(partner_ids))
            partners = {r[0]: (r[1], r[2]) for r in cur.execute(
                f'SELECT id, created, content FROM chunks WHERE id IN ({ph})',
                partner_ids).fetchall()}
        clusters = librarian._build_clusters(batch, dups, partners)
    return {'scope': scope, 'threshold': thr, 'scanned': len(batch),
            'batch_limit': cfg['batch'],
            'clusters': [
                {'ids': [cid for cid, _cr, _c in cl],
                 'previews': [f"[{cid}] {(cr or '')[:10]} · {(c or '')[:80]}"
                              for cid, cr, c in cl]}
                for cl in clusters]}


# ─── Human Dedup (Mind → Admin, 2026-07-26) ─────────────────────────────────
# Mechanical candidate scans + a human-only entity fold. The librarian's
# dedup pass stays the AI's lane (memories, similarity-gated merges); this
# is the trusted-surface twin: the human sees each pair and decides.

MAX_DEDUP_PAIRS = 50
MAX_DEDUP_SCAN = 2000   # newest embedded chunks entering the pairwise scan


def _cosine_pairs(rows, thr):
    """Highest-similarity pairs among embedded chunk rows, grouped by
    (provider, dim) — cosine only compares like with like. rows carry
    (id, content, created, layer, label, favorite, embedding, provider,
    dim). Per-row garbage (short blobs, NULL dim) is SKIPPED; anything
    bigger RAISES to the caller — a scan that dies must never read as a
    clean shelf (Lane-4/5 scouts, 2026-07-27). The caller bounds rows at
    MAX_DEDUP_SCAN, so the sims matrix stays small."""
    import numpy as np
    groups = {}
    for r in rows:
        if not r[8]:
            continue          # unverifiable vector space — skip the row
        try:
            v = np.frombuffer(r[6], dtype=np.float32)
        except Exception:
            continue          # blob length not a multiple of 4 — skip
        if v.shape[0] != r[8]:
            continue
        groups.setdefault((r[7], r[8]), []).append((r, v))
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        mat = np.stack([v for _r, v in members])
        sims = mat @ mat.T
        for i, j in np.argwhere(np.triu(sims >= thr, k=1)):
            out.append((float(sims[i, j]), members[i][0], members[j][0]))
    out.sort(key=lambda p: -p[0])
    return out


def _entity_pairs(cur, scope):
    """Same-scope entity pairs whose names look like the same thing —
    the Sky/Skye class the exact-NOCASE door check can't catch. Fuzzy
    ratio ≥ 0.75, containment (≥3 chars), or one's name already among the
    other's nicknames. difflib, no model; the human rules on every pair."""
    import difflib
    rows = cur.execute(
        'SELECT id, name, kind, mentions, created, meta FROM entities '
        'WHERE scope = ? ORDER BY id', (scope,)).fetchall()
    ents = []
    for eid, name, kind, mentions, created, meta_raw in rows:
        fields = (_parse_meta(meta_raw) or {}).get('fields') or {}
        head = cur.execute(
            "SELECT content FROM chunks WHERE entity_id = ? AND tier = 1 "
            "ORDER BY COALESCE(json_extract(meta, '$.headline'), 0) DESC, "
            "created DESC LIMIT 1", (eid,)).fetchone()
        ents.append({'id': eid, 'name': name, 'kind': kind,
                     'mentions': mentions or 0, 'created': created,
                     'headline': (head[0][:200] if head else ''),
                     'nfields': sum(1 for v in fields.values() if v),
                     '_nicks': {n.strip().lower() for n in
                                str(fields.get('nicknames') or '').split(',')
                                if n.strip()}})
    pairs = []
    for i in range(len(ents)):
        for j in range(i + 1, len(ents)):
            a, b = ents[i], ents[j]
            an, bn = a['name'].lower(), b['name'].lower()
            sim = difflib.SequenceMatcher(None, an, bn).ratio()
            if an in b['_nicks'] or bn in a['_nicks']:
                sim = 1.0
            elif (len(an) >= 3 and an in bn) or (len(bn) >= 3 and bn in an):
                sim = max(sim, 0.9)
            if sim >= 0.75:
                pairs.append((round(sim, 3), a, b))
    pairs.sort(key=lambda p: -p[0])
    strip = lambda e: {k: v for k, v in e.items() if k != '_nicks'}
    return {'what': 'entities', 'scope': scope, 'threshold': 0.75,
            'scanned': len(ents), 'found': len(pairs),
            'pairs': [{'sim': s, 'a': strip(a), 'b': strip(b)}
                      for s, a, b in pairs[:MAX_DEDUP_PAIRS]]}


def dedup_candidates(query=None, **_):
    """GET dedup/candidates?scope=&what= — the Human Dedup scan, mechanical
    and read-only. what='memories' (events + opt-in layers) or
    'knowledge' pairs by stored-embedding cosine at the librarian merge
    threshold; 'entities' pairs by name similarity. No LLM, no dedup_at
    stamps, no cap spend — the human resolves each pair in the modal
    (memories/knowledge → DELETE chunks/{id}, entities → POST
    entities/merge). Favorites are shown flagged, never hidden: unlike the
    librarian, this surface is trusted with them."""
    q = query or {}
    scope = (q.get('scope') or '').strip()
    what = (q.get('what') or 'memories').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    if what not in ('memories', 'knowledge', 'entities'):
        return {'error': 'what must be memories | knowledge | entities'}, 400
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    with pt._get_connection() as conn:
        cur = conn.cursor()
        if what == 'entities':
            return _entity_pairs(cur, scope)
        from plugins.mindpalace.tools import librarian, librarian_tools as lt
        layers = (('knowledge',) if what == 'knowledge'
                  else ('events',) + librarian._optin_layers())
        thr = lt._merge_threshold()
        ph = ','.join('?' * len(layers))
        rows = cur.execute(f'''
            SELECT id, content, created, layer, label, favorite,
                   embedding, embedding_provider, embedding_dim, meta
            FROM chunks WHERE scope = ? AND layer IN ({ph})
            AND embedding IS NOT NULL
            AND (meta IS NULL OR (
                json_extract(meta, '$.pruned_at') IS NULL
                AND json_extract(meta, '$.section') IS NULL
                AND json_extract(meta, '$.superseded_at') IS NULL))
            ORDER BY created DESC LIMIT ?''',
            (scope, *layers, MAX_DEDUP_SCAN)).fetchall()
        try:
            pairs = _cosine_pairs(rows, thr)
        except Exception as e:
            # LOUD: an error is not an empty shelf.
            logger.error(f"[MINDPALACE] dedup scan failed: {e}")
            return {'error': f'dedup scan failed: {e}'}, 500
    found = len(pairs)                     # no silent caps — say what dropped
    pairs = pairs[:MAX_DEDUP_PAIRS]
    def side(r):
        return {'id': r[0], 'content': r[1][:600], 'created': r[2],
                'layer': r[3], 'label': r[4], 'favorite': bool(r[5])}
    out = []
    for s, a, b in pairs:
        ma, mb = _parse_meta(a[9]) or {}, _parse_meta(b[9]) or {}
        # Lineage pairs surface FLAGGED: one side is a promotion/rewording of
        # the other (often her third→first-person retelling) — the human
        # should know they're judging her voice, not an accident. Type-guarded:
        # a stray int/str promoted_to must degrade to unflagged, never 500.
        pa = ma.get('promoted_to')
        pb = mb.get('promoted_to')
        lin = (ma.get('derived_from') == b[0] or mb.get('derived_from') == a[0]
               or (isinstance(pa, list) and b[0] in pa)
               or (isinstance(pb, list) and a[0] in pb))
        out.append({'sim': round(s, 3), 'lineage': lin,
                    'a': side(a), 'b': side(b)})
    return {'what': what, 'scope': scope, 'threshold': thr,
            'scanned': len(rows), 'found': found, 'pairs': out}


def merge_entities(body=None, **_):
    """POST entities/merge {keeper, loser} — Human Dedup's fold. Everything
    the duplicate accumulated moves to the keeper: mention edges (deduped
    after the repoint — edges has no UNIQUE constraint), tier chunks,
    mention count, blank template fields. The duplicate's name and
    nicknames join the keeper's nicknames, so the alias matcher folds
    future mentions instead of re-minting the split — the merge closes the
    leak, not just the puddle. If both carry a curated headline the
    keeper's stays the card line (the loser's flag drops, its text
    survives as a plain fact). Trusted UI surface; the AI has no
    entity-merge verb (same rule as delete). Same scope only."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    try:
        keeper, loser = int(b.get('keeper')), int(b.get('loser'))
    except (TypeError, ValueError):
        return {'error': 'keeper and loser entity ids required'}, 400
    if keeper == loser:
        return {'error': 'keeper and loser must differ'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        rows = {r[0]: r for r in cur.execute(
            'SELECT id, name, scope, kind, mentions, meta FROM entities '
            'WHERE id IN (?, ?)', (keeper, loser)).fetchall()}
        if len(rows) != 2:
            return {'error': 'Not found'}, 404
        k, l = rows[keeper], rows[loser]
        if k[2] != l[2]:
            return {'error': 'entities live in different scopes'}, 400
        k_head = cur.execute(
            "SELECT id FROM chunks WHERE entity_id = ? AND tier = 1 "
            "AND json_extract(meta, '$.headline') IS NOT NULL",
            (keeper,)).fetchone()
        if k_head:
            cur.execute(
                "UPDATE chunks SET meta = json_remove(meta, '$.headline') "
                "WHERE entity_id = ? "
                "AND json_extract(meta, '$.headline') IS NOT NULL", (loser,))
        moved_chunks = cur.execute(
            'UPDATE chunks SET entity_id = ? WHERE entity_id = ?',
            (keeper, loser)).rowcount
        moved_edges = 0
        for col in ('src', 'dst'):
            moved_edges += cur.execute(
                f"UPDATE edges SET {col}_id = ? WHERE {col}_type = 'entity' "
                f"AND {col}_id = ?", (keeper, loser)).rowcount
        cur.execute('''
            DELETE FROM edges WHERE id NOT IN (
                SELECT MIN(id) FROM edges GROUP BY src_type, src_id,
                dst_type, dst_id, kind)
            AND ((src_type = 'entity' AND src_id = ?)
                 OR (dst_type = 'entity' AND dst_id = ?))''',
            (keeper, keeper))
        km = _parse_meta(k[5]) or {}
        lf = (_parse_meta(l[5]) or {}).get('fields') or {}
        kf = km.get('fields') or {}
        absorbed = []
        for key, val in lf.items():
            # Booleans are PERMISSION gates (allow_call/allow_email feed the
            # phone/email whitelists), and "unset" is indistinguishable from
            # "explicitly denied" here — so bools NEVER absorb (Lane-4
            # scout, 2026-07-27: a merge click must not grant the AI calling
            # rights the human never set on the keeper).
            if isinstance(val, bool) or key == 'nicknames' or not val:
                continue
            if not kf.get(key):
                kf[key] = val
                absorbed.append(key)
        nicks = [n.strip() for n in
                 str(kf.get('nicknames') or '').split(',') if n.strip()]
        seen_n = {n.lower() for n in nicks} | {k[1].lower()}
        for cand in [l[1]] + [n.strip() for n in
                              str(lf.get('nicknames') or '').split(',')]:
            if cand and cand.lower() not in seen_n:
                nicks.append(cand)
                seen_n.add(cand.lower())
        if nicks:
            kf['nicknames'] = ', '.join(nicks)
        km['fields'] = kf
        cur.execute(
            'UPDATE entities SET mentions = COALESCE(mentions, 0) + ?, '
            'meta = ?, kind = COALESCE(kind, ?), updated = ? WHERE id = ?',
            (l[4] or 0, json.dumps(km), l[3], pt._now(), keeper))
        cur.execute('DELETE FROM entities WHERE id = ?', (loser,))
        pt._ledger(k[2], 'user', 'update', layer='entities', target=keeper,
                   cursor=cur,
                   detail={'absorbed_fields': absorbed} if absorbed else None,
                   summary=(f'merged duplicate entity "{l[1]}" into "{k[1]}" '
                            f'— {moved_edges} links + {moved_chunks} facts '
                            f'moved, "{l[1]}" kept as nickname'
                            + (f', fields absorbed: {", ".join(absorbed)}'
                               if absorbed else '')))
        conn.commit()
    _publish('entities', k[2], 'update')
    return {'success': True, 'keeper': keeper, 'name': k[1],
            'moved_edges': moved_edges, 'moved_chunks': moved_chunks}


def maintenance(body=None, **_):
    """Self-serve rescue actions (Mind → Admin). Every action is
    an UPDATE on metadata columns/keys — memory content is never touched and
    nothing is deleted, by construction. Alpha-feature support: undo what
    importance or the librarian wrote, per scope."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    action = (b.get('action') or '').strip()
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400

    if action == 'clear_ledger':
        # Debug/reset valve (Krem, 2026-07-24): raze this scope's ledger
        # HISTORY — rows + read watermark. Memories untouched. Typed-confirm,
        # human-only. The clearing itself is recorded as the first row of the
        # fresh ledger: an emptied tamper log that doesn't say why it's empty
        # would itself read as tampering.
        if (b.get('confirm') or '') != scope:
            return {'error': 'confirm must equal the scope name exactly'}, 400
        with pt._get_connection() as conn:
            cur = conn.cursor()
            n = cur.execute('SELECT COUNT(*) FROM ledger WHERE scope = ?',
                            (scope,)).fetchone()[0]
            cur.execute('DELETE FROM ledger WHERE scope = ?', (scope,))
            cur.execute('DELETE FROM ledger_reads WHERE scope = ?', (scope,))
            pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                       summary=f'ledger cleared — {n} entries removed')
            conn.commit()
        return {'success': True, 'cleared': n}

    if action == 'fold_promotion_clones':
        # TRANSITION SCAFFOLDING (remove after every install has run it once).
        # The old sort pass copied memories onto the self layer; the
        # 2026-07-26 retirement moved the copies back beside their originals.
        # Two clone classes, both proved by meta lineage (was_self_layer +
        # derived_from), both requiring a live original:
        #   identical (word-for-word after normalization) → deleted; nothing
        #     is lost, the original carries the richer meta.
        #   reworded (her third→first-person retellings) → deleted AND the
        #     original re-queued for her sort pass (librarian_at cleared) —
        #     she re-judges those 58 herself under the new architecture
        #     instead of a human ruling on her voice pair by pair (Krem's
        #     call 2026-07-26; Sapph pre-agreed to the test window).
        # Favorited clones are NEVER touched. Typed-confirm, human-only.
        if (b.get('confirm') or '') != scope:
            return {'error': 'confirm must equal the scope name exactly'}, 400
        norm = lambda s: re.sub(r'\s+', ' ', (s or '')).strip().lower()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            info = {}
            for cid, content, fav, meta_raw in cur.execute(
                    "SELECT id, content, favorite, meta FROM chunks "
                    "WHERE scope = ? AND layer = 'events'", (scope,)).fetchall():
                info[cid] = (content, fav, _parse_meta(meta_raw) or {})
            identical, reworded, kept = [], [], 0
            for cid, (content, fav, m) in info.items():
                src = m.get('derived_from')
                if not m.get('was_self_layer') or not isinstance(src, int) \
                        or src not in info:
                    continue
                if m.get('superseded_at') or m.get('pruned_at'):
                    continue
                sc, _sf, sm = info[src]
                if sm.get('superseded_at') or sm.get('pruned_at'):
                    continue   # original retired → the clone is the live copy
                if fav:
                    kept += 1
                    continue
                (identical if norm(content) == norm(sc)
                 else reworded).append(cid)
            folded = identical + reworded
            requeue_srcs = {info[c][2]['derived_from'] for c in reworded}
            if folded:
                marks = ','.join('?' * len(folded))
                cur.execute(f"DELETE FROM edges WHERE (src_type = 'chunk' AND "
                            f"src_id IN ({marks})) OR (dst_type = 'chunk' AND "
                            f"dst_id IN ({marks}))", folded + folded)
                cur.execute(f'DELETE FROM chunks WHERE id IN ({marks})', folded)
                # Scrub dangling promoted_to pointers on every survivor; clear
                # the sort stamp only where her judgment is being re-asked.
                gone = set(folded)
                for cid in {info[c][2]['derived_from'] for c in folded}:
                    m = dict(info[cid][2])
                    changed = False
                    left = [p for p in (m.get('promoted_to') or [])
                            if p not in gone and p in info]   # live ids only
                    if left != m.get('promoted_to'):
                        if left:
                            m['promoted_to'] = left
                        else:
                            m.pop('promoted_to', None)
                        changed = True
                    if cid in requeue_srcs and 'librarian_at' in m:
                        m.pop('librarian_at')
                        changed = True
                    if changed:
                        cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                                    (json.dumps(m, ensure_ascii=False), cid))
                pt._ledger(scope, 'user', 'deleted', layer='events', cursor=cur,
                           detail={'identical_ids': identical,
                                   'reworded_ids': reworded,
                                   'requeued_ids': sorted(requeue_srcs)},
                           summary=(f'folded {len(identical)} identical and '
                                    f'retired {len(reworded)} reworded '
                                    f'promotion clone(s); {len(requeue_srcs)} '
                                    f'original(s) re-queued for your sort '
                                    f'pass — re-judge them in your own time'
                                    + (f'; {kept} favorited kept' if kept
                                       else '')))
            conn.commit()
        if folded:
            _publish('events', scope, 'delete')
        return {'success': True, 'folded_identical': len(identical),
                'retired_reworded': len(reworded),
                'requeued': len(requeue_srcs), 'kept_favorited': kept}

    if action == 'reset_importance':
        # Known-good state: favorites and permanent goals at 0.95 (the
        # never-fades band), everything else unrated. Rescues bad librarian
        # ratings and any decay/boost drift in one click. Self-sheet sections
        # are structural, not maintenance territory — identity carries a
        # designed 0.95 pin (self_tools) that must survive a reset.
        with pt._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chunks SET importance = NULL WHERE scope = ? "
                "AND favorite = 0 AND importance IS NOT NULL "
                "AND COALESCE(label, '') != 'self-sheet' "
                "AND COALESCE(json_extract(meta, '$.permanent'), 0) != 1",
                (scope,))
            cleared = cur.rowcount
            cur.execute(
                "UPDATE chunks SET importance = 0.95 WHERE scope = ? "
                "AND (favorite = 1 OR COALESCE(json_extract(meta, '$.permanent'), 0) = 1) "
                "AND (importance IS NULL OR importance != 0.95)",
                (scope,))
            restored = cur.rowcount
            if cleared or restored:
                pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                           summary=(f"reset importance: {cleared} ratings cleared, "
                                    f"{restored} favorites/permanents restored to 0.95"))
            conn.commit()
        _publish('events', scope, 'update')
        return {'success': True, 'cleared': cleared, 'restored': restored}

    if action == 'restore_retired':
        # Bulk reversal of librarian prunes. Atomize/merge retirements are
        # skipped — their content lives on in derived chunks, and restoring
        # the originals would mint duplicates (each is still restorable
        # one-by-one from the Memories view, where the tradeoff is visible).
        restored, skipped = 0, 0
        with pt._get_connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, meta FROM chunks WHERE scope = ? "
                "AND json_extract(meta, '$.pruned_at') IS NOT NULL",
                (scope,)).fetchall()
            for cid, raw in rows:
                meta = _parse_meta(raw) or {}
                if meta.get('atomized_into') or meta.get('merged_into'):
                    skipped += 1
                    continue
                for k in ('pruned_at', 'pruned_reason'):
                    meta.pop(k, None)
                cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                            (json.dumps(meta, ensure_ascii=False), cid))
                restored += 1
            if restored:
                pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                           summary=(f"restored {restored} retired memories to her recall "
                                    f"({skipped} atomize/merge retirements kept)"))
            conn.commit()
        _publish('events', scope, 'update')
        return {'success': True, 'restored': restored, 'skipped': skipped}

    if action == 'import_v1':
        # Bulk copy from the classic system (all stores, ALL scopes, additive,
        # idempotent — re-runs copy zero duplicates). The v1 databases are
        # opened READ-ONLY and never modified: they stay the pristine import
        # source and switch-back path. Metadata backfill runs inside.
        from plugins.mindpalace.tools import import_tools
        summary = import_tools._run_import('all', _config())
        _publish('events', scope, 'update')
        _publish('entities', scope, 'update')
        return {'success': True, 'summary': summary}

    if action == 'generate_metadata':
        # Re-derive mechanical metadata outside create-time: stats/temporal/
        # noun stamps + entity-mention edges on every chunk missing its md_v
        # marker (idempotent), then clear the embedding latch so missing
        # vectors re-embed on the next search sweep.
        from plugins.mindpalace.tools import metadata
        res = metadata.backfill()
        if 'error' in res:
            return {'error': res['error']}, 500
        pt.reset_backfill_latch()
        _publish('events', scope, 'update')
        return {'success': True, 'stamped': res.get('stamped', 0),
                'edges': res.get('edges', 0)}

    if action == 'clear_layer':
        # Fine-grained tester reset: hard-delete ONE tab's data in ONE scope
        # (Krem 2026-07-19 — "clear a SINGLE tab, not the whole scope").
        # Same double gate as wipe_scope: typed scope name, server re-check.
        # layer 'knowledge' clears BOTH stores: knowledge chunks in mind.db
        # AND the scope's Library (docs, files, collections, watch folders).
        # 'entities' clears entity rows + their chunks. Memory v1 untouched.
        if (b.get('confirm') or '') != scope:
            return {'error': 'confirm must equal the scope name exactly'}, 400
        layer = (b.get('layer') or '').strip()
        if layer not in ('events', 'self', 'entities', 'goals', 'knowledge'):
            return {'error': f"unknown layer '{layer}'"}, 400
        with pt._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM edges WHERE "
                "(src_type = 'chunk' AND src_id IN "
                " (SELECT id FROM chunks WHERE scope = ? AND layer = ?)) "
                "OR (dst_type = 'chunk' AND dst_id IN "
                " (SELECT id FROM chunks WHERE scope = ? AND layer = ?))",
                (scope, layer, scope, layer))
            edges = cur.rowcount
            cur.execute("DELETE FROM chunks WHERE scope = ? AND layer = ?",
                        (scope, layer))
            chunks = cur.rowcount
            ents = 0
            if layer == 'entities':
                cur.execute(
                    "DELETE FROM edges WHERE "
                    "(src_type = 'entity' AND src_id IN "
                    " (SELECT id FROM entities WHERE scope = ?)) "
                    "OR (dst_type = 'entity' AND dst_id IN "
                    " (SELECT id FROM entities WHERE scope = ?))",
                    (scope, scope))
                edges += cur.rowcount
                cur.execute("DELETE FROM entities WHERE scope = ?", (scope,))
                ents = cur.rowcount
            try:
                from plugins.mindpalace.tools import ledger as lg
                lg.record(scope, 'user', 'cleared', layer=layer,
                          summary=(f"cleared the {layer} tab: {chunks} "
                                   f"memories, {edges} connections"
                                   + (f", {ents} entities" if ents else "")),
                          cursor=cur)
            except Exception as e:
                logger.warning(f"[MINDPALACE] clear ledger row skipped: {e}")
            conn.commit()
        lib_docs = 0
        if layer == 'knowledge':
            try:
                from plugins.mindpalace.tools import library
                lib_docs = library.delete_scope_library(scope)
            except Exception as e:
                logger.warning(f"[MINDPALACE] library clear failed: {e}")
        pt.bump_matrix_gen()
        return {'deleted_chunks': chunks, 'deleted_entities': ents,
                'deleted_edges': edges, 'library_docs_deleted': lib_docs}

    if action == 'wipe_scope':
        # Tester reset: hard-delete EVERYTHING in one scope — chunks (FTS
        # triggers clean the index), entities, their edges, the scope's
        # librarian counters, AND the scope's Library (docs + files, below).
        # The ledger survives and gains a wipe row: the audit trail outlives
        # the data. Double-gated — the UI makes
        # the user type the scope name, and this route refuses unless
        # body.confirm matches it exactly, so a stray POST can never wipe.
        # Memory v1 is a different database entirely; this cannot reach it.
        if (b.get('confirm') or '') != scope:
            return {'error': 'confirm must equal the scope name exactly'}, 400
        with pt._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM edges WHERE "
                "(src_type = 'chunk' AND src_id IN (SELECT id FROM chunks WHERE scope = ?)) "
                "OR (dst_type = 'chunk' AND dst_id IN (SELECT id FROM chunks WHERE scope = ?)) "
                "OR (src_type = 'entity' AND src_id IN (SELECT id FROM entities WHERE scope = ?)) "
                "OR (dst_type = 'entity' AND dst_id IN (SELECT id FROM entities WHERE scope = ?))",
                (scope, scope, scope, scope))
            edges = cur.rowcount
            cur.execute("DELETE FROM chunks WHERE scope = ?", (scope,))
            chunks = cur.rowcount
            cur.execute("DELETE FROM entities WHERE scope = ?", (scope,))
            ents = cur.rowcount
            try:
                cur.execute("DELETE FROM librarian_state WHERE scope = ?", (scope,))
            except Exception:
                pass   # table is lazily created by the librarian; absent = nothing to clear
            pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                       summary=(f"wiped scope: {chunks} memories, {ents} entities, "
                                f"{edges} connections deleted"))
            conn.commit()
        # The Library goes WITH the wipe — this route predated it and razed
        # mind.db only, leaving the scope's docs (and files) alive in the
        # Knowledge tab (Krem's live find, 2026-07-19). Same cascade as
        # scope-delete and clear-knowledge.
        lib_docs = 0
        try:
            from plugins.mindpalace.tools import library
            lib_docs = library.delete_scope_library(scope)
        except Exception as e:
            logger.warning(f"[MINDPALACE] wipe: library teardown failed: {e}")
        _publish('events', scope, 'update')
        _publish('entities', scope, 'update')
        return {'success': True, 'deleted_chunks': chunks,
                'deleted_entities': ents, 'deleted_edges': edges,
                'library_docs_deleted': lib_docs}

    if action == 'redate_regex':
        # Re-run the temporal floor over the whole scope, each chunk anchored
        # to its own created date — the regex-tuning loop (md_v gates normal
        # backfill, so improved rules need this to reach old rows). Librarian
        # verdicts (temporal_at / src='librarian') are terminal and kept.
        from plugins.mindpalace.tools import temporal
        stamped = cleared = kept = recurred = 0
        with pt._get_connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, content, created, meta FROM chunks WHERE scope = ? "
                "AND json_extract(meta, '$.superseded_at') IS NULL", (scope,)).fetchall()
            for cid, content, created, raw in rows:
                meta = _parse_meta(raw) or {}
                dirty = False
                # recurring_dates is regex-OWNED (librarian never writes it),
                # so it refreshes even on verdict-protected chunks.
                rec = temporal.recurring(content)
                if rec != (meta.get('recurring_dates') or []):
                    if rec:
                        meta['recurring_dates'] = rec
                    else:
                        meta.pop('recurring_dates', None)
                    recurred += 1
                    dirty = True
                if meta.get('temporal_at') or meta.get('event_date_src') == 'librarian':
                    kept += 1
                else:
                    dates = temporal.resolve(content, created)
                    had = 'event_dates' in meta
                    if dates and meta.get('event_dates') != dates:
                        meta['event_dates'] = dates
                        meta['event_date_src'] = 'regex'
                        stamped += 1
                        dirty = True
                    elif not dates and had:
                        meta.pop('event_dates', None)
                        meta.pop('event_date_src', None)
                        cleared += 1
                        dirty = True
                if dirty:
                    cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                                (json.dumps(meta, ensure_ascii=False), cid))
            if stamped or cleared or recurred:
                pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                           summary=(f"redated with regex floor: {stamped} stamped, "
                                    f"{cleared} cleared, {recurred} recurring, "
                                    f"{kept} librarian verdicts kept"))
            conn.commit()
        _publish('events', scope, 'update')
        return {'success': True, 'stamped': stamped, 'cleared': cleared,
                'recurring': recurred, 'kept': kept}

    if action == 'redate_model':
        # Requeue every date candidate for the librarian's temporal pass —
        # verdicts reopen (temporal_at + src cleared; existing dates stay as
        # current-best until the model re-rules) — then kick one pass. Caps
        # apply: batch-size chunks per pass, passes-per-day per kind; re-run
        # or let the nightly drain the rest.
        from plugins.mindpalace.tools import librarian
        if not librarian._enabled():
            return {'error': 'The librarian is disabled (alpha) — enable it in '
                             'Settings → Plugins → Mind Palace first.'}, 400
        requeued = 0
        with pt._get_connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, meta FROM chunks WHERE scope = ? "
                "AND json_extract(meta, '$.refers_to_time') IS NOT NULL "
                "AND (json_extract(meta, '$.temporal_at') IS NOT NULL "
                "     OR json_extract(meta, '$.event_date_src') = 'librarian')",
                (scope,)).fetchall()
            for cid, raw in rows:
                meta = _parse_meta(raw) or {}
                meta.pop('temporal_at', None)
                if meta.get('event_date_src') == 'librarian':
                    meta.pop('event_date_src', None)
                cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                            (json.dumps(meta, ensure_ascii=False), cid))
                requeued += 1
            if requeued:
                pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                           summary=f"reopened {requeued} temporal verdicts for the model")
            conn.commit()
        msg, ok = librarian.start(scope, kind='dates')
        return {'success': True, 'requeued': requeued,
                'message': msg if ok else f'pass not started: {msg}'}

    if action == 'wipe_dates':
        # The danger-zone date reset (Krem, 2026-07-24): wipe EVERY derived
        # date verdict in the scope — event_dates, src, librarian temporal
        # stamps, recurring — so the date system can rebuild from zero
        # (redate_regex → fresh floor, redate_model/nightly → refinement).
        # Built for the poisoned-dates cleanup: pre-fix passes wrote bad
        # dates that redate_model keeps as "current-best" and redate_regex
        # treats as terminal verdicts — only a full wipe clears them.
        # refers_to_time (the regex "mentions time" detector) survives; it
        # gates the rebuild queue. Memory content is never touched.
        if (b.get('confirm') or '') != scope:
            return {'error': 'confirm must equal the scope name exactly'}, 400
        wiped = 0
        with pt._get_connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, meta FROM chunks WHERE scope = ? "
                "AND json_extract(meta, '$.superseded_at') IS NULL "
                "AND (json_extract(meta, '$.event_dates') IS NOT NULL "
                "     OR json_extract(meta, '$.event_date_src') IS NOT NULL "
                "     OR json_extract(meta, '$.temporal_at') IS NOT NULL "
                "     OR json_extract(meta, '$.recurring_dates') IS NOT NULL)",
                (scope,)).fetchall()
            for cid, raw in rows:
                meta = _parse_meta(raw) or {}
                for key in ('event_dates', 'event_date_src', 'temporal_at',
                            'recurring_dates'):
                    meta.pop(key, None)
                cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                            (json.dumps(meta, ensure_ascii=False), cid))
                wiped += 1
            if wiped:
                pt._ledger(scope, 'user', 'maintenance', cursor=cur,
                           summary=f'wiped all derived dates on {wiped} '
                                   f'memories — date system reset for rebuild')
            conn.commit()
        _publish('events', scope, 'update')
        return {'success': True, 'wiped': wiped}

    return {'error': f'Unknown maintenance action: {action}'}, 400


def unprune_chunk(cid=None, **_):
    """Human reversal of a librarian prune — the soft-delete promise kept."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return {'error': 'Invalid chunk id'}, 400
    with pt._get_connection() as conn:
        cur = conn.cursor()
        row = cur.execute('SELECT layer, scope, meta, content FROM chunks WHERE id = ?',
                          (cid,)).fetchone()
        if not row:
            return {'error': 'Not found'}, 404
        meta = _parse_meta(row[2]) or {}
        if not meta.get('pruned_at'):
            return {'error': 'Not pruned'}, 400
        for k in ('pruned_at', 'pruned_reason'):
            meta.pop(k, None)
        cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                    (json.dumps(meta, ensure_ascii=False), cid))
        pt._ledger(row[1], 'user', 'restored', layer=row[0], target=cid, cursor=cur,
                   summary=(f"restored [{cid}] to her recall: "
                            f"\"{row[3][:60]}{'…' if len(row[3]) > 60 else ''}\""))
        conn.commit()
    _publish(row[0], row[1], 'update')
    return {'success': True, 'restored': cid,
            'note': ('was atomized — its parts also exist'
                     if meta.get('atomized_into') else None)}
