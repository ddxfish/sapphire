# functions/knowledge.py
"""
Knowledge base system for reference data: people contacts and knowledge tabs.
SQLite-backed with FTS5 search, semantic embeddings, and scope isolation.
People are scoped via people_scope. Knowledge tabs are scoped via knowledge_scope.
"""

import sqlite3
import logging
import re
import numpy as np
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True

_db_path = None
_db_initialized = False

AVAILABLE_FUNCTIONS = [
    'save_person',
    'save_knowledge',
    'search_knowledge',
    'delete_knowledge',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "save_person",
            "description": "Save or update a person's contact info in your knowledge base. Upserts by name (case-insensitive). These are YOUR contacts — people you've learned about through conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Person's name (unique key, case-insensitive)"
                    },
                    "relationship": {
                        "type": "string",
                        "description": "Relationship to user (e.g. 'father', 'friend', 'coworker')"
                    },
                    "phone": {"type": "string", "description": "Phone number"},
                    "email": {"type": "string", "description": "Email address"},
                    "address": {"type": "string", "description": "Physical address"},
                    "notes": {
                        "type": "string",
                        "description": "Additional notes about this person"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "save_knowledge",
            "description": "Save information to your personal knowledge base under a category. This is YOUR notebook — use it to store reference data, research, notes, and things you've learned. Auto-creates the category if new. Long content is automatically chunked.\nExamples:\n  save_knowledge(category='recipes', content='...') — save a recipe\n  save_knowledge(category='project_notes', content='...') — save project info",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category to save under (e.g. 'recipes', 'books', 'research'). Auto-creates if new."
                    },
                    "content": {
                        "type": "string",
                        "description": "The information to store"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional category description (only used when creating a new category)"
                    }
                },
                "required": ["category", "content"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_knowledge",
            "description": "Search, browse, or read from your personal knowledge base. This contains YOUR stored knowledge — people you know, things you've learned, and notes you've saved.\nExamples:\n  search_knowledge(query='mars') — find entries about mars\n  search_knowledge() — overview of all your categories and people\n  search_knowledge(category='books') — browse all entries in a category\n  search_knowledge(id=42) — read a specific entry in full\n  search_knowledge(query='orbital', category='physics') — search within a category",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms (optional — omit to browse)"
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter to or browse a specific category"
                    },
                    "id": {
                        "type": "integer",
                        "description": "Read a specific entry in full by its ID"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 10)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "delete_knowledge",
            "description": "Delete entries or categories from your knowledge base. You can only delete content YOU created — user-created content is protected. Deleting the last entry in a category auto-removes the category.\nExamples:\n  delete_knowledge(id=42) — delete a specific entry\n  delete_knowledge(category='old_research') — delete an entire category and all its entries",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Delete a specific entry by its ID"
                    },
                    "category": {
                        "type": "string",
                        "description": "Delete an entire category and all its entries"
                    }
                },
                "required": []
            }
        }
    }
]


# ─── Database ─────────────────────────────────────────────────────────────────

def _get_db_path():
    global _db_path
    if _db_path is None:
        _db_path = Path(__file__).parent.parent / "user" / "knowledge.db"
    return _db_path


