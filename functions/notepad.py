# functions/notepad.py
"""
Notepad tool for AI scratch notes.
Stores in user/notepad/notepad.txt with line-numbered CRUD.
"""

import os
import logging
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📝'
NOTEPAD_PATH = Path("user/notepad/notepad.txt")
_notepad_lock = threading.Lock()

AVAILABLE_FUNCTIONS = [
    'notepad_read',
    'notepad_append_lines',
    'notepad_delete_lines',
    'notepad_insert_line',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "notepad_read",
            "description": "Read your scratch notepad. Returns all lines with line numbers for reference.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "notepad_append_lines",
            "description": "Append lines to the notepad.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lines to append"
                    }
                },
                "required": ["lines"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "notepad_delete_lines",
            "description": "Delete lines by number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "1-indexed line numbers"
                    }
                },
                "required": ["line_numbers"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "notepad_insert_line",
            "description": "Insert a line after a line number. 0 = beginning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "after_line": {
                        "type": "integer",
                        "description": "Line to insert after (0 = beginning)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Line content"
                    }
                },
                "required": ["after_line", "content"]
            }
        }
    }
]


def _ensure_notepad():
    """Create notepad file and directory if they don't exist."""
    NOTEPAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not NOTEPAD_PATH.exists():
        # Explicit utf-8: Windows defaults to cp1252 which silently corrupts
        # any unicode the user writes to their notepad. 2026-04-24.
        NOTEPAD_PATH.write_text("", encoding='utf-8')
    return NOTEPAD_PATH


def _read_lines():
    """Read notepad lines, stripping trailing newlines."""
    path = _ensure_notepad()
    content = path.read_text(encoding='utf-8')
    if not content:
        return []
    return content.splitlines()


def _write_lines(lines):
    """Write lines to notepad (atomic via tmp+rename)."""
    path = _ensure_notepad()
    content = '\n'.join(lines) + '\n' if lines else ''
    tmp = path.with_suffix('.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)


def _format_notepad(lines):
    """Format notepad with line numbers for AI display."""
    if not lines:
        return "(notepad is empty)"
    
    formatted = []
    for i, line in enumerate(lines, 1):
        formatted.append(f"[{i}] {line}")
    return '\n'.join(formatted)


def execute(function_name, arguments, config):
    """Execute notepad functions. Lock ensures parallel tool calls don't corrupt."""
    with _notepad_lock:
      return _execute_inner(function_name, arguments, config)


def _execute_inner(function_name, arguments, config):
    try:
        if function_name == "notepad_read":
            lines = _read_lines()
            return _format_notepad(lines), True

        elif function_name == "notepad_append_lines":
            new_lines = arguments.get('lines', [])
            if isinstance(new_lines, str):
                new_lines = [new_lines]
            if not new_lines:
                return "No lines provided to append.", False
            
            lines = _read_lines()
            lines.extend(new_lines)
            _write_lines(lines)
            
            count = len(new_lines)
            total = len(lines)
            return f"Appended {count} line(s). Notepad now has {total} lines.", True

        elif function_name == "notepad_delete_lines":
            line_numbers = arguments.get('line_numbers', [])
            if not line_numbers:
                return "No line numbers provided.", False
            
            lines = _read_lines()
            if not lines:
                return "Notepad is empty, nothing to delete.", False
            
            # Validate line numbers
            invalid = [n for n in line_numbers if n < 1 or n > len(lines)]
            if invalid:
                return f"Invalid line numbers: {invalid}. Valid range: 1-{len(lines)}", False
            
            # Delete in reverse order to preserve indices
            for n in sorted(line_numbers, reverse=True):
                del lines[n - 1]
            
            _write_lines(lines)
            
            deleted = len(line_numbers)
            remaining = len(lines)
            return f"Deleted {deleted} line(s). {remaining} lines remaining.", True

        elif function_name == "notepad_insert_line":
            after_line = arguments.get('after_line')
            content = arguments.get('content', '')
            
            if after_line is None:
                return "after_line is required.", False
            
            lines = _read_lines()
            max_line = len(lines)
            
            if after_line < 0 or after_line > max_line:
                return f"Invalid after_line: {after_line}. Valid range: 0-{max_line}", False
            
            lines.insert(after_line, content)
            _write_lines(lines)
            
            new_line_num = after_line + 1
            total = len(lines)
            return f"Inserted at line {new_line_num}. Notepad now has {total} lines.", True

        else:
            return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"Notepad error in {function_name}: {e}", exc_info=True)
        return f"Error: {str(e)}", False