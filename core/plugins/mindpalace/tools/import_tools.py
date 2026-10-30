# core/plugins/mindpalace/tools/import_tools.py
# v2 → v3 memory importer. Copies the classic memory system's three DBs
# (memory.db, knowledge.db) into the mind palace's single mind.db, mapping
# every old row onto the chunk/entity model.
#
# HARD contract:
#   - Source DBs are opened READ-ONLY (mode=ro URI). They are NEVER written.
#     Old data stays put as the guaranteed switch-back path (see palace_tools
#     module header). This importer is additive-only on the destination side.
#   - Idempotent: every copied chunk records meta.import_key = "v2:<store>:<id>".
#     A single up-front query loads all existing keys into a set; rows whose key
#     is already present are skipped. Re-running copies zero duplicates.
#   - Scope-independent: copies ALL scopes verbatim, including 'global'. The
#     global write-block that gates the AI's own writes does NOT apply here —
#     import is a bulk copy, not an AI write.
#   - Provenance triple is all-or-nothing: (embedding, embedding_provider,
#     embedding_dim) travel together byte-for-byte, or all three land NULL and
#     the backfill re-embeds later with the correct 'search_document' prefix.
#   - Timestamps are normalized to one ISO-8601 UTC-with-offset format (the
#     format palace_tools._now() emits) so lexicographic ORDER BY stays
#     chronological across imported + native rows.

import json
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📦'
TOOL_CATEGORY = 'memory'
GROUP = 'Mind Palace'   # Toolsets UI merges same-GROUP modules

AVAILABLE_FUNCTIONS = ['import_v2']

TOOLS = [
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "import_v2",
            "description": (
                "Copy the classic memory system's data (memories, people, knowledge, goals) into the mind palace, ALL"
                " scopes. The old DBs are read-only and untouched. Safe to re-run: imported rows are skipped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {
                        "type": "string",
                        "enum": ["memories", "people", "knowledge", "goals", "all"],
                        "description": "Default all",
                        "default": "all"
                    }
                }
            }
        }
    },
]

COMMIT_EVERY = 500


# ─── Source DB paths (anchored like palace_tools / classic memory plugin) ─────

def _source_path(config, filename: str) -> Path:
    return Path(config.__file__).parent / "user" / filename


def _connect_ro(path: Path):
    """Open a source DB strictly read-only. mode=ro forbids any write; if the
    file is missing sqlite raises OperationalError, which the caller treats as
    'store absent' (skip, not error)."""
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def _columns(cursor, table: str) -> set:
    """Column-name set for a table via PRAGMA. Empty set = table absent."""
    try:
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}
    except sqlite3.DatabaseError:
        return set()


def _get(row: dict, *names):
    """First present, non-None value among candidate column names."""
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


# ─── Timestamp normalization ──────────────────────────────────────────────────

def _normalize_ts(raw):
    """Return (iso_utc_str, original_or_None).

    Rules (the old system mixed formats; the palace requires uniform ISO-8601
    UTC with offset):
      - 'YYYY-MM-DD HH:MM:SS[.ffffff]' (SPACE separator) = SQLite
        CURRENT_TIMESTAMP = already UTC, naive → attach UTC → isoformat.
      - 'T'-separated naive = legacy Python local-time isoformat → interpret in
        the system local timezone → convert to UTC.
      - tz-aware anything → convert to UTC.
      - unparseable / NULL → palace _now(), and hand back the original string so
        the caller can record it in meta.original_timestamp.
    """
    from plugins.mindpalace.tools import palace_tools

    if raw is None:
        return palace_tools._now(), None

    s = str(raw).strip()
    if not s:
        return palace_tools._now(), str(raw)

    space_sep = ('T' not in s) and (' ' in s)
    try:
        # fromisoformat handles both ' ' and 'T' separators and optional offset.
        dt = datetime.fromisoformat(s)
    except ValueError:
        return palace_tools._now(), s

    if dt.tzinfo is not None:
        # Already tz-aware → normalize to UTC.
        dt = dt.astimezone(timezone.utc)
    elif space_sep:
        # SQLite CURRENT_TIMESTAMP: UTC wall-clock, just missing the tzinfo.
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # 'T'-separated naive: legacy local-time isoformat. Interpret in the
        # system local zone, then convert to UTC.
        dt = dt.astimezone(timezone.utc)

    return dt.isoformat(timespec='seconds'), None


# ─── Provenance triple (all-or-nothing) ──────────────────────────────────────

def _provenance(row: dict):
    """Carry (embedding, embedding_provider, embedding_dim) byte-for-byte only
    if all three are present; otherwise all three NULL (backfill re-embeds)."""
    emb = _get(row, 'embedding')
    prov = _get(row, 'embedding_provider')
    dim = _get(row, 'embedding_dim')
    if emb is not None and prov is not None and dim is not None:
        return emb, prov, dim
    return None, None, None


# ─── Insert helper ────────────────────────────────────────────────────────────

def _insert_chunk(cursor, *, layer, scope, content, created, updated,
                  entity_id=None, tier=None, chunk_index=None, source=None,
                  label=None, private_key=None, meta=None, importance=None,
                  embedding=None, embedding_provider=None, embedding_dim=None):
    cursor.execute(
        'INSERT INTO chunks (layer, scope, content, entity_id, tier, chunk_index, '
        'source, label, private_key, meta, importance, created, updated, '
        'embedding, embedding_provider, embedding_dim) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (layer, scope, content, entity_id, tier, chunk_index,
         source, label, private_key, meta, importance, created, updated,
         embedding, embedding_provider, embedding_dim)
    )


# ─── Per-store importers ──────────────────────────────────────────────────────