def _get_connection():
    _ensure_db()
    conn = sqlite3.connect(_get_db_path(), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return

    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")

    # People (scoped via people_scope)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            relationship TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            scope TEXT NOT NULL DEFAULT 'default',
            embedding BLOB,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Migration: add scope column if missing (existing DBs)
    try:
        cursor.execute('SELECT scope FROM people LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE people ADD COLUMN scope TEXT NOT NULL DEFAULT 'default'")
        logger.info("Migrated people table: added scope column")

    # Unique per name+scope (drop old name-only index)
    cursor.execute('DROP INDEX IF EXISTS idx_people_name_lower')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_people_name_scope ON people(LOWER(name), scope)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_people_scope ON people(scope)')

    # Knowledge tabs (scoped)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            type TEXT NOT NULL DEFAULT 'user',
            scope TEXT NOT NULL DEFAULT 'default',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, scope)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tabs_scope ON knowledge_tabs(scope)')

    # Knowledge entries (within tabs, chunked + embedded)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tab_id INTEGER NOT NULL REFERENCES knowledge_tabs(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            chunk_index INTEGER DEFAULT 0,
            source_filename TEXT,
            embedding BLOB,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_entries_tab ON knowledge_entries(tab_id)')

    # FTS5 on entries
    try:
        _setup_fts(cursor)
    except sqlite3.DatabaseError as e:
        logger.warning(f"Knowledge FTS5 corrupted, rebuilding: {e}")
        cursor.execute("DROP TABLE IF EXISTS knowledge_fts")
        cursor.execute("DROP TRIGGER IF EXISTS knowledge_fts_insert")
        cursor.execute("DROP TRIGGER IF EXISTS knowledge_fts_delete")
        cursor.execute("DROP TRIGGER IF EXISTS knowledge_fts_update")
        conn.commit()
        _setup_fts(cursor)

    # Scope registries
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_scopes (
            name TEXT PRIMARY KEY,
            created DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO knowledge_scopes (name) VALUES ('default')")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS people_scopes (
            name TEXT PRIMARY KEY,
            created DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO people_scopes (name) VALUES ('default')")

    conn.commit()
    conn.close()
    _db_initialized = True
    logger.info(f"Knowledge database ready at {db_path}")


def _setup_fts(cursor):
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            content,
            content=knowledge_entries, content_rowid=id
        )
    """)

    cursor.execute("DROP TRIGGER IF EXISTS knowledge_fts_insert")
    cursor.execute("DROP TRIGGER IF EXISTS knowledge_fts_delete")
    cursor.execute("DROP TRIGGER IF EXISTS knowledge_fts_update")

    cursor.execute("""
        CREATE TRIGGER knowledge_fts_insert
        AFTER INSERT ON knowledge_entries BEGIN
            INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, new.content);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER knowledge_fts_delete
        AFTER DELETE ON knowledge_entries BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER knowledge_fts_update
        AFTER UPDATE OF content ON knowledge_entries BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, new.content);
        END
    """)

    # Populate if empty
    cursor.execute("SELECT COUNT(*) FROM knowledge_entries")
    entry_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM knowledge_fts")
    fts_count = cursor.fetchone()[0]
    if entry_count > 0 and fts_count == 0:
        logger.info(f"Populating knowledge FTS5 from {entry_count} entries...")
        cursor.execute("INSERT INTO knowledge_fts(rowid, content) SELECT id, content FROM knowledge_entries")


def _get_current_scope():
    try:
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_knowledge_scope
    except Exception:
        return 'default'


def _get_current_people_scope():
    try:
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_people_scope
    except Exception:
        return 'default'


def _get_embedder():
    """Reuse the singleton embedder from memory.py."""
    try:
        from functions.memory import _get_embedder as mem_get_embedder
        return mem_get_embedder()
    except Exception as e:
        logger.warning(f"Could not import embedder from memory: {e}")
        return None


SIMILARITY_THRESHOLD = 0.40


# ─── Public API (used by api_fastapi.py) ──────────────────────────────────────

def get_scopes():
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT scope, COUNT(*) FROM knowledge_tabs GROUP BY scope')
        tab_counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute('SELECT name FROM knowledge_scopes ORDER BY name')
        registered = [row[0] for row in cursor.fetchall()]
        conn.close()
        all_scopes = set(registered) | set(tab_counts.keys()) | {'default'}
        return [{"name": name, "count": tab_counts.get(name, 0)} for name in sorted(all_scopes)]
    except Exception as e:
        logger.error(f"Error getting knowledge scopes: {e}")
        return [{"name": "default", "count": 0}]


def create_scope(name: str) -> bool:
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO knowledge_scopes (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to create knowledge scope '{name}': {e}")
        return False


def delete_scope(name: str) -> dict:
    """Delete a knowledge scope, ALL its tabs, and ALL entries within those tabs."""
    if name == 'default':
        return {"error": "Cannot delete the default scope"}
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM knowledge_tabs WHERE scope = ?', (name,))
        tab_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM knowledge_entries WHERE tab_id IN (SELECT id FROM knowledge_tabs WHERE scope = ?)', (name,))
        entry_count = cursor.fetchone()[0]
        cursor.execute('DELETE FROM knowledge_entries WHERE tab_id IN (SELECT id FROM knowledge_tabs WHERE scope = ?)', (name,))
        cursor.execute('DELETE FROM knowledge_tabs WHERE scope = ?', (name,))
        cursor.execute('DELETE FROM knowledge_scopes WHERE name = ?', (name,))
        conn.commit()
        conn.close()
        logger.info(f"Deleted knowledge scope '{name}' with {tab_count} tabs and {entry_count} entries")
        return {"deleted_tabs": tab_count, "deleted_entries": entry_count}
    except Exception as e:
        logger.error(f"Failed to delete knowledge scope '{name}': {e}")
        return {"error": str(e)}


# ─── People Scope CRUD ────────────────────────────────────────────────────────

def get_people_scopes():
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT scope, COUNT(*) FROM people GROUP BY scope')
        counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute('SELECT name FROM people_scopes ORDER BY name')
        registered = [row[0] for row in cursor.fetchall()]
        conn.close()
        all_scopes = set(registered) | set(counts.keys()) | {'default'}
        return [{"name": name, "count": counts.get(name, 0)} for name in sorted(all_scopes)]
    except Exception as e:
        logger.error(f"Error getting people scopes: {e}")
        return [{"name": "default", "count": 0}]


def create_people_scope(name: str) -> bool:
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO people_scopes (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to create people scope '{name}': {e}")
        return False


def delete_people_scope(name: str) -> dict:
    if name == 'default':
        return {"error": "Cannot delete the default scope"}
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM people WHERE scope = ?', (name,))
        count = cursor.fetchone()[0]
        cursor.execute('DELETE FROM people WHERE scope = ?', (name,))
        cursor.execute('DELETE FROM people_scopes WHERE name = ?', (name,))
        conn.commit()
        conn.close()
        logger.info(f"Deleted people scope '{name}' with {count} people")
        return {"deleted_people": count}
    except Exception as e:
        logger.error(f"Failed to delete people scope '{name}': {e}")
        return {"error": str(e)}


# ─── People CRUD ──────────────────────────────────────────────────────────────

def get_people(scope='default'):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, relationship, phone, email, address, notes, created_at, updated_at FROM people WHERE scope = ? ORDER BY name', (scope,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "relationship": r[2], "phone": r[3],
             "email": r[4], "address": r[5], "notes": r[6],
             "created_at": r[7], "updated_at": r[8]} for r in rows]


def create_or_update_person(name, relationship=None, phone=None, email=None, address=None, notes=None, scope='default'):
    conn = _get_connection()
    cursor = conn.cursor()

    # Check if person exists (case-insensitive, within scope)
    cursor.execute('SELECT id FROM people WHERE LOWER(name) = LOWER(?) AND scope = ?', (name.strip(), scope))
    existing = cursor.fetchone()

    # Build embed text for semantic search
    parts = [name.strip()]
    if relationship: parts.append(f"relationship: {relationship}")
    if phone: parts.append(f"phone: {phone}")
    if email: parts.append(f"email: {email}")
    if address: parts.append(f"address: {address}")
    if notes: parts.append(f"notes: {notes}")
    embed_text = '. '.join(parts)

    embedding_blob = None
    embedder = _get_embedder()
    if embedder and embedder.available:
        embs = embedder.embed([embed_text], prefix='search_document')
        if embs is not None:
            embedding_blob = embs[0].tobytes()

    now = datetime.now().isoformat()

    if existing:
        pid = existing[0]
        # Update provided fields — empty string clears to NULL, None means "don't touch"
        updates, params = [], []
        for col, val in [('relationship', relationship), ('phone', phone),
                         ('email', email), ('address', address), ('notes', notes)]:
            if val is not None:
                updates.append(f'{col} = ?'); params.append(val if val else None)
        if name.strip():
            updates.append('name = ?'); params.append(name.strip())
        updates.append('embedding = ?'); params.append(embedding_blob)
        updates.append('updated_at = ?'); params.append(now)
        params.append(pid)
        cursor.execute(f'UPDATE people SET {", ".join(updates)} WHERE id = ?', params)
        conn.commit()
        conn.close()
        return pid, False  # (id, is_new)
    else:
        cursor.execute(
            'INSERT INTO people (name, relationship, phone, email, address, notes, scope, embedding, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (name.strip(), relationship, phone, email, address, notes, scope, embedding_blob, now)
        )
        pid = cursor.lastrowid
        conn.commit()
        conn.close()
        return pid, True


def delete_person(person_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM people WHERE id = ?', (person_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False
    cursor.execute('DELETE FROM people WHERE id = ?', (person_id,))
    conn.commit()
    conn.close()
    return True


# ─── Knowledge Tabs CRUD ─────────────────────────────────────────────────────

def get_tabs(scope='default', tab_type=None):
    conn = _get_connection()
    cursor = conn.cursor()
    if tab_type:
        cursor.execute('''
            SELECT t.id, t.name, t.description, t.type, t.scope, t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM knowledge_entries WHERE tab_id = t.id) as entry_count
            FROM knowledge_tabs t WHERE t.scope = ? AND t.type = ? ORDER BY t.name
        ''', (scope, tab_type))
    else:
        cursor.execute('''
            SELECT t.id, t.name, t.description, t.type, t.scope, t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM knowledge_entries WHERE tab_id = t.id) as entry_count
            FROM knowledge_tabs t WHERE t.scope = ? ORDER BY t.name
        ''', (scope,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "description": r[2], "type": r[3],
             "scope": r[4], "created_at": r[5], "updated_at": r[6],
             "entry_count": r[7]} for r in rows]


def get_tab_entries(tab_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, content, chunk_index, source_filename, created_at, updated_at FROM knowledge_entries WHERE tab_id = ? ORDER BY chunk_index, created_at',
        (tab_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "content": r[1], "chunk_index": r[2],
             "source_filename": r[3], "created_at": r[4], "updated_at": r[5]} for r in rows]


def create_tab(name, scope='default', description=None, tab_type='user'):
    conn = _get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO knowledge_tabs (name, description, type, scope) VALUES (?, ?, ?, ?)',
            (name.strip(), description, tab_type, scope)
        )
        tab_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return tab_id
    except sqlite3.IntegrityError:
        conn.close()
        return None  # Already exists


def update_tab(tab_id, name=None, description=None):
    conn = _get_connection()
    cursor = conn.cursor()
    updates, params = [], []
    if name is not None:
        updates.append('name = ?'); params.append(name.strip())
    if description is not None:
        updates.append('description = ?'); params.append(description)
    if not updates:
        conn.close()
        return False
    updates.append('updated_at = ?'); params.append(datetime.now().isoformat())
    params.append(tab_id)
    cursor.execute(f'UPDATE knowledge_tabs SET {", ".join(updates)} WHERE id = ?', params)
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_tab(tab_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM knowledge_tabs WHERE id = ?', (tab_id,))
    if not cursor.fetchone():
        conn.close()
        return False
    cursor.execute('DELETE FROM knowledge_entries WHERE tab_id = ?', (tab_id,))
    cursor.execute('DELETE FROM knowledge_tabs WHERE id = ?', (tab_id,))
    conn.commit()
    conn.close()
    return True


# ─── Knowledge Entries CRUD ───────────────────────────────────────────────────

def add_entry(tab_id, content, chunk_index=0, source_filename=None):
    embedding_blob = None
    embedder = _get_embedder()
    if embedder and embedder.available:
        embs = embedder.embed([content], prefix='search_document')
        if embs is not None:
            embedding_blob = embs[0].tobytes()

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO knowledge_entries (tab_id, content, chunk_index, source_filename, embedding) VALUES (?, ?, ?, ?, ?)',
        (tab_id, content, chunk_index, source_filename, embedding_blob)
    )
    entry_id = cursor.lastrowid
    # Bump tab updated_at
    cursor.execute('UPDATE knowledge_tabs SET updated_at = ? WHERE id = ?',
                   (datetime.now().isoformat(), tab_id))
    conn.commit()
    conn.close()
    return entry_id


def update_entry(entry_id, content):
    embedding_blob = None
    embedder = _get_embedder()
    if embedder and embedder.available:
        embs = embedder.embed([content], prefix='search_document')
        if embs is not None:
            embedding_blob = embs[0].tobytes()

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE knowledge_entries SET content = ?, embedding = ?, updated_at = ? WHERE id = ?',
        (content, embedding_blob, datetime.now().isoformat(), entry_id)
    )
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def delete_entry(entry_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM knowledge_entries WHERE id = ?', (entry_id,))
    if not cursor.fetchone():
        conn.close()
        return False
    cursor.execute('DELETE FROM knowledge_entries WHERE id = ?', (entry_id,))
    conn.commit()
    conn.close()
    return True


def delete_entries_by_filename(tab_id, filename):
    """Delete all entries in a tab that came from a specific uploaded file."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM knowledge_entries WHERE tab_id = ? AND source_filename = ?',
                   (tab_id, filename))
    count = cursor.fetchone()[0]
    if count:
        cursor.execute('DELETE FROM knowledge_entries WHERE tab_id = ? AND source_filename = ?',
                       (tab_id, filename))
        conn.commit()
    conn.close()
    return count


