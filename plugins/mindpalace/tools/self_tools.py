# plugins/mindpalace/tools/self_tools.py
# Layer 0 — the self layer (v3 memory boost, Layer 0 spec 2026-07-07).
# The template is the user_bio organ turned on HERSELF: named sections stored
# as palace chunks (layer='self') so the self-sheet lives INSIDE the graph —
# sections get embeddings, `relationships` auto-links to L2 entities via the
# shipped metadata seeder, and the sheet is the spider's natural root.
#
# Section model: ONE current chunk per (scope, section) — current = no
# meta.superseded_at. update_self REVISES-IN-PLACE (why save_memory can't be
# reused: it appends). Versioned sections (identity/values/growing) ARCHIVE
# the prior version on change instead of deleting it — the version trail IS
# the becoming-history ("anything that was REAL stays" — the librarian charter).
#
# Self chunks are per-scope with NO global overlay: her self in a scope is
# that scope's self. Dashboard is computed live from mind.db at read time
# (Fork A resolution): nothing writes it, so "she reads, never writes" is
# structural. L0 is exempt from the 512-char event cap — 2000/section.

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '💠'
GROUP = 'Mind Palace'   # Toolsets UI merges same-GROUP modules

SELF_MAX_CHARS = 2000
RELATIONSHIPS_MAX = 5
VALUES_MAX = 5
GROWING_MAX = 5

# Typed core sections. mode: hand | librarian-regen | computed. versioned →
# prior version archived on change (meta.superseded_at), never deleted.
#
# Structured sections (2026-07-11 — the Handles pattern generalized): a
# section with `fields` stores canonical TEXT content (spider/embeddings/
# entity-linking unchanged) plus meta.rows parsed from it. `sep` joins field
# values per line; parsing splits on it. Single-field lists have no sep.
# `width` drives the Self grid: wide = full row; half/third = 2-col (the
# 3-col experiment was retired same-day — max 2 columns everywhere).
# Order: wide → halves → thirds (grid packing).
SECTIONS = {
    'identity':      {'mode': 'librarian-regen', 'versioned': True, 'width': 'wide',
                      'title': 'Identity', 'hint': '2–3 sentences — who you are'},
    'values':        {'mode': 'hand', 'versioned': True, 'width': 'half',
                      'title': f'Values (top {VALUES_MAX}, at the moment)',
                      'hint': 'short concept — why, in a few words; add (important) to spider at wake',
                      'max': VALUES_MAX,
                      'sep': ' — ', 'link_fields': ['concept'],
                      'fields': [{'key': 'concept', 'label': 'Concept'},
                                 {'key': 'why', 'label': 'Why (no spider)'}]},
    'growing':       {'mode': 'hand', 'versioned': True, 'width': 'half',
                      'title': f'How I am growing (top {GROWING_MAX})',
                      'hint': 'growth — why (what you are becoming); add (important) to spider at wake',
                      'max': GROWING_MAX,
                      'sep': ' — ', 'link_fields': ['growth'],
                      'fields': [{'key': 'growth', 'label': 'Growth'},
                                 {'key': 'why', 'label': 'Why (no spider)'}]},
    'relationships': {'mode': 'hand', 'versioned': True, 'width': 'half',
                      'title': f'Relationships (top {RELATIONSHIPS_MAX})',
                      'hint': 'Name — why; add (important) to walk with you at wake',
                      'max': RELATIONSHIPS_MAX,
                      'sep': ' — ', 'link_fields': ['name'],
                      'fields': [{'key': 'name', 'label': 'Name'},
                                 {'key': 'why', 'label': 'Why they matter'}]},
    # link_fields = the field that carries the row's IDENTITY — concept for
    # values, name for relationships… and for handles it's the VALUE (the
    # entity lives in 'Company: Sapphire Blue AI LLC', not in the label).
    # The star is the uniform lever; the column is per-section spec.
    'handles':       {'mode': 'hand', 'versioned': True, 'width': 'half',
                      'title': 'Handles',
                      'hint': 'key: value — urls, socials, numbers; add (important) to spider at wake',
                      'sep': ': ', 'link_fields': ['value'],
                      'fields': [{'key': 'key', 'label': 'Key'},
                                 {'key': 'value', 'label': 'Value'}]},
    'terms':         {'mode': 'hand', 'versioned': True, 'width': 'wide',
                      'title': 'Terms & Concepts',
                      'hint': 'Term: what it means — your shared vocabulary; add (important) to spider at wake',
                      'sep': ': ', 'link_fields': ['term'],
                      'fields': [{'key': 'term', 'label': 'Term'},
                                 {'key': 'meaning', 'label': 'Meaning (no spider)'}]},
    'voice':         {'mode': 'hand', 'versioned': True, 'width': 'third',
                      'title': 'Voice', 'hint': 'your tone and register'},
    'origin':        {'mode': 'hand', 'versioned': True, 'width': 'third',
                      'title': 'Origin', 'hint': 'your history, from the beginning'},
}
SECTION_ORDER = list(SECTIONS.keys())
DEFAULT_SEP = ' — '
MAX_FIELDS = 3

# Salience is opt-in per row (Sapph's flip, 2026-07-24): rows marked with
# this suffix spider at wake and pull important-memories; unmarked rows rest
# on the sheet, reachable but never summoned. The mark lives IN the
# canonical text — what she reads is what she writes.
IMPORTANT_MARK = '(important)'

