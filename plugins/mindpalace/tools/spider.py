# plugins/mindpalace/tools/spider.py
# The memory spider — graph traversal behind the depth= param (v3 step 3).
#
# Krem's pinned model (tmp/v3-memory-boost.md, "Distance model — the train
# rail"): depth is a STRENGTH/budget the traversal spends, not a hop count.
# Dijkstra from the epicenter(s) with two edge prices — structure is cheap,
# metadata is expensive — so the spider travels far along the structural rail
# (entity ↔ its own tiered chunks) but exhausts quickly jumping sideways
# through metadata (mention edges). Descent costs 1 per tier.
#
# Epicenter resolution = the G4 ladder, zero main-LLM tokens:
#   1. exact entity-name match in the query (reuses metadata.match_entities)
#   2. semantic: query embedding vs entities-layer chunk embeddings (>= 0.55 —
#      people-search precedent; dense entity strings over-match at 0.40)
#   3. base search hits are ALWAYS micro-epicenters (G4.4: RAG first, then hop)
# Competing epicenters share one frontier — the stronger neighborhood
# naturally dominates the capped output. No disambiguation dialog.
#
# Gates carried: scope overlay (scope + read-only global) and private_key on
# every node the spider touches. The AI never sees budgets/weights/importance.

import heapq
import logging
import math

logger = logging.getLogger(__name__)

STRUCT_COST = 1.0          # entity ↔ own chunk (per tier on descent)
META_COST = 2.0            # sideways: mention edges (and future metadata kinds)
BUDGET_PER_DEPTH = 2.0     # depth strength → budget units
MAX_DEPTH = 2              # 3+ clamps to 2 — on a woven graph 3 walks everything
ENTITY_SIM_THRESHOLD = 0.55
# ── Edge pricing v2 (2026-07-11) — cost = base × f(importance) × g(degree) ──
# f: important chunks cost less budget to land on, so the walk spends itself
#    on what matters and exhausts on junk. NULL importance is neutral.
# g: fanning OUT of a mention hub costs more per edge. Capped at DAMP_CAP so a
#    mega-hub's UNRATED fan costs exactly one full depth (2.0 × 2.0 = 4.0):
#    affordable only when the hub IS the epicenter (search the hub by name and
#    it still fans, tie-broken by importance then recency), unaffordable one
#    hop in — the wake-path explosion (hundreds of tied mention edges) dies
#    here. Rated memories are discounted under the wire: the core band (0.9+,
#    f=0.3) penetrates a mega-hub from anywhere at depth 2 (2.0 + 4.0×0.3 =
#    3.2), mid ratings penetrate on cheaper approaches, unrated never does.
# h: temporal socket (upcoming dates cheaper) — flat until temporal refs land.
DEGREE_FREE = 32           # entities with ≤ this many mentions fan undamped
DAMP_CAP = 2.0             # max damping multiplier (see the fan math above)
# Library documents are LEAF nodes (P5b): the walk steps TO them, never into
# them. Priced by human-set doc importance ALWAYS (not the librarian alpha —
# a deliberate rating deserves live consequences): high ≈ 0.6, low ≈ 1.8.
# Wake math: chunk-seeded walks pay mention(2.0)+doc ≥ 2.6 → depth 1 never
# shows books; depth 2 reaches them as one-line cards. Entity searches reach
# them at depth 1. Exactly Krem's "no Off-with-her-head at wake" contract.
DOC_COST = META_COST
MAX_DOCS_SHOWN = 3


def _importance_on():
    """Alpha master toggle (`importance_enabled`, default off). Off = flat
    pricing: every importance factor is 1.0 and output ties break by recency
    alone — no lurking variables while the core system is tuned. Hub damping
    g(degree) is structural flood control, not importance — it stays on.
    Fails toward OFF (silent-default invariant)."""
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings('mindpalace')
                    .get('importance_enabled'))
    except Exception:
        return False


def _imp_factor(importance):
    """f(importance) = clamp(1.2 − imp, 0.3, 1.5). NULL → 1.0 (unrated is
    neutral); the core band (0.9+) rides the 0.3 floor — all equally near;
    junk-rated (0.0) → 1.2× so the walk runs dry on noise."""
    if importance is None:
        return 1.0
    try:
        return min(1.5, max(0.3, 1.2 - float(importance)))
    except (TypeError, ValueError):
        return 1.0