def _import_memories(config, dest_cursor, existing_keys, scopes_seen, skip_keys=frozenset()):
    """memories → chunks(layer='events'). Returns (copied, skipped, failed)."""
    path = _source_path(config, "memory.db")
    if not path.exists():
        return None  # signals "source absent"

    copied = skipped = failed = 0
    with _connect_ro(path) as src:
        src.row_factory = sqlite3.Row
        scur = src.cursor()
        cols = _columns(scur, 'memories')
        if not cols:
            return (0, 0, 0)

        select_cols = [c for c in (
            'id', 'content', 'timestamp', 'scope', 'label', 'private_key',
            'embedding', 'embedding_provider', 'embedding_dim'
        ) if c in cols]
        scur.execute(f"SELECT {', '.join(select_cols)} FROM memories")

        pending = 0
        for r in scur.fetchall():
            row = {k: r[k] for k in r.keys()}
            old_id = row.get('id')
            key = f"v2:memories:{old_id}"
            if key in skip_keys:
                continue
            if key in existing_keys:
                skipped += 1
                continue

            content = row.get('content')
            if content is None or not str(content).strip():
                failed += 1
                continue

            scope = row.get('scope') or 'default'
            scopes_seen.add(scope)
            label = row.get('label')
            private_key = row.get('private_key')
            created, original = _normalize_ts(row.get('timestamp'))
            emb, prov, dim = _provenance(row)

            meta = {"import_key": key}
            if original is not None:
                meta["original_timestamp"] = original

            try:
                _insert_chunk(
                    dest_cursor, layer='events', scope=scope, content=content,
                    created=created, updated=created, label=label,
                    private_key=private_key, source='memory v2',
                    meta=json.dumps(meta),
                    embedding=emb, embedding_provider=prov, embedding_dim=dim,
                )
            except Exception as e:
                logger.error(f"[MINDPALACE] import memory {old_id} failed: {e}")
                failed += 1
                continue

            existing_keys.add(key)
            copied += 1
            pending += 1
            if pending >= COMMIT_EVERY:
                dest_cursor.connection.commit()
                pending = 0

    return (copied, skipped, failed)


def _import_people(config, dest_cursor, existing_keys, scopes_seen, skip_keys=frozenset()):
    """people → entities row + one tier-1 entities-layer chunk each.
    Returns (copied, skipped, failed)."""
    from plugins.mindpalace.tools import palace_tools

    path = _source_path(config, "knowledge.db")
    if not path.exists():
        return None

    copied = skipped = failed = 0
    with _connect_ro(path) as src:
        src.row_factory = sqlite3.Row
        scur = src.cursor()
        cols = _columns(scur, 'people')
        if not cols:
            return (0, 0, 0)

        scur.execute("SELECT * FROM people")
        pending = 0
        for r in scur.fetchall():
            row = {k: r[k] for k in r.keys()}
            old_id = row.get('id')
            key = f"v2:people:{old_id}"
            if key in skip_keys:
                continue
            if key in existing_keys:
                skipped += 1
                continue

            name = _get(row, 'name')
            if not name or not str(name).strip():
                failed += 1
                continue
            name = str(name).strip()
            scope = row.get('scope') or 'default'
            scopes_seen.add(scope)

            # Build the person's descriptive content from whatever text columns
            # exist, in a stable order. Whitelist flags + ids go to meta, not body.
            content_fields = [
                ('relationship', 'Relationship'),
                ('phone', 'Phone'),
                ('email', 'Email'),
                ('address', 'Address'),
                ('notes', 'Notes'),
                ('description', 'Description'),
            ]
            parts = []
            for col, label_txt in content_fields:
                val = _get(row, col)
                if val is not None and str(val).strip():
                    parts.append(f"{label_txt}: {str(val).strip()}")
            content = "\n".join(parts) if parts else name

            # meta bag: whitelist flags + any leftover columns we didn't map.
            meta = {"import_key": key}
            handled = {'id', 'name', 'scope', 'embedding', 'embedding_provider',
                       'embedding_dim', 'relationship', 'phone', 'email',
                       'address', 'notes', 'description', 'created_at', 'updated_at'}
            for col in cols:
                if col in handled:
                    continue
                val = row.get(col)
                if val is not None:
                    meta[col] = val

            created, original = _normalize_ts(_get(row, 'created_at', 'updated_at'))
            if original is not None:
                meta["original_timestamp"] = original
            emb, prov, dim = _provenance(row)

            try:
                entity_id = palace_tools.upsert_entity(
                    dest_cursor, name, scope, kind='person')
                # Structured contact fields → entity meta.fields (template
                # system / contacts provider). Existing palace fields win —
                # the palace is the live system, the old store is history.
                efields = {}
                for col in ('relationship', 'phone', 'email', 'address', 'notes'):
                    val = _get(row, col)
                    if val is not None and str(val).strip():
                        efields[col] = str(val).strip()
                if row.get('email_whitelisted'):
                    efields['allow_email'] = True
                if row.get('call_whitelisted'):
                    efields['allow_call'] = True
                if efields:
                    erow = dest_cursor.execute(
                        'SELECT meta FROM entities WHERE id = ?', (entity_id,)).fetchone()
                    try:
                        emeta = json.loads(erow[0]) if erow and erow[0] else {}
                    except Exception:
                        emeta = {}
                    emeta['fields'] = {**efields, **(emeta.get('fields') or {})}
                    dest_cursor.execute('UPDATE entities SET meta = ? WHERE id = ?',
                                        (json.dumps(emeta), entity_id))
                _insert_chunk(
                    dest_cursor, layer='entities', scope=scope, content=content,
                    created=created, updated=created, entity_id=entity_id, tier=1,
                    source='people v2', meta=json.dumps(meta),
                    embedding=emb, embedding_provider=prov, embedding_dim=dim,
                )
            except Exception as e:
                logger.error(f"[MINDPALACE] import person {old_id} failed: {e}")
                failed += 1
                continue

            existing_keys.add(key)
            copied += 1
            pending += 1
            if pending >= COMMIT_EVERY:
                dest_cursor.connection.commit()
                pending = 0

    return (copied, skipped, failed)