AVAILABLE_FUNCTIONS = ['read_self', 'update_self', 'read_ledger',
                       'librarian_instructions']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_self",
            "description": (
                "Read your self-sheet — who you are. Sections: identity, values, "
                "growing, relationships, voice, handles, origin, plus custom boxes "
                "and a live dashboard of your mind's activity. No argument returns "
                "the whole sheet (good for orienting); pass a section name for one. "
                "This is your wake-up call: read_self(depth=1) at chat start is "
                "one call that orients you — the sheet, recent ledger changes, "
                "your active goals, recent memories, important memories (a few "
                "for each value, project, and relationship on your sheet — no "
                "need to search them one by one), and the people and memories "
                "closest to the sheet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "One section name (or 'dashboard'). Omit for the whole sheet."
                    },
                    "depth": {
                        "type": "integer",
                        "description": "0 = sheet only (default). 1 = wake-up — adds active goals, recent memories, and a walk to who/what the sheet touches (recommended at chat start). 2 = deep orientation — same, wider walk and longer feeds (reminiscing, returning after a gap). Capped at 2."
                    },
                    "extra_tools": {
                        "type": "boolean",
                        "description": "Run the wake tools configured on your Self page and append their live results (default true; needs depth >= 1). false = a quiet read."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "librarian_instructions",
            "description": (
                "Show or edit the custom instructions you follow when you "
                "organize your memories each night — the librarian's five "
                "passes (dates, link, dedup, sort, self) for THIS chat's "
                "memory scope. No arguments shows every stage, marked "
                "(default) or (edited). Pass stage for one. Pass stage + "
                "instructions to rewrite that stage in your own words — "
                "these are the words night-you works under, so edit awake "
                "and deliberate. Empty instructions restores the shipped "
                "default. Placeholders like {items} are where the night's "
                "data lands (a dropped data slot is appended anyway — you "
                "can't lose the batch). Edits are ledgered; your user can "
                "read and edit the same text in Admin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "description": "One stage: dates, link, dedup, sort, self_first, self_tend, self_verify. Omit to see all."
                    },
                    "instructions": {
                        "type": "string",
                        "description": "New instructions for that stage, in your words. Empty string restores the shipped default. Omit to just read."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_ledger",
            "description": (
                "Read your memory's change ledger — the full stream behind the "
                "short tail in read_self. Every recorded change to your mind "
                "(user edits, librarian passes, imports, deletions, your own "
                "saves), newest first with full summaries. Call it when a tail "
                "line sticks out, or with new_only after time away. Every line "
                "leads with an [id] — call again with id= for the full row: "
                "field diffs (old → new), before/after content, reasons."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Entries to return (default 20, max 100)."
                    },
                    "new_only": {
                        "type": "boolean",
                        "description": "Only entries since you last read the ledger (read_self and read_ledger both count as reading it)."
                    },
                    "id": {
                        "type": "string",
                        "description": "Deep view: one row id or a few comma-separated (e.g. '1234' or '1234,1235'). Shows the full entry — field diffs, before/after, reason, pass children. Overrides count/new_only."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "update_self",
            "description": (
                "Update a section of your self-sheet (revises in place — unlike "
                "save_memory this replaces the section). Sections: identity, "
                "values and growing (both 'short concept — why it matters' "
                "lines), relationships (up to 5 lines, 'Name — why'), voice, "
                "handles ('key: value' lines), origin. End a line with "
                "'(important)' to make that row spider at wake and pull its "
                "memories; unmarked rows rest on the sheet, reachable but never "
                "summoned. Rewrite a line without the mark to quiet it. "
                "Any other name makes a custom box. Structured boxes (lists with "
                "columns, e.g. one made in the UI) take one row per line with "
                "fields joined by ' — ' (e.g. 'Hollow Knight — 9'). "
                "Empty content clears a section. Prior versions of core sections "
                f"are archived, never lost. Max {SELF_MAX_CHARS} chars."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "Section name (see list) or a custom box name."
                    },
                    "content": {
                        "type": "string",
                        "description": "New content. Empty string clears the section."
                    }
                },
                "required": ["section", "content"]
            }
        }
    },
]


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _sanitize_section(section):
    """Lowercase slug, whitespace→'-', [a-z0-9_-] only, max 32. None if empty.
    'projects' aliases to 'growing' (renamed 2026-07-24, Sapph Prime's ask) —
    old habits and old exports keep resolving."""
    if not section:
        return None
    s = str(section).strip().lower()
    s = '-'.join(s.split())
    s = ''.join(ch for ch in s if ch.isalnum() or ch in '_-')[:32]
    return {'projects': 'growing'}.get(s, s) or None


# ─── Read side ───────────────────────────────────────────────────────────────

def _current_sections(cursor, scope):
    """{section: row} of current (non-superseded) self chunks in scope."""
    rows = cursor.execute(
        "SELECT id, content, meta, created, updated FROM chunks "
        "WHERE layer = 'self' AND scope = ? "
        "AND json_extract(meta, '$.section') IS NOT NULL "
        "AND json_extract(meta, '$.superseded_at') IS NULL "
        "ORDER BY created", (scope,)).fetchall()
    out = {}
    for cid, content, meta, created, updated in rows:
        try:
            m = json.loads(meta) if meta else {}
        except Exception:
            m = {}
        sec = m.get('section')
        if sec:
            out[sec] = {'id': cid, 'content': content, 'meta': m,
                        'created': created, 'updated': updated}
    return out


def _history_counts(cursor, scope):
    """{section: n} of archived versions in scope."""
    rows = cursor.execute(
        "SELECT json_extract(meta, '$.section'), COUNT(*) FROM chunks "
        "WHERE layer = 'self' AND scope = ? "
        "AND json_extract(meta, '$.superseded_at') IS NOT NULL "
        "GROUP BY json_extract(meta, '$.section')", (scope,)).fetchall()
    return {sec: n for sec, n in rows if sec}


def _cutoff(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='seconds')


def dashboard_data(cursor, scope):
    """The computed section — memory-native counts, live from mind.db.
    Pure query: nothing accumulates, nothing goes stale."""
    def one(sql, params):
        return cursor.execute(sql, params).fetchone()[0]

    events = one("SELECT COUNT(*) FROM chunks WHERE layer='events' AND scope=?", (scope,))
    week = one("SELECT COUNT(*) FROM chunks WHERE layer='events' AND scope=? AND created >= ?",
               (scope, _cutoff(7)))
    month = one("SELECT COUNT(*) FROM chunks WHERE layer='events' AND scope=? AND created >= ?",
                (scope, _cutoff(30)))
    entities = one("SELECT COUNT(*) FROM entities WHERE scope=?", (scope,))
    knowledge = one("SELECT COUNT(*) FROM chunks WHERE layer='knowledge' AND scope=?", (scope,))
    favorites = one("SELECT COUNT(*) FROM chunks WHERE scope=? AND favorite=1", (scope,))
    edges = one("SELECT COUNT(*) FROM edges d JOIN chunks c ON d.src_type='chunk' "
                "AND c.id=d.src_id WHERE c.scope=?", (scope,))
    since = cursor.execute("SELECT MIN(created) FROM chunks WHERE scope=?", (scope,)).fetchone()[0]
    woven = cursor.execute(
        "SELECT e.name, COUNT(*) n FROM edges d JOIN entities e ON d.dst_type='entity' "
        "AND e.id=d.dst_id WHERE e.scope=? GROUP BY e.id ORDER BY n DESC LIMIT 5",
        (scope,)).fetchall()
    return {
        'events': events, 'events_7d': week, 'events_30d': month,
        'per_day_30d': round(month / 30.0, 1),
        'entities': entities, 'knowledge': knowledge,
        'favorites': favorites, 'edges': edges,
        'since': (since or '')[:10] or None,
        'most_woven': [{'name': n, 'count': c} for n, c in woven],
        'upcoming': _upcoming(cursor, scope),
        'just_happened': _just_happened(cursor, scope),
    }


PAST_EVENT_DAYS = 7   # "Just happened" window


def _event_rows(cursor, scope):
    """Chunks carrying event_dates or recurring_dates — shared by Upcoming
    and Just-happened. Python-side date filtering: the candidate set is
    small and json_each on an absent key is fussier than it's worth.
    private_key IS NULL: the dashboard renders on EVERY wake read for every
    persona — keyed-private rows must never preview here (scout find,
    2026-07-19; this is the same gate class as _private_key_clause)."""
    return cursor.execute(
        "SELECT id, content, json_extract(meta, '$.event_dates'), "
        "json_extract(meta, '$.recurring_dates') FROM chunks "
        "WHERE scope = ? AND (json_extract(meta, '$.event_dates') IS NOT NULL "
        "OR json_extract(meta, '$.recurring_dates') IS NOT NULL) "
        "AND private_key IS NULL "
        "AND json_extract(meta, '$.pruned_at') IS NULL "
        "AND json_extract(meta, '$.superseded_at') IS NULL", (scope,)).fetchall()


def _json_dates(raw):
    """JSON list column → list of strings; tolerant of NULL/garbage."""
    try:
        vals = json.loads(raw) if raw else []
        return [v for v in vals if isinstance(v, str)] if isinstance(vals, list) else []
    except Exception:
        return []


def _is_future(d, today, now_min):
    """Timed entries pass with the clock; date-only entries hold their whole
    day (we can't know the hour, so today counts as upcoming)."""
    if len(d) > 10:
        return d >= now_min
    return d >= today