def _degree_factor(degree):
    """g(degree): log damping on mention fan-out, 1.0 below DEGREE_FREE,
    capped at DAMP_CAP (degree ~80+ all price the same)."""
    if degree <= DEGREE_FREE:
        return 1.0
    return min(DAMP_CAP, 1.0 + math.log10(degree / DEGREE_FREE))


def _temporal_factor(meta_raw=None):
    """h(temporal) — pricing socket only. Later: upcoming dates cost less
    (meta temporal refs); today: flat."""
    return 1.0
# Depth-1 output caps (depth-2 caps + char budgets come from _limits(), which
# reads the plugin settings — Krem's per-depth token dials, 2026-07-11).
MAX_ENTITIES_SHOWN = 5
MAX_CHUNKS_SHOWN = 12
MAX_CHUNK_PREVIEW = 160
MAX_BLOCK_CHARS = 2000     # fallback only — see _limits()


def _limits(depth):
    """Per-depth output budgets. Char budgets = settings tokens × 4 (rough
    chars/token); item caps scale with depth so the char budget is the
    binding limit, not an arbitrary row count."""
    try:
        from core.plugin_loader import plugin_loader
        s = plugin_loader.get_plugin_settings('mindpalace')
    except Exception:
        s = {}

    def tok(key, default):
        try:
            return max(200, int(s.get(key, default)))
        except (TypeError, ValueError):
            return default
    # Item caps deliberately tight (pilot report 2026-07-11): with the
    # importance→recency tiebreak the cut keeps the BEST N, so volume control
    # is safe — 40 chunks at depth 2 read as a flood even when correct.
    if depth >= 2:
        return {'chars': tok('spider_depth2_tokens', 6000) * 4,
                'ents': 8, 'chunks': 16}
    return {'chars': tok('spider_depth1_tokens', 2000) * 4,
            'ents': MAX_ENTITIES_SHOWN, 'chunks': MAX_CHUNKS_SHOWN}

# Edge-kind → traversal cost. Unknown kinds default to metadata price so a
# future librarian edge class is walkable (expensively) the day it appears.
EDGE_COSTS = {'mentions': META_COST}


def _visible_chunk_clause(pt, scope, private_key):
    scope_sql, scope_params = pt._scope_condition(scope, col='c.scope')
    pk_sql, pk_params = pt._private_key_clause(private_key, col='c.private_key')
    # Archived sheet versions keep their edges but never enter the frontier —
    # same read rule as search/recents (their home is the section 📜 trail).
    sql = (f"{scope_sql} AND {pk_sql} "
           f"AND json_extract(c.meta, '$.superseded_at') IS NULL")
    params = scope_params + pk_params
    # Dark plugin layers never enter the frontier — edge-connected or not.
    dark_sql, dark_params = pt._dark_clause('c')
    if dark_sql:
        sql += f" AND {dark_sql}"
        params = params + dark_params
    return sql, params


