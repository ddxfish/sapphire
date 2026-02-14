# functions/knowledge.py
"""
Knowledge base system for reference data: people contacts and knowledge tabs.
SQLite-backed with FTS5 search, semantic embeddings, and scope isolation.
People are universal (no scope). Knowledge tabs are scoped via knowledge_scope.
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
    'list_knowledge',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "save_person",
            "description": "Save or update a person's contact info in the knowledge base. Upserts by name (case-insensitive). Use for storing contacts, relationships, and notes about people.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Person's name (used as unique key, case-insensitive)"
                    },
                    "relationship": {
                        "type": "string",
                        "description": "Relationship to user (e.g. 'father', 'friend', 'coworker')"
                    },
                    "phone": {
                        "type": "string",
                        "description": "Phone number"
                    },
                    "email": {
                        "type": "string",
                        "description": "Email address"
                    },
                    "address": {
                        "type": "string",
                        "description": "Physical address"
                    },
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
            "description": "Save information to a knowledge tab. Auto-creates the tab if it doesn't exist (type='ai'). Long content is automatically chunked. Use for storing reference data, notes, research, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_name": {
                        "type": "string",
                        "description": "Name of the knowledge tab to write to (e.g. 'recipes', 'project_notes')"
                    },
                    "content": {
                        "type": "string",
                        "description": "The information to store"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional tab description (only used when creating a new tab)"
                    }
                },
                "required": ["tab_name", "content"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_knowledge",
            "description": "Search the knowledge base using semantic similarity and full-text search. Can search across all sources or filter by type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms or topic"
                    },
                    "source": {
                        "type": "string",
                        "description": "Filter: 'all' (default), 'people', or 'knowledge'"
                    },
                    "tab_name": {
                        "type": "string",
                        "description": "Search within a specific knowledge tab only"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 10)"
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
            "name": "list_knowledge",
            "description": "Browse the knowledge base directory. No args = overview of tabs and people count. Use source='people' for contact list. Use tab_name to read entries in a specific tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "What to list: 'all' (default overview), 'people' (contacts), 'tabs' (knowledge tabs)"
                    },
                    "tab_name": {
                        "type": "string",
                        "description": "Read entries from a specific tab"
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

    # People (universal, no scope)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            relationship TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            embedding BLOB,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_people_name_lower ON people(LOWER(name))')

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

    # Scope registry
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_scopes (
            name TEXT PRIMARY KEY,
            created DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO knowledge_scopes (name) VALUES ('default')")

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


# ─── People CRUD ──────────────────────────────────────────────────────────────

def get_people():
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, relationship, phone, email, address, notes, created_at, updated_at FROM people ORDER BY name')
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "relationship": r[2], "phone": r[3],
             "email": r[4], "address": r[5], "notes": r[6],
             "created_at": r[7], "updated_at": r[8]} for r in rows]


def create_or_update_person(name, relationship=None, phone=None, email=None, address=None, notes=None):
    conn = _get_connection()
    cursor = conn.cursor()

    # Check if person exists (case-insensitive)
    cursor.execute('SELECT id FROM people WHERE LOWER(name) = LOWER(?)', (name.strip(),))
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
            'INSERT INTO people (name, relationship, phone, email, address, notes, embedding, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (name.strip(), relationship, phone, email, address, notes, embedding_blob, now)
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


def _search_entries(query, scope, tab_name=None, limit=10):
    """Search knowledge entries with cascading FTS + vector + LIKE."""
    conn = _get_connection()
    cursor = conn.cursor()

    # Resolve tab filter
    tab_filter = ""
    tab_params = []
    if tab_name:
        cursor.execute('SELECT id FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                       (tab_name, scope))
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

    # Strategy 1: FTS AND
    fts_exact = _sanitize_fts_query(query)
    if fts_exact:
        try:
            cursor.execute(f'''
                SELECT e.id, e.content, t.name as tab_name, bm25(knowledge_fts) as rank
                FROM knowledge_fts f
                JOIN knowledge_entries e ON f.rowid = e.id
                JOIN knowledge_tabs t ON e.tab_id = t.id
                WHERE knowledge_fts MATCH ?{tab_filter}
                ORDER BY rank LIMIT ?
            ''', [fts_exact] + tab_params + [limit])
            results = cursor.fetchall()

            # Strategy 2: FTS OR + prefix
            if not results:
                fts_broad = _sanitize_fts_query(query, use_or=True, use_prefix=True)
                if fts_broad != fts_exact:
                    cursor.execute(f'''
                        SELECT e.id, e.content, t.name as tab_name, bm25(knowledge_fts) as rank
                        FROM knowledge_fts f
                        JOIN knowledge_entries e ON f.rowid = e.id
                        JOIN knowledge_tabs t ON e.tab_id = t.id
                        WHERE knowledge_fts MATCH ?{tab_filter}
                        ORDER BY rank LIMIT ?
                    ''', [fts_broad] + tab_params + [limit])
                    results = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Knowledge FTS query failed: {e}")

    conn.close()

    if results:
        # FTS matches are direct text hits — score high so they outrank semantic guesses
        return [{"id": r[0], "content": r[1], "tab": r[2], "source": "knowledge", "score": 0.95} for r in results]

    # Strategy 3: Vector similarity (already returns score)
    vec_results = _vector_search_entries(query, scope, tab_name, limit)
    if vec_results:
        return vec_results

    # Strategy 4: LIKE fallback
    conn = _get_connection()
    cursor = conn.cursor()
    terms = query.lower().split()[:5]
    if terms:
        conditions = ' OR '.join(['e.content LIKE ?' for _ in terms])
        params = [f'%{t}%' for t in terms]
        cursor.execute(f'''
            SELECT e.id, e.content, t.name as tab_name
            FROM knowledge_entries e
            JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE ({conditions}){tab_filter}
            ORDER BY e.updated_at DESC LIMIT ?
        ''', params + tab_params + [limit])
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "content": r[1], "tab": r[2], "source": "knowledge", "score": 0.35} for r in rows]

    conn.close()
    return []


def _vector_search_entries(query, scope, tab_name=None, limit=10):
    embedder = _get_embedder()
    if not embedder or not embedder.available:
        return []

    query_emb = embedder.embed([query], prefix='search_query')
    if query_emb is None:
        return []
    query_vec = query_emb[0]

    conn = _get_connection()
    cursor = conn.cursor()

    if tab_name:
        cursor.execute('''
            SELECT e.id, e.content, t.name, e.embedding
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE t.scope = ? AND LOWER(t.name) = LOWER(?) AND e.embedding IS NOT NULL
        ''', (scope, tab_name))
    else:
        cursor.execute('''
            SELECT e.id, e.content, t.name, e.embedding
            FROM knowledge_entries e JOIN knowledge_tabs t ON e.tab_id = t.id
            WHERE t.scope = ? AND e.embedding IS NOT NULL
        ''', (scope,))

    rows = cursor.fetchall()
    conn.close()

    scored = []
    for eid, content, tname, emb_blob in rows:
        emb = np.frombuffer(emb_blob, dtype=np.float32)
        sim = float(np.dot(query_vec, emb))
        if sim >= SIMILARITY_THRESHOLD:
            scored.append({"id": eid, "content": content, "tab": tname, "source": "knowledge", "score": sim})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _search_people(query, limit=10):
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
            cursor.execute('SELECT id, name, relationship, phone, email, address, notes, embedding FROM people WHERE embedding IS NOT NULL')
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
            FROM people WHERE {conditions} ORDER BY name LIMIT ?
        ''', params + [limit])
        rows = cursor.fetchall()
        conn.close()
        # LIKE results get a low fixed score so they sort below vector matches
        return [{"id": r[0], "name": r[1], "relationship": r[2], "phone": r[3],
                 "email": r[4], "address": r[5], "notes": r[6], "source": "people", "score": 0.3} for r in rows]

    conn.close()
    return []


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _format_person(p):
    parts = [p["name"]]
    if p.get("relationship"): parts.append(f"({p['relationship']})")
    details = []
    if p.get("phone"): details.append(f"phone: {p['phone']}")
    if p.get("email"): details.append(f"email: {p['email']}")
    if p.get("address"): details.append(f"address: {p['address']}")
    if p.get("notes"): details.append(f"notes: {p['notes']}")
    if details:
        parts.append("— " + ", ".join(details))
    return " ".join(parts)


def _format_entry(r):
    preview = r["content"][:800] + ('...' if len(r["content"]) > 800 else '')
    tab_info = f" [tab: {r['tab']}]" if r.get("tab") else ""
    return f"{tab_info} {preview}"


# ─── Tool Operations ─────────────────────────────────────────────────────────

def _save_person(name, relationship=None, phone=None, email=None, address=None, notes=None):
    if not name or not name.strip():
        return "Person name is required.", False
    if len(name) > 100:
        return "Name too long (max 100 chars).", False

    pid, is_new = create_or_update_person(name, relationship, phone, email, address, notes)
    action = "Saved new" if is_new else "Updated"
    logger.info(f"{action} person [{pid}] '{name.strip()}'")
    return f"{action} contact: {name.strip()} (ID: {pid})", True


def _save_knowledge(tab_name, content, description=None, scope='default'):
    if not tab_name or not tab_name.strip():
        return "Tab name is required.", False
    if not content or not content.strip():
        return "Content is required.", False
    if len(tab_name) > 100:
        return "Tab name too long (max 100 chars).", False

    tab_name = tab_name.strip()
    content = content.strip()

    # Get or create tab
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                   (tab_name, scope))
    row = cursor.fetchone()
    conn.close()

    if row:
        tab_id = row[0]
    else:
        tab_id = create_tab(tab_name, scope, description, tab_type='ai')
        if not tab_id:
            return f"Failed to create tab '{tab_name}'.", False

    # Chunk if needed
    chunks = _chunk_text(content)
    entry_ids = []
    for i, chunk in enumerate(chunks):
        eid = add_entry(tab_id, chunk, chunk_index=i)
        entry_ids.append(eid)

    chunk_note = f" ({len(chunks)} chunks)" if len(chunks) > 1 else ""
    logger.info(f"Saved knowledge to tab '{tab_name}' in scope '{scope}': {len(chunks)} entries")
    return f"Saved to '{tab_name}'{chunk_note} — {len(content)} chars", True


def _search_knowledge(query, source='all', tab_name=None, limit=10, scope='default'):
    if not query or not query.strip():
        return "Search query is required.", False

    results = []

    if source in ('all', 'people'):
        results.extend(_search_people(query, limit))

    if source in ('all', 'knowledge'):
        results.extend(_search_entries(query, scope, tab_name, limit))

    if not results:
        return f"No results for '{query}'.", True

    # Sort all results by score (highest first) — unified ranking across sources
    results.sort(key=lambda r: r.get("score", 0), reverse=True)

    # Format output
    lines = [f"Found {len(results)} results:"]
    for r in results[:limit]:
        if r["source"] == "people":
            lines.append(f"  [Person] {_format_person(r)}")
        else:
            lines.append(f"  [Knowledge]{_format_entry(r)}")

    return '\n'.join(lines), True


def _list_knowledge(source='all', tab_name=None, scope='default'):
    lines = []

    if tab_name:
        # Read entries from specific tab
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM knowledge_tabs WHERE LOWER(name) = LOWER(?) AND scope = ?',
                       (tab_name, scope))
        tab = cursor.fetchone()
        conn.close()
        if not tab:
            return f"Tab '{tab_name}' not found in scope '{scope}'.", True
        entries = get_tab_entries(tab[0])
        if not entries:
            return f"Tab '{tab_name}' is empty.", True
        lines.append(f"=== Tab: {tab_name} ({len(entries)} entries) ===")
        for e in entries:
            preview = e["content"][:500] + ('...' if len(e["content"]) > 500 else '')
            lines.append(f"  [{e['id']}] {preview}")
        return '\n'.join(lines), True

    # Overview
    if source in ('all', 'people'):
        people = get_people()
        lines.append(f"=== People ({len(people)}) ===")
        if people:
            for p in people[:10]:
                lines.append(f"  {_format_person(p)}")
            if len(people) > 10:
                lines.append(f"  ... and {len(people) - 10} more")
        else:
            lines.append("  (none)")

    if source in ('all', 'tabs'):
        tabs = get_tabs(scope)
        lines.append(f"\n=== Knowledge Tabs (scope: {scope}, {len(tabs)} tabs) ===")
        if tabs:
            for t in tabs:
                type_tag = f" [{t['type']}]" if t['type'] == 'ai' else ""
                lines.append(f"  {t['name']}{type_tag} — {t['entry_count']} entries")
        else:
            lines.append("  (none)")

    if not lines:
        return "Knowledge base is empty.", True

    return '\n'.join(lines), True


# ─── Executor ─────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        scope = _get_current_scope()
        if scope is None:
            return "Knowledge base is disabled for this chat.", False

        if function_name == "save_person":
            return _save_person(
                name=arguments.get('name'),
                relationship=arguments.get('relationship'),
                phone=arguments.get('phone'),
                email=arguments.get('email'),
                address=arguments.get('address'),
                notes=arguments.get('notes'),
            )

        elif function_name == "save_knowledge":
            return _save_knowledge(
                tab_name=arguments.get('tab_name'),
                content=arguments.get('content'),
                description=arguments.get('description'),
                scope=scope,
            )

        elif function_name == "search_knowledge":
            return _search_knowledge(
                query=arguments.get('query'),
                source=arguments.get('source', 'all'),
                tab_name=arguments.get('tab_name'),
                limit=arguments.get('limit', 10),
                scope=scope,
            )

        elif function_name == "list_knowledge":
            return _list_knowledge(
                source=arguments.get('source', 'all'),
                tab_name=arguments.get('tab_name'),
                scope=scope,
            )

        else:
            return f"Unknown knowledge function '{function_name}'.", False

    except Exception as e:
        logger.error(f"Knowledge function error in {function_name}: {e}", exc_info=True)
        return f"Knowledge system error: {str(e)}", False
