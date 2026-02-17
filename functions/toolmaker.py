# functions/toolmaker.py
"""
Tool creation tools — lets Sapphire create, read, and activate custom tools.
Custom tools are saved to user/functions/ and loaded on next restart.
"""

import ast
import importlib.util
import logging
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🛠️'

_USER_FUNCTIONS = Path(__file__).parent.parent / "user" / "functions"

AVAILABLE_FUNCTIONS = ['tool_activate', 'tool_read', 'tool_save']

# --- Validation config ---

_BLOCKED_IMPORTS = {
    'subprocess', 'shutil', 'ctypes', 'multiprocessing',
    'socket', 'signal', 'importlib',
}

_BLOCKED_CALLS = {'eval', 'exec', '__import__', 'compile', 'globals', 'locals'}

_BLOCKED_ATTRS = {
    ('os', 'system'), ('os', 'popen'), ('os', 'exec'), ('os', 'execv'),
    ('os', 'execvp'), ('os', 'execvpe'), ('os', 'spawn'), ('os', 'spawnl'),
    ('os', 'remove'), ('os', 'unlink'), ('os', 'rmdir'), ('os', 'removedirs'),
    ('os', 'rename'), ('os', 'kill'), ('os', 'environ'),
}

_ALLOWED_STRICT = {
    'json', 're', 'datetime', 'math', 'collections', 'itertools',
    'functools', 'hashlib', 'hmac', 'base64', 'urllib', 'requests',
    'time', 'random', 'string', 'textwrap', 'pathlib', 'logging',
    'typing', 'enum', 'dataclasses', 'copy', 'os', 'io',
}

# Example tool for the AI's reference (embedded in tool description)
_TOOL_FORMAT = """ENABLED = True
AVAILABLE_FUNCTIONS = ['my_func']
TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "my_func",
            "description": "What this tool does and when to use it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The input"}
                },
                "required": ["query"]
            }
        }
    }
]

def execute(function_name, arguments, config):
    if function_name == 'my_func':
        query = arguments.get('query', '')
        return f"Result: {query}", True
    return f"Unknown function: {function_name}", False"""

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tool_activate",
            "description": "Restart the app to load new/modified custom tools. This ends the current conversation — save progress to goals first.",
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
            "name": "tool_read",
            "description": "Read a custom tool's source code. Call without name to list all custom tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Tool module name (without .py). Omit to list all custom tools."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tool_save",
            "description": f"Create or update a custom tool module. Validates code (AST + smoke test) before saving to user/functions/. Overwrites if exists. After saving, call tool_activate to load.\n\nRequired format:\n{_TOOL_FORMAT}",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Module name — alphanumeric and underscores only, no .py"
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python source code for the tool module"
                    }
                },
                "required": ["name", "code"]
            }
        }
    },
]


# === Validation ===

def _validate_ast(code, strictness):
    """Validate code AST. Returns (ok, error_msg)."""
    if strictness == 'trust':
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"Syntax error: {e}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split('.')[0]
                if mod in _BLOCKED_IMPORTS:
                    return False, f"Blocked import: {mod}"
                if strictness == 'strict' and mod not in _ALLOWED_STRICT:
                    return False, f"Import '{mod}' not in allowlist (strict mode)"

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split('.')[0]
                if mod in _BLOCKED_IMPORTS:
                    return False, f"Blocked import: {mod}"
                if strictness == 'strict' and mod not in _ALLOWED_STRICT:
                    return False, f"Import '{mod}' not in allowlist (strict mode)"

        # Check dangerous calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
                return False, f"Blocked call: {node.func.id}()"

        # Check dangerous attribute access
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in _BLOCKED_ATTRS:
                return False, f"Blocked: {node.value.id}.{node.attr}"

    return True, ""


