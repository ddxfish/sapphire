# functions/memory.py
# Long-term memory with FTS5 full-text search, semantic embeddings, and labels

import sqlite3
import logging
import re
import numpy as np
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '💾'

# Database location - lazy initialized
_db_path = None
_db_initialized = False

# Embedding model - lazy loaded
_embedder = None

SUGGESTED_LABELS = "family, preferences, technical, stories, people, places, routines, opinions, self"

AVAILABLE_FUNCTIONS = [
    'save_memory',
    'search_memory',
    'get_recent_memories',
    'delete_memory',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "save_memory",
            "description": f"Save information to long-term memory. Max 512 chars - be concise. Assign a label to categorize. Suggested labels: {SUGGESTED_LABELS}. You can create new labels too. Use 'self' for your own self-knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember"
                    },
                    "label": {
                        "type": "string",
                        "description": f"Category label (e.g. {SUGGESTED_LABELS})"
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
            "description": "Search stored memories using semantic similarity and full-text search. Understands meaning, not just keywords. Optionally filter by label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms or topic"
                    },
                    "label": {
                        "type": "string",
                        "description": "Filter by label(s), comma-separated for multiple (e.g. 'family,people')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return",
                        "default": 10
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
            "description": "Get the most recent memories, optionally filtered by label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent memories to retrieve",
                        "default": 10
                    },
                    "label": {
                        "type": "string",
                        "description": "Filter by label(s), comma-separated for multiple (e.g. 'family,people')"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "delete_memory",
            "description": "Delete a memory by its ID number",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "integer",
                        "description": "The ID number of the memory to delete (shown in brackets like [42])"
                    }
                },
                "required": ["memory_id"]
            }
        }
    },
]


STOPWORDS = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
             'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
             'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
             'would', 'should', 'could', 'may', 'might', 'can', 'this', 'that',
             'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'}

EMBEDDING_MODEL = 'nomic-ai/nomic-embed-text-v1.5'
EMBEDDING_ONNX_FILE = 'onnx/model_quantized.onnx'
SIMILARITY_THRESHOLD = 0.40


# ─── Embedding Engine ────────────────────────────────────────────────────────

class EmbeddingEngine:
    """Lazy-loaded nomic-embed-text-v1.5 via ONNX runtime."""

    def __init__(self):
        self.session = None
        self.tokenizer = None
        self.input_names = None

    def _load(self):
        if self.session is not None:
            return
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
            from huggingface_hub import hf_hub_download

            # Try local cache first to avoid phoning home to HuggingFace
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    EMBEDDING_MODEL, trust_remote_code=True, local_files_only=True)
                model_path = hf_hub_download(
                    EMBEDDING_MODEL, EMBEDDING_ONNX_FILE, local_files_only=True)
            except Exception:
                # First run — download and cache
                logger.info(f"Downloading embedding model: {EMBEDDING_MODEL}")
                self.tokenizer = AutoTokenizer.from_pretrained(
                    EMBEDDING_MODEL, trust_remote_code=True)
                model_path = hf_hub_download(EMBEDDING_MODEL, EMBEDDING_ONNX_FILE)

            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            self.input_names = [i.name for i in self.session.get_inputs()]
            logger.info(f"Embedding model loaded: {EMBEDDING_MODEL} (quantized ONNX, local cache)")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            self.session = None

    def embed(self, texts, prefix='search_document'):
        """
        Generate embeddings for a list of texts.
        prefix: 'search_document' for stored content, 'search_query' for queries
        Returns numpy array of shape (n, dim) or None on failure.
        """
        self._load()
        if self.session is None:
            return None

        try:
            prefixed = [f'{prefix}: {t}' for t in texts]
            encoded = self.tokenizer(prefixed, return_tensors='np', padding=True,
                                     truncation=True, max_length=512)
            inputs = {k: v for k, v in encoded.items() if k in self.input_names}
            if 'token_type_ids' not in inputs:
                inputs['token_type_ids'] = np.zeros_like(inputs['input_ids'])

            outputs = self.session.run(None, inputs)
            embeddings = outputs[0]
            mask = encoded['attention_mask']
            masked = embeddings * mask[:, :, np.newaxis]
            pooled = masked.sum(axis=1) / mask.sum(axis=1, keepdims=True)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            return (pooled / norms).astype(np.float32)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return None

    @property
    def available(self):
        self._load()
        return self.session is not None


