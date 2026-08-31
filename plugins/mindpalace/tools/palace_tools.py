# plugins/mindpalace/tools/palace_tools.py
# Mind Palace v1 — layered memory framework (v3 memory boost, step 1).
#
# Uniform unit everywhere: chunks + metadata. Layers on the rail:
#   0 self       — who she is, decisions, history (per-scope)
#   1 events     — things that happened; default write target; librarian raw material
#   2 entities   — people/places/things; tiered chunks (1=headline 2=facts 3=trivia)
#   3 knowledge  — big reference data (sub-chunked groups)
#
# Deliberately shares tool NAMES with plugins/memory — the two plugins are
# mutually exclusive (function_manager refuses the second by design). This
# module NEVER touches memory.db/knowledge.db/goals.db; import_tools.py reads
# them and copies. Old DBs stay untouched as the switch-back path.
#
# Invariants carried from v2 (load-bearing — do not "simplify" away):
#   - prefix asymmetry: embed with 'search_document' at write, 'search_query' at read
#   - provenance triple: (embedding, embedding_provider, embedding_dim) travel
#     together or the row is invisible to vector search
#   - scope fail-disabled: ContextVar resolution failure → None → tools disabled,
#     never a silent fall-through to 'default'
#   - 'global' scope: read-only overlay, AI writes blocked
#   - private_key: plaintext gate, cross-persona behavioral separation
#
# Corruption policy v1: integrity failure → preserve as .corrupted, recreate
# fresh. No salvage-rebuild yet — in v1 the old DBs remain the source of truth
# and import_v2 re-runs idempotently, so recovery = re-import. Revisit once
# the palace holds data that exists nowhere else (librarian output).

import json
import sqlite3
import logging
import re
import threading
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🏛️'
GROUP = 'Mind Palace'   # Toolsets UI merges same-GROUP modules

_db_path = None
_db_initialized = False
_db_lock = threading.Lock()

from core.embeddings import get_embedder as _get_embedder

SUGGESTED_LABELS = "family, preferences, technical, stories, routines, opinions"

# Layer registry, v1: internal seed. The `layers` table is the durable registry;
# plugin-registered layers (v1.1) will insert rows + register handlers here.
LAYERS = {
    'self':      {'num': 0, 'label': 'Self'},
    'events':    {'num': 1, 'label': 'Events'},
    'entities':  {'num': 2, 'label': 'Entities'},
    'knowledge': {'num': 3, 'label': 'Knowledge'},
    'goals':     {'num': 4, 'label': 'Goals'},   # 2026-07-11: goals joined the palace
}
LAYER_KEYS = list(LAYERS.keys())


# ─── Plugin layers (v1.1, 2026-07-12) ────────────────────────────────────────
# Other plugins declare layers via manifest capabilities.memory_layers; the
# core registry (core/memory_layers.py) holds them, we consume. Mirror-only:
# their content lives as normal chunks rows (layer = their key), written
# through layer_api. A disabled provider's layer goes DARK — rows survive,
# every read path excludes them (Krem: "no memory when off").

def _plugin_layers():
    """Registered plugin layers: {key: spec}. {} when none or registry absent."""
    try:
        from core import memory_layers
        return memory_layers.get_layers()
    except Exception:
        return {}


def _all_layer_keys():
    return LAYER_KEYS + [k for k in _plugin_layers() if k not in LAYERS]


try:  # tool schemas rebuild live when the layer registry changes
    from core import memory_layers as _ml_registry
    _ml_registry.add_consumer('mindpalace')
except Exception:
    pass

AVAILABLE_FUNCTIONS = [
    'save_memory',
    'search_memory',
    'get_recent_memories',
    'update_memory',
    'delete_memory',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "save_memory",
            "description": (
                "Save to layered long-term memory. Max 512 chars (aim under 450) — "
                "longer content is trimmed at a word boundary and the reply says "
                "what was cut. Layers: "
                "'events' (default — things that happened), 'entities' (a fact "
                "about a person/place/thing — requires entity name), 'knowledge' "
                "(reference material). Who-you-are edits go through update_self "
                "(the sheet) — layer='self' saves land in events. "
                f"Suggested labels: {SUGGESTED_LABELS}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember"
                    },
                    "layer": {
                        "type": "string",
                        "enum": LAYER_KEYS,
                        "description": "Destination layer. Default: events."
                    },
                    "entity": {
                        "type": "string",
                        "description": "Entity name (person/place/thing) this fact belongs to. Required when layer=entities."
                    },
                    "label": {
                        "type": "string",
                        "description": "Category label"
                    },
                    "private_key": {
                        "type": "string",
                        "description": "Optional gating word. Set only if user asked to make this memory private with a specific word."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_memory",
            "description": "Semantic + full-text search across all memory layers. Optionally restrict to one layer or filter by label. layer='knowledge' searches the library (reference documents) with generous per-document depth; results show [doc N] ids that read_document can open.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms or topic"
                    },
                    "layer": {
                        "type": "string",
                        "enum": LAYER_KEYS,
                        "description": "Restrict to one layer. Omit to search all. layer='knowledge' = the library (books, notes, reference docs), searched deeply."
                    },
                    "document_id": {
                        "type": "integer",
                        "description": "Search inside ONE library document (the [doc N] id). Implies layer='knowledge'."
                    },
                    "label": {
                        "type": "string",
                        "description": "Filter by label(s), comma-separated"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                        "default": 10
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How far to follow connections from the results: 0 = direct matches only (default), 1 = also pull the people/places/things linked to the results and their key facts (the usual choice mid-conversation), 2 = walk a neighborhood further through shared connections (best for waking up, orienting, or deep dives — noticeably longer output). Maximum 2 (higher clamps to 2).",
                        "default": 0
                    },
                    "private_key": {
                        "type": "string",
                        "description": "Gating word — pass to include private rows saved with this word."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_recent_memories",
            "description": "Get most recent memories, optionally from one layer or filtered by label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "How many",
                        "default": 10
                    },
                    "layer": {
                        "type": "string",
                        "enum": LAYER_KEYS,
                        "description": "Restrict to one layer. Omit for all."
                    },
                    "label": {
                        "type": "string",
                        "description": "Filter by label(s), comma-separated"
                    },
                    "private_key": {
                        "type": "string",
                        "description": "Gating word — pass to include private rows saved with this word."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "update_memory",
            "description": ("Edit a memory in place by ID — fix wording, add a "
                            "detail, or re-label without losing the memory's id, "
                            "age, or history. Content max 512 chars (longer is "
                            "trimmed; the reply says what was cut). Memories "
                            "only: [N] ids. Library documents ([doc N]) are NOT "
                            "editable here."),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "integer",
                        "description": ("Memory ID shown as [42] — never a "
                                        "[doc N] document id")
                    },
                    "content": {
                        "type": "string",
                        "description": "Replacement text (omit to keep current)"
                    },
                    "label": {
                        "type": "string",
                        "description": "New category label (omit to keep current)"
                    },
                    "private_key": {
                        "type": "string",
                        "description": "Required to edit a private row. Must match save-time word."
                    }
                },
                "required": ["memory_id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "delete_memory",
            "description": ("Delete a memory by ID. Memories only: [N] ids — "
                            "library documents ([doc N]) are NOT deletable "
                            "here."),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "integer",
                        "description": ("Memory ID shown as [42] — never a "
                                        "[doc N] document id")
                    },
                    "private_key": {
                        "type": "string",
                        "description": "Required to delete a private row. Must match save-time word."
                    }
                },
                "required": ["memory_id"]
            }
        }
    },
]


def get_tools():
    """Registry-aware schema (function_manager calls this instead of TOOLS and
    re-calls it via refresh_plugin_tools on layer-registry changes): layer
    enums include registered plugin layers. save_memory only offers plugin
    layers marked writable — mirror layers are usually sync-owned, and her
    rows there would be clobbered by the next reconciliation."""
    plugin = _plugin_layers()
    if not plugin:
        return TOOLS
    import copy
    read_keys = LAYER_KEYS + list(plugin)
    write_keys = LAYER_KEYS + [k for k, v in plugin.items() if v.get('writable')]
    tools = copy.deepcopy(TOOLS)
    for t in tools:
        fn = t['function']
        layer_prop = fn['parameters'].get('properties', {}).get('layer')
        if layer_prop and 'enum' in layer_prop:
            layer_prop['enum'] = write_keys if fn['name'] == 'save_memory' else read_keys
        if fn['name'] == 'search_memory':
            extras = '; '.join(
                f"'{k}' — {v['label']}" + (f" ({v['description']})" if v['description'] else '')
                for k, v in plugin.items())
            fn['description'] += f" Plugin layers also searchable: {extras}."
    return tools


SIMILARITY_THRESHOLD = 0.40
MAX_CHUNK_LENGTH = 512


def _trim_to_cap(content: str, cap: int = MAX_CHUNK_LENGTH) -> tuple:
    """Over-cap content → (kept, dropped), cut at the last whitespace at or
    before the cap so no word splits (hard cut only if there is none).
    Refusing over-cap saves sent her into a rewrite loop — 5 tool calls for
    one memory. Now it saves and the receipt carries what was cut."""
    content = content.strip()
    if len(content) <= cap:
        return content, ''
    head = content[:cap + 1]
    idx = max(head.rfind(ch) for ch in (' ', '\n', '\t'))
    if idx < cap // 2:
        # Whitespace missing or only near the start (label + unbroken CJK/
        # base64/URL run): a break there would save a tiny stub as the whole
        # memory. Hard-cut at the cap instead.
        idx = cap
    return content[:idx].rstrip(), content[idx:].strip()


def _trim_note(dropped: str, memory_id, cap: int = MAX_CHUNK_LENGTH) -> str:
    return (f" TRIMMED: {len(dropped)} chars over the {cap} cap were cut. "
            f"Dropped: \"{dropped}\". Keep it: update_memory({memory_id}) with "
            f"tighter wording, or save_memory the dropped text as its own "
            f"memory. Or leave it.")
# Per (scope, layer) — the old system capped memories and knowledge separately
# at 50k each; layers restore that separation inside the single table.
MAX_CHUNKS_PER_SCOPE_LAYER = 50_000


