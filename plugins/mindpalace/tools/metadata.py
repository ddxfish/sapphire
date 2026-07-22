# plugins/mindpalace/tools/metadata.py
# Tier A write-time metadata: pure string-level extraction, zero models.
# Everything lands in the chunks.meta JSON column; entity-name matches also
# seed edges (kind='mentions', weight 0.3) and bump entity mention counters
# (the librarian dirty flag). Standing constraint (shortcoming #1): metadata
# is mechanical, never the main LLM — and a failure here must never block
# a save (callers wrap in try/except, meta degrades to thinner).

import re
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MD_VERSION = 2  # bump to selectively re-backfill rows stamped by older passes
                # v2 (2026-07-16): derivable set gained event_dates (temporal
                # floor) — v1 rows re-backfill so old memories get dated too

# Boot-window id for librarian batch grouping. Module import ≈ plugin load ≈
# boot; a plugin reload starts a new window, which is fine — the semantic is
# "batch window", not strict process identity.
SESSION_ID = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

MENTION_EDGE_WEIGHT = 0.3  # metadata-class: costs more than a structural hop

_MONTHS = (r'january|february|march|april|may|june|july|august|september|'
           r'october|november|december')
_WEEKDAYS = r'monday|tuesday|wednesday|thursday|friday|saturday|sunday'
_UNITS = (r'week|month|year|summer|winter|spring|fall|autumn|weekend|'
          r'morning|afternoon|evening|night')

# Explicit temporal references INSIDE content — a different axis from
# saved-at. Powers "what did we do last summer" as a walk over what memories
# are ABOUT, and feeds the temporal-pass CANDIDATE queue (tmp/librarian.md):
# any chunk with refers_to_time and no terminal temporal verdict is date-pass
# material. Bare month names are excluded ("may", "march" collide with common
# English); months only count with a day/year or a temporal lead-in.
_MONTHS_ABBR = r'jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec'
_NUM_WORDS = r'a|an|one|two|three|four|five|six|seven|eight|nine|ten'
_TEMPORAL_RE = re.compile(
    r'\b(?:'
    r'yesterday|today|tomorrow|tonight|last\s+night|'
    r'(?:last|this|next)\s+(?:' + _UNITS + r'|' + _WEEKDAYS + r'|' + _MONTHS + r')|'
    r'(?:\d+|' + _NUM_WORDS + r')\s+(?:day|week|month|year|hour|minute)s?\s+ago|'
    r'in\s+(?:\d+|' + _NUM_WORDS + r')\s+(?:day|week|month|year|hour|minute)s?\b|'
    r'(?:in|since|until|by)\s+(?:' + _MONTHS + r')|'
    r'(?:' + _MONTHS + r'|' + _MONTHS_ABBR + r')\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|'
    r'\d{1,2}(?:st|nd|rd|th)?\s+of\s+(?:' + _MONTHS + r')|'
    r'the\s+\d{1,2}(?:st|nd|rd|th)|'
    r'end\s+of\s+(?:the\s+)?(?:month|year|week)|'
    r'at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|'
    r"christmas(?:\s+eve)?|thanksgiving|halloween|new\s+year's?|valentine'?s?(?:\s+day)?|"
    r'(?:' + _WEEKDAYS + r')|'
    r'(?:19|20)\d{2}'
    r')\b', re.IGNORECASE)

_URL_RE = re.compile(r'https?://')
_WORD_RE = re.compile(r"[A-Za-z][\w'’-]*")
_SENT_SPLIT = re.compile(r'[.!?]\s+|\n+')

# Sentence-position filtering handles ordinary capitalization; this only
# needs words that stay capitalized mid-sentence without naming anything.
_NOUN_STOP = {
    'i', "i'm", "i'll", "i've", "i'd", 'ok', 'okay', 'god', 'oh', 'hey',
    'ai', 'llm', 'tts', 'stt', 'gpu', 'cpu', 'url', 'api', 'ui', 'db', 'id',
}


def _context_fields() -> dict:
    """Provenance from core's tool_context ContextVar (chat/persona/model/
    channel), set at chat setup and carried on the scope-snapshot rail.
    Fail-safe: unreadable context → fewer meta keys, never an error."""
    try:
        from core.chat.function_manager import tool_context
        ctx = tool_context.get()
        return {k: v for k, v in dict(ctx).items() if v} if ctx else {}
    except Exception:
        return {}


