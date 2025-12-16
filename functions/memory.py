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
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save important information to long-term memory for future conversations",
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
        
        conn.commit()
        conn.close()
        
        _db_initialized = True
        logger.info(f"Memory database ready at {db_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize memory database: {e}")
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


def _save_memory(content: str, importance: int = 5) -> tuple:
    """Store a new memory."""
    try:
        if not content or not content.strip():
            return "Cannot save empty memory.", False
        
        importance = max(1, min(10, importance))
        keywords = _extract_keywords(content)
        
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO memories (content, importance, keywords)
            VALUES (?, ?, ?)
        ''', (content.strip(), importance, keywords))
        
        memory_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"Stored memory ID {memory_id} with importance {importance}")
        return f"Memory saved (ID: {memory_id}, importance: {importance})", True
        
    except Exception as e:
        logger.error(f"Error saving memory: {e}")
        return f"Failed to save memory: {e}", False


def _search_memory(query: str, limit: int = 10) -> tuple:
    """Search memories by query string."""
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
        
        # Build LIKE query for each term
        like_conditions = ' OR '.join(['(content LIKE ? OR keywords LIKE ?)' for _ in search_terms])
        like_params = []
        for term in search_terms:
            like_params.extend([f'%{term}%', f'%{term}%'])
        
        cursor.execute(f'''
            SELECT id, content, timestamp, importance
            FROM memories
            WHERE {like_conditions}
            ORDER BY importance DESC, timestamp DESC
            LIMIT ?
        ''', like_params + [limit])
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return f"No memories found matching '{query}'", True
        
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


def _get_recent_memories(count: int = 10) -> tuple:
    """Get most recent memories."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, content, timestamp, importance
            FROM memories
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (count,))
        
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


def execute(function_name: str, arguments: dict, config) -> tuple:
    """Execute memory function. Returns (result_string, success_bool)."""
    try:
        if function_name == "save_memory":
            content = arguments.get("content", "")
            importance = arguments.get("importance", 5)
            return _save_memory(content, importance)
        
        elif function_name == "search_memory":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 10)
            return _search_memory(query, limit)
        
        elif function_name == "get_recent_memories":
            count = arguments.get("count", 10)
            return _get_recent_memories(count)
        
        else:
            return f"Unknown memory function: {function_name}", False
            
    except Exception as e:
        logger.error(f"Memory function error: {e}")
        return f"Memory error: {e}", False