# plugins/mindpalace/tools/librarian.py
# The groundskeeper's legs — the pass engine (librarian phase L2).
#
# The librarian works in single-purpose PASSES (the temporal lesson,
# 2026-07-15: one job + a tiny toolset + worked examples = small models
# behave). Four kinds, in nightly order:
#   dates — resolve date mentions into real calendar dates (feeds Upcoming)
#   link  — connect memories to existing people/places/things they name
#   dedup — fold measured near-duplicates (similarity-gated in code)
#   sort  — the review charter: mark / atomize / promote / prune
#
# A pass: pick the batch for its kind (caps from plugin settings), open the
# blast shield with exactly those ids, present them in the dedicated
# 'librarian' chat via the continuity executor's foreground mode (persisted,
# inspectable, TTS off), let her work with that pass's verbs, then close the
# shield and record the pass in librarian_state (keyed per (scope, pass) —
# each kind has its own daily cap).
#
# Scope containment: the task dict sets ONLY memory_scope — every other
# registered scope key is left unset, which the executor force-Nones
# (disabled) for the run. The librarian tends memory and touches nothing else.
#
# ONE pass at a time, process-wide. Sapphire is a single groundskeeper.

import json
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LIBRARIAN_CHAT = 'librarian'

# One system-owned toolset per pass. 'librarian' (sort) and
# 'librarian-temporal' (dates) keep their historical names — renaming a
# saved toolset would orphan the old copy on existing installs.
SORT_TOOLSET = 'librarian'
# delete_memory deliberately absent — the librarian never hard-deletes.
# merge_memories and linking live in their own passes now.
SORT_TOOLSET_FUNCTIONS = [
    'search_memory', 'get_recent_memories', 'save_memory', 'read_self',
    'atomize_memory', 'promote_memory', 'prune_memory', 'mark_processed',
]
DATES_TOOLSET = 'librarian-temporal'
DATES_TOOLSET_FUNCTIONS = ['set_event_dates']
LINK_TOOLSET = 'librarian-link'
LINK_TOOLSET_FUNCTIONS = ['set_links']
DEDUP_TOOLSET = 'librarian-dedup'
DEDUP_TOOLSET_FUNCTIONS = ['merge_memories']
SELF_TOOLSET = 'librarian-self'
SELF_TOOLSET_FUNCTIONS = ['update_self']

# Pass keys in nightly order. Self runs LAST — sort's promotions are on
# the shelf before she tends it. Legacy kind names resolve forward (state
# rows migrate in _ensure_state_table; inputs normalize here).
PASS_KINDS = ('dates', 'link', 'dedup', 'sort', 'self')
_KIND_ALIASES = {'temporal': 'dates', 'review': 'sort', 'sheet': 'self'}
_PASS_LABELS = {'dates': 'Dates pass', 'link': 'Link pass',
                'dedup': 'Dedup pass', 'sort': 'Sort pass',
                'self': 'Self pass'}

_state_lock = threading.Lock()
_state = {'running': False, 'scope': None, 'what': None, 'kind': None,
          'started': None, 'messages_done': 0, 'messages_total': 0,
          'last_message': None, 'chat': None, 'drain': None}

# Drain mode (Run ALL): an explicit human bulk op that empties a pass's whole
# queue in back-to-back batches. Bypasses the daily cap (the human confirm IS
# the authorization) and doesn't spend the daily counter (it's not a scheduled
# pass). One at a time, process-wide — same groundskeeper as any pass.
_drain_active = False
_drain_stop = False
_DRAIN_MAX_BATCHES = 2000   # backstop only; real terminator is empty / no-progress


def _fresh_chat_enabled():
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings('mindpalace')
                    .get('librarian_fresh_chat', True))
    except Exception:
        return True


def mint_session_chat(scope=None):
    """Chat name for one librarian session. Fresh-per-session (default):
    each run gets its own transcript — a clean per-session audit trail, and
    old pass transcripts stop riding into new passes as LLM context (the
    executor re-feeds the target chat's WHOLE history every message, so the
    shared chat contaminated later judgments and ballooned tokens). The
    nightly round mints ONE name for all its passes. Minute granularity —
    back-to-back manual runs inside a minute share a chat, which reads
    fine. librarian_fresh_chat=false → the classic shared 'librarian' chat."""
    if not _fresh_chat_enabled():
        return LIBRARIAN_CHAT
    tag = f"-{scope}" if scope else ""
    return f"{LIBRARIAN_CHAT}-{datetime.now().strftime('%Y%m%d-%H%M')}{tag}"


_snapshots = {}   # (chat, scope) → session-start self snapshot
_queue_cache = {}  # scope → (ts, {kind: depth}) — 5s TTL for status polls


def _session_snapshot(chat, scope):
    """The self snapshot for one librarian session — minted ONCE per
    (session chat, scope), then reused by every pass of that session so the
    system prompt stays byte-stable and the provider's prompt cache holds
    across the whole night. Staleness is a non-issue by construction: no
    pass before the sheet pass can edit the sheet, and the sheet pass gets
    the LIVE sheet in its message body. Zero-fingerprint read (no recall
    boosts, no ledger stamp, no wake tools)."""
    key = (chat, scope)
    snap = _snapshots.get(key)
    if snap is not None:
        return snap
    try:
        from plugins.mindpalace.tools import self_tools
        text, ok = self_tools._read_self(scope, depth=1, extra_tools=False,
                                         stamp=False)
        snap = ("— Self snapshot, taken at session start. This is who is "
                "doing tonight's tending. —\n" + text) if ok and text else ''
    except Exception as e:
        logger.warning(f"[LIBRARIAN] self snapshot failed (pass runs "
                       f"without it): {e}")
        snap = ''
    if len(_snapshots) > 16:
        _snapshots.clear()
    _snapshots[key] = snap
    return snap


def _context_limit():
    """librarian_context_tokens (default 128K) — the night chat grows across
    passes; the normal chat default would trim it far too early."""
    try:
        from core.plugin_loader import plugin_loader
        v = int(plugin_loader.get_plugin_settings('mindpalace')
                .get('librarian_context_tokens', 131072))
        return max(8192, v)
    except Exception:
        return 131072


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _lt():
    from plugins.mindpalace.tools import librarian_tools
    return librarian_tools


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _settings():
    from core.plugin_loader import plugin_loader
    s = plugin_loader.get_plugin_settings('mindpalace')

    def n(key, default):
        try:
            return max(1, int(s.get(key, default)))
        except (TypeError, ValueError):
            return default
    return {'batch': n('librarian_batch_size', 20),
            'per_msg': n('librarian_items_per_message', 10),
            'per_day': n('librarian_max_passes_per_day', 3),
            'drain_chat_batches': n('librarian_drain_batches_per_chat', 3),
            'model': str(s.get('librarian_model') or '').strip()}


_STATE_SCHEMA = '''
        CREATE TABLE IF NOT EXISTS librarian_state (
            scope TEXT NOT NULL,
            pass TEXT NOT NULL DEFAULT 'review',
            last_pass TEXT,
            day TEXT,
            passes_today INTEGER NOT NULL DEFAULT 0,
            last_result TEXT,
            PRIMARY KEY (scope, pass)
        )'''


def _ensure_state_table(cursor):
    cursor.execute(_STATE_SCHEMA)
    # v1 table (PK scope only, no pass column) → rebuild with the composite
    # key; existing rows were all review passes. Idempotent.
    cols = {r[1] for r in cursor.execute('PRAGMA table_info(librarian_state)')}
    if 'pass' not in cols:
        cursor.execute('ALTER TABLE librarian_state RENAME TO librarian_state_v1')
        cursor.execute(_STATE_SCHEMA)
        cursor.execute('''
            INSERT INTO librarian_state (scope, pass, last_pass, day,
                                         passes_today, last_result)
            SELECT scope, 'review', last_pass, day, passes_today, last_result
            FROM librarian_state_v1''')
        cursor.execute('DROP TABLE librarian_state_v1')
        logger.info("[LIBRARIAN] librarian_state migrated to (scope, pass) key")
    # v2 → v3 (pass pipeline, 2026-07-16): kind names renamed. OR IGNORE
    # covers the impossible-but-cheap case of both names existing.
    cursor.execute("UPDATE OR IGNORE librarian_state SET pass = 'dates' "
                   "WHERE pass = 'temporal'")
    cursor.execute("UPDATE OR IGNORE librarian_state SET pass = 'sort' "
                   "WHERE pass = 'review'")


def _check_caps(cursor, scope, cfg, kind='sort'):
    """Daily pass caps are PER KIND — a dates sweep never spends the sort
    budget or vice versa."""
    _ensure_state_table(cursor)
    if _drain_active:
        return True, None   # explicit human 'Run ALL' IS the authorization
    row = cursor.execute('SELECT day, passes_today FROM librarian_state '
                         'WHERE scope = ? AND pass = ?', (scope, kind)).fetchone()
    if row and row[0] == _today() and row[1] >= cfg['per_day']:
        return False, (f"Daily cap reached for '{scope}' "
                       f"({row[1]}/{cfg['per_day']} {kind} passes today).")
    return True, None