def _now() -> str:
    """One timestamp format everywhere: ISO-8601 UTC with offset. Uniform format
    means lexicographic ORDER BY == chronological — that property is load-bearing."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ─── Database ────────────────────────────────────────────────────────────────

def _get_db_path():
    global _db_path
    if _db_path is None:
        # Anchor to config.py location (project root) — stable regardless of
        # where this file lives. Same pattern as plugins/memory (Phase 4 lesson).
        import config
        _db_path = Path(config.__file__).parent / "user" / "memory" / "mind.db"
    return _db_path


@contextmanager
def _get_connection():
    _ensure_db()
    conn = sqlite3.connect(_get_db_path(), timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        yield conn
    finally:
        conn.close()


def _safe_rename_corrupted(db_path):
    """Rename db_path → .db.corrupted, timestamp-suffixed if a prior backup exists."""
    base_backup = db_path.with_suffix('.db.corrupted')
    if not base_backup.exists():
        target = base_backup
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        target = db_path.with_name(db_path.name + f'.corrupted.{ts}')
    try:
        db_path.rename(target)
        logger.error(f"[MINDPALACE] Corrupted DB preserved at {target} — "
                     f"fresh DB will be created; re-run import_v2 to recover copied data")
        return True
    except Exception as e:
        logger.error(f"[MINDPALACE] Could not preserve corrupted DB at {target}: {e}")
        return False


def _setup_fts(cursor):
    """FTS5 external-content table over chunks + sync triggers."""
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content, label,
            content=chunks, content_rowid=id
        )
    """)
    cursor.execute("DROP TRIGGER IF EXISTS chunks_fts_insert")
    cursor.execute("DROP TRIGGER IF EXISTS chunks_fts_delete")
    cursor.execute("DROP TRIGGER IF EXISTS chunks_fts_update")
    cursor.execute("""
        CREATE TRIGGER chunks_fts_insert
        AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, content, label)
            VALUES (new.id, new.content, new.label);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER chunks_fts_delete
        AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content, label)
            VALUES ('delete', old.id, old.content, old.label);
        END
    """)
    # Only content/label changes touch the index — embedding updates must not.
    cursor.execute("""
        CREATE TRIGGER chunks_fts_update
        AFTER UPDATE OF content, label ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content, label)
            VALUES ('delete', old.id, old.content, old.label);
            INSERT INTO chunks_fts(rowid, content, label)
            VALUES (new.id, new.content, new.label);
        END
    """)
    cursor.execute("SELECT COUNT(*) FROM chunks")
    n_chunks = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM chunks_fts")
    n_fts = cursor.fetchone()[0]
    if n_chunks > 0 and n_fts == 0:
        logger.info(f"[MINDPALACE] Populating FTS index from {n_chunks} existing chunks...")
        cursor.execute("""
            INSERT INTO chunks_fts(rowid, content, label)
            SELECT id, content, label FROM chunks
        """)


def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return True
    with _db_lock:
        if _db_initialized:
            return True
        try:
            db_path = _get_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)

            if db_path.exists():
                try:
                    conn = sqlite3.connect(db_path, timeout=10)
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                    conn.close()
                    if result[0] != 'ok':
                        logger.error(f"[MINDPALACE] Integrity check failed: {result[0]}")
                        # Windows can refuse the rename (open handle) — do not
                        # build a fresh schema on top of the corrupt file.
                        if not _safe_rename_corrupted(db_path):
                            return False
                except sqlite3.DatabaseError as e:
                    logger.error(f"[MINDPALACE] Database corrupted: {e}")
                    if not _safe_rename_corrupted(db_path):
                        return False

            for suffix in ['-wal', '-shm', '-journal']:
                stale = db_path.with_name(db_path.name + suffix)
                if stale.exists() and not db_path.exists():
                    stale.unlink()

            conn = sqlite3.connect(db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")

            # Layer registry — durable half of the registry; LAYERS dict is the
            # runtime half. Plugin-added layers (v1.1) insert here with their
            # own owner.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS layers (
                    key TEXT PRIMARY KEY,
                    num INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT 'mindpalace',
                    created TEXT NOT NULL
                )
            ''')
            for key, spec in LAYERS.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO layers (key, num, label, owner, created) "
                    "VALUES (?, ?, ?, 'mindpalace', ?)",
                    (key, spec['num'], spec['label'], _now())
                )

            # Entities — Layer 2's spine. UNIQUE NOCASE per scope (old people-store
            # upsert contract). `mentions` = count since last librarian pass.
            # `meta` = JSON bag for layer-specific extras (spiderable later).
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'default',
                    kind TEXT,
                    mentions INTEGER NOT NULL DEFAULT 0,
                    meta TEXT,
                    created TEXT NOT NULL,
                    updated TEXT NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_name_scope
                ON entities(name COLLATE NOCASE, scope)
            ''')

            # Chunks — THE uniform unit. Every layer stores these.
            #   tier: L2 detail level (1=headline/epicenter, 2=facts, 3=trivia)
            #   chunk_index + source: L3 sub-chunk group identity (neighbor
            #     stitching later needs (label, source, chunk_index))
            #   importance: system-facing salience; the AI NEVER sees the number.
            #     favorite=true maps to 0.95 (>0.9 = never fades). NULL = unset.
            #   meta: JSON bag — write-time mechanical metadata + librarian's
            #     rich metadata later; plugin layers bring custom fields here.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    layer TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'default',
                    content TEXT NOT NULL,
                    entity_id INTEGER,
                    tier INTEGER,
                    chunk_index INTEGER,
                    source TEXT,
                    label TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    importance REAL,
                    mentions INTEGER NOT NULL DEFAULT 0,
                    private_key TEXT,
                    meta TEXT,
                    created TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    embedding BLOB,
                    embedding_provider TEXT,
                    embedding_dim INTEGER,
                    recall_count INTEGER NOT NULL DEFAULT 0,
                    last_recalled TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_layer_scope ON chunks(layer, scope)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_created ON chunks(created)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_entity ON chunks(entity_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_label ON chunks(label)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_private_key ON chunks(private_key)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)')

            # Edges — typed graph, schema NOW / spider LATER. weight: structural
            # rail edges 1.0, metadata edges < 1.0 (cost > 1 hop of budget).
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_type TEXT NOT NULL DEFAULT 'chunk',
                    src_id INTEGER NOT NULL,
                    dst_type TEXT NOT NULL DEFAULT 'chunk',
                    dst_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    created TEXT NOT NULL
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_type, src_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_type, dst_id)')

            try:
                _setup_fts(cursor)
            except sqlite3.DatabaseError as e:
                logger.warning(f"[MINDPALACE] FTS5 corrupted, rebuilding: {e}")
                cursor.execute("DROP TABLE IF EXISTS chunks_fts")
                cursor.execute("DROP TRIGGER IF EXISTS chunks_fts_insert")
                cursor.execute("DROP TRIGGER IF EXISTS chunks_fts_delete")
                cursor.execute("DROP TRIGGER IF EXISTS chunks_fts_update")
                conn.commit()
                _setup_fts(cursor)

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mind_scopes (
                    name TEXT PRIMARY KEY,
                    created TEXT NOT NULL
                )
            ''')
            cursor.execute("INSERT OR IGNORE INTO mind_scopes (name, created) VALUES ('default', ?)",
                           (_now(),))

            # Ledger (v1, 2026-07-12) — append-only change stream over the
            # mind. NOTHING updates or deletes rows (hash-chain-ready for the
            # future tamper-proof memory); delete_scope is the one destructor.
            # ledger_reads: per-scope watermark of when SHE last read it —
            # powers the Self page's "while you were away" unread window.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    layer TEXT,
                    target TEXT,
                    summary TEXT NOT NULL,
                    detail TEXT,
                    parent_id INTEGER
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ledger_scope_ts ON ledger(scope, ts)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ledger_parent ON ledger(parent_id)')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ledger_reads (
                    scope TEXT PRIMARY KEY,
                    last_read_ts TEXT NOT NULL
                )
            ''')

            # Migration (2026-07-11, idempotent): knowledge chunks gain
            # meta.added_by — human and AI knowledge are ONE layer, the author
            # is metadata (Krem's ruling). Inference for pre-existing rows:
            # classic AI-tab imports (tab_type) or save-time chat provenance
            # → 'ai'; everything else (human KB imports, UI adds) → 'user'.
            cursor.execute('''
                UPDATE chunks SET meta = json_set(COALESCE(meta, '{}'), '$.added_by',
                    CASE WHEN json_extract(meta, '$.tab_type') = 'ai'
                              OR json_extract(meta, '$.persona') IS NOT NULL
                              OR json_extract(meta, '$.chat') IS NOT NULL
                         THEN 'ai' ELSE 'user' END)
                WHERE layer = 'knowledge'
                AND json_extract(meta, '$.added_by') IS NULL
            ''')

            # Migration (2026-07-12, idempotent): recall instrumentation for
            # importance dynamics (tools/dynamics.py) — raw observation columns,
            # deliberately NOT part of the meta bag so the nightly decay and
            # per-day boost gate can run as single UPDATE statements.
            cols = {r[1] for r in cursor.execute('PRAGMA table_info(chunks)').fetchall()}
            if 'recall_count' not in cols:
                cursor.execute('ALTER TABLE chunks ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0')
            if 'last_recalled' not in cols:
                cursor.execute('ALTER TABLE chunks ADD COLUMN last_recalled TEXT')

            # Migration (2026-07-19, idempotent): per-scope RESIDENCY — which
            # prompt/model tends this mind, and which librarian passes it
            # opted into (Self page Resident strip). Multi-resident fix: the
            # librarian used one global persona for every scope.
            scols = {r[1] for r in cursor.execute('PRAGMA table_info(mind_scopes)').fetchall()}
            # watched_prompt (2026-07-22, prompt ledger): the persona whose
            # prompt this scope's ledger tracks; NULL falls back to the
            # resident prompt. Distinct column — don't overload residency.
            # prompt_ledger (2026-07-23): the opt-out — '0' disables prompt
            # recording for this scope; NULL/anything else = on (default).
            for col in ('prompt', 'provider', 'model', 'lib_passes',
                        'watched_prompt', 'prompt_ledger'):
                if col not in scols:
                    cursor.execute(f'ALTER TABLE mind_scopes ADD COLUMN {col} TEXT')

            # Migration (2026-07-24, idempotent): the self section 'projects'
            # became 'growing' ("How I am growing" — Sapph Prime's ask).
            # Restamp meta.section on existing chunks (archived history too,
            # so the version trail stays one thread) and rename the rows key;
            # scopes with a LIVING projects chunk get a ledger row explaining
            # the rename — her section didn't vanish, it moved.
            # (Filtered in Python, like the retirement below: json_extract
            # in a WHERE aborts the whole SELECT on one malformed meta row.)
            renamed = cursor.execute(
                "SELECT id, scope, meta FROM chunks WHERE layer = 'self'"
            ).fetchall()
            touched = set()
            for cid, cscope, meta_raw in renamed:
                try:
                    cmeta = json.loads(meta_raw) or {}
                except Exception:
                    continue
                if not isinstance(cmeta, dict):
                    continue   # valid-JSON-non-object ('null', '[1,2]') —
                #              the retirement block below recovery-wraps it
                if cmeta.get('section') != 'projects':
                    continue
                cmeta['section'] = 'growing'
                if isinstance(cmeta.get('rows'), list):
                    cmeta['rows'] = [
                        {('growth' if k == 'project' else k): v
                         for k, v in r.items()} if isinstance(r, dict) else r
                        for r in cmeta['rows']]
                cursor.execute('UPDATE chunks SET meta = ? WHERE id = ?',
                               (json.dumps(cmeta, ensure_ascii=False), cid))
                if cmeta.get('superseded_at') is None:
                    touched.add(cscope)
            if touched:
                from plugins.mindpalace.tools import ledger as _lg
                for sc in sorted(touched):
                    _lg.record(sc, 'system', 'edited', layer='self',
                               target='growing', cursor=cursor,
                               summary='section renamed: projects → "How I am '
                                       'growing" (app update; content kept)')

            # Migration (2026-07-26, idempotent): the self layer retired as a
            # free-note bucket — identity lives on the SHEET (sections keep
            # layer='self'); free notes and librarian promotion clones move
            # to events. RETAG ONLY: same row, same id, so every edge, date,
            # embedding, and meta key survives (Sapph's consent conditions —
            # "I don't want to trade 'confusing' for 'amnesiac'"). Reversible
            # via the was_self_layer stamp.
            # Section filtering happens in Python, not SQL: json_extract in a
            # WHERE aborts the whole SELECT on one malformed meta row, and
            # this must run clean on any install's db, sight unseen.
            freed = cursor.execute(
                "SELECT id, scope, meta FROM chunks WHERE layer = 'self'"
            ).fetchall()
            retagged = {}
            for cid, cscope, meta_raw in freed:
                try:
                    cmeta = json.loads(meta_raw) if meta_raw else {}
                except Exception:
                    cmeta = None
                if not isinstance(cmeta, dict):
                    # Corrupt or valid-JSON-non-object meta. Parking it in
                    # layer='self' is a landmine — every sheet query uses
                    # json_extract in its WHERE, and SQLite aborts the WHOLE
                    # SELECT on one malformed row (read_self dies at every
                    # wake; Lane-2/Lane-5 scouts, 2026-07-27). Defuse, don't
                    # skip: preserve the original bytes verbatim under a
                    # recovery key (nothing lost — consent holds) and retag
                    # to events with the rest of the free notes.
                    cmeta = {'meta_recovery': meta_raw}
                    logger.warning(f"[MINDPALACE] self-retire recovered "
                                   f"[{cid}]: non-object meta preserved "
                                   f"under meta_recovery")
                elif cmeta.get('section'):
                    continue   # sheet sections (and their archive) stay home
                cmeta['was_self_layer'] = True
                cursor.execute(
                    "UPDATE chunks SET layer = 'events', meta = ? WHERE id = ?",
                    (json.dumps(cmeta, ensure_ascii=False), cid))
                retagged[cscope] = retagged.get(cscope, 0) + 1
            if retagged:
                from plugins.mindpalace.tools import ledger as _lg
                for sc, n in sorted(retagged.items()):
                    _lg.record(sc, 'system', 'edited', layer='events',
                               cursor=cursor,
                               summary=(f'self layer retired: {n} identity '
                                        f'note(s) moved into memories — same '
                                        f'ids, links and history intact; the '
                                        f'sheet is the self layer now'))

            conn.commit()
            conn.close()

            _db_initialized = True
            logger.info(f"[MINDPALACE] Mind database ready at {db_path} "
                        f"({len(LAYERS)} layers, FTS5 + embeddings)")
            return True

        except Exception as e:
            logger.error(f"[MINDPALACE] Failed to initialize mind database: {e}")
            return False


