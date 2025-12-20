import os
import sys
import json
import logging
import importlib.util
import string
import re
import inspect
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from core.process_manager import ProcessManager
import config

logger = logging.getLogger(__name__)

class ModuleLoader:
    def __init__(self, core_dir: str = "core/modules", plugins_dir: str = "plugins"):
        self.base_path = Path(__file__).parent.parent.parent
        self.core_dir = os.path.join(self.base_path, core_dir)
        self.plugins_dir = os.path.join(self.base_path, plugins_dir)
        self.user_plugins_dir = os.path.join(self.base_path, "user", "plugins")
        self.modules = {}
        self.keyword_map = {}
        self.module_instances = {}
        self.auto_started_services = {}  # Track ProcessManager instances
        self.system = None  # Will be set by LLMChat
        
        # Create directories if they don't exist
        os.makedirs(self.core_dir, exist_ok=True)
        os.makedirs(self.plugins_dir, exist_ok=True)
            
        self.load_modules()
    
    def load_modules(self) -> None:
        """Load all modules from both core and plugins directories with robust error handling."""
        self.modules = {}
        self.keyword_map = {}
        self.module_instances = {}
        
        auto_start_modules = []
        
        # Load from both directories
        for search_dir, dir_label, dir_name, enabled in [
            (self.core_dir, "core", "core.modules", config.MODULES_ENABLED), 
            (self.plugins_dir, "plugin", "plugins", config.PLUGINS_ENABLED),
            (self.user_plugins_dir, "user plugin", "user.plugins", True)
        ]:
            if not enabled:
                logger.info(f"{dir_label.title()} loading disabled by config")
                continue
                
            if not os.path.exists(search_dir):
                logger.warning(f"{dir_label.title()} directory not found: {search_dir}")
                continue
            
            for module_name in os.listdir(search_dir):
                module_dir = os.path.join(search_dir, module_name)
                config_file = os.path.join(module_dir, "prompt_details.json")
                
                if not (os.path.isdir(module_dir) and os.path.exists(config_file)):
                    continue
                
                # State snapshot for rollback
                state_snapshot = {
                    'modules': self.modules.copy(),
                    'keyword_map': self.keyword_map.copy(),
                    'instances': self.module_instances.copy()
                }
                
                try:
                    # Load module config
                    with open(config_file, 'r', encoding='utf-8') as f:

                        module_config = json.load(f)
                    
                    # Mark whether this is core or plugin
                    module_config['_type'] = dir_label
                    self.modules[module_name] = module_config
                    
                    # Map keywords to this module
                    if "keywords" in module_config:
                        for keyword_entry in module_config["keywords"]:
                            for keyword in keyword_entry.split(','):
                                clean_keyword = keyword.lower().strip()
                                if clean_keyword:
                                    self.keyword_map[clean_keyword] = module_name
                        
                        logger.info(f"Loaded {dir_label} module: {module_name} with keywords: {module_config['keywords']}")
                    else:
                        logger.warning(f"Module {module_name} has no keywords defined")
                    
                    # Check for auto-start
                    if module_config.get("auto_start", False):
                        startup_script = module_config.get("startup_script")
                        if startup_script:
                            auto_start_modules.append((module_name, module_config, module_dir))
                    
                    # Try to load Python module implementation
                    module_impl_file = os.path.join(module_dir, f"{module_name}.py")
                    if os.path.exists(module_impl_file):
                        instance = self._load_module_safe(module_name, module_impl_file, dir_name)
                        if instance:
                            self.module_instances[module_name] = instance
                            logger.info(f"Loaded implementation for {dir_label} module: {module_name}")
                        else:
                            logger.warning(f"Module {module_name} registered but implementation unavailable")
                        
                except Exception as e:
                    # Rollback to pre-module state
                    self.modules = state_snapshot['modules']
                    self.keyword_map = state_snapshot['keyword_map']
                    self.module_instances = state_snapshot['instances']
                    logger.error(f"Error loading module {module_name}, rolled back: {e}")
        
        logger.info(f"Module loading complete: {len(self.module_instances)} implementations, {len(self.modules)} configs")
        
        # Start auto-start modules in startup_order
        if auto_start_modules:
            auto_start_modules.sort(key=lambda x: x[1].get("startup_order", 0))
            self._start_auto_services(auto_start_modules)
    
    def _start_auto_services(self, auto_start_modules):
        """Start auto-start services using ProcessManager."""
        for module_name, module_config, module_dir in auto_start_modules:
            try:
                startup_script = module_config.get("startup_script")
                script_path = Path(module_dir) / startup_script
                
                if not script_path.exists():
                    logger.error(f"Startup script not found for {module_name}: {script_path}")
                    continue
                
                # Create ProcessManager instance
                process_manager = ProcessManager(
                    script_path=script_path,
                    log_name=module_name,
                    base_dir=Path(self.base_path)
                )
                
                # Start the service
                process_manager.start()
                self.auto_started_services[module_name] = process_manager
                
                logger.info(f"Auto-started service: {module_name}")
                
            except Exception as e:
                logger.error(f"Error auto-starting {module_name}: {e}")
    
    def _load_module_safe(self, module_name: str, module_impl_file: str, dir_name: str):
        """Load module with multi-stage error handling and validation."""
        try:
            # Stage 1: Load the Python module
            try:
                impl_module = self._load_module(module_name, module_impl_file, dir_name)
            except ImportError as e:
                logger.error(f"Import error in {module_name}: {e}")
                return None
            except SyntaxError as e:
                logger.error(f"Syntax error in {module_name}: {e}")
                return None
            except Exception as e:
                logger.error(f"Load error in {module_name}: {e}")
                return None
            
            # Stage 2: Find the class
            module_class_name = ''.join(word.capitalize() for word in module_name.split('_'))
            if not hasattr(impl_module, module_class_name):
                logger.warning(f"Module {module_name} missing class {module_class_name}")
                return None
            
            # Stage 3: Instantiate with error catching
            try:
                instance = getattr(impl_module, module_class_name)()
            except TypeError as e:
                logger.error(f"Instantiation failed for {module_name} (check __init__ args): {e}")
                return None
            except Exception as e:
                logger.error(f"Instantiation failed for {module_name}: {e}")
                return None
            
            # Stage 4: Validate required methods exist
            if not hasattr(instance, 'process'):
                logger.error(f"Module {module_name} missing required process() method")
                return None
            
            # Stage 5: Quick validation that process is callable
            if not callable(getattr(instance, 'process')):
                logger.error(f"Module {module_name} process attribute is not callable")
                return None
            
            return instance
            
        except Exception as e:
            logger.error(f"Unexpected error loading module {module_name}: {e}")
            return None
    
    def _clean_text(self, text: str) -> str:
        """Clean text for comparison."""
        if not text:
            return ""
        return text.lower().translate(str.maketrans('', '', string.punctuation)).strip()
    
    def _load_module(self, module_name: str, file_path: str, dir_name: str = "modules") -> Any:
        """Load a Python module from file path with correct import namespace.
        
        Args:
            module_name: Name of the module
            file_path: Full path to the .py file
            dir_name: Directory name for import path ("modules" or "plugins")
        """
        # Use correct import path based on directory name
        import_path = f"{dir_name}.{module_name}.{module_name}"
        module_spec = importlib.util.spec_from_file_location(import_path, file_path)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[import_path] = module
        module_spec.loader.exec_module(module)
        return module
    
    def get_module_info(self, module_name: str) -> Dict[str, Any]:
        """Get module information."""
        if module_name not in self.modules:
            return {}
            
        return {
            "name": module_name,
            "config": self.modules[module_name],
            "instance": self.get_module_instance(module_name),
            "prompt": self.modules[module_name].get("prompt", {})
        }
    
    def get_module_instance(self, module_name: str) -> Optional[Any]:
        """Get module instance."""
        return self.module_instances.get(module_name)
    
    def detect_module(self, text: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], str]:
        """Detect which module should handle the given text."""
        if not text:
            return None, None, text
            
        # Preserve original text
        original_text = text
        text_clean = self._clean_text(text)
        
        # Sort keywords by length (longest first) to prioritize longer matches
        sorted_keywords = sorted(self.keyword_map.keys(), key=len, reverse=True)
        
        for keyword in sorted_keywords:
            module_name = self.keyword_map[keyword]
            config = self.modules.get(module_name, {})
            
            # Check if exact match is required - support both old and new property names
            exact_match_only = config.get("exact_match", config.get("capture_only_exact_match", config.get("exact_phrase_match", True)))
            keyword_clean = self._clean_text(keyword)
            
            # Check for exact match first
            if text_clean == keyword_clean:
                instance = self.get_module_instance(module_name)
                if instance:
                    instance.keyword_match = keyword
                    instance.full_command = original_text
                
                return module_name, self.get_module_info(module_name), original_text
                
            # Check for partial match if allowed
            elif not exact_match_only and text_clean.startswith(keyword_clean):
                # Find remaining text after keyword
                remaining = ""
                if len(original_text) > len(keyword):
                    for i in range(len(keyword), len(original_text)):
                        if original_text[i].isspace():
                            remaining = original_text[i:].strip()
                            break
                
                # Store context for the module
                instance = self.get_module_instance(module_name)
                if instance:
                    instance.keyword_match = keyword
                    instance.full_command = original_text
                
                logger.info(f"Partial match for '{keyword}' with '{module_name}', remaining: '{remaining}'")
                return module_name, self.get_module_info(module_name), remaining
                
        # No module found
        return None, None, text
    
    def process_direct(self, module_name: str, user_input: str, active_chat: str = "default") -> Optional[str]:
        """
        Process input directly with a module with error isolation.
        
        Args:
            module_name: Name of the module to execute
            user_input: User's input text
            active_chat: Name of the active chat (defaults to "default")
        """
        if module_name not in self.module_instances:
            logger.error(f"No module implementation for {module_name}")
            return None
        
        try:
            logger.info(f"Direct processing with module '{module_name}': '{user_input}' (chat: {active_chat})")
            
            # Isolate module execution
            try:
                module_instance = self.module_instances[module_name]
                
                # Check if module's process() accepts active_chat parameter
                sig = inspect.signature(module_instance.process)
                if 'active_chat' in sig.parameters:
                    logger.debug(f"Module {module_name} accepts active_chat parameter")
                    result = module_instance.process(user_input, active_chat=active_chat)
                else:
                    logger.debug(f"Module {module_name} uses legacy signature (no active_chat)")
                    result = module_instance.process(user_input)
                
                return result
            except AttributeError as e:
                logger.error(f"Module {module_name} has invalid process method: {e}")
                return f"Module configuration error"
            except TypeError as e:
                logger.error(f"Module {module_name} process() signature error: {e}")
                return f"Module interface error"
            except Exception as e:
                logger.error(f"Module {module_name} execution error: {e}")
                return f"Error: {str(e)}"
                
        except Exception as e:
            logger.error(f"Critical error in module processing for {module_name}: {e}")
            return f"Module system error"
            
    def set_system(self, system):
        """Set reference to the main system."""
        self.system = system
        
    def get_module_list(self):
        """Returns a dictionary of all loaded modules and their configurations."""
        return self.modules