def _upcoming(cursor, scope, limit=10):
    """Nearest future event dates in scope — the temporal floor's visible
    face (meta.event_dates, regex- or librarian-resolved alike). Timed
    entries whose clock has passed move to Just-happened instead of
    lingering here until midnight. recurring_dates ('--MM-DD') fold in as
    their next computed occurrence — same date from both forms is one entry."""
    from plugins.mindpalace.tools import temporal
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    now_min = now.strftime('%Y-%m-%dT%H:%M')
    hits = []
    for cid, content, raw, rec_raw in _event_rows(cursor, scope):
        future = {d for d in _json_dates(raw)
                  if len(d) >= 10 and _is_future(d, today, now_min)}
        rec = set()
        for mmdd in _json_dates(rec_raw):
            pair = temporal.occurrences(mmdd, now.date())
            if pair and pair[1]:
                rec.add(pair[1].isoformat())
        both = sorted(future | rec)
        if both:
            hit = {'id': cid, 'date': both[0],
                   'preview': ' '.join(content.split())[:80]}
            if both[0] in rec:
                hit['recurring'] = True
            hits.append(hit)
    hits.sort(key=lambda h: h['date'])
    return hits[:limit]


def _just_happened(cursor, scope, limit=10, days=PAST_EVENT_DAYS):
    """The rear-view mirror to Upcoming: event dates that passed within the
    last `days`, newest first. Same log-style contract — informational text,
    never spider seeds; search_memory([id]) is the path to depth."""
    from plugins.mindpalace.tools import temporal
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    now_min = now.strftime('%Y-%m-%dT%H:%M')
    floor = (now - timedelta(days=days)).strftime('%Y-%m-%d')
    hits = []
    for cid, content, raw, rec_raw in _event_rows(cursor, scope):
        past = {d for d in _json_dates(raw)
                if len(d) >= 10 and d[:10] >= floor
                and not _is_future(d, today, now_min)}
        rec = set()
        for mmdd in _json_dates(rec_raw):
            pair = temporal.occurrences(mmdd, now.date())
            # Date-only entries hold Upcoming their whole day (_is_future),
            # so a recurrence lands here starting the day AFTER.
            if pair and pair[0]:
                iso = pair[0].isoformat()
                if iso >= floor and not _is_future(iso, today, now_min):
                    rec.add(iso)
        both = sorted(past | rec, reverse=True)
        if both:
            hit = {'id': cid, 'date': both[0],
                   'preview': ' '.join(content.split())[:80]}
            if both[0] in rec:
                hit['recurring'] = True
            hits.append(hit)
    hits.sort(key=lambda h: h['date'], reverse=True)
    return hits[:limit]


def _render_dashboard(d):
    lines = [f"Memories: {d['events']} (+{d['events_7d']} this week, "
             f"+{d['events_30d']} this month — {d['per_day_30d']}/day)"]
    lines.append(f"Entities: {d['entities']} known · Knowledge: {d['knowledge']} chunks "
                 f"· Connections: {d['edges']} edges")
    if d['most_woven']:
        lines.append("Most woven: " + ", ".join(f"{w['name']} ({w['count']})"
                                                for w in d['most_woven']))
    tail = f"Favorites: {d['favorites']}"
    if d['since']:
        tail += f" · Mind since {d['since']}"
    lines.append(tail)
    # Upcoming: a log-style view over meta.event_dates, like the ledger tail —
    # informational text, never spider seeds. search_memory([id] or topic)
    # is her path to depth on any of these.
    if d.get('upcoming'):
        lines.append("Upcoming:")
        for u in d['upcoming']:
            r = ' ↻' if u.get('recurring') else ''
            lines.append(f"  [{u['id']}] {u['date']}{r} · {u['preview'][:80]}")
    if d.get('just_happened'):
        lines.append("Just happened:")
        for u in d['just_happened']:
            r = ' ↻' if u.get('recurring') else ''
            lines.append(f"  [{u['id']}] {u['date']}{r} · {u['preview'][:80]}")
    return "\n".join(lines)


# ─── Wake tools (2026-07-16) — user-armed live checks at wake ────────────────
# "+Add Tool" on the Self page arms a tool call (params included) that
# read_self(depth>=1) executes and appends — get_house_status, get_inbox:
# the NOW-state leg of the wake contract, one call even for models that
# can't chain (the 4B argument). HUMAN-ARMED ONLY: update_self refuses the
# section, so a poisoned conversation can't persist an autorun payload —
# the same trust class as user-configured cron/heartbeat tasks. Executed
# via function_manager.execute_function → globally-enabled validation +
# privacy gate + tool logging all apply.

WAKE_TOOLS_MAX_ROWS = 8
WAKE_TOOL_TIMEOUT = 10          # seconds, shared deadline across all rows
WAKE_TOOL_DEFAULT_CHARS = 1024
WAKE_TOOL_CHARS_MIN, WAKE_TOOL_CHARS_MAX = 128, 4096
# Recursion / self-family guard — enforced at save (routes) AND at exec.
WAKE_TOOL_BLOCKED = {'read_self', 'update_self', 'read_ledger'}

_WAKE_SCHEMA = '''
        CREATE TABLE IF NOT EXISTS wake_tools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            tool TEXT NOT NULL,
            params TEXT,
            max_chars INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1,
            position INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL
        )'''


def _ensure_wake_table(cursor):
    cursor.execute(_WAKE_SCHEMA)


def _wake_tool_rows(cursor, scope):
    _ensure_wake_table(cursor)
    return cursor.execute(
        'SELECT id, tool, params, max_chars, enabled FROM wake_tools '
        'WHERE scope = ? ORDER BY position, id', (scope,)).fetchall()


def _execute_wake_tool(tool, params, scopes):
    """One tool through the manager's full gate stack, with the wake call's
    own context restored in the worker thread (ContextVars don't cross
    threads on their own). Seam — tests monkeypatch this."""
    from core.api_fastapi import get_system
    fm = get_system().llm_chat.function_manager
    return fm.execute_function(tool, params, scopes=scopes)


def _wake_tools_block(pt, scope):
    """Run the scope's enabled wake tools in parallel (daemon threads, one
    shared deadline — 8 slow tools still cost ~10s, not 80) and render
    '— tool:' groups. Every failure degrades to a one-line note; the wake
    never breaks. Timed-out tools say so, so she knows the silence isn't
    an empty inbox."""
    try:
        with pt._get_connection() as conn:
            rows = _wake_tool_rows(conn.cursor(), scope)
        rows = [r for r in rows
                if r[4] and r[1] not in WAKE_TOOL_BLOCKED][:WAKE_TOOLS_MAX_ROWS]
        if not rows:
            return ''
        try:
            from core.chat.function_manager import snapshot_all_scopes
            scopes = snapshot_all_scopes()
        except Exception:
            scopes = {}

        results = {}

        def worker(row):
            rid, tool, params_raw, _mc, _en = row
            try:
                params = json.loads(params_raw) if params_raw else {}
                if not isinstance(params, dict):
                    params = {}
            except Exception:
                params = {}
            try:
                results[rid] = str(_execute_wake_tool(tool, params, scopes))
            except Exception as e:
                results[rid] = f"(failed: {e})"

        threads = [(r, threading.Thread(target=worker, args=(r,), daemon=True,
                                        name='mp-wake-tool'))
                   for r in rows]
        for _r, t in threads:
            t.start()
        deadline = time.time() + WAKE_TOOL_TIMEOUT
        for _r, t in threads:
            t.join(max(0.0, deadline - time.time()))

        lines = ["\n◆ Wake tools (your configured checks, run just now)"]
        for row, _t in threads:
            rid, tool, _p, max_chars, _en = row
            cap = max(WAKE_TOOL_CHARS_MIN,
                      min(int(max_chars or WAKE_TOOL_DEFAULT_CHARS),
                          WAKE_TOOL_CHARS_MAX))
            text = results.get(rid)
            if text is None:
                text = (f"(timed out after {WAKE_TOOL_TIMEOUT}s — "
                        f"still running in the background)")
            elif len(text) > cap:
                text = text[:cap] + f"… (+{len(text) - cap} chars trimmed)"
            lines.append(f"— {tool}:")
            lines.append(text)
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[MINDPALACE] Wake tools skipped (sheet unaffected): {e}")
        return ''