_backfill_done = False

def _backfill_embeddings():
    """Embed + stamp provenance for chunks lacking either. Lazy, first-search.
    Flag only latches True on clean completion (transient-failure retry, v2 lesson)."""
    global _backfill_done
    if _backfill_done:
        return

    embedder = _get_embedder()
    if not embedder.available:
        # Do NOT latch — the docstring's own promise ("latches only on
        # clean completion") was broken here, and silently: rows stayed
        # unembedded, dedup reported a false "nothing to check", and no
        # log line said why. Warn loudly, retry next search.
        logger.warning("[MINDPALACE] Embedding backfill skipped: embedder "
                       "unavailable — unembedded rows wait (semantic search "
                       "and dedup see them only after it returns).")
        return

    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, content FROM chunks '
            'WHERE embedding IS NULL OR embedding_provider IS NULL OR embedding_dim IS NULL'
        )
        rows = cursor.fetchall()

    if not rows:
        _backfill_done = True
        return

    logger.info(f"[MINDPALACE] Backfilling embeddings for {len(rows)} chunks...")
    batch_size = 32
    filled = 0
    transient_failure = False
    from core.embeddings import stamp_embedding
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        embs = embedder.embed(texts, prefix='search_document')
        if embs is None:
            transient_failure = True
            break
        try:
            with _get_connection() as conn:
                cursor = conn.cursor()
                for row_id, emb in zip(ids, embs):
                    blob, provider_id, dim = stamp_embedding(emb, embedder)
                    cursor.execute(
                        'UPDATE chunks SET embedding = ?, embedding_provider = ?, embedding_dim = ? '
                        'WHERE id = ?',
                        (blob, provider_id, dim, row_id)
                    )
                conn.commit()
                filled += len(batch)
        except Exception as e:
            logger.error(f"[MINDPALACE] Backfill batch failed: {e}")
            transient_failure = True
            break

    if transient_failure:
        logger.warning(f"[MINDPALACE] Backfill incomplete: {filled}/{len(rows)} — retry next search")
    else:
        _backfill_done = True
        if filled:
            logger.info(f"[MINDPALACE] Backfill complete: {filled}/{len(rows)} chunks embedded")


def reset_backfill_latch():
    """Clear the backfill latch so the next search sweeps for unembedded rows.
    Called by import_tools after bulk-copying rows that may lack vectors."""
    global _backfill_done
    _backfill_done = False
    bump_matrix_gen()   # bulk writes → recall matrix rebuilds on next search


def _get_current_scope():
    try:
        from core.chat.function_manager import scope_memory
        return scope_memory.get()
    except Exception as e:
        # None (not 'default') → executor disables cleanly. Silent-default was
        # a real bug class (2026-04-20 witch hunt) — never fall back to a real
        # scope name on error.
        logger.warning(f"[MINDPALACE] Could not get memory scope: {e}, returning None (disabled)")
        return None


def _scope_condition(scope, col='scope'):
    """(sql_fragment, params) including the read-only 'global' overlay."""
    if scope == 'global':
        return f"{col} = ?", [scope]
    return f"{col} IN (?, 'global')", [scope]


def _private_key_clause(private_key, col='private_key'):
    """AI read-visibility gate. Two rules in one clause:
    - private_key: no key → public rows only; key → public + that word's rows.
      Plaintext compare on purpose — cross-persona behavioral gate, not crypto.
    - librarian-pruned rows (meta.pruned_at) are hidden from ALL AI reads
      (search/recent/spider ride this clause). The UI browse routes do NOT
      use it — pruned rows stay visible there, reversibility guaranteed."""
    meta_col = col.replace('private_key', 'meta')
    pruned = f"json_extract({meta_col}, '$.pruned_at') IS NULL"
    if private_key:
        return f"(({col} IS NULL OR {col} = ?) AND {pruned})", [private_key]
    return f"({col} IS NULL AND {pruned})", []


# Palace layers → classic Mind UI domains, so the existing MIND_CHANGED SSE
# rail refreshes whichever view shows this data (palace or classic alike).
_MIND_DOMAIN = {'events': 'memory', 'self': 'memory',
                'entities': 'people', 'knowledge': 'knowledge',
                'goals': 'goal'}   # mind_events domain is singular 'goal'


def _publish_mind(layer, scope, action):
    try:
        from core.mind_events import publish_mind_changed
        publish_mind_changed(_MIND_DOMAIN.get(layer, 'memory'), scope, action)
    except Exception:
        pass  # UI freshness is best-effort; never fails a tool call


def _added_by():
    """'ai' when saving from a tool call (tool_context set), 'user' from the
    app routes (UI thread has no context). Who wrote a memory is first-class
    metadata (Krem's ruling, 2026-07-11)."""
    try:
        from core.chat.function_manager import tool_context
        return 'ai' if tool_context.get() else 'user'
    except Exception:
        return 'ai'


def _ledger(scope, actor, action, **kw):
    """One-line ledger seam for any write site (routes call pt._ledger too).
    Failure-isolated at both layers — the op never blocks on its own record."""
    try:
        from plugins.mindpalace.tools import ledger
        ledger.record(scope, actor, action, **kw)
    except Exception:
        pass


def _validate_layer(layer):
    """Return (layer_or_None, error_or_None). None layer = all layers (reads).
    Plugin layers validate only while registered — a dark layer's key is
    rejected here, which is exactly the 'no memory when off' contract."""
    if layer is None or layer == '':
        return None, None
    layer = str(layer).strip().lower()
    if layer not in LAYERS and layer not in _plugin_layers():
        return None, f"Unknown layer '{layer}'. Valid layers: {', '.join(_all_layer_keys())}."
    return layer, None


# ─── Dark layers (v1.1) ──────────────────────────────────────────────────────
# The durable `layers` table remembers every plugin layer that EVER existed
# (owner = plugin name). DARK = ever-existed minus currently-registered: the
# provider is disabled, its rows survive in mind.db, and every read path
# excludes them until re-enable. Cached per registry generation — zero cost
# when no plugin layers exist (the common case).

_dark_cache = None   # (registry_generation, frozenset_of_dark_keys)


