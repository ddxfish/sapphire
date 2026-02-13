import logging
import json
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class PromptManager:
    """Manages prompt templates with hot-reload. Uses ONLY user/prompts/ directory."""
    
    def __init__(self):
        # Single source of truth - user/prompts/ only
        self.USER_DIR = Path(__file__).parent.parent.parent.parent / "user" / "prompts"
        
        self._components = {}
        self._scenario_presets = {}
        self._monoliths = {}
        self._spices = {}
        self._disabled_categories = set()
        
        self._lock = threading.Lock()
        self._watcher_thread = None
        self._watcher_running = False
        self._last_mtimes = {}
        self._active_preset_name = 'unknown'
        
        # Ensure user directory exists (bootstrap should have run, but be safe)
        self.USER_DIR.mkdir(parents=True, exist_ok=True)
        
        self._load_all()
    
    def _load_all(self):
        """Load all prompt data from user/prompts/ JSON files."""
        self._load_pieces()
        self._load_monoliths()
        self._load_spices()
    
    def _load_pieces(self):
        """Load prompt pieces from user/prompts/."""
        path = self.USER_DIR / "prompt_pieces.json"
        
        if not path.exists():
            logger.warning(f"prompt_pieces.json not found at {path} - using empty defaults")
            self._components = {}
            self._scenario_presets = {}
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self._components = data.get("components", {})
            self._scenario_presets = data.get("scenario_presets", {})
            
            logger.info(f"Loaded prompt pieces: {len(self._components)} component types")
        except Exception as e:
            logger.error(f"Failed to load prompt pieces: {e}")
            self._components = {}
            self._scenario_presets = {}
    
    def _load_monoliths(self):
        """Load monolith prompts from user/prompts/."""
        path = self.USER_DIR / "prompt_monoliths.json"

        if not path.exists():
            logger.warning(f"prompt_monoliths.json not found at {path} - using empty defaults")
            self._monoliths = {}
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)

            # Normalize format: support both old (string) and new (object) formats
            self._monoliths = {}
            for k, v in raw_data.items():
                if k.startswith('_'):
                    continue
                if isinstance(v, str):
                    # Old format: {name: "content"} -> convert to new format
                    self._monoliths[k] = {'content': v, 'privacy_required': False}
                elif isinstance(v, dict):
                    # New format: {name: {content: "...", privacy_required: bool}}
                    self._monoliths[k] = v
                else:
                    logger.warning(f"Skipping monolith '{k}' with unexpected type: {type(v)}")

            logger.info(f"Loaded {len(self._monoliths)} monolith prompts")
        except Exception as e:
            logger.error(f"Failed to load monoliths: {e}")
            self._monoliths = {}
    
    def _load_spices(self):
        """Load spice pool from user/prompts/."""
        path = self.USER_DIR / "prompt_spices.json"
        
        if not path.exists():
            logger.warning(f"prompt_spices.json not found at {path} - using empty defaults")
            self._spices = {}
            self._disabled_categories = set()
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            
            # Extract disabled categories before filtering metadata
            self._disabled_categories = set(raw_data.get('_disabled_categories', []))
            
            # Remove metadata keys for spices dict
            self._spices = {k: v for k, v in raw_data.items() if not k.startswith('_')}
            
            logger.info(f"Loaded spice pool: {len(self._spices)} categories, {len(self._disabled_categories)} disabled")
        except Exception as e:
            logger.error(f"Failed to load spices: {e}")
            self._spices = {}
            self._disabled_categories = set()
    
    def _replace_templates(self, text: str) -> str:
        """Replace {ai_name} and {user_name} with values from settings."""
        if not text:
            return text
        
        try:
            from core.settings_manager import settings
            ai_name = settings.get('DEFAULT_AI_NAME', 'Sapphire')
            user_name = settings.get('DEFAULT_USERNAME', 'Human Protagonist')
            # Sanitize curly brackets to prevent template injection
            ai_name = ai_name.replace('{', '').replace('}', '')
            user_name = user_name.replace('{', '').replace('}', '')
            return text.replace('{ai_name}', ai_name).replace('{user_name}', user_name)
        except Exception as e:
            logger.error(f"Template replacement failed: {e}")
            return text
    
    def reload(self):
        """Reload all prompt data from disk."""
        with self._lock:
            self._load_all()
            logger.info("Prompt data reloaded")
    
    def start_file_watcher(self):
        """Start background file watcher for user prompts."""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            logger.warning("File watcher already running")
            return
        
        self._watcher_running = True
        self._watcher_thread = threading.Thread(
            target=self._file_watcher_loop,
            daemon=True,
            name="PromptFileWatcher"
        )
        self._watcher_thread.start()
        logger.info("Prompt file watcher started")
    
    def stop_file_watcher(self):
        """Stop the file watcher."""
        if self._watcher_thread is None:
            return
        
        self._watcher_running = False
        if self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=5)
        logger.info("Prompt file watcher stopped")
    
    def _file_watcher_loop(self):
        """Watch user prompt files for changes."""
        watch_files = [
            self.USER_DIR / "prompt_pieces.json",
            self.USER_DIR / "prompt_monoliths.json",
            self.USER_DIR / "prompt_spices.json"
        ]
        
        while self._watcher_running:
            try:
                time.sleep(2)
                
                changed = False
                for path in watch_files:
                    if not path.exists():
                        continue
                    
                    current_mtime = path.stat().st_mtime
                    last_mtime = self._last_mtimes.get(str(path))
                    
                    if last_mtime is not None and current_mtime != last_mtime:
                        logger.info(f"Detected change in {path.name}")
                        changed = True
                    
                    self._last_mtimes[str(path)] = current_mtime
                
                if changed:
                    time.sleep(0.5)  # Debounce
                    self.reload()
            
            except Exception as e:
                logger.error(f"File watcher error: {e}")
                time.sleep(5)
    
    def assemble_from_components(self, components):
        """Assemble prompt text from component structure."""
        prompt_parts = []
        
        # Add character (main character description)
        character_key = components.get('character', 'sapphire')
        if 'character' in self._components:
            if character_key in self._components['character']:
                prompt_parts.append(self._components['character'][character_key])
        
        # Add structured components
        components_text = []
        
        component_types = ['goals', 'location', 'relationship', 'format', 'scenario']
        for comp_type in component_types:
            key = components.get(comp_type)
            if key and comp_type in self._components:
                if key in self._components[comp_type]:
                    value = self._components[comp_type][key]
                    if value and value.strip():
                        components_text.append(f"{comp_type.capitalize()}: {value}")
        
        # Extras (multiple allowed)
        extras = components.get('extras', [])
        if extras:
            extras_list = []
            if 'extras' in self._components:
                for extra_key in extras:
                    if extra_key in self._components['extras']:
                        extras_list.append(self._components['extras'][extra_key])
            if extras_list:
                components_text.append(f"Extras: {', '.join(extras_list)}")
        
        # Emotions (multiple allowed)
        emotions = components.get('emotions', [])
        if emotions:
            emotions_list = []
            if 'emotions' in self._components:
                for emotion_key in emotions:
                    if emotion_key in self._components['emotions']:
                        emotions_list.append(self._components['emotions'][emotion_key])
            if emotions_list:
                components_text.append(f"Emotions: {', '.join(emotions_list)}")
        
        # Combine all parts
        if components_text:
            prompt_parts.append("\n".join(components_text))
        
        return "\n\n".join(prompt_parts)
    
    def save_scenario_presets(self):
        """Save scenario presets to user/prompts/prompt_pieces.json"""
        target_path = self.USER_DIR / "prompt_pieces.json"
        
        # Load existing data
        try:
            with open(target_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {"_comment": "User prompt pieces", "components": {}, "scenario_presets": {}}
        
        # Update scenario_presets section
        data['scenario_presets'] = self._scenario_presets
        
        # Save back
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved scenario presets to {target_path}")
    
    def save_monoliths(self):
        """Save monoliths to user/prompts/prompt_monoliths.json"""
        target_path = self.USER_DIR / "prompt_monoliths.json"

        # Load existing to preserve _comment
        try:
            with open(target_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            comment = old_data.get('_comment')
        except Exception:
            comment = "User monolith prompts"

        # Build fresh dict with new format
        data = {}
        if comment:
            data['_comment'] = comment
        # Ensure each monolith has the full object structure
        for name, mono in self._monoliths.items():
            if isinstance(mono, dict):
                data[name] = mono
            else:
                # Shouldn't happen, but handle gracefully
                data[name] = {'content': str(mono), 'privacy_required': False}

        # Save
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved monoliths to {target_path}")
    
    def save_components(self):
        """Save components to user/prompts/prompt_pieces.json"""
        target_path = self.USER_DIR / "prompt_pieces.json"
        
        # Load existing data
        try:
            with open(target_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {"_comment": "User prompt pieces", "components": {}, "scenario_presets": {}}
        
        # Update components section
        data['components'] = self._components
        
        # Save back
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved components to {target_path}")
    
    def save_spices(self):
        """Save spices to user/prompts/prompt_spices.json"""
        target_path = self.USER_DIR / "prompt_spices.json"
        
        # Build data with metadata
        data = {"_comment": "User spices - managed via Spice Manager plugin"}
        if self._disabled_categories:
            data["_disabled_categories"] = sorted(list(self._disabled_categories))
        data.update(self._spices)
        
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved spices to {target_path}")
    
    def is_category_enabled(self, category: str) -> bool:
        """Check if a spice category is enabled."""
        return category not in self._disabled_categories
    
    def set_category_enabled(self, category: str, enabled: bool):
        """Enable or disable a spice category."""
        if enabled:
            self._disabled_categories.discard(category)
        else:
            self._disabled_categories.add(category)
        self.save_spices()
        logger.info(f"Spice category '{category}' {'enabled' if enabled else 'disabled'}")
    
    def get_enabled_spices(self) -> list:
        """Get all spices from enabled categories only."""
        return [
            spice 
            for category, spices in self._spices.items() 
            if category not in self._disabled_categories
            for spice in spices
        ]
    
    @property
    def disabled_categories(self):
        return self._disabled_categories
    
    @property
    def components(self):
        return self._components
    
    @property
    def scenario_presets(self):
        return self._scenario_presets
    
    @property
    def monoliths(self):
        return self._monoliths
    
    @property
    def spices(self):
        return self._spices


# Create singleton instance
prompt_manager = PromptManager()