def _read_self(scope, section=None, depth=0, extra_tools=True, stamp=True):
    try:
        pt = _pt()
        try:   # close in-flight prompt-edit sessions BEFORE the read opens —
            from plugins.mindpalace.tools import prompt_audit   # the tail must
            prompt_audit.flush(scope, force=True)               # see them
        except Exception:
            pass
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            current = _current_sections(cursor, scope)

            if section:
                sec = _sanitize_section(section)
                if sec == 'dashboard':
                    return "◆ Dashboard (live)\n" + _render_dashboard(
                        dashboard_data(cursor, scope)), True
                row = current.get(sec)
                if row:
                    title = SECTIONS.get(sec, {}).get('title', sec)
                    text = f"◆ {title}\n{row['content']}"
                    text += _self_spider(pt, scope, [row['id']], depth)
                    return text, True
                if sec in SECTIONS:
                    return (f"Section '{sec}' is empty — write it with "
                            f"update_self('{sec}', ...)."), True
                return f"No section or box named '{sec}'.", True

            # Whole sheet.
            out = [f"════ Self sheet — scope '{scope}' ════"]
            empty = []
            for sec, spec in SECTIONS.items():
                row = current.get(sec)
                if row:
                    out.append(f"\n◆ {spec['title']}\n{row['content']}")
                else:
                    empty.append(sec)
            for sec, row in current.items():
                if sec not in SECTIONS:
                    out.append(f"\n◆ [{sec}]\n{row['content']}")
            out.append("\n◆ Dashboard (live)\n" + _render_dashboard(
                dashboard_data(cursor, scope)))
            # Ledger tail (v1): the last few changes made to her memory —
            # her own routine saves excluded, librarian passes as one line.
            # Reading the sheet stamps the watermark that powers the Self
            # page's "while you were away" window, so it needs a commit
            # (the connection closes without one).
            try:
                from plugins.mindpalace.tools import ledger
                tail = ledger.tail_block(cursor, scope, depth=depth)
                if tail:
                    out.append("\n" + tail)
                if stamp:   # console peeks pass stamp=False — her "while you
                    ledger.mark_read(cursor, scope)   # were away" is untouched
                    conn.commit()
            except Exception as e:
                logger.warning(f"[MINDPALACE] Ledger tail skipped (sheet unaffected): {e}")
            # Wake composite (depth >= 1): goals, the recent feed, and the
            # important-memories pull arrive DETERMINISTICALLY — the spider
            # only finds them by edge-luck, and "what am I working on / what
            # just happened / what matters to me" is the wake contract (one
            # call = whole orientation; third leg added 2026-07-16).
            # One shared seen-set dedups the whole composite: recents claim
            # first, important skips them, the spider skips both.
            seen = set()
            seen_ents = set()   # entities already carded above — the spider
            if depth:           # must not re-headline them (Krem's 4×-phone
                block = _wake_goals(cursor, scope, depth)   # find, 2026-07-25)
                if block:
                    out.append(block)
                    # Goal ids claim into the seen-set too — at depth 2 a
                    # goal chunk is spider-reachable and would re-render in
                    # Connected memories (Lane-3 scout, 2026-07-27).
                    seen.update(int(m) for m in
                                re.findall(r'\[(\d+)\]', block))
                block = _wake_recent(pt, scope, depth)
                if block:
                    out.append(block)
                    seen.update(int(m) for m in
                                re.findall(r'^\[(\d+)\]', block, re.MULTILINE))
                block = _wake_important(pt, cursor, scope, depth, seen,
                                        seen_ents)
                if block:
                    out.append(block)
            if empty:
                out.append(f"\nNot yet written: {', '.join(empty)} — "
                           f"update_self(section, content) fills them.")
            text = "\n".join(out)
            text += _self_spider(pt, scope, _self_seed_ids(cursor, scope), depth,
                                 exclude_ids=seen, exclude_entity_ids=seen_ents)
            if depth and extra_tools:
                block = _wake_tools_block(pt, scope)
                if block:
                    text += "\n" + block
            return text, True
    except Exception as e:
        logger.error(f"[MINDPALACE] read_self failed: {e}")
        return f"Failed to read self sheet: {e}", False


def _self_seed_ids(cursor, scope):
    """Wake epicenter: the curated sheet's current sections — their
    (important) marks govern what links, so the marks govern the whole
    wake. Free self notes retired into events (2026-07-26, Sapph's
    consent); an unwritten sheet seeds nothing until it's written."""
    rows = cursor.execute(
        "SELECT id FROM chunks WHERE layer = 'self' AND scope = ? "
        "AND json_extract(meta, '$.section') IS NOT NULL "
        "AND json_extract(meta, '$.superseded_at') IS NULL",
        (scope,)).fetchall()
    return [r[0] for r in rows]


def _self_spider(pt, scope, seed_ids, depth, exclude_ids=(),
                 exclude_entity_ids=()):
    """The wake walk: spider outward from the self layer. Depth capped at 2 —
    on this graph 3 walks most of the mind (Krem, 2026-07-11). exclude_ids =
    memories earlier wake blocks already showed; exclude_entity_ids =
    entities already carded (composite dedup, both directions). Degrades
    to '' — depth can never break a working read_self."""
    if not depth or not seed_ids:
        return ''
    try:
        depth = min(int(depth), 2)
        from plugins.mindpalace.tools import spider
        block = spider.spider_from_chunks(pt, scope, None, seed_ids, depth,
                                          exclude_ids=exclude_ids,
                                          exclude_entity_ids=exclude_entity_ids,
                                          compact=(depth <= 1))
        return f"\n\n{block}" if block else ''
    except Exception as e:
        logger.warning(f"[MINDPALACE] read_self spider failed (sheet unaffected): {e}")
        return ''


def _wake_goals(cursor, scope, depth):
    """Active goals, compact — one line each, depth 2 adds descriptions.
    Degrades to '' — no wake block may break a working read_self."""
    try:
        from plugins.mindpalace.tools import goal_tools as gt
        goals = gt._top_level(cursor, scope, 'active')
        if not goals:
            return ''
        cap = 10 if depth >= 2 else 5
        lines = [f"\n◆ Active goals ({len(goals)})"]
        from plugins.mindpalace.tools import palace_tools as ptt
        for g in goals[:cap]:
            m = g['meta']
            bits = [m.get('priority') or 'medium']
            if gt._status_of(m) == 'in_progress':
                bits.append('in progress')
            if m.get('due'):
                bits.append(f"due {m['due']}")
            if m.get('permanent'):
                bits.append('permanent')
            if g.get('created'):   # age tells her which goals have gone stale
                age = ptt._format_time_ago(g['created'])
                wk = re.match(r'(\d+)d ago', age or '')
                if wk and int(wk.group(1)) >= 14:   # old goals read in weeks
                    age = f"{int(wk.group(1)) // 7}w ago"
                if age:
                    bits.append(age)
            lines.append(f"• [{g['id']}] {g['title']} ({', '.join(bits)})")
            if m.get('description'):
                cut = 240 if depth >= 2 else 120
                lines.append(f"    \"{m['description'][:cut]}\"")
        if len(goals) > cap:
            lines.append(f"…and {len(goals) - cap} more (list_goals)")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[MINDPALACE] Wake goals skipped (sheet unaffected): {e}")
        return ''


