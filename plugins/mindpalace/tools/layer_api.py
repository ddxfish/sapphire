# plugins/mindpalace/tools/layer_api.py — the plugin-author write surface
# (Plugin Layers v1.1, 2026-07-12).
#
# Layer-provider plugins (manifest capabilities.memory_layers) mirror their
# content into the palace through THIS module and nothing else:
#
#     from plugins.mindpalace.tools import layer_api
#     layer_api.save("note text", layer='lore', scope='default')
#     layer_api.bulk_save(items, layer='lore')          # syncs — ONE ledger row
#     layer_api.delete_by_source('lore', 'vault/note.md')
#
# The fence: only REGISTERED plugin layers are accepted — never the palace's
# own layers (self/events/entities/knowledge/goals). Import lazily, inside
# functions, so load order never matters (the claude-persona pattern).
#
# Ledger etiquette: save() logs one row per note (actor 'system'); bulk_save
# and delete_by_source log ONE summary row for the whole batch — a 500-note
# vault sync must not flood her "while you were away" window.

import json
import logging

logger = logging.getLogger(__name__)

def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _check_layer(layer):
    """(spec, error) — layer must be a currently-registered plugin layer."""
    pt = _pt()
    spec = pt._plugin_layers().get(str(layer or '').strip().lower())
    if not spec:
        return None, (f"'{layer}' is not a registered plugin layer. Declare it in "
                      f"your manifest under capabilities.memory_layers (and note the "
                      f"palace's own layers are never writable through layer_api).")
    return spec, None


def save(content, layer, scope='default', label=None, fields=None, source=None):
    """Mirror one item into the palace. Returns (chunk_id|None, message).
    Rides the full save path: embedding, FTS, entity-mention weaving.
    fields → meta.fields (your custom metadata); source = your external id
    (note path etc.), the reconciliation key for delete_by_source."""
    spec, err = _check_layer(layer)
    if err:
        return None, err
    pt = _pt()
    msg, ok = pt._save_memory(content, scope, layer=str(layer).strip().lower(),
                              label=label, added_by=f"plugin:{spec['plugin_name']}",
                              meta_extra=fields, source=source)
    if not ok:
        return None, msg
    try:
        chunk_id = int(msg.split('ID: ')[1].split(',')[0])
    except Exception:
        chunk_id = None
    return chunk_id, msg


def bulk_save(items, layer, scope='default'):
    """Mirror a batch in ONE transaction with ONE ledger summary row.
    items: [{'content': str, 'label'?: str, 'fields'?: dict, 'source'?: str}].
    Embeddings are left NULL and swept by the backfill on next search —
    keeps big syncs fast. Returns (ids, message); over-length or empty
    items are skipped and counted."""
    spec, err = _check_layer(layer)
    if err:
        return [], err
    pt = _pt()
    layer = str(layer).strip().lower()
    ids, skipped = [], 0
    try:
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            try:
                pt._sync_layer_rows(cursor)   # durable row → dark-exclusion works later
            except Exception:
                pass
            # One alias map for the whole batch — mention edges still weave.
            try:
                from plugins.mindpalace.tools import metadata as md
                arows = md.entity_aliases(cursor, scope)
                amap = md.alias_map(arows)
                aliases = [a for _, _, a in arows]
            except Exception:
                md, amap, aliases = None, {}, []
            now = pt._now()
            for item in items or []:
                content = str(item.get('content') or '').strip()
                if not content or len(content) > pt.MAX_CHUNK_LENGTH:
                    skipped += 1
                    continue
                meta = {'added_by': f"plugin:{spec['plugin_name']}"}
                if item.get('fields'):
                    meta['fields'] = dict(item['fields'])
                cursor.execute(
                    'INSERT INTO chunks (layer, scope, content, label, favorite, '
                    'meta, created, updated, source) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)',
                    (layer, scope, content, item.get('label'),
                     json.dumps(meta, ensure_ascii=False), now, now,
                     item.get('source')))
                cid = cursor.lastrowid
                ids.append(cid)
                if md:
                    try:
                        hits = md.match_entities(content, aliases)
                        seen, mention_ids = set(), []
                        for h in hits:
                            pair = amap.get(h.lower())
                            if pair and pair[0] not in seen:
                                seen.add(pair[0])
                                mention_ids.append(pair[0])
                        if mention_ids:
                            md.seed_edges(cursor, cid, mention_ids, now)
                    except Exception:
                        pass
                if len(ids) + skipped >= 10_000:   # runaway-sync backstop
                    break
            pt._ledger(scope, 'system', 'imported', layer=layer, cursor=cursor,
                       summary=(f"'{spec['plugin_name']}' synced {len(ids)} items "
                                f"into {layer}"
                                + (f" ({skipped} skipped)" if skipped else '')))
            conn.commit()
        pt._backfill_done = False    # NULL vectors → swept on next search
        pt._publish_mind(layer, scope, 'save')
        return ids, f"Synced {len(ids)} items into '{layer}'" + \
                    (f", {skipped} skipped (empty or >{pt.MAX_CHUNK_LENGTH} chars)" if skipped else '')
    except Exception as e:
        logger.error(f"[MINDPALACE] layer_api.bulk_save failed: {e}")
        return ids, f"Bulk save failed: {e}"


