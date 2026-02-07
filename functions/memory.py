# functions/memory.py
# Direct SQLite memory storage - no server required

import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True

# Database location - lazy initialized
_db_path = None
_db_initialized = False

STOPWORDS = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
             'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
             'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
             'would', 'should', 'could', 'may', 'might', 'can', 'this', 'that',
             'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'}

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
            "description": "Save important information to long-term memory for future conversations. Max 512 characters - be concise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The information to remember"
                    },
                    "importance": {
                        "type": "integer",
                        "description": "Importance level 1-10 (10 = critical)",
                        "default": 5
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
            "description": "Search stored memories by keywords or topic",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms or topic"
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
            "description": "Get the most recent memories",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent memories to retrieve",
                        "default": 10
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
    }
]


def _get_db_path():
    """Get database path, resolving from project root."""
    global _db_path
    if _db_path is None:
        # Resolve path relative to project root (functions/ is one level down)
        project_root = Path(__file__).parent.parent
        _db_path = project_root / "user" / "memory.db"
    return _db_path


def _ensure_db():
    """Initialize database if needed. Called lazily on first access."""
    global _db_initialized
    if _db_initialized:
        return True
    
    try:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                importance INTEGER DEFAULT 5 CHECK(importance >= 1 AND importance <= 10),
                keywords TEXT,
                context TEXT
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_keywords ON memories(keywords)')
        
        # Migration: add scope column if not exists
        cursor.execute("PRAGMA table_info(memories)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'scope' not in columns:
            cursor.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'default'")
            logger.info("Migrated memories table: added scope column")
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_scope ON memories(scope)')
        
        # Registry table for empty scopes (so they persist before first write)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_scopes (
                name TEXT PRIMARY KEY,
                created DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Ensure 'default' scope exists in registry
        cursor.execute("INSERT OR IGNORE INTO memory_scopes (name) VALUES ('default')")
        
        conn.commit()
        conn.close()
        
        _db_initialized = True
        logger.info(f"Memory database ready at {db_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize memory database: {e}")
        return False


def _get_current_scope():
    """Get current memory scope from FunctionManager. Returns None if disabled."""
    try:
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_memory_scope
    except Exception as e:
        logger.warning(f"Could not get memory scope: {e}, using 'default'")
        return 'default'


def get_scopes():
    """Get list of memory scopes with counts (includes registered empty scopes)."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        # Get counts from memories table
        cursor.execute('''
            SELECT scope, COUNT(*) as count 
            FROM memories 
            GROUP BY scope 
            ORDER BY scope
        ''')
        memory_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Get all registered scopes (including empty ones)
        cursor.execute('SELECT name FROM memory_scopes ORDER BY name')
        registered = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        # Merge: all registered scopes + any scopes with data
        all_scopes = set(registered) | set(memory_counts.keys())
        
        # Always include 'default'
        all_scopes.add('default')
        
        return [{"name": name, "count": memory_counts.get(name, 0)} for name in sorted(all_scopes)]
    except Exception as e:
        logger.error(f"Error getting scopes: {e}")
        return [{"name": "default", "count": 0}]


def create_scope(name: str) -> bool:
    """Register a new memory scope (persists even when empty)."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO memory_scopes (name) VALUES (?)", (name,))
        conn.commit()
        inserted = cursor.rowcount > 0
        conn.close()
        
        if inserted:
            logger.info(f"Created memory scope: {name}")
        else:
            logger.debug(f"Memory scope already exists: {name}")
        return True
    except Exception as e:
        logger.error(f"Failed to create scope '{name}': {e}")
        return False


def _get_connection():
    """Get database connection, ensuring DB exists first."""
    _ensure_db()
    return sqlite3.connect(_get_db_path())


def _extract_keywords(content: str) -> str:
    """Extract keywords from content by removing stopwords."""
    words = content.lower().split()
    keywords = [w.strip('.,!?;:') for w in words if len(w) > 2 and w.lower() not in STOPWORDS]
    return ' '.join(sorted(set(keywords)))


def _format_time_ago(timestamp_str: str) -> str:
    """Format timestamp as simple relative time."""
    try:
        ts = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        diff = now - ts
        
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        
        if days > 0:
            return f"{days}d ago"
        elif hours > 0:
            return f"{hours}h ago"
        elif minutes > 0:
            return f"{minutes}m ago"
        else:
            return "just now"
    except:
        return ""


MAX_MEMORY_LENGTH = 512

