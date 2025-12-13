# core/modules/memory_engine/create_db.py

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def initialize_database(db_path: str) -> bool:
    """Create database schema if it doesn't exist."""
    try:
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
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
        
        logger.info(f"Database initialized at {db_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False