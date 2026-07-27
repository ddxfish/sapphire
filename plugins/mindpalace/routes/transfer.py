# plugins/mindpalace/routes/transfer.py
# Per-tab, per-scope import/export (Krem's ask, 2026-07-11: "If I export
# sapphire self, it's only self for her").
#
# Format: portable JSON, ONE layer × ONE scope per file.
#   - entity references travel by NAME (ids never survive machines)
#   - mention edges export as entity names, re-seeded against whatever
#     exists in the target scope at import (missing names skip cleanly)
#   - derived_from chunk→chunk provenance survives when both ends are in
#     the same file (intra-file index pairs)
#   - embeddings are EXCLUDED — the backfill sweep regenerates them
# Import is additive + idempotent: exact (content, created) matches in the
# target scope+layer skip, so re-importing a file is a no-op. A self-sheet
# section arriving where a current one lives becomes HISTORY (superseded),
# never clobbers the living sheet.

import json
import logging

logger = logging.getLogger(__name__)

FORMAT = 'mindpalace-export'
VERSION = 1


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _parse_meta(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def export_data(query=None, **_):
    """GET transfer/export?scope=&layer= → the export object (the UI saves
    it as a file client-side)."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    q = query or {}
    scope = q.get('scope') or 'default'
    layer, err = pt._validate_layer(q.get('layer'))
    if err or not layer:
        return {'error': err or 'layer is required'}, 400

    with pt._get_connection() as conn:
        cur = conn.cursor()
        rows = cur.execute(
            'SELECT c.id, c.content, c.tier, c.label, c.favorite, c.importance, '
            'c.private_key, c.meta, c.created, c.updated, c.source, c.chunk_index, '
            'e.name FROM chunks c LEFT JOIN entities e ON e.id = c.entity_id '
            'WHERE c.scope = ? AND c.layer = ? ORDER BY c.created, c.id',
            (scope, layer)).fetchall()
        ids = [r[0] for r in rows]
        index_of = {cid: i for i, cid in enumerate(ids)}
        mentions, derived = {}, {}
        if ids:
            ph = ','.join('?' * len(ids))
            for src, name in cur.execute(
                    f"SELECT d.src_id, e.name FROM edges d "
                    f"JOIN entities e ON e.id = d.dst_id "
                    f"WHERE d.src_type = 'chunk' AND d.dst_type = 'entity' "
                    f"AND d.kind = 'mentions' AND d.src_id IN ({ph})", ids).fetchall():
                mentions.setdefault(src, []).append(name)
            for src, dst in cur.execute(
                    f"SELECT src_id, dst_id FROM edges "
                    f"WHERE src_type = 'chunk' AND dst_type = 'chunk' "
                    f"AND kind = 'derived_from' AND src_id IN ({ph})", ids).fetchall():
                if dst in index_of:
                    derived.setdefault(src, []).append(index_of[dst])
        entities = []
        if layer == 'entities':
            entities = [
                {'name': r[0], 'kind': r[1], 'meta': _parse_meta(r[2]),
                 'created': r[3], 'updated': r[4]}
                for r in cur.execute(
                    'SELECT name, kind, meta, created, updated FROM entities '
                    'WHERE scope = ? ORDER BY name COLLATE NOCASE', (scope,)).fetchall()]

    chunks = []
    for r in rows:
        (cid, content, tier, label, favorite, importance,
         private_key, meta, created, updated, source, chunk_index, ename) = r
        c = {'content': content, 'created': created, 'updated': updated}
        if tier is not None:
            c['tier'] = tier
        if label:
            c['label'] = label
        if favorite:
            c['favorite'] = True
        if importance is not None:
            c['importance'] = importance
        if private_key:
            c['private_key'] = private_key
        if source:
            c['source'] = source
        if chunk_index is not None:
            c['chunk_index'] = chunk_index
        if ename:
            c['entity'] = ename
        m = _parse_meta(meta)
        if m:
            c['meta'] = m
        if cid in mentions:
            c['mentions'] = mentions[cid]
        if cid in derived:
            c['derived_from_idx'] = derived[cid]
        chunks.append(c)

    return {'format': FORMAT, 'version': VERSION, 'layer': layer,
            'scope': scope, 'exported': pt._now(),
            'counts': {'chunks': len(chunks), 'entities': len(entities)},
            'entities': entities, 'chunks': chunks}


def import_data(body=None, **_):
    """POST transfer/import {scope, expect_layer, data} → summary. Additive,
    idempotent; imports INTO `scope` regardless of the file's origin scope."""
    pt = _pt()
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}, 500
    b = body or {}
    # Import writes require an explicit target scope (scout class, 2026-07-19).
    scope = (b.get('scope') or '').strip()
    if not scope:
        return {'error': 'scope required'}, 400
    data = b.get('data')
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        return {'error': 'Not a Mind Palace export file'}, 400
    if data.get('version') != VERSION:
        return {'error': f"Unsupported export version {data.get('version')}"}, 400
    layer, err = pt._validate_layer(data.get('layer'))
    if err or not layer:
        return {'error': 'Export file has no valid layer'}, 400
    expect = b.get('expect_layer')
    if expect and layer != expect:
        return {'error': f"This is a '{layer}' export — import it from the "
                         f"{layer.capitalize()} tab"}, 400
    chunks = data.get('chunks') or []
    now = pt._now()
    keep_stamps = bool(b.get('keep_librarian_stamps'))

    imported = skipped = edges_seeded = ents_upserted = as_history = 0
    with pt._get_connection() as conn:
        cur = conn.cursor()
        from plugins.mindpalace.tools import metadata as md

        # Entities first (entities-layer files carry the full records).
        for e in data.get('entities') or []:
            name = (e.get('name') or '').strip()
            if not name:
                continue
            eid = pt.upsert_entity(cur, name, scope, kind=e.get('kind'))
            fields = ((e.get('meta') or {}).get('fields') or {})
            if fields:
                row = cur.execute('SELECT meta FROM entities WHERE id = ?',
                                  (eid,)).fetchone()
                meta = _parse_meta(row[0]) or {}
                merged = dict(meta.get('fields') or {})
                merged.update(fields)          # incoming wins per key
                meta['fields'] = merged
                cur.execute('UPDATE entities SET meta = ?, updated = ? WHERE id = ?',
                            (json.dumps(meta, ensure_ascii=False), now, eid))
            ents_upserted += 1

        def entity_id_of(name):
            row = cur.execute(
                "SELECT id FROM entities WHERE name = ? COLLATE NOCASE "
                "AND scope IN (?, 'global')", (name, scope)).fetchone()
            return row[0] if row else None

        # Legacy 'self' files may carry free notes (the layer retired
        # 2026-07-26) — those arrive as events, so the idempotency set must
        # look in both layers or a re-import would duplicate them.
        lay_set = ('self', 'events') if layer == 'self' else (layer,)
        lph = ','.join('?' * len(lay_set))
        existing = {(r[0], r[1]) for r in cur.execute(
            f'SELECT content, created FROM chunks WHERE scope = ? '
            f'AND layer IN ({lph})', (scope, *lay_set)).fetchall()}
        sections_taken = set()
        if layer == 'self':
            from plugins.mindpalace.tools import self_tools as st
            sections_taken = set(st._current_sections(cur, scope).keys())

        id_by_index = {}
        for i, c in enumerate(chunks):
            content = (c.get('content') or '').strip()
            created = c.get('created') or now
            if not content or (content, created) in existing:
                skipped += 1
                continue
            existing.add((content, created))
            meta = dict(c.get('meta') or {})
            # Librarian verdict stamps are per-install: carried in, they
            # arrive PRE-DRAINED and this install's passes silently skip
            # every imported row (no requeue exists for link/dedup/sort).
            # The verdicts' DATA (event_date, links as mention edges) still
            # travels; only the "already reviewed" markers reset. Escape
            # hatch: keep_librarian_stamps=true in the request body.
            if not keep_stamps:
                for k in ('librarian_at', 'temporal_at', 'link_at', 'dedup_at'):
                    meta.pop(k, None)
            meta['import_src'] = data.get('scope')
            sec = meta.get('section')
            row_layer = layer
            if layer == 'self':
                if sec and not meta.get('superseded_at'):
                    if sec in sections_taken:
                        meta['superseded_at'] = now   # arrives as history
                        as_history += 1
                    else:
                        sections_taken.add(sec)
                elif not sec:
                    # Legacy free note: the migration's contract applied at
                    # the door — lands in events wearing the stamp, instead
                    # of sitting invisible until the next boot retag.
                    meta['was_self_layer'] = True
                    row_layer = 'events'
            entity_id = None
            if c.get('entity'):
                entity_id = entity_id_of(c['entity']) or \
                    pt.upsert_entity(cur, c['entity'], scope)
            cur.execute(
                'INSERT INTO chunks (layer, scope, content, entity_id, tier, label, '
                'favorite, importance, private_key, meta, created, updated, '
                'source, chunk_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (row_layer, scope, content, entity_id, c.get('tier'), c.get('label'),
                 1 if c.get('favorite') else 0, c.get('importance'),
                 c.get('private_key'), json.dumps(meta, ensure_ascii=False),
                 created, c.get('updated') or created,
                 c.get('source'), c.get('chunk_index')))
            new_id = cur.lastrowid
            id_by_index[i] = new_id
            imported += 1
            names = [n for n in (c.get('mentions') or []) if isinstance(n, str)]
            eids = [eid for eid in (entity_id_of(n) for n in names) if eid]
            if eids:
                edges_seeded += md.seed_edges(cur, new_id, eids, now)

        # Second pass: intra-file derived_from provenance.
        for i, c in enumerate(chunks):
            if i not in id_by_index:
                continue
            for j in c.get('derived_from_idx') or []:
                if isinstance(j, int) and j in id_by_index:
                    cur.execute(
                        "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                        "VALUES ('chunk', ?, 'chunk', ?, 'derived_from', 1.0, ?)",
                        (id_by_index[i], id_by_index[j], now))
        if imported or ents_upserted:
            pt._ledger(scope, 'import', 'imported', layer=layer, cursor=cur,
                       summary=(f"imported {imported} {layer} chunks from a file"
                                + (f", {ents_upserted} entities" if ents_upserted else '')
                                + (f" ({skipped} skipped)" if skipped else '')),
                       detail={'src_scope': data.get('scope'),
                               'as_history': as_history})
        conn.commit()

    if imported:
        pt.reset_backfill_latch()
        # Kick the embedding sweep NOW instead of waiting for the next
        # search — exports exclude embeddings, and a dedup pass fired right
        # after an import would otherwise see an empty batch.
        import threading
        threading.Thread(target=pt._backfill_embeddings,
                         name='mindpalace-import-backfill',
                         daemon=True).start()
    try:
        pt._publish_mind(layer, scope, 'save')
    except Exception:
        pass
    logger.info(f"[MINDPALACE] Import into '{scope}'/{layer}: {imported} new, "
                f"{skipped} skipped, {ents_upserted} entities, {edges_seeded} edges")
    return {'success': True, 'imported': imported, 'skipped': skipped,
            'entities_upserted': ents_upserted, 'edges_seeded': edges_seeded,
            'arrived_as_history': as_history}
