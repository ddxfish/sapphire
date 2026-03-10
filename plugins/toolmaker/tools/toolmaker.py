# Toolmaker — plugin tool
"""
Tool creation tools — lets Sapphire create, read, and activate custom tools.
Custom tools are saved as proper plugins in user/plugins/ and loaded live via rescan.
"""

import ast
import importlib.util
import json
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f6e0\ufe0f'
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_USER_PLUGINS = _PROJECT_ROOT / "user" / "plugins"

# Names that cannot be used for AI-created tools
_RESERVED_NAMES = {
    # Core function modules
    'ai', 'docs', 'goals', 'knowledge', 'memory', 'meta', 'network', 'notepad', 'web',
    # System plugins
    'bitcoin', 'email', 'homeassistant', 'image_gen', 'ssh', 'toolmaker', 'voice_commands', 'stop', 'reset',
    # Core-UI
    'backup', 'continuity', 'setup_wizard',
}


def _get_validation_level():
    """Read validation level from plugin settings. Managed mode forces strict."""
    import os
    if os.environ.get('SAPPHIRE_MANAGED'):
        return 'strict'
    try:
        from core.plugin_loader import plugin_loader
        level = plugin_loader.get_plugin_settings('toolmaker').get('validation', 'strict')
        # Migrate legacy "trust" → "system_killer"
        if level == 'trust':
            level = 'system_killer'
        return level
    except Exception:
        return 'strict'


AVAILABLE_FUNCTIONS = ['tool_load', 'tool_read', 'tool_save']

# Validation uses shared code_validator (single source of truth for blocklists/allowlists)
from core.code_validator import validate_code

# Minimal tool format — embedded in tool_save description so AI always has it
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

# Optional: settings editable in Settings UI (prefix keys with plugin name!)
# SETTINGS = {'MYFUNC_API_KEY': ''}
# SETTINGS_HELP = {'MYFUNC_API_KEY': 'Your API key'}