def _save_memory(content: str, importance: int = 5, scope: str = 'default') -> tuple:
    """Store a new memory in the given scope."""
    try:
        if not content or not content.strip():
            return "Cannot save empty memory.", False

        if len(content) > MAX_MEMORY_LENGTH:
            return f"Memory too long ({len(content)} chars). Max is {MAX_MEMORY_LENGTH}. Write a shorter, more concise memory.", False
        
        importance = max(1, min(10, importance))
        keywords = _extract_keywords(content)
        
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO memories (content, importance, keywords, scope)
            VALUES (?, ?, ?, ?)
        ''', (content.strip(), importance, keywords, scope))
        
        memory_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"Stored memory ID {memory_id} with importance {importance} in scope '{scope}'")
        return f"Memory saved (ID: {memory_id}, importance: {importance})", True
        
    except Exception as e:
        logger.error(f"Error saving memory: {e}")
        return f"Failed to save memory: {e}", False


def _search_memory(query: str, limit: int = 10, scope: str = 'default') -> tuple:
    """Search memories by query string within scope. Multiple terms use AND logic."""
    try:
        if not query or not query.strip():
            return "Search query cannot be empty.", False
        
        query_keywords = _extract_keywords(query)
        search_terms = query_keywords.split()
        
        if not search_terms:
            # Fall back to raw query words if keyword extraction removed everything
            search_terms = query.lower().split()[:5]
        
        conn = _get_connection()
        cursor = conn.cursor()
        
        # Build AND query - all terms must match, within scope
        like_conditions = ' AND '.join(['(content LIKE ? OR keywords LIKE ?)' for _ in search_terms])
        like_params = []
        for term in search_terms:
            like_params.extend([f'%{term}%', f'%{term}%'])
        
        cursor.execute(f'''
            SELECT id, content, timestamp, importance
            FROM memories
            WHERE scope = ? AND ({like_conditions})
            ORDER BY importance DESC, timestamp DESC
            LIMIT ?
        ''', [scope] + like_params + [limit])
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return f"No memories found matching all terms: '{query}'", True
        
        results = []
        for row in rows:
            time_ago = _format_time_ago(row[2])
            time_str = f" ({time_ago})" if time_ago else ""
            preview = row[1][:150] + ('...' if len(row[1]) > 150 else '')
            results.append(f"[{row[0]}]{time_str} importance:{row[3]} - {preview}")
        
        return f"Found {len(rows)} memories:\n" + "\n".join(results), True
        
    except Exception as e:
        logger.error(f"Error searching memory: {e}")
        return f"Search failed: {e}", False


def _get_recent_memories(count: int = 10, scope: str = 'default') -> tuple:
    """Get most recent memories within scope."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, content, timestamp, importance
            FROM memories
            WHERE scope = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (scope, count))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "No memories stored yet.", True
        
        results = []
        for row in rows:
            time_ago = _format_time_ago(row[2])
            time_str = f" ({time_ago})" if time_ago else ""
            preview = row[1][:150] + ('...' if len(row[1]) > 150 else '')
            results.append(f"[{row[0]}]{time_str} importance:{row[3]} - {preview}")
        
        return f"Recent {len(rows)} memories:\n" + "\n".join(results), True
        
    except Exception as e:
        logger.error(f"Error getting recent memories: {e}")
        return f"Failed to retrieve memories: {e}", False


def _delete_memory(memory_id: int, scope: str = 'default') -> tuple:
    """Delete a memory by ID within scope."""
    try:
        if not isinstance(memory_id, int) or memory_id < 1:
            return "Invalid memory ID. Use the number shown in brackets [N].", False
        
        conn = _get_connection()
        cursor = conn.cursor()
        
        # Check if memory exists in this scope
        cursor.execute('SELECT id, content FROM memories WHERE id = ? AND scope = ?', (memory_id, scope))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return f"Memory [{memory_id}] not found in current memory slot.", False
        
        # Delete it
        cursor.execute('DELETE FROM memories WHERE id = ? AND scope = ?', (memory_id, scope))
        conn.commit()
        conn.close()
        
        preview = row[1][:50] + ('...' if len(row[1]) > 50 else '')
        logger.info(f"Deleted memory ID {memory_id} from scope '{scope}'")
        return f"Deleted memory [{memory_id}]: {preview}", True
        
    except Exception as e:
        logger.error(f"Error deleting memory: {e}")
        return f"Failed to delete memory: {e}", False


def execute(function_name: str, arguments: dict, config) -> tuple:
    """Execute memory function. Returns (result_string, success_bool)."""
    try:
        # Get current memory scope from FunctionManager
        scope = _get_current_scope()
        
        # If scope is None, memory is disabled for this chat
        if scope is None:
            return "Memory is disabled for this chat.", False
        
        if function_name == "save_memory":
            content = arguments.get("content", "")
            importance = arguments.get("importance", 5)
            return _save_memory(content, importance, scope)
        
        elif function_name == "search_memory":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 10)
            return _search_memory(query, limit, scope)
        
        elif function_name == "get_recent_memories":
            count = arguments.get("count", 10)
            return _get_recent_memories(count, scope)
        
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