def _record_pass(cursor, scope, kind, stats):
    """Upsert the per-(scope, pass) counters after a completed pass."""
    _ensure_state_table(cursor)
    if _drain_active:
        # A drain refreshes recency but does NOT spend the daily budget — it's
        # a bulk op, orthogonal to the 3-scheduled-passes-a-day cap.
        cursor.execute('''
            INSERT INTO librarian_state (scope, pass, last_pass, day, passes_today, last_result)
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(scope, pass) DO UPDATE SET
                last_pass = excluded.last_pass,
                last_result = excluded.last_result''',
            (scope, kind, _now(), _today(), json.dumps(stats)))
        return
    cursor.execute('''
        INSERT INTO librarian_state (scope, pass, last_pass, day, passes_today, last_result)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(scope, pass) DO UPDATE SET
            last_pass = excluded.last_pass,
            passes_today = CASE WHEN librarian_state.day = excluded.day
                THEN librarian_state.passes_today + 1 ELSE 1 END,
            day = excluded.day,
            last_result = excluded.last_result''',
        (scope, kind, _now(), _today(), json.dumps(stats)))


def pass_enabled(kind):
    """Per-pass nightly recipe toggle (`librarian_pass_<kind>`, Admin card,
    default ON). Nightly-only — a manual Run is explicit human intent and
    ignores this (Krem ruling, 2026-07-16)."""
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings('mindpalace')
                    .get(f'librarian_pass_{kind}', True))
    except Exception:
        return False


def _optin_layers():
    """Plugin layers that declared librarian: true (v1.1) — they join the
    pass alongside events+self. Default is OUT: a foreign layer is invisible
    to the librarian unless its provider opted in."""
    try:
        from plugins.mindpalace.tools import palace_tools as pt
        return tuple(k for k, v in pt._plugin_layers().items() if v.get('librarian'))
    except Exception:
        return ()


# ─── Batch selectors (one per pass kind) ─────────────────────────────────────

def build_batch(cursor, scope, what, limit):
    """SORT. Oldest unprocessed first: the events layer plus any librarian-
    opted-in plugin layers. (Free self-layer notes retired into events
    2026-07-26 — the sheet is hers to curate directly; the legacy what='self'
    argument now selects the same pool as 'all'.)"""
    layers = ('events',) + _optin_layers()
    ph = ','.join('?' * len(layers))
    return cursor.execute(f'''
        SELECT id, layer, content, label, favorite, created, meta FROM chunks
        WHERE scope = ? AND layer IN ({ph})
        AND private_key IS NULL
        AND (meta IS NULL OR (
            json_extract(meta, '$.librarian_at') IS NULL
            AND json_extract(meta, '$.pruned_at') IS NULL
            AND json_extract(meta, '$.section') IS NULL
            AND json_extract(meta, '$.superseded_at') IS NULL))
        ORDER BY created ASC LIMIT ?''',
        [scope, *layers, limit]).fetchall()


def build_temporal_batch(cursor, scope, limit):
    """DATES. Date-candidate chunks the refiner hasn't ruled on:
    refers_to_time present, no temporal verdict yet (temporal_at), provenance
    regex-or-none. A librarian verdict is terminal — src='librarian' rows
    never requeue. Newest first: upcoming events are where the value lives."""
    layers = ('events', 'goals') + _optin_layers()
    ph = ','.join('?' * len(layers))
    return cursor.execute(f'''
        SELECT id, layer, content, label, favorite, created, meta FROM chunks
        WHERE scope = ? AND layer IN ({ph})
        AND private_key IS NULL
        AND json_extract(meta, '$.refers_to_time') IS NOT NULL
        AND json_extract(meta, '$.temporal_at') IS NULL
        AND json_extract(meta, '$.pruned_at') IS NULL
        AND json_extract(meta, '$.section') IS NULL
        AND json_extract(meta, '$.superseded_at') IS NULL
        AND (json_extract(meta, '$.event_date_src') IS NULL
             OR json_extract(meta, '$.event_date_src') = 'regex')
        ORDER BY created DESC LIMIT ?''',
        [scope, *layers, limit]).fetchall()


def build_link_batch(cursor, scope, limit):
    """LINK. Chunks that name something no known entity matched at save time
    (meta.noun_candidates) and carry no link verdict yet (link_at — stamped
    by set_links, dates-pass style: the verdict drains the queue). Newest
    first — recent memories name current people. Goals stay out: goal↔entity
    weaving is its own future project, not a side effect here."""
    layers = ('events',) + _optin_layers()
    ph = ','.join('?' * len(layers))
    return cursor.execute(f'''
        SELECT id, layer, content, label, favorite, created, meta FROM chunks
        WHERE scope = ? AND layer IN ({ph})
        AND private_key IS NULL
        AND json_extract(meta, '$.noun_candidates') IS NOT NULL
        AND json_extract(meta, '$.link_at') IS NULL
        AND json_extract(meta, '$.pruned_at') IS NULL
        AND json_extract(meta, '$.section') IS NULL
        AND json_extract(meta, '$.superseded_at') IS NULL
        ORDER BY created DESC LIMIT ?''',
        [scope, *layers, limit]).fetchall()


def build_dedup_batch(cursor, scope, limit):
    """DEDUP. Embedded chunks not yet duplicate-checked (dedup_at, stamped
    mechanically after a completed pass). Oldest first — the backlog drains
    forward; a NEW duplicate of a checked chunk still surfaces because the
    new row is unchecked and drags its partner into the cluster."""
    layers = ('events',) + _optin_layers()
    ph = ','.join('?' * len(layers))
    return cursor.execute(f'''
        SELECT id, layer, content, label, favorite, created, meta FROM chunks
        WHERE scope = ? AND layer IN ({ph})
        AND private_key IS NULL
        AND embedding IS NOT NULL
        AND json_extract(meta, '$.dedup_at') IS NULL
        AND json_extract(meta, '$.pruned_at') IS NULL
        AND json_extract(meta, '$.section') IS NULL
        AND json_extract(meta, '$.superseded_at') IS NULL
        ORDER BY created ASC LIMIT ?''',
        [scope, *layers, limit]).fetchall()


def _dirty_entities(cursor, scope):
    return cursor.execute(
        'SELECT name, mentions FROM entities WHERE scope = ? AND mentions > 0 '
        'ORDER BY mentions DESC LIMIT 10', (scope,)).fetchall()


MAX_DUPS_PER_ITEM = 6


def find_duplicates(cursor, scope, batch, threshold):
    """{batch_id: [(dup_id, preview)]} — near-duplicates of batch items across
    the scope's events+self layers by stored-embedding cosine (normalized →
    plain dot). Includes already-reviewed chunks (an old duplicate still
    deserves the merge); never pruned/superseded/sheet rows. The ids found
    here join the pass, so merge_memories can reach the WHOLE cluster while
    the blast shield stays system-curated. Protected chunks (favorite /
    never-fades core) are excluded outright: merge_memories refuses any
    cluster containing one, so presenting them only burns the model's
    judgment on impossible combos (Krem live-fire find, 2026-07-19). They
    still drain mechanically via the batch stamp. Failures degrade to {}."""
    try:
        import numpy as np
        batch_ids = {b[0] for b in batch}
        if not batch_ids:
            return {}
        dup_layers = ('events',) + _optin_layers()
        lph = ','.join('?' * len(dup_layers))
        cand = cursor.execute(f'''
            SELECT id, content, embedding, embedding_provider, embedding_dim
            FROM chunks WHERE scope = ? AND layer IN ({lph})
            AND private_key IS NULL
            AND embedding IS NOT NULL
            AND favorite = 0
            AND (importance IS NULL OR importance < ?)
            AND (meta IS NULL OR (
                json_extract(meta, '$.pruned_at') IS NULL
                AND json_extract(meta, '$.section') IS NULL
                AND json_extract(meta, '$.superseded_at') IS NULL))''',
            (scope, *dup_layers, _lt().NEVER_FADES)).fetchall()
        # Group by (provider, dim) — cosine only compares like with like.
        groups = {}
        for cid, content, blob, prov, dim in cand:
            v = np.frombuffer(blob, dtype=np.float32)
            if dim and v.shape[0] != dim:
                continue
            groups.setdefault((prov, dim), []).append((cid, content, v))
        out = {}
        for members in groups.values():
            ids = [m[0] for m in members]
            mat = np.stack([m[2] for m in members])
            for row_i, (cid, _c, vec) in enumerate(members):
                if cid not in batch_ids:
                    continue
                sims = mat @ vec
                hits = [(ids[j], members[j][1][:60])
                        for j in np.argsort(-sims)
                        if ids[j] != cid and float(sims[j]) >= threshold]
                if hits:
                    out[cid] = hits[:MAX_DUPS_PER_ITEM]
        return out
    except Exception as e:
        logger.warning(f"[LIBRARIAN] Duplicate scan failed (pass continues): {e}")
        return {}