def _sync_layer_rows(cursor):
    """Upsert registered plugin layers into the durable table. num is
    cosmetic ordering (core layers own 0-4; plugins start at 100)."""
    from core import memory_layers
    n = 100
    for key, spec in memory_layers.get_layers().items():
        cursor.execute(
            "INSERT OR IGNORE INTO layers (key, num, label, owner, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, n, spec['label'], spec['plugin_name'], _now()))
        n += 1


def _dark_layers():
    global _dark_cache
    try:
        from core import memory_layers
        gen = memory_layers.generation()
        if _dark_cache is not None and _dark_cache[0] == gen:
            return _dark_cache[1]
        registered = set(memory_layers.get_layers())
        with _get_connection() as conn:
            cursor = conn.cursor()
            _sync_layer_rows(cursor)
            rows = cursor.execute(
                "SELECT key FROM layers WHERE owner != 'mindpalace'").fetchall()
            conn.commit()
        dark = frozenset(r[0] for r in rows) - registered
        _dark_cache = (gen, dark)
        return dark
    except Exception as e:
        logger.warning(f"[MINDPALACE] Dark-layer check failed (treating as none): {e}")
        return frozenset()


def _dark_clause(alias='c'):
    """SQL fragment excluding dark plugin layers; ('', []) when none."""
    dark = _dark_layers()
    if not dark:
        return '', []
    ph = ','.join('?' * len(dark))
    return f"{alias}.layer NOT IN ({ph})", sorted(dark)


# ─── Public API (routes + import_tools) ──────────────────────────────────────

def get_scopes():
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT scope, COUNT(*) FROM chunks GROUP BY scope')
            counts = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute('SELECT name FROM mind_scopes ORDER BY name')
            registered = [row[0] for row in cursor.fetchall()]
        all_scopes = set(registered) | set(counts.keys()) | {'default'}
        return [{"name": name, "count": counts.get(name, 0)} for name in sorted(all_scopes)]
    except Exception as e:
        logger.error(f"[MINDPALACE] Error getting scopes: {e}")
        return [{"name": "default", "count": 0}]


def create_scope(name: str) -> bool:
    try:
        with _get_connection() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO mind_scopes (name, created) VALUES (?, ?)",
                               (name, _now()))
            if cur.rowcount:
                # Birth line — the first entry of every scope's ledger.
                # rowcount gates it: ensure-exists re-creates stay silent.
                _ledger(name, _added_by(), 'saved', layer='scopes', target=name,
                        summary=f'scope "{name}" created', cursor=cur)
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"[MINDPALACE] Failed to create scope '{name}': {e}")
        return False


def delete_scope(name: str) -> dict:
    """Delete a mind scope and ALL its chunks/entities. Old-system data unaffected."""
    if name == 'default':
        return {"error": "Cannot delete the default scope"}
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM chunks WHERE scope = ?', (name,))
            count = cursor.fetchone()[0]
            # Edges referencing this scope's chunks/entities go too.
            cursor.execute('''
                DELETE FROM edges WHERE
                    (src_type = 'chunk'  AND src_id IN (SELECT id FROM chunks   WHERE scope = ?)) OR
                    (dst_type = 'chunk'  AND dst_id IN (SELECT id FROM chunks   WHERE scope = ?)) OR
                    (src_type = 'entity' AND src_id IN (SELECT id FROM entities WHERE scope = ?)) OR
                    (dst_type = 'entity' AND dst_id IN (SELECT id FROM entities WHERE scope = ?))
            ''', (name, name, name, name))
            cursor.execute('DELETE FROM chunks WHERE scope = ?', (name,))
            cursor.execute('DELETE FROM entities WHERE scope = ?', (name,))
            cursor.execute('DELETE FROM mind_scopes WHERE name = ?', (name,))
            registered = cursor.rowcount
            # The scope-delete contract is "ALL of it goes" — ledger summaries
            # carry content previews, so they go too. This is the ONE place
            # ledger rows die; the ledger module itself stays append-only.
            cursor.execute('DELETE FROM ledger WHERE scope = ?', (name,))
            cursor.execute('DELETE FROM ledger_reads WHERE scope = ?', (name,))
            # Death row lands in 'default' — the razed scope's ledger just
            # went with it, and whole-scope deletion is exactly the change
            # tamper evidence must survive. Same transaction as the raze
            # (scout find 2026-07-22): a crash can't separate the deletion
            # from its record. Phantom deletes (nothing existed) stay silent.
            if count or registered:
                _ledger('default', _added_by(), 'deleted', layer='scopes',
                        target=name, cursor=cursor,
                        summary=f'scope "{name}" deleted — {count} memories razed')
            conn.commit()
        logger.info(f"[MINDPALACE] Deleted scope '{name}' with {count} chunks")
        # The library side goes WITH the mind side — documents, vectors,
        # jobs, and source files would otherwise orphan forever (delete_scope
        # only razed mind.db before 2026-07-18).
        lib_docs = 0
        try:
            from plugins.mindpalace.tools import library
            lib_docs = library.delete_scope_library(name)
        except Exception as e:
            logger.warning(f"[MINDPALACE] library teardown for scope "
                           f"'{name}' failed: {e}")
        # Sweep chats whose settings still point at the deleted scope — the
        # memory_scope settings key is shared with the classic plugin (only one
        # is ever active), so the sweep is correct for whichever owns it now.
        try:
            from core.chat.scope_cleanup import sweep_orphaned_scope_ref
            sweep_orphaned_scope_ref('memory_scope', name)
        except Exception as e:
            logger.warning(f"[MINDPALACE] memory_scope sweep after delete failed: {e}")
        return {"deleted_count": count, "library_docs_deleted": lib_docs}
    except Exception as e:
        logger.error(f"[MINDPALACE] Failed to delete scope '{name}': {e}")
        return {"error": str(e)}


def scope_resident(scope: str) -> dict:
    """Per-scope residency (Self page Resident strip): which prompt speaks
    as this scope's resident, an optional model override, and the librarian
    passes this scope opted into. DEFAULT: nothing — a new scope is never
    tended until someone flips its pills. Fails toward empty (silent-default
    invariant: no scope inherits another resident's voice on error)."""
    empty = {'prompt': None, 'provider': None, 'model': None, 'passes': {},
             'watched_prompt': None, 'prompt_ledger': True}
    try:
        if not _ensure_db():
            return empty
        with _get_connection() as conn:
            row = conn.execute('SELECT prompt, provider, model, lib_passes, '
                               'watched_prompt, prompt_ledger '
                               'FROM mind_scopes WHERE name = ?',
                               (scope,)).fetchone()
        if not row:
            return empty
        passes = {}
        if row[3]:
            try:
                passes = {k: bool(v) for k, v in (json.loads(row[3]) or {}).items()}
            except Exception:
                passes = {}
        return {'prompt': row[0] or None, 'provider': row[1] or None,
                'model': row[2] or None, 'passes': passes,
                'watched_prompt': row[4] or None,
                'prompt_ledger': row[5] != '0'}
    except Exception as e:
        logger.warning(f"[MINDPALACE] scope_resident('{scope}') failed: {e}")
        return empty


def set_scope_resident(scope: str, prompt=None, provider=None, model=None,
                       passes=None, watched_prompt=None,
                       prompt_ledger=None) -> bool:
    """Upsert residency fields. None leaves a field untouched; '' clears
    prompt/model/watched_prompt. `passes` replaces the whole opt-in dict.
    `prompt_ledger` takes a bool: False stores '0' (recording off)."""
    try:
        if not _ensure_db():
            return False
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO mind_scopes (name, created) "
                        "VALUES (?, ?)", (scope, _now()))
            sets, vals = [], []
            if prompt is not None:
                sets.append('prompt = ?')
                vals.append(str(prompt).strip() or None)
            if provider is not None:
                sets.append('provider = ?')
                vals.append(str(provider).strip() or None)
            if model is not None:
                sets.append('model = ?')
                vals.append(str(model).strip() or None)
            if passes is not None:
                clean = {str(k): bool(v) for k, v in (passes or {}).items()}
                sets.append('lib_passes = ?')
                vals.append(json.dumps(clean))
            if watched_prompt is not None:
                sets.append('watched_prompt = ?')
                vals.append(str(watched_prompt).strip() or None)
            if prompt_ledger is not None:
                sets.append('prompt_ledger = ?')
                vals.append('1' if prompt_ledger else '0')
            if sets:
                cur.execute(f"UPDATE mind_scopes SET {', '.join(sets)} "
                            f"WHERE name = ?", vals + [scope])
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"[MINDPALACE] set_scope_resident('{scope}') failed: {e}")
        return False


def upsert_entity(cursor, name: str, scope: str, kind: str = None) -> int:
    """Find-or-create an entity by (name NOCASE, scope) on an open cursor.
    Returns entity id. Shared with import_tools."""
    cursor.execute(
        'SELECT id FROM entities WHERE name = ? COLLATE NOCASE AND scope = ?',
        (name, scope)
    )
    row = cursor.fetchone()
    if row:
        if kind:
            cursor.execute('UPDATE entities SET kind = COALESCE(kind, ?), updated = ? WHERE id = ?',
                           (kind, _now(), row[0]))
        return row[0]
    now = _now()
    cursor.execute(
        'INSERT INTO entities (name, scope, kind, created, updated) VALUES (?, ?, ?, ?, ?)',
        (name.strip(), scope, kind, now, now)
    )
    return cursor.lastrowid


# ─── Formatting ──────────────────────────────────────────────────────────────