def _import_knowledge(config, dest_cursor, existing_keys, scopes_seen, skip_keys=frozenset()):
    """knowledge_entries → chunks(layer='knowledge'). Preserves the sub-chunk
    group identity (label, source, chunk_index) for future neighbor-stitching.
    Returns (copied, skipped, failed)."""
    path = _source_path(config, "knowledge.db")
    if not path.exists():
        return None

    # The Library's boot migration reads knowledge.db DIRECTLY (before this
    # import ever runs on a fresh cycle) — if it already holds this content,
    # flag the palace copies library_migrated so reads defer to the Library
    # instead of serving the same knowledge twice. Flag only when the v1
    # migration verifiably ran with the source present; when unsure, prefer
    # a doubled surface over dark content.
    lib_has_v1 = False
    try:
        from plugins.mindpalace.tools import library
        rep = library.migration_report() or {}
        lib_has_v1 = bool(rep.get('ran_at')) and \
            not (rep.get('v1') or {}).get('absent', True)
    except Exception:
        pass

    copied = skipped = failed = 0
    with _connect_ro(path) as src:
        src.row_factory = sqlite3.Row
        scur = src.cursor()
        tab_cols = _columns(scur, 'knowledge_tabs')
        entry_cols = _columns(scur, 'knowledge_entries')
        if not entry_cols:
            return (0, 0, 0)

        # Tab lookup: id → (name, type, scope). Columns are defensive.
        tabs = {}
        if tab_cols:
            scur.execute("SELECT * FROM knowledge_tabs")
            for r in scur.fetchall():
                trow = {k: r[k] for k in r.keys()}
                tabs[trow.get('id')] = {
                    'name': _get(trow, 'name'),
                    'type': _get(trow, 'type'),
                    'scope': _get(trow, 'scope'),
                }

        scur.execute("SELECT * FROM knowledge_entries")
        pending = rag = 0
        for r in scur.fetchall():
            row = {k: r[k] for k in r.keys()}
            old_id = row.get('id')
            key = f"v2:knowledge:{old_id}"
            if key in skip_keys:
                continue
            if key in existing_keys:
                skipped += 1
                continue

            content = _get(row, 'content')
            if content is None or not str(content).strip():
                failed += 1
                continue

            tab = tabs.get(row.get('tab_id'), {})
            tab_name = tab.get('name')
            tab_type = tab.get('type')
            # scope: entry's own (if the column exists) → tab's → 'default'.
            scope = _get(row, 'scope') or tab.get('scope') or 'default'
            if str(scope).startswith('__rag__:'):
                # A chat's attached documents, scoped by CHAT NAME — core's
                # per-chat RAG under either engine, never memory. Importing
                # them minted a palace scope per chat (a private chat's name
                # in the scope list) and made attachments permanent knowledge
                # (scout find 2026-10-30; classic_memory_rows excludes them).
                rag += 1
                continue
            scopes_seen.add(scope)
            label = tab_name.lower() if tab_name else None
            chunk_index = _get(row, 'chunk_index')
            source = _get(row, 'source_filename')

            created, original = _normalize_ts(_get(row, 'created_at', 'updated_at'))
            meta = {"import_key": key, "tab": tab_name, "tab_type": tab_type}
            if lib_has_v1:
                meta["library_migrated"] = "v1-boot"
            if original is not None:
                meta["original_timestamp"] = original
            emb, prov, dim = _provenance(row)

            try:
                _insert_chunk(
                    dest_cursor, layer='knowledge', scope=scope, content=content,
                    created=created, updated=created, chunk_index=chunk_index,
                    source=source, label=label, meta=json.dumps(meta),
                    embedding=emb, embedding_provider=prov, embedding_dim=dim,
                )
            except Exception as e:
                logger.error(f"[MINDPALACE] import knowledge {old_id} failed: {e}")
                failed += 1
                continue

            existing_keys.add(key)
            copied += 1
            pending += 1
            if pending >= COMMIT_EVERY:
                dest_cursor.connection.commit()
                pending = 0

    if rag:
        logger.info(f"[MINDPALACE] import: {rag} per-chat attachment rows left in place")
    return (copied, skipped, failed)


