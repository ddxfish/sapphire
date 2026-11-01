# functions/documents.py
"""
Her way into the files attached to this chat, past what a turn hands her automatically
(core/documents.py). The chat comes from the turn's scope, never from the model, so a
call or a background chat only ever reads its own. Offered by the chat having documents,
not by the toolset: core.documents.fence_tools.
"""
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📎'
TOOL_CATEGORY = 'files'
TOOL_EXPIRES = 3          # user turns: an old read leaves her view

AVAILABLE_FUNCTIONS = ['read_attachment', 'search_attachments']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_attachment",
            "description": "Read a file attached to this chat. Walk it in order by part, or ask a PDF for one page, which also shows that page's pictures (scans, photos). Use this when the excerpts you were given don't have what you need.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "The document's name"},
                    "part": {"type": "integer", "description": "Part to start from, counted from 1"},
                    "count": {"type": "integer", "description": "How many parts, at most 10 (default 3)"},
                    "page": {"type": "integer", "description": "PDF only: this page, counted from 1. Replaces part and count"}
                },
                "required": ["file"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_attachments",
            "description": "Search the files attached to this chat by meaning and by exact words such as names, numbers and error codes. Hits say which part they're from, so read_attachment can continue from there.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for"},
                    "file": {"type": "string", "description": "Only this document (optional)"},
                    "limit": {"type": "integer", "description": "How many hits, at most 20 (default 5)"}
                },
                "required": ["query"]
            }
        }
    },
]


def _int(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def execute(function_name, arguments, config):
    from core import documents
    from core.images import result as image_result
    from core.chat.function_manager import scope_rag

    chat = documents.chat_of(scope_rag.get())
    if not chat:
        return "No documents are attached here.", True
    try:
        if function_name == 'search_attachments':
            query = str(arguments.get('query') or '').strip()
            if not query:
                return "Say what to look for.", False
            return documents.search(chat, query, arguments.get('file'), _int(arguments.get('limit')) or 5), True
        if function_name == 'read_attachment':
            text, pictures = documents.read(chat, arguments.get('file'), _int(arguments.get('part')) or 1,
                                            _int(arguments.get('count')) or 3, _int(arguments.get('page')))
            return (image_result(text, [raw for raw, _ in pictures]) if pictures else text), True
    except Exception as e:
        logger.error(f"[Documents] {function_name} failed: {e}", exc_info=True)
        return f"Couldn't do that: {e}", False
    return f"Unknown function '{function_name}'.", False