def execute(function_name, arguments, config, plugin_settings=None):
    # config = global Sapphire config (system settings)
    # plugin_settings = dict of THIS plugin's settings from the Settings UI
    if function_name == 'my_func':
        query = arguments.get('query', '')
        # Read plugin settings: plugin_settings.get('MYFUNC_API_KEY', '')
        return f"Result: {query}", True
    return f"Unknown function: {function_name}", False"""

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "tool_load",
            "description": "Activate newly saved tools. Discovers and loads the plugin live — no restart needed.",
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
            "description": "Read a custom tool's source code. Call without name to list all AI-created plugins.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (without .py). Omit to list all AI-created plugins."
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
            "description": f"Create or update a custom tool plugin. Validates code (AST + smoke test) before saving as a plugin. Overwrites if exists. After saving, call tool_load to activate live.\n\nFor settings, multi-function tools, and advanced features: call search_help_docs(\"TOOLMAKER\")\n\nMinimal format:\n{_TOOL_FORMAT}",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name — alphanumeric and underscores only, no .py"
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
    """Validate code AST. Delegates to shared code_validator."""
    return validate_code(code, strictness)


def _smoke_test(filepath):
    """Import module and validate structure. Returns (ok, error_msg).

    Note: This executes module-level code, but only AFTER AST validation has
    blocked all dangerous imports/calls/attrs. In managed mode the AST gate is
    even stricter (no os.getenv, io, pathlib, filesystem ops). What remains
    is data declarations (TOOLS, ENABLED) and safe imports (json, requests).
    """
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

    return True, module


def _settings_to_schema(settings_dict, help_dict=None):
    """Convert SETTINGS dict to manifest settings schema."""
    schema = []
    for key, default in settings_dict.items():
        field = {"key": key, "label": key.replace("_", " ").title(), "default": default}
        if isinstance(default, bool):
            field["type"] = "boolean"
        elif isinstance(default, (int, float)):
            field["type"] = "number"
        else:
            field["type"] = "string"
        if help_dict and key in help_dict:
            field["help"] = help_dict[key]
        schema.append(field)
    return schema


def _generate_manifest(name, module, code):
    """Generate plugin.json manifest from validated module."""
    # Title from plugin name: weather_lookup → Weather Lookup
    title = name.replace('_', ' ').title()

    # Description from first tool's description (first sentence, capped)
    tool_desc = ''
    if module.TOOLS:
        func = module.TOOLS[0].get('function', {})
        tool_desc = func.get('description', '').split('.')[0].strip()[:80]

    # Convention: "Title — short description" (API splits on — for display title)
    description = f"{title} — {tool_desc}" if tool_desc else title

    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": description,
        "author": "ai-toolmaker",
        "default_enabled": True,
        "capabilities": {
            "tools": [f"tools/{name}.py"]
        }
    }

    # Pull emoji from module if defined
    emoji = getattr(module, 'EMOJI', None)
    if emoji and isinstance(emoji, str):
        manifest["icon"] = emoji

    # Convert SETTINGS dict to manifest schema
    settings_dict = getattr(module, 'SETTINGS', None)
    if isinstance(settings_dict, dict) and settings_dict:
        help_dict = getattr(module, 'SETTINGS_HELP', None)
        manifest["capabilities"]["settings"] = _settings_to_schema(
            settings_dict, help_dict if isinstance(help_dict, dict) else None
        )

    return manifest


def _list_user_plugins():
    """List AI-created plugins in user/plugins/."""
    if not _USER_PLUGINS.exists():
        return "No AI-created plugins found."
    plugins = []
    for child in sorted(_USER_PLUGINS.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "plugin.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            tool_names = []
            tools_dir = child / "tools"
            if tools_dir.exists():
                for py in tools_dir.glob("*.py"):
                    if not py.name.startswith("_"):
                        tool_names.append(py.stem)
            plugins.append(f"  {child.name} ({', '.join(tool_names) or 'no tools'})")
        except Exception:
            plugins.append(f"  {child.name} (broken manifest)")
    if not plugins:
        return "No AI-created plugins found."
    return "AI-created plugins:\n" + "\n".join(plugins)


def _sanitize_name(name):
    """Sanitize plugin name. Returns None if invalid."""
    name = name.strip().lower().replace('.py', '').replace('-', '_')
    if not name or not all(c.isalnum() or c == '_' for c in name):
        return None
    if name.startswith('_'):
        return None
    # Block reserved names
    if name in _RESERVED_NAMES:
        return None
    # Block overwriting core tools
    core_dir = _PROJECT_ROOT / "functions"
    if (core_dir / f"{name}.py").exists():
        return None
    # Block overwriting system plugins
    if ((_PROJECT_ROOT / "plugins" / name)).exists():
        return None
    return name


def execute(function_name, arguments, config):
    try:
        if function_name == 'tool_save':
            name = _sanitize_name(arguments.get('name', ''))
            if not name:
                return "Invalid or reserved name. Use alphanumeric/underscores, cannot match core tools or system plugins.", False

            code = arguments.get('code', '')
            if not code.strip():
                return "No code provided.", False

            strictness = _get_validation_level()

            ok, err = _validate_ast(code, strictness)
            if not ok:
                return f"Validation failed ({strictness} mode): {err}", False

            # Create plugin directory structure
            plugin_dir = _USER_PLUGINS / name
            tools_dir = plugin_dir / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            filepath = tools_dir / f"{name}.py"
            filepath.write_text(code, encoding='utf-8')

            # Smoke test
            ok, result = _smoke_test(filepath)
            if not ok:
                shutil.rmtree(plugin_dir, ignore_errors=True)
                return f"Smoke test failed: {result}\nPlugin directory removed — fix and retry.", False

            # Generate and write manifest
            manifest = _generate_manifest(name, result, code)
            manifest_path = plugin_dir / "plugin.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

            plugin_list = _list_user_plugins()
            return f"Plugin '{name}' saved and validated.\n{plugin_list}\nNow call tool_load to activate.", True

        elif function_name == 'tool_read':
            name = arguments.get('name')
            if not name:
                return _list_user_plugins(), True

            clean = _sanitize_name(name)
            if not clean:
                return f"Invalid tool name: '{name}'. Use alphanumeric and underscores only.", False
            # Check user plugins first
            plugin_tool = _USER_PLUGINS / clean / "tools" / f"{clean}.py"
            if plugin_tool.exists():
                code = plugin_tool.read_text(encoding='utf-8')
                return f"=== {clean} (user plugin) ===\n{code}", True

            # Check legacy user/functions/ for backward compat
            legacy = _PROJECT_ROOT / "user" / "functions" / f"{clean}.py"
            if legacy.exists():
                code = legacy.read_text(encoding='utf-8')
                return f"=== {clean} (legacy user/functions/) ===\n{code}", True

            return f"Tool '{clean}' not found.\n{_list_user_plugins()}", False

        elif function_name == 'tool_load':
            try:
                from core.plugin_loader import plugin_loader
                result = plugin_loader.rescan()
                added = result.get("added", [])

                # Filter added list to only plugins that actually loaded
                actually_loaded = []
                failed_to_load = []
                for name in added:
                    info = plugin_loader.get_plugin_info(name)
                    if info and info.get("loaded"):
                        actually_loaded.append(name)
                    else:
                        failed_to_load.append(name)

                # Reload any already-loaded user plugins (handles tool updates)
                reloaded = []
                if _USER_PLUGINS.exists():
                    for child in _USER_PLUGINS.iterdir():
                        if not child.is_dir():
                            continue
                        name = child.name
                        info = plugin_loader.get_plugin_info(name)
                        if info and info.get("loaded") and name not in added:
                            plugin_loader.reload_plugin(name)
                            reloaded.append(name)

                # Re-sync toolset so new/updated tools are available
                try:
                    from core.api_fastapi import get_system
                    system = get_system()
                    if system and hasattr(system, 'llm_chat'):
                        toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
                        toolset_name = toolset_info.get("name", "custom")
                        system.llm_chat.function_manager.update_enabled_functions([toolset_name])
                        # Notify frontend so toolset count refreshes
                        from core.event_bus import publish, Events
                        publish(Events.TOOLSET_CHANGED, {"name": toolset_name})
                except Exception:
                    pass

                parts = []
                if actually_loaded:
                    parts.append(f"Loaded {len(actually_loaded)} new: {', '.join(actually_loaded)}")
                if reloaded:
                    parts.append(f"Reloaded {len(reloaded)} updated: {', '.join(reloaded)}")
                if failed_to_load:
                    parts.append(f"FAILED to load {len(failed_to_load)}: {', '.join(failed_to_load)} (check plugin signing/sideloading)")
                if parts:
                    return f"{'. '.join(parts)}. Tools are now available.", not failed_to_load
                else:
                    return "Rescan complete — no changes detected.", True
            except Exception as e:
                return f"Load failed: {e}", False

        return f"Unknown function: {function_name}", False

    except Exception as e:
        logger.error(f"Toolmaker error in {function_name}: {e}", exc_info=True)
        return f"Error: {str(e)}", False