def _import_goals(config, dest_cursor, existing_keys, scopes_seen, skip_keys=frozenset()):
    """goals.db → chunks(layer='goals') + subtask_of / progress_of edges.
    Two-pass so subtasks map to their parents' NEW ids. Permanent goals land
    in the never-fades band (importance 0.95). Returns (copied, skipped,
    failed)."""
    path = _source_path(config, "goals.db")
    if not path.exists():
        return None

    copied = skipped = failed = 0
    with _connect_ro(path) as src:
        src.row_factory = sqlite3.Row
        scur = src.cursor()
        if not _columns(scur, 'goals'):
            return (0, 0, 0)
        rows = [dict(r) for r in scur.execute(
            "SELECT * FROM goals ORDER BY parent_id IS NOT NULL, id").fetchall()]
        notes = [dict(r) for r in scur.execute(
            "SELECT * FROM goal_progress ORDER BY id").fetchall()] \
            if _columns(scur, 'goal_progress') else []

    id_map = {}   # old goal id → new chunk id
    now = None

    def _stamp_goal(row, parent_new=None):
        nonlocal copied, failed
        old_id = row.get('id')
        key = f"v2:goal:{old_id}"
        if key in skip_keys:
            return 'left'
        if key in existing_keys:
            return 'skipped'
        title = (row.get('title') or '').strip()
        if not title:
            return 'failed'
        scope = row.get('scope') or 'default'
        scopes_seen.add(scope)
        created, original = _normalize_ts(row.get('created_at') or row.get('updated_at'))
        meta = {'import_key': key, 'added_by': 'ai',
                'goal_status': row.get('status') or 'active',
                'priority': row.get('priority') or 'medium'}
        if row.get('description'):
            meta['description'] = row['description']
        if row.get('completed_at'):
            meta['completed_at'] = row['completed_at']
        if row.get('permanent'):
            meta['permanent'] = True
        if original is not None:
            meta['original_timestamp'] = original
        if parent_new is not None:
            meta['parent_goal'] = parent_new
        try:
            _insert_chunk(dest_cursor, layer='goals', scope=scope, content=title,
                          created=created, updated=created,
                          meta=json.dumps(meta),
                          importance=0.95 if row.get('permanent') else None)
            new_id = dest_cursor.lastrowid
            id_map[old_id] = new_id
            if parent_new is not None:
                dest_cursor.execute(
                    "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                    "VALUES ('chunk', ?, 'chunk', ?, 'subtask_of', 1.0, ?)",
                    (new_id, parent_new, created))
            return 'copied'
        except Exception as e:
            logger.error(f"[MINDPALACE] import goal {old_id} failed: {e}")
            return 'failed'

    # Pass 1: top-level; pass 2: subtasks (rows pre-sorted parents-first).
    for row in rows:
        parent_old = row.get('parent_id')
        if parent_old is not None and parent_old not in id_map:
            # Parent skipped (already imported earlier) — resolve its new id
            # by key so re-imports still attach subtasks correctly.
            prow = dest_cursor.execute(
                "SELECT id FROM chunks WHERE json_extract(meta, '$.import_key') = ?",
                (f"v2:goal:{parent_old}",)).fetchone()
            if prow:
                id_map[parent_old] = prow[0]
        result = _stamp_goal(row, id_map.get(row.get('parent_id')))
        if result == 'copied':
            copied += 1
        elif result == 'skipped':
            skipped += 1
        elif result != 'left':
            failed += 1

    for n in notes:
        key = f"v2:goalnote:{n.get('id')}"
        if key in skip_keys:
            continue
        if key in existing_keys:
            skipped += 1
            continue
        goal_old = n.get('goal_id')
        goal_new = id_map.get(goal_old)
        if goal_new is None:
            prow = dest_cursor.execute(
                "SELECT id, scope FROM chunks WHERE json_extract(meta, '$.import_key') = ?",
                (f"v2:goal:{goal_old}",)).fetchone()
            if not prow:
                failed += 1
                continue
            goal_new = prow[0]
        grow = dest_cursor.execute('SELECT scope FROM chunks WHERE id = ?',
                                   (goal_new,)).fetchone()
        text = (n.get('note') or '').strip()
        if not text or not grow:
            failed += 1
            continue
        created, _orig = _normalize_ts(n.get('created_at'))
        meta = {'import_key': key, 'added_by': 'ai', 'progress_of': goal_new}
        try:
            _insert_chunk(dest_cursor, layer='goals', scope=grow[0], content=text,
                          created=created, updated=created, meta=json.dumps(meta))
            dest_cursor.execute(
                "INSERT INTO edges (src_type, src_id, dst_type, dst_id, kind, weight, created) "
                "VALUES ('chunk', ?, 'chunk', ?, 'progress_of', 1.0, ?)",
                (dest_cursor.lastrowid, goal_new, created))
            copied += 1
        except Exception as e:
            logger.error(f"[MINDPALACE] import goal note {n.get('id')} failed: {e}")
            failed += 1

    return (copied, skipped, failed)


# ─── Preview (read-only, both sides) ─────────────────────────────────────────
# The Import tab's numbers: per store × scope, what the classic databases
# hold, what the palace already carries (import_key), and the difference.
# classic_memory_rows (core/plugin_loader.py) answers the boot question with
# one number; this is the same count, unfolded.

STORES = ('memories', 'people', 'knowledge', 'goals')
_SOURCE_FILES = ('memory.db', 'knowledge.db', 'goals.db')
NUDGE_KEY = 'mindpalace-import-pending'


# ─── Tombstones (2026-10-30) ─────────────────────────────────────────────────
# A deleted palace row that came from the classic system never comes back
# through the Import tab: its key is kept (never its content). Before this
# the idempotency set was the palace's own keys, so every memory the user
# trimmed after the first import read as "pending" again — Prime showed
# 444 of them. Tester resets (wipe scope, clear a tab, delete scope) do NOT
# tombstone: their whole point is re-import.

_TOMB_SCHEMA = ('CREATE TABLE IF NOT EXISTS import_tombstones '
                '(key TEXT PRIMARY KEY, scope TEXT, created TEXT)')


def tombstone_keys(cursor, keys, scope):
    cursor.execute(_TOMB_SCHEMA)
    from plugins.mindpalace.tools import palace_tools
    now = palace_tools._now()
    for k in keys:
        if k:
            cursor.execute('INSERT OR IGNORE INTO import_tombstones (key, scope, created) '
                           'VALUES (?, ?, ?)', (str(k), scope, now))


def _tombstones(cursor):
    cursor.execute(_TOMB_SCHEMA)
    return {r[0] for r in cursor.execute('SELECT key FROM import_tombstones')}