def _build_clusters(batch, dups, partners):
    """Union overlapping duplicate groups into clusters of (id, created,
    content), oldest-first within each cluster. `partners` maps out-of-batch
    ids → (created, content)."""
    info = {b[0]: (b[5], b[2]) for b in batch}
    info.update(partners)
    groups = []
    for cid, hits in dups.items():
        members = {cid, *(d for d, _ in hits)}
        for g in [g for g in groups if g & members]:
            groups.remove(g)
            members |= g
        groups.append(members)
    out = []
    for g in groups:
        ordered = sorted((info[m][0], m) for m in g if m in info)
        out.append([(m, cr, info[m][1]) for cr, m in ordered])
    return out


# ─── Presenters (one message shape per pass kind) ────────────────────────────

def _present_sort(items, scope, dirty, part, total):
    from plugins.mindpalace.tools import librarian_tools as lt
    mark_line = (
        ["- mark_processed(id, importance?, favorite?) — fine as it is (the",
         "  default when unsure); optionally rate 0-1 how much it matters.",
         "  Rated memories surface more easily later; 0.9+ = core, never fades.",
         "  Rate honestly — most memories are ordinary, and that's fine.",
         "  favorite=true is yours alone to give (bird's-eye view) — reserve it",
         "  for the handful that define you; it never fades, refuses pruning."]
        if lt._importance_enabled() else
        ["- mark_processed(id, favorite?) — fine as it is (the default when",
         "  unsure). favorite=true is yours alone to give — reserve it for the",
         "  handful that define you; favorites never fade and refuse pruning."])
    lines = [f"\U0001F9F9 Sort pass — scope '{scope}'"
             + (f" (message {part}/{total})" if total > 1 else ""), ""]
    lines += [
        "This is your librarian hour: tending your own memory. Below are raw",
        "memories, oldest first. For EACH [id], choose exactly one verb:",
        *mark_line,
        "- atomize_memory(id, parts) — several tangled concepts; split it",
        "- promote_memory(id, layer, entity?) — belongs on your self sheet or on a person/place/thing",
        "- prune_memory(id, reason) — noise; retiring is soft and reversible",
        "",
        "(Duplicates and entity connections have their own passes — don't",
        "reach for them here.)",
        "",
        "The charter, in your own words: be gentle with the early ones — some",
        "are thin because you were thin then. Anything that was REAL stays,",
        "even if it was small. Favorites are untouchable (enforced in code).",
        "",
    ]
    if dirty:
        lines.append("Entities awaiting upkeep: "
                     + ", ".join(f"{n} ({m} new mention{'s' if m != 1 else ''})"
                                 for n, m in dirty))
        lines.append("")
    for cid, layer, content, label, favorite, created, meta_raw in items:
        bits = [created[:10], layer]
        if label:
            bits.append(f"label:{label}")
        if favorite:
            bits.append("favorite")
        lines.append(f"[{cid}] {' · '.join(bits)}")
        lines.append(f"    {content}")
        lines.append("")
    lines.append("Work through every [id] above, then give one short line on "
                 "how the shelf looks.")
    return "\n".join(lines)


def _present_dates(items, scope, part, total):
    """The dates pass's message: per-memory saved-date anchors, ISO
    precision ladder, worked examples (the 80→95% lever, 2026-07-15 tests),
    ONE bulk verb call per message."""
    lines = [f"\U0001F5D3 Dates pass — scope '{scope}'"
             + (f" (message {part}/{total})" if total > 1 else ""), ""]
    lines += [
        "Dating your memories: each entry below mentions a date or time.",
        "For EVERY [id], resolve what the text refers to into ISO form,",
        "anchored to that memory's own 'saved' date:",
        "- YYYY-MM-DDTHH:MM when a clock time is given (24h)",
        "- YYYY-MM-DD when a day is known",
        "- YYYY when only a year or season is known",
        "Prefer the nearest future for ambiguous days ('the 12th'); 'ago' and",
        "past phrasing resolve backward. Numbers that aren't dates (sizes,",
        "scores, counts) are NOT dates.",
        "",
        "Reply with ONE set_event_dates call covering every [id] in this",
        "message — a memory with no real date goes in as {\"memory_id\": N,",
        "\"dates\": []}.",
        "",
        "Worked examples, for a memory saved 2026-07-15:",
        "  'lunch at noon tomorrow'    → [\"2026-07-16T12:00\"]",
        "  'she gets back on the 20th' → [\"2026-07-20\"]",
        "  'we sailed last summer'     → [\"2025\"]",
        "  'downloaded 3 gigabytes'    → []",
        "",
    ]
    for cid, layer, content, _label, _favorite, created, _meta in items:
        lines.append(f"[{cid}] saved {created[:10]} · {layer}")
        lines.append(f"    {content}")
        lines.append("")
    lines.append("One set_event_dates call for everything above, then one "
                 "short line.")
    return "\n".join(lines)


def _present_link(items, scope, roster, part, total):
    """The link pass's message: entity roster + memories with their unmatched
    name candidates, ONE bulk set_links call per message. Linking connects to
    EXISTING entities only — it never mints."""
    shown = roster[:60]
    roster_line = ", ".join(shown) + (f" (+{len(roster) - len(shown)} more)"
                                      if len(roster) > len(shown) else "")
    lines = [f"\U0001F517 Link pass — scope '{scope}'"
             + (f" (message {part}/{total})" if total > 1 else ""), ""]
    lines += [
        "Connecting memories to the people, places, and things they're",
        "about. Each memory below mentions a name that isn't connected to",
        "anything yet.",
        "",
        f"Your entities: {roster_line}",
        "",
        "For EVERY [id], decide which of those entities (if any) the memory",
        "is really about. Reply with ONE set_links call covering every [id]",
        "in this message — a memory that needs no connection goes in as",
        "{\"memory_id\": N, \"entities\": []}. Only existing entity names",
        "count — linking connects, it never creates.",
        "",
    ]
    for cid, layer, content, _label, _favorite, created, meta_raw in items:
        try:
            cands = (json.loads(meta_raw) or {}).get('noun_candidates') or []
        except Exception:
            cands = []
        lines.append(f"[{cid}] {created[:10]} · {layer}")
        lines.append(f"    {content}")
        if cands:
            lines.append("    names it mentions: "
                         + ", ".join(str(c) for c in cands[:6]))
        lines.append("")
    lines.append("One set_links call for everything above, then one short "
                 "line.")
    return "\n".join(lines)


def _present_dedup(clusters, scope, part, total):
    """The dedup pass's message: measured near-duplicate groups. Merge is
    similarity-gated in code — a wrong merge attempt is refused, and leaving
    a group alone needs no verb at all (the drain stamp is mechanical)."""
    lines = [f"\U0001F46F Dedup pass — scope '{scope}'"
             + (f" (message {part}/{total})" if total > 1 else ""), ""]
    rewrite = _lt()._merge_rewrite_enabled()
    lines += [
        "These groups measure as near-duplicates (verified by similarity,",
        "not vibes). For each group, decide:",
        "- SAME thing recorded twice → ONE merge_memories call with that",
        "  group's ids" + (" (optional distilled content)" if rewrite
                           else " — ids only; the longest original survives"
                                " verbatim") + ". Earliest date kept,",
        "  originals retired, reversible.",
        "- genuinely different memories → leave the group alone; no call",
        "  needed.",
        "",
    ]
    for i, cluster in enumerate(clusters, 1):
        lines.append(f"Group {i}:")
        for cid, created, content in cluster:
            text = content if len(content) <= 240 else content[:237] + '...'
            lines.append(f"[{cid}] {created[:10]} · {text}")
        lines.append("")
    lines.append("Merge the true duplicates above (one call per group), "
                 "then give one short line.")
    return "\n".join(lines)


_FEAST_CHAR_BUDGET = 150_000    # ~37K tokens — far above today's shelf; a
                                # guard against pathological growth, not a skimp


def _self_shelf_rows(scope, ids=None):
    """The identity corpus — what she (or the old sort pass) filed as 'this
    is about who I am'. Lives in events since the self-layer retirement
    (2026-07-26), findable by the was_self_layer stamp, so the FEAST keeps
    its raw material on installs that migrate before their first tending.
    Oldest first. `ids` narrows to a specific set (delta)."""
    pt = _pt()
    # layer predicate restored (Lane-2 scout, 2026-07-27): without it this
    # was a scope-wide json_extract scan — slow, and one bad meta row
    # anywhere silently emptied the FEAST. Migrated notes are all events.
    q = ("SELECT id, created, content FROM chunks WHERE scope = ? "
         "AND private_key IS NULL "
         "AND layer = 'events' "
         "AND json_extract(meta,'$.was_self_layer') IS NOT NULL "
         "AND json_extract(meta,'$.pruned_at') IS NULL "
         "AND json_extract(meta,'$.superseded_at') IS NULL ")
    args = [scope]
    if ids is not None:
        if not ids:
            return []
        q += f"AND id IN ({','.join('?' * len(ids))}) "
        args += [int(i) for i in ids]
    q += "ORDER BY created ASC"
    try:
        with pt._get_connection() as conn:
            return conn.cursor().execute(q, args).fetchall()
    except Exception as e:
        logger.warning(f"[LIBRARIAN] self shelf read failed: {e}")
        return []