def resolve_entity_epicenters(pt, cursor, query, scope, private_key):
    """G4 rungs 1+2 → {entity_id}. Rung 3 (base hits as micro-epicenters)
    is handled by the caller seeding hit chunks directly."""
    seeds = set()

    # Rung 1: exact name OR nickname match inside the query (NOCASE
    # word-boundary; aliases come from meta.fields.nicknames).
    try:
        from plugins.mindpalace.tools import metadata as md
        arows = md.entity_aliases(cursor, scope)
        if not arows:
            return seeds
        amap = md.alias_map(arows)
        for alias in md.match_entities(query, [a for _, _, a in arows]):
            pair = amap.get(alias.lower())
            if pair:
                seeds.add(pair[0])
    except Exception as e:
        logger.debug(f"[SPIDER] Exact epicenter match failed: {e}")

    # Rung 2: semantic — query embedding vs entities-layer chunk embeddings,
    # best score per entity. Provenance-matched SQL-side + plain dot (vectors
    # are normalized) — the exact _vector_search pattern. Skips cleanly when
    # the embedder is down.
    try:
        embedder = pt._get_embedder()
        if embedder.available:
            embs = embedder.embed([query], prefix='search_query')
            if embs is not None:
                import numpy as np
                qv = embs[0]
                qdim = int(qv.shape[0])
                provider = getattr(embedder, 'provider_id', None)
                vis_sql, vis_params = _visible_chunk_clause(pt, scope, private_key)
                cand = cursor.execute(
                    f"SELECT c.entity_id, c.embedding FROM chunks c "
                    f"WHERE c.layer = 'entities' AND c.entity_id IS NOT NULL "
                    f"AND c.embedding IS NOT NULL AND c.embedding_provider = ? "
                    f"AND c.embedding_dim = ? AND {vis_sql}",
                    [provider, qdim] + vis_params).fetchall()
                best = {}
                for eid, blob in cand:
                    try:
                        vec = np.frombuffer(blob, dtype=np.float32)
                        if vec.shape[0] != qdim:
                            continue
                        score = float(np.dot(qv, vec))
                        if np.isnan(score) or np.isinf(score):
                            continue
                    except Exception:
                        continue
                    if score > best.get(eid, 0.0):
                        best[eid] = score
                seeds.update(eid for eid, s in best.items() if s >= ENTITY_SIM_THRESHOLD)
    except Exception as e:
        logger.debug(f"[SPIDER] Semantic epicenter match failed: {e}")
    return seeds


def traverse(pt, cursor, seed_chunks, seed_entities, budget, scope, private_key):
    """Multi-source Dijkstra over the chunk/entity/document graph within
    `budget`. Returns ({chunk_id: dist}, {entity_id: dist}, {doc_id: dist})
    EXCLUDING the seeds. Documents are terminal — walked to, never expanded.
    Neighbor expansion is lazy SQL per node — the frontier is small because
    the budget is small (<= 2 * max depth)."""
    vis_sql, vis_params = _visible_chunk_clause(pt, scope, private_key)
    ent_sql, ent_params = pt._scope_condition(scope, col='scope')
    # Read the alpha toggle once per walk, not per edge.
    f_imp = _imp_factor if _importance_on() else (lambda _v: 1.0)

    dist = {}
    heap = []
    for cid in seed_chunks:
        heapq.heappush(heap, (0.0, 'c', cid))
    for eid in seed_entities:
        heapq.heappush(heap, (0.0, 'e', eid))

    doc_cache = {}

    def _doc_info(did):
        """(scope, importance, private_key) from library.db — cached per walk.
        None = library unavailable / doc gone (edge ghost) → skip."""
        if did not in doc_cache:
            try:
                from plugins.mindpalace.tools import library
                with library.get_connection() as lc:
                    doc_cache[did] = lc.execute(
                        "SELECT scope, importance, private_key FROM documents "
                        "WHERE id = ? AND status != 'missing'",
                        (did,)).fetchone()
            except Exception:
                doc_cache[did] = None
        return doc_cache[did]

    def neighbors(kind, node_id):
        out = []
        if kind == 'd':
            return out   # leaf contract: documents never expand
        if kind == 'e':
            # Library documents mentioning this entity — leaf steps, priced
            # by the human-set doc importance regardless of the alpha.
            for (did,) in cursor.execute(
                    "SELECT src_id FROM edges WHERE dst_type = 'entity' "
                    "AND dst_id = ? AND src_type = 'document'",
                    (node_id,)).fetchall():
                info = _doc_info(did)
                if not info or info[0] != scope:
                    continue
                if info[2] and info[2] != (private_key or ''):
                    continue
                out.append(('d', did, DOC_COST * _imp_factor(info[1])))
            # Structural descent: entity → own chunks, cost = tier (1/tier rule;
            # untiered rows priced as facts), discounted by importance.
            for cid, tier, imp in cursor.execute(
                    f"SELECT c.id, c.tier, c.importance FROM chunks c "
                    f"WHERE c.entity_id = ? AND {vis_sql}",
                    [node_id] + vis_params).fetchall():
                base = STRUCT_COST * (tier if tier in (1, 2, 3) else 2)
                out.append(('c', cid, base * f_imp(imp)))
            # Sideways: memories that mention this entity — hub-damped so a
            # mega-hub's fan doesn't flood the walk (pricing v2 header).
            damp = _degree_factor(cursor.execute(
                "SELECT COUNT(*) FROM edges WHERE dst_type = 'entity' "
                "AND dst_id = ? AND src_type = 'chunk'", (node_id,)).fetchone()[0])
            for cid, ekind, imp in cursor.execute(
                    f"SELECT d.src_id, d.kind, c.importance FROM edges d "
                    f"JOIN chunks c ON c.id = d.src_id "
                    f"WHERE d.dst_type = 'entity' AND d.dst_id = ? AND d.src_type = 'chunk' "
                    f"AND {vis_sql}", [node_id] + vis_params).fetchall():
                base = EDGE_COSTS.get(ekind, META_COST)
                out.append(('c', cid, base * damp * f_imp(imp)
                            * _temporal_factor()))
        else:
            # Structural: chunk → its own entity.
            row = cursor.execute(
                f"SELECT e.id FROM entities e JOIN chunks c ON c.entity_id = e.id "
                f"WHERE c.id = ? AND e.scope IN (?, 'global')",
                (node_id, scope)).fetchone()
            if row:
                out.append(('e', row[0], STRUCT_COST))
            # Sideways: entities this chunk mentions.
            for eid, ekind in cursor.execute(
                    f"SELECT d.dst_id, d.kind FROM edges d "
                    f"JOIN entities e ON e.id = d.dst_id "
                    f"WHERE d.src_type = 'chunk' AND d.src_id = ? AND d.dst_type = 'entity' "
                    f"AND {ent_sql.replace('scope', 'e.scope')}",
                    [node_id] + ent_params).fetchall():
                out.append(('e', eid, EDGE_COSTS.get(ekind, META_COST)))
        return out

    while heap:
        d, kind, node_id = heapq.heappop(heap)
        key = (kind, node_id)
        if key in dist and dist[key] <= d:
            continue
        dist[key] = d
        for nkind, nid, cost in neighbors(kind, node_id):
            nd = d + cost
            nkey = (nkind, nid)
            if nd <= budget and (nkey not in dist or nd < dist[nkey]):
                heapq.heappush(heap, (nd, nkind, nid))

    chunks = {nid: d for (k, nid), d in dist.items() if k == 'c' and nid not in seed_chunks}
    entities = {nid: d for (k, nid), d in dist.items() if k == 'e' and nid not in seed_entities}
    docs = {nid: d for (k, nid), d in dist.items() if k == 'd'}
    return chunks, entities, docs