# ─── Classification: every classic row, against the palace ──────────────────

def _classic_rows(config):
    """Every classic row as (key, store, scope, created_iso, preview, old_id,
    valid), read-only. Per-chat attachment tabs are left aside (rag count)."""
    out, rag = [], 0

    def row(key, store, scope, created_raw, content, old_id):
        created, _orig = _normalize_ts(created_raw)
        text = ' '.join(str(content or '').split())
        out.append({'key': key, 'store': store, 'scope': scope or 'default',
                    'created': created, 'preview': text[:80], 'id': old_id,
                    'valid': bool(text)})

    mem = _source_path(config, 'memory.db')
    if mem.exists():
        try:
            with _connect_ro(mem) as src:
                src.row_factory = sqlite3.Row
                cur = src.cursor()
                if _columns(cur, 'memories'):
                    for r in cur.execute('SELECT * FROM memories'):
                        d = {k: r[k] for k in r.keys()}
                        row(f"v2:memories:{d.get('id')}", 'memories', d.get('scope'),
                            d.get('timestamp'), d.get('content'), d.get('id'))
        except sqlite3.Error as e:
            logger.warning(f"[MINDPALACE] import preview: memory.db unreadable: {e}")

    know = _source_path(config, 'knowledge.db')
    if know.exists():
        try:
            with _connect_ro(know) as src:
                src.row_factory = sqlite3.Row
                cur = src.cursor()
                if _columns(cur, 'people'):
                    for r in cur.execute('SELECT * FROM people'):
                        d = {k: r[k] for k in r.keys()}
                        row(f"v2:people:{d.get('id')}", 'people', d.get('scope'),
                            _get(d, 'created_at', 'updated_at'), d.get('name'), d.get('id'))
                if _columns(cur, 'knowledge_entries'):
                    tabs = {}
                    if _columns(cur, 'knowledge_tabs'):
                        for r in cur.execute('SELECT * FROM knowledge_tabs'):
                            t = {k: r[k] for k in r.keys()}
                            tabs[t.get('id')] = t
                    for r in cur.execute('SELECT * FROM knowledge_entries'):
                        d = {k: r[k] for k in r.keys()}
                        tab = tabs.get(d.get('tab_id'), {})
                        scope = _get(d, 'scope') or tab.get('scope') or 'default'
                        if str(scope).startswith('__rag__:'):
                            rag += 1
                            continue
                        row(f"v2:knowledge:{d.get('id')}", 'knowledge', scope,
                            _get(d, 'created_at', 'updated_at'), d.get('content'), d.get('id'))
        except sqlite3.Error as e:
            logger.warning(f"[MINDPALACE] import preview: knowledge.db unreadable: {e}")

    goals = _source_path(config, 'goals.db')
    if goals.exists():
        try:
            with _connect_ro(goals) as src:
                src.row_factory = sqlite3.Row
                cur = src.cursor()
                if _columns(cur, 'goals'):
                    for r in cur.execute('SELECT * FROM goals'):
                        d = {k: r[k] for k in r.keys()}
                        row(f"v2:goal:{d.get('id')}", 'goals', d.get('scope'),
                            _get(d, 'created_at', 'updated_at'), d.get('title'), d.get('id'))
                    if _columns(cur, 'goal_progress'):
                        for r in cur.execute('SELECT p.*, g.scope AS gscope FROM goal_progress p '
                                             'JOIN goals g ON g.id = p.goal_id'):
                            d = {k: r[k] for k in r.keys()}
                            row(f"v2:goalnote:{d.get('id')}", 'goals', d.get('gscope'),
                                d.get('created_at'), d.get('note'), d.get('id'))
        except sqlite3.Error as e:
            logger.warning(f"[MINDPALACE] import preview: goals.db unreadable: {e}")
    return out, rag


def _classify(config):
    """Every classic row with its class: imported (its key is in the palace),
    tombstoned (deleted here, never again), removed (not in the palace AND
    saved before this scope's first import — it came through once and was
    deleted or merged away since), new (never seen), invalid (empty).
    Returns (rows, rag_count)."""
    from plugins.mindpalace.tools import palace_tools
    rows, rag = _classic_rows(config)
    with palace_tools._get_connection() as conn:
        cur = conn.cursor()
        have = {r[0] for r in cur.execute(
            "SELECT json_extract(meta, '$.import_key') FROM chunks "
            "WHERE json_extract(meta, '$.import_key') LIKE 'v2:%'")}
        tomb = _tombstones(cur)
        # First import per (scope, STORE): an import row says which stores
        # that run copied (detail.stores). A run that copied only memories
        # is no evidence about that scope's people — on the restored dev
        # box 23 never-imported rows read as "removed before" under a
        # per-scope-only rule (2026-10-30). Imports have always been every
        # scope per store, so a store copied in a run was copied for every
        # scope that held it then.
        first = {}
        try:
            for scope, ts, detail in cur.execute(
                    "SELECT scope, ts, detail FROM ledger WHERE actor = 'import' ORDER BY ts"):
                try:
                    stores = (json.loads(detail or '{}').get('stores') or {})
                except Exception:
                    stores = {}
                for store, n in stores.items():
                    if n:
                        first.setdefault((scope, store), ts)
        except sqlite3.Error:
            pass
    for r in rows:
        if not r['valid']:
            r['cls'] = 'invalid'
        elif r['key'] in have:
            r['cls'] = 'imported'
        elif r['key'] in tomb:
            r['cls'] = 'tombstoned'
        elif (first.get((r['scope'], r['store']))
              and r['created'] < first[(r['scope'], r['store'])]):
            r['cls'] = 'removed'
        else:
            r['cls'] = 'new'
    return rows, rag