def temporal_refs(content: str) -> list:
    """Lowercased explicit time expressions found in content, order kept."""
    seen, out = set(), []
    for m in _TEMPORAL_RE.finditer(content):
        ref = ' '.join(m.group(0).lower().split())
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
        if len(out) >= 8:
            break
    return out


def content_stats(content: str) -> dict:
    return {
        'len': len(content),
        'words': len(content.split()),
        'question': '?' in content,
        'url': bool(_URL_RE.search(content)),
        'code': '```' in content or content.count('`') >= 2,
    }


def noun_candidates(content: str, exclude=()) -> list:
    """Mid-sentence capitalized runs ("New York") — candidate NEW entities,
    parked for the librarian to resolve. Sentence-initial words are skipped
    (the cost of a zero-model heuristic; Tier B NER replaces this).
    `exclude` is a set of lowercased names (already-matched entities)."""
    exclude = {e.lower() for e in exclude}
    seen, out = set(), []
    for sentence in _SENT_SPLIT.split(content):
        run = []
        for i, m in enumerate(_WORD_RE.finditer(sentence)):
            word = m.group(0)
            if i > 0 and word[0].isupper() and word.lower() not in _NOUN_STOP:
                run.append(word)
            else:
                if run:
                    _push_run(run, exclude, seen, out)
                run = []
        if run:
            _push_run(run, exclude, seen, out)
    return out[:10]


def _push_run(run, exclude, seen, out):
    cand = ' '.join(run)
    key = cand.lower()
    if key not in exclude and key not in seen:
        seen.add(key)
        out.append(cand)
    run.clear()


def match_entities(content: str, entity_names) -> list:
    """NOCASE word-boundary match of known entity names against content.
    Returns matched names in their stored casing, longest-name-first wins
    on overlapping spans ("Krem Senior" beats "Krem")."""
    names = [n for n in entity_names if n and n.strip()]
    if not names:
        return []
    pattern = r'\b(?:' + '|'.join(
        re.escape(n) for n in sorted(names, key=len, reverse=True)) + r')\b'
    try:
        hits = re.findall(pattern, content, re.IGNORECASE)
    except re.error as e:
        logger.warning(f"[MINDPALACE] Entity match pattern failed: {e}")
        return []
    by_lower = {n.lower(): n for n in names}
    seen, out = set(), []
    for h in hits:
        key = h.lower()
        if key in by_lower and key not in seen:
            seen.add(key)
            out.append(by_lower[key])
    return out


def entity_aliases(cursor, scope):
    """[(entity_id, canonical_name, alias)] for scope + global overlay.
    Canonical name first, then nicknames from meta.fields.nicknames (CSV,
    person template) — so 'the boss' matches Krem everywhere match_entities
    runs (save-time seeding, backfill, spider epicenter resolution)."""
    rows = cursor.execute(
        "SELECT id, name, meta FROM entities WHERE scope IN (?, 'global')",
        (scope,)).fetchall()
    out = []
    for eid, name, meta_raw in rows:
        out.append((eid, name, name))
        if not meta_raw:
            continue
        try:
            nicks = (json.loads(meta_raw).get('fields') or {}).get('nicknames') or ''
        except Exception:
            continue
        for nick in str(nicks).split(','):
            nick = nick.strip()
            if nick and nick.lower() != name.lower():
                out.append((eid, name, nick))
    return out


def alias_map(alias_rows):
    """{alias_lower: (entity_id, canonical_name)}. Two passes: real names
    claim their slot first, so a nickname colliding with another entity's
    actual name never shadows it."""
    amap = {}
    for eid, canonical, alias in alias_rows:
        if alias == canonical:
            amap.setdefault(alias.lower(), (eid, canonical))
    for eid, canonical, alias in alias_rows:
        amap.setdefault(alias.lower(), (eid, canonical))
    return amap


def derivable_meta(content: str, exclude_names=(), anchor=None) -> dict:
    """The retro-safe subset: stats, temporal refs, resolved event dates,
    noun candidates. Used by both save-time stamping and backfill().
    `anchor` = the moment the content was written (save: now — the resolver's
    fallback; backfill: the chunk's own created) — relative dates resolve
    against it (temporal floor, tmp/librarian.md)."""
    meta = {'md_v': MD_VERSION, 'stats': content_stats(content)}
    refs = temporal_refs(content)
    if refs:
        meta['refers_to_time'] = refs
    try:
        from plugins.mindpalace.tools import temporal
        dates = temporal.resolve(content, anchor)
        if dates:
            meta['event_dates'] = dates
            meta['event_date_src'] = 'regex'
        rec = temporal.recurring(content)
        if rec:
            meta['recurring_dates'] = rec
    except Exception as e:
        logger.debug(f"[MINDPALACE] Temporal floor skipped: {e}")
    nouns = noun_candidates(content, exclude=exclude_names)
    if nouns:
        meta['noun_candidates'] = nouns
    return meta


