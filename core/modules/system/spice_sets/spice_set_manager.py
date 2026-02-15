# core/modules/system/spice_sets/spice_set_manager.py
import logging
import json
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class SpiceSetManager:
    """Manages spice set definitions with hot-reload and user overrides."""

    def __init__(self):
        self.BASE_DIR = Path(__file__).parent
        project_root = self.BASE_DIR.parent
        while project_root.parent != project_root:
            if (project_root / 'core').exists() or (project_root / 'main.py').exists():
                break
            project_root = project_root.parent

        self.USER_DIR = project_root / "user" / "spice_sets"

        self._sets = {}
        self._active_name = 'default'

        self._lock = threading.Lock()
        self._watcher_thread = None
        self._watcher_running = False
        self._last_mtimes = {}

        try:
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Spice set user directory: {self.USER_DIR}")
        except Exception as e:
            logger.error(f"Failed to create spice set user directory: {e}")

        self._load()

    def _load(self):
        """Load spice sets from user file, seeding from core defaults if needed."""
        user_path = self.USER_DIR / "spice_sets.json"
        core_path = self.BASE_DIR / "spice_sets.json"

        core_sets = {}
        try:
            with open(core_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            core_sets = {k: v for k, v in data.items() if not k.startswith('_')}
        except Exception as e:
            logger.error(f"Failed to load core spice sets: {e}")

        self._sets = {}
        if user_path.exists():
            try:
                with open(user_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._sets = {k: v for k, v in data.items() if not k.startswith('_')}
            except Exception as e:
                logger.error(f"Failed to load user spice sets: {e}")

        seeded = 0
        for name, ss in core_sets.items():
            if name not in self._sets:
                self._sets[name] = ss
                seeded += 1

        if seeded > 0:
            logger.info(f"Seeded {seeded} new spice sets from defaults")
            self._save_to_user()

        if not self._sets:
            self._sets = {}

        logger.info(f"Loaded {len(self._sets)} spice sets")

    def reload(self):
        with self._lock:
            self._load()
            logger.info("Spice sets reloaded")

    def start_file_watcher(self):
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            return
        self._watcher_running = True
        self._watcher_thread = threading.Thread(
            target=self._file_watcher_loop, daemon=True, name="SpiceSetFileWatcher"
        )
        self._watcher_thread.start()
        logger.info("Spice set file watcher started")

    def stop_file_watcher(self):
        if self._watcher_thread is None:
            return
        self._watcher_running = False
        if self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=5)
        logger.info("Spice set file watcher stopped")

    def _file_watcher_loop(self):
        watch_files = [
            self.BASE_DIR / "spice_sets.json",
            self.USER_DIR / "spice_sets.json"
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
                        time.sleep(0.5)
                        self.reload()
                        for p in watch_files:
                            if p.exists():
                                self._last_mtimes[str(p)] = p.stat().st_mtime
                        break
                    self._last_mtimes[path_key] = current_mtime
            except Exception as e:
                logger.error(f"Spice set file watcher error: {e}")
                time.sleep(5)

    # === Getters ===

    def get_set(self, name: str) -> dict:
        return self._sets.get(name, {})

    def get_categories(self, name: str) -> list:
        return self._sets.get(name, {}).get('categories', [])

    def get_emoji(self, name: str) -> str:
        return self._sets.get(name, {}).get('emoji', '')

    def set_emoji(self, name: str, emoji: str) -> bool:
        if name not in self._sets:
            return False
        with self._lock:
            if emoji:
                self._sets[name]['emoji'] = emoji
            else:
                self._sets[name].pop('emoji', None)
            return self._save_to_user()

    def get_all_sets(self) -> dict:
        return self._sets.copy()

    def get_set_names(self) -> list:
        return list(self._sets.keys())

    def set_exists(self, name: str) -> bool:
        return name in self._sets

    @property
    def active_name(self):
        return self._active_name

    @active_name.setter
    def active_name(self, name):
        self._active_name = name

    # === CRUD ===

    def save_set(self, name: str, categories: list) -> bool:
        with self._lock:
            existing = self._sets.get(name, {})
            self._sets[name] = {"categories": categories}
            if 'emoji' in existing:
                self._sets[name]['emoji'] = existing['emoji']
            return self._save_to_user()

    def delete_set(self, name: str) -> bool:
        if name not in self._sets:
            return False
        with self._lock:
            del self._sets[name]
            return self._save_to_user()

    def _save_to_user(self) -> bool:
        user_path = self.USER_DIR / "spice_sets.json"
        try:
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            data = {"_comment": "Your spice sets"}
            data.update(self._sets)
            with open(user_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self._last_mtimes[str(user_path)] = user_path.stat().st_mtime
            logger.info(f"Saved {len(self._sets)} spice sets to {user_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save spice sets to {user_path}: {e}")
            return False

    @property
    def sets(self):
        return self._sets


# Singleton instance
spice_set_manager = SpiceSetManager()