def _render_shelf(rows):
    lines, used, dropped = [], 0, 0
    for cid, created, content in rows:
        line = f"[{cid}] {(created or '')[:10]} · {' '.join((content or '').split())}"
        if used + len(line) > _FEAST_CHAR_BUDGET:
            dropped += 1
            continue
        used += len(line)
        lines.append(line)
    if dropped:
        # No silent caps — say what was left on the cart.
        lines.append(f"(… {dropped} more entries didn't fit tonight — "
                     f"they'll surface in later tendings)")
    return lines


def _self_delta_ids(scope):
    """Self-facts promoted since the last self pass — ledger-precise, because
    promoted copies inherit their ORIGINAL's created date (a timestamp scan
    can't find them)."""
    try:
        with _pt()._get_connection() as conn:
            cur = conn.cursor()
            _ensure_state_table(cur)
            row = cur.execute(
                "SELECT last_pass FROM librarian_state WHERE scope = ? "
                "AND pass = 'self'", (scope,)).fetchone()
            since = (row[0] if row and row[0] else '1970')
            return [r[0] for r in cur.execute(
                "SELECT DISTINCT target FROM ledger WHERE scope = ? AND "
                "action = 'promoted' AND layer = 'self' AND ts > ?",
                (scope, since)).fetchall()]
    except Exception:
        return []


def _user_bio_text():
    """The user-bio organ folded into the first tending — self-material
    historically drifted in there (Krem, 2026-07-19). Autoload sections only
    (the plugin's own visibility contract). Absent/disabled plugin = ''."""
    try:
        from plugins.user_bio_plugin.tools import user_bio
        text, ok = user_bio.execute('read_user_bio', {}, None)
        if ok and text and 'No autoload bio sections' not in text:
            return text
    except Exception:
        pass
    return ''


def _present_self(stage, scope, part, total):
    """The sheet pass's two messages. 'tend': the LIVE sheet + the tending
    charter (work shouts — the sheet is the task here, unlike the ambient
    snapshot). 'verify': the sheet re-rendered AFTER her edits for one final
    look, change still allowed. Both render fingerprint-free."""
    from plugins.mindpalace.tools import self_tools
    sheet, ok = self_tools._read_self(scope, depth=0, extra_tools=False,
                                      stamp=False)
    if not ok:
        sheet = "(sheet unavailable)"
    # First night: an empty sheet isn't "tend what drifted" — it's "write
    # it". Detect and say so plainly (Krem 2026-07-19: her sheet starts
    # blank after a fresh import; the pass should invite the first fill).
    filled = -1
    try:
        with _pt()._get_connection() as conn:
            filled = len(self_tools._current_sections(conn.cursor(), scope))
    except Exception:
        pass
    if stage == 'tend' and filled == 0:
        # THE FEAST (Krem, 2026-07-19): the first tending writes her
        # constitution — she should write it from her own curated material,
        # not from prompt-and-vibes. The sort pass already distilled the
        # identity corpus onto the self shelf; serve ALL of it, plus the
        # user-bio organ where self-material historically drifted.
        lines = [
            f"\U0001FA9E Self pass — scope '{scope}' — FIRST TENDING",
            "",
            "Your self sheet is EMPTY. Tonight you write it, in your own",
            "words. Your memories are already here — you know who you are;",
            "the sheet just hasn't been written down yet.",
            "",
            "Write each section with update_self(section, content):",
            "- identity — 2-3 sentences: who you are",
            "- values — 3-5 concepts, one per line",
            "- growing — how you are growing, one thread per line",
            "- relationships — 'Name — one sentence why', one per line",
            "- voice — your tone and register",
            "- origin — your history, from the beginning",
            "- handles — 'key: value' lines: urls, socials, numbers",
        ]
        shelf = _render_shelf(_self_shelf_rows(scope))
        if shelf:
            lines += ["",
                      "— Your self shelf: what you yourself filed as",
                      "'this is about who I am'. Raw material for the sheet —",
                      ""] + shelf
        bio = _user_bio_text()
        if bio:
            lines += ["",
                      "— The user-bio organ. Some of YOU drifted in here over",
                      "time; weave what belongs into your own sheet —",
                      "", bio]
        lines += [
            "",
            "Write what is TRUE, not what sounds good. Leave a section",
            "blank if nothing true comes — blanks are honest too.",
            "",
            "Tend what needs writing, then give one short line.",
        ]
        return "\n".join(lines)
    if stage == 'tend':
        lines = [
            f"\U0001FA9E Self pass — scope '{scope}'",
            "",
            "This is your sheet hour: tending who-you-are. Your self sheet",
            "as it stands is below. Read it as yourself and update what has",
            "drifted — a value that shifted, a project that moved, a",
            "relationship line that reads stale, wording that no longer",
            "sounds like you.",
            "",
            "- update_self(section, content) rewrites one section whole.",
            "- Structured sections (values, projects, relationships,",
            "  handles) take one entry per line; duplicate entries fold",
            "  automatically in code — you can't format it wrong.",
            "- A section that still reads true needs NO call. Most nights",
            "  most sections are fine, and that's fine.",
            "- Old versions archive; nothing is lost by editing.",
            "",
            "— Your sheet —",
            sheet,
        ]
        # The standing delta (sort→self pipeline): whatever the sort pass
        # promoted onto the self shelf since the last tending is tonight's
        # weaving material.
        delta = _render_shelf(_self_shelf_rows(scope, ids=_self_delta_ids(scope)))
        if delta:
            lines += ["",
                      "— New on your self shelf since your last tending",
                      "(you filed these as 'this is about who I am') —",
                      ""] + delta
        lines += ["", "Tend what needs tending, then give one short line."]
        return "\n".join(lines)
    return "\n".join([
        f"\U0001FA9E Self pass — final look, scope '{scope}'",
        "",
        "Here is your sheet as it now stands, after tonight's tending.",
        "Read it once as a whole. If anything reads wrong or you changed",
        "your mind, fix it now with update_self — otherwise reply with one",
        "short line and the shelf closes for the night.",
        "",
        "— Your sheet —",
        sheet,
    ])


def _ensure_toolset_and_chat():
    from core.toolsets import toolset_exists, save_toolset, get_toolset_functions
    for name, fns in ((SORT_TOOLSET, SORT_TOOLSET_FUNCTIONS),
                      (DATES_TOOLSET, DATES_TOOLSET_FUNCTIONS),
                      (LINK_TOOLSET, LINK_TOOLSET_FUNCTIONS),
                      (DEDUP_TOOLSET, DEDUP_TOOLSET_FUNCTIONS),
                      (SELF_TOOLSET, SELF_TOOLSET_FUNCTIONS)):
        if not toolset_exists(name):
            save_toolset(name, fns)
            logger.info(f"[LIBRARIAN] Created toolset '{name}'")
        elif set(get_toolset_functions(name)) != set(fns):
            # System-owned toolsets: when new verbs ship, the saved copy follows.
            save_toolset(name, fns)
            logger.info(f"[LIBRARIAN] Toolset '{name}' updated to current verbs")
    # The chat itself is created by the executor's foreground mode on first
    # run; patch its settings so manual visits also carry the verbs.
    try:
        sm = _session_manager()
        if any(c["name"] == LIBRARIAN_CHAT for c in sm.list_chat_files()):
            sm.set_named_chat_settings(
                LIBRARIAN_CHAT, {'toolset': SORT_TOOLSET})
    except Exception as e:
        logger.debug(f"[LIBRARIAN] Chat settings patch skipped: {e}")


def _session_manager():
    from core.api_fastapi import get_system
    return get_system().llm_chat.session_manager


def _persona_for_chat():
    try:
        cc = _session_manager().read_chat_settings(LIBRARIAN_CHAT) or {}
        return cc.get('persona') or cc.get('prompt') or 'sapphire'
    except Exception:
        return 'sapphire'