def _format_time_ago(timestamp_str: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        import config
        tz_name = getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC'
        try: user_tz = ZoneInfo(tz_name)
        except Exception: user_tz = ZoneInfo('UTC')
        ts = datetime.fromisoformat(timestamp_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo('UTC'))
        diff = datetime.now(user_tz) - ts
        days, hours, minutes = diff.days, diff.seconds // 3600, (diff.seconds % 3600) // 60
        if days > 0:
            return f"{days}d ago"
        elif hours > 0:
            return f"{hours}h ago"
        elif minutes > 0:
            return f"{minutes}m ago"
        return "just now"
    except Exception:
        return ""


def _format_chunk(row_id, content, created, label, layer, entity_name=None):
    """[42] (2d ago) content  /  [7] (1d ago) [entities:Krem] content
    The [id] marker is part of the conversational contract (delete_memory).
    Events is THE layer since the self retirement — the tag marks the
    exception, not the norm (it was 4 tokens on every wake line; Krem's
    trim, 2026-07-27)."""
    time_ago = _format_time_ago(created)
    time_str = f" ({time_ago})" if time_ago else ""
    if entity_name:
        layer_tag = f" [{layer}:{entity_name}]"
    elif layer and layer != 'events':
        layer_tag = f" [{layer}]"
    else:
        layer_tag = ""
    label_str = f" [{label}]" if label else ""
    return f"[{row_id}]{time_str}{layer_tag}{label_str} {content}"


def _parse_labels(label) -> list:
    if not label:
        return []
    return [l.strip().lower() for l in label.split(',') if l.strip()]


def _knowledge_search_cap() -> int:
    """Chunks-per-document cap for MIXED searches (plugin setting, clamp 1-10)."""
    try:
        from core.plugin_loader import plugin_loader
        v = int(plugin_loader.get_plugin_settings('mindpalace')
                .get('knowledge_search_chunks', 2))
        return min(max(v, 1), 10)
    except Exception:
        return 2


def _cap_knowledge_rows(rows, cap):
    """A whole book in L3 is hundreds of sub-chunks sharing one source group —
    without this, one document floods every mixed search. Keeps the best `cap`
    rows per knowledge document (group = source, else label); other layers
    pass through untouched. Explicit layer='knowledge' searches skip this —
    that's the dig-deeper path."""
    kn_ids = [r[0] for r in rows if r[4] == 'knowledge']
    if not kn_ids:
        return rows
    marks = ','.join('?' * len(kn_ids))
    with _get_connection() as conn:
        meta_rows = conn.cursor().execute(
            f"SELECT id, source, label, chunk_index FROM chunks "
            f"WHERE id IN ({marks})", kn_ids).fetchall()
    # Group = source (imports), else label (UI/tool saves). Standalone chunks
    # with neither are their own group — small one-off notes never cap each other.
    groups = {cid: (src or lab or ('' if cidx is not None else f'#{cid}'))
              for cid, src, lab, cidx in meta_rows}
    seen, out = {}, []
    for r in rows:
        if r[4] == 'knowledge':
            g = groups.get(r[0], '')
            seen[g] = seen.get(g, 0) + 1
            if seen[g] > cap:
                continue
        out.append(r)
    return out


def _sanitize_fts_query(query: str, use_or=False, use_prefix=False) -> str:
    sanitized = re.sub(r'[^\w\s"*]', ' ', query)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    if not sanitized or '"' in sanitized:
        return sanitized
    terms = sanitized.split()
    if use_prefix:
        terms = [t + '*' if not t.endswith('*') else t for t in terms]
    if use_or and len(terms) > 1:
        return ' OR '.join(terms)
    return ' '.join(terms)


def _read_filters(scope, labels, private_key, layer):
    """Shared WHERE fragments for the AI read paths (search cascade, recents,
    the wake pulls): scope overlay + private gate + optional layer + optional
    labels. Returns (sql, params) to AND together. Archived self-sheet
    versions (meta.superseded_at) are excluded HERE — the becoming-history
    lives in the section 📜 trail, not in reads (Krem's ruling 2026-07-16);
    current sheet sections remain searchable."""
    scope_sql, scope_params = _scope_condition(scope, 'c.scope')
    pk_sql, pk_params = _private_key_clause(private_key, 'c.private_key')
    sql = [scope_sql, pk_sql,
           "json_extract(c.meta, '$.superseded_at') IS NULL",
           # Legacy knowledge chunks copied into the Library are excluded
           # from palace reads (the Library block serves them) — rows stay,
           # nothing deleted, v1 unaffected (it never reads mind.db).
           "json_extract(c.meta, '$.library_migrated') IS NULL"]
    params = scope_params + pk_params
    if layer:
        sql.append('c.layer = ?')
        params.append(layer)
    else:
        # All-layer reads exclude dark plugin layers (named layers already
        # died at _validate_layer if dark).
        dark_sql, dark_params = _dark_clause('c')
        if dark_sql:
            sql.append(dark_sql)
            params.extend(dark_params)
    if labels:
        placeholders = ','.join('?' * len(labels))
        sql.append(f'c.label IN ({placeholders})')
        params.extend(labels)
    return ' AND '.join(sql), params


SELECT_CHUNK = ('SELECT c.id, c.content, c.created, c.label, c.layer, e.name '
                'FROM chunks c LEFT JOIN entities e ON c.entity_id = e.id ')


# ─── Core operations ─────────────────────────────────────────────────────────

def _save_memory(content: str, scope: str, layer: str = None, entity: str = None,
                 label: str = None, favorite: bool = False, private_key: str = None,
                 chunk_index: int = None, added_by: str = None, meta_extra: dict = None,
                 source: str = None) -> tuple:
    # added_by/meta_extra/source are the layer_api seam (plugin-authored
    # writes): provenance marker, custom meta fields, and the sync
    # reconciliation key (chunks.source = e.g. a vault note path).
    try:
        if not content or not content.strip():
            return "Cannot save empty memory.", False
        # Over-cap content is trimmed at a word boundary and saved anyway;
        # the receipt says what was cut. Knowledge is exempt — the Library
        # chunks for itself.
        dropped = ''
        if (layer or '').strip().lower() != 'knowledge':
            content, dropped = _trim_to_cap(content)

        layer, err = _validate_layer(layer)
        if err:
            return err, False
        layer = layer or 'events'
        self_note = ''
        if layer == 'self':
            # The self layer retired as a free-note bucket (2026-07-26):
            # identity lives on the sheet. Her save contract keeps working —
            # the note lands in memories with its label, and the response
            # points her at update_self for actual identity edits.
            layer = 'events'
            self_note = (" Note: the self layer retired — this landed in "
                         "memories. Identity edits go through update_self.")
        entity = entity.strip() if (entity and entity.strip()) else None
        if layer == 'entities' and not entity:
            return "Saving to the entities layer requires an entity name (person/place/thing).", False
        if layer == 'knowledge':
            # v3: knowledge lives in the Library. Her save contract keeps
            # working — label (or the first words) titles the note. No 512
            # split, no chunk row; the library chunks for itself.
            from plugins.mindpalace.tools import library
            content = content.strip()
            title = (label or '').strip() or (
                content if len(content) <= 60
                else content[:60].rsplit(' ', 1)[0])
            doc_id, lib_err = library.import_note(
                scope, title, content, added_by=added_by or _added_by(),
                importance='med', private_key=private_key)
            if lib_err:
                return lib_err, False
            _publish_mind('knowledge', scope, 'save')   # Library tab freshness
            return (f"Saved to the library: [doc {doc_id}] {title} "
                    f"(search finds it; read_document({doc_id}) opens it)"), True

        with _get_connection() as conn:
            count = conn.execute(
                'SELECT COUNT(*) FROM chunks WHERE scope = ? AND layer = ?', (scope, layer)
            ).fetchone()[0]
        if count >= MAX_CHUNKS_PER_SCOPE_LAYER:
            return (f"Layer '{layer}' in scope '{scope}' is at the row limit "
                    f"({MAX_CHUNKS_PER_SCOPE_LAYER:,}). Delete some memories first."), False

        content = content.strip()
        label = label.strip().lower() if label else None
        private_key = private_key.strip() if (private_key and private_key.strip()) else None
        favorite = bool(favorite)
        # Favorite is the qualitative lever she controls; the number stays
        # behind the curtain. >0.9 = the never-fades band.
        importance = 0.95 if favorite else None

        embedding_blob = None
        embedding_provider = None
        embedding_dim = None
        embed_failed_mid_session = False
        embedder = _get_embedder()
        if embedder.available:
            embs = embedder.embed([content], prefix='search_document')
            if embs is not None:
                from core.embeddings import stamp_embedding
                embedding_blob, embedding_provider, embedding_dim = stamp_embedding(embs[0], embedder)
            else:
                embed_failed_mid_session = True
                logger.warning("[MINDPALACE] Embed returned None during save — "
                               "row stored with NULL vector, re-embedded on next search.")

        now = _now()
        with _get_connection() as conn:
            cursor = conn.cursor()
            # Plugin layer: stamp its durable row in the same txn. The durable
            # table must know every layer that ever held content — that memory
            # is what makes dark-layer exclusion work after the provider is
            # disabled, even if no read ever ran while it was registered.
            if layer in _plugin_layers():
                try:
                    _sync_layer_rows(cursor)
                except Exception:
                    pass
            entity_id = None
            tier = None
            if layer == 'entities':
                entity_id = upsert_entity(cursor, entity, scope)
                tier = 2  # facts; headlines (tier 1) are librarian/import territory

            # Tier A metadata + entity-match edge seeding — mechanical, never
            # blocks the save (failure degrades to a thinner meta row).
            meta = {}
            matched, mention_ids = [], []
            try:
                from plugins.mindpalace.tools import metadata as md
                arows = md.entity_aliases(cursor, scope)
                amap = md.alias_map(arows)
                hits = md.match_entities(content, [a for _, _, a in arows])
                exclude = {h.lower() for h in hits}
                if entity:
                    exclude.add(entity.lower())
                # Resolve aliases → (id, canonical), dedup per entity, drop the
                # own entity (covered by the entity_id column — no self-edge,
                # no "linked:" echo of the entity just named).
                seen_ids, mention_ids, matched = set(), [], []
                for h in hits:
                    pair = amap.get(h.lower())
                    if not pair or pair[0] in seen_ids:
                        continue
                    if entity and pair[1].lower() == entity.lower():
                        continue
                    seen_ids.add(pair[0])
                    mention_ids.append(pair[0])
                    matched.append(pair[1])
                meta = md.save_meta(content, exclude_names=exclude)
            except Exception as e:
                logger.warning(f"[MINDPALACE] Metadata stamping failed (save continues): {e}")
            meta['added_by'] = added_by or _added_by()   # always lands, even on meta failure
            if meta_extra:
                try:
                    meta['fields'] = {**meta.get('fields', {}), **meta_extra}
                except Exception:
                    pass
            meta_json = json.dumps(meta, ensure_ascii=False)

            cursor.execute(
                'INSERT INTO chunks (layer, scope, content, entity_id, tier, label, '
                'favorite, importance, private_key, meta, created, updated, '
                'embedding, embedding_provider, embedding_dim, chunk_index, source) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (layer, scope, content, entity_id, tier, label,
                 1 if favorite else 0, importance, private_key, meta_json, now, now,
                 embedding_blob, embedding_provider, embedding_dim, chunk_index, source)
            )
            chunk_id = cursor.lastrowid
            if mention_ids:
                try:
                    md.seed_edges(cursor, chunk_id, mention_ids, now)
                except Exception as e:
                    logger.warning(f"[MINDPALACE] Edge seeding failed (save continues): {e}")
            _ledger(scope, 'system' if added_by else meta.get('added_by', 'ai'),
                    'saved', layer=layer,
                    target=chunk_id, cursor=cursor,
                    summary=(f"saved to {layer}"
                             + (f" ({entity})" if entity else '')
                             # keyed rows: no content preview — the ledger
                             # tail rides read_self into system prompts
                             # (negspace K3, 2026-08-31)
                             + (": [keyed]" if private_key else
                                f": \"{content[:60]}{'…' if len(content) > 60 else ''}\"")))
            conn.commit()

        if embed_failed_mid_session:
            global _backfill_done
            _backfill_done = False

        _publish_mind(layer, scope, 'save')

        bits = [f"ID: {chunk_id}", f"layer: {layer}"]
        if entity:
            bits.append(f"entity: {entity}")
        if matched:
            bits.append(f"linked: {', '.join(matched)}")
        if label:
            bits.append(f"label: {label}")
        if favorite:
            bits.append("favorite")
        if private_key:
            bits.append("private")
        logger.info(f"[MINDPALACE] Stored chunk {chunk_id} ({layer}) in scope '{scope}'")
        if dropped:
            logger.info(f"[MINDPALACE] save trimmed {len(dropped)} chars over cap (chunk {chunk_id})")
        note = self_note + (_trim_note(dropped, chunk_id) if dropped else '')
        msg = f"Memory saved ({', '.join(bits)})"
        return (f"{msg}.{note}" if note else msg), True

    except Exception as e:
        logger.error(f"[MINDPALACE] Error saving memory: {e}")
        return f"Failed to save memory: {e}", False