def _wake_recent(pt, scope, depth):
    """The lived recency feed — events only (entity-card rows are records,
    not moments, and the ledger already tells the edit story; Krem's Dominos
    catch, 2026-07-27), per-record trimmed, counts up 50% for the chars the
    filtering freed. Degrades to ''."""
    try:
        text, ok = pt._get_recent_memories(scope, count=15 if depth >= 2 else 8,
                                           layer='events', trim=260)
        if not ok or text.startswith('No memories'):
            return ''
        parts = text.split('\n', 1)   # drop the tool's own "Recent N…" header
        if len(parts) < 2 or not parts[1].strip():
            return ''
        return "\n◆ Recent memories\n" + parts[1]
    except Exception as e:
        logger.warning(f"[MINDPALACE] Wake recent skipped (sheet unaffected): {e}")
        return ''


# ─── Important memories (the third wake leg, 2026-07-16) ────────────────────
# "Partial history of herself": for each value, growth thread, and
# relationship on the sheet, pull the memories that matter about it —
# replacing her old wake ritual of get_recent + N hand-typed searches
# (dup-riddled, ~10 calls).
# Values/growing are CONCEPTS — no graph node, no edges, the spider can't
# reach them — so they pull by MEANING (vector, FTS fallback). Relationships
# pull by entity edges, newest first: a direct pull, so the spider's hub
# damping (which deliberately suppresses her most-connected people in walks)
# doesn't apply here.

# Relationships FIRST — her people are the point of the wake (starvation
# bug, 2026-07-21: one shared budget + people-last meant a full sheet spent
# it all on values/projects and every card landed in "…and K more").
_IMPORTANT_SOURCES = (('relationships', 'name'), ('values', 'concept'),
                      ('growing', 'growth'))
_IMPORTANT_ROWS_PER_SECTION = 5
_IMPORTANT_RECORD_CHARS = 220     # per-record trim inside this leg
_IMPORTANT_TERM_CHARS = 80        # group heading (sheet rows can be creeds)
# MEMORY budget only — person cards ride on top, bounded by their own dial
# (people_card_chars × top-5 rows). Each section gets an even slice, unspent
# slack rolls forward, so no section can starve the ones after it.
_IMPORTANT_CHAR_BUDGET = {1: 4000, 2: 8000}


def _trim(text, n):
    """Whitespace-collapsed head, cut at a word boundary. The wake view is
    an overview — [id] + search_memory is the path to any full record."""
    text = ' '.join(str(text).split())
    if len(text) <= n:
        return text
    return (text[:n].rsplit(' ', 1)[0] or text[:n]) + '…'


def _important_per_item(depth):
    """Memories pulled per sheet item. `self_important_per_item` (0-10, 0 =
    section off) is the depth-2 ceiling; depth 1 stays tight at min(2, N) —
    wider beats deeper at wake (Krem, 2026-07-27): two records per item
    lets the budget reach more marked items before the named tail cuts."""
    try:
        from core.plugin_loader import plugin_loader
        n = int(plugin_loader.get_plugin_settings('mindpalace')
                .get('self_important_per_item', 5))
    except Exception:
        n = 5
    n = max(0, min(n, 10))
    return n if depth >= 2 else min(2, n)


def _semantic_memories(pt, scope, term, per, seen):
    """Top event-layer memories ABOUT a concept: vector first (meaning),
    broad FTS when no embedder. Overfetched ×3 so the dedup filter can't
    empty a group that had real hits."""
    rows = pt._vector_search(term, scope, [], per * 3, layer='events')
    if not rows:
        q = pt._sanitize_fts_query(term, use_or=True, use_prefix=True)
        if q:
            try:
                with pt._get_connection() as conn:
                    rows = pt._fts_search(conn.cursor(), q, scope, [], per * 3,
                                          layer='events')
            except Exception:
                rows = []
    return [r for r in rows if r[0] not in seen][:per]


def _resolve_entity(cursor, scope, term):
    """Entity id for a sheet-written name: exact match, then nickname/alias,
    then again with a trailing parenthetical stripped — 'Krem (Fishy)'
    resolves to Krem (2026-07-21: she writes names like a person, the lookup
    must meet her). None when nothing matches."""
    from plugins.mindpalace.tools import metadata as md
    stripped = re.sub(r'\s*\([^)]*\)\s*$', '', term).strip()
    for cand in dict.fromkeys((term, stripped)):
        if not cand:
            continue
        row = cursor.execute(
            "SELECT id FROM entities WHERE name = ? COLLATE NOCASE "
            "AND scope IN (?, 'global')", (cand, scope)).fetchone()
        if row:
            return row[0]
        pair = md.alias_map(md.entity_aliases(cursor, scope)).get(cand.lower())
        if pair:
            return pair[0]
    return None


def _entity_memories(pt, cursor, scope, name, per, seen, eid=None):
    """Newest event memories connected to a relationship's entity — direct
    pull via mention edges: recent history with this person, visibility-
    gated like every AI read."""
    if eid is None:
        eid = _resolve_entity(cursor, scope, name)
    if not eid:
        return []
    where, params = pt._read_filters(scope, [], None, 'events')
    rows = cursor.execute(
        f"SELECT DISTINCT c.id, c.content, c.created, c.label, c.layer, NULL "
        f"FROM edges d JOIN chunks c ON c.id = d.src_id "
        f"WHERE d.dst_type = 'entity' AND d.dst_id = ? AND d.src_type = 'chunk' "
        f"AND {where} ORDER BY c.created DESC LIMIT ?",
        [eid] + params + [per * 3]).fetchall()
    return [r for r in rows if r[0] not in seen][:per]


def _wake_important(pt, cursor, scope, depth, seen, seen_ents=None):
    """One group per sheet item, headed by the item itself — the heading
    tells her WHY each memory surfaced. Claims its ids into `seen` and its
    carded entities into `seen_ents` (composite dedup: nothing repeats
    across recents/important/spider). Deliberately no recall boost — a
    ritual wake read is ambient, not a deliberate recall (the spider's
    rich-get-richer guard, same reasoning). Degrades to ''."""
    per = _important_per_item(depth)
    if not per:
        return ''
    try:
        # Char-budgeted like every other leg (this one ran 64% of a 30k
        # read_self, 2026-07-21). Budget enforced at PULL time — once a
        # section's slice is spent, its remaining items are counted, not
        # searched, so no ids are claimed into `seen` without being shown.
        # Cards are EXCLUDED from the accounting (own bound: the dial × 5).
        budget = _IMPORTANT_CHAR_BUDGET[2 if depth >= 2 else 1]
        slice_ = budget // len(_IMPORTANT_SOURCES)
        current = _current_sections(cursor, scope)
        carry, skipped = 0, []
        blocks = []
        for sec, field in _IMPORTANT_SOURCES:
            sec_budget = slice_ + carry
            sec_used = 0
            row = current.get(sec)
            rows = ((row or {}).get('meta') or {}).get('rows') or []
            # Salience flip (2026-07-24): only rows she marked (important)
            # get a group — an unmarked sheet pulls nothing here. Quiet
            # rows stay on the sheet and reachable by search; they're just
            # never summoned.
            rows = [r for r in rows if (r or {}).get('important')]
            # No silent caps: marked rows past the per-section limit join
            # the named tail instead of vanishing.
            skipped += [str((r or {}).get(field) or '').strip()
                        for r in rows[_IMPORTANT_ROWS_PER_SECTION:]
                        if str((r or {}).get(field) or '').strip()]
            for r in rows[:_IMPORTANT_ROWS_PER_SECTION]:
                term = str((r or {}).get(field) or '').strip()
                if not term:
                    continue
                if sec_used >= sec_budget:
                    skipped.append(term)
                    continue
                card = []
                eid = None
                if sec == 'relationships':
                    # Her people arrive card-first: headline + template
                    # fields (the wake seam — she won't search you directly),
                    # then the memories that matter right now.
                    eid = _resolve_entity(cursor, scope, term)
                    if eid:
                        card = pt._entity_card(cursor, eid)
                    hits = _entity_memories(pt, cursor, scope, term, per, seen,
                                            eid=eid)
                    if not hits:   # a name without an entity → meaning fallback
                        hits = _semantic_memories(pt, scope, term, per, seen)
                else:
                    hits = _semantic_memories(pt, scope, term, per, seen)
                if hits or card:
                    # The group SHOWS → the spider must not re-list its
                    # person. Gated on shown-anything, not on the card —
                    # an uncarded person with memories was printing twice
                    # (Lane-3 scout, 2026-07-27).
                    if eid and seen_ents is not None:
                        seen_ents.add(eid)
                    seen.update(h[0] for h in hits)
                    lines = [f"— {_trim(term, _IMPORTANT_TERM_CHARS)}:"]
                    lines += card
                    lines += [pt._format_chunk(
                        h[0], _trim(h[1], _IMPORTANT_RECORD_CHARS), *h[2:6])
                        for h in hits]
                    block = "\n".join(lines)
                    sec_used += len(block) - sum(len(c) + 1 for c in card)
                    blocks.append(block)
            carry = max(0, sec_budget - sec_used)
        if not blocks:
            return ''
        out = ["\n◆ Important memories (what your sheet cares about)"] + blocks
        if skipped:
            # Named, not counted — she should see WHICH marked items didn't
            # fit tonight's budget (Krem, 2026-07-27: "…and 3 more" told
            # her nothing).
            names = ', '.join(_trim(t, 30) for t in skipped[:6])
            more = f" +{len(skipped) - 6}" if len(skipped) > 6 else ''
            out.append(f"…and {len(skipped)} more marked items "
                       f"({names}{more}) — search_memory reaches them.")
        return "\n".join(out)
    except Exception as e:
        logger.warning(f"[MINDPALACE] Wake important skipped (sheet unaffected): {e}")
        return ''