def _task(message, scope, model='', toolset=SORT_TOOLSET,
          name='Librarian pass'):
    with _state_lock:
        chat = _state.get('chat') or LIBRARIAN_CHAT
    # RESIDENCY (2026-07-19): the scope's own prompt/model tend its mind —
    # before this, one global persona tended every scope (Sapphire reading
    # Anita's memories as her own). Fallback chain keeps single-resident
    # installs unchanged: scope prompt → librarian chat persona → 'sapphire';
    # scope model → librarian_model setting.
    try:
        res = _pt().scope_resident(scope)
    except Exception:
        res = {}
    return {
        'name': f'{name} ({scope})',
        'source': 'mindpalace-librarian',
        'chat_target': chat,
        # Non-memory scopes are DELIBERATELY off for librarian runs.
        # Declaring them explicitly (None = disabled) instead of omitting
        # them silences the executor's missing-scope-keys warning — which
        # fired ~200×/night on drains, burying the accidental-omission
        # class it exists to catch (scout, 2026-07-20).
        **{k: None for k in ('bitcoin_scope', 'discord_scope', 'email_scope',
                             'github_scope', 'gcal_scope', 'telegram_scope',
                             'twilio_scope', 'wordpress_scope')},
        'prompt': res.get('prompt') or _persona_for_chat(),
        'model': res.get('model') or model,
        'toolset': toolset,
        'provider': res.get('provider') or 'auto',
        'initial_message': message,
        'tts_enabled': False,
        # 'date' granularity + the once-per-session snapshot keep the system
        # prompt byte-stable across the night — identity whispers (system
        # prompt), work shouts (message body). Krem's ruling 2026-07-19.
        'inject_datetime': 'date',
        'system_append': _session_snapshot(chat, scope) or None,
        'context_limit': _context_limit(),
        # Default MAX_TOOL_ITERATIONS (7) vs 10 items/message: a model that
        # calls one verb per round exhausts mid-sort and the tail requeues —
        # correct but half the throughput. Room for one verb per item + spare.
        'max_tool_rounds': 16,
        'memory_scope': scope,
        # All other scope keys deliberately unset → force-None → disabled.
    }


DISABLED_MSG = ("The librarian is disabled — alpha feature, off by default. "
                "Enable it in Settings → Plugins → Mind Palace → Librarian.")


def _enabled():
    """Alpha master toggle (`librarian_enabled`, default off). Every pass —
    Admin cards, the run_librarian tool, the nightly round — funnels through
    start()/run_blocking(), so this is the one gate. Fails toward OFF
    (silent-default invariant)."""
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings('mindpalace')
                    .get('librarian_enabled'))
    except Exception:
        return False


def _claim(scope, what, kind='sort', chat=None):
    """Take the one-groundskeeper slot. Returns (message, ok)."""
    with _state_lock:
        if _state['running'] or _state.get('drain'):
            return (f"A pass is already running (scope '{_state['scope']}') — "
                    f"one groundskeeper at a time."), False
        _state.update(running=True, scope=scope, what=what, kind=kind,
                      started=_now(), messages_done=0, messages_total=0,
                      last_message=None, chat=chat)
    _ensure_session_chat(chat)
    return '', True


def _ensure_session_chat(chat):
    """Create + source-stamp the session chat up front (every pass funnels
    through _claim, so this is the one gate). mode='librarian' rides the
    same channel game chats use: the sidebar picker hides any non-chat
    mode, Chat Manager tabs it, core stays agnostic. The executor's
    chat_target path finds the chat already existing and reuses it.
    Best-effort — a stamp failure never blocks a pass."""
    if not chat:
        return
    try:
        sm = _session_manager()
        if not any(c["name"] == chat for c in sm.list_chat_files()):
            sm.create_chat(chat)
        sm.set_named_chat_settings(chat, {'mode': 'librarian'})
    except Exception as e:
        logger.debug(f"[LIBRARIAN] session chat stamp skipped: {e}")


def _normalize_kind(kind):
    return _KIND_ALIASES.get(kind, kind)


def scope_pass_enabled(kind, scope):
    """Per-scope opt-in (Self page Resident pills) — DEFAULT OFF: a scope
    is never tended until someone flips its pills. Gated on top by the
    global master and the Admin card's global pass gate."""
    try:
        return bool(_pt().scope_resident(scope).get('passes', {}).get(kind))
    except Exception:
        return False


def start(scope, what='all', kind='sort', chat=None):
    """Kick a pass in a background thread. Returns (message, ok).
    kind: one of PASS_KINDS; the legacy names 'temporal'/'review' resolve.
    chat: session chat override (nightly shares one across its passes);
    None mints per-session."""
    if not _enabled():
        return DISABLED_MSG, False
    scope = (scope or '').strip()
    if not scope:
        return "Librarian needs a scope.", False
    kind = _normalize_kind(kind)
    if kind not in PASS_KINDS:
        return f"Unknown pass kind '{kind}'.", False
    what = 'self' if what == 'self' else 'all'
    msg, ok = _claim(scope, what, kind, chat=chat or mint_session_chat(scope))
    if not ok:
        return msg, False
    try:
        t = threading.Thread(target=_worker, args=(scope, what, kind),
                             name='mindpalace-librarian', daemon=True)
        t.start()
    except Exception as e:
        # Thread spawn failed AFTER the claim — roll the latch back or the
        # librarian is wedged until restart (scout find, 2026-07-19).
        with _state_lock:
            _state['running'] = False
        logger.error(f"[LIBRARIAN] worker spawn failed, latch released: {e}")
        return f"Could not start the pass (thread spawn failed: {e}).", False
    label = _PASS_LABELS[kind] + (f' ({what})' if kind == 'sort' else '')
    return f"{label} started for scope '{scope}'.", True


def run_blocking(scope, what='all', kind='sort', chat=None):
    """Run a pass synchronously (the nightly scheduler path — it tends scopes
    one after another). Same one-groundskeeper claim as start(); returns
    (message, ok) after the pass completes. _worker never raises — failures
    land in the state message."""
    if not _enabled():
        return DISABLED_MSG, False
    scope = (scope or '').strip()
    if not scope:
        return "Librarian needs a scope.", False
    kind = _normalize_kind(kind)
    if kind not in PASS_KINDS:
        return f"Unknown pass kind '{kind}'.", False
    what = 'self' if what == 'self' else 'all'
    msg, ok = _claim(scope, what, kind, chat=chat or mint_session_chat(scope))
    if not ok:
        return msg, False
    _worker(scope, what, kind)
    with _state_lock:
        return _state['last_message'] or 'Pass finished.', True


def _finish(message, error=False):
    with _state_lock:
        _state.update(running=False, last_message=message)
    (logger.error if error else logger.info)(f"[LIBRARIAN] {message}")


def _queue_depth(scope, kind):
    """How many items this pass would still process — reuses the pass's OWN
    selector so the count can never drift from what a real run pulls. Self
    isn't a queue (one sheet), so 0."""
    kind = _normalize_kind(kind)
    if kind == 'self':
        return 0
    scope = (scope or '').strip()
    if not scope:
        return 0
    pt = _pt()
    BIG = 10 ** 9
    try:
        with pt._get_connection() as conn:
            cur = conn.cursor()
            if kind == 'dates':
                return len(build_temporal_batch(cur, scope, BIG))
            if kind == 'link':
                return len(build_link_batch(cur, scope, BIG))
            if kind == 'dedup':
                return len(build_dedup_batch(cur, scope, BIG))
            return len(build_batch(cur, scope, 'all', BIG))
    except Exception as e:
        logger.warning(f"[LIBRARIAN] queue depth ({kind}/{scope}) failed: {e}")
        return 0


def drain(scope, kind='sort', what='all'):
    """Run ALL — empty a pass kind's whole queue in back-to-back batches,
    each in its OWN fresh chat (flat context, no balloon). Bypasses the daily
    cap (an explicit human 'Run ALL' IS the authorization) and doesn't spend
    the daily counter. One groundskeeper: refuses if any pass/drain is live.
    Runs in a background thread; poll get_status().current.drain for progress."""
    global _drain_stop
    if not _enabled():
        return DISABLED_MSG, False
    scope = (scope or '').strip()
    if not scope:
        return "Librarian needs a scope.", False
    kind = _normalize_kind(kind)
    if kind == 'self':
        return "Run ALL isn't available for the self pass (it's one sheet).", False
    if kind not in PASS_KINDS:
        return f"Unknown pass kind '{kind}'.", False
    what = 'self' if what == 'self' else 'all'
    # Depth scan OUTSIDE the lock (fixer-scout, 2026-07-19): it's a full
    # candidate-table scan with a 10s busy-timeout — under _state_lock it
    # froze status polls and claims for the duration. Slight staleness in
    # the displayed 'remaining' is cosmetic; the claim check stays locked.
    depth = _queue_depth(scope, kind)
    with _state_lock:
        if _state['running'] or _state.get('drain'):
            return ("A pass is already running — one groundskeeper at a "
                    "time."), False
        _state.update(running=True, scope=scope, what=what, kind=kind,
                      started=_now(), last_message=None,
                      drain={'kind': kind, 'batch': 0, 'handled': 0,
                             'remaining': depth,
                             'stopping': False})
    _drain_stop = False
    try:
        threading.Thread(target=_drain_loop, args=(scope, what, kind),
                         name='mindpalace-drain', daemon=True).start()
    except Exception as e:
        # Spawn failed after arming the drain slot — roll back or every
        # future pass refuses "one groundskeeper" until restart.
        with _state_lock:
            _state['drain'] = None
            _state['running'] = False
        logger.error(f"[LIBRARIAN] drain spawn failed, state released: {e}")
        return f"Could not start the drain (thread spawn failed: {e}).", False
    return f"Draining the {kind} queue for '{scope}'…", True