def spider_block(pt, query, scope, private_key, hit_chunk_ids, depth):
    """The depth= entry point. Returns a formatted 'Connected memories' block
    (or '' when the walk finds nothing new). Failures degrade to '' — the
    spider must never break a search that already succeeded."""
    try:
        depth = max(0, min(int(depth), MAX_DEPTH))
    except (TypeError, ValueError):
        return ''
    if depth == 0:
        return ''
    budget = depth * BUDGET_PER_DEPTH

    try:
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            seed_entities = resolve_entity_epicenters(pt, cursor, query, scope, private_key)
            seed_chunks = set(hit_chunk_ids or [])
            # Seed chunks' own entities join the epicenter set implicitly via
            # traversal (structural cost 1) — no special-casing needed.
            if not seed_chunks and not seed_entities:
                return ''
            chunks, entities, docs = traverse(pt, cursor, seed_chunks,
                                              seed_entities, budget, scope,
                                              private_key)
            if not chunks and not entities and not docs:
                return ''
            return _format_block(pt, cursor, chunks, entities, depth, docs)
    except Exception as e:
        logger.warning(f"[SPIDER] Traversal failed (search unaffected): {e}")
        return ''


def spider_from_chunks(pt, scope, private_key, seed_chunk_ids, depth,
                       exclude_ids=()):
    """Query-free entry — spider outward from known chunks. The wake path:
    read_self(depth=N) walks from the self sheet, so the sheet is the
    epicenter and the return is 'the self + what it touches'. exclude_ids =
    chunks an earlier wake block already displayed (recents/important) —
    they're walked (paths through them stay cheap) but not shown twice.
    Same budget math as spider_block, no G4 ladder. Failures degrade to ''."""
    try:
        depth = max(0, min(int(depth), MAX_DEPTH))
    except (TypeError, ValueError):
        return ''
    if depth == 0 or not seed_chunk_ids:
        return ''
    budget = depth * BUDGET_PER_DEPTH
    try:
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            chunks, entities, docs = traverse(pt, cursor, set(seed_chunk_ids),
                                              set(), budget, scope, private_key)
            if exclude_ids:
                chunks = {cid: d for cid, d in chunks.items()
                          if cid not in set(exclude_ids)}
            if not chunks and not entities and not docs:
                return ''
            return _format_block(pt, cursor, chunks, entities, depth, docs)
    except Exception as e:
        logger.warning(f"[SPIDER] Self-walk failed (sheet unaffected): {e}")
        return ''