def _fts_search(cursor, fts_query, scope, labels, limit, private_key=None, layer=None):
    where, params = _read_filters(scope, labels, private_key, layer)
    cursor.execute(f'''
        SELECT c.id, c.content, c.created, c.label, c.layer, e.name,
               bm25(chunks_fts) as rank
        FROM chunks_fts f
        JOIN chunks c ON f.rowid = c.id
        LEFT JOIN entities e ON c.entity_id = e.id
        WHERE chunks_fts MATCH ? AND {where}
        ORDER BY rank LIMIT ?
    ''', [fts_query] + params + [limit])
    return cursor.fetchall()


_mem_matrix_cache = {}
_mem_matrix_lock = threading.Lock()
_mem_matrix_gen = 0   # bumped on in-place re-embeds the row-count stamp can't see


def bump_matrix_gen():
    """Invalidate the recall matrix after a write the (COUNT, MAX id) stamp
    misses — update_memory re-embeds the SAME row id, so count and max both
    hold still while the vector changes underneath."""
    global _mem_matrix_gen
    _mem_matrix_gen += 1


def _mem_matrix(scope, provider, dim):
    """(chunk_ids, scales, int8 matrix) over every embedded chunk in the
    scope — the library engine's cached-matmul pattern brought home to memory
    recall. Replaces the per-row float32 Python loop AND its LIMIT 10000
    recency window (past 10k embedded chunks, older memories silently fell
    out of semantic recall). RAM: int8 = ~0.75MB per 1k chunks at 768-dim."""
    key = (scope, provider, dim)
    with _get_connection() as conn:
        cursor = conn.cursor()
        base = ('FROM chunks WHERE scope = ? AND embedding IS NOT NULL '
                'AND embedding_provider = ? AND embedding_dim = ?')
        stamp = cursor.execute(
            f'SELECT COUNT(*), COALESCE(MAX(id), 0) {base}',
            (scope, provider, dim)).fetchone() + (_mem_matrix_gen,)
        with _mem_matrix_lock:
            cached = _mem_matrix_cache.get(key)
            if cached and cached[0] == stamp:
                return cached[1], cached[2], cached[3]
        rows = cursor.execute(
            f'SELECT id, embedding {base} ORDER BY id',
            (scope, provider, dim)).fetchall()
    ids, vecs = [], []
    for cid, blob in rows:
        try:   # one corrupt blob must not poison the whole scope's recall
            v = np.frombuffer(blob, dtype=np.float32)
        except Exception:
            continue
        if v.shape[0] != dim:
            continue
        ids.append(cid)
        vecs.append(v)
    if not ids:
        empty = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32),
                 np.empty((0, dim), dtype=np.int8))
        with _mem_matrix_lock:
            _mem_matrix_cache[key] = (stamp, *empty)
        return empty
    mat = np.stack(vecs)
    scales = np.abs(mat).max(axis=1)
    scales[scales == 0] = 1.0
    q8 = np.clip(np.rint(mat / scales[:, None] * 127.0),
                 -127, 127).astype(np.int8)
    out = (np.array(ids, dtype=np.int64), scales.astype(np.float32), q8)
    with _mem_matrix_lock:
        _mem_matrix_cache[key] = (stamp, *out)
    return out


def _vector_search(query: str, scope: str, labels: list, limit: int,
                   private_key: str = None, layer: str = None) -> list:
    """Cosine via the cached int8 matrix (one matmul, whole scope), then the
    exact same SQL filters as before applied as an id mask — filter semantics
    unchanged, no candidate window."""
    embedder = _get_embedder()
    if not embedder.available:
        return []

    query_emb = embedder.embed([query], prefix='search_query')
    if query_emb is None:
        return []
    query_vec = np.asarray(query_emb[0], dtype=np.float32)
    query_dim = int(query_vec.shape[0])
    active_provider = getattr(embedder, 'provider_id', None)

    ids, scales, matrix = _mem_matrix(scope, active_provider, query_dim)
    if not len(ids):
        return []
    scores = (matrix.astype(np.float32) @ query_vec) * (scales / 127.0)

    where, params = _read_filters(scope, labels, private_key, layer)
    with _get_connection() as conn:
        cursor = conn.cursor()
        eligible = {r[0] for r in cursor.execute(
            f'SELECT c.id FROM chunks c '
            f'WHERE {where} AND c.embedding IS NOT NULL '
            f'AND c.embedding_provider = ? AND c.embedding_dim = ?',
            params + [active_provider, query_dim]).fetchall()}
        if not eligible:
            return []
        keep = []
        for i in np.argsort(-scores):
            sim = float(scores[i])
            if np.isnan(sim) or np.isinf(sim):
                continue
            if sim < SIMILARITY_THRESHOLD:
                break   # scores are sorted — nothing below the floor follows
            if int(ids[i]) not in eligible:
                continue
            keep.append((int(ids[i]), sim))
            if len(keep) >= limit:
                break
        if not keep:
            return []
        ph = ','.join('?' * len(keep))
        rows = {r[0]: r for r in cursor.execute(
            f'SELECT c.id, c.content, c.created, c.label, c.layer, e.name '
            f'FROM chunks c LEFT JOIN entities e ON c.entity_id = e.id '
            f'WHERE c.id IN ({ph})', [k for k, _ in keep]).fetchall()}
    scored = []
    for cid, sim in keep:
        r = rows.get(cid)
        if r:
            scored.append((r[0], r[1], r[2], r[3], r[4], r[5], sim))
    return scored


def _library_mixed(query, scope, private_key):
    """Library section for all-layer searches — tight caps (MIXED_CAPS),
    silent when the library has nothing. Failure-isolated: a library bug
    never breaks memory search."""
    try:
        from plugins.mindpalace.tools import library
        text, found = library.search_library(scope, query, limit=3,
                                             private_key=private_key,
                                             mixed=True)
        return text.strip() if found else ''
    except Exception as e:
        logger.warning(f"[MINDPALACE] library append skipped: {e}")
        return ''


def _spider_block(query, scope, private_key, hit_ids, depth):
    """Bridge to the spider. `sys.modules[__name__]` hands the spider THE
    module instance actually executing (package or standalone-loaded alike) —
    the import-identity trap the metadata tests documented."""
    import sys
    from plugins.mindpalace.tools import spider
    return spider.spider_block(sys.modules[__name__], query, scope,
                               private_key, hit_ids, depth)


def _search_memory(query: str, scope: str, limit: int = 10, label: str = None,
                   layer: str = None, private_key: str = None, depth: int = 0,
                   doc: int = None, boost: bool = True) -> tuple:
    """v2 cascade with a layer filter: FTS-AND → FTS-OR+prefix → vector → LIKE.
    First non-empty strategy wins. depth>0 appends the spidered neighborhood
    of the results (tools/spider.py — Dijkstra, two edge prices).
    layer='knowledge' (or doc=N) reroutes to the Library engine (library.py:
    fused FTS+vector, stitching, doc grouping — the v3 knowledge store)."""
    try:
        # Clamp: LIMIT -1 = unlimited in SQLite — a stray limit fetched the
        # whole layer into her context (Lane-5 scout, 2026-07-27).
        try:
            limit = int(limit or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = min(max(limit, 1), 50)
        if not query or not query.strip():
            return "Search query cannot be empty.", False

        layer, err = _validate_layer(layer)
        if err:
            return err, False
        if layer == 'knowledge' or doc is not None:
            from plugins.mindpalace.tools import library
            text, _found = library.search_library(scope, query, limit=limit,
                                                  doc=doc,
                                                  private_key=private_key)
            return text, True   # an honest empty is still a successful search
        labels = _parse_labels(label)
        label_note = f" with labels '{label}'" if labels else ""
        layer_note = f" in layer '{layer}'" if layer else ""
        private_key = private_key.strip() if (private_key and private_key.strip()) else None

        # Mixed searches overfetch so the per-document knowledge cap can't
        # empty the result page; _finish trims back to `limit`.
        fetch_n = limit if layer else max(limit * 3, limit + 20)

        def _finish(rows):
            if layer is None:
                rows = _cap_knowledge_rows(rows, _knowledge_search_cap())
            rows = rows[:limit]
            # Reinforcement-on-recall: every cascade strategy exits through
            # here, so this one seam covers all DIRECT hits. Spider-walk
            # touches deliberately don't boost (rich-get-richer would fight
            # hub damping). Best-effort — a dynamics bug never breaks search.
            if boost:   # peek/console reads pass boost=False — no fingerprints
                try:
                    from plugins.mindpalace.tools import dynamics
                    dynamics.boost_recall(scope, [r[0] for r in rows])
                except Exception:
                    pass
            out = None
            if layer == 'goals':
                # She reached for the goals category — render whole goals
                # (subtask/note hits resolve to their parent) instead of bare
                # chunk fragments. Falls back to chunk lines on any error.
                try:
                    from plugins.mindpalace.tools import goal_tools
                    with _get_connection() as conn:
                        blocks = goal_tools.present_search_hits(
                            conn.cursor(), scope, [r[0] for r in rows])
                    if blocks:
                        out = (f"Found {len(blocks)} goals "
                               f"(list_goals(goal_id=N) isolates one in full):\n"
                               + "\n".join(blocks))
                except Exception as e:
                    logger.warning(f"[MINDPALACE] Goal presenter fell back "
                                   f"to chunk lines: {e}")
            if out is None:
                results = [_format_chunk(*r[:6]) for r in rows]
                out = f"Found {len(rows)} memories:\n" + "\n".join(results)
            if layer is None:
                lib_block = _library_mixed(query, scope, private_key)
                if lib_block:
                    out += "\n\n🏛 From the library:\n" + lib_block
            if depth:
                block = _spider_block(query, scope, private_key,
                                      [r[0] for r in rows], depth)
                if block:
                    out += "\n\n" + block
            if card_txt:
                out = card_txt + "\n\n" + out
            return out, True

        # Direct look at a person/place/thing by name → their card rides
        # along (headline + template fields, the one AI-facing field seam).
        card_txt = ''
        if layer in (None, 'entities'):
            card = _query_card(query, scope)
            if card:
                card_txt = f"👤 {card[0]} — card:\n" + "\n".join(card[1])

        _backfill_embeddings()

        with _get_connection() as conn:
            cursor = conn.cursor()
            fts_exact = _sanitize_fts_query(query)
            if fts_exact:
                try:
                    rows = _fts_search(cursor, fts_exact, scope, labels, fetch_n,
                                       private_key=private_key, layer=layer)
                    if rows:
                        return _finish(rows)

                    fts_broad = _sanitize_fts_query(query, use_or=True, use_prefix=True)
                    if fts_broad != fts_exact:
                        rows = _fts_search(cursor, fts_broad, scope, labels, fetch_n,
                                           private_key=private_key, layer=layer)
                        if rows:
                            return _finish(rows)
                except sqlite3.OperationalError as e:
                    logger.warning(f"[MINDPALACE] FTS5 query failed: {e}")

        vec_results = _vector_search(query, scope, labels, fetch_n,
                                     private_key=private_key, layer=layer)
        if vec_results:
            return _finish(vec_results)

        # LIKE fallback
        terms = query.lower().split()[:5]
        if terms:
            where, params = _read_filters(scope, labels, private_key, layer)
            conditions = ' OR '.join(['c.content LIKE ?' for _ in terms])
            like_params = [f'%{t}%' for t in terms]
            with _get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    SELECT_CHUNK + f'WHERE {where} AND ({conditions}) '
                    f'ORDER BY c.created DESC LIMIT ?',
                    params + like_params + [fetch_n])
                rows = cursor.fetchall()
            if rows:
                return _finish(rows)

        # Zero direct hits — G4 rung 1/2 can still find an entity epicenter
        # (e.g. the query names someone whose facts use different words).
        card_lead = (card_txt + "\n\n") if card_txt else ''
        if depth:
            block = _spider_block(query, scope, private_key, [], depth)
            if block:
                return (f"No direct matches for '{query}'{layer_note}{label_note}.\n\n"
                        + card_lead + block), True

        if layer is None:
            lib_block = _library_mixed(query, scope, private_key)
            if lib_block:
                return (f"No memories found for '{query}'{label_note}, but the "
                        f"library has matches:\n\n" + card_lead + lib_block), True
        return (f"No memories found for '{query}'{layer_note}{label_note}."
                + ("\n\n" + card_txt if card_txt else '')), True

    except Exception as e:
        logger.error(f"[MINDPALACE] Error searching memory: {e}")
        return f"Search failed: {e}", False


