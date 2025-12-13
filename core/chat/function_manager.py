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
    def __init__(self):
        self.tool_history_file = 'user/history/tools/chat_tool_history.json'
        self.tool_history = []
        self.system_instance = None
        self._load_tool_history()

        # Dynamically load all function modules from functions/
        self.function_modules = {}
        self.execution_map = {}
        self.all_possible_tools = []
        self.enabled_tools = []
        
        # Track what was REQUESTED, not reverse-engineered
        self.current_ability_name = "default"
        
        self._load_function_modules()
        
        # Initialize with default toolset from toolset_manager
        default_toolset = toolset_manager.get_toolset('default')
        default_functions = default_toolset.get('functions', [])
        self.update_enabled_functions(default_functions if default_functions else ['default'])

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
                    
                    self.all_possible_tools.extend(tools)
                    
                    for tool in tools:
                        self.execution_map[tool['function']['name']] = executor
                    
                    logger.info(f"Loaded function module '{module_name}' with {len(tools)} tools")
                    
                except Exception as e:
                    logger.error(f"Failed to load function module '{module_name}': {e}")

    def update_enabled_functions(self, enabled_names: list):
        """Update enabled tools based on function names from config or ability name."""
        
        # Determine what ability name was requested
        requested_ability = enabled_names[0] if len(enabled_names) == 1 else "custom"
        
        # Special case: "all" loads every function from every module
        if len(enabled_names) == 1 and enabled_names[0] == "all":
            self.current_ability_name = "all"
            self.enabled_tools = self.all_possible_tools.copy()
            logger.info(f"Ability 'all' - LOADED ALL {len(self.enabled_tools)} FUNCTIONS")
            return
        
        # Special case: "none" disables all functions
        if len(enabled_names) == 1 and enabled_names[0] == "none":
            self.current_ability_name = "none"
            self.enabled_tools = []
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
        self.enabled_tools = [
            tool for tool in self.all_possible_tools 
            if tool['function']['name'] in enabled_names
        ]
        
        actual_names = [tool['function']['name'] for tool in self.enabled_tools]
        missing = set(enabled_names) - set(actual_names)
        
        if missing:
            logger.warning(f"Ability '{self.current_ability_name}' missing functions: {missing}")
        
        logger.info(f"Ability '{self.current_ability_name}': {len(self.enabled_tools)}/{expected_count} functions loaded")
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
        """Get list of currently enabled function names."""
        return [tool['function']['name'] for tool in self.enabled_tools]

    def get_current_ability_info(self):
        """Get info about current ability configuration."""
        actual_count = len(self.enabled_tools)
        expected_count = actual_count
        
        if self.current_ability_name == "all":
            expected_count = len(self.all_possible_tools)
        elif self.current_ability_name == "none":
            expected_count = 0
        elif self.current_ability_name in self.function_modules:
            expected_count = len(self.function_modules[self.current_ability_name]['available_functions'])
        elif toolset_manager.toolset_exists(self.current_ability_name):
            expected_count = len(toolset_manager.get_toolset_functions(self.current_ability_name))
        
        return {
            "name": self.current_ability_name,
            "function_count": actual_count,
            "expected_count": expected_count,
            "status": "ok" if actual_count == expected_count else "partial"
        }

    def execute_function(self, function_name, arguments):
        """Execute a function using the mapped executor."""
        start_time = time.time()
        
        logger.info(f"Executing function: {function_name}")
        
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
        """Load tool history from disk. Skipped if TOOL_HISTORY_MAX_ENTRIES is 0."""
        max_entries = getattr(config, 'TOOL_HISTORY_MAX_ENTRIES', 100)
        if max_entries == 0:
            self.tool_history = []
            return
        
        try:
            os.makedirs(os.path.dirname(self.tool_history_file), exist_ok=True)
            if os.path.exists(self.tool_history_file):
                with open(self.tool_history_file, 'r') as f:
                    self.tool_history = json.load(f)
        except Exception as e:
            logger.error(f"Error loading tool history: {e}")
            self.tool_history = []

    def _save_tool_history(self):
        """Save tool history to disk. Skipped if TOOL_HISTORY_MAX_ENTRIES is 0."""
        max_entries = getattr(config, 'TOOL_HISTORY_MAX_ENTRIES', 100)
        if max_entries == 0:
            return
        
        try:
            os.makedirs(os.path.dirname(self.tool_history_file), exist_ok=True)
            with open(self.tool_history_file, 'w') as f:
                json.dump(self.tool_history, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving tool history: {e}")

    def _log_tool_call(self, function_name, arguments, result, execution_time, success):
        """Log tool call to history. Disabled if TOOL_HISTORY_MAX_ENTRIES is 0."""
        max_entries = getattr(config, 'TOOL_HISTORY_MAX_ENTRIES', 100)
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