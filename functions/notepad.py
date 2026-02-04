# functions/notepad.py
"""
Notepad tool for AI scratch notes.
Stores in user/notepad/notepad.txt with line-numbered CRUD.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
NOTEPAD_PATH = Path("user/notepad/notepad.txt")

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
            "description": "Append one or more lines to the end of your notepad.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lines to append to the notepad"
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
            "description": "Delete specific lines by their line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Line numbers to delete (1-indexed)"
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
            "description": "Insert a line after a specific line number. Use 0 to insert at the beginning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "after_line": {
                        "type": "integer",
                        "description": "Line number to insert after (0 = beginning, 1 = after first line)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to insert"
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
        NOTEPAD_PATH.write_text("")
    return NOTEPAD_PATH


def _read_lines():
    """Read notepad lines, stripping trailing newlines."""
    path = _ensure_notepad()
    content = path.read_text()
    if not content:
        return []
    return content.splitlines()


def _write_lines(lines):
    """Write lines to notepad."""
    path = _ensure_notepad()
    path.write_text('\n'.join(lines) + '\n' if lines else '')


def _format_notepad(lines):
    """Format notepad with line numbers for AI display."""
    if not lines:
        return "(notepad is empty)"
    
    formatted = []
    for i, line in enumerate(lines, 1):
        formatted.append(f"[{i}] {line}")
    return '\n'.join(formatted)


def execute(function_name, arguments, config):
    """Execute notepad functions."""
    try:
        if function_name == "notepad_read":
            lines = _read_lines()
            return _format_notepad(lines), True

        elif function_name == "notepad_append_lines":
            new_lines = arguments.get('lines', [])
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