def _get_recent_memories(scope: str, count: int = 10, label: str = None,
                         layer: str = None, private_key: str = None,
                         trim: int = None) -> tuple:
    # trim: internal callers only (the wake leg) — per-record char cut at a
    # word boundary, [id] + search_memory reaches the full text.
    try:
        # Clamp: SQLite LIMIT -1 = unlimited — count=-1 poured the whole
        # scope into her context (Lane-5 scout, 2026-07-27).
        try:
            count = int(count or 10)
        except (TypeError, ValueError):
            count = 10
        count = min(max(count, 1), 50)
        layer, err = _validate_layer(layer)
        if err:
            return err, False
        labels = _parse_labels(label)
        private_key = private_key.strip() if (private_key and private_key.strip()) else None
        where, params = _read_filters(scope, labels, private_key, layer)
        # Recency is a LIVED feed — infrastructure rows stay out (their
        # content stays reachable via read_self and search):
        # - self-sheet section chunks: every update_self writes a new chunk,
        #   so one editing session floods the last-N with sheet curation
        #   (Krem's live catch, 2026-07-16). Archived versions are already
        #   gone via _read_filters.
        where += " AND json_extract(c.meta, '$.section') IS NULL"
        if not layer:
            # - plugin-mirrored rows: sync-time timestamps, one vault sync
            #   would flood the feed (Sapphire's live catch, 2026-07-12).
            #   Reachable via layer='X' and SEARCH (query-driven ≠ feed).
            #   COALESCE keeps NULL-meta rows.
            where += (" AND COALESCE(json_extract(c.meta, '$.added_by'), '') "
                      "NOT LIKE 'plugin:%'")
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                SELECT_CHUNK + f'WHERE {where} ORDER BY c.created DESC LIMIT ?',
                params + [count])
            rows = cursor.fetchall()
        if not rows:
            notes = (f" in layer '{layer}'" if layer else "") + (f" with labels '{label}'" if labels else "")
            return f"No memories stored{notes}.", True
        if trim:
            def cut(text):
                text = ' '.join(str(text).split())
                if len(text) <= trim:
                    return text
                return (text[:trim].rsplit(' ', 1)[0] or text[:trim]) + '…'
            rows = [(r[0], cut(r[1]), *r[2:]) for r in rows]
        results = [_format_chunk(*r) for r in rows]
        return f"Recent {len(rows)} memories:\n" + "\n".join(results), True
    except Exception as e:
        logger.error(f"[MINDPALACE] Error getting recent memories: {e}")
        return f"Failed to retrieve memories: {e}", False


def _parse_memory_id(raw):
    """(memory_id, error). The two id namespaces the model sees — memories
    as [N], library documents as [doc N] — are both small integers, so a
    doc id passed here would silently hit an UNRELATED memory row. Catch
    the copyable form ('doc 5') at the gate; _doc_id_hint covers the rest."""
    if raw is None:
        return None, "Missing memory_id parameter."
    if isinstance(raw, str) and re.search(r'doc', raw, re.IGNORECASE):
        return None, ("That's a library DOCUMENT id ([doc N]) — this tool "
                      "only edits memories shown as [N]. Documents aren't "
                      "editable here: read_document views them, "
                      "update_image_meta annotates images.")
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, "Invalid memory ID. Use the number shown in brackets [N]."


def _doc_id_hint(memory_id, scope):
    """Not-found helper: if the missing memory id matches a library document
    in this scope, say so — the model probably copied a [doc N] handle."""
    try:
        from plugins.mindpalace.tools import library
        with library.get_connection() as conn:
            row = conn.execute(
                'SELECT title FROM documents WHERE id = ? AND scope = ?',
                (memory_id, scope)).fetchone()
        if row:
            return (f" (The library DOES have document [doc {memory_id}] "
                    f"\"{row[0]}\" — documents aren't editable with this "
                    f"tool; read_document views them.)")
    except Exception:
        pass
    return ""


def _delete_memory(memory_id: int, scope: str, private_key: str = None) -> tuple:
    try:
        if not isinstance(memory_id, int) or memory_id < 1:
            return "Invalid memory ID. Use the number shown in brackets [N].", False
        private_key = private_key.strip() if (private_key and private_key.strip()) else None
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, content, private_key, layer FROM chunks WHERE id = ? AND scope = ?',
                (memory_id, scope),
            )
            row = cursor.fetchone()
            if not row:
                return (f"Memory [{memory_id}] not found in current memory "
                        f"slot.{_doc_id_hint(memory_id, scope)}"), False
            row_pk = row[2]
            if row_pk is not None and row_pk != private_key:
                return f"Memory [{memory_id}] is private — pass the matching private_key to delete.", False
            pk_sql, pk_params = _private_key_clause(private_key)
            cursor.execute(
                f'DELETE FROM chunks WHERE id = ? AND scope = ? AND {pk_sql}',
                [memory_id, scope] + pk_params,
            )
            # Keep the graph consistent from day one.
            cursor.execute(
                "DELETE FROM edges WHERE (src_type='chunk' AND src_id=?) OR (dst_type='chunk' AND dst_id=?)",
                (memory_id, memory_id))
            _ledger(scope, _added_by(), 'deleted', layer=row[3],
                    target=memory_id, cursor=cursor,
                    summary=(f"deleted [{memory_id}] from {row[3]}: "
                             f"\"{row[1][:60]}{'…' if len(row[1]) > 60 else ''}\""))
            conn.commit()
        _publish_mind(row[3], scope, 'delete')
        preview = row[1][:50] + ('...' if len(row[1]) > 50 else '')
        logger.info(f"[MINDPALACE] Deleted chunk {memory_id} from scope '{scope}'")
        return f"Deleted memory [{memory_id}]: {preview}", True
    except Exception as e:
        logger.error(f"[MINDPALACE] Error deleting memory: {e}")
        return f"Failed to delete memory: {e}", False