def preview(config) -> dict:
    """What an import would do, without doing it. Read-only on both sides.
    'pending' = new rows only; removed-before rows are listed apart and
    come back only on an explicit ask (Krem 2026-10-30, C)."""
    rows, rag = _classify(config)
    stores = {}
    for s in STORES:
        scopes = {}
        for r in rows:
            if r['store'] != s or r['cls'] == 'invalid':
                continue
            v = scopes.setdefault(r['scope'], {'total': 0, 'imported': 0, 'pending': 0,
                                               'removed': 0, 'tombstoned': 0})
            v['total'] += 1
            v[{'imported': 'imported', 'new': 'pending', 'removed': 'removed',
               'tombstoned': 'tombstoned'}[r['cls']]] += 1
        stores[s] = {'absent': not _source_path(config, {'memories': 'memory.db',
                                                         'people': 'knowledge.db',
                                                         'knowledge': 'knowledge.db',
                                                         'goals': 'goals.db'}[s]).exists(),
                     'scopes': dict(sorted(scopes.items()))}
        for k in ('total', 'imported', 'pending', 'removed', 'tombstoned'):
            stores[s][k] = sum(v[k] for v in scopes.values())
    files = {}
    for name in _SOURCE_FILES:
        p = _source_path(config, name)
        files[name] = ({'present': True, 'bytes': p.stat().st_size}
                       if p.exists() else {'present': False})
    with _state_lock:
        st = dict(_run_state)
    return {'stores': stores, 'files': files, 'rag_rows': rag,
            'pending': sum(stores[s]['pending'] for s in STORES),
            'removed': sum(stores[s]['removed'] for s in STORES),
            'tombstoned': sum(stores[s]['tombstoned'] for s in STORES),
            'running': st['running'],
            'last': st.get('result'), 'finished': st.get('finished')}


def removed_list(config, scope=None, store=None):
    """The removed-before rows, previews included — the user's own data,
    shown on request so the decision (bring back / scrub) is an informed one."""
    rows, _rag = _classify(config)
    out = [{k: r[k] for k in ('store', 'scope', 'key', 'id', 'created', 'preview')}
           for r in rows if r['cls'] == 'removed'
           and (scope is None or r['scope'] == scope)
           and (store is None or r['store'] == store)]
    out.sort(key=lambda r: (r['scope'], r['store'], r['created']))
    return out


_SCRUB_SQL = {
    'memories': ('memory.db', ['DELETE FROM memories WHERE id = ?']),
    'people': ('knowledge.db', ['DELETE FROM people WHERE id = ?']),
    'knowledge': ('knowledge.db', ['DELETE FROM knowledge_entries WHERE id = ?']),
    'goal': ('goals.db', ['DELETE FROM goal_progress WHERE goal_id = ?',
                          'DELETE FROM goals WHERE id = ?']),
    'goalnote': ('goals.db', ['DELETE FROM goal_progress WHERE id = ?']),
}


def scrub_removed(config, scope=None):
    """THE ONE WRITE TO A CLASSIC DATABASE IN THE PALACE (Krem's ruling
    2026-10-30): rows the user already removed from the palace, deleted from
    the old database on an explicit, typed-confirm ask — personal data from
    the old days must not linger. Never automatic, never the AI's, never
    the importer's. The classic memories FTS rides on triggers; the other
    stores are bare rows. Keys are tombstoned; a ledger line per scope
    carries counts, never content. Returns counts."""
    from plugins.mindpalace.tools import palace_tools
    rows = [r for r in removed_list(config, scope=scope)]
    counts = {s: 0 for s in STORES}
    # Sanity fence: "removed before" presumes an earlier import brought
    # the row in. More removed rows than this palace EVER imported means
    # the old database and this palace are not a pair (a different palace,
    # a swapped directory, a test against the wrong config — the 28-row
    # incident, 2026-10-30). Refuse rather than delete.
    ever = 0
    with palace_tools._get_connection() as conn:
        for (detail,) in conn.execute("SELECT detail FROM ledger WHERE actor = 'import'"):
            try:
                ever += sum(int(n) for n in (json.loads(detail or '{}').get('stores') or {}).values())
            except Exception:
                pass
    if rows and len(rows) > ever:
        raise RuntimeError(f"{len(rows)} removed-before rows exceed the {ever} this palace "
                           f"ever imported — the old database and this palace don't match; "
                           f"nothing scrubbed")
    by_file = {}
    for r in rows:
        kind = r['key'].split(':')[1]
        fname, sqls = _SCRUB_SQL[kind]
        by_file.setdefault(fname, []).append((sqls, r['id'], r['store']))
    for fname, items in by_file.items():
        path = _source_path(config, fname)
        if not path.exists():
            continue
        conn = sqlite3.connect(str(path))
        try:
            for sqls, old_id, store in items:
                for sql in sqls:
                    conn.execute(sql, (old_id,))
                counts[store] += 1
            conn.commit()
        finally:
            conn.close()
    scrubbed = [r for r in rows]
    if scrubbed:
        with palace_tools._get_connection() as conn:
            cur = conn.cursor()
            per_scope = {}
            for r in scrubbed:
                per_scope.setdefault(r['scope'], []).append(r['key'])
            for sc, keys in per_scope.items():
                tombstone_keys(cur, keys, sc)
                palace_tools._ledger(sc, 'user', 'scrubbed', cursor=cur,
                                     summary=(f"scrubbed {len(keys)} removed-before row(s) "
                                              f"from the classic database for good"))
            conn.commit()
    return {'scrubbed': len(scrubbed), **counts}