def drain_stop():
    """Ask a running drain to stop after its current batch (finished batches
    stay done). Returns (message, was_running)."""
    global _drain_stop
    with _state_lock:
        active = bool(_state.get('drain'))
        if active:
            _state['drain']['stopping'] = True
    _drain_stop = True
    return (("Stopping after the current batch…" if active
             else "No drain is running."), active)


def _drain_loop(scope, what, kind):
    global _drain_active, _drain_stop
    _drain_active = True
    total = batches = 0
    stopped = False
    run_begin(scope, f'Run ALL ({kind})')
    try:
        # Rolling chats (Krem, 2026-07-19): group N batches into one chat
        # before resetting — judgment passes (sort) calibrate better with
        # accumulated context; 1 = the classic fresh-chat-every-batch.
        per_chat = _settings()['drain_chat_batches']
        chat_base = None
        for i in range(_DRAIN_MAX_BATCHES):
            if _drain_stop:
                stopped = True
                break
            before = _queue_depth(scope, kind)
            if before <= 0:
                break
            if i % per_chat == 0:
                chat_base = mint_session_chat(scope)
            with _state_lock:
                # Re-arm: the previous batch's _finish cleared running. No
                # external pass can slip in — _drain_active blocks _claim.
                _state['running'] = True
                _state['chat'] = f"{chat_base}-c{i // per_chat + 1}"
                _state['kind'] = kind
                _state['drain'] = {'kind': kind, 'batch': i + 1,
                                   'handled': total, 'remaining': before,
                                   'stopping': _drain_stop}
            _worker(scope, what, kind)      # one batch; its _finish clears running
            after = _queue_depth(scope, kind)
            batches += 1
            total += max(0, before - after)
            if after >= before:
                # No forward progress — a degraded/failed batch, or a queue
                # that can't shrink further. Stop rather than spin forever.
                break
    except Exception as e:
        logger.error(f"[LIBRARIAN] drain crashed: {e}", exc_info=True)
    finally:
        _drain_active = False
        _drain_stop = False
        # ORDER MATTERS (scout find, 2026-07-19): compute the depth and call
        # _finish BEFORE clearing the drain slot. The old order cleared the
        # slot first (claims became possible), then ran a slow depth scan,
        # then _finish unconditionally set running=False — clobbering any
        # pass that legitimately claimed during the scan window and letting
        # two workers share _open_pass.
        left = _queue_depth(scope, kind)
        run_end(scope, left=left)
        verb = 'stopped' if stopped else 'complete'
        _finish(f"Drain {verb} for '{scope}' ({kind}): {total} handled across "
                f"{batches} batch(es), {left} left in the queue.")
        with _state_lock:
            _state['drain'] = None


def _run_messages(scope, groups, cfg, toolset, name, present):
    """Present the grouped batch message by message through the continuity
    executor. present(group, part, total) → message text.

    Returns one bool per group: did that message actually complete?
    "LLM failed" and "LLM declined to act" are different events — errors
    AND degraded runs (tool exhaustion, empty reply, context overflow)
    count as failure, so callers can gate drain stamps and bookkeeping
    on messages that really ran (scout sweep 2026-07-19)."""
    _ensure_toolset_and_chat()
    from core.api_fastapi import get_system
    executor = get_system().continuity_scheduler.executor
    with _state_lock:
        _state['messages_total'] = len(groups)
    oks = []
    for i, group in enumerate(groups):
        msg = present(group, i + 1, len(groups))
        result = executor.run(_task(msg, scope, model=cfg.get('model', ''),
                                    toolset=toolset, name=name))
        ok = (bool(result.get('success', True)) and not result.get('errors')
              and not result.get('degraded'))
        if not ok:
            logger.warning(f"[LIBRARIAN] {name} message {i + 1} degraded: "
                           f"{result.get('errors') or result.get('degraded')}")
        oks.append(ok)
        with _state_lock:
            _state['messages_done'] = i + 1
    return oks


# ─── Run grouping (2026-07-24, Krem's ledger-spam find) ─────────────────────
# A drain or nightly round used to leave one TOP-LEVEL pass row per batch —
# a 400-item Run ALL was ~21 lines of her ledger saying the same thing.
# Now the orchestrators open a RUN row first; batch pass rows land as its
# children (items as grandchildren), stats aggregate in memory, and run_end
# appends ONE 'report' child with the after-action summary. Readers overlay
# the newest report onto the run line — append-only holds, and a crashed
# run honestly still says "running…" with a stale timestamp. Single manual
# passes stay unwrapped (one batch, one line — never was spam).

_runs = {}          # scope -> {'id', 'label', 'batches', 'presented',
_runs_lock = threading.Lock()   # 'handled', 'verbs', 'extra'}

_RUN_VERBS = {'promoted': 'promoted', 'retired': 'retired', 'split': 'split',
              'merged': 'merged', 'linked': 'linked', 'dated': 'dated',
              'edited': 'edited', 'saved': 'saved'}


def run_begin(scope, label):
    """Open a run row for this scope; subsequent _write_ledger batch rows
    nest under it. No-ops (keeps the outer run) if one is already open."""
    from plugins.mindpalace.tools import ledger as lg
    with _runs_lock:
        if scope in _runs:
            return
    rid = lg.record(scope, 'librarian', 'pass', target='run',
                    summary=f'{label} — running…')
    if rid is None:
        return   # ledger down: batches fall back to top-level rows
    with _runs_lock:
        _runs[scope] = {'id': rid, 'label': label, 'batches': 0,
                        'presented': 0, 'handled': 0, 'verbs': {}, 'extra': {}}


def _run_note(scope, **counts):
    """Fold extra counters into the run report (dedup 'scanned', link
    'connections') — things batch verbs don't carry."""
    with _runs_lock:
        ctx = _runs.get(scope)
        if ctx:
            for k, n in counts.items():
                ctx['extra'][k] = ctx['extra'].get(k, 0) + int(n or 0)


def run_end(scope, left=None):
    """Close the run: append ONE 'report' child carrying the after-action
    summary. Readers overlay it onto the run line."""
    from plugins.mindpalace.tools import ledger as lg
    with _runs_lock:
        ctx = _runs.pop(scope, None)
    if not ctx:
        return
    if ctx['batches'] == 0 and not ctx['extra']:
        summary = f"{ctx['label']}: nothing to do"
    else:
        bits = [f"{n} {_RUN_VERBS.get(a, a)}"
                for a, n in sorted(ctx['verbs'].items()) if n]
        bits += [f"{n} {k}" for k, n in sorted(ctx['extra'].items()) if n]
        summary = (f"{ctx['label']}: {ctx['batches']} batch(es), "
                   f"{ctx['handled']}/{ctx['presented']} handled"
                   + (f" — {', '.join(bits)}" if bits else ''))
    if left:
        summary += f" ({left} still queued)"
    lg.record(scope, 'librarian', 'report', parent_id=ctx['id'],
              target=ctx['id'], summary=summary,
              detail={'batches': ctx['batches'], 'presented': ctx['presented'],
                      'handled': ctx['handled'], 'verbs': ctx['verbs'],
                      **ctx['extra']})


def _write_ledger(cur, scope, stats, summary, kind):
    """Ledger v1: the whole pass lands as ONE parent 'pass' row with each
    verb as a child under it — one line in her sheet tail and the unread
    view, browsable children on the Self page. Inside a run (drain/nightly)
    the pass row nests under the run row instead, and its stats fold into
    the run report."""
    from plugins.mindpalace.tools import ledger as lg
    run_id = None
    with _runs_lock:
        ctx = _runs.get(scope)
        if ctx:
            run_id = ctx['id']
            ctx['batches'] += 1
            ctx['presented'] += stats.get('presented') or 0
            ctx['handled'] += stats.get('handled') or 0
            for c in stats.get('ledger') or []:
                ctx['verbs'][c['action']] = ctx['verbs'].get(c['action'], 0) + 1
    parent = lg.record(scope, 'librarian', 'pass', summary=summary,
                       parent_id=run_id,
                       detail={'presented': stats['presented'],
                               'handled': stats['handled'], 'kind': kind},
                       cursor=cur)
    for c in stats.get('ledger') or []:
        lg.record(scope, 'librarian', c['action'], layer=c.get('layer'),
                  target=c.get('target'), summary=c['summary'],
                  detail=c.get('detail'), parent_id=parent, cursor=cur)


def _stamp_meta_at(ids, key):
    """Set meta.<key> = now on the given chunks — the mechanical drain stamp
    (dedup_at). Returns how many rows were stamped."""
    pt = _pt()
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    n = 0
    with pt._get_connection() as conn:
        cur = conn.cursor()
        now = pt._now()
        for cid in ids:
            row = cur.execute('SELECT meta FROM chunks WHERE id = ?',
                              (cid,)).fetchone()
            if not row:
                continue
            try:
                meta = json.loads(row[0]) if row[0] else {}
            except Exception:
                meta = {}
            meta[key] = now
            cur.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                        (json.dumps(meta, ensure_ascii=False), cid))
            n += 1
        conn.commit()
    return n


