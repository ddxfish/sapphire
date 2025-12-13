# functions/memory.py

import socket
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

ENABLED = True
OLDEST_MEMORIES_COUNT = 5
LATEST_MEMORIES_COUNT = 10
SEARCH_RESULTS_LIMIT = 10

SOCKET_PATH = '/tmp/sapphire_memory.sock'

AVAILABLE_FUNCTIONS = [
    'get_memories',
    'set_memory',
    'search_memory',
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memories",
            "description": "Retrieve your oldest foundational memories and latest recent memories about the user",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_memory",
            "description": "Store a new memory for future recall. Rate importance 1-10 (10=critical, 5=useful, 1=minor)",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Memory content"},
                    "importance": {"type": "integer", "description": "Importance 1-10", "default": 5}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search stored memories by keywords",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    }
]

def send_request(action: str, params: dict) -> dict:
    """Send request to memory engine via Unix socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        
        request = {'action': action, 'params': params}
        sock.sendall((json.dumps(request) + '\n\n').encode('utf-8'))
        
        response = b''
        while chunk := sock.recv(4096):
            response += chunk
            if not chunk:
                break
        
        sock.close()
        
        return json.loads(response.decode('utf-8'))
        
    except FileNotFoundError:
        return {'success': False, 'error': 'Memory engine not running'}
    except Exception as e:
        logger.error(f"Memory request error: {e}")
        return {'success': False, 'error': str(e)}

def format_time_ago(timestamp_str: str) -> str:
    """Format timestamp as simple relative time."""
    try:
        ts = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        diff = now - ts
        
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        
        if days > 0:
            return f"{days} day{'s' if days != 1 else ''} ago"
        elif hours > 0:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif minutes > 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            return "just now"
    except:
        return "unknown time"

def format_memory(mem: dict) -> str:
    """Format single memory as: (X days ago) content"""
    time_ago = format_time_ago(mem['timestamp'])
    return f"({time_ago}) {mem['content']}"


def execute(function_name, arguments, config):
    try:
        if function_name == "get_memories":
            response = send_request('get', {
                'oldest': OLDEST_MEMORIES_COUNT,
                'latest': LATEST_MEMORIES_COUNT
            })
            
            if not response.get('success', True):
                return f"Memory retrieval failed: {response.get('error', 'Unknown error')}", False
            
            data = response.get('data', {})
            oldest_memories = data.get('oldest', [])
            latest_memories = data.get('latest', [])
            
            if not oldest_memories and not latest_memories:
                return "No memories stored yet.", True
            
            # Combine and deduplicate by ID
            seen_ids = set()
            all_memories = []
            
            # Add oldest first
            for mem in oldest_memories:
                if mem['id'] not in seen_ids:
                    all_memories.append(mem)
                    seen_ids.add(mem['id'])
            
            # Add latest (skip duplicates)
            for mem in latest_memories:
                if mem['id'] not in seen_ids:
                    all_memories.append(mem)
                    seen_ids.add(mem['id'])
            
            # Format output
            lines = ["Your memories are retrieved:"]
            for mem in all_memories:
                time_ago = format_time_ago(mem['timestamp'])
                lines.append(f"({time_ago}) (Importance: {mem['importance']}) {mem['content']}")
            
            return '\n'.join(lines), True
        
        elif function_name == "set_memory":
            content = arguments.get('content')
            if not content:
                return "Cannot store empty memory.", False
            
            importance = arguments.get('importance', 5)
            
            response = send_request('set', {
                'content': content,
                'importance': importance
            })
            
            if not response.get('success'):
                return f"Failed to store memory: {response.get('error', 'Unknown error')}", False
            
            return f"Memory stored (importance: {importance}/10)", True
        
        elif function_name == "search_memory":
            query = arguments.get('query')
            if not query:
                return "Search query cannot be empty.", False
            
            response = send_request('search', {
                'query': query,
                'limit': SEARCH_RESULTS_LIMIT
            })
            
            if not response.get('success'):
                return f"Search failed: {response.get('error', 'Unknown error')}", False
            
            results = response.get('results', [])
            
            if not results:
                return f"No memories found matching '{query}'.", True
            
            lines = [f"Found {len(results)} memories:"]
            for mem in results:
                lines.append(format_memory(mem))
            
            return '\n'.join(lines), True
        
        return f"Unknown function: {function_name}", False
    
    except Exception as e:
        logger.error(f"{function_name} error: {e}")
        return f"Error executing {function_name}: {str(e)}", False