# core/modules/system/toolsets/toolset_manager.py
import logging
import json
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class ToolsetManager:
    """Manages toolset definitions with hot-reload and user overrides."""
    
    def __init__(self):
        self.BASE_DIR = Path(__file__).parent
        # Find project root (where user/ directory lives)
        # Walk up until we find a directory containing 'user' or 'core'
        project_root = self.BASE_DIR.parent
        while project_root.parent != project_root:  # Stop at filesystem root
            if (project_root / 'core').exists() or (project_root / 'main.py').exists():
                break
            project_root = project_root.parent
        
        self.USER_DIR = project_root / "user" / "toolsets"
        
        self._toolsets = {}
        
        self._lock = threading.Lock()
        self._watcher_thread = None
        self._watcher_running = False
        self._last_mtimes = {}  # Per-file mtime tracking
        
        # Ensure user directory exists
        try:
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Toolset user directory: {self.USER_DIR}")
        except Exception as e:
            logger.error(f"Failed to create toolset user directory: {e}")
        
        self._load()
    
    def _load(self):
        """Load toolsets - core presets + user overrides merged."""
        user_path = self.USER_DIR / "toolsets.json"
        core_path = self.BASE_DIR / "toolsets.json"

        self._toolsets = {}

        # Load core presets first
        try:
            with open(core_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._toolsets = {k: v for k, v in data.items() if not k.startswith('_')}
            logger.info(f"Loaded {len(self._toolsets)} core toolsets")
        except Exception as e:
            logger.error(f"Failed to load core toolsets: {e}")

        # Merge user toolsets
        if user_path.exists():
            try:
                with open(user_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                user_ts = {k: v for k, v in data.items() if not k.startswith('_')}
                for k, v in user_ts.items():
                    if k in self._toolsets and self._toolsets[k].get('type') == 'preset':
                        # Preset override — only take emoji, keep core functions
                        if 'emoji' in v:
                            self._toolsets[k]['emoji'] = v['emoji']
                    else:
                        self._toolsets[k] = v
                logger.info(f"Merged {len(user_ts)} user toolsets")
            except Exception as e:
                logger.error(f"Failed to load user toolsets: {e}")

        if not self._toolsets:
            self._toolsets = {"default": {"functions": []}}
    
    def reload(self):
        """Reload toolsets from disk."""
        with self._lock:
            self._load()
            logger.info("Toolsets reloaded")
    
    def start_file_watcher(self):
        """Start background file watcher."""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            logger.warning("Toolset file watcher already running")
            return
        
        self._watcher_running = True
        self._watcher_thread = threading.Thread(
            target=self._file_watcher_loop,
            daemon=True,
            name="ToolsetFileWatcher"
        )
        self._watcher_thread.start()
        logger.info("Toolset file watcher started")
    
    def stop_file_watcher(self):
        """Stop the file watcher."""
        if self._watcher_thread is None:
            return
        
        self._watcher_running = False
        if self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=5)
        logger.info("Toolset file watcher stopped")
    
    def _file_watcher_loop(self):
        """Watch for file changes."""
        watch_files = [
            self.BASE_DIR / "toolsets.json",
            self.USER_DIR / "toolsets.json"
        ]
        
        while self._watcher_running:
            try:
                time.sleep(2)
                
                for path in watch_files:
                    if not path.exists():
                        continue
                    
                    path_key = str(path)
                    current_mtime = path.stat().st_mtime
                    last_mtime = self._last_mtimes.get(path_key)
                    
                    if last_mtime is not None and current_mtime != last_mtime:
                        logger.info(f"Detected change in {path.name}")
                        time.sleep(0.5)  # Debounce
                        self.reload()
                        # Update all mtimes after reload to prevent re-trigger
                        for p in watch_files:
                            if p.exists():
                                self._last_mtimes[str(p)] = p.stat().st_mtime
                        break  # Exit inner loop, start fresh
                    
                    self._last_mtimes[path_key] = current_mtime
            
            except Exception as e:
                logger.error(f"Toolset file watcher error: {e}")
                time.sleep(5)
    
    # === Getters ===
    
    def get_toolset(self, name: str) -> dict:
        """Get a toolset by name."""
        return self._toolsets.get(name, {})
    
    def get_toolset_functions(self, name: str) -> list:
        """Get function list for a toolset."""
        return self._toolsets.get(name, {}).get('functions', [])

    def get_toolset_type(self, name: str) -> str:
        """Get type for a toolset ('preset' or 'user')."""
        return self._toolsets.get(name, {}).get('type', 'user')

    def get_toolset_emoji(self, name: str) -> str:
        """Get custom emoji for a toolset, or empty string."""
        return self._toolsets.get(name, {}).get('emoji', '')

    def set_emoji(self, name: str, emoji: str) -> bool:
        """Set custom emoji on any toolset (including presets)."""
        if name not in self._toolsets:
            return False
        with self._lock:
            if emoji:
                self._toolsets[name]['emoji'] = emoji
            else:
                self._toolsets[name].pop('emoji', None)
            return self._save_to_user()
    
    def get_all_toolsets(self) -> dict:
        """Get all toolsets."""
        return self._toolsets.copy()
    
    def get_toolset_names(self) -> list:
        """Get list of toolset names."""
        return list(self._toolsets.keys())
    
    def toolset_exists(self, name: str) -> bool:
        """Check if toolset exists."""
        return name in self._toolsets
    
    # === CRUD for user toolsets ===
    
    def save_toolset(self, name: str, functions: list) -> bool:
        """Save or update a toolset (writes to user file)."""
        with self._lock:
            self._toolsets[name] = {"functions": functions}
            return self._save_to_user()
    
    def delete_toolset(self, name: str) -> bool:
        """Delete a toolset (user-created only, not presets)."""
        if name not in self._toolsets:
            return False
        if self._toolsets[name].get('type') == 'preset':
            return False

        with self._lock:
            del self._toolsets[name]
            return self._save_to_user()
    
    def _save_to_user(self) -> bool:
        """Save user toolsets + preset emoji overrides to user file."""
        user_path = self.USER_DIR / "toolsets.json"

        try:
            # Ensure directory exists
            self.USER_DIR.mkdir(parents=True, exist_ok=True)

            data = {"_comment": "Your custom toolsets"}
            for k, v in self._toolsets.items():
                if v.get('type') == 'preset':
                    # Only save emoji override for presets (not functions)
                    if 'emoji' in v:
                        data[k] = {"type": "preset", "emoji": v['emoji']}
                else:
                    data[k] = v
            
            with open(user_path, 'w', encoding='utf-8') as f:

                json.dump(data, f, indent=2)
            
            # Update mtime after save to prevent watcher from triggering
            self._last_mtimes[str(user_path)] = user_path.stat().st_mtime
            
            logger.info(f"Saved {len(self._toolsets)} toolsets to {user_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save toolsets to {user_path}: {e}")
            return False
    
    @property
    def toolsets(self):
        """Property access to toolsets dict (for backward compat)."""
        return self._toolsets


# Singleton instance
toolset_manager = ToolsetManager()