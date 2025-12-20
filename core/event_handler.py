import os
import json
import logging
import datetime
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class EventScheduler:
    def __init__(self, system=None):
        self.system = system  # Reference to main system
        self.events = []  # List of all events
        self.last_check_minute = -1  # To avoid checking multiple times in same minute
        self.load_events()
        logger.info("Event scheduler initialized")
    
    def load_events(self):
        """Load events from all events.json files."""
        self.events = []
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Load events from main events.json if it exists
        main_events_file = os.path.join(base_dir, "events.json")
        if os.path.exists(main_events_file):
            self._load_events_file(main_events_file)
        
        # Load events from module directories
        modules_dir = os.path.join(base_dir, "core", "modules")
        if os.path.exists(modules_dir):
            for module_name in os.listdir(modules_dir):
                module_dir = os.path.join(modules_dir, module_name)
                if os.path.isdir(module_dir):
                    module_events_file = os.path.join(module_dir, "events.json")
                    self._load_events_file(module_events_file, default_module=module_name)
        
        # Load events from plugin directories
        plugins_dir = os.path.join(base_dir, "plugins")
        if os.path.exists(plugins_dir):
            for plugin_name in os.listdir(plugins_dir):
                plugin_dir = os.path.join(plugins_dir, plugin_name)
                if os.path.isdir(plugin_dir):
                    plugin_events_file = os.path.join(plugin_dir, "events.json")
                    self._load_events_file(plugin_events_file, default_module=plugin_name)
        
        # Load events from user plugin directories
        user_plugins_dir = os.path.join(base_dir, "user", "plugins")
        if os.path.exists(user_plugins_dir):
            for plugin_name in os.listdir(user_plugins_dir):
                plugin_dir = os.path.join(user_plugins_dir, plugin_name)
                if os.path.isdir(plugin_dir):
                    plugin_events_file = os.path.join(plugin_dir, "events.json")
                    self._load_events_file(plugin_events_file, default_module=plugin_name)
        
        logger.info(f"Loaded {len(self.events)} events total")
    
    def _load_events_file(self, file_path, default_module=None):
        """Load events from a single file."""
        if not os.path.exists(file_path):
            return
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:

                events_data = json.load(f)
                
            if "events" in events_data and isinstance(events_data["events"], list):
                for event in events_data["events"]:
                    # Add default module name if not specified
                    if default_module and "module" not in event:
                        event["module"] = default_module
                    
                    # Validate required fields
                    if "module" not in event or "action" not in event:
                        logger.warning(f"Skipping invalid event in {file_path}: {event}")
                        continue
                        
                    self.events.append(event)
                    logger.info(f"Loaded event: {event.get('name', 'unnamed')} for module: {event.get('module')}")
        except Exception as e:
            logger.error(f"Error loading events from {file_path}: {e}")
    
    def check_and_trigger_events(self):
        """Check if any events should be triggered based on current time."""
        now = datetime.datetime.now()
        current_minute = now.hour * 60 + now.minute
        
        # Only check once per minute
        if current_minute == self.last_check_minute:
            return
            
        self.last_check_minute = current_minute
        
        # Get current time components
        current_hour = now.hour
        current_minute = now.minute
        current_day = now.strftime("%A")  # Day name (Monday, Tuesday, etc.)
        
        for event in self.events:
            # Check if this event should trigger now
            if self._should_trigger(event, current_hour, current_minute, current_day):
                self._trigger_event(event)
    
    def _should_trigger(self, event, hour, minute, day):
        """Check if an event should trigger at the given time."""
        schedule = event.get("schedule", {})
        
        # Check hours (empty list means every hour)
        hours = schedule.get("hours", [])
        if hours and hour not in hours:
            return False
            
        # Check minutes (empty list means every minute)
        minutes = schedule.get("minutes", [])
        if minutes and minute not in minutes:
            return False
            
        # Check days (empty list means every day)
        days = schedule.get("days", [])
        if days and day not in days:
            return False
            
        return True
    
    def _trigger_event(self, event):
        """Trigger an event by calling the appropriate module."""
        if not self.system:
            logger.error("Cannot trigger event: no system reference")
            return False
        
        try:
            module_name = event.get("module")
            action = event.get("action", "")
            parameters = event.get("parameters", "")
            append_to_history = event.get("append_to_history", False)
            
            logger.info(f"Triggering event: {event.get('name', 'unnamed')} - {module_name} {action} {parameters}")
            
            # Construct the command - action is the primary input, parameters are appended
            command = f"{action} {parameters}".strip() if parameters else action
            
            # Get module instance
            module_loader = self.system.llm_chat.module_loader
            module_instance = module_loader.get_module_instance(module_name)
            
            if module_instance:
                # Simulate normal module detection by setting these properties
                module_instance.keyword_match = action
                module_instance.full_command = command
                
                # Call the module with action (+ parameters if any)
                # Modules expect action as input, e.g. "run_scheduled" or "trigger_scene bedroom"
                result = module_instance.process(command)

                
                # Speak result if it's a string
                if isinstance(result, str) and result.strip():
                    self.system.tts.speak(result)
                    
                # Optionally add to chat history
                if append_to_history and hasattr(self.system, 'llm_chat'):
                    self.system.llm_chat.chat(command, add_to_history=True)
                
                return True
            else:
                # If no module instance, try the LLM path
                if hasattr(self.system, 'process_llm_query'):
                    self.system.process_llm_query(command)
                    return True
                else:
                    logger.error(f"Cannot find module or LLM processor for event: {event.get('name')}")
                    return False
            
        except Exception as e:
            logger.error(f"Error processing event: {e}")
            return False