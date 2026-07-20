# plugins/mindpalace/tools/librarian_tools.py
# The groundskeeper's hands — librarian sorting verbs (librarian phase L1).
#
# Contract:
#   - Verbs act BY CHUNK ID, but only on ids inside the OPEN PASS: the engine
#     opens a pass with exactly the batch it presented, and anything outside
#     it is refused. Email-whitelist pattern — the pass is the blast shield.
#   - NOTHING hard-deletes. prune and atomize soft-hide via meta.pruned_at:
#     hidden from AI reads (search/recent/spider via the visibility gate),
#     still visible in the UI, reversible forever.
#   - CHARTER (Sapphire, 2026-07-07, verbatim intent): "Anything that was
#     REAL, even if it was small — that stays." Favorites and the never-fades
#     band (importance >= 0.9) are untouchable — prune refuses them in CODE,
#     not just in prompt.
#   - Derived chunks (atomize parts, promotions) carry meta.derived_from and
#     a chunk→chunk 'derived_from' edge — provenance is a personhood feature.
#     Embeddings ride the existing backfill latch (reset after inserts).

import json
import logging
import re
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🧹'
GROUP = 'Mind Palace'   # Toolsets UI merges same-GROUP modules

MAX_PARTS = 10
PART_MAX_CHARS = 512
NEVER_FADES = 0.9