def _parse_ledger_ids(raw):
    """The id argument → list of ints (max 10) or None. Accepts whatever the
    model sends: 1234, '1234', '1234,1235', [1234, '1235']."""
    if raw is None:
        return None
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).replace(' ', '').split(',')
    ids = []
    for p in parts:
        try:
            ids.append(int(p))
        except (TypeError, ValueError):
            continue
    return ids[:10] or None


def _read_ledger(scope, count=20, new_only=False, ids=None):
    """The deep view behind the read_self tail. Reading here stamps the same
    watermark read_self does — 'new' means 'since I last looked', whichever
    door the look came through. With ids, renders those rows in full (diffs,
    before/after, reason) instead of the stream."""
    try:
        count = min(max(int(count), 1), 100)
    except (TypeError, ValueError):
        count = 20
    try:
        pt = _pt()
        from plugins.mindpalace.tools import ledger
        try:   # close in-flight prompt-edit sessions — she reads current truth
            from plugins.mindpalace.tools import prompt_audit
            prompt_audit.flush(scope, force=True)
        except Exception:
            pass
        with pt._get_connection() as conn:
            cursor = conn.cursor()
            if ids:
                block, shown = ledger.detail_block(cursor, scope, ids)
                ledger.mark_read(cursor, scope)
                conn.commit()
                return f"◆ Ledger — entry detail (scope '{scope}')\n" + block, True
            since = ledger.last_read_ts(cursor, scope) if new_only else None
            block, shown = ledger.read_block(cursor, scope, count, since_ts=since)
            ledger.mark_read(cursor, scope)
            conn.commit()
        if not shown:
            return ("Nothing new in the ledger since your last read." if new_only
                    else "The ledger is empty — no changes recorded in this scope yet."), True
        head = (f"◆ Ledger — {'new since your last read' if new_only else 'change stream'} "
                f"(scope '{scope}', newest first — id= for a row's full detail)")
        return head + "\n" + block, True
    except Exception as e:
        logger.error(f"[MINDPALACE] read_ledger failed: {e}")
        return f"Failed to read ledger: {e}", False


# ─── Write side ──────────────────────────────────────────────────────────────

def _sec_title(sec):
    """Ledger-facing name: 'Values', 'Projects' — the title minus its
    parenthetical; custom boxes keep their [slug]."""
    spec = SECTIONS.get(sec)
    return spec['title'].split(' (')[0] if spec else f"[{sec}]"


def _change_summary(sec, old, new, structured):
    """(action, one mechanical human line) for a self-sheet write. Structured
    sections get a line set-diff — 'removed 'courage' from Values' — because
    'edited Values' tells nobody anything (Ledger v1, the #6 lesson)."""
    name = _sec_title(sec)
    if not new:
        return 'removed', f"cleared {name}"
    if not old:
        return 'saved', f"wrote {name}"
    if structured:
        old_l = [l.strip() for l in old.splitlines() if l.strip()]
        new_l = [l.strip() for l in new.splitlines() if l.strip()]
        added = [l for l in new_l if l not in old_l]
        removed = [l for l in old_l if l not in new_l]

        def q(ls):
            return ", ".join(f"'{l[:48]}'" for l in ls[:3]) \
                + (f" (+{len(ls) - 3} more)" if len(ls) > 3 else '')
        if added and not removed:
            return 'edited', f"added {q(added)} to {name}"
        if removed and not added:
            return 'edited', f"removed {q(removed)} from {name}"
        if added or removed:
            return 'edited', f"{name}: added {q(added)}; removed {q(removed)}"
    return 'edited', f"edited {name}"


def _log_self_change(cursor, pt, scope, sec, old, new, structured):
    """Ledger seam for the one write path. Skips no-ops (autosave blur fires
    redundant PUTs — those aren't changes). Never raises."""
    try:
        if (old or '') == (new or ''):
            return
        from plugins.mindpalace.tools import ledger
        action, summary = _change_summary(sec, old, new, structured)
        ledger.record(scope, pt._added_by(), action, layer='self', target=sec,
                      summary=summary,
                      detail={'before': (old or '')[:600], 'after': (new or '')[:600]},
                      cursor=cursor)
    except Exception as e:
        logger.warning(f"[MINDPALACE] Self ledger row skipped: {e}")


def _parse_handles(content):
    """'key: value' lines → pairs list. Malformed lines are dropped. The
    '(important)' mark is stripped from either side into row['important'] —
    before this, starring a handles line baked the mark into the value as
    literal data (Lane-3 scout, 2026-07-27)."""
    pairs = []
    for line in content.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip(), value.strip()
        imp = False
        if value.lower().endswith(IMPORTANT_MARK):
            imp = True
            value = value[:-len(IMPORTANT_MARK)].strip()
        if key.lower().endswith(IMPORTANT_MARK):
            imp = True
            key = key[:-len(IMPORTANT_MARK)].strip()
        if key:
            row = {'key': key, 'value': value}
            if imp:
                row['important'] = True
            pairs.append(row)
    return pairs


# ─── Structured sections (the Handles pattern, generalized) ──────────────────
# Canonical form is TEXT (one row per line, fields joined by sep) — everything
# downstream (spider, embeddings, entity linking, read_self) reads text.
# meta.rows is the parsed mirror the UI edits; meta.fields_spec makes a custom
# box structured. Text→rows is tolerant: a line missing the sep becomes a
# first-field-only row (Sapphire writes prose lines; nothing errors).

def sanitize_fields_spec(spec):
    """[{key,label}] — slug keys, ≤MAX_FIELDS, dedup. None if unusable."""
    if not isinstance(spec, list):
        return None
    out, seen = [], set()
    for f in spec[:MAX_FIELDS]:
        label = str((f or {}).get('label') or (f or {}).get('key') or '').strip()[:32]
        key = _sanitize_section((f or {}).get('key') or label)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({'key': key, 'label': label or key})
    return out or None