def delete_by_source(layer, source, scope='default'):
    """Remove every mirrored chunk with this source (your external id) —
    the reconciliation primitive: on note change, delete_by_source then
    re-save. Cleans the chunks' edges. ONE ledger summary row.
    Returns (count, message)."""
    spec, err = _check_layer(layer)
    if err:
        return 0, err
    pt = _pt()
    layer = str(layer).strip().lower()
    try:
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            ids = [r[0] for r in cursor.execute(
                'SELECT id FROM chunks WHERE layer = ? AND scope = ? AND source = ?',
                (layer, scope, source)).fetchall()]
            if ids:
                ph = ','.join('?' * len(ids))
                cursor.execute(f"DELETE FROM edges WHERE src_type = 'chunk' "
                               f"AND src_id IN ({ph})", ids)
                cursor.execute(f"DELETE FROM edges WHERE dst_type = 'chunk' "
                               f"AND dst_id IN ({ph})", ids)
                cursor.execute(f'DELETE FROM chunks WHERE id IN ({ph})', ids)
                pt._ledger(scope, 'system', 'deleted', layer=layer, cursor=cursor,
                           summary=(f"'{spec['plugin_name']}' removed {len(ids)} "
                                    f"items from {layer} (source: {str(source)[:80]})"))
            conn.commit()
        if ids:
            pt._publish_mind(layer, scope, 'delete')
        return len(ids), f"Removed {len(ids)} chunk(s)"
    except Exception as e:
        logger.error(f"[MINDPALACE] layer_api.delete_by_source failed: {e}")
        return 0, f"Delete failed: {e}"


def get_sources(layer, scope='default'):
    """{source: {'count': n, 'fields': dict}} for your mirrored chunks,
    keyed by the source ids you saved with. The cheap reconciliation diff:
    stamp a content hash into fields at save time, compare here, and skip
    unchanged sources on the next sync. Sourceless chunks aren't listed."""
    spec, err = _check_layer(layer)
    if err:
        return {}
    pt = _pt()
    out = {}
    try:
        with pt._get_connection() as conn:
            rows = conn.execute(
                'SELECT source, COUNT(*), MIN(id) FROM chunks '
                'WHERE layer = ? AND scope = ? AND source IS NOT NULL '
                'GROUP BY source',
                (str(layer).strip().lower(), scope)).fetchall()
            for src, n, first_id in rows:
                meta = conn.execute('SELECT meta FROM chunks WHERE id = ?',
                                    (first_id,)).fetchone()[0]
                fields = {}
                try:
                    fields = (json.loads(meta) or {}).get('fields', {})
                except Exception:
                    pass
                out[src] = {'count': n, 'fields': fields}
    except Exception as e:
        logger.error(f"[MINDPALACE] layer_api.get_sources failed: {e}")
    return out


def sync_note(layer, summary, scope='default', detail=None):
    """Write one ledger line about a sync event you performed out-of-band.
    Use sparingly — one per sync run, not one per item."""
    spec, err = _check_layer(layer)
    if err:
        return False
    pt = _pt()
    pt._ledger(scope, 'system', 'imported', layer=layer,
               summary=f"'{spec['plugin_name']}': {str(summary)[:200]}",
               detail=detail)
    return True