def _get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingEngine()
    return _embedder


# ─── Database ────────────────────────────────────────────────────────────────

def _get_db_path():
    global _db_path
    if _db_path is None:
        project_root = Path(__file__).parent.parent
        _db_path = project_root / "user" / "memory.db"
    return _db_path


def _get_connection():
    _ensure_db()
    conn = sqlite3.connect(_get_db_path(), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _repair_db(db_path):
    """Attempt to salvage memories from a corrupted database into a fresh one."""
    backup_path = db_path.with_suffix('.db.corrupted')
    try:
        # Try to read memories from corrupted db
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        # Grab whatever we can - ignore columns that may not exist
        try:
            cursor.execute('SELECT id, content, timestamp, importance, keywords, context, scope, label FROM memories')
        except sqlite3.DatabaseError:
            try:
                cursor.execute('SELECT id, content, timestamp, importance, keywords, context, scope FROM memories')
            except sqlite3.DatabaseError:
                try:
                    cursor.execute('SELECT id, content, timestamp, importance, keywords, context FROM memories')
                except sqlite3.DatabaseError:
                    conn.close()
                    logger.error("Cannot read any data from corrupted database")
                    db_path.rename(backup_path)
                    logger.info(f"Corrupted database moved to {backup_path}")
                    return

        rows = cursor.fetchall()
        col_count = len(rows[0]) if rows else 0
        conn.close()

        # Rename corrupted file
        db_path.rename(backup_path)
        logger.info(f"Corrupted database backed up to {backup_path}")

        if not rows:
            return

        # Create fresh db and insert salvaged rows
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute('''
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                importance INTEGER DEFAULT 5,
                keywords TEXT,
                context TEXT,
                scope TEXT NOT NULL DEFAULT 'default',
                label TEXT,
                embedding BLOB
            )
        ''')
        for row in rows:
            # Pad missing columns with defaults
            r = list(row) + [None] * (8 - len(row))
            cursor.execute(
                'INSERT INTO memories (id, content, timestamp, importance, keywords, context, scope, label) VALUES (?,?,?,?,?,?,?,?)',
                r[:8]
            )
        conn.commit()
        conn.close()
        logger.info(f"Salvaged {len(rows)} memories into fresh database")

    except Exception as e:
        logger.error(f"Repair failed: {e}")
        # Last resort - move corrupted file so fresh db can be created
        if db_path.exists() and not backup_path.exists():
            db_path.rename(backup_path)
            logger.info(f"Corrupted database moved to {backup_path}")


def _setup_fts(cursor):
    """Create FTS5 table, triggers, and populate from existing data."""
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content, keywords, label,
            content=memories, content_rowid=id
        )
    """)

    # Drop old triggers (may have wrong scope from previous version)
    cursor.execute("DROP TRIGGER IF EXISTS memories_fts_insert")
    cursor.execute("DROP TRIGGER IF EXISTS memories_fts_delete")
    cursor.execute("DROP TRIGGER IF EXISTS memories_fts_update")

    cursor.execute("""
        CREATE TRIGGER memories_fts_insert
        AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, keywords, label)
            VALUES (new.id, new.content, new.keywords, new.label);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER memories_fts_delete
        AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, keywords, label)
            VALUES ('delete', old.id, old.content, old.keywords, old.label);
        END
    """)
    # Only fire on FTS-indexed columns, NOT on embedding updates
    cursor.execute("""
        CREATE TRIGGER memories_fts_update
        AFTER UPDATE OF content, keywords, label ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, keywords, label)
            VALUES ('delete', old.id, old.content, old.keywords, old.label);
            INSERT INTO memories_fts(rowid, content, keywords, label)
            VALUES (new.id, new.content, new.keywords, new.label);
        END
    """)

    # Populate if empty
    cursor.execute("SELECT COUNT(*) FROM memories")
    mem_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM memories_fts")
    fts_count = cursor.fetchone()[0]

    if mem_count > 0 and fts_count == 0:
        logger.info(f"Populating FTS5 index from {mem_count} existing memories...")
        cursor.execute("""
            INSERT INTO memories_fts(rowid, content, keywords, label)
            SELECT id, content, keywords, label FROM memories
        """)


def _ensure_db():
    """Initialize database with FTS5 + embedding column. Migrates from old schema."""
    global _db_initialized
    if _db_initialized:
        return True

    try:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Health check - detect corruption before doing anything
        if db_path.exists():
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                cursor = conn.cursor()
                result = cursor.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result[0] != 'ok':
                    logger.error(f"Database integrity check failed: {result[0]}")
                    _repair_db(db_path)
            except sqlite3.DatabaseError as e:
                logger.error(f"Database corrupted: {e}")
                _repair_db(db_path)

        # Clean up stale WAL/journal files if db was replaced
        for suffix in ['-wal', '-shm', '-journal']:
            stale = db_path.with_name(db_path.name + suffix)
            if stale.exists() and not db_path.exists():
                stale.unlink()

        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")

        # Core table (may already exist from old schema)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                importance INTEGER DEFAULT 5,
                keywords TEXT,
                context TEXT
            )
        ''')

        # Migrations: add columns if missing
        cursor.execute("PRAGMA table_info(memories)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'scope' not in columns:
            cursor.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'default'")
            logger.info("Migration: added scope column")
        if 'label' not in columns:
            cursor.execute("ALTER TABLE memories ADD COLUMN label TEXT")
            logger.info("Migration: added label column")
        if 'embedding' not in columns:
            cursor.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
            logger.info("Migration: added embedding column")

        # Indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_scope ON memories(scope)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_label ON memories(label)')

        # FTS5 - try setup, rebuild on corruption
        try:
            _setup_fts(cursor)
        except sqlite3.DatabaseError as e:
            logger.warning(f"FTS5 corrupted, rebuilding: {e}")
            cursor.execute("DROP TABLE IF EXISTS memories_fts")
            cursor.execute("DROP TRIGGER IF EXISTS memories_fts_insert")
            cursor.execute("DROP TRIGGER IF EXISTS memories_fts_delete")
            cursor.execute("DROP TRIGGER IF EXISTS memories_fts_update")
            conn.commit()
            _setup_fts(cursor)

        # Scope registry
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_scopes (
                name TEXT PRIMARY KEY,
                created DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("INSERT OR IGNORE INTO memory_scopes (name) VALUES ('default')")

        conn.commit()
        conn.close()

        _db_initialized = True
        logger.info(f"Memory database ready at {db_path} (FTS5 + embeddings)")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize memory database: {e}")
        return False


_backfill_done = False

def _backfill_embeddings():
    """Generate embeddings for memories that don't have them yet. Called lazily."""
    global _backfill_done
    if _backfill_done:
        return

    embedder = _get_embedder()
    if not embedder.available:
        _backfill_done = True
        return

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, content FROM memories WHERE embedding IS NULL')
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        _backfill_done = True
        return

    logger.info(f"Backfilling embeddings for {len(rows)} memories...")
    batch_size = 32
    filled = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        embs = embedder.embed(texts, prefix='search_document')
        if embs is None:
            break
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            for row_id, emb in zip(ids, embs):
                cursor.execute('UPDATE memories SET embedding = ? WHERE id = ?',
                               (emb.tobytes(), row_id))
            conn.commit()
            conn.close()
            filled += len(batch)
        except Exception as e:
            logger.error(f"Backfill batch failed: {e}")
            try:
                conn.close()
            except Exception:
                pass
            break

    _backfill_done = True
    if filled:
        logger.info(f"Backfill complete: {filled}/{len(rows)} memories embedded")