def get_tabs_by_id(tab_id):
    """Get a single tab by ID."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, type, scope FROM knowledge_tabs WHERE id = ?', (tab_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "type": row[2], "scope": row[3]}


# ─── Chunking ─────────────────────────────────────────────────────────────────

def _chunk_text(text, max_tokens=400, overlap_tokens=50):
    """Split text into chunks by paragraph, respecting token limits."""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []

    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para.split())
        if current and current_len + para_len > max_tokens:
            chunks.append('\n\n'.join(current))
            # Overlap: keep last paragraph if it fits
            if overlap_tokens > 0 and current:
                last = current[-1]
                if len(last.split()) <= overlap_tokens:
                    current = [last]
                    current_len = len(last.split())
                else:
                    current = []
                    current_len = 0
            else:
                current = []
                current_len = 0
        current.append(para)
        current_len += para_len

    if current:
        chunks.append('\n\n'.join(current))

    return chunks if chunks else [text.strip()]


# ─── Search ───────────────────────────────────────────────────────────────────

def _sanitize_fts_query(query, use_or=False, use_prefix=False):
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


def _search_entries(query, scope, category=None, limit=10):
    """Search knowledge entries with cascading FTS + vector + LIKE."""
    conn = _get_connection()
    cursor = conn.cursor()

    # Resolve category filter
    tab_filter = ""
    tab_params = []
    if category:
        cursor.execute('SELECT id FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                       (category, scope))
        tab = cursor.fetchone()
        if not tab:
            conn.close()
            return []
        tab_filter = " AND e.tab_id = ?"
        tab_params = [tab[0]]
    else:
        # All tabs in scope
        cursor.execute('SELECT id FROM knowledge_tabs WHERE scope = ?', (scope,))
        tab_ids = [r[0] for r in cursor.fetchall()]
        if not tab_ids:
            conn.close()
            return []
        placeholders = ','.join('?' * len(tab_ids))
        tab_filter = f" AND e.tab_id IN ({placeholders})"
        tab_params = tab_ids

    results = []
    seen_ids = set()

    # Strategy 0: Filename match
    cursor.execute(f'''
        SELECT e.id, e.content, t.name as tab_name, e.source_filename
        FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
        WHERE e.source_filename LIKE ?{tab_filter}
        ORDER BY e.chunk_index LIMIT ?
    ''', [f'%{query}%'] + tab_params + [limit])
    for r in cursor.fetchall():
        results.append({"id": r[0], "content": r[1], "tab": r[2], "file": r[3], "source": "knowledge", "score": 0.96})
        seen_ids.add(r[0])

    # Strategy 1: FTS AND
    fts_results = []
    fts_exact = _sanitize_fts_query(query)
    if fts_exact:
        try:
            cursor.execute(f'''
                SELECT e.id, e.content, t.name as tab_name, e.source_filename
                FROM knowledge_fts f
                JOIN knowledge_entries e ON f.rowid = e.id
                JOIN knowledge_tabs t ON e.tab_id = t.id
                WHERE knowledge_fts MATCH ?{tab_filter}
                ORDER BY bm25(knowledge_fts) LIMIT ?
            ''', [fts_exact] + tab_params + [limit])
            fts_results = cursor.fetchall()

            # Strategy 2: FTS OR + prefix
            if not fts_results:
                fts_broad = _sanitize_fts_query(query, use_or=True, use_prefix=True)
                if fts_broad != fts_exact:
                    cursor.execute(f'''
                        SELECT e.id, e.content, t.name as tab_name, e.source_filename
                        FROM knowledge_fts f
                        JOIN knowledge_entries e ON f.rowid = e.id
                        JOIN knowledge_tabs t ON e.tab_id = t.id
                        WHERE knowledge_fts MATCH ?{tab_filter}
                        ORDER BY bm25(knowledge_fts) LIMIT ?
                    ''', [fts_broad] + tab_params + [limit])
                    fts_results = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Knowledge FTS query failed: {e}")

    conn.close()

    if fts_results:
        for r in fts_results:
            if r[0] not in seen_ids:
                entry = {"id": r[0], "content": r[1], "tab": r[2], "source": "knowledge", "score": 0.95}
                if r[3]: entry["file"] = r[3]
                results.append(entry)
                seen_ids.add(r[0])
        return results

    # Strategy 3: Vector similarity (already returns score)
    vec_results = _vector_search_entries(query, scope, category, limit)
    if vec_results:
        for r in vec_results:
            if r["id"] not in seen_ids:
                results.append(r)
        return results

    # Strategy 4: LIKE fallback
    conn = _get_connection()
    cursor = conn.cursor()
    terms = query.lower().split()[:5]
    if terms:
        conditions = ' OR '.join(['e.content LIKE ?' for _ in terms])
        params = [f'%{t}%' for t in terms]
        cursor.execute(f'''
            SELECT e.id, e.content, t.name as tab_name, e.source_filename
            FROM knowledge_entries e
            JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE ({conditions}){tab_filter}
            ORDER BY e.updated_at DESC LIMIT ?
        ''', params + tab_params + [limit])
        rows = cursor.fetchall()
        conn.close()
        for r in rows:
            if r[0] not in seen_ids:
                entry = {"id": r[0], "content": r[1], "tab": r[2], "source": "knowledge", "score": 0.35}
                if r[3]: entry["file"] = r[3]
                results.append(entry)
        return results

    conn.close()
    return results


def _vector_search_entries(query, scope, category=None, limit=10):
    embedder = _get_embedder()
    if not embedder or not embedder.available:
        return []

    query_emb = embedder.embed([query], prefix='search_query')
    if query_emb is None:
        return []
    query_vec = query_emb[0]

    conn = _get_connection()
    cursor = conn.cursor()

    if category:
        cursor.execute('''
            SELECT e.id, e.content, t.name, e.embedding, e.source_filename
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE t.scope = ? AND LOWER(t.name) = LOWER(?) AND e.embedding IS NOT NULL
        ''', (scope, category))
    else:
        cursor.execute('''
            SELECT e.id, e.content, t.name, e.embedding, e.source_filename
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE t.scope = ? AND e.embedding IS NOT NULL
        ''', (scope,))

    rows = cursor.fetchall()
    conn.close()

    scored = []
    for eid, content, tname, emb_blob, src_file in rows:
        emb = np.frombuffer(emb_blob, dtype=np.float32)
        sim = float(np.dot(query_vec, emb))
        if sim >= SIMILARITY_THRESHOLD:
            entry = {"id": eid, "content": content, "tab": tname, "source": "knowledge", "score": sim}
            if src_file: entry["file"] = src_file
            scored.append(entry)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _search_people(query, scope='default', limit=10):
    """Search people via vector + LIKE. Only returns actual matches."""
    results = []

    # Vector search — use higher threshold for people (their embeddings are dense info strings)
    embedder = _get_embedder()
    if embedder and embedder.available:
        query_emb = embedder.embed([query], prefix='search_query')
        if query_emb is not None:
            query_vec = query_emb[0]
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id, name, relationship, phone, email, address, notes, embedding FROM people WHERE scope = ? AND embedding IS NOT NULL', (scope,))
            rows = cursor.fetchall()
            conn.close()
            for pid, name, rel, phone, email, addr, notes, emb_blob in rows:
                emb = np.frombuffer(emb_blob, dtype=np.float32)
                sim = float(np.dot(query_vec, emb))
                # Higher threshold for people — their dense contact strings match too broadly at 0.40
                if sim >= 0.55:
                    results.append({"id": pid, "name": name, "relationship": rel,
                                    "phone": phone, "email": email, "address": addr,
                                    "notes": notes, "source": "people", "score": sim})
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:limit]

    # LIKE fallback (only when embeddings unavailable) — must actually match query terms
    conn = _get_connection()
    cursor = conn.cursor()
    terms = query.lower().split()[:5]
    if terms:
        conditions = ' OR '.join(['(LOWER(name) LIKE ? OR LOWER(relationship) LIKE ? OR LOWER(notes) LIKE ?)' for _ in terms])
        params = []
        for t in terms:
            params.extend([f'%{t}%', f'%{t}%', f'%{t}%'])
        cursor.execute(f'''
            SELECT id, name, relationship, phone, email, address, notes
            FROM people WHERE scope = ? AND ({conditions}) ORDER BY name LIMIT ?
        ''', [scope] + params + [limit])
        rows = cursor.fetchall()
        conn.close()
        # LIKE results get a low fixed score so they sort below vector matches
        return [{"id": r[0], "name": r[1], "relationship": r[2], "phone": r[3],
                 "email": r[4], "address": r[5], "notes": r[6], "source": "people", "score": 0.3} for r in rows]

    conn.close()
    return []


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _format_person(p):
    pid = f"[id:{p['id']}] " if p.get("id") else ""
    parts = [f"{pid}{p['name']}"]
    if p.get("relationship"): parts.append(f"({p['relationship']})")
    details = []
    if p.get("phone"): details.append(f"phone: {p['phone']}")
    if p.get("email"): details.append(f"email: {p['email']}")
    if p.get("address"): details.append(f"address: {p['address']}")
    if p.get("notes"): details.append(f"notes: {p['notes']}")
    if details:
        parts.append("— " + ", ".join(details))
    return " ".join(parts)


def _format_entry(r, query=None, max_len=480):
    content = r["content"]
    eid = f"[id:{r['id']}] " if r.get("id") else ""
    tab_info = f"[{r['tab']}] " if r.get("tab") else ""
    file_info = f"[file: {r['file']}] " if r.get("file") else ""

    if len(content) <= max_len:
        preview = content
    elif query:
        # Show snippet weighted around match: ~1/3 before, ~2/3 after
        pos = content.lower().find(query.lower())
        if pos >= 0:
            before = max_len // 3
            start = max(0, pos - before)
            end = min(len(content), start + max_len)
            if start > 0:
                start = max(0, end - max_len)
            preview = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
        else:
            preview = content[:max_len] + '...'
    else:
        preview = content[:max_len] + '...'

    return f"{tab_info}{file_info}{eid}{preview}"


# ─── Tool Operations ─────────────────────────────────────────────────────────

def _save_person(name, relationship=None, phone=None, email=None, address=None, notes=None, scope='default'):
    if not name or not name.strip():
        return "Person name is required.", False
    if len(name) > 100:
        return "Name too long (max 100 chars).", False

    pid, is_new = create_or_update_person(name, relationship, phone, email, address, notes, scope=scope)
    action = "Saved new" if is_new else "Updated"
    logger.info(f"{action} person [{pid}] '{name.strip()}' (scope: {scope})")
    return f"{action} contact: {name.strip()} (ID: {pid})", True


def _save_knowledge(category, content, description=None, scope='default'):
    if not category or not category.strip():
        return "Category name is required.", False
    if not content or not content.strip():
        return "Content is required.", False
    if len(category) > 100:
        return "Category name too long (max 100 chars).", False

    category = category.strip()
    content = content.strip()

    # Get or create category (stored as knowledge_tab)
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                   (category, scope))
    row = cursor.fetchone()
    conn.close()

    if row:
        tab_id = row[0]
    else:
        tab_id = create_tab(category, scope, description, tab_type='ai')
        if not tab_id:
            return f"Failed to create category '{category}'.", False

    # Chunk if needed
    chunks = _chunk_text(content)
    entry_ids = []
    for i, chunk in enumerate(chunks):
        eid = add_entry(tab_id, chunk, chunk_index=i)
        entry_ids.append(eid)

    ids_str = ', '.join(f'id:{eid}' for eid in entry_ids)
    chunk_note = f" ({len(chunks)} chunks)" if len(chunks) > 1 else ""
    logger.info(f"Saved knowledge to '{category}' in scope '{scope}': {len(chunks)} entries")
    return f"Saved to '{category}'{chunk_note} [{ids_str}] — {len(content)} chars", True


def _search_knowledge(query=None, category=None, entry_id=None, limit=10, scope='default', people_scope='default'):
    # Mode 1: Read a single entry in full by ID
    if entry_id:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT e.id, e.content, t.name, t.type
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE e.id = ?
        ''', (entry_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return f"Entry {entry_id} not found.", True
        return f"=== Entry [id:{row[0]}] from '{row[2]}' ({row[3]}) ===\n{row[1]}", True

    # Mode 2: Browse a category (no query needed)
    if category and not query:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                       (category, scope))
        tab = cursor.fetchone()
        conn.close()
        if not tab:
            return f"Category '{category}' not found in scope '{scope}'.", True
        entries = get_tab_entries(tab[0])
        if not entries:
            return f"Category '{category}' is empty.", True
        lines = [f"=== {category} ({len(entries)} entries) ==="]
        for e in entries:
            lines.append(f"  [id:{e['id']}] {e['content']}")
        return '\n'.join(lines), True

    # Mode 3: Overview (no query, no category, no id)
    if not query:
        lines = []
        people = get_people(people_scope) if people_scope else []
        lines.append(f"=== People ({len(people)}) ===")
        if people:
            for p in people[:10]:
                lines.append(f"  {_format_person(p)}")
            if len(people) > 10:
                lines.append(f"  ... and {len(people) - 10} more")
        else:
            lines.append("  (none)")

        tabs = get_tabs(scope)
        lines.append(f"\n=== Categories (scope: {scope}, {len(tabs)}) ===")
        if tabs:
            for t in tabs:
                type_tag = f" [{t['type']}]" if t['type'] == 'ai' else ""
                lines.append(f"  {t['name']}{type_tag} — {t['entry_count']} entries")
        else:
            lines.append("  (none)")

        if not lines:
            return "Your knowledge base is empty.", True
        return '\n'.join(lines), True

    # Mode 4: Search (query provided)
    results = []
    if people_scope:
        results.extend(_search_people(query, people_scope, limit))
    results.extend(_search_entries(query, scope, category, limit))

    if not results:
        return f"No results for '{query}'.", True

    # Sort all results by score (highest first) — unified ranking across sources
    results.sort(key=lambda r: r.get("score", 0), reverse=True)

    lines = [f"Found {len(results)} results:"]
    for r in results[:limit]:
        if r["source"] == "people":
            lines.append(f"---\n[Person] {_format_person(r)}")
        else:
            lines.append(f"---\n[Knowledge] {_format_entry(r, query=query)}")

    return '\n'.join(lines), True


def _delete_knowledge(entry_id=None, category=None, scope='default'):
    if not entry_id and not category:
        return "Provide id or category to delete.", False

    conn = _get_connection()
    cursor = conn.cursor()

    if entry_id:
        # Delete single entry — must belong to an AI tab
        cursor.execute('''
            SELECT e.id, e.content, t.id, t.name, t.type
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE e.id = ?
        ''', (entry_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return f"Entry {entry_id} not found.", False
        if row[4] != 'ai':
            return f"Cannot delete user-created content (entry {entry_id} in tab '{row[3]}').", False
        tab_id, tab_name_str = row[2], row[3]
        delete_entry(entry_id)
        preview = row[1][:100] + ('...' if len(row[1]) > 100 else '')
        logger.info(f"AI deleted entry [{entry_id}] from tab '{tab_name_str}'")
        # Auto-delete empty AI tab
        remaining = get_tab_entries(tab_id)
        if not remaining:
            delete_tab(tab_id)
            logger.info(f"Auto-deleted empty AI tab '{tab_name_str}'")
            return f"Deleted entry [id:{entry_id}] from tab '{tab_name_str}': {preview}\nTab '{tab_name_str}' is now empty and was removed.", True
        return f"Deleted entry [id:{entry_id}] from tab '{tab_name_str}': {preview}", True

    if category:
        # Delete entire category — must be AI type
        cursor.execute('SELECT id, type FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                       (category, scope))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return f"Category '{category}' not found in scope '{scope}'.", False
        if row[1] != 'ai':
            return f"Cannot delete user-created category '{category}'.", False
        delete_tab(row[0])
        logger.info(f"AI deleted category '{category}' (scope: {scope})")
        return f"Deleted category '{category}' and all its entries.", True

    conn.close()
    return "Nothing to delete.", False


# ─── Executor ─────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        scope = _get_current_scope()
        people_scope = _get_current_people_scope()

        # People tools check people_scope, knowledge tools check knowledge scope
        if function_name == "save_person":
            if people_scope is None:
                return "People contacts are disabled for this chat.", False
            return _save_person(
                name=arguments.get('name'),
                relationship=arguments.get('relationship'),
                phone=arguments.get('phone'),
                email=arguments.get('email'),
                address=arguments.get('address'),
                notes=arguments.get('notes'),
                scope=people_scope,
            )

        elif function_name == "save_knowledge":
            if scope is None:
                return "Knowledge base is disabled for this chat.", False
            return _save_knowledge(
                category=arguments.get('category'),
                content=arguments.get('content'),
                description=arguments.get('description'),
                scope=scope,
            )

        elif function_name == "search_knowledge":
            # Search spans both scopes — either can be active
            if scope is None and people_scope is None:
                return "Knowledge base is disabled for this chat.", False
            return _search_knowledge(
                query=arguments.get('query'),
                category=arguments.get('category'),
                entry_id=arguments.get('id'),
                limit=arguments.get('limit', 10),
                scope=scope or 'default',
                people_scope=people_scope,
            )

        elif function_name == "delete_knowledge":
            if scope is None:
                return "Knowledge base is disabled for this chat.", False
            return _delete_knowledge(
                entry_id=arguments.get('id'),
                category=arguments.get('category'),
                scope=scope,
            )

        else:
            return f"Unknown knowledge function '{function_name}'.", False

    except Exception as e:
        logger.error(f"Knowledge function error in {function_name}: {e}", exc_info=True)
        return f"Knowledge system error: {str(e)}", False