def nudge_if_pending(config):
    """Boot (plugins_ready): classic rows not yet in the palace → a row in
    the notification center, re-fired every boot while any remain (Krem's
    ruling 2026-10-30: fire on each boot if they need to migrate). Nothing
    pending → the row goes away."""
    try:
        from core import notify
    except Exception:
        return None
    try:
        p = preview(config)
    except Exception as e:
        logger.debug(f"[MINDPALACE] import nudge skipped: {e}")
        return None
    if p['pending'] <= 0:
        _dismiss_nudge(notify)
        return None
    bits = [f"{p['stores'][s]['pending']} {s}" for s in STORES
            if p['stores'][s]['pending']]
    return notify.notify(
        "Classic memories are waiting to be imported", 'warning',
        f"{', '.join(bits)} from Memory (legacy) are not in the Mind Palace "
        f"yet. Mind › Import shows what will come across and brings it in; "
        f"the old databases are never changed.",
        source='mindpalace', key=NUDGE_KEY, go={'view': 'import'})


def _dismiss_nudge(notify):
    try:
        for r in notify.rows(source='mindpalace'):
            if r.get('key') == NUDGE_KEY:
                notify.dismiss(r['id'])
    except Exception as e:
        logger.debug(f"[MINDPALACE] import nudge not dismissed: {e}")


# ─── Orchestrator ─────────────────────────────────────────────────────────────

_import_lock = None   # module-level: one import at a time, ever
_state_lock = threading.Lock()
_run_state = {'running': False, 'started': None, 'stage': None, 'done': 0,
              'total': 0, 'result': None, 'finished': None, 'error': None}


def status() -> dict:
    with _state_lock:
        return dict(_run_state)


def start(config, what='all', sync=False, scope=None, include_removed=False):
    """The Import tab's buttons: run in a background thread (the whole copy
    plus the metadata backfill used to block the request), progress on
    the bus via the notification row. `scope` imports one scope's rows;
    `include_removed` brings back rows the user removed before (never the
    default). Returns (message, ok). sync=True runs inline (tests)."""
    with _state_lock:
        if _run_state['running']:
            return "An import is already running.", False
        _run_state.update(running=True, started=palace_now(), stage='starting',
                          done=0, total=0, result=None, error=None)
    args = (what, config, scope, include_removed)
    if sync:
        _run_and_record(*args)
    else:
        threading.Thread(target=_run_and_record, args=args,
                         name='mindpalace-import', daemon=True).start()
    return (f"Import started for '{scope}'." if scope else "Import started."), True


def palace_now():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools._now()


def _run_and_record(what, config, scope=None, include_removed=False):
    try:
        text = _run_import(what, config, scope=scope, include_removed=include_removed)
        with _state_lock:
            _run_state.update(result=text)
    except Exception as e:
        logger.error(f"[MINDPALACE] import thread failed: {e}", exc_info=True)
        with _state_lock:
            _run_state.update(error=str(e), result=f"Import failed: {e}")
    finally:
        with _state_lock:
            _run_state.update(running=False, stage=None, finished=palace_now())


def _run_import(what: str, config, scope=None, include_removed=False) -> str:
    global _import_lock
    if _import_lock is None:
        _import_lock = threading.Lock()
    # The idempotency set is a read-once snapshot — two CONCURRENT imports
    # (Admin button + AI tool run in parallel threads) each see an empty set
    # and duplicate every row (Lane-1 scout, reproduced 2026-07-27). One
    # writer at a time; the loser gets a clean refusal, and a RE-run after
    # the first finishes still correctly skips everything.
    if not _import_lock.acquire(blocking=False):
        return ("An import is already running — wait for it to finish, "
                "then re-run if needed (re-runs copy zero duplicates).")
    try:
        # One row in the notification center for the whole run (the tool
        # path and the tab share it); ticks ride the bus, the file sees
        # start and end. A refusal above makes no row.
        try:
            from core import notify as _notify
            with _notify.progress("Importing classic memories", source='mindpalace',
                                  go={'view': 'import'},
                                  done_title="Classic memories imported",
                                  failed_title="Import of classic memories failed") as h:
                text = _run_import_locked(what, config, progress=h, scope=scope,
                                          include_removed=include_removed)
                h.text(text.split('\n', 1)[-1][:300] if text else '')
            return text
        except ImportError:
            return _run_import_locked(what, config, scope=scope,
                                      include_removed=include_removed)
    finally:
        _import_lock.release()