def _get_current_scope():
    try:
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_memory_scope
    except Exception as e:
        logger.warning(f"Could not get memory scope: {e}, using 'default'")
        return 'default'


# ─── Public API (used by api_fastapi.py) ─────────────────────────────────────

def get_scopes():
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT scope, COUNT(*) FROM memories GROUP BY scope')
        memory_counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute('SELECT name FROM memory_scopes ORDER BY name')
        registered = [row[0] for row in cursor.fetchall()]
        conn.close()
        all_scopes = set(registered) | set(memory_counts.keys()) | {'default'}
        return [{"name": name, "count": memory_counts.get(name, 0)} for name in sorted(all_scopes)]
    except Exception as e:
        logger.error(f"Error getting scopes: {e}")
        return [{"name": "default", "count": 0}]


def create_scope(name: str) -> bool:
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO memory_scopes (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to create scope '{name}': {e}")
        return False


def delete_scope(name: str) -> dict:
    """Delete a memory scope and ALL memories in it. Returns {deleted_count}."""
    if name == 'default':
        return {"error": "Cannot delete the default scope"}
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM memories WHERE scope = ?', (name,))
        count = cursor.fetchone()[0]
        cursor.execute('DELETE FROM memories WHERE scope = ?', (name,))
        cursor.execute('DELETE FROM memory_scopes WHERE name = ?', (name,))
        conn.commit()
        conn.close()
        logger.info(f"Deleted memory scope '{name}' with {count} memories")
        return {"deleted_count": count}
    except Exception as e:
        logger.error(f"Failed to delete memory scope '{name}': {e}")
        return {"error": str(e)}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _extract_keywords(content: str) -> str:
    words = content.lower().split()
    keywords = [w.strip('.,!?;:\'\"()') for w in words if len(w) > 2 and w.lower() not in STOPWORDS]
    return ' '.join(sorted(set(keywords)))


def _format_time_ago(timestamp_str: str) -> str:
    try:
        ts = datetime.fromisoformat(timestamp_str)
        diff = datetime.now() - ts
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


def _format_memory(row_id, content, timestamp, label):
    time_ago = _format_time_ago(timestamp)
    time_str = f" ({time_ago})" if time_ago else ""
    label_str = f" [{label}]" if label else ""
    preview = content[:150] + ('...' if len(content) > 150 else '')
    return f"[{row_id}]{time_str}{label_str} {preview}"


def _parse_labels(label) -> list:
    """Parse comma-separated label string into list of lowercase labels."""
    if not label:
        return []
    return [l.strip().lower() for l in label.split(',') if l.strip()]


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


# ─── Core Operations ─────────────────────────────────────────────────────────

MAX_MEMORY_LENGTH = 512


def _save_memory(content: str, label: str = None, scope: str = 'default') -> tuple:
    try:
        if not content or not content.strip():
            return "Cannot save empty memory.", False
        if len(content) > MAX_MEMORY_LENGTH:
            return f"Memory too long ({len(content)} chars). Max is {MAX_MEMORY_LENGTH}. Write a shorter, more concise memory.", False

        content = content.strip()
        keywords = _extract_keywords(content)
        label = label.strip().lower() if label else None

        # Generate embedding
        embedding_blob = None
        embedder = _get_embedder()
        if embedder.available:
            embs = embedder.embed([content], prefix='search_document')
            if embs is not None:
                embedding_blob = embs[0].tobytes()

        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO memories (content, keywords, scope, label, embedding) VALUES (?, ?, ?, ?, ?)',
            (content, keywords, scope, label, embedding_blob)
        )
        memory_id = cursor.lastrowid
        conn.commit()
        conn.close()

        label_str = f", label: {label}" if label else ""
        logger.info(f"Stored memory ID {memory_id} in scope '{scope}'{label_str}")
        return f"Memory saved (ID: {memory_id}{label_str})", True

    except Exception as e:
        logger.error(f"Error saving memory: {e}")
        return f"Failed to save memory: {e}", False