def save_meta(content: str, exclude_names=(), anchor=None) -> dict:
    """Full save-time meta: derivable subset + boot window + tool context.
    The context fields (model/chat/persona/channel) only exist forward —
    they are unknowable retroactively, so backfill() never writes them.
    `anchor`: pass the inherited created for chunks whose content predates
    this call (librarian-derived) — default None resolves relative dates
    against now, which is only right for genuinely-new content."""
    meta = derivable_meta(content, exclude_names=exclude_names, anchor=anchor)
    meta['session_id'] = SESSION_ID
    meta.update(_context_fields())
    return meta


def seed_edges(cursor, chunk_id: int, entity_ids, now: str) -> int:
    """Insert chunk→entity 'mentions' edges + bump entity mention counters
    (librarian dirty flag). Skips edges that already exist (backfill safety /
    double-count guard). Returns edges created."""
    created = 0
    for eid in entity_ids:
        cursor.execute(
            "SELECT 1 FROM edges WHERE src_type = 'chunk' AND src_id = ? "
            "AND dst_type = 'entity' AND dst_id = ? AND kind = 'mentions'",
            (chunk_id, eid))
        if cursor.fetchone():
            continue
        cursor.execute(
            "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
            "VALUES ('chunk', ?, 'entity', ?, 'mentions', ?, ?)",
            (chunk_id, eid, MENTION_EDGE_WEIGHT, now))
        cursor.execute(
            "UPDATE entities SET mentions = mentions + 1, updated = ? WHERE id = ?",
            (now, eid))
        created += 1
    return created


def backfill() -> dict:
    """One-shot idempotent pass: stamp rows missing md_v with the derivable
    subset and seed entity edges. Runs after import_v2; safe to re-run any
    time (md_v marks done). Does NOT touch chunks.updated — content is
    unchanged, only mechanical annotation. Returns counts."""
    from plugins.mindpalace.tools import palace_tools as pt
    if not pt._ensure_db():
        return {'error': 'mind database unavailable'}
    stamped = edges = 0
    try:
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT id, scope, content, entity_id, meta, created FROM chunks "
                "WHERE meta IS NULL OR json_extract(meta, '$.md_v') IS NULL"
            ).fetchall()
            ent_cache = {}
            for cid, scope, content, entity_id, meta_raw, created in rows:
                if scope not in ent_cache:
                    ent_cache[scope] = entity_aliases(cursor, scope)
                arows = ent_cache[scope]
                amap = alias_map(arows)
                matched = match_entities(content, [a for _, _, a in arows])
                ent_ids = []
                for m in matched:
                    pair = amap.get(m.lower())
                    if pair and pair[0] != entity_id and pair[0] not in ent_ids:
                        ent_ids.append(pair[0])

                meta = {}
                if meta_raw:
                    try:
                        meta = json.loads(meta_raw)
                    except Exception:
                        meta = {'meta_orig': meta_raw}
                # A librarian temporal verdict (dates or ruled-dateless) is
                # terminal — the regex floor never overwrites the refiner.
                verdict = (meta.get('temporal_at')
                           or meta.get('event_date_src') == 'librarian')
                keep = {k: meta[k] for k in ('event_dates', 'event_date_src')
                        if k in meta} if verdict else None
                meta.update(derivable_meta(
                    content, exclude_names=[m.lower() for m in matched],
                    anchor=created))
                if verdict:
                    meta.pop('event_dates', None)
                    meta.pop('event_date_src', None)
                    meta.update(keep)

                now = pt._now()
                cursor.execute("UPDATE chunks SET meta = ? WHERE id = ?",
                               (json.dumps(meta, ensure_ascii=False), cid))
                edges += seed_edges(cursor, cid, ent_ids, now)
                stamped += 1
                if stamped % 500 == 0:
                    conn.commit()
            conn.commit()
    except Exception as e:
        logger.error(f"[MINDPALACE] Metadata backfill failed: {e}")
        return {'error': str(e), 'stamped': stamped, 'edges': edges}
    logger.info(f"[MINDPALACE] Metadata backfill: {stamped} chunks stamped, "
                f"{edges} edges seeded")
    return {'stamped': stamped, 'edges': edges}