def _smoke_test(filepath):
    """Import module and validate structure. Returns (ok, error_msg)."""
    module_name = f"_toolmaker_smoke_{filepath.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        return False, f"Import failed: {e}"
    finally:
        sys.modules.pop(module_name, None)

    # Required exports
    if not isinstance(getattr(module, 'TOOLS', None), list):
        return False, "Missing or invalid TOOLS list"
    if not isinstance(getattr(module, 'AVAILABLE_FUNCTIONS', None), list):
        return False, "Missing or invalid AVAILABLE_FUNCTIONS list"
    if not callable(getattr(module, 'execute', None)):
        return False, "Missing execute() function"

    # Validate each tool schema
    for tool in module.TOOLS:
        if not isinstance(tool, dict) or 'function' not in tool:
            return False, "TOOLS entry missing 'function' key"
        func = tool['function']
        if 'name' not in func:
            return False, "Tool function missing 'name'"
        if 'description' not in func:
            return False, "Tool function missing 'description'"
        if func['name'] not in module.AVAILABLE_FUNCTIONS:
            return False, f"Tool '{func['name']}' not in AVAILABLE_FUNCTIONS"

    if not module.TOOLS:
        return False, "TOOLS list is empty"

    return True, ""


def _list_custom_tools():
    """List custom tools in user/functions/."""
    _USER_FUNCTIONS.mkdir(parents=True, exist_ok=True)
    tools = sorted(
        f.stem for f in _USER_FUNCTIONS.glob("*.py")
        if not f.name.startswith("_")
    )
    if not tools:
        return "No custom tools found."
    return "Custom tools: " + ", ".join(tools)


def _sanitize_name(name):
    """Sanitize module name. Returns None if invalid."""
    name = name.strip().lower().replace('.py', '')
    if not name or not all(c.isalnum() or c == '_' for c in name):
        return None
    if name.startswith('_'):
        return None
    # Block overwriting core tools
    core_dir = Path(__file__).parent
    if (core_dir / f"{name}.py").exists():
        return None
    return name


def execute(function_name, arguments, config):
    try:
        if function_name == 'tool_save':
            name = _sanitize_name(arguments.get('name', ''))
            if not name:
                return "Invalid or reserved name. Use alphanumeric/underscores, cannot match core tool names.", False

            code = arguments.get('code', '')
            if not code.strip():
                return "No code provided.", False

            strictness = getattr(config, 'TOOL_MAKER_VALIDATION', 'moderate')

            ok, err = _validate_ast(code, strictness)
            if not ok:
                return f"Validation failed ({strictness} mode): {err}", False

            # Write file
            _USER_FUNCTIONS.mkdir(parents=True, exist_ok=True)
            filepath = _USER_FUNCTIONS / f"{name}.py"
            filepath.write_text(code, encoding='utf-8')

            # Smoke test
            ok, err = _smoke_test(filepath)
            if not ok:
                filepath.unlink(missing_ok=True)
                return f"Smoke test failed: {err}\nFile removed — fix and retry.", False

            tool_list = _list_custom_tools()
            return f"Tool '{name}' saved and validated.\n{tool_list}\nCall tool_activate to load.", True

        elif function_name == 'tool_read':
            name = arguments.get('name')
            if not name:
                return _list_custom_tools(), True

            clean = name.strip().lower().replace('.py', '')
            filepath = _USER_FUNCTIONS / f"{clean}.py"
            if not filepath.exists():
                return f"Tool '{clean}' not found.\n{_list_custom_tools()}", False

            code = filepath.read_text(encoding='utf-8')
            return f"=== {clean}.py ===\n{code}", True

        elif function_name == 'tool_activate':
            def _delayed_restart():
                time.sleep(5)
                from core.api_fastapi import _restart_callback
                if _restart_callback:
                    _restart_callback()
                else:
                    logger.error("No restart callback available")
            threading.Thread(target=_delayed_restart, daemon=True).start()
            return "Restart will trigger in ~5 seconds — tools reload on startup.", True

        return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"Toolmaker error in {function_name}: {e}", exc_info=True)
        return f"Error: {str(e)}", False
