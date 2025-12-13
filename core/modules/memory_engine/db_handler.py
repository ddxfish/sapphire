# core/modules/memory_engine/db_handler.py

import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

STOPWORDS = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
             'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
             'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
             'would', 'should', 'could', 'may', 'might', 'can', 'this', 'that',
             'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'}

class MemoryDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def _get_connection(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)
    
    def _extract_keywords(self, content: str) -> str:
        """Extract keywords from content by removing stopwords."""
        words = content.lower().split()
        keywords = [w.strip('.,!?;:') for w in words if len(w) > 2 and w.lower() not in STOPWORDS]
        return ' '.join(sorted(set(keywords)))
    
    def get_memories(self, oldest: int = 5, latest: int = 10) -> Dict:
        """Retrieve oldest foundational and latest recent memories."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, content, timestamp, importance, context
                FROM memories
                ORDER BY timestamp ASC, importance DESC
                LIMIT ?
            ''', (oldest,))
            
            oldest_rows = cursor.fetchall()
            
            cursor.execute('''
                SELECT id, content, timestamp, importance, context
                FROM memories
                ORDER BY timestamp DESC, importance DESC
                LIMIT ?
            ''', (latest,))
            
            latest_rows = cursor.fetchall()
            
            conn.close()
            
            def format_row(row):
                return {
                    'id': row[0],
                    'content': row[1],
                    'timestamp': row[2],
                    'importance': row[3],
                    'context': row[4]
                }
            
            oldest_memories = [format_row(r) for r in oldest_rows]
            latest_memories = [format_row(r) for r in latest_rows]
            
            return {
                'oldest': oldest_memories,
                'latest': latest_memories,
                'total_count': len(oldest_memories) + len(latest_memories)
            }
            
        except Exception as e:
            logger.error(f"Error getting memories: {e}")
            return {'oldest': [], 'latest': [], 'total_count': 0, 'error': str(e)}
    
    def set_memory(self, content: str, importance: int = 5) -> Dict:
        """Store a new memory."""
        try:
            if not content or not content.strip():
                return {'success': False, 'error': 'Content cannot be empty'}
            
            importance = max(1, min(10, importance))
            
            keywords = self._extract_keywords(content)
            
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO memories (content, importance, keywords)
                VALUES (?, ?, ?)
            ''', (content.strip(), importance, keywords))
            
            memory_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            logger.info(f"Stored memory ID {memory_id} with importance {importance}")
            
            return {
                'success': True,
                'id': memory_id,
                'importance': importance
            }
            
        except Exception as e:
            logger.error(f"Error setting memory: {e}")
            return {'success': False, 'error': str(e)}
    
    def search_memory(self, query: str, limit: int = 10) -> Dict:
        """Search memories by query string."""
        try:
            if not query or not query.strip():
                return {'success': False, 'results': [], 'error': 'Query cannot be empty'}
            
            query_keywords = self._extract_keywords(query)
            search_terms = query_keywords.split()
            
            if not search_terms:
                return {'success': False, 'results': [], 'error': 'No valid search terms'}
            
            conn = self._get_connection()
            cursor = conn.cursor()
            
            like_conditions = ' OR '.join(['(content LIKE ? OR keywords LIKE ?)' for _ in search_terms])
            like_params = []
            for term in search_terms:
                like_params.extend([f'%{term}%', f'%{term}%'])
            
            query_sql = f'''
                SELECT id, content, timestamp, importance, context
                FROM memories
                WHERE {like_conditions}
                ORDER BY importance DESC, timestamp DESC
                LIMIT ?
            '''
            
            cursor.execute(query_sql, like_params + [limit])
            rows = cursor.fetchall()
            conn.close()
            
            results = []
            for row in rows:
                results.append({
                    'id': row[0],
                    'content': row[1],
                    'preview': row[1][:100] + ('...' if len(row[1]) > 100 else ''),
                    'timestamp': row[2],
                    'importance': row[3],
                    'context': row[4]
                })
            
            logger.info(f"Search for '{query}' found {len(results)} results")
            
            return {
                'success': True,
                'results': results,
                'count': len(results),
                'query': query
            }
            
        except Exception as e:
            logger.error(f"Error searching memory: {e}")
            return {'success': False, 'results': [], 'error': str(e)}