def _fts_search(cursor, fts_query, scope, labels, limit):
    if labels:
        placeholders = ','.join('?' * len(labels))
        cursor.execute(f'''
            SELECT m.id, m.content, m.timestamp, m.label, bm25(memories_fts) as rank
            FROM memories_fts f JOIN memories m ON f.rowid = m.id
            WHERE memories_fts MATCH ? AND m.scope = ? AND m.label IN ({placeholders})
            ORDER BY rank LIMIT ?
        ''', [fts_query, scope] + labels + [limit])
    else:
        cursor.execute('''
            SELECT m.id, m.content, m.timestamp, m.label, bm25(memories_fts) as rank
            FROM memories_fts f JOIN memories m ON f.rowid = m.id
            WHERE memories_fts MATCH ? AND m.scope = ?
            ORDER BY rank LIMIT ?
        ''', (fts_query, scope, limit))
    return cursor.fetchall()


def _vector_search(query: str, scope: str, labels: list, limit: int) -> list:
    """
    Semantic search via cosine similarity on stored embeddings.
    Returns list of (id, content, timestamp, label, similarity) tuples.
    """
    embedder = _get_embedder()
    if not embedder.available:
        return []

    query_emb = embedder.embed([query], prefix='search_query')
    if query_emb is None:
        return []
    query_vec = query_emb[0]

    conn = _get_connection()
    cursor = conn.cursor()

    if labels:
        placeholders = ','.join('?' * len(labels))
        cursor.execute(
            f'SELECT id, content, timestamp, label, embedding FROM memories WHERE scope = ? AND label IN ({placeholders}) AND embedding IS NOT NULL',
            [scope] + labels)
    else:
        cursor.execute(
            'SELECT id, content, timestamp, label, embedding FROM memories WHERE scope = ? AND embedding IS NOT NULL',
            (scope,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return []

    # Compute cosine similarity (vectors are already L2-normalized)
    scored = []
    for row_id, content, timestamp, lbl, emb_blob in rows:
        emb = np.frombuffer(emb_blob, dtype=np.float32)
        sim = float(np.dot(query_vec, emb))
        if sim >= SIMILARITY_THRESHOLD:
            scored.append((row_id, content, timestamp, lbl, sim))

    scored.sort(key=lambda x: x[4], reverse=True)
    return scored[:limit]


def _search_memory(query: str, limit: int = 10, label: str = None, scope: str = 'default') -> tuple:
    """
    Search memories with cascading strategy:
    1. FTS5 AND (exact token match)
    2. FTS5 OR + prefix (broader token match)
    3. Vector similarity (semantic match)
    4. LIKE fallback
    """
    try:
        if not query or not query.strip():
            return "Search query cannot be empty.", False

        labels = _parse_labels(label)
        label_note = f" with labels '{label}'" if labels else ""

        # Trigger backfill on first search (lazy, one-time)
        _backfill_embeddings()

        conn = _get_connection()
        cursor = conn.cursor()

        # Strategy 1: FTS5 exact AND
        fts_exact = _sanitize_fts_query(query)
        if fts_exact:
            try:
                rows = _fts_search(cursor, fts_exact, scope, labels, limit)
                if rows:
                    conn.close()
                    results = [_format_memory(r[0], r[1], r[2], r[3]) for r in rows]
                    return f"Found {len(rows)} memories:\n" + "\n".join(results), True

                # Strategy 2: FTS5 OR + prefix
                fts_broad = _sanitize_fts_query(query, use_or=True, use_prefix=True)
                if fts_broad != fts_exact:
                    rows = _fts_search(cursor, fts_broad, scope, labels, limit)
                    if rows:
                        conn.close()
                        results = [_format_memory(r[0], r[1], r[2], r[3]) for r in rows]
                        return f"Found {len(rows)} memories:\n" + "\n".join(results), True
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 query failed: {e}")

        conn.close()

        # Strategy 3: Vector similarity (semantic)
        vec_results = _vector_search(query, scope, labels, limit)
        if vec_results:
            results = [_format_memory(r[0], r[1], r[2], r[3]) for r in vec_results]
            return f"Found {len(vec_results)} memories:\n" + "\n".join(results), True

        # Strategy 4: LIKE fallback
        terms = query.lower().split()[:5]
        if terms:
            conn = _get_connection()
            cursor = conn.cursor()
            conditions = ' OR '.join(['(content LIKE ? OR keywords LIKE ?)' for _ in terms])
            params = []
            for term in terms:
                params.extend([f'%{term}%', f'%{term}%'])
            if labels:
                placeholders = ','.join('?' * len(labels))
                label_filter = f" AND label IN ({placeholders})"
                params.extend(labels)
            else:
                label_filter = ""
            cursor.execute(f'''
                SELECT id, content, timestamp, label FROM memories
                WHERE scope = ? AND ({conditions}){label_filter}
                ORDER BY timestamp DESC LIMIT ?
            ''', [scope] + params + [limit])
            rows = cursor.fetchall()
            conn.close()
            if rows:
                results = [_format_memory(r[0], r[1], r[2], r[3]) for r in rows]
                return f"Found {len(rows)} memories:\n" + "\n".join(results), True

        return f"No memories found for '{query}'{label_note}.", True

    except Exception as e:
        logger.error(f"Error searching memory: {e}")
        return f"Search failed: {e}", False


def _get_recent_memories(count: int = 10, label: str = None, scope: str = 'default') -> tuple:
    try:
        labels = _parse_labels(label)
        conn = _get_connection()
        cursor = conn.cursor()
        if labels:
            placeholders = ','.join('?' * len(labels))
            cursor.execute(f'''
                SELECT id, content, timestamp, label FROM memories
                WHERE scope = ? AND label IN ({placeholders}) ORDER BY timestamp DESC LIMIT ?
            ''', [scope] + labels + [count])
        else:
            cursor.execute('''
                SELECT id, content, timestamp, label FROM memories
                WHERE scope = ? ORDER BY timestamp DESC LIMIT ?
            ''', (scope, count))
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            label_note = f" with labels '{label}'" if labels else ""
            return f"No memories stored{label_note}.", True
        results = [_format_memory(r[0], r[1], r[2], r[3]) for r in rows]
        return f"Recent {len(rows)} memories:\n" + "\n".join(results), True
    except Exception as e:
        logger.error(f"Error getting recent memories: {e}")
        return f"Failed to retrieve memories: {e}", False


def _delete_memory(memory_id: int, scope: str = 'default') -> tuple:
    try:
        if not isinstance(memory_id, int) or memory_id < 1:
            return "Invalid memory ID. Use the number shown in brackets [N].", False
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, content FROM memories WHERE id = ? AND scope = ?', (memory_id, scope))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return f"Memory [{memory_id}] not found in current memory slot.", False
        cursor.execute('DELETE FROM memories WHERE id = ? AND scope = ?', (memory_id, scope))
        conn.commit()
        conn.close()
        preview = row[1][:50] + ('...' if len(row[1]) > 50 else '')
        logger.info(f"Deleted memory ID {memory_id} from scope '{scope}'")
        return f"Deleted memory [{memory_id}]: {preview}", True
    except Exception as e:
        logger.error(f"Error deleting memory: {e}")
        return f"Failed to delete memory: {e}", False


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name: str, arguments: dict, config) -> tuple:
    try:
        scope = _get_current_scope()
        if scope is None:
            return "Memory is disabled for this chat.", False

        if function_name == "save_memory":
            return _save_memory(arguments.get("content", ""), arguments.get("label"), scope)
        elif function_name == "search_memory":
            return _search_memory(arguments.get("query", ""), arguments.get("limit", 10),
                                  arguments.get("label"), scope)
        elif function_name == "get_recent_memories":
            return _get_recent_memories(arguments.get("count", 10), arguments.get("label"), scope)
        elif function_name == "delete_memory":
            memory_id = arguments.get("memory_id")
            if memory_id is None:
                return "Missing memory_id parameter.", False
            return _delete_memory(int(memory_id), scope)
        else:
            return f"Unknown memory function: {function_name}", False
    except Exception as e:
        logger.error(f"Memory function error: {e}")
        return f"Memory error: {e}", False