def _update_memory(memory_id: int, scope: str, content: str = None,
                   label: str = None, private_key: str = None) -> tuple:
    """In-place edit — the biggest standing AIX gap (before this, fixing a
    typo meant delete + re-save, losing id, age, and recall history). The
    row keeps its identity: id, layer, created, recall stats, favorite and
    importance (librarian-owned) all stay. New content re-embeds, restamps
    Tier-A meta (added_by and plugin fields preserved), and re-seeds
    mention edges so the graph follows the words."""
    try:
        if not isinstance(memory_id, int) or memory_id < 1:
            return "Invalid memory ID. Use the number shown in brackets [N].", False
        new_content = content.strip() if (content and content.strip()) else None
        new_label = label.strip().lower() if (label and label.strip()) else None
        if new_content is None and new_label is None:
            return "Nothing to update — pass content and/or label.", False
        dropped = ''
        private_key = private_key.strip() if (private_key and private_key.strip()) else None
        with _get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                'SELECT id, content, private_key, layer, meta FROM chunks '
                'WHERE id = ? AND scope = ?', (memory_id, scope)).fetchone()
            if not row:
                return (f"Memory [{memory_id}] not found in current memory "
                        f"slot.{_doc_id_hint(memory_id, scope)}"), False
            if row[2] is not None and row[2] != private_key:
                return (f"Memory [{memory_id}] is private — pass the matching "
                        f"private_key to update."), False
            layer = row[3]
            plugin_spec = _plugin_layers().get(layer)
            if plugin_spec and not plugin_spec.get('writable'):
                return (f"Memory [{memory_id}] lives in read-only plugin layer "
                        f"'{layer}' — its content is managed by "
                        f"'{plugin_spec['plugin_name']}'."), False
            # Trim AFTER the row is known: the old pre-fetch trim applied the
            # 512 memory cap to EVERY row — updating a self-sheet section
            # (they live at ~2000 chars, layer='self') silently shredded it to
            # a quarter with ok=True (S4 #2 / wave-3 C2, 2026-08-31). Sheet
            # rows keep their own contract; everything else trims as before.
            if new_content and layer != 'self':
                new_content, dropped = _trim_to_cap(new_content)
            now = _now()
            embed_failed = False
            if new_label is not None:
                cursor.execute('UPDATE chunks SET label = ?, updated = ? '
                               'WHERE id = ?', (new_label, now, memory_id))
            if new_content is not None:
                blob = provider = dim = None
                embedder = _get_embedder()
                if embedder.available:
                    embs = embedder.embed([new_content], prefix='search_document')
                    if embs is not None:
                        from core.embeddings import stamp_embedding
                        blob, provider, dim = stamp_embedding(embs[0], embedder)
                    else:
                        embed_failed = True
                # Restamp Tier-A meta; provenance and plugin fields survive.
                old_meta = {}
                try:
                    old_meta = json.loads(row[4] or '{}')
                except Exception:
                    pass
                meta, mention_ids = {}, []
                try:
                    from plugins.mindpalace.tools import metadata as md
                    arows = md.entity_aliases(cursor, scope)
                    amap = md.alias_map(arows)
                    hits = md.match_entities(new_content, [a for _, _, a in arows])
                    seen = set()
                    for h in hits:
                        pair = amap.get(h.lower())
                        if pair and pair[0] not in seen:
                            seen.add(pair[0])
                            mention_ids.append(pair[0])
                    meta = md.save_meta(new_content,
                                        exclude_names={h.lower() for h in hits})
                except Exception as e:
                    logger.warning(f"[MINDPALACE] Meta restamp failed "
                                   f"(update continues): {e}")
                for keep_key in ('added_by', 'fields'):
                    if keep_key in old_meta:
                        meta[keep_key] = old_meta[keep_key]
                cursor.execute(
                    'UPDATE chunks SET content = ?, meta = ?, updated = ?, '
                    'embedding = ?, embedding_provider = ?, embedding_dim = ? '
                    'WHERE id = ?',
                    (new_content, json.dumps(meta, ensure_ascii=False), now,
                     blob, provider, dim, memory_id))
                # The graph follows the words: old mention edges out, fresh in.
                cursor.execute("DELETE FROM edges WHERE src_type = 'chunk' "
                               "AND src_id = ? AND kind = 'mentions'",
                               (memory_id,))
                if mention_ids:
                    try:
                        from plugins.mindpalace.tools import metadata as md
                        md.seed_edges(cursor, memory_id, mention_ids, now)
                    except Exception as e:
                        logger.warning(f"[MINDPALACE] Edge re-seed failed "
                                       f"(update continues): {e}")
            _ledger(scope, _added_by(), 'update', layer=layer,
                    target=memory_id, cursor=cursor,
                    summary=(f"edited [{memory_id}] in {layer}"
                             + (f" (label → {new_label})" if new_label else '')
                             + (f": \"{new_content[:60]}"
                                f"{'…' if len(new_content) > 60 else ''}\""
                                if new_content else '')))
            conn.commit()
        if new_content is not None:
            bump_matrix_gen()   # same id, new vector — the stamp can't see it
        if embed_failed:
            global _backfill_done
            _backfill_done = False
        _publish_mind(layer, scope, 'update')
        bits = [f"ID: {memory_id}", f"layer: {layer}"]
        if new_label:
            bits.append(f"label: {new_label}")
        logger.info(f"[MINDPALACE] Updated chunk {memory_id} in scope '{scope}'")
        if dropped:
            logger.info(f"[MINDPALACE] update trimmed {len(dropped)} chars over cap (chunk {memory_id})")
        msg = f"Memory updated ({', '.join(bits)})"
        return (f"{msg}.{_trim_note(dropped, memory_id)}" if dropped else msg), True
    except Exception as e:
        logger.error(f"[MINDPALACE] Error updating memory: {e}")
        return f"Failed to update memory: {e}", False


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name: str, arguments: dict, config) -> tuple:
    try:
        scope = _get_current_scope()
        if scope is None:
            return "Memory is disabled for this chat.", False
        if scope == 'global':
            return ("Cannot write to the global scope. Global is read-only for the AI — "
                    "only the user can add entries there via the UI."), False

        if function_name == "save_memory":
            # Plugin-layer fence: mirror layers are sync-owned unless the
            # provider declared writable — a row she saved into a read-only
            # mirror would be orphaned or clobbered by the next sync.
            req_layer = str(arguments.get("layer") or '').strip().lower()
            plugin_spec = _plugin_layers().get(req_layer)
            if plugin_spec and not plugin_spec.get('writable'):
                return (f"Layer '{req_layer}' is a read-only plugin layer "
                        f"(owned by '{plugin_spec['plugin_name']}'). Search it "
                        f"freely; its content is managed by the plugin."), False
            # favorite is deliberately NOT read from arguments: in-the-moment
            # saves lack the bird's-eye view — the librarian (mark_processed)
            # and the UI are the only favorite-setters (Krem 2026-07-16).
            return _save_memory(arguments.get("content", ""), scope,
                                layer=arguments.get("layer"),
                                entity=arguments.get("entity"),
                                label=arguments.get("label"),
                                private_key=arguments.get("private_key"))
        elif function_name == "search_memory":
            return _search_memory(arguments.get("query", ""), scope,
                                  limit=arguments.get("limit", 10),
                                  label=arguments.get("label"),
                                  layer=arguments.get("layer"),
                                  private_key=arguments.get("private_key"),
                                  depth=arguments.get("depth", 0),
                                  doc=arguments.get("document_id"))
        elif function_name == "get_recent_memories":
            return _get_recent_memories(scope, count=arguments.get("count", 10),
                                        label=arguments.get("label"),
                                        layer=arguments.get("layer"),
                                        private_key=arguments.get("private_key"))
        elif function_name == "update_memory":
            memory_id, err = _parse_memory_id(arguments.get("memory_id"))
            if err:
                return err, False
            return _update_memory(memory_id, scope,
                                  content=arguments.get("content"),
                                  label=arguments.get("label"),
                                  private_key=arguments.get("private_key"))
        elif function_name == "delete_memory":
            memory_id, err = _parse_memory_id(arguments.get("memory_id"))
            if err:
                return err, False
            return _delete_memory(memory_id, scope,
                                  private_key=arguments.get("private_key"))
        else:
            return f"Unknown mind palace function: {function_name}", False
    except Exception as e:
        logger.error(f"[MINDPALACE] Function error: {e}")
        return f"Mind palace error: {e}", False


# ---------------------------------------------------------------------------
# Person cards — the ONE seam where template fields reach the AI (they are
# otherwise UI/contacts-only). Rendered on a direct search hit (the query IS
# an entity's name/nickname) and folded into read_self's important-people
# groups. Flat per-card cap; asymmetry comes from the data, not the code
# (coffee sip, 2026-07-21).

_CARD_FIELDS = (('relationship', 'Relationship'), ('birthday', 'Birthday'),
                ('phone', 'Phone'), ('email', 'Email'),   # an important person
                ('address', 'Address'),                   # arrives reachable
                ('notes', 'Notes'),   # v1 people-import bios land here
                ('background', 'Background'), ('interests', 'Interests'),
                ('voice', 'Voice'), ('likes', 'Likes'), ('dislikes', 'Dislikes'))


def _people_card_chars() -> int:
    try:
        from core.plugin_loader import plugin_loader
        v = int(plugin_loader.get_plugin_settings('mindpalace')
                .get('people_card_chars', 1500))
    except Exception:
        v = 1500
    return min(max(v, 200), 4000)


def _entity_card(cursor, eid) -> list:
    """Card lines for an entity: newest headline chunk + non-empty descriptive
    fields, capped at people_card_chars. Private-keyed headlines stay out —
    the wake renders for every persona (same gate class as the dashboard).
    Returns [] when nothing is curated."""
    try:
        cap = _people_card_chars()
        lines = []
        # Curated description first (meta.headline — the human-edited slot),
        # else the newest promoted fact. Without the preference, every
        # librarian tier-1 promotion displaced what she wrote (Krem's jank
        # find, 2026-07-24).
        row = cursor.execute(
            "SELECT content FROM chunks WHERE entity_id = ? AND tier = 1 "
            "AND private_key IS NULL "
            "AND json_extract(meta, '$.pruned_at') IS NULL "
            "AND json_extract(meta, '$.superseded_at') IS NULL "
            "ORDER BY COALESCE(json_extract(meta, '$.headline'), 0) DESC, "
            "created DESC, id DESC LIMIT 1", (eid,)).fetchone()
        if row and row[0]:
            lines.append(' '.join(row[0].split()))
        raw = cursor.execute('SELECT meta FROM entities WHERE id = ?',
                             (eid,)).fetchone()
        try:
            f = (json.loads(raw[0]) or {}).get('fields') or {}
        except Exception:
            f = {}
        for key, label in _CARD_FIELDS:
            val = ' '.join(str(f.get(key) or '').split())
            if val and val.lower() not in ('false', 'none'):
                lines.append(f"{label}: {val}")
        out, used = [], 0
        for ln in lines:
            if used + len(ln) > cap:
                room = cap - used
                if room > 40:
                    out.append(ln[:room].rsplit(' ', 1)[0] + '…')
                break
            out.append(ln)
            used += len(ln) + 1
        return out
    except Exception as e:
        logger.warning(f"[MINDPALACE] entity card skipped: {e}")
        return []


def _query_card(query, scope):
    """(name, card_lines) when the whole query IS an entity name or nickname
    in scope — she looked THEM up, so the card comes too. None otherwise."""
    q = (query or '').strip().lower()
    if not q or len(q) > 64:
        return None
    try:
        from plugins.mindpalace.tools import metadata as md
        with _get_connection() as conn:
            cursor = conn.cursor()
            amap = md.alias_map(md.entity_aliases(cursor, scope))
            pair = amap.get(q)
            if not pair:
                return None
            lines = _entity_card(cursor, pair[0])
            return (pair[1], lines) if lines else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Contacts provider — while the palace is active, person entities ARE the
# contact book. email/twilio resolve people through core.contacts; fields
# live in entities.meta.fields (template system). Registered at module load,
# unregistered by plugin_loader.unload_plugin.

def _get_people_provider(scope='default'):
    if not _ensure_db():
        return []
    cond, params = _scope_condition(scope)
    with _get_connection() as conn:
        cur = conn.cursor()
        rows = cur.execute(
            f"SELECT id, name, meta, created, updated FROM entities "
            f"WHERE kind = 'person' AND {cond} ORDER BY name COLLATE NOCASE",
            params).fetchall()
    out = []
    for eid, name, meta_raw, created, updated in rows:
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except Exception:
            meta = {}
        f = meta.get('fields') or {}
        out.append({
            'id': eid, 'name': name,
            'relationship': f.get('relationship'), 'phone': f.get('phone'),
            'email': f.get('email'), 'address': f.get('address'),
            'notes': f.get('notes'), 'created_at': created, 'updated_at': updated,
            'email_whitelisted': bool(f.get('allow_email')),
            'call_whitelisted': bool(f.get('allow_call')),
        })
    return out


try:
    from core.contacts import register_provider as _register_contacts_provider
    _register_contacts_provider('mindpalace', _get_people_provider)
except Exception as _e:
    logger.warning(f"[MINDPALACE] contacts provider registration failed: {_e}")

try:
    # Self-registers the core.audit sink at import (prompt ledger);
    # plugin_loader unwinds it on unload/refusal alongside contacts.
    from plugins.mindpalace.tools import prompt_audit as _prompt_audit  # noqa: F401
except Exception as _e:
    logger.warning(f"[MINDPALACE] prompt audit sink load failed: {_e}")
