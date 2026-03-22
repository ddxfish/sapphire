# core/chat/function_manager.py

import json
import logging
import time
import os
import importlib
import threading
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
import config
from core.toolsets import toolset_manager

logger = logging.getLogger(__name__)


# Per-context scope isolation — each thread/async-task gets its own values
scope_memory:    ContextVar[str]  = ContextVar('scope_memory',    default='default')
scope_goal:      ContextVar[str]  = ContextVar('scope_goal',      default='default')
scope_knowledge: ContextVar[str]  = ContextVar('scope_knowledge', default='default')
scope_people:    ContextVar[str]  = ContextVar('scope_people',    default='default')
scope_email:     ContextVar[str]  = ContextVar('scope_email',     default='default')
scope_bitcoin:   ContextVar[str]  = ContextVar('scope_bitcoin',   default='default')
scope_gcal:      ContextVar[str]  = ContextVar('scope_gcal',      default='default')
scope_telegram:  ContextVar[str]  = ContextVar('scope_telegram',  default='default')
scope_discord:   ContextVar[str]  = ContextVar('scope_discord',   default='default')
scope_rag:       ContextVar       = ContextVar('scope_rag',       default=None)
scope_private:   ContextVar[bool] = ContextVar('scope_private',   default=False)

# Scope registry — single source of truth for all scope operations.
# Adding a new scope = one ContextVar above + one entry here. That's it.
# 'setting' is the key in chat_settings dict (None = not user-settable via sidebar).
SCOPE_REGISTRY = {
    'memory':    {'var': scope_memory,    'default': 'default', 'setting': 'memory_scope'},
    'goal':      {'var': scope_goal,      'default': 'default', 'setting': 'goal_scope'},
    'knowledge': {'var': scope_knowledge, 'default': 'default', 'setting': 'knowledge_scope'},
    'people':    {'var': scope_people,    'default': 'default', 'setting': 'people_scope'},
    'email':     {'var': scope_email,     'default': 'default', 'setting': 'email_scope'},
    'bitcoin':   {'var': scope_bitcoin,   'default': 'default', 'setting': 'bitcoin_scope'},
    'gcal':      {'var': scope_gcal,      'default': 'default', 'setting': 'gcal_scope'},
    'telegram':  {'var': scope_telegram,  'default': 'default', 'setting': 'telegram_scope'},
    'discord':   {'var': scope_discord,   'default': 'default', 'setting': 'discord_scope'},
    'rag':       {'var': scope_rag,       'default': None,      'setting': None},
    'private':   {'var': scope_private,   'default': False,     'setting': 'private_chat'},
}


def apply_scopes_from_settings(fm, settings: dict):
    """Apply scope values from a chat_settings dict to ContextVars.
    Converts 'none' string to None (disabled). Used by chat.py, chat_streaming.py, api_fastapi.py."""
    for name, reg in SCOPE_REGISTRY.items():
        key = reg['setting']
        if not key:
            continue
        if key in settings:
            val = settings[key]
            # Bool settings (private_chat) — coerce to bool
            if reg['default'] is False or reg['default'] is True:
                val = val not in (False, 0, '', 'false', '0', 'no', 'off', None)
            # String 'none' means disabled
            elif isinstance(val, str) and val == 'none':
                val = None
            elif isinstance(val, str) and val == '':
                val = reg['default']
            reg['var'].set(val)


def reset_scopes():
    """Reset all scopes to defaults."""
    for reg in SCOPE_REGISTRY.values():
        reg['var'].set(reg['default'])


def snapshot_all_scopes() -> dict:
    """Capture all ContextVar scopes as a plain dict."""
    return {name: reg['var'].get() for name, reg in SCOPE_REGISTRY.items()}


def restore_scopes(scopes: dict):
    """Restore scopes from a snapshot dict."""
    for name, reg in SCOPE_REGISTRY.items():
        if name in scopes:
            reg['var'].set(scopes[name])
        else:
            reg['var'].set(reg['default'])