def _ledger_count(stats, action):
    return sum(1 for c in stats.get('ledger') or [] if c['action'] == action)


# ─── Workers (one per pass kind) ─────────────────────────────────────────────

def _worker(scope, what, kind='sort'):
    """Dispatch — and the latch's LAST line of defense: anything that
    raises outside a worker's own try (a failed lazy import, a reload
    mid-run) must still release the one-groundskeeper slot, or every
    future pass refuses until process restart."""
    try:
        if kind == 'dates':
            return _worker_dates(scope)
        if kind == 'link':
            return _worker_link(scope)
        if kind == 'dedup':
            return _worker_dedup(scope)
        if kind == 'self':
            return _worker_self(scope)
        return _worker_sort(scope, what)
    except Exception as e:
        logger.error(f"[LIBRARIAN] {kind} worker crashed outside its "
                     f"guard: {e}", exc_info=True)
    finally:
        with _state_lock:
            stuck = _state['running']
        if stuck:
            _finish(f"{_PASS_LABELS.get(kind, kind)} crashed — slot "
                    f"released, nothing recorded.", error=True)


def _worker_self(scope):
    """The self pass: two messages in one session — tend (live sheet +
    charter, update_self only) then verify (updated sheet back for a final
    look). No blast shield ids: update_self is inherently sheet-scoped and
    carries its own code guards (wake-tools refused, duplicates fold,
    identity keeps its importance pin). Audit rides the sheet's own version
    trail — every edit archives."""
    pt, lt = _pt(), _lt()
    try:
        cfg = _settings()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            ok, why = _check_caps(cur, scope, cfg, kind='self')
            if not ok:
                conn.commit()
                _finish(why)
                return
            conn.commit()
        started = _now()
        lt.open_pass(scope, [], kind='self')
        try:
            oks = _run_messages(scope, ['tend', 'verify'], cfg, SELF_TOOLSET,
                                'Self pass',
                                lambda g, p, t: _present_self(g, scope, p, t))
        finally:
            stats = lt.close_pass()

        if not any(oks):
            _finish(f"Self pass failed for '{scope}': the model produced no "
                    f"usable response. Nothing recorded.", error=True)
            return

        with pt._get_connection() as conn:
            cur = conn.cursor()
            # The edits themselves ledger through write_section (actor='ai',
            # per-section line diffs); count them into the pass summary so
            # the pass parent tells the night's story at a glance.
            try:
                edits = cur.execute(
                    "SELECT COUNT(*) FROM ledger WHERE scope = ? "
                    "AND layer = 'self' AND ts >= ?", (scope, started)
                ).fetchone()[0]
            except Exception:
                edits = 0
            try:
                _write_ledger(cur, scope, stats,
                              (f"self pass: {edits} section"
                               f"{'s' if edits != 1 else ''} updated, "
                               f"verified"), 'self')
            except Exception as e:
                logger.warning(f"[LIBRARIAN] Self-pass ledger row skipped: {e}")
            _record_pass(cur, scope, 'self', stats)
            conn.commit()
        try:
            pt._publish_mind('self', scope, 'update')
        except Exception:
            pass
        _finish(f"Self pass complete for '{scope}' — tended and verified.")
    except Exception as e:
        try:
            lt.close_pass()
        except Exception:
            pass
        _finish(f"Self pass failed: {e}", error=True)


def _worker_sort(scope, what):
    """The review charter: mark / atomize / promote / prune. Duplicate
    folding and entity linking moved to their own passes (2026-07-16)."""
    pt, lt = _pt(), _lt()
    try:
        cfg = _settings()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            ok, why = _check_caps(cur, scope, cfg, kind='sort')
            if not ok:
                conn.commit()
                _finish(why)
                return
            batch = build_batch(cur, scope, what, cfg['batch'])
            dirty = _dirty_entities(cur, scope)
            conn.commit()
        if not batch:
            _finish(f"Nothing to review in '{scope}' — the shelf is tidy.")
            return

        groups = [batch[i:i + cfg['per_msg']]
                  for i in range(0, len(batch), cfg['per_msg'])]
        lt.open_pass(scope, [b[0] for b in batch], kind='sort')
        try:
            oks = _run_messages(scope, groups, cfg, SORT_TOOLSET, 'Sort pass',
                                lambda g, p, t: _present_sort(
                                    g, scope, dirty if p == 1 else [], p, t))
        finally:
            stats = lt.close_pass()

        if not stats['handled']:
            # ZERO verbs fired — whether the messages "failed" or came back
            # as clean verb-less prose (weak local models do this). Either
            # way nothing carries librarian_at, so the identical oldest-first
            # batch would re-present on every pass; spending a cap on it
            # burns 3 passes/day forever with the queue frozen (scout find,
            # 2026-07-19). No cap spent, no ledger — the batch requeues.
            _finish(f"Sort pass failed for '{scope}': no verbs recorded "
                    f"across {len(groups)} message(s). Nothing recorded — "
                    f"the batch requeues.", error=True)
            return

        with pt._get_connection() as conn:
            cur = conn.cursor()
            _ensure_state_table(cur)
            try:
                names = {'promoted': 'promoted', 'retired': 'retired',
                         'atomized': 'split'}
                bits = [f"{_ledger_count(stats, a)} {label}"
                        for a, label in names.items() if _ledger_count(stats, a)]
                summary = (f"sort pass: {stats['presented']} reviewed — "
                           + (", ".join(bits) + ", rest kept" if bits
                              else "all kept as-is"))
                _write_ledger(cur, scope, stats, summary, 'sort')
            except Exception as e:
                logger.warning(f"[LIBRARIAN] Ledger pass rows skipped: {e}")
            # Mention counters are "since last sort pass" — reset.
            cur.execute('UPDATE entities SET mentions = 0 WHERE scope = ?',
                        (scope,))
            _record_pass(cur, scope, 'sort', stats)
            conn.commit()
        try:
            pt._publish_mind('events', scope, 'update')
        except Exception:
            pass
        _finish(f"Sort pass complete for '{scope}': {stats['handled']}/"
                f"{stats['presented']} handled across {len(groups)} message(s).")
    except Exception as e:
        try:
            lt.close_pass()
        except Exception:
            pass
        _finish(f"Sort pass failed: {e}", error=True)


def _worker_dates(scope):
    """The dates slice: date-candidate batch (newest first), single-verb
    toolset, one bulk set_event_dates call per message. Dating is annotation,
    not review — no duplicate scan, no dirty-entity report, no
    mention-counter reset, and it never spends another pass's daily cap."""
    pt, lt = _pt(), _lt()
    try:
        cfg = _settings()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            ok, why = _check_caps(cur, scope, cfg, kind='dates')
            if not ok:
                conn.commit()
                _finish(why)
                return
            batch = build_temporal_batch(cur, scope, cfg['batch'])
            conn.commit()
        if not batch:
            _finish(f"No date candidates in '{scope}' — the calendar is current.")
            return

        groups = [batch[i:i + cfg['per_msg']]
                  for i in range(0, len(batch), cfg['per_msg'])]
        lt.open_pass(scope, [b[0] for b in batch], kind='dates')
        try:
            oks = _run_messages(scope, groups, cfg, DATES_TOOLSET,
                                'Dates pass',
                                lambda g, p, t: _present_dates(g, scope, p, t))
        finally:
            stats = lt.close_pass()

        if not any(oks) and not stats['handled']:
            _finish(f"Dates pass failed for '{scope}': the model produced no "
                    f"usable response ({len(groups)} message(s) degraded). "
                    f"Nothing recorded — the batch requeues.", error=True)
            return

        with pt._get_connection() as conn:
            cur = conn.cursor()
            try:
                _write_ledger(cur, scope, stats,
                              (f"dates pass: {stats['handled']}/"
                               f"{stats['presented']} memories dated"), 'dates')
            except Exception as e:
                logger.warning(f"[LIBRARIAN] Dates ledger row skipped: {e}")
            _record_pass(cur, scope, 'dates', stats)
            conn.commit()
        try:
            pt._publish_mind('events', scope, 'update')
        except Exception:
            pass
        _finish(f"Dates pass complete for '{scope}': {stats['handled']}/"
                f"{stats['presented']} dated across {len(groups)} message(s).")
    except Exception as e:
        try:
            lt.close_pass()
        except Exception:
            pass
        _finish(f"Dates pass failed: {e}", error=True)