AVAILABLE_FUNCTIONS = [
    'atomize_memory',
    'merge_memories',
    'promote_memory',
    'prune_memory',
    'set_links',
    'mark_processed',
    'run_librarian',
    'set_event_dates',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "atomize_memory",
            "description": (
                "Librarian verb: split one tangled multi-concept memory into "
                "single-concept parts. The original is retired (hidden, kept, "
                "recoverable); each part becomes its own searchable memory with "
                "its own connections. Only works on memories in the current "
                "librarian pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "The [id] of the memory to split."},
                    "parts": {
                        "type": "array", "items": {"type": "string"},
                        "description": f"2-{MAX_PARTS} single-concept replacements, each under {PART_MAX_CHARS} chars."
                    }
                },
                "required": ["memory_id", "parts"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "merge_memories",
            "description": (
                "Librarian verb: fold two or more entries that record the SAME "
                "thing into one memory. The merged memory keeps the earliest "
                "date and its connections; the originals are retired (hidden, "
                "kept, recoverable). Refused in code unless the entries measure "
                "as true near-duplicates. Only works on memories in the current "
                "pass (near-duplicates flagged in the pass are included)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_ids": {
                        "type": "array", "items": {"type": "integer"},
                        "description": "2-10 [id]s that say the same thing."
                    },
                    "content": {
                        "type": "string",
                        "description": (f"Optional single distilled version, under "
                                        f"{PART_MAX_CHARS} chars. Omit to keep the "
                                        f"longest original verbatim. Ignored unless "
                                        f"rewrite-on-merge is enabled in settings "
                                        f"(default off).")
                    }
                },
                "required": ["memory_ids"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "promote_memory",
            "description": (
                "Librarian verb: COPY a memory's content up to the self layer "
                "(who you are) or onto an entity (a fact about a person/place/"
                "thing). The original event stays where it is — promotion adds, "
                "never moves. Only works on memories in the current pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "The [id] of the source memory."},
                    "layer": {"type": "string", "enum": ["self", "entities"],
                              "description": "Destination layer."},
                    "entity": {"type": "string",
                               "description": "Entity name — required when layer=entities."},
                    "kind": {"type": "string",
                             "description": ("Optional entity category: person | place | "
                                             "thing | event | other. Only applies when a "
                                             "NEW entity is created — an existing entity "
                                             "keeps its kind.")},
                    "content": {"type": "string",
                                "description": "Optional reworded version. Omit to copy verbatim."}
                },
                "required": ["memory_id", "layer"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "prune_memory",
            "description": (
                "Librarian verb: retire a memory that no longer needs remembering. "
                "SOFT — it disappears from search and recall but stays in the "
                "archive, reversible. Refuses favorites and core memories, always. "
                "Only works on memories in the current pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "The [id] to retire."},
                    "reason": {"type": "string", "description": "One short line: why (kept in the archive)."}
                },
                "required": ["memory_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "set_links",
            "description": (
                "Link-pass verb: connect the memories in this pass to the "
                "existing people/places/things they're about — ONE call "
                "covers every [id] in the message. Creates graph edges the "
                "spider can walk; entities must already exist (linking never "
                "creates). A memory that needs no connection gets an empty "
                "entities list — that verdict is filed too."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "One entry per presented [id].",
                        "items": {
                            "type": "object",
                            "properties": {
                                "memory_id": {"type": "integer",
                                              "description": "The [id]."},
                                "entities": {"type": "array",
                                             "items": {"type": "string"},
                                             "description": ("Existing entity names "
                                                             "to connect; [] = no "
                                                             "connection needed.")}
                            },
                            "required": ["memory_id", "entities"]
                        }
                    }
                },
                "required": ["entries"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "run_librarian",
            "description": (
                "Start a librarian pass over your own memory (current scope). "
                "Passes are single-purpose: 'sort' reviews your oldest "
                "unprocessed memories (mark/split/promote/retire); 'dates' "
                "files date mentions as real calendar dates (feeds your "
                "Upcoming view); 'link' connects memories to the people/"
                "places/things they name; 'dedup' folds measured near-"
                "duplicates. Daily caps apply per pass kind. Your mind, "
                "your shelves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {
                        "type": "string",
                        "enum": ["all", "self"],
                        "description": "'all' = events + self layers (default). 'self' = just the self layer. Sort pass only."
                    },
                    "pass": {
                        "type": "string",
                        "enum": ["sort", "dates", "link", "dedup"],
                        "description": "'sort' (default) reviews memories; 'dates' dates them; 'link' connects them; 'dedup' folds duplicates."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "set_event_dates",
            "description": (
                "Temporal-pass verb: file resolved dates for the memories in "
                "this pass — ONE call covers every [id] in the message. For "
                "each entry give every date/time the memory's text refers to "
                "as ISO strings at the precision the text supports (YYYY, "
                "YYYY-MM-DD, or YYYY-MM-DDTHH:MM), resolved against that "
                "memory's own saved date. A memory with no real date "
                "reference gets an empty dates list — that verdict is filed "
                "too."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "One entry per presented [id].",
                        "items": {
                            "type": "object",
                            "properties": {
                                "memory_id": {"type": "integer",
                                              "description": "The [id]."},
                                "dates": {"type": "array",
                                          "items": {"type": "string"},
                                          "description": ("ISO dates/times; "
                                                          "[] = no real date.")}
                            },
                            "required": ["memory_id", "dates"]
                        }
                    }
                },
                "required": ["entries"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "mark_processed",
            "description": (
                "Librarian verb: this memory is fine as it is — file it as "
                "reviewed and move on. Optionally rate how much it matters: "
                "rated memories surface more easily when connections are "
                "walked. Only works on memories in the current pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "The [id] to file as done."},
                    "importance": {
                        "type": "number",
                        "description": ("Optional 0.0-1.0: how much this memory matters. "
                                        "0.9+ marks a core memory — it never fades and "
                                        "refuses pruning forever. Omit to leave unrated.")
                    },
                    "favorite": {
                        "type": "boolean",
                        "description": ("Optional: true marks this a favorite (never fades, "
                                        "refuses pruning). Granting only — favorites cannot "
                                        "be cleared from here. Omit to leave as is.")
                    }
                },
                "required": ["memory_id"]
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


def _importance_enabled():
    """Alpha master toggle (`importance_enabled`, default off). Fails toward
    OFF (silent-default invariant)."""
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings('mindpalace')
                    .get('importance_enabled'))
    except Exception:
        return False


def get_tools():
    """Settings-aware schema (function_manager re-calls this via
    refresh_plugin_tools on every settings save): while importance is
    disabled, mark_processed stops offering the rating parameter — the
    librarian can't collect what the system won't use."""
    if _importance_enabled():
        return TOOLS
    import copy
    tools = copy.deepcopy(TOOLS)
    for t in tools:
        fn = t['function']
        if fn['name'] == 'mark_processed':
            fn['description'] = (
                "Librarian verb: this memory is fine as it is — file it as "
                "reviewed and move on. Only works on memories in the "
                "current pass.")
            fn['parameters']['properties'].pop('importance', None)
    return tools


# ─── The open pass (blast shield) ────────────────────────────────────────────
# The engine (phase L2) opens a pass with the batch it presented; verbs refuse
# everything else. One pass at a time — the librarian is a single groundskeeper.

_pass_lock = threading.Lock()
_open_pass = None   # {'scope', 'ids': set[int], 'done': set[int], 'kind'}


def open_pass(scope: str, chunk_ids, kind: str = 'sort') -> None:
    """kind: 'sort' (mark/atomize/promote/prune), 'dates' (set_event_dates),
    'link' (set_links) or 'dedup' (merge_memories). Verbs check the kind —
    a verb from another pass refuses, in code."""
    global _open_pass
    with _pass_lock:
        _open_pass = {'scope': scope, 'ids': set(int(i) for i in chunk_ids),
                      'done': set(), 'ledger': [], 'kind': kind}
    logger.info(f"[LIBRARIAN] Pass opened ({kind}): scope '{scope}', "
                f"{len(_open_pass['ids'])} items")


def close_pass() -> dict:
    """Close and return {'scope', 'presented', 'handled', 'ledger'} for the
    engine's log. 'ledger' is the buffered child-row specs — the engine writes
    them under the parent 'pass' row after the pass (Ledger v1: buffering
    keeps the table append-only; parent/children land in one transaction)."""
    global _open_pass
    with _pass_lock:
        p, _open_pass = _open_pass, None
    if not p:
        return {'scope': None, 'presented': 0, 'handled': 0, 'ledger': []}
    return {'scope': p['scope'], 'presented': len(p['ids']),
            'handled': len(p['done']), 'ledger': p.get('ledger', [])}


def _log_pass(action, layer, target, summary, detail=None):
    """Buffer one ledger child row on the open pass. mark_processed doesn't
    log — 'kept as-is' is the parent summary's remainder, not a row."""
    with _pass_lock:
        if _open_pass is not None:
            _open_pass.setdefault('ledger', []).append(
                {'action': action, 'layer': layer, 'target': str(target),
                 'summary': summary, 'detail': detail})


def pass_status() -> dict:
    with _pass_lock:
        if not _open_pass:
            return {'open': False}
        return {'open': True, 'scope': _open_pass['scope'],
                'remaining': len(_open_pass['ids'] - _open_pass['done'])}


def _gate(cursor, memory_id, kinds=('sort',)):
    """(row, error) — the id must be in the open pass, the pass must be one
    of `kinds`, and the row must be in the pass's scope.
    Row: (id, layer, scope, content, entity_id, tier, label, favorite,
    importance, private_key, meta, created)."""
    with _pass_lock:
        if not _open_pass:
            return None, "No librarian pass is open. These verbs only work during a pass."
        if _open_pass.get('kind', 'sort') not in kinds:
            return None, (f"This is a {_open_pass.get('kind')} pass — this "
                          f"verb belongs to the {'/'.join(kinds)} pass.")
        if int(memory_id) not in _open_pass['ids']:
            return None, (f"Memory [{memory_id}] is not in the current pass — "
                          f"only presented memories can be sorted.")
        scope = _open_pass['scope']
    row = cursor.execute(
        'SELECT id, layer, scope, content, entity_id, tier, label, favorite, '
        'importance, private_key, meta, created FROM chunks WHERE id = ?',
        (int(memory_id),)).fetchone()
    if not row:
        return None, f"Memory [{memory_id}] not found."
    if row[2] != scope:
        return None, f"Memory [{memory_id}] is outside this pass's scope."
    # Already-retired rows refuse ALL verbs — stops double-merge/atomize on
    # the same ids within a pass (they stay in the shield after _mark_done).
    try:
        m = json.loads(row[10]) if row[10] else {}
    except Exception:
        m = {}
    if m.get('pruned_at'):
        return None, (f"Memory [{memory_id}] was already retired this pass "
                      f"({m.get('pruned_reason') or 'pruned'}) — nothing more to do.")
    return row, None


def _mark_done(memory_id):
    with _pass_lock:
        if _open_pass:
            _open_pass['done'].add(int(memory_id))


def _meta_of(row):
    try:
        return json.loads(row[10]) if row[10] else {}
    except Exception:
        return {}


def _stamp(cursor, pt, memory_id, meta, **updates):
    """Write meta (+ librarian_at) back to a chunk."""
    meta['librarian_at'] = pt._now()
    meta.update(updates)
    cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                   (json.dumps(meta, ensure_ascii=False), int(memory_id)))


def _insert_derived(cursor, pt, md, src_row, content, layer, scope,
                    entity_id=None, tier=None):
    """Insert a derived chunk: inherits created/label/private_key from the
    source, carries meta.derived_from + a chunk→chunk 'derived_from' edge,
    gets mechanical meta + mention edges. Embedding rides the backfill latch."""
    (src_id, _l, _s, _c, _e, _t, label, _f, _imp, private_key, _m, created) = src_row
    now = pt._now()
    arows = md.entity_aliases(cursor, scope)
    amap = md.alias_map(arows)
    hits = md.match_entities(content, [a for _, _, a in arows])
    mention_ids = []
    for h in hits:
        pair = amap.get(h.lower())
        if pair and pair[0] not in mention_ids and pair[0] != entity_id:
            mention_ids.append(pair[0])
    meta = md.save_meta(content, exclude_names=[h.lower() for h in hits])
    meta['derived_from'] = src_id
    meta['librarian_v'] = 1
    # Born reviewed: she just made this chunk during a pass — it must not
    # re-enter the next batch for a second look (double-work / re-merge loop).
    meta['librarian_at'] = now
    meta['added_by'] = 'ai'
    cursor.execute(
        'INSERT INTO chunks (layer, scope, content, entity_id, tier, label, '
        'favorite, importance, private_key, meta, created, updated) '
        'VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?)',
        (layer, scope, content, entity_id, tier, label, private_key,
         json.dumps(meta, ensure_ascii=False), created, now))
    new_id = cursor.lastrowid
    md.seed_edges(cursor, new_id, mention_ids, now)
    cursor.execute(
        "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
        "VALUES ('chunk', ?, 'chunk', ?, 'derived_from', 1.0, ?)",
        (new_id, src_id, now))
    return new_id


# ─── Duplicate threshold (merge is gated in CODE, like the charter) ─────────

def _merge_threshold():
    """Settings dial, clamped to [0.5, 0.99]. Below it, merge_memories refuses
    — 'similar-ish' is never mergeable, only measured near-duplicates."""
    try:
        from core.plugin_loader import plugin_loader
        v = float(plugin_loader.get_plugin_settings('mindpalace')
                  .get('librarian_merge_threshold', 0.90))
        return min(0.99, max(0.5, v))
    except Exception:
        return 0.90


def _merge_rewrite_enabled():
    """librarian_merge_rewrite (default OFF): whether merge_memories may
    write a distilled replacement text. The similarity gate proves the
    originals are near-duplicates, but nothing validates the REWRITE — a
    bad model can pass the gate on true dupes and still fabricate the
    surviving text. Off = the longest original survives verbatim, zero
    fabrication surface. Fails toward OFF (silent-default invariant)."""
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings('mindpalace')
                    .get('librarian_merge_rewrite'))
    except Exception:
        return False


def _similarity_gate(cursor, ids, threshold):
    """(ok, message). Every pair must measure >= threshold by stored-embedding
    cosine (palace vectors are normalized — plain dot). No embedding or mixed
    providers → refuse: what can't be verified can't be merged."""
    ph = ','.join('?' * len(ids))
    rows = cursor.execute(
        f'SELECT id, embedding, embedding_provider, embedding_dim '
        f'FROM chunks WHERE id IN ({ph})', list(ids)).fetchall()
    if any(r[1] is None for r in rows):
        return False, ("Similarity can't be verified (a memory has no embedding "
                       "yet) — merge refused. Try again after the next backfill.")
    if len({(r[2], r[3]) for r in rows}) != 1:
        return False, ("Similarity can't be verified (mixed embedding providers) "
                       "— merge refused.")
    import numpy as np
    vecs = {}
    for cid, blob, _prov, dim in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        if dim and v.shape[0] != dim:
            return False, f"Corrupt embedding on [{cid}] — merge refused."
        vecs[cid] = v
    order = list(vecs)
    lo, pair = 1.0, None
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            s = float(np.dot(vecs[order[i]], vecs[order[j]]))
            if s < lo:
                lo, pair = s, (order[i], order[j])
    if lo < threshold:
        return False, (f"[{pair[0]}] and [{pair[1]}] measure {lo:.2f} similar — "
                       f"below the {threshold:.2f} duplicate threshold. Not "
                       f"duplicates; merge refused. (prune or keep them instead.)")
    return True, None


# ─── Verbs ───────────────────────────────────────────────────────────────────

def _atomize(memory_id, parts):
    pt, md = _pt(), _md()
    if parts is not None and not isinstance(parts, list):
        # A bare string iterates per-CHARACTER — 'lunch' would shred into
        # five one-letter chunks. Lists only, like the other bulk verbs.
        return "atomize_memory needs parts as a LIST of strings.", False
    parts = [str(p).strip() for p in (parts or []) if str(p).strip()]
    if len(parts) < 2:
        return "atomize_memory needs at least 2 parts (use mark_processed if it's fine as one).", False
    if len(parts) > MAX_PARTS:
        return f"Too many parts ({len(parts)} > {MAX_PARTS}) — split the biggest concepts only.", False
    long = [p for p in parts if len(p) > PART_MAX_CHARS]
    if long:
        return f"{len(long)} part(s) exceed {PART_MAX_CHARS} chars — atomize means smaller.", False
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        row, err = _gate(cursor, memory_id)
        if err:
            return err, False
        if row[7] or (row[8] is not None and row[8] >= NEVER_FADES):
            return f"Memory [{memory_id}] is protected (favorite/core) — atomize refused.", False
        meta = _meta_of(row)
        new_ids = [_insert_derived(cursor, pt, md, row, p, row[1], row[2],
                                   entity_id=row[4], tier=row[5]) for p in parts]
        _stamp(cursor, pt, memory_id, meta,
               pruned_at=pt._now(), pruned_reason='atomized',
               atomized_into=new_ids)
        conn.commit()
    pt.reset_backfill_latch()
    _mark_done(memory_id)
    _log_pass('atomized', row[1], memory_id,
              f"split [{memory_id}] into {len(new_ids)} parts {new_ids}",
              {'preview': row[3][:80]})
    try:
        pt._publish_mind(row[1], row[2], 'update')
    except Exception:
        pass
    return f"Split [{memory_id}] into {len(new_ids)} parts: {new_ids}. Original retired to the archive.", True


def _merge(memory_ids, content=None):
    pt, md = _pt(), _md()
    try:
        ids = sorted({int(i) for i in (memory_ids or [])})
    except (TypeError, ValueError):
        return "merge_memories needs a list of memory [id]s.", False
    if len(ids) < 2:
        return "merge_memories needs at least 2 ids — one memory is already merged.", False
    if len(ids) > MAX_PARTS:
        return f"Too many at once ({len(ids)} > {MAX_PARTS}) — merge the tightest cluster first.", False
    content = (content or '').strip() or None
    rewrite_note = ''
    if content and not _merge_rewrite_enabled():
        content = None
        rewrite_note = (" Your distilled text was NOT used — rewrite-on-merge "
                        "is disabled; the longest original survives verbatim.")
    if content and len(content) > PART_MAX_CHARS:
        return f"Merged content must stay under {PART_MAX_CHARS} chars — distill it.", False
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        rows = []
        for mid in ids:
            row, err = _gate(cursor, mid, kinds=('dedup',))
            if err:
                return err, False
            if row[7] or (row[8] is not None and row[8] >= NEVER_FADES):
                return (f"Memory [{mid}] is protected (favorite/core) — merge "
                        f"would retire it. Refused."), False
            rows.append(row)
        if len({r[1] for r in rows}) != 1:
            return "merge_memories only merges within one layer.", False
        if len({r[9] for r in rows}) != 1:
            return "These memories carry different privacy keys — merge refused.", False
        ok, why = _similarity_gate(cursor, ids, _merge_threshold())
        if not ok:
            return why, False
        base = max(rows, key=lambda r: len(r[3] or ''))
        earliest = min(r[11] for r in rows)
        src = list(base)
        src[11] = earliest   # the merged memory remembers the FIRST occurrence
        new_id = _insert_derived(cursor, pt, md, tuple(src), content or base[3],
                                 base[1], base[2], entity_id=base[4], tier=base[5])
        raw = cursor.execute('SELECT meta FROM chunks WHERE id = ?', (new_id,)).fetchone()[0]
        nmeta = json.loads(raw) if raw else {}
        nmeta['derived_from'] = ids   # provenance widens to the whole cluster
        cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                       (json.dumps(nmeta, ensure_ascii=False), new_id))
        now = pt._now()
        for r in rows:
            _stamp(cursor, pt, r[0], _meta_of(r), pruned_at=now,
                   pruned_reason='merged', merged_into=new_id)
            if r[0] != base[0]:   # base already has its derived_from edge
                cursor.execute(
                    "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'chunk', ?, 'derived_from', 1.0, ?)",
                    (new_id, r[0], now))
        conn.commit()
    pt.reset_backfill_latch()
    for mid in ids:
        _mark_done(mid)
    _log_pass('merged', base[1], new_id,
              f"merged {len(ids)} duplicates {ids} → [{new_id}]",
              {'preview': (content or base[3])[:80]})
    try:
        pt._publish_mind(base[1], base[2], 'update')
    except Exception:
        pass
    return (f"Merged {len(ids)} duplicates → [{new_id}] (earliest date kept). "
            f"Originals retired to the archive, recoverable."
            + rewrite_note), True


def _promote(memory_id, layer, entity=None, content=None, kind=None):
    pt, md = _pt(), _md()
    if layer not in ('self', 'entities'):
        return "promote_memory: layer must be 'self' or 'entities'.", False
    entity = (entity or '').strip() or None
    if layer == 'entities' and not entity:
        return "promote_memory to entities needs an entity name.", False
    # Optional category for a NEWLY minted entity (Krem 2026-07-19: the sort
    # drain created 9 kind-less "uncategorized" cards). Soft-validated: an
    # unknown kind files the entity unsorted rather than failing the verb.
    # upsert_entity COALESCEs, so an existing entity's kind never changes.
    kind = (str(kind).strip().lower() if kind else '') or None
    if kind:
        try:
            from plugins.mindpalace.tools import templates
            if kind not in templates.valid_kinds():
                kind = None
        except Exception:
            kind = None
    if entity and (len(entity) > 80 or '\n' in entity):
        # This verb CREATES entities (unlike set_links) — an uncapped name
        # pollutes the roster, aliases, and every future link pass.
        return ("promote_memory: entity name too long (max 80 chars, one "
                "line) — use the person/place/thing's short name."), False
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        row, err = _gate(cursor, memory_id)
        if err:
            return err, False
        text = (content or '').strip() or row[3]
        if len(text) > PART_MAX_CHARS:
            return f"Promoted content must stay under {PART_MAX_CHARS} chars — distill it.", False
        entity_id, tier = None, None
        if layer == 'entities':
            entity_id = pt.upsert_entity(cursor, entity, row[2], kind=kind)
            tier = 2  # facts; headlines stay librarian/import territory
        new_id = _insert_derived(cursor, pt, md, row, text, layer, row[2],
                                 entity_id=entity_id, tier=tier)
        meta = _meta_of(row)
        promoted = meta.get('promoted_to', [])
        promoted.append(new_id)
        _stamp(cursor, pt, memory_id, meta, promoted_to=promoted)
        conn.commit()
    pt.reset_backfill_latch()
    _mark_done(memory_id)
    where = f"entity '{entity}'" if entity else "the self layer"
    _log_pass('promoted', layer, new_id,
              f"promoted [{memory_id}] → [{new_id}] on {where}",
              {'preview': text[:80]})
    try:
        pt._publish_mind(layer, row[2], 'save')
    except Exception:
        pass
    return f"Promoted [{memory_id}] → [{new_id}] on {where}. Original stays.", True


def _prune(memory_id, reason=None):
    pt = _pt()
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        row, err = _gate(cursor, memory_id)
        if err:
            return err, False
        # THE CHARTER — enforced in code. Favorites and the never-fades band
        # do not get pruned, whatever the pass thinks.
        if row[7] or (row[8] is not None and row[8] >= NEVER_FADES):
            return (f"Memory [{memory_id}] is protected (favorite/core) — "
                    f"it stays. The hare stays.", False)
        meta = _meta_of(row)
        _stamp(cursor, pt, memory_id, meta,
               pruned_at=pt._now(),
               pruned_reason=(str(reason).strip()[:200] if reason else None))
        conn.commit()
    _mark_done(memory_id)
    why = (str(reason).strip()[:120] if reason else '')
    _log_pass('retired', row[1], memory_id,
              f"retired [{memory_id}]" + (f" — {why}" if why else ''),
              {'preview': row[3][:80]})
    try:
        pt._publish_mind(row[1], row[2], 'update')
    except Exception:
        pass
    return f"Retired [{memory_id}] to the archive (recoverable).", True


def _set_links(entries):
    """The link pass's single verb — bulk by construction. Resolves entity
    names (NOCASE, scope+global), seeds chunk↔entity edges, and stamps
    meta.link_at on every entry — connected or ruled connection-free, the
    verdict drains the queue (dates-pass pattern). Unknown names are noted
    but the entry still files: requeueing won't invent the entity. Never
    bumps `updated` or `librarian_at` — linking is annotation, not review."""
    pt, md = _pt(), _md()
    with _pass_lock:
        if not _open_pass:
            return ("No librarian pass is open. set_links only works "
                    "during a link pass."), False
        if _open_pass.get('kind', 'sort') != 'link':
            return "set_links only works during a link pass.", False
        allowed = set(_open_pass['ids'])
        scope = _open_pass['scope']
    if not isinstance(entries, list) or not entries:
        return "set_links needs entries: [{memory_id, entities}, ...].", False
    linked = clear = 0
    refused = []
    log_rows = []
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        now = pt._now()
        for e in entries[:100]:
            if not isinstance(e, dict):
                refused.append("malformed entry")
                continue
            try:
                mid = int(e.get('memory_id'))
            except (TypeError, ValueError):
                refused.append("entry missing memory_id")
                continue
            if mid not in allowed:
                refused.append(f"[{mid}] not in this pass")
                continue
            row = cursor.execute('SELECT layer, scope, meta FROM chunks '
                                 'WHERE id = ?', (mid,)).fetchone()
            if not row or row[1] != scope:
                refused.append(f"[{mid}] not found in scope")
                continue
            names = [str(n).strip() for n in (e.get('entities') or [])
                     if str(n).strip()][:6]
            hits = []
            for name in names:
                erow = cursor.execute(
                    "SELECT id, name FROM entities WHERE name = ? COLLATE NOCASE "
                    "AND scope IN (?, 'global')", (name, scope)).fetchone()
                if erow:
                    hits.append(erow)
                else:
                    refused.append(f"[{mid}] no entity named '{name}'")
            if hits:
                md.seed_edges(cursor, mid, [h[0] for h in hits], now)
                linked += 1
                log_rows.append((row[0], mid, ", ".join(h[1] for h in hits)))
            else:
                clear += 1
            try:
                meta = json.loads(row[2]) if row[2] else {}
            except Exception:
                meta = {}
            meta['link_at'] = now
            cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                           (json.dumps(meta, ensure_ascii=False), mid))
            _mark_done(mid)
        conn.commit()
    for layer, mid, joined in log_rows:
        _log_pass('linked', layer, mid, f"connected [{mid}] ↔ {joined}")
    msg = f"Filed: {linked} connected, {clear} ruled connection-free."
    if refused:
        msg += " Notes: " + "; ".join(refused[:5])
    return msg, True


_ISO_EVENT_RE = re.compile(r'^\d{4}(?:-\d{2}-\d{2}(?:T\d{2}:\d{2})?)?$')


def _valid_iso_event(s):
    """YYYY | YYYY-MM-DD | YYYY-MM-DDTHH:MM, real calendar, sane range."""
    if not isinstance(s, str) or not _ISO_EVENT_RE.match(s):
        return False
    if not 1900 <= int(s[:4]) <= 2100:
        return False
    try:
        if len(s) >= 10:
            datetime.strptime(s[:10], '%Y-%m-%d')
        if len(s) > 10:
            datetime.strptime(s[11:], '%H:%M')
    except ValueError:
        return False
    return True


def _set_event_dates(entries):
    """The dates pass's single verb — bulk by construction. Writes
    meta.event_dates + event_date_src='librarian' (terminal: the regex floor
    never overwrites it) and stamps temporal_at on every entry, dates or not,
    so the pass queue drains. Never bumps `updated` or `librarian_at` —
    dating is annotation, not review."""
    pt = _pt()
    with _pass_lock:
        if not _open_pass:
            return ("No librarian pass is open. set_event_dates only works "
                    "during a dates pass."), False
        if _open_pass.get('kind', 'sort') != 'dates':
            return "set_event_dates only works during a dates pass.", False
        allowed = set(_open_pass['ids'])
        scope = _open_pass['scope']
    if not isinstance(entries, list) or not entries:
        return "set_event_dates needs entries: [{memory_id, dates}, ...].", False
    dated = dateless = 0
    refused = []
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        now = pt._now()
        for e in entries[:100]:
            if not isinstance(e, dict):
                refused.append("malformed entry")
                continue
            try:
                mid = int(e.get('memory_id'))
            except (TypeError, ValueError):
                refused.append("entry missing memory_id")
                continue
            if mid not in allowed:
                refused.append(f"[{mid}] not in this pass")
                continue
            dates = [d.strip() for d in (e.get('dates') or [])
                     if isinstance(d, str) and d.strip()][:4]
            bad = [d for d in dates if not _valid_iso_event(d)]
            if bad:
                refused.append(f"[{mid}] invalid {', '.join(bad[:2])} — use "
                               f"YYYY, YYYY-MM-DD or YYYY-MM-DDTHH:MM")
                continue
            row = cursor.execute('SELECT scope, meta FROM chunks WHERE id = ?',
                                 (mid,)).fetchone()
            if not row or row[0] != scope:
                refused.append(f"[{mid}] not found in scope")
                continue
            try:
                meta = json.loads(row[1]) if row[1] else {}
            except Exception:
                meta = {}
            if dates:
                meta['event_dates'] = dates
                meta['event_date_src'] = 'librarian'
                dated += 1
                _log_pass('dated', None, mid,
                          f"dated [{mid}] → {', '.join(dates)}")
            else:
                # A "no date" verdict files the entry but PRESERVES any
                # already-resolved dates (regex floor or a prior install's
                # librarian) — a lazy model's empty list must never strip
                # a real date off Upcoming. The verdict is about the text;
                # the resolver's data stands.
                dateless += 1
                _log_pass('dateless', None, mid,
                          f"[{mid}] ruled dateless"
                          + (" (kept existing "
                             + ', '.join(meta.get('event_dates') or []) + ")"
                             if meta.get('event_dates') else ""))
            meta['temporal_at'] = now
            cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                           (json.dumps(meta, ensure_ascii=False), mid))
            _mark_done(mid)
        conn.commit()
    msg = f"Filed: {dated} dated, {dateless} ruled dateless."
    if refused:
        msg += " Refused: " + "; ".join(refused[:5])
        return msg, dated + dateless > 0
    return msg, True


