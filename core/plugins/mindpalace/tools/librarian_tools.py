# core/plugins/mindpalace/tools/librarian_tools.py
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
TOOL_CATEGORY = 'memory'
GROUP = 'Mind Palace'   # Toolsets UI merges same-GROUP modules

MAX_PARTS = 10
PART_MAX_CHARS = 512
NEVER_FADES = 0.9

AVAILABLE_FUNCTIONS = [
    'atomize_memory',
    'merge_memories',
    'keep_separate',
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
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "atomize_memory",
            "description": (
                "Librarian verb: split one multi-concept memory into single-concept parts. The original is retired "
                "(recoverable). Current-pass memories only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer"},
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
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "merge_memories",
            "description": (
                "Librarian verb: fold entries that record the SAME thing into one memory; it keeps the earliest date, "
                "the highest rating and every connection, and the originals are retired (recoverable). The newest "
                "entry's words survive unless keep= names another. Refused unless they measure as near-duplicates, "
                "or when the others carry details the survivor lacks (then keep_separate, or pass keep=). "
                "Current-pass memories only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_ids": {
                        "type": "array", "items": {"type": "integer"},
                        "description": "2-10 [id]s"
                    },
                    "keep": {
                        "type": "integer",
                        "description": "The [id] whose words survive — the most complete and correct one. Default: the newest."
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
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "keep_separate",
            "description": (
                "Dedup-pass verb: this group measures similar but the memories are genuinely DIFFERENT - file the "
                "verdict and keep each as is. One call per group."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_ids": {
                        "type": "array", "items": {"type": "integer"},
                        "description": "The group's [id]s"
                    }
                },
                "required": ["memory_ids"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "promote_memory",
            "description": (
                "Librarian verb: COPY a memory's content onto an entity as a fact; the original stays. "
                "Identity-worthy memories go on your sheet instead (update_self). Current-pass memories only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer"},
                    "layer": {"type": "string", "enum": ["entities"]},
                    "entity": {"type": "string",
                               "description": "Entity name (required when layer=entities)"},
                    "kind": {"type": "string",
                             "description": ("person | place | thing | event | other. Only used when a NEW entity is created.")},
                    "content": {"type": "string",
                                "description": "Reworded version. Omit to copy verbatim."}
                },
                "required": ["memory_id", "layer"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "prune_memory",
            "description": (
                "Librarian verb: retire a memory that no longer needs remembering. Soft: gone from search and recall,"
                " kept in the archive, reversible. Favorites and core memories are refused. Current-pass memories "
                "only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer"},
                    "reason": {"type": "string", "description": "One short line — a human reads it next to the retirement"}
                },
                "required": ["memory_id", "reason"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "set_links",
            "description": (
                "Link-pass verb: connect the memories in this pass to the existing people/places/things they are "
                "about; ONE call covers every [id]. Linking never creates an entity. No connection needed = an empty "
                "list, still filed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "One per presented [id]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "memory_id": {"type": "integer"},
                                "entities": {"type": "array",
                                             "items": {"type": "string"},
                                             "description": ("Existing entity names; [] = none needed")}
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
        "writes": True,
        "is_local": True,
        "function": {
            "name": "run_librarian",
            "description": (
                "Start a librarian pass over your memory (current scope). Daily caps apply per pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {
                        "type": "string",
                        "enum": ["all", "self"],
                        "description": "Sort pass only: 'all' = events + self layers (default), 'self' = the self layer"
                    },
                    "pass": {
                        "type": "string",
                        "enum": ["sort", "dates", "link", "dedup"],
                        "description": "sort (default) = review oldest unprocessed memories; dates = file date mentions as calendar dates; "
                                       "link = connect memories to the people/places/things they name; dedup = fold near-duplicates"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "set_event_dates",
            "description": (
                "Temporal-pass verb: file dates for the memories in this pass; ONE call covers every [id]. Give every"
                " date/time a memory's text refers to as ISO at the precision the text supports (YYYY, YYYY-MM-DD or "
                "YYYY-MM-DDTHH:MM), resolved against that memory's saved date. No real date = an empty list, still "
                "filed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "One per presented [id]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "memory_id": {"type": "integer"},
                                "dates": {"type": "array",
                                          "items": {"type": "string"},
                                          "description": ("ISO; [] = no real date")}
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
        "writes": True,
        "is_local": True,
        "hidden": True,
        "function": {
            "name": "mark_processed",
            "description": (
                "Librarian verb: this memory is fine as is - file it as reviewed. Current-pass memories only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer"},
                    "importance": {
                        "type": "number",
                        "description": ("0.0-1.0, how much it matters; rated memories surface more easily. 0.9+ = core memory, never fades or"
                                        " prunes. Omit to leave unrated.")
                    },
                    "favorite": {
                        "type": "boolean",
                        "description": ("true = favorite (never fades or prunes). Cannot be cleared from here.")
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
_open_pass = None   # {'scope', 'ids': set[int], 'done': set[int], 'kind', 'attempts'}


def open_pass(scope: str, chunk_ids, kind: str = 'sort') -> None:
    """kind: 'sort' (mark/atomize/promote/prune), 'dates' (set_event_dates),
    'link' (set_links) or 'dedup' (merge_memories / keep_separate). Verbs
    check the kind — a verb from another pass refuses, in code."""
    global _open_pass
    with _pass_lock:
        _open_pass = {'scope': scope, 'ids': set(int(i) for i in chunk_ids),
                      'done': set(), 'ledger': [], 'kind': kind, 'attempts': 0,
                      'retired': 0, 'cap_refused': set(), 'capped': None}
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
        return {'scope': None, 'presented': 0, 'handled': 0, 'ledger': [],
                'capped': None}
    return {'scope': p['scope'], 'presented': len(p['ids']),
            'handled': len(p['done']), 'ledger': p.get('ledger', []),
            'capped': p.get('capped')}


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


def _note_attempt():
    """A pass verb was CALLED (accepted or refused). The engine reads this
    per message: a message where the model talked but never reached for a
    verb is not a completed message (2026-10-07 — a tool-incapable model's
    prose used to read as 'ruled distinct' and drain the dedup queue)."""
    with _pass_lock:
        if _open_pass:
            _open_pass['attempts'] += 1


def pass_attempts() -> int:
    with _pass_lock:
        return _open_pass['attempts'] if _open_pass else 0


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
                    entity_id=None, tier=None, importance=None, anchor=None):
    """Insert a derived chunk: inherits created/label/private_key from the
    source, carries meta.derived_from + a chunk→chunk 'derived_from' edge,
    gets mechanical meta + mention edges. Embedding rides the backfill latch.
    `importance`: what the source(s) earned — a split of a 0.7 memory is
    0.7 twice, a merge keeps the cluster's highest; born unrated was a
    silent loss (scout find 2026-10-30). `anchor`: the day the CONTENT was
    written when it differs from the row's created (a merge keeps the
    cluster's earliest date but the longest member's words)."""
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
    # anchor=created: the content was LIVED at the source's timestamp — a
    # "tonight" in a February memory must not resolve against drain day.
    meta = md.save_meta(content, exclude_names=[h.lower() for h in hits],
                        anchor=anchor or created)
    meta['derived_from'] = src_id
    meta['librarian_v'] = 1
    # Born reviewed: she just made this chunk during a pass — it must not
    # re-enter the next batch for a second look (double-work / re-merge loop).
    meta['librarian_at'] = now
    meta['added_by'] = 'ai'
    cursor.execute(
        'INSERT INTO chunks (layer, scope, content, entity_id, tier, label, '
        'favorite, importance, private_key, meta, created, updated) '
        'VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)',
        (layer, scope, content, entity_id, tier, label, importance, private_key,
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


def _setting(key, default, lo, hi):
    try:
        from core.plugin_loader import plugin_loader
        v = float(plugin_loader.get_plugin_settings('mindpalace').get(key, default))
        return min(hi, max(lo, v))
    except Exception:
        return default


def _retire_ratio():
    """Share of a pass's presented memories the librarian may retire
    (prune, split, merge losers) — the mass-prune fence (2026-10-30)."""
    return _setting('librarian_retire_ratio', 0.25, 0.05, 1.0)


def _retire_per_night():
    """Retirements per scope per UTC day across every pass and drain."""
    return int(_setting('librarian_retire_per_night', 30, 1, 100000))


def current_pass_kind():
    """'sort' | 'dates' | 'link' | 'dedup' | 'self' while a pass is open,
    else None — the self sheet's write path asks (its librarian guards)."""
    with _pass_lock:
        return _open_pass.get('kind') if _open_pass else None


_night_capped = set()   # scopes whose night cap refused during the open pass


def _local_day_start():
    """Tonight's counter starts at LOCAL midnight (ledger ts are UTC ISO)."""
    from datetime import timezone as _tz
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(_tz.utc).strftime('%Y-%m-%dT%H:%M:%S')


def _retired_tonight(cursor, scope):
    """Retirements this scope's ledger carries since local midnight: a
    prune or split is one, a merge of N is N-1 (its losers)."""
    n = 0
    for action, detail in cursor.execute(
            "SELECT action, detail FROM ledger WHERE scope = ? AND actor = 'librarian' "
            "AND action IN ('retired', 'split', 'merged') AND ts >= ?",
            (scope, _local_day_start())).fetchall():
        if action != 'merged':
            n += 1
            continue
        try:
            n += max(1, len((json.loads(detail or '{}').get('members') or [])) - 1)
        except Exception:
            n += 1
    return n


def night_capped(scope):
    """Did the night cap refuse this scope since the last ask? A drain
    reads it after each batch and ends the drain instead of grinding
    on with every retirement refused (2026-10-31)."""
    with _pass_lock:
        if scope in _night_capped:
            _night_capped.discard(scope)
            return True
        return False


def _retire_check(cursor, scope, n=1, ids=()):
    """(ok, why): may the open pass retire `n` more memories? Two fences,
    both code (scout audit 2026-10-30 — nothing limited pruning but the
    charter's prose): a share of what this pass was shown, and a count per
    scope per night across passes and drains (the ledger is the counter).
    The caps are settings a human raises in Admin. A refused id is
    remembered: mark_processed refuses to file it as reviewed, so it stays
    in the queue for a night with room. The dedup pass is exempt from the
    share (whole clusters are presented; a 6-copy cluster retires 5 of 6)
    and its refusal names no verb — keep_separate would file true
    duplicates as distinct."""
    import math
    with _pass_lock:
        p = _open_pass
        if not p:
            return True, None
        presented, so_far = max(1, len(p['ids'])), p.get('retired', 0)
        kind = p.get('kind', 'sort')
    rest = ("leave the rest for another night" if kind == 'dedup'
            else "mark_processed the rest")
    cap = max(2, math.ceil(_retire_ratio() * presented))
    why = None
    if kind != 'dedup' and so_far + n > cap:
        why = (f"Retirement cap for this pass reached ({so_far} of {presented} "
               f"presented, cap {cap}) — {rest}. A human raises "
               f"librarian_retire_ratio in Admin.")
        capped = 'pass'
    else:
        night = _retire_per_night()
        try:
            n_today = _retired_tonight(cursor, scope)
        except Exception:
            n_today = 0
        if n_today + so_far + n > night:
            why = (f"Tonight's retirement cap for '{scope}' is reached "
                   f"({n_today + so_far} of {night}) — {rest}. A human raises "
                   f"librarian_retire_per_night in Admin.")
            capped = 'night'
    if why is None:
        return True, None
    with _pass_lock:
        if _open_pass is not None:
            _open_pass['cap_refused'].update(int(i) for i in ids)
            _open_pass['capped'] = capped
        if capped == 'night':
            _night_capped.add(scope)
    return False, why


def _count_retired(n=1):
    with _pass_lock:
        if _open_pass is not None:
            _open_pass['retired'] = _open_pass.get('retired', 0) + n


_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")   # Unicode: Zoë, José, Кирилл


def _grounding_gap(original, parts):
    """Why a split is NOT grounded in the original, or None: a part that
    adds words the original never said beyond a small budget, or parts
    that together run far longer than the original (an atomize that
    invents is a retirement plus a fabrication — scout audit 2026-10-30)."""
    have = set(_WORD_RE.findall((original or '').lower()))
    if sum(len(p) for p in parts) > 1.3 * len(original or '') + 40:
        return "the parts together run far longer than the original — split it, don't expand it"
    for i, p in enumerate(parts, 1):
        words = _WORD_RE.findall(p.lower())
        novel = [w for w in words if w not in have and len(w) > 3]
        if len(novel) > max(2, int(0.2 * len(words))):
            return (f"part {i} adds words the original never said "
                    f"({', '.join(novel[:3])}) — quote the original")
    return None


_FACT_RE = re.compile(r"(?<![\w'])(?:\d[\d:/.\-]*\d\s*(?:am|pm)?|\d\s*(?:am|pm)|\d"
                      r"|[^\W\d_]{3,})(?![\w'])", re.IGNORECASE)
_FACT_STOP = {'the', 'this', 'that', 'these', 'those', 'and', 'but', 'with',
              'when', 'then', 'she', 'they', 'him', 'her', 'his', 'our', 'its',
              'was', 'were', 'had', 'has', 'have', 'not', 'for', 'from', 'into'}
_SENT_START = re.compile(r'(?:^|[.!?]\s+|\n\s*)$')


def _facts(text):
    """Numbers, times and capitalised names (Unicode) — a word that opens a
    sentence is capitalised by grammar, not by being a name, so it only
    counts when it is capitalised elsewhere too."""
    out = []
    for m in _FACT_RE.finditer(text or ''):
        t = m.group(0)
        if t[0].isdigit():
            out.append(re.sub(r'\s+', '', t.lower()))
        elif t[0].isupper() and not _SENT_START.search(text[:m.start()]):
            out.append(t.lower())
    return out


def _fact_gap(survivor, losers):
    """Facts (numbers, dates, names) the losers carry that the survivor's
    words lack — three or more and the group is a correction or a richer
    note, not the same thing twice. The survivor's EVERY word counts as
    had (a paraphrase is not a missing fact). Empty = merge freely."""
    have = set(_WORD_RE.findall((survivor or '').lower())) | set(_facts(survivor))
    missing = []
    for text in losers:
        for k in _facts(text):
            if k not in have and k not in _FACT_STOP and k not in missing:
                missing.append(k)
    return missing if len(missing) >= 3 else []


def revert_run(scope, run_id):
    """Undo everything one librarian run (a night, a drain, a lone pass)
    did, from its ledger rows: retirements come back, merges and splits
    unwind, promoted copies retire, ratings, dates and links go back to
    their before-values. Soft throughout; the run stays in the ledger with
    a 'reverted' line. A row the human (or a later run) changed since is
    left alone and counted as skipped; favorites are never lowered or
    retired; queue stamps stay so the next night does not redo the work
    (2026-10-31). Returns (counts, None) or (None, why)."""
    pt = _pt()
    from plugins.mindpalace.tools import ledger as lg
    counts = {'restored': 0, 'retired': 0, 'ratings': 0, 'dates': 0,
              'links': 0, 'skipped': 0}
    with pt._get_connection() as conn:
        cur = conn.cursor()
        root = cur.execute('SELECT id, actor, scope FROM ledger WHERE id = ?',
                           (int(run_id),)).fetchone()
        if not root or root[2] != scope or root[1] != 'librarian':
            return None, "not a librarian run in this scope"
        if cur.execute("SELECT 1 FROM ledger WHERE scope = ? AND action = 'reverted' "
                       "AND target = ?", (scope, str(run_id))).fetchone():
            return None, "this run was already reverted"
        rows, frontier = [], [int(run_id)]
        for _depth in range(3):
            if not frontier:
                break
            ph = ','.join('?' * len(frontier))
            kids = cur.execute(
                f"SELECT id, action, target, detail FROM ledger WHERE parent_id IN ({ph}) "
                f"AND actor = 'librarian'", frontier).fetchall()
            rows += kids
            frontier = [k[0] for k in kids]
        now = pt._now()

        def meta_of(cid):
            raw = cur.execute('SELECT meta FROM chunks WHERE id = ?', (cid,)).fetchone()
            if not raw:
                return None
            try:
                m = json.loads(raw[0]) if raw[0] else {}
            except Exception:
                m = {}
            return m if isinstance(m, dict) else {}

        def put(cid, m):
            cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                        (json.dumps(m, ensure_ascii=False), cid))

        def protected(cid):
            r = cur.execute('SELECT favorite, importance FROM chunks WHERE id = ?',
                            (cid,)).fetchone()
            return bool(r) and (bool(r[0]) or (r[1] is not None and r[1] >= NEVER_FADES))

        def restore(cid, at=None, *keys):
            """Un-retire — only the retirement this run made (same stamp)."""
            m = meta_of(cid)
            if m is None or not m.get('pruned_at'):
                return False
            if at and m.get('pruned_at') != at:
                return False                      # retired again since, by someone else
            for k in ('pruned_at', 'pruned_reason') + keys:
                m.pop(k, None)
            m['reverted_at'] = now
            put(cid, m)
            counts['restored'] += 1
            return True

        def retire(cid, why):
            m = meta_of(cid)
            if m is None or m.get('pruned_at') or protected(cid):
                return False
            m.update(pruned_at=now, pruned_reason=why)
            put(cid, m)
            counts['retired'] += 1
            return True

        for _lid, act, target, detail in rows:
            try:
                d = json.loads(detail) if detail else {}
            except Exception:
                d = {}
            if act in ('pass', 'report', 'distinct'):
                continue                      # structure and no-ops: nothing to undo
            try:
                tid = int(target)
            except (TypeError, ValueError):
                continue
            if act == 'retired':
                m = meta_of(tid) or {}
                if m.get('merged_into') or m.get('atomized_into') \
                        or not restore(tid, d.get('at')):
                    counts['skipped'] += 1
            elif act == 'split':
                m = meta_of(tid) or {}
                parts = d.get('parts') or m.get('atomized_into') or []
                if m.get('atomized_into') == parts and restore(tid, d.get('at'), 'atomized_into'):
                    for p in parts:
                        retire(int(p), 'reverted split')
                else:
                    counts['skipped'] += 1
            elif act == 'merged':
                m = meta_of(tid) or {}
                members = d.get('members') or m.get('derived_from') or []
                if isinstance(members, int):
                    members = [members]
                # A survivor consumed by a later merge or split, or one the
                # human starred, stays: its members would come back beside
                # something newer.
                if m.get('merged_into') or m.get('atomized_into') or protected(tid):
                    counts['skipped'] += 1
                    continue
                retire(tid, 'reverted merge')
                for mid in members:
                    mm = meta_of(int(mid)) or {}
                    if mm.get('merged_into') == tid:
                        restore(int(mid), None, 'merged_into')
                    else:
                        counts['skipped'] += 1
            elif act == 'promoted':
                if not retire(tid, 'reverted promotion'):
                    counts['skipped'] += 1
                    continue
                src = d.get('source')
                if src is not None:
                    m = meta_of(int(src))
                    if m and tid in (m.get('promoted_to') or []):
                        m['promoted_to'] = [p for p in m['promoted_to'] if p != tid]
                        if not m['promoted_to']:
                            m.pop('promoted_to')
                        put(int(src), m)
            elif act == 'marked':
                if 'before' not in d:
                    continue
                r = cur.execute('SELECT importance, favorite FROM chunks WHERE id = ?',
                                (tid,)).fetchone()
                if not r or ('after' in d and r[0] != d['after']):
                    counts['skipped'] += 1          # rated again since
                    continue
                # The rating goes back; a favorite never comes off here.
                cur.execute('UPDATE chunks SET importance = ? WHERE id = ?',
                            (d['before'], tid))
                counts['ratings'] += 1
            elif act in ('dated', 'dateless'):
                before, after = d.get('before'), d.get('after')
                m = meta_of(tid)
                if m is None or not isinstance(before, dict):
                    continue
                keys = [k for k in before if not k.endswith('_at')]
                if isinstance(after, dict) and any(m.get(k) != after.get(k) for k in keys):
                    counts['skipped'] += 1          # dated again since
                    continue
                for k in keys:
                    if before[k] is None:
                        m.pop(k, None)
                    else:
                        m[k] = before[k]
                put(tid, m)
                counts['dates'] += 1
            elif act == 'linked':
                eids, at = d.get('entity_ids') or [], d.get('at')
                if eids and at:
                    ph = ','.join('?' * len(eids))
                    cur.execute(
                        f"DELETE FROM edges WHERE src_type = 'chunk' AND src_id = ? "
                        f"AND dst_type = 'entity' AND dst_id IN ({ph}) "
                        f"AND kind = 'mentions' AND created = ?", [tid, *eids, at])
                    counts['links'] += cur.rowcount
            else:
                counts['skipped'] += 1
        lg.record(scope, 'user', 'reverted', target=run_id, cursor=cur,
                  summary=(f"reverted run [{run_id}]: {counts['restored']} restored, "
                           f"{counts['retired']} retired, {counts['ratings']} ratings, "
                           f"{counts['dates']} dates, {counts['links']} links put back"
                           + (f"; {counts['skipped']} changed since, left alone"
                              if counts['skipped'] else '')),
                  detail=counts)
        conn.commit()
    try:
        pt._publish_mind('events', scope, 'update')
    except Exception:
        pass
    return counts, None


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
                       f"duplicates; merge refused. (keep_separate, or merge the "
                       f"tightest pair.)")
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
        gap = _grounding_gap(row[3], parts)
        if gap:
            return f"atomize_memory keeps your words: {gap}. Refused.", False
        ok, why = _retire_check(cursor, row[2], 1, ids=[memory_id])
        if not ok:
            return why, False
        meta = _meta_of(row)
        new_ids = [_insert_derived(cursor, pt, md, row, p, row[1], row[2],
                                   entity_id=row[4], tier=row[5],
                                   importance=row[8]) for p in parts]
        at = pt._now()
        _stamp(cursor, pt, memory_id, meta,
               pruned_at=at, pruned_reason='atomized',
               atomized_into=new_ids)
        conn.commit()
    pt.reset_backfill_latch()
    _count_retired(1)
    _mark_done(memory_id)
    _log_pass('split', row[1], memory_id,
              f"split [{memory_id}] into {len(new_ids)} parts {new_ids}",
              {'preview': row[3][:80], 'parts': new_ids, 'at': at})
    try:
        pt._publish_mind(row[1], row[2], 'update')
    except Exception:
        pass
    return f"Split [{memory_id}] into {len(new_ids)} parts: {new_ids}. Original retired to the archive.", True


def _merge(memory_ids, content=None, keep=None):
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
        # The survivor's words (2026-10-30, was: the longest): keep=<id> when
        # she chose, else the NEWEST member — a later correction beats the
        # note it corrects. Without an explicit keep, losers that carry
        # facts the survivor lacks (a date, a name, a number) refuse: that
        # is a correction or a richer note, not the same thing twice.
        if keep is not None:
            try:
                keep = int(keep)
            except (TypeError, ValueError):
                return "merge_memories: keep must be one of the group's ids.", False
            if keep not in ids:
                return f"merge_memories: keep=[{keep}] is not in the group.", False
            base = next(r for r in rows if r[0] == keep)
        else:
            base = max(rows, key=lambda r: (r[11] or '', r[0]))
            gap = _fact_gap(base[3], [r[3] for r in rows if r[0] != base[0]])
            if gap:
                return (f"The others carry details [{base[0]}] lacks "
                        f"({', '.join(gap[:4])}) — keep_separate, or merge with "
                        f"keep=<id> of the one that has everything."), False
        ok, why = _retire_check(cursor, base[2], len(ids) - 1, ids=ids)
        if not ok:
            return why, False
        earliest = min(r[11] for r in rows)
        src = list(base)
        src[11] = earliest   # the merged memory remembers the FIRST occurrence
        # What the cluster earned travels onto the survivor (scout find
        # 2026-10-30 — it was born unrated, with name-matched edges only,
        # and re-entered the dates queue): the highest rating, every
        # member's mention edges (the link pass made some by hand), and a
        # librarian date verdict — the survivor's own first, else any
        # member's. Its words are base's, so they anchor to base's day.
        rated = [r[8] for r in rows if r[8] is not None]
        new_id = _insert_derived(cursor, pt, md, tuple(src), content or base[3],
                                 base[1], base[2], entity_id=base[4], tier=base[5],
                                 importance=max(rated) if rated else None,
                                 anchor=base[11])
        raw = cursor.execute('SELECT meta FROM chunks WHERE id = ?', (new_id,)).fetchone()[0]
        nmeta = json.loads(raw) if raw else {}
        nmeta['derived_from'] = ids   # provenance widens to the whole cluster
        for m in [_meta_of(base)] + [_meta_of(r) for r in rows]:
            if m.get('temporal_at') or m.get('event_date_src') == 'librarian':
                for k in ('event_dates', 'event_date_src', 'temporal_at'):
                    nmeta.pop(k, None)
                    if k in m:
                        nmeta[k] = m[k]
                break
        cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                       (json.dumps(nmeta, ensure_ascii=False), new_id))
        now = pt._now()
        ph = ','.join('?' * len(ids))
        mentioned = [r[0] for r in cursor.execute(
            f"SELECT DISTINCT dst_id FROM edges WHERE src_type = 'chunk' "
            f"AND kind = 'mentions' AND src_id IN ({ph})", ids).fetchall()]
        md.seed_edges(cursor, new_id, [e for e in mentioned if e != base[4]], now)
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
    _count_retired(len(ids) - 1)
    for mid in ids:
        _mark_done(mid)
    _log_pass('merged', base[1], new_id,
              f"merged {len(ids)} duplicates {ids} → [{new_id}]",
              {'preview': (content or base[3])[:80], 'members': ids,
               'kept': base[0], 'at': now})
    try:
        pt._publish_mind(base[1], base[2], 'update')
    except Exception:
        pass
    return (f"Merged {len(ids)} duplicates → [{new_id}] (earliest date kept). "
            f"Originals retired to the archive, recoverable."
            + rewrite_note), True


def _keep_separate(memory_ids):
    """The dedup pass's other verb (2026-10-07): the group measured similar
    but is genuinely distinct. Stamps dedup_at on each id — the VERDICT
    drains the queue, exactly like set_event_dates / set_links. Before this
    the engine stamped a cluster whenever its message 'completed', so a
    model that answered in prose and never called anything filed the whole
    cluster as judged. Nothing else changes: no retire, no edit."""
    pt = _pt()
    try:
        ids = sorted({int(i) for i in (memory_ids or [])})
    except (TypeError, ValueError):
        return "keep_separate needs a list of memory [id]s.", False
    if not ids:
        return "keep_separate needs at least one [id].", False
    with pt._get_connection() as conn:
        cursor = conn.cursor()
        rows = []
        for mid in ids:
            row, err = _gate(cursor, mid, kinds=('dedup',))
            if err:
                return err, False
            rows.append(row)
        now = pt._now()
        for r in rows:
            meta = _meta_of(r)
            meta['dedup_at'] = now
            cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                           (json.dumps(meta, ensure_ascii=False), r[0]))
        conn.commit()
    for mid in ids:
        _mark_done(mid)
    _log_pass('distinct', rows[0][1], ids[0],
              f"kept {ids} separate — similar, not the same")
    return f"Kept {ids} separate — filed as judged, nothing changed.", True


def _promote(memory_id, layer, entity=None, content=None, kind=None):
    pt, md = _pt(), _md()
    if layer == 'self':
        # Retired 2026-07-26 with Sapph's consent: promoting to self minted
        # clone pairs the dedup scans tripped over, and identity lives on
        # the sheet now. The self pass hands her update_self for that.
        return ("promote_memory: the self layer retired — identity edits go "
                "through update_self (your nightly self pass). Promoting to "
                "'entities' still works."), False
    if layer != 'entities':
        return "promote_memory: layer must be 'entities'.", False
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
              {'preview': text[:80], 'source': int(memory_id)})
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
        if not (reason and str(reason).strip()):
            return (f"prune_memory needs a reason for [{memory_id}] — one short "
                    f"line on why it's noise (a human reads it next to the "
                    f"retirement)."), False
        ok, why = _retire_check(cursor, row[2], 1, ids=[memory_id])
        if not ok:
            return why, False
        meta = _meta_of(row)
        at = pt._now()
        _stamp(cursor, pt, memory_id, meta,
               pruned_at=at,
               pruned_reason=str(reason).strip()[:200])
        conn.commit()
    _count_retired(1)
    _mark_done(memory_id)
    why = (str(reason).strip()[:120] if reason else '')
    _log_pass('retired', row[1], memory_id,
              f"retired [{memory_id}]" + (f" — {why}" if why else ''),
              {'preview': row[3][:80], 'at': at})
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
                    "AND scope IN (?, 'global') "
                    "ORDER BY CASE WHEN scope = ? THEN 0 ELSE 1 END, id",
                    (name, scope, scope)).fetchone()
                if erow:
                    hits.append(erow)
                else:
                    refused.append(f"[{mid}] no entity named '{name}'")
            if hits:
                md.seed_edges(cursor, mid, [h[0] for h in hits], now)
                linked += 1
                log_rows.append((row[0], mid, ", ".join(h[1] for h in hits),
                                 [h[0] for h in hits]))
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
    for layer, mid, joined, eids in log_rows:
        _log_pass('linked', layer, mid, f"connected [{mid}] ↔ {joined}",
                  {'entity_ids': eids, 'at': now})
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
            row = cursor.execute('SELECT scope, meta, created FROM chunks WHERE id = ?',
                                 (mid,)).fetchone()
            if not row or row[0] != scope:
                refused.append(f"[{mid}] not found in scope")
                continue
            try:
                meta = json.loads(row[1]) if row[1] else {}
            except Exception:
                meta = {}
            before = {k: meta.get(k) for k in ('event_dates', 'event_date_src',
                                                'when', 'when_src', 'temporal_at')}
            # The plan (meta.when) follows the verdict: the earliest date
            # after the save day is what Upcoming shows; a verdict with
            # nothing ahead clears a regex plan — this is where "On Monday
            # we fixed the bug" gets corrected. Her own `when` outranks.
            if meta.get('when_src') != 'ai':
                from plugins.mindpalace.tools import temporal
                w = temporal.when_from(dates, row[2]) if dates else None
                if w:
                    meta['when'], meta['when_src'] = w, 'librarian'
                else:
                    meta.pop('when', None)
                    meta.pop('when_src', None)
            if dates:
                meta['event_dates'] = dates
                meta['event_date_src'] = 'librarian'
                dated += 1
            else:
                # A "no date" verdict files the entry but PRESERVES any
                # already-resolved dates (regex floor or a prior install's
                # librarian) — a lazy model's empty list must never strip
                # a real date off Upcoming. The verdict is about the text;
                # the resolver's data stands.
                dateless += 1
            meta['temporal_at'] = now
            after = {k: meta.get(k) for k in before}
            if dates:
                _log_pass('dated', None, mid,
                          f"dated [{mid}] → {', '.join(dates)}",
                          {'before': before, 'after': after})
            else:
                _log_pass('dateless', None, mid,
                          f"[{mid}] ruled dateless"
                          + (" (kept existing "
                             + ', '.join(meta.get('event_dates') or []) + ")"
                             if meta.get('event_dates') else ""),
                          {'before': before, 'after': after})
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
    with _pass_lock:
        refused = bool(_open_pass) and int(memory_id) in _open_pass['cap_refused']
    if refused:
        # The cap said no to retiring it; filing it as reviewed would hide
        # it from every later pass. It stays in the queue, unstamped.
        return (f"[{memory_id}] was refused by the retirement cap this pass — "
                f"leave it; it stays in the queue for a night with room."), False
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
    # every other verb (was invisible before; audit scout 2026-07-19), and
    # the before-values ride along so a run can be reverted (2026-10-30).
    changed = (rated is not None and rated != row[8]) or (favorite and not row[7])
    after = rated if rated is not None else row[8]
    if favorite:
        after = max(after or 0, 0.95)
    _log_pass('marked', row[1], memory_id,
              f"filed [{memory_id}] as reviewed{extra}",
              {'before': row[8], 'favorite_before': int(row[7] or 0),
               'after': after, 'favorite_after': int(bool(favorite or row[7]))}
              if changed else None)
    return f"Filed [{memory_id}] as reviewed{extra}.", True


def execute(function_name, arguments, config):
    try:
        if function_name != 'run_librarian':
            _note_attempt()
        if function_name == 'atomize_memory':
            return _atomize(arguments.get('memory_id'), arguments.get('parts'))
        if function_name == 'merge_memories':
            return _merge(arguments.get('memory_ids'),
                          content=arguments.get('content'),
                          keep=arguments.get('keep'))
        if function_name == 'keep_separate':
            return _keep_separate(arguments.get('memory_ids'))
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