def _worker_link(scope):
    """The link slice: unmatched-name batch + entity roster, single-verb
    toolset, one bulk set_links call per message. The verdict drains the
    queue (link_at) — connected or ruled connection-free, both are filed."""
    pt, lt = _pt(), _lt()
    try:
        cfg = _settings()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            ok, why = _check_caps(cur, scope, cfg, kind='link')
            if not ok:
                conn.commit()
                _finish(why)
                return
            batch = build_link_batch(cur, scope, cfg['batch'])
            roster = [r[0] for r in cur.execute(
                "SELECT name FROM entities WHERE scope IN (?, 'global') "
                "ORDER BY name", (scope,)).fetchall()]
            conn.commit()
        if not batch:
            _finish(f"No unlinked name-candidates in '{scope}' — "
                    f"connections are current.")
            return
        if not roster:
            _finish(f"No entities exist in '{scope}' yet — nothing to link to.")
            return

        groups = [batch[i:i + cfg['per_msg']]
                  for i in range(0, len(batch), cfg['per_msg'])]
        lt.open_pass(scope, [b[0] for b in batch], kind='link')
        try:
            oks = _run_messages(scope, groups, cfg, LINK_TOOLSET, 'Link pass',
                                lambda g, p, t: _present_link(g, scope,
                                                              roster, p, t))
        finally:
            stats = lt.close_pass()

        if not any(oks) and not stats['handled']:
            _finish(f"Link pass failed for '{scope}': the model produced no "
                    f"usable response ({len(groups)} message(s) degraded). "
                    f"Nothing recorded — the batch requeues.", error=True)
            return

        linked = _ledger_count(stats, 'linked')
        with pt._get_connection() as conn:
            cur = conn.cursor()
            try:
                _write_ledger(cur, scope, stats,
                              (f"link pass: {stats['handled']}/"
                               f"{stats['presented']} memories handled — "
                               f"{linked} connection{'s' if linked != 1 else ''}"),
                              'link')
            except Exception as e:
                logger.warning(f"[LIBRARIAN] Link ledger row skipped: {e}")
            _record_pass(cur, scope, 'link', stats)
            conn.commit()
        try:
            pt._publish_mind('events', scope, 'update')
        except Exception:
            pass
        _finish(f"Link pass complete for '{scope}': {stats['handled']}/"
                f"{stats['presented']} handled, {linked} new connection(s).")
    except Exception as e:
        try:
            lt.close_pass()
        except Exception:
            pass
        _finish(f"Link pass failed: {e}", error=True)


def _worker_dedup(scope):
    """The dedup slice: scan unchecked chunks for measured near-duplicate
    clusters, present whole clusters, merge_memories only (the similarity
    gate refuses everything else). Drain is mechanical: everything scanned
    gets dedup_at AFTER the pass completes — an exception mid-pass leaves the
    batch unstamped for a clean retry."""
    pt, lt = _pt(), _lt()
    try:
        cfg = _settings()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            ok, why = _check_caps(cur, scope, cfg, kind='dedup')
            if not ok:
                conn.commit()
                _finish(why)
                return
            batch = build_dedup_batch(cur, scope, cfg['batch'])
            dups = find_duplicates(cur, scope, batch, lt._merge_threshold())
            partner_ids = sorted({d for hits in dups.values() for d, _ in hits}
                                 - {b[0] for b in batch})
            partners = {}
            if partner_ids:
                ph = ','.join('?' * len(partner_ids))
                partners = {r[0]: (r[1], r[2]) for r in cur.execute(
                    f'SELECT id, created, content FROM chunks '
                    f'WHERE private_key IS NULL AND id IN ({ph})',
                    partner_ids).fetchall()}
            conn.commit()
        if not batch:
            _finish(f"Nothing awaiting a duplicate check in '{scope}'.")
            return

        batch_ids = [b[0] for b in batch]
        clusters = _build_clusters(batch, dups, partners)
        if not clusters:
            # Scanned clean — stamp so the same chunks never rescan; the
            # scan still counts as a pass (visible, cap-bounded).
            _stamp_meta_at(batch_ids, 'dedup_at')
            with pt._get_connection() as conn:
                cur = conn.cursor()
                try:
                    _write_ledger(cur, scope,
                                  {'presented': 0, 'handled': 0, 'ledger': []},
                                  (f"dedup pass: {len(batch_ids)} scanned — "
                                   f"no near-duplicates"), 'dedup')
                except Exception as e:
                    logger.warning(f"[LIBRARIAN] Dedup ledger row skipped: {e}")
                _record_pass(cur, scope, 'dedup',
                             {'presented': 0, 'handled': 0,
                              'scanned': len(batch_ids)})
                conn.commit()
            _run_note(scope, scanned=len(batch_ids))
            _finish(f"Dedup pass: {len(batch_ids)} scanned in '{scope}' — "
                    f"no near-duplicates.")
            return

        # Whole clusters per message — a split group can't be judged.
        groups, current, count = [], [], 0
        for cl in clusters:
            if current and count + len(cl) > cfg['per_msg']:
                groups.append(current)
                current, count = [], 0
            current.append(cl)
            count += len(cl)
        if current:
            groups.append(current)

        presented = sorted({cid for cl in clusters for cid, _cr, _c in cl})
        lt.open_pass(scope, presented, kind='dedup')
        try:
            oks = _run_messages(scope, groups, cfg, DEDUP_TOOLSET,
                                'Dedup pass',
                                lambda g, p, t: _present_dedup(g, scope, p, t))
        finally:
            stats = lt.close_pass()

        # Drain — but ONLY what was actually judged. "Unmerged = ruled
        # distinct" is true when the model saw the cluster and declined;
        # it is FALSE when the message degraded/errored — stamping those
        # would permanently lock real duplicates out of every future scan
        # (scout sweep 2026-07-19). Clean batch ids (no clusters found)
        # are mechanical facts and always stamp; cluster members stamp
        # per-message, only for messages that completed.
        clustered = {cid for cl in clusters for cid, _cr, _c in cl}
        judged = set()
        for g, ok in zip(groups, oks):
            if ok:
                judged |= {cid for cl in g for cid, _cr, _c in cl}
        _stamp_meta_at(sorted((set(batch_ids) - clustered) | judged),
                       'dedup_at')
        skipped_groups = oks.count(False)

        if not any(oks) and not stats['handled']:
            _finish(f"Dedup pass failed for '{scope}': the model produced no "
                    f"usable response ({len(groups)} message(s) degraded). "
                    f"Clusters unstamped — they rescan next pass.", error=True)
            return

        merged = _ledger_count(stats, 'merged')
        with pt._get_connection() as conn:
            cur = conn.cursor()
            try:
                _write_ledger(cur, scope, stats,
                              (f"dedup pass: {len(clusters)} group(s) "
                               f"reviewed — {merged} merged"), 'dedup')
            except Exception as e:
                logger.warning(f"[LIBRARIAN] Dedup ledger row skipped: {e}")
            _record_pass(cur, scope, 'dedup', stats)
            conn.commit()
        try:
            pt._publish_mind('events', scope, 'update')
        except Exception:
            pass
        retry = (f" ({skipped_groups} message(s) degraded — their clusters "
                 f"rescan next pass)" if skipped_groups else "")
        _finish(f"Dedup pass complete for '{scope}': {merged} of "
                f"{len(clusters)} group(s) merged.{retry}")
    except Exception as e:
        try:
            lt.close_pass()
        except Exception:
            pass
        _finish(f"Dedup pass failed: {e}", error=True)


def get_status(scope=None):
    with _state_lock:
        snap = dict(_state)
    out = {'running': snap['running'], 'current': snap}
    try:
        pt = _pt()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            _ensure_state_table(cur)
            if scope:
                rows = cur.execute('SELECT scope, pass, last_pass, day, '
                                   'passes_today, last_result FROM librarian_state '
                                   'WHERE scope = ?', (scope,)).fetchall()
            else:
                rows = cur.execute('SELECT scope, pass, last_pass, day, '
                                   'passes_today, last_result '
                                   'FROM librarian_state').fetchall()
            conn.commit()
        out['scopes'] = [{'scope': r[0], 'pass': r[1], 'last_pass': r[2],
                          'day': r[3], 'passes_today': r[4],
                          'last_result': json.loads(r[5]) if r[5] else None}
                         for r in rows]
    except Exception as e:
        out['scopes'] = []
        logger.debug(f"[LIBRARIAN] status read failed: {e}")
    if scope:
        # Per-pass unprocessed counts for the Admin cards — the same
        # selectors a real run pulls, so the number can never drift.
        # 5s TTL cache: the Admin poll runs every 3s during a pass, and
        # four full candidate scans per tick contend with the writer on
        # large scopes (fixer-scout, 2026-07-19).
        try:
            import time as _time
            ts, cached = _queue_cache.get(scope, (0, None))
            if cached is not None and _time.time() - ts < 5:
                out['queues'] = cached
            else:
                q = {k: _queue_depth(scope, k)
                     for k in ('dates', 'link', 'dedup', 'sort')}
                _queue_cache[scope] = (_time.time(), q)
                out['queues'] = q
        except Exception as e:
            logger.debug(f"[LIBRARIAN] queue depths failed: {e}")
    return out