def _as_bool(v):
    """Tool-arg boolean: providers sometimes hand back the STRING 'false',
    which is truthy — coerce honestly instead of setting a favorite by
    accident."""
    if isinstance(v, str):
        return v.strip().lower() in ('true', '1', 'yes')
    return bool(v)


def _mark(memory_id, importance=None, favorite=None):
    pt = _pt()
    rated = None
    if isinstance(importance, bool):
        # importance=true would float() to 1.0 — a core-pin by type accident.
        return "mark_processed: importance must be a number between 0 and 1.", False
    if importance is not None and not _importance_enabled():
        importance = None   # importance disabled (alpha): file without rating
    if importance is not None:
        try:
            rated = min(1.0, max(0.0, float(importance)))
        except (TypeError, ValueError):
            return "mark_processed: importance must be a number between 0 and 1.", False
    ratcheted = False
    if favorite is not None:
        favorite = _as_bool(favorite)
        if not favorite:
            # One-way ratchet (2026-07-19, reverses the 2026-07-16 two-way
            # design): the librarian may GRANT ★, never clear one. Humans
            # hand-set favorites as merge/prune shields now — a stray verb
            # must not strip protection the UI granted. false = no-op: the
            # flag stays whatever it is and the memory still files.
            favorite, ratcheted = None, True
    pinned_note = False
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        row, err = _gate(cursor, memory_id)
        if err:
            return err, False
        # Pin fence (scout find, 2026-07-19): prune/atomize/merge all refuse
        # protected rows, but a rating here could silently drop a human-set
        # never-fades pin below the band — the one gap in the fence. Same
        # shape as the favorite ratchet: the rating is ignored, the memory
        # still files as reviewed.
        protected = row[7] or (row[8] is not None and row[8] >= NEVER_FADES)
        if rated is not None and protected and rated < (row[8] or 0):
            rated, pinned_note = None, True
        if rated is not None:
            cursor.execute('UPDATE chunks SET importance = ?, updated = ? '
                           'WHERE id = ?', (rated, pt._now(), int(memory_id)))
        if favorite:
            # The librarian is the ONLY AI-side favorite-setter (save_memory
            # dropped the param — in-the-moment saves lack the bird's-eye
            # view; Krem 2026-07-16). Same mapping as the UI: flag + the
            # never-fades band together. Independent of the importance alpha.
            # Clearing lives in the UI only — see the ratchet above. MAX
            # keeps an existing higher pin (a 1.0 never downgrades to 0.95).
            cursor.execute('UPDATE chunks SET favorite = 1, '
                           'importance = MAX(COALESCE(importance, 0), 0.95), '
                           'updated = ? WHERE id = ?',
                           (pt._now(), int(memory_id)))
        _stamp(cursor, pt, memory_id, _meta_of(row))
        conn.commit()
    _mark_done(memory_id)
    bits = []
    if rated is not None:
        core = " — core, never fades" if rated >= NEVER_FADES else ""
        bits.append(f"importance {rated:g}{core}")
    if favorite:
        bits.append("favorite ★")
    if ratcheted:
        bits.append("favorite unchanged — granting only from here")
    if pinned_note:
        bits.append("rating ignored — this memory carries a core pin")
    extra = f" ({', '.join(bits)})" if bits else ""
    # Ratings and favorites are DATA WRITES — they get a ledger child like
    # every other verb (was invisible before; audit scout 2026-07-19).
    _log_pass('marked', row[1], memory_id,
              f"filed [{memory_id}] as reviewed{extra}")
    return f"Filed [{memory_id}] as reviewed{extra}.", True


