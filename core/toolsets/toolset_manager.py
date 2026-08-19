# core/toolsets/toolset_manager.py
import logging
import json
import threading
import time
from pathlib import Path
from core.fs_utils import replace_with_retry

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
        """Load toolsets from user file. Seeds from core defaults on first run only.

        After first run, user/toolsets.json is authoritative — deleted toolsets
        stay deleted across restarts. Mirrors the c0b6817 fix for personas.
        """
        user_path = self.USER_DIR / "toolsets.json"
        core_path = self.BASE_DIR / "toolsets.json"

        if user_path.exists():
            # User file is authoritative — no re-seeding on boot
            try:
                with open(user_path, 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
                self._toolsets = {k: v for k, v in data.items() if not k.startswith('_')}
            except Exception as e:
                # Fail-safe (2026-08-19): an unreadable file must not run the
                # migrations below — they seed factory defaults and SAVE,
                # clobbering a file the user might still hand-recover. On a
                # reload (watcher mid-write race, transient corruption), keep
                # the previous in-memory registry: wiping to {} made every
                # toolset name dangle, so the next apply fell to 'none' —
                # zero tools, cemented until restart. On first load there is
                # nothing to keep — empty registry, file left untouched.
                if self._toolsets:
                    logger.error(f"Failed to load user toolsets: {e} — keeping "
                                 f"{len(self._toolsets)} previously loaded toolsets")
                else:
                    logger.error(f"Failed to load user toolsets: {e} — no toolsets "
                                 f"available; file left untouched for recovery")
                return
            # Targeted migration: ensure load-bearing toolsets exist. 'default'
            # is referenced by the agent persona (core/personas/personas.json)
            # and the agents plugin default_toolset setting; 'limited_web' is
            # the factory new-chat default (chat_defaults.json). On installs
            # that seeded their user file before these were shipped toolsets,
            # the consumers resolve to 0 tools and silently run with no
            # capability. Scout #8 — 2026-04-20; extended 2026-08-17. If the
            # user deliberately deleted one after this migration, they can
            # remove it again; we only seed when genuinely missing.
            missing = [n for n in ('default', 'limited_web') if n not in self._toolsets]
            if missing:
                try:
                    with open(core_path, 'r', encoding='utf-8') as f:
                        core_data = json.load(f)
                    seeded = []
                    for name in missing:
                        if core_data.get(name):
                            self._toolsets[name] = core_data[name]
                            seeded.append(name)
                    if seeded:
                        self._save_to_user()
                        logger.info(f"Seeded missing toolsets from core defaults (scout #8 migration): {seeded}")
                except Exception as e:
                    logger.warning(f"Toolset seed migration failed: {e}")
            # Targeted migration: rewrite meta tools renamed in a365e37 (2026-07).
            # Seeded-before-the-rename user toolsets still list the old names,
            # which silently drop at resolve time (logged INFO) — e.g. the
            # 'personality' toolset loses ALL its prompt/voice tools. Same shape
            # as the 'default' migration above: rewrite in place, persist once.
            if self._migrate_renamed_tools():
                self._save_to_user()
                logger.info("Migrated renamed meta-tool references in user toolsets")
            logger.info(f"Loaded {len(self._toolsets)} toolsets")
            return

        # First run — seed from core defaults
        self._toolsets = {}
        try:
            with open(core_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._toolsets = {k: v for k, v in data.items() if not k.startswith('_')}
        except Exception as e:
            logger.error(f"Failed to load core toolsets for first-run seed: {e}")

        if self._toolsets:
            self._save_to_user()
            logger.info(f"First run — seeded {len(self._toolsets)} toolsets from defaults")

    # Old -> new names for meta tools renamed in a365e37. The four *_piece tools
    # collapse into the single action-based prompt_pieces (deduped below).
    _TOOL_RENAMES = {
        "view_prompt": "prompt_view",
        "switch_prompt": "prompt_switch",
        "edit_prompt": "prompt_edit",
        "set_piece": "prompt_pieces",
        "remove_piece": "prompt_pieces",
        "create_piece": "prompt_pieces",
        "list_pieces": "prompt_pieces",
        "set_tts_voice": "set_voice",
    }

    def _migrate_renamed_tools(self):
        """Rewrite renamed tool references in every user toolset, order-preserving
        and deduped (the *_piece collapse can produce repeats). Names not in the
        map are left untouched, so custom user tools are never disturbed. Returns
        True if anything changed (caller persists)."""
        changed = False
        for ts in self._toolsets.values():
            funcs = ts.get("functions")
            if not isinstance(funcs, list):
                continue
            new_funcs, seen = [], set()
            for fn in funcs:
                mapped = self._TOOL_RENAMES.get(fn, fn)
                if mapped in seen:
                    continue
                seen.add(mapped)
                new_funcs.append(mapped)
            if new_funcs != funcs:
                ts["functions"] = new_funcs
                changed = True
        return changed

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
        """Get type for a toolset. All toolsets in the manager are 'user' type."""
        return 'user'

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
            existing = self._toolsets.get(name, {})
            self._toolsets[name] = {"functions": functions}
            # Preserve emoji if it existed
            if 'emoji' in existing:
                self._toolsets[name]['emoji'] = existing['emoji']
            return self._save_to_user()
    
    def delete_toolset(self, name: str) -> bool:
        """Delete a toolset."""
        if name not in self._toolsets:
            return False

        with self._lock:
            del self._toolsets[name]
            return self._save_to_user()
    
    def _save_to_user(self) -> bool:
        """Save all toolsets to user file."""
        user_path = self.USER_DIR / "toolsets.json"

        try:
            self.USER_DIR.mkdir(parents=True, exist_ok=True)

            data = {"_comment": "Your toolsets"}
            data.update(self._toolsets)
            
            tmp_path = user_path.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            replace_with_retry(tmp_path, user_path)
            
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