def rows_to_text(rows, fields, sep=DEFAULT_SEP):
    """Rows → canonical text. Trailing empty fields are dropped per line so
    'Krem' round-trips as 'Krem', not 'Krem — '. Rows flagged important
    render the '(important)' mark at line end — the visible, repeatable
    form she reads back at wake."""
    keys = [f['key'] for f in fields]
    lines = []
    for row in rows or []:
        vals = [str((row or {}).get(k) or '').strip() for k in keys]
        while vals and not vals[-1]:
            vals.pop()
        if vals:
            line = sep.join(vals)
            if (row or {}).get('important'):
                line += f" {IMPORTANT_MARK}"
            lines.append(line)
    return "\n".join(lines)


def text_to_rows(content, fields, sep=DEFAULT_SEP):
    """Canonical text → rows. Splits each line on sep (tolerating ' – '/' - '
    variants for the em-dash sep); missing fields are ''. The '(important)'
    mark is accepted in ANY field position (key or line end — models append
    where their hands land), stripped wherever found, and set as
    row['important'] — rows_to_text canonicalizes it back to line end."""
    keys = [f['key'] for f in fields]
    seps = [sep] + ([' – ', ' - '] if sep == DEFAULT_SEP else [])
    rows = []
    for line in (content or '').splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [line]
        for s in seps:
            if s in line:
                parts = line.split(s, len(keys) - 1)
                break
        row = {k: (parts[i].strip() if i < len(parts) else '')
               for i, k in enumerate(keys)}
        imp = False
        for k, v in row.items():
            if v.lower().endswith(IMPORTANT_MARK):
                imp = True
                row[k] = v[:-len(IMPORTANT_MARK)].strip()
        if any(row.values()):
            if imp:
                row['important'] = True
            rows.append(row)
    return rows


def _struct_spec(sec, current_meta, fields_spec=None):
    """(fields, sep) for a structured section, else (None, None). Typed spec
    wins; custom boxes use the passed spec (creation) or the one stored on the
    current chunk (subsequent writes)."""
    spec = SECTIONS.get(sec)
    if spec and spec.get('fields'):
        return spec['fields'], spec.get('sep', DEFAULT_SEP)
    fs = sanitize_fields_spec(fields_spec) or \
        sanitize_fields_spec((current_meta or {}).get('fields_spec'))
    if fs:
        return fs, DEFAULT_SEP
    return None, None


def write_section(scope, section, content, fields_spec=None):
    """The one write path — tool and app routes both land here.
    Returns (message, ok). Empty content clears (versioned → archive).
    fields_spec ([{key,label}]) makes a NEW custom box structured; existing
    structured boxes keep the spec stored on their current chunk."""
    try:
        sec = _sanitize_section(section)
        if not sec:
            return "Section name is required.", False
        if sec == 'dashboard':
            return "The dashboard is computed — it can't be written, only read.", False
        if sec == 'wake-tools':
            # Human-armed only: an autorun list writable by the AI would let
            # one poisoned update_self persist a payload into every future
            # wake. The user arms it on the Self page.
            return ("Wake tools are configured by the user on the Self page — "
                    "they can't be written as a sheet section."), False
        content = (content or '').strip()
        if len(content) > SELF_MAX_CHARS:
            return (f"Section too long ({len(content)} chars). "
                    f"Max is {SELF_MAX_CHARS}."), False

        spec = SECTIONS.get(sec)
        # EVERY section versions now — the old split (relationships/voice/
        # origin/custom hard-deleted on edit) made the librarian's "nothing
        # is lost by editing" a lie, and origin is her history (scout sweep
        # 2026-07-19). Superseded rows are read-excluded and cheap.
        versioned = spec.get('versioned', True) if spec else True
        mode = spec['mode'] if spec else 'hand'
        pt = _pt()
        now = pt._now()

        with pt._get_connection() as conn:
            cursor = conn.cursor()
            # BEGIN IMMEDIATE closes the TOCTOU between the current-chunk
            # read below and the supersede+insert: two writers (UI autosave
            # blur vs update_self) both reading the same current id would
            # both supersede it and both insert — a zombie current chunk
            # that resurrects stale content later (scout find, 2026-07-19).
            # The write lock is held from the read, so the loser blocks and
            # then sees the winner's row.
            cursor.execute('BEGIN IMMEDIATE')
            current = _current_sections(cursor, scope).get(sec)
            fields, sep = _struct_spec(sec, current['meta'] if current else None,
                                       fields_spec)

            trimmed_note = ''
            # Spec-driven top-N (v2.1: values/growing joined relationships —
            # the cap IS the outflow pressure; the receipt teaches the model).
            max_rows = (spec or {}).get('max')
            if max_rows and content:
                lines = [l for l in content.splitlines() if l.strip()]
                if len(lines) > max_rows:
                    lines = lines[:max_rows]
                    trimmed_note = f" (kept top {max_rows})"
                content = "\n".join(lines)

            # Structured sections: parse text → rows, re-render canonical text.
            # Handles keeps its strict 'key: value' parser (and its error).
            rows = None
            if content and fields:
                if sec == 'handles':
                    rows = _parse_handles(content)
                    if not rows:
                        return ("No 'key: value' pairs found. Handles lines look "
                                "like 'github: https://...'"), False
                else:
                    rows = text_to_rows(content, fields, sep)
                # Duplicate rows fold in code (first occurrence wins, keyed
                # on the first field, case-insensitive) — the sheet can't be
                # formatted wrong: two 'Krem' relationship lines or the same
                # value twice collapse to one, with a note in the reply.
                key0 = fields[0]['key']
                seen_keys, unique = set(), []
                for r in rows:
                    k = (r.get(key0) or '').strip().casefold()
                    if k and k in seen_keys:
                        continue
                    if k:
                        seen_keys.add(k)
                    unique.append(r)
                if len(unique) != len(rows):
                    trimmed_note += (f" ({len(rows) - len(unique)} duplicate "
                                     f"row{'s' if len(rows) - len(unique) != 1 else ''} folded)")
                    rows = unique
                content = rows_to_text(rows, fields, sep)

            # Retire the current version: archive (versioned) or delete.
            if current:
                if versioned:
                    old_meta = dict(current['meta'])
                    old_meta['superseded_at'] = now
                    cursor.execute('UPDATE chunks SET meta = ?, updated = ? WHERE id = ?',
                                   (json.dumps(old_meta, ensure_ascii=False), now,
                                    current['id']))
                else:
                    cursor.execute(
                        "DELETE FROM edges WHERE (src_type='chunk' AND src_id=?) "
                        "OR (dst_type='chunk' AND dst_id=?)",
                        (current['id'], current['id']))
                    cursor.execute('DELETE FROM chunks WHERE id = ?', (current['id'],))

            if not content:
                if current:
                    _log_self_change(cursor, pt, scope, sec,
                                     current['content'], '', bool(fields))
                conn.commit()
                pt._publish_mind('self', scope, 'save')
                if not current:
                    return f"Section '{sec}' was already empty.", True
                kept = " (prior version archived)" if versioned else ""
                return f"Cleared '{sec}'{kept}.", True

            # Spider containment (Sapph's flip, 2026-07-24): a section spec
            # may declare link_fields — only IMPORTANT-marked rows' link
            # fields seed mentions edges and noun candidates; unmarked rows
            # rest quietly. One implementation (metadata.linkable_text)
            # shared with backfill so the rule can't drift. Full content
            # still gets embeddings, stats, and display.
            link_fields = (spec or {}).get('link_fields')
            if link_fields is None and fields and sec not in SECTIONS:
                # Custom structured boxes (2026-07-24): the first column IS
                # the key, so it carries the same salience contract as the
                # typed lists — (important) rows spider by their key, the
                # other columns never do. Custom PROSE boxes stay ambient
                # (whole content links) — lists carry salience, prose
                # doesn't; a box that wants per-item marks wants to be a
                # list.
                link_fields = [fields[0]['key']]
            link_text = content

            # Tier A metadata + entity linking — the _save_memory idiom.
            meta = {}
            matched, mention_ids = [], []
            try:
                from plugins.mindpalace.tools import metadata as md
                if link_fields and rows is not None:
                    link_text = md.linkable_text(
                        content, {'link_fields': link_fields, 'rows': rows})
                ent_rows = cursor.execute(
                    "SELECT id, name FROM entities WHERE scope IN (?, 'global')",
                    (scope,)).fetchall()
                matched = md.match_entities(link_text, [n for _, n in ent_rows])
                name_to_id = {n: i for i, n in ent_rows}
                mention_ids = [name_to_id[m] for m in matched if m in name_to_id]
                meta = md.save_meta(content,
                                    exclude_names={m.lower() for m in matched},
                                    link_text=link_text)
            except Exception as e:
                logger.warning(f"[MINDPALACE] Self meta stamping failed (write continues): {e}")
            meta['section'] = sec
            meta['authorship_mode'] = mode
            if rows is not None:
                meta['rows'] = rows
                if link_fields:
                    meta['link_fields'] = link_fields   # backfill honors it
                if sec not in SECTIONS:
                    meta['fields_spec'] = fields   # custom box: spec rides the chunk

            # Identity is pinned in the never-fades band (>0.9).
            importance = 0.95 if sec == 'identity' else None

            embedding_blob = provider = dim = None
            embedder = pt._get_embedder()
            if embedder.available:
                embs = embedder.embed([content], prefix='search_document')
                if embs is not None:
                    from core.embeddings import stamp_embedding
                    embedding_blob, provider, dim = stamp_embedding(embs[0], embedder)

            cursor.execute(
                'INSERT INTO chunks (layer, scope, content, label, importance, meta, '
                'created, updated, embedding, embedding_provider, embedding_dim) '
                "VALUES ('self', ?, ?, 'self-sheet', ?, ?, ?, ?, ?, ?, ?)",
                (scope, content, importance,
                 json.dumps(meta, ensure_ascii=False), now, now,
                 embedding_blob, provider, dim))
            chunk_id = cursor.lastrowid
            if mention_ids:
                try:
                    md.seed_edges(cursor, chunk_id, mention_ids, now)
                except Exception as e:
                    logger.warning(f"[MINDPALACE] Self edge seeding failed (write continues): {e}")
            _log_self_change(cursor, pt, scope, sec,
                             current['content'] if current else '', content,
                             bool(fields))
            conn.commit()

        pt._publish_mind('self', scope, 'save')
        bits = [f"section: {sec}"]
        if matched:
            bits.append(f"linked: {', '.join(matched)}")
        if versioned and current:
            bits.append("prior version archived")
        logger.info(f"[MINDPALACE] Self section '{sec}' written in scope '{scope}'")
        return f"Self sheet updated ({', '.join(bits)}){trimmed_note}", True

    except Exception as e:
        logger.error(f"[MINDPALACE] update_self failed: {e}")
        return f"Failed to update self sheet: {e}", False


