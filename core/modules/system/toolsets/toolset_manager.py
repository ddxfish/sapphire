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
        """Load toolsets - user file replaces core entirely."""
        user_path = self.USER_DIR / "toolsets.json"
        core_path = self.BASE_DIR / "toolsets.json"
        
        path = user_path if user_path.exists() else core_path
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            # Filter out metadata keys
            self._toolsets = {k: v for k, v in data.items() if not k.startswith('_')}
            
            source = "user" if path == user_path else "core"
            logger.info(f"Loaded {len(self._toolsets)} toolsets from {source}")
        except Exception as e:
            logger.error(f"Failed to load toolsets: {e}")
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
        """Delete a toolset (from user file only)."""
        if name not in self._toolsets:
            return False
        
        with self._lock:
            del self._toolsets[name]
            return self._save_to_user()
    
    def _save_to_user(self) -> bool:
        """Save current toolsets to user file."""
        user_path = self.USER_DIR / "toolsets.json"
        
        try:
            # Ensure directory exists
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Saving toolsets to: {user_path}")
            
            data = {"_comment": "Your custom toolsets"}
            data.update(self._toolsets)
            
            with open(user_path, 'w') as f:
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