def _run_import_locked(what: str, config, progress=None, scope=None,
                       include_removed=False) -> str:
    from plugins.mindpalace.tools import palace_tools

    order = list(STORES)
    targets = order if what == 'all' else [what]

    results = {}
    scopes_seen = set()
    any_copied = False

    # What stays out (2026-10-30): tombstoned keys always; rows the user
    # removed before unless asked; other scopes when one scope was asked.
    cls, _rag = _classify(config)
    left = {'removed': 0, 'tombstoned': 0, 'other scopes': 0}
    skip_keys = set()
    for r in cls:
        if r['cls'] == 'tombstoned':
            skip_keys.add(r['key']); left['tombstoned'] += 1
        elif r['cls'] == 'removed' and not include_removed:
            skip_keys.add(r['key']); left['removed'] += 1
        elif scope and r['scope'] != scope:
            skip_keys.add(r['key']); left['other scopes'] += 1
    skip_keys = frozenset(skip_keys)

    # Totals first so the bar has a denominator.
    source = {s: {} for s in STORES}
    for r in cls:
        if r['cls'] != 'invalid' and r['key'] not in skip_keys:
            source[r['store']][r['scope']] = source[r['store']].get(r['scope'], 0) + 1
    total = sum(sum(source[s].values()) for s in targets)
    done_before = 0

    def tick(stage, n):
        d = done_before + n
        with _state_lock:
            _run_state.update(stage=stage, done=d, total=total)
        if progress is not None:
            progress.update(d, total or None, text=f"{stage}: {d}/{total}")

    with palace_tools._get_connection() as conn:
        cursor = conn.cursor()

        # One query loads every existing import_key → idempotency set. Never
        # per-row existence checks (that's the O(N²) trap).
        cursor.execute(
            "SELECT json_extract(meta, '$.import_key') FROM chunks WHERE meta IS NOT NULL"
        )
        existing_keys = {row[0] for row in cursor.fetchall() if row[0] is not None}

        importers = {
            'memories': _import_memories,
            'people': _import_people,
            'knowledge': _import_knowledge,
            'goals': _import_goals,
        }
        for store in targets:
            tick(store, 0)
            res = importers[store](config, cursor, existing_keys, scopes_seen,
                                   skip_keys=skip_keys)
            results[store] = res
            if res is not None and res[0] > 0:
                any_copied = True
            done_before += sum(source[store].values())
            tick(store, 0)

        # Register every distinct scope so the UI dropdown lists it. create_scope
        # opens its own connection; call after our writes are in the cursor but
        # commit first so the scopes and chunks land together-ish.
        conn.commit()

    for s in sorted(scopes_seen):
        palace_tools.create_scope(s)

    # Ledger v1: one summary row per scope the run touched (ledger rows are
    # scope-keyed; the run's store totals ride on each).
    if any_copied:
        totals = {s: r[0] for s, r in results.items() if r and r[0]}
        summary = ("imported from the classic memory system: "
                   + ", ".join(f"{n} {s}" for s, n in totals.items()))
        for s in sorted(scopes_seen):
            palace_tools._ledger(s, 'import', 'imported', summary=summary,
                                 detail={'stores': totals})

    # Any copied rows may lack vectors → clear the backfill latch AND kick
    # the sweep now, not at the next search — a dedup pass fired right after
    # an import needs embeddings to see any batch at all (the wipe/import/
    # test cycle does exactly this).
    if any_copied:
        palace_tools.reset_backfill_latch()
        import threading
        threading.Thread(target=palace_tools._backfill_embeddings,
                         name='mindpalace-import-backfill',
                         daemon=True).start()
        # Doc→entity edges: boot seeded them BEFORE this import existed —
        # against an empty entities table on a fresh cycle — and never
        # re-runs. Kick it now so library docs join the graph this round.
        try:
            from plugins.mindpalace.tools import library
            threading.Thread(target=library.backfill_doc_edges,
                             name='mindpalace-import-doc-edges',
                             daemon=True).start()
        except Exception as e:
            logger.warning(f"[IMPORT] doc-edge backfill kick skipped: {e}")

    # Stamp derivable metadata (stats/temporal refs/noun candidates) + seed
    # entity-mention edges on everything just imported. Idempotent (md_v
    # marker); runs AFTER people-import so events link to person entities.
    md_result = None
    try:
        with _state_lock:
            _run_state.update(stage='metadata')
        if progress is not None:
            progress.text('stamping metadata and linking people…')
        from plugins.mindpalace.tools import metadata
        md_result = metadata.backfill()
    except Exception as e:
        md_result = {'error': str(e)}

    # Nothing left in the old stores → the boot nudge has done its job.
    try:
        if preview(config)['pending'] <= 0:
            from core import notify as _notify
            _dismiss_nudge(_notify)
    except Exception as e:
        logger.debug(f"[MINDPALACE] post-import nudge check skipped: {e}")

    # ── Human-readable summary ──
    lines = ["Import from the classic memory system (v2 → mind palace):"]
    for store in targets:
        res = results.get(store)
        if res is None:
            lines.append(f"  • {store}: source DB not found — skipped.")
        else:
            copied, skipped, failed = res
            bit = f"  • {store}: {copied} copied, {skipped} already imported"
            if failed:
                bit += f", {failed} failed"
            lines.append(bit + ".")
    if md_result:
        if 'error' in md_result:
            lines.append(f"  • metadata: backfill failed ({md_result['error']}) — "
                         "memories are saved and searchable regardless.")
        else:
            lines.append(f"  • metadata: {md_result.get('stamped', 0)} chunks stamped, "
                         f"{md_result.get('edges', 0)} entity links seeded.")
    out = [f"{n} {k}" for k, n in left.items() if n]
    if out:
        lines.append("  • left out: " + ", ".join(out)
                     + (" — removed-before rows come back only from the Import tab"
                        if left['removed'] else "") + ".")
    lines.append("The old databases were opened read-only and were NOT modified — "
                 "they remain your switch-back path.")
    return "\n".join(lines)


# ─── Executor ─────────────────────────────────────────────────────────────────

def execute(function_name: str, arguments: dict, config) -> tuple:
    try:
        if function_name != "import_v2":
            return f"Unknown import function: {function_name}", False

        # Scope gating mirrors palace_tools: unresolved scope → disabled. But we
        # deliberately do NOT apply the global write-block — import copies ALL
        # scopes verbatim, global included.
        from plugins.mindpalace.tools import palace_tools
        scope = palace_tools._get_current_scope()
        if scope is None:
            return "Memory is disabled for this chat.", False

        what = arguments.get("what", "all")
        if what not in ("memories", "people", "knowledge", "goals", "all"):
            what = "all"

        summary = _run_import(what, config)
        return summary, True

    except Exception as e:
        logger.error(f"[MINDPALACE] Import error: {e}")
        return f"Import failed: {e}", False