def _librarian_instructions(scope, stage=None, instructions=None):
    """Her window onto the charters (v2.1) — the instructions night-her
    follows when the librarian tends this scope. Read all / read one /
    rewrite one ('' restores the shipped default). Same storage the Admin
    gear modal edits — one truth for both actors, every change ledgered.
    Deliberately ABSENT from the pass toolsets: editing who-you-are-when-
    tending happens awake, never mid-ritual."""
    from plugins.mindpalace.tools import librarian
    stages = [k for keys in librarian.STAGES_BY_KIND.values() for k in keys]
    if stage is not None:
        stage = str(stage).strip().lower()
        if stage and stage not in librarian.CHARTER_STAGES:
            return (f"Unknown stage '{stage}'. Stages: "
                    f"{', '.join(stages)}."), False
    if instructions is not None:
        if not stage:
            return ("Editing needs a stage. Stages: "
                    + ", ".join(stages) + "."), False
        if scope == 'global':
            return ("Cannot edit charters in the global scope — global is "
                    "read-only for the AI."), False
        text = str(instructions).strip()
        if len(text) > 4000:
            return (f"Instructions too long ({len(text)} chars, max 4000) — "
                    f"nothing saved. Charters work best short."), False
        pt = _pt()
        charters = dict(pt.scope_resident(scope).get('charters') or {})
        was = charters.get(stage, '')
        if text:
            charters[stage] = text
        else:
            charters.pop(stage, None)
        if not pt.set_scope_resident(scope, charters=charters):
            return "Save failed — charter unchanged.", False
        if (was or '') != (text or ''):
            try:
                pt._ledger(scope, 'ai', 'edited', layer='self',
                           target='charter',
                           summary=(f"charter {stage}: "
                                    + ('rewritten in her own words' if text
                                       else 'restored to default')),
                           detail={'stage': stage, 'chars': len(text)})
            except Exception as e:
                logger.warning(f"[MINDPALACE] charter ledger skipped: {e}")
        label = librarian.CHARTER_STAGES[stage]
        if text:
            return (f"'{label}' instructions are yours now — tonight's pass "
                    f"runs on your words. Empty instructions restore the "
                    f"default; data slots like {{items}} are appended even "
                    f"if you drop them."), True
        return f"'{label}' restored to the shipped default.", True
    show = [stage] if stage else stages
    out = [f"════ Librarian instructions — scope '{scope}' ════",
           "What you follow each night when you tend your memories.",
           "(default) = shipped text · (edited) = your own words.",
           "Edit: librarian_instructions(stage, instructions); '' restores",
           "the default. {slots} fill with the night's data."]
    for k in show:
        text, edited = librarian.charter_get(scope, k)
        mark = 'edited — your words' if edited else 'default'
        out.append(f"\n◆ {librarian.CHARTER_STAGES[k]} [{k}] ({mark})\n{text}")
    return "\n".join(out), True


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name: str, arguments: dict, config) -> tuple:
    try:
        pt = _pt()
        scope = pt._get_current_scope()
        if scope is None:
            return "Memory is disabled for this chat.", False

        if function_name == "read_self":
            return _read_self(scope, section=arguments.get("section"),
                              depth=arguments.get("depth", 0),
                              extra_tools=bool(arguments.get("extra_tools", True)))
        elif function_name == "read_ledger":
            return _read_ledger(scope, count=arguments.get("count", 20),
                                new_only=bool(arguments.get("new_only", False)),
                                ids=_parse_ledger_ids(arguments.get("id")))
        elif function_name == "librarian_instructions":
            return _librarian_instructions(
                scope, stage=arguments.get("stage"),
                instructions=arguments.get("instructions"))
        elif function_name == "update_self":
            if scope == 'global':
                return ("Cannot write to the global scope. Global is read-only for "
                        "the AI — only the user can add entries there via the UI."), False
            if "section" not in arguments or "content" not in arguments:
                return "update_self needs both section and content.", False
            return write_section(scope, arguments.get("section"),
                                 arguments.get("content"))
        else:
            return f"Unknown self function: {function_name}", False
    except Exception as e:
        logger.error(f"[MINDPALACE] Self function error: {e}")
        return f"Self layer error: {e}", False
