#!/usr/bin/env python3
# core/modules/memory_engine/memory_engine.py

import os
import sys
import json
import socket
import logging
from pathlib import Path

# Setup path for imports
BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.modules.memory_engine.create_db import initialize_database
from core.modules.memory_engine.db_handler import MemoryDatabase

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [MEMORY] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

SOCKET_PATH = '/tmp/sapphire_memory.sock'
DB_PATH = 'user/memory_engine.db'

class MemoryEngine:
    def __init__(self):
        self.db_path = os.path.join(BASE_DIR, DB_PATH)
        self.socket_path = SOCKET_PATH
        
        if not initialize_database(self.db_path):
            raise RuntimeError("Failed to initialize database")
        
        self.db = MemoryDatabase(self.db_path)
        self.socket = None
    
    def process(self, user_input):
        """CLI interface for memory engine commands.
        
        Allows users to interact with memory via voice/text commands:
        - "memory search <query>" - Search for memories
        - "memory recent" - Show recent memories
        - "memory add <text>" - Add a new memory
        """
        parts = user_input.strip().split(maxsplit=1)
        
        if not parts:
            return "Memory commands: search <query>, recent, add <text>"
        
        command = parts[0].lower()
        
        if command == "search" and len(parts) > 1:
            query = parts[1]
            result = self.db.search_memory(query=query, limit=10)
            if result.get('success'):
                memories = result.get('data', [])
                if memories:
                    return "\n".join([f"- {m['content']}" for m in memories[:5]])
                return "No memories found."
            return f"Search failed: {result.get('error')}"
        
        elif command == "recent":
            result = self.db.get_memories(oldest=0, latest=5)
            if result.get('success'):
                memories = result.get('data', [])
                if memories:
                    return "\n".join([f"- {m['content']}" for m in memories])
                return "No recent memories."
            return f"Failed: {result.get('error')}"
        
        elif command == "add" and len(parts) > 1:
            content = parts[1]
            result = self.db.set_memory(content=content, importance=5)
            if result.get('success'):
                return "Memory saved."
            return f"Failed: {result.get('error')}"
        
        else:
            return "Unknown command. Try: search <query>, recent, or add <text>"
        
    def start(self):
        """Start the Unix socket server."""
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(self.socket_path)
        self.socket.listen(5)
        
        logger.info(f"Memory engine listening on {self.socket_path}")
        
        try:
            while True:
                conn, _ = self.socket.accept()
                try:
                    self.handle_request(conn)
                except Exception as e:
                    logger.error(f"Request handling error: {e}")
                finally:
                    conn.close()
        except KeyboardInterrupt:
            logger.info("Shutting down memory engine")
        finally:
            self.cleanup()
    
    def handle_request(self, conn):
        """Handle incoming request."""
        data = b''
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b'\n\n' in data:
                break
        
        if not data:
            return
        
        try:
            request = json.loads(data.decode('utf-8').strip())
        except json.JSONDecodeError as e:
            response = {'success': False, 'error': f'Invalid JSON: {e}'}
            conn.sendall(json.dumps(response).encode('utf-8'))
            return
        
        action = request.get('action')
        params = request.get('params', {})
        
        if action == 'get':
            result = self.db.get_memories(
                oldest=params.get('oldest', 5),
                latest=params.get('latest', 10)
            )
            response = {'success': True, 'data': result}
            
        elif action == 'set':
            result = self.db.set_memory(
                content=params.get('content', ''),
                importance=params.get('importance', 5)
            )
            response = result
            
        elif action == 'search':
            result = self.db.search_memory(
                query=params.get('query', ''),
                limit=params.get('limit', 10)
            )
            response = result
            
        else:
            response = {'success': False, 'error': f'Unknown action: {action}'}
        
        conn.sendall(json.dumps(response).encode('utf-8'))
    
    def cleanup(self):
        """Cleanup socket on shutdown."""
        if self.socket:
            self.socket.close()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        logger.info("Memory engine stopped")

if __name__ == '__main__':
    try:
        server = MemoryEngine()
        server.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)