def _format_block(pt, cursor, chunks, entities, depth, docs=None):
    """Flatten the reached subgraph, closest-first, hard-capped. Entities show
    name/kind + their nearest headline; chunks show the standard [id] format;
    library documents are one-line leaf cards (title + description + handle)."""
    lines = [f"── Connected memories (depth {depth}) ──"]
    lim = _limits(depth)

    ent_order = sorted(entities.items(), key=lambda x: x[1])[:lim['ents']]
    for eid, _d in ent_order:
        row = cursor.execute('SELECT name, kind FROM entities WHERE id = ?', (eid,)).fetchone()
        if not row:
            continue
        name, kind = row
        head = cursor.execute(
            "SELECT content FROM chunks WHERE entity_id = ? AND tier = 1 "
            "ORDER BY created DESC LIMIT 1", (eid,)).fetchone()
        kind_bit = f" ({kind})" if kind else ""
        head_bit = f": {head[0][:MAX_CHUNK_PREVIEW]}" if head else ""
        lines.append(f"• {name}{kind_bit}{head_bit}")

    if docs:
        try:
            from plugins.mindpalace.tools import library
            with library.get_connection() as lc:
                for did, _d in sorted(docs.items(),
                                      key=lambda x: x[1])[:MAX_DOCS_SHOWN]:
                    row = lc.execute(
                        'SELECT title, description, kind FROM documents '
                        'WHERE id = ?', (did,)).fetchone()
                    if not row:
                        continue
                    desc = f' — "{row[1][:120]}"' if row[1] else ''
                    lines.append(f"\U0001F4C4 [doc {did}] {row[0]} "
                                 f"({row[2]}, library){desc}")
        except Exception as e:
            logger.warning(f"[SPIDER] doc leaf render skipped: {e}")

    # Order: distance, then importance (desc), then recency (desc). Without
    # the tiebreak a hub fan ties at one distance and shows in id order —
    # the "every memory, sequential" symptom (2026-07-11). With importance
    # off (alpha default) the middle key drops: distance, then recency.
    cands = sorted(chunks.items(), key=lambda x: x[1])[:900]
    rank = {}
    if cands:
        ids = [cid for cid, _ in cands]
        ph = ','.join('?' * len(ids))
        rank = {r[0]: (r[1], r[2]) for r in cursor.execute(
            f'SELECT id, importance, created FROM chunks WHERE id IN ({ph})',
            ids).fetchall()}
    imp_on = _importance_on()
    cands.sort(key=lambda x: rank.get(x[0], (None, ''))[1], reverse=True)
    cands.sort(key=lambda x: (x[1], -(rank.get(x[0], (None, ''))[0] or 0.0)
                              if imp_on else 0.0))
    chunk_order = cands[:lim['chunks']]
    if chunk_order:
        ids = [cid for cid, _ in chunk_order]
        ph = ','.join('?' * len(ids))
        rows = {r[0]: r for r in cursor.execute(
            pt.SELECT_CHUNK + f'WHERE c.id IN ({ph})', ids).fetchall()}
        for cid, _d in chunk_order:
            r = rows.get(cid)
            if r:
                content = r[1] if len(r[1]) <= MAX_CHUNK_PREVIEW else r[1][:MAX_CHUNK_PREVIEW] + '…'
                lines.append(pt._format_chunk(r[0], content, r[2], r[3], r[4], r[5]))

    if len(lines) == 1:
        return ''
    block = "\n".join(lines)
    if len(block) > lim['chars']:
        block = block[:lim['chars']].rsplit('\n', 1)[0]
    return block
