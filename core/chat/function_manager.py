# core/chat/function_manager.py

import json
import logging
import time
import os
import importlib
from datetime import datetime
from pathlib import Path
import config
from core.modules.system.toolsets import toolset_manager

logger = logging.getLogger(__name__)

class FunctionManager:
    # Class-level memory scope - accessible from memory module
    _current_memory_scope = 'default'
    
    def __init__(self):
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
        
        # Memory scope for current execution context (None = disabled)
        self._memory_scope = 'default'
        
        # State engine for games/simulations (None = disabled)
        self._state_engine = None
        self._state_engine_enabled = False  # Explicit enabled flag
        self._turn_getter = None  # Callable that returns current turn number
        
        # Track what was REQUESTED, not reverse-engineered
        self.current_ability_name = "none"
        
        self._load_function_modules()
        
        # Initialize with no tools - user/chat settings will override
        self.update_enabled_functions(['none'])

    def _load_function_modules(self):
        """Dynamically load all function modules from functions/ and user/functions/."""
        if not config.FUNCTIONS_ENABLED:
            logger.info("Function loading disabled by config")
            return
        
        base_functions_dir = Path(__file__).parent.parent.parent / "functions"
        base_dir = Path(__file__).parent.parent.parent 

        search_paths = [
            base_functions_dir,
            base_dir / "user/functions",
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
                    
                    if not tools or not executor:
                        logger.warning(f"Module '{module_name}' missing TOOLS or execute()")
                        continue
                    
                    if available_functions is not None:
                        tools = [t for t in tools if t['function']['name'] in available_functions]
                    
                    self.function_modules[module_name] = {
                        'module': module,
                        'tools': tools,
                        'executor': executor,
                        'available_functions': available_functions if available_functions else [t['function']['name'] for t in tools]
                    }
                    
                    # Track network functions and is_local (per-tool flags)
                    for tool in tools:
                        func_name = tool['function']['name']
                        if tool.get('network', False):
                            self._network_functions.add(func_name)
                        # Track is_local flag (True, False, or "endpoint" for conditional)
                        if 'is_local' in tool:
                            self._is_local_map[func_name] = tool['is_local']
                        self._function_module_map[func_name] = module_name
                    
                    # Store mode filter if present
                    if mode_filter:
                        self._mode_filters[module_name] = mode_filter
                        logger.info(f"Module '{module_name}' has mode filtering: {list(mode_filter.keys())}")
                    
                    self.all_possible_tools.extend(tools)
                    
                    for tool in tools:
                        self.execution_map[tool['function']['name']] = executor
                    
                    logger.info(f"Loaded function module '{module_name}' with {len(tools)} tools")
                    
                except Exception as e:
                    logger.error(f"Failed to load function module '{module_name}': {e}")

    def _get_current_prompt_mode(self) -> str:
        """Get current prompt mode for filtering. Returns 'monolith' or 'assembled'."""
        try:
            from core.modules.system.prompt_state import get_prompt_mode
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
        """Get enabled tools filtered by current prompt mode, plus state tools if active."""
        tools = self._apply_mode_filter(self._enabled_tools)
        
        # Add state tools if state engine is active (both engine AND flag must be set)
        if self._state_engine and self._state_engine_enabled:
            from core.state_engine import TOOLS as STATE_TOOLS
            # Only include move tool if navigation is configured
            has_navigation = (self._state_engine.navigation is not None and 
                              self._state_engine.navigation.is_enabled)
            if has_navigation:
                tools = tools + STATE_TOOLS
            else:
                # Exclude move tool for non-navigation presets
                tools = tools + [t for t in STATE_TOOLS if t['function']['name'] != 'move']
        
        return tools

    def update_enabled_functions(self, enabled_names: list):
        """Update enabled tools based on function names from config or ability name."""
        
        # Determine what ability name was requested
        requested_ability = enabled_names[0] if len(enabled_names) == 1 else "custom"
        
        # Special case: "all" loads every function from every module
        if len(enabled_names) == 1 and enabled_names[0] == "all":
            self.current_ability_name = "all"
            self._enabled_tools = self.all_possible_tools.copy()
            logger.info(f"Ability 'all' - LOADED ALL {len(self._enabled_tools)} FUNCTIONS")
            return
        
        # Special case: "none" disables all functions
        if len(enabled_names) == 1 and enabled_names[0] == "none":
            self.current_ability_name = "none"
            self._enabled_tools = []
            logger.info(f"Ability 'none' - all functions disabled")
            return
        
        # Check if this is a module ability name
        if len(enabled_names) == 1 and enabled_names[0] in self.function_modules:
            ability_name = enabled_names[0]
            self.current_ability_name = ability_name
            module_info = self.function_modules[ability_name]
            enabled_names = module_info['available_functions']
            logger.info(f"Ability '{ability_name}' (module) requesting {len(enabled_names)} functions")
        
        # Check if this is a toolset name
        elif len(enabled_names) == 1 and toolset_manager.toolset_exists(enabled_names[0]):
            toolset_name = enabled_names[0]
            self.current_ability_name = toolset_name
            enabled_names = toolset_manager.get_toolset_functions(toolset_name)
            logger.info(f"Ability '{toolset_name}' (toolset) requesting {len(enabled_names)} functions")
        
        # Otherwise treat as direct function name list (custom)
        else:
            self.current_ability_name = "custom"
        
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
            logger.warning(f"Ability '{self.current_ability_name}' missing functions: {missing}")
        
        logger.info(f"Ability '{self.current_ability_name}': {len(self._enabled_tools)}/{expected_count} functions loaded")
        logger.debug(f"Enabled: {actual_names}")

    def is_valid_ability(self, ability_name: str) -> bool:
        """Check if an ability name is valid (exists in toolsets, modules, or is special)."""
        if ability_name in ["all", "none"]:
            return True
        if ability_name in self.function_modules:
            return True
        if toolset_manager.toolset_exists(ability_name):
            return True
        return False
    
    def get_available_abilities(self) -> list:
        """Get list of all available ability names."""
        abilities = ["all", "none"]
        abilities.extend(list(self.function_modules.keys()))
        abilities.extend(toolset_manager.get_toolset_names())
        return sorted(set(abilities))

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

    def get_current_ability_info(self):
        """Get info about current ability configuration."""
        actual_count = len(self.enabled_tools)  # Uses property, so mode-filtered
        base_count = len(self._enabled_tools)   # Pre-mode-filter count
        expected_count = base_count
        
        if self.current_ability_name == "all":
            expected_count = len(self.all_possible_tools)
        elif self.current_ability_name == "none":
            expected_count = 0
        elif self.current_ability_name in self.function_modules:
            expected_count = len(self.function_modules[self.current_ability_name]['available_functions'])
        elif toolset_manager.toolset_exists(self.current_ability_name):
            expected_count = len(toolset_manager.get_toolset_functions(self.current_ability_name))
        
        mode = self._get_current_prompt_mode()
        
        return {
            "name": self.current_ability_name,
            "function_count": actual_count,
            "base_count": base_count,
            "expected_count": expected_count,
            "prompt_mode": mode,
            "status": "ok" if base_count == expected_count else "partial"
        }

    def set_memory_scope(self, scope: str):
        """Set memory scope for current execution context. None = disabled."""
        self._memory_scope = scope
        FunctionManager._current_memory_scope = scope  # Update class-level for cross-module access
        logger.debug(f"Memory scope set to: {scope}")

    def get_memory_scope(self) -> str:
        """Get current memory scope. Returns None if memory disabled."""
        return self._memory_scope

    def set_state_engine(self, engine, turn_getter=None):
        """
        Set state engine for current chat context.
        
        Args:
            engine: StateEngine instance, or None to disable
            turn_getter: Callable that returns current turn number
        """
        self._state_engine = engine
        self._state_engine_enabled = engine is not None  # Track enabled state
        self._turn_getter = turn_getter
        if engine:
            logger.info(f"State engine enabled for chat '{engine.chat_name}'")
        else:
            logger.debug("State engine disabled")

    def get_state_engine(self):
        """Get current state engine. Returns None if disabled."""
        return self._state_engine

    def _check_privacy_allowed(self, function_name: str) -> tuple:
        """
        Check if function is allowed under current privacy mode.

        Returns:
            (allowed: bool, error_message: str or None)
        """
        from core.privacy import is_privacy_mode, is_allowed_endpoint

        if not is_privacy_mode():
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
        module_name = self._function_module_map.get(function_name, '')

        if module_name == 'image':
            # Image generation endpoint from plugin settings
            import json
            settings_path = Path(config.BASE_DIR) / 'user' / 'webui' / 'plugins' / 'image-gen.json'
            try:
                if settings_path.exists():
                    with open(settings_path) as f:
                        return json.load(f).get('api_url', 'http://localhost:5153')
                return 'http://localhost:5153'  # Default
            except Exception:
                return 'http://localhost:5153'

        elif module_name == 'homeassistant':
            # Home Assistant endpoint from plugin settings
            import json
            settings_path = Path(config.BASE_DIR) / 'user' / 'webui' / 'plugins' / 'homeassistant.json'
            try:
                if settings_path.exists():
                    with open(settings_path) as f:
                        return json.load(f).get('url', 'http://homeassistant.local:8123')
                return 'http://homeassistant.local:8123'  # Default
            except Exception:
                return 'http://homeassistant.local:8123'

        return ''

    def execute_function(self, function_name, arguments):
        """Execute a function using the mapped executor."""
        start_time = time.time()

        # Validate function is currently enabled
        enabled_names = self.get_enabled_function_names()
        if function_name not in enabled_names:
            logger.warning(f"Function '{function_name}' called but not enabled. Enabled: {enabled_names}")
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
        
        # Check if this is a state tool
        from core.state_engine import STATE_TOOL_NAMES, execute as state_execute
        if function_name in STATE_TOOL_NAMES:
            if not self._state_engine or not self._state_engine_enabled:
                result = f"Error: State engine not active for tool '{function_name}'"
                self._log_tool_call(function_name, arguments, result, time.time() - start_time, False)
                return result
            
            # Get current turn number
            turn = self._turn_getter() if self._turn_getter else 0
            
            try:
                result, success = state_execute(function_name, arguments, self._state_engine, turn)
                execution_time = time.time() - start_time
                self._log_tool_call(function_name, arguments, result, execution_time, success)
                return result
            except Exception as e:
                logger.error(f"Error executing state tool {function_name}: {e}")
                execution_time = time.time() - start_time
                self._log_tool_call(function_name, arguments, f"Error: {e}", execution_time, False)
                return f"Error executing {function_name}: {str(e)}"
        
        # Standard function execution
        executor = self.execution_map.get(function_name)
        if not executor:
            logger.error(f"No executor found for function '{function_name}'")
            result = f"The tool {function_name} is recognized but has no execution logic."
            self._log_tool_call(function_name, arguments, result, time.time() - start_time, False)
            return result
        
        try:
            result, success = executor(function_name, arguments, config)
            execution_time = time.time() - start_time
            self._log_tool_call(function_name, arguments, result, execution_time, success)
            return result
                
        except Exception as e:
            logger.error(f"Error executing function {function_name}: {e}")
            execution_time = time.time() - start_time
            self._log_tool_call(function_name, arguments, f"Error: {e}", execution_time, False)
            return f"Error executing {function_name}: {str(e)}"

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