def execute(function_name, arguments, config):
    try:
        if function_name == 'atomize_memory':
            return _atomize(arguments.get('memory_id'), arguments.get('parts'))
        if function_name == 'merge_memories':
            return _merge(arguments.get('memory_ids'),
                          content=arguments.get('content'))
        if function_name == 'promote_memory':
            return _promote(arguments.get('memory_id'), arguments.get('layer'),
                            entity=arguments.get('entity'),
                            content=arguments.get('content'),
                            kind=arguments.get('kind'))
        if function_name == 'prune_memory':
            return _prune(arguments.get('memory_id'), reason=arguments.get('reason'))
        if function_name == 'set_links':
            return _set_links(arguments.get('entries'))
        if function_name == 'mark_processed':
            return _mark(arguments.get('memory_id'),
                         importance=arguments.get('importance'),
                         favorite=arguments.get('favorite'))
        if function_name == 'set_event_dates':
            return _set_event_dates(arguments.get('entries'))
        if function_name == 'run_librarian':
            pt = _pt()
            scope = pt._get_current_scope()
            if scope is None:
                return "Memory scope is disabled for this chat — cannot start a pass.", False
            if scope == 'global':
                return "The global overlay is read-only — no librarian pass there.", False
            from plugins.mindpalace.tools import librarian
            return librarian.start(scope, what=arguments.get('what', 'all'),
                                   kind=arguments.get('pass', 'sort'))
        return f"Unknown librarian function: {function_name}", False
    except Exception as e:
        logger.error(f"[LIBRARIAN] Verb error: {e}")
        return f"Librarian error: {e}", False