def scope_setting_keys() -> list:
    """Return all setting keys that map to scopes (for defaults/persona reset)."""
    return [reg['setting'] for reg in SCOPE_REGISTRY.values() if reg['setting'] and reg['setting'] != 'private_chat']


class FunctionManager:
    
    def __init__(self):
        self._tools_lock = threading.Lock()
        self.tool_history_file = 'user/history/tools/chat_tool_history.json'
        self.tool_history = []
        self.system_instance = None
        self._load_tool_history()

        # Dynamically load all function modules from functions/
        self.function_modules = {}
        self.execution_map = {}
        self.all_possible_tools = []
        self._enabled_tools = []  # Internal storage (ability-filtered)
        self._mode_filters = {}   # module_name -> MODE_FILTER dict
        self._network_functions = set()  # Function names that require network access
        self._is_local_map = {}  # function_name -> is_local value (True, False, or "endpoint")
        self._function_module_map = {}  # function_name -> module_name (for endpoint lookups)
        
        # Story engine for games/simulations (None = disabled)
        self._story_engine = None
        self._story_engine_enabled = False  # Explicit enabled flag
        self._turn_getter = None  # Callable that returns current turn number
        
        # Track what was REQUESTED, not reverse-engineered
        self.current_toolset_name = "none"
        
        self._load_function_modules()
        
        # Initialize with no tools - user/chat settings will override
        self.update_enabled_functions(['none'])

    def _load_function_modules(self):
        """Dynamically load all function modules from functions/ and user/functions/."""
        base_functions_dir = Path(__file__).parent.parent.parent / "functions"
        base_dir = Path(__file__).parent.parent.parent 

        user_functions = base_dir / "user/functions"
        if user_functions.exists() and any(user_functions.glob("*.py")):
            logger.warning("Deprecated: user/functions/ detected. Migrate to user/plugins/ format (use toolmaker).")

        search_paths = [
            base_functions_dir,
            user_functions,
        ]

        for search_dir in search_paths:
            if not search_dir.exists():
                continue
            
            for py_file in search_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                    
                module_name = py_file.stem
                
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"sapphire.functions.{module_name}", 
                        py_file
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    if not getattr(module, 'ENABLED', True):
                        logger.info(f"Function module '{module_name}' is disabled")
                        continue
                    
                    available_functions = getattr(module, 'AVAILABLE_FUNCTIONS', None)
                    tools = getattr(module, 'TOOLS', [])
                    executor = getattr(module, 'execute', None)
                    mode_filter = getattr(module, 'MODE_FILTER', None)
                    emoji = getattr(module, 'EMOJI', '')

                    if not tools or not executor:
                        logger.warning(f"Module '{module_name}' missing TOOLS or execute()")
                        continue

                    if available_functions is not None:
                        tools = [t for t in tools if t['function']['name'] in available_functions]

                    self.function_modules[module_name] = {
                        'module': module,
                        'tools': tools,
                        'executor': executor,
                        'available_functions': available_functions if available_functions else [t['function']['name'] for t in tools],
                        'emoji': emoji
                    }

                    # Register tool-declared settings
                    tool_settings = getattr(module, 'SETTINGS', None)
                    if tool_settings and isinstance(tool_settings, dict):
                        from core.settings_manager import settings as sm
                        tool_help = getattr(module, 'SETTINGS_HELP', None)
                        sm.register_tool_settings(module_name, tool_settings, tool_help)
                    
                    # Track network functions, is_local (per-tool flags)
                    for tool in tools:
                        func_name = tool['function']['name']
                        if tool.get('network', False):
                            self._network_functions.add(func_name)
                        if 'is_local' in tool:
                            self._is_local_map[func_name] = tool['is_local']
                        self._function_module_map[func_name] = module_name
                    
                    # Store mode filter if present
                    if mode_filter:
                        self._mode_filters[module_name] = mode_filter
                        logger.info(f"Module '{module_name}' has mode filtering: {list(mode_filter.keys())}")
                    
                    # Dedup: warn and skip tools with names already registered
                    existing_names = {t['function']['name'] for t in self.all_possible_tools}
                    for tool in tools:
                        fname = tool['function']['name']
                        if fname in existing_names:
                            logger.warning(f"Duplicate tool name '{fname}' in module '{module_name}' — skipping (already registered by '{self._function_module_map.get(fname, '?')}')")
                        else:
                            self.all_possible_tools.append(tool)
                            existing_names.add(fname)

                    for tool in tools:
                        self.execution_map[tool['function']['name']] = executor
                    
                    logger.info(f"Loaded function module '{module_name}' with {len(tools)} tools")
                    
                except Exception as e:
                    logger.error(f"Failed to load function module '{module_name}': {e}")

    def register_plugin_tools(self, plugin_name: str, plugin_dir, tool_paths: list):
        """Register tools from a plugin directory.

        Args:
            plugin_name: Plugin name for tracking
            plugin_dir: Path to plugin root directory
            tool_paths: List of relative paths to tool files (e.g., ["tools/ha.py"])
        """
        plugin_dir = Path(plugin_dir)

        for tool_rel_path in tool_paths:
            tool_path = plugin_dir / tool_rel_path
            try:
                tool_path.resolve().relative_to(plugin_dir.resolve())
            except ValueError:
                logger.error(f"Plugin '{plugin_name}' path traversal blocked: {tool_rel_path}")
                continue
            if not tool_path.exists():
                logger.warning(f"Plugin '{plugin_name}' tool not found: {tool_path}")
                continue

            module_name = f"plugin_{plugin_name}_{tool_path.stem}"

            try:
                source = tool_path.read_text(encoding="utf-8")
                namespace = {"__file__": str(tool_path), "__name__": module_name}
                exec(compile(source, str(tool_path), "exec"), namespace)

                if not namespace.get('ENABLED', True):
                    logger.info(f"Plugin tool '{module_name}' is disabled")
                    continue

                tools = namespace.get('TOOLS', [])
                executor = namespace.get('execute')

                if not tools or not executor:
                    logger.warning(f"Plugin tool '{tool_path}' missing TOOLS or execute()")
                    continue

                available_functions = namespace.get('AVAILABLE_FUNCTIONS')
                if available_functions:
                    tools = [t for t in tools if t['function']['name'] in available_functions]

                emoji = namespace.get('EMOJI', '')
                mode_filter = namespace.get('MODE_FILTER')

                with self._tools_lock:
                    self.function_modules[module_name] = {
                        'module': None,
                        'tools': tools,
                        'executor': executor,
                        'available_functions': available_functions or [t['function']['name'] for t in tools],
                        'emoji': emoji,
                        '_plugin': plugin_name,
                    }

                    # Plugin settings live in user/webui/plugins/{name}.json only
                    # No register_tool_settings — single settings path, no collisions

                    # Track per-tool flags
                    for tool in tools:
                        func_name = tool['function']['name']
                        if tool.get('network', False):
                            self._network_functions.add(func_name)
                        if 'is_local' in tool:
                            self._is_local_map[func_name] = tool['is_local']
                        self._function_module_map[func_name] = module_name

                    if mode_filter:
                        self._mode_filters[module_name] = mode_filter

                    # Check for function name conflicts — hard error, don't silently corrupt
                    existing_names = {t['function']['name'] for t in self.all_possible_tools}
                    for tool in tools:
                        fname = tool['function']['name']
                        if fname in existing_names:
                            owner = self._function_module_map.get(fname, 'unknown')
                            logger.error(f"\033[91mPlugin '{plugin_name}' tool '{fname}' conflicts with existing tool from '{owner}' — plugin NOT loaded\033[0m")
                            raise ValueError(f"Tool name '{fname}' already registered by '{owner}'")

                    for tool in tools:
                        self.all_possible_tools.append(tool)
                        self.execution_map[tool['function']['name']] = executor

                    # If "all" toolset is active, add new tools to _enabled_tools too
                    if self.current_toolset_name == "all":
                        enabled_names = {t['function']['name'] for t in self._enabled_tools}
                        for tool in tools:
                            if tool['function']['name'] not in enabled_names:
                                self._enabled_tools.append(tool)

                logger.info(f"Plugin '{plugin_name}' tool '{module_name}': {len(tools)} tools registered")

            except Exception as e:
                logger.error(f"Failed to load plugin tool '{tool_path}': {e}", exc_info=True)

    def unregister_plugin_tools(self, plugin_name: str):
        """Remove all tools belonging to a plugin."""
        with self._tools_lock:
            to_remove = [name for name, info in self.function_modules.items()
                         if info.get('_plugin') == plugin_name]

            for module_name in to_remove:
                info = self.function_modules.pop(module_name, None)
                if not info:
                    continue

                func_names = set(info['available_functions'])

                for fname in func_names:
                    self.execution_map.pop(fname, None)
                    self._network_functions.discard(fname)
                    self._is_local_map.pop(fname, None)
                    self._function_module_map.pop(fname, None)

                self.all_possible_tools = [t for t in self.all_possible_tools
                                           if t['function']['name'] not in func_names]
                self._enabled_tools = [t for t in self._enabled_tools
                                       if t['function']['name'] not in func_names]
                self._mode_filters.pop(module_name, None)

        if to_remove:
            logger.info(f"Plugin '{plugin_name}' tools unregistered: {to_remove}")

    def _get_current_prompt_mode(self) -> str:
        """Get current prompt mode for filtering. Returns 'monolith' or 'assembled'."""
        try:
            from core.prompt_state import get_prompt_mode
            return get_prompt_mode()
        except ImportError:
            logger.warning("Could not import get_prompt_mode, defaulting to 'monolith'")
            return "monolith"

    def _apply_mode_filter(self, tools: list) -> list:
        """Filter tools based on current prompt mode."""
        if not self._mode_filters:
            return tools
        
        current_mode = self._get_current_prompt_mode()
        
        # Build set of allowed function names for current mode
        allowed_functions = set()
        for module_name, mode_filter in self._mode_filters.items():
            if current_mode in mode_filter:
                allowed_functions.update(mode_filter[current_mode])
        
        # Also include all functions from modules that don't have mode filtering
        modules_with_filters = set(self._mode_filters.keys())
        for module_name, module_info in self.function_modules.items():
            if module_name not in modules_with_filters:
                allowed_functions.update(module_info['available_functions'])
        
        # Filter tools
        filtered = []
        for tool in tools:
            func_name = tool['function']['name']
            # Check if this function is from a module with mode filtering
            has_mode_filter = any(
                func_name in mf.get(current_mode, []) or func_name in mf.get('monolith', []) + mf.get('assembled', [])
                for mf in self._mode_filters.values()
            )
            
            if has_mode_filter:
                # Only include if allowed for current mode
                if func_name in allowed_functions:
                    filtered.append(tool)
            else:
                # No mode filter for this function's module, include it
                filtered.append(tool)
        
        if len(filtered) != len(tools):
            logger.debug(f"Mode filter ({current_mode}): {len(tools)} -> {len(filtered)} tools")
        
        return filtered

    @property
    def enabled_tools(self) -> list:
        """Get enabled tools filtered by current prompt mode, plus story tools if active."""
        tools = self._apply_mode_filter(self._enabled_tools)

        # Add story tools if story engine is active (both engine AND flag must be set)
        if self._story_engine and self._story_engine_enabled:
            from core.story_engine import TOOLS as STORY_TOOLS
            tools = tools + STORY_TOOLS

            # Add custom story tools from story folder
            custom = self._story_engine.get_story_tools()
            if custom:
                tools = tools + custom

        # Final dedup — Claude API requires unique tool names
        seen = set()
        deduped = []
        for tool in tools:
            name = tool['function']['name']
            if name not in seen:
                seen.add(name)
                deduped.append(tool)
            else:
                logger.warning(f"Duplicate tool '{name}' removed from enabled_tools")
        return deduped

    def update_enabled_functions(self, enabled_names: list):
        """Update enabled tools based on function names from config or ability name."""
        with self._tools_lock:
            # Determine what ability name was requested
            requested_ability = enabled_names[0] if len(enabled_names) == 1 else "custom"

            # Special case: "all" loads every function from every module
            if len(enabled_names) == 1 and enabled_names[0] == "all":
                self.current_toolset_name = "all"
                self._enabled_tools = self.all_possible_tools.copy()
                logger.info(f"Ability 'all' - LOADED ALL {len(self._enabled_tools)} FUNCTIONS")
                return

            # Special case: "none" disables all functions
            if len(enabled_names) == 1 and enabled_names[0] == "none":
                self.current_toolset_name = "none"
                self._enabled_tools = []
                logger.info(f"Ability 'none' - all functions disabled")
                return

            # Check if this is a module ability name
            if len(enabled_names) == 1 and enabled_names[0] in self.function_modules:
                ability_name = enabled_names[0]
                self.current_toolset_name = ability_name
                module_info = self.function_modules[ability_name]
                enabled_names = module_info['available_functions']
                logger.info(f"Ability '{ability_name}' (module) requesting {len(enabled_names)} functions")

            # Check if this is a toolset name
            elif len(enabled_names) == 1 and toolset_manager.toolset_exists(enabled_names[0]):
                toolset_name = enabled_names[0]
                self.current_toolset_name = toolset_name
                enabled_names = toolset_manager.get_toolset_functions(toolset_name)
                logger.info(f"Ability '{toolset_name}' (toolset) requesting {len(enabled_names)} functions")

            # Otherwise treat as direct function name list (custom)
            else:
                self.current_toolset_name = "custom"

            # Store expected count before filtering
            expected_count = len(enabled_names)

            # Filter to only functions that actually exist
            self._enabled_tools = [
                tool for tool in self.all_possible_tools
                if tool['function']['name'] in enabled_names
            ]
        
            actual_names = [tool['function']['name'] for tool in self._enabled_tools]
            missing = set(enabled_names) - set(actual_names)

            if missing:
                logger.warning(f"Toolset '{self.current_toolset_name}' missing functions: {missing}")

            logger.info(f"Toolset '{self.current_toolset_name}': {len(self._enabled_tools)}/{expected_count} functions loaded")
            logger.debug(f"Enabled: {actual_names}")

    def is_valid_toolset(self, ability_name: str) -> bool:
        """Check if a toolset name is valid (exists in toolsets, modules, or is special)."""
        if ability_name in ["all", "none"]:
            return True
        if ability_name in self.function_modules:
            return True
        if toolset_manager.toolset_exists(ability_name):
            return True
        return False
    
    def get_available_toolsets(self) -> list:
        """Get list of all available toolset names."""
        toolsets = ["all", "none"]
        toolsets.extend(list(self.function_modules.keys()))
        toolsets.extend(toolset_manager.get_toolset_names())
        return sorted(set(toolsets))

    def get_enabled_function_names(self):
        """Get list of currently enabled function names (mode-filtered)."""
        return [tool['function']['name'] for tool in self.enabled_tools]

    def has_network_tools_enabled(self) -> bool:
        """Check if any currently enabled tools require network access."""
        enabled_names = set(self.get_enabled_function_names())
        return bool(enabled_names & self._network_functions)

    def get_network_functions(self) -> list:
        """Get list of all functions that require network access."""
        return list(self._network_functions)

    def get_current_toolset_info(self):
        """Get info about current toolset configuration."""
        actual_count = len(self.enabled_tools)  # Uses property, so mode-filtered
        base_count = len(self._enabled_tools)   # Pre-mode-filter count
        expected_count = base_count
        
        if self.current_toolset_name == "all":
            expected_count = len(self.all_possible_tools)
        elif self.current_toolset_name == "none":
            expected_count = 0
        elif self.current_toolset_name in self.function_modules:
            expected_count = len(self.function_modules[self.current_toolset_name]['available_functions'])
        elif toolset_manager.toolset_exists(self.current_toolset_name):
            expected_count = len(toolset_manager.get_toolset_functions(self.current_toolset_name))
        
        mode = self._get_current_prompt_mode()

        # Include story tool info
        story_tool_count = 0
        story_custom_names = []
        if self._story_engine and self._story_engine_enabled:
            from core.story_engine import STORY_TOOL_NAMES
            story_tool_count = len(STORY_TOOL_NAMES)
            story_custom_names = [t['function']['name'] for t in self._story_engine.get_story_tools()]
            story_tool_count += len(story_custom_names)

        return {
            "name": self.current_toolset_name,
            "function_count": actual_count,
            "base_count": base_count,
            "expected_count": expected_count,
            "enabled_functions": self.get_enabled_function_names(),
            "prompt_mode": mode,
            "status": "ok" if base_count == expected_count else "partial",
            "story_tools": story_tool_count,
            "story_custom_tools": story_custom_names,
        }

    # --- Scope methods (thin wrappers around registry functions) ---

    def set_scope(self, name: str, value):
        """Generic scope setter. Use for any registered scope."""
        SCOPE_REGISTRY[name]['var'].set(value)

    def get_scope(self, name: str):
        """Generic scope getter."""
        return SCOPE_REGISTRY[name]['var'].get()

    # Legacy setters/getters — kept for backward compat with direct callers
    def set_memory_scope(self, s): scope_memory.set(s)
    def get_memory_scope(self): return scope_memory.get()
    def set_goal_scope(self, s): scope_goal.set(s)
    def get_goal_scope(self): return scope_goal.get()
    def set_knowledge_scope(self, s): scope_knowledge.set(s)
    def get_knowledge_scope(self): return scope_knowledge.get()
    def set_people_scope(self, s): scope_people.set(s)
    def get_people_scope(self): return scope_people.get()
    def set_email_scope(self, s): scope_email.set(s)
    def get_email_scope(self): return scope_email.get()
    def set_bitcoin_scope(self, s): scope_bitcoin.set(s)
    def get_bitcoin_scope(self): return scope_bitcoin.get()
    def set_gcal_scope(self, s): scope_gcal.set(s)
    def get_gcal_scope(self): return scope_gcal.get()
    def set_rag_scope(self, s): scope_rag.set(s)
    def set_private_chat(self, enabled): scope_private.set(bool(enabled))

    def snapshot_scopes(self) -> dict:
        """Capture current ContextVar scopes as a plain dict."""
        return snapshot_all_scopes()

    def apply_scopes(self, settings: dict):
        """Apply scopes from chat_settings dict."""
        apply_scopes_from_settings(self, settings)

    def set_story_engine(self, engine, turn_getter=None):
        """
        Set story engine for current chat context.

        Args:
            engine: StoryEngine instance, or None to disable
            turn_getter: Callable that returns current turn number
        """
        self._story_engine = engine
        self._story_engine_enabled = engine is not None  # Track enabled state
        self._turn_getter = turn_getter
        if engine:
            logger.info(f"Story engine enabled for chat '{engine.chat_name}'")
        else:
            logger.debug("Story engine disabled")

    def get_story_engine(self):
        """Get current story engine. Returns None if disabled."""
        return self._story_engine


    def _check_privacy_allowed(self, function_name: str) -> tuple:
        """
        Check if function is allowed under current privacy mode.

        Returns:
            (allowed: bool, error_message: str or None)
        """
        from core.privacy import is_privacy_mode, is_allowed_endpoint

        if not is_privacy_mode() and not scope_private.get():
            return True, None

        is_local = self._is_local_map.get(function_name)

        # No is_local flag = assume non-local for safety
        if is_local is None:
            logger.warning(f"Tool '{function_name}' has no is_local flag, blocking in privacy mode")
            return False, f"Tool '{function_name}' is blocked in privacy mode (no locality flag)."

        # Explicitly local tools are always allowed
        if is_local is True:
            return True, None

        # Explicitly non-local tools are blocked
        if is_local is False:
            return False, f"Tool '{function_name}' requires external network access and is blocked in privacy mode. Inform the user that privacy mode is active."

        # Conditional tools ("endpoint") - check their configured endpoint
        if is_local == "endpoint":
            endpoint = self._get_tool_endpoint(function_name)
            if not endpoint:
                logger.warning(f"Tool '{function_name}' has no configured endpoint")
                return False, f"Tool '{function_name}' has no configured endpoint."

            if is_allowed_endpoint(endpoint):
                logger.info(f"Tool '{function_name}' endpoint '{endpoint}' allowed in privacy mode")
                return True, None
            else:
                return False, f"Tool '{function_name}' endpoint '{endpoint}' is not in privacy whitelist. Inform the user."

        # Unknown is_local value - block for safety
        return False, f"Tool '{function_name}' has unknown locality setting."

    def _get_tool_endpoint(self, function_name: str) -> str:
        """Get the configured endpoint URL for conditional tools."""
        return ''

    def _get_plugin_settings_for(self, function_name: str):
        """Get plugin settings for a function, or None if it's not a plugin tool."""
        module_name = self._function_module_map.get(function_name)
        if not module_name:
            return None
        info = self.function_modules.get(module_name)
        if not info or '_plugin' not in info:
            return None
        try:
            from core.plugin_loader import plugin_loader
            return plugin_loader.get_plugin_settings(info['_plugin'])
        except Exception:
            return None

    def execute_function(self, function_name, arguments, scopes=None, allowed_tools=None):
        """Execute a function using the mapped executor.

        scopes: optional dict to re-apply ContextVars before execution.
                 Needed because Starlette's iterate_in_threadpool creates
                 fresh context copies per generator yield.
        allowed_tools: optional set/list of function names that were sent to the LLM.
                       When provided, validates against this snapshot instead of
                       current enabled_tools (prevents race conditions on plugin reload).
        """
        if scopes:
            restore_scopes(scopes)

        start_time = time.time()

        # Validate function was available when sent to LLM (snapshot)
        # or is currently enabled (fallback)
        check_names = set(allowed_tools) if allowed_tools else self.get_enabled_function_names()
        if function_name not in check_names:
            logger.warning(f"Function '{function_name}' called but not enabled. Enabled: {check_names}")
            result = f"Error: The tool '{function_name}' is not currently available."
            self._log_tool_call(function_name, arguments, result, time.time() - start_time, False)
            return result

        # Privacy mode check
        allowed, error_msg = self._check_privacy_allowed(function_name)
        if not allowed:
            logger.info(f"Function '{function_name}' blocked by privacy mode: {error_msg}")
            self._log_tool_call(function_name, arguments, error_msg, time.time() - start_time, False)
            return error_msg

        logger.info(f"Executing function: {function_name}")

        # pre_execute hook — plugins can mutate arguments or skip execution
        from core.hooks import hook_runner, HookEvent
        if hook_runner.has_handlers("pre_execute"):
            exec_event = HookEvent(
                function_name=function_name,
                arguments=dict(arguments) if arguments else {},
                config=config, metadata={"system": self.system_instance}
            )
            hook_runner.fire("pre_execute", exec_event)
            arguments = exec_event.arguments
            if exec_event.skip_llm:
                result = exec_event.result or "Execution skipped by plugin."
                self._log_tool_call(function_name, arguments, result, time.time() - start_time, True)
                return result

        # Execute — 3 paths: story tools, custom story tools, standard
        result = None
        from core.story_engine import STORY_TOOL_NAMES, execute as story_execute

        if function_name in STORY_TOOL_NAMES:
            if not self._story_engine or not self._story_engine_enabled:
                result = f"Error: Story engine not active for tool '{function_name}'"
                self._log_tool_call(function_name, arguments, result, time.time() - start_time, False)
                return result

            turn = self._turn_getter() if self._turn_getter else 0
            try:
                result, success = story_execute(function_name, arguments, self._story_engine, turn)
            except Exception as e:
                logger.error(f"Error executing story tool {function_name}: {e}")
                result = f"Error executing {function_name}: {str(e)}"
                success = False

        elif (self._story_engine and self._story_engine_enabled and
                function_name in self._story_engine.story_tool_names):
            try:
                result, success = self._story_engine.execute_story_tool(function_name, arguments)
            except Exception as e:
                logger.error(f"Error executing custom story tool {function_name}: {e}")
                result = f"Error executing {function_name}: {str(e)}"
                success = False

        else:
            executor = self.execution_map.get(function_name)
            if not executor:
                logger.error(f"No executor found for function '{function_name}'")
                result = f"The tool {function_name} is recognized but has no execution logic."
                self._log_tool_call(function_name, arguments, result, time.time() - start_time, False)
                return result

            try:
                # For plugin tools, inject plugin settings as 4th arg
                plugin_settings = self._get_plugin_settings_for(function_name)
                if plugin_settings is not None:
                    try:
                        result, success = executor(function_name, arguments, config, plugin_settings)
                    except TypeError:
                        # Backward compat: tool doesn't accept 4th arg
                        result, success = executor(function_name, arguments, config)
                else:
                    result, success = executor(function_name, arguments, config)
            except Exception as e:
                logger.error(f"Error executing function {function_name}: {e}")
                result = f"Error executing {function_name}: {str(e)}"
                success = False

        execution_time = time.time() - start_time
        self._log_tool_call(function_name, arguments, result, execution_time, success)

        # post_execute hook — plugins can observe results
        if hook_runner.has_handlers("post_execute"):
            hook_runner.fire("post_execute", HookEvent(
                function_name=function_name, arguments=arguments,
                result=result, config=config
            ))

        return result

    def _load_tool_history(self):
        """Load tool history from disk. Disabled - legacy debug feature."""
        max_entries = getattr(config, 'TOOL_HISTORY_MAX_ENTRIES', 0)
        if max_entries == 0:
            self.tool_history = []
            return
        
        try:
            os.makedirs(os.path.dirname(self.tool_history_file), exist_ok=True)
            if os.path.exists(self.tool_history_file):
                with open(self.tool_history_file, 'r', encoding='utf-8') as f:
                    self.tool_history = json.load(f)
        except Exception as e:
            logger.error(f"Error loading tool history: {e}")
            self.tool_history = []

    def _save_tool_history(self):
        """Save tool history to disk. Disabled - legacy debug feature."""
        max_entries = getattr(config, 'TOOL_HISTORY_MAX_ENTRIES', 0)
        if max_entries == 0:
            return
        
        try:
            os.makedirs(os.path.dirname(self.tool_history_file), exist_ok=True)
            with open(self.tool_history_file, 'w', encoding='utf-8') as f:
                json.dump(self.tool_history, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving tool history: {e}")

    def _log_tool_call(self, function_name, arguments, result, execution_time, success):
        """Log tool call to history. Disabled - legacy debug feature."""
        max_entries = getattr(config, 'TOOL_HISTORY_MAX_ENTRIES', 0)
        if max_entries == 0:
            return
        
        tool_entry = {
            "timestamp": datetime.now().isoformat(),
            "function_name": function_name,
            "arguments": arguments,
            "result": str(result),
            "execution_time_ms": round(execution_time * 1000, 2),
            "success": success
        }
        self.tool_history.append(tool_entry)
        
        if len(self.tool_history) > max_entries:
            self.tool_history = self.tool_history[-max_entries:]
        
        self._save_tool_history()