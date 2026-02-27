# core/plugin_loader.py — Plugin discovery, loading, and lifecycle
#
# Scans plugins/ and user/plugins/ for plugin.json manifests.
# Registers hooks, voice commands, and (later) tools/web/schedule.

import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any

from core.hooks import hook_runner
from core.plugin_verify import verify_plugin

logger = logging.getLogger(__name__)

# Plugin search paths (relative to project root)
PROJECT_ROOT = Path(__file__).parent.parent
SYSTEM_PLUGINS_DIR = PROJECT_ROOT / "plugins"
USER_PLUGINS_DIR = PROJECT_ROOT / "user" / "plugins"
PLUGIN_STATE_DIR = PROJECT_ROOT / "user" / "plugin_state"

# Where enabled/disabled state is stored
USER_PLUGINS_JSON = PROJECT_ROOT / "user" / "webui" / "plugins.json"
STATIC_PLUGINS_JSON = PROJECT_ROOT / "interfaces" / "web" / "static" / "core-ui" / "plugins.json"


class PluginState:
    """Simple JSON key-value store for plugin data.

    Each plugin gets its own file at user/plugin_state/{name}.json.
    Authors who need more can bring their own SQLite.
    """

    def __init__(self, plugin_name: str):
        self._name = plugin_name
        self._path = PLUGIN_STATE_DIR / f"{plugin_name}.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[PLUGIN-STATE] Failed to load {self._path}: {e}")
        return {}

    def _save(self):
        PLUGIN_STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def save(self, key: str, value):
        self._data[key] = value
        self._save()

    def delete(self, key: str):
        self._data.pop(key, None)
        self._save()

    def all(self) -> dict:
        return dict(self._data)

    def clear(self):
        self._data = {}
        self._save()


class PluginLoader:
    """Discovers, validates, and loads plugins from plugins/ and user/plugins/."""

    def __init__(self):
        # {plugin_name: {manifest, path, enabled, band, state}}
        self._plugins: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._function_manager = None  # Set via scan() for plugin tool loading
        self._scheduler = None  # Set via set_scheduler() for plugin schedule tasks
        self._watcher_running = False
        self._watcher_thread = None

    def scan(self, function_manager=None):
        """Discover all plugins and load enabled ones.

        Args:
            function_manager: Optional FunctionManager for plugin tool registration.
        """
        self._function_manager = function_manager
        self._plugins.clear()
        enabled_list = self._get_enabled_list()

        # System plugins (priority band 0-99)
        self._scan_dir(SYSTEM_PLUGINS_DIR, band="system", enabled_list=enabled_list)

        # User plugins (priority band 100-199)
        self._scan_dir(USER_PLUGINS_DIR, band="user", enabled_list=enabled_list)

        # Load enabled plugins
        loaded = 0
        for name, info in self._plugins.items():
            if info["enabled"]:
                self._load_plugin(name)
                loaded += 1

        logger.info(f"[PLUGINS] Scan complete: {len(self._plugins)} found, {loaded} loaded")

    def _scan_dir(self, directory: Path, band: str, enabled_list: list):
        """Scan a directory for plugin.json manifests."""
        if not directory.exists():
            return

        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[PLUGINS] Bad manifest in {child.name}: {e}")
                continue

            name = manifest.get("name", child.name)
            if not self._validate_manifest(name, manifest):
                continue

            # Enabled if in user config, or if manifest declares default_enabled
            is_enabled = name in enabled_list or manifest.get("default_enabled", False)

            # Verify signature on discovery (before any code loads)
            verified, verify_msg = verify_plugin(child)

            self._plugins[name] = {
                "manifest": manifest,
                "path": child,
                "enabled": is_enabled,
                "band": band,
                "loaded": False,
                "verified": verified,
                "verify_msg": verify_msg,
            }
            logger.debug(f"[PLUGINS] Found: {name} ({band}, enabled={is_enabled}, {verify_msg})")

    def _validate_manifest(self, name: str, manifest: dict) -> bool:
        """Basic manifest validation."""
        if "name" not in manifest:
            logger.warning(f"[PLUGINS] {name}: manifest missing 'name' field")
            return False
        return True

    def _get_enabled_list(self) -> list:
        """Read enabled plugins from user/webui/plugins.json."""
        for path in (USER_PLUGINS_JSON, STATIC_PLUGINS_JSON):
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return data.get("enabled", [])
                except Exception as e:
                    logger.warning(f"[PLUGINS] Failed to read {path}: {e}")
        return []

    def _load_plugin(self, name: str) -> bool:
        """Load an enabled plugin — check cached verification, register hooks, voice commands.
        Returns True if loaded, False if blocked."""
        info = self._plugins.get(name)
        if not info:
            return False

        # Use verification result from scan
        verified = info.get("verified", False)
        verify_msg = info.get("verify_msg", "unknown")

        if not verified:
            try:
                import config
                allow_unsigned = config.ALLOW_UNSIGNED_PLUGINS
            except Exception:
                allow_unsigned = False

            if verify_msg == "unsigned" and allow_unsigned:
                logger.warning(f"[PLUGINS] {name}: unsigned plugin (sideloading enabled)")
            elif verify_msg == "unsigned" and not allow_unsigned:
                logger.warning(f"[PLUGINS] BLOCKED {name}: unsigned plugin (sideloading disabled)")
                return False
            else:
                logger.error(f"[PLUGINS] BLOCKED {name}: {verify_msg}")
                return False
        else:
            logger.info(f"[PLUGINS] {name}: signature verified")

        manifest = info["manifest"]
        plugin_dir = info["path"]
        band = info["band"]
        base_priority = manifest.get("priority", 50)

        # Offset user plugins into 100-199 band
        if band == "user":
            base_priority = min(base_priority + 100, 199)

        capabilities = manifest.get("capabilities", {})

        # Register hooks
        hooks = capabilities.get("hooks", {})
        for hook_name, handler_path in hooks.items():
            handler_func = self._load_handler(plugin_dir, handler_path, hook_name)
            if handler_func:
                hook_runner.register(
                    hook_name, handler_func,
                    priority=base_priority,
                    plugin_name=name
                )

        # Register voice commands as auto-wired pre_chat hooks
        voice_commands = capabilities.get("voice_commands", [])
        for vc in voice_commands:
            handler_path = vc.get("handler")
            handler_func = self._load_handler(plugin_dir, handler_path, "pre_chat")
            if handler_func:
                voice_match = {
                    "triggers": vc.get("triggers", []),
                    "match": vc.get("match", "exact"),
                }
                # Voice commands that bypass LLM get highest priority in their band
                vc_priority = base_priority if not vc.get("bypass_llm") else min(base_priority, 19)
                if band == "user" and vc.get("bypass_llm"):
                    vc_priority = min(base_priority, 119)

                hook_runner.register(
                    "pre_chat", handler_func,
                    priority=vc_priority,
                    plugin_name=name,
                    voice_match=voice_match
                )

        # Register tools with FunctionManager
        tool_paths = capabilities.get("tools", [])
        if tool_paths and self._function_manager:
            self._function_manager.register_plugin_tools(name, plugin_dir, tool_paths)

        # Register scheduled tasks with continuity scheduler
        schedules = capabilities.get("schedule", [])
        if schedules and self._scheduler:
            task_ids = []
            for sched in schedules:
                try:
                    task = self._scheduler.create_task({
                        "name": sched.get("name", f"{name} task"),
                        "schedule": sched.get("cron", "0 9 * * *"),
                        "enabled": sched.get("enabled", True),
                        "chance": sched.get("chance", 100),
                        "initial_message": sched.get("description", "Plugin scheduled task"),
                        "source": f"plugin:{name}",
                        "handler": sched.get("handler", ""),
                        "plugin_dir": str(plugin_dir),
                    })
                    task_ids.append(task["id"])
                    logger.info(f"[PLUGINS] Registered schedule task '{sched.get('name')}' for {name}")
                except Exception as e:
                    logger.error(f"[PLUGINS] Failed to register schedule for {name}: {e}")
            info["schedule_task_ids"] = task_ids

        info["loaded"] = True
        logger.info(f"[PLUGINS] Loaded: {name} (priority {base_priority}, {band})")
        return True

    def _load_handler(self, plugin_dir: Path, handler_path: str, hook_name: str):
        """Import a Python handler from a plugin directory.

        Args:
            plugin_dir: Plugin root (e.g., plugins/stop/)
            handler_path: Relative path (e.g., "hooks/stop.py")
            hook_name: The hook this handler is for (used as function name to look up)

        Returns:
            Callable or None
        """
        if not handler_path:
            return None

        full_path = plugin_dir / handler_path
        if not full_path.exists():
            logger.warning(f"[PLUGINS] Handler not found: {full_path}")
            return None

        try:
            source = full_path.read_text(encoding="utf-8")
            namespace = {"__file__": str(full_path), "__name__": f"plugin_{plugin_dir.name}_{full_path.stem}"}
            exec(compile(source, str(full_path), "exec"), namespace)

            # Look for a function matching the hook name (e.g., pre_chat, prompt_inject)
            handler = namespace.get(hook_name)
            if handler and callable(handler):
                return handler

            # Fallback: look for a generic 'handle' function
            handler = namespace.get("handle")
            if handler and callable(handler):
                return handler

            logger.warning(f"[PLUGINS] No '{hook_name}' or 'handle' function in {full_path}")
            return None

        except Exception as e:
            logger.error(f"[PLUGINS] Failed to load handler {full_path}: {e}", exc_info=True)
            return None

    def unload_plugin(self, name: str):
        """Unload a plugin — deregister all hooks, tools, and schedule tasks."""
        hook_runner.unregister_plugin(name)
        if self._function_manager:
            self._function_manager.unregister_plugin_tools(name)
        # Remove plugin schedule tasks
        with self._lock:
            if self._scheduler and name in self._plugins:
                for tid in self._plugins[name].get("schedule_task_ids", []):
                    try:
                        self._scheduler.delete_task(tid)
                    except Exception as e:
                        logger.warning(f"[PLUGINS] Failed to delete schedule task {tid}: {e}")
                self._plugins[name].pop("schedule_task_ids", None)
            if name in self._plugins:
                self._plugins[name]["loaded"] = False
        logger.info(f"[PLUGINS] Unloaded: {name}")

    def reload_plugin(self, name: str):
        """Unload and reload a plugin. Safe — if reload fails, plugin stays unloaded.

        Also re-enables plugin tools in the active toolset.
        """
        self.unload_plugin(name)
        with self._lock:
            should_load = name in self._plugins and self._plugins[name]["enabled"]
        if should_load:
            try:
                self._load_plugin(name)
                # Re-enable tools in active toolset
                if self._function_manager:
                    current = self._function_manager.current_toolset_name
                    if current:
                        self._function_manager.update_enabled_functions([current])
                logger.info(f"[PLUGINS] Reloaded: {name}")
                from core.event_bus import publish, Events
                publish(Events.PLUGIN_RELOADED, {"plugin": name})
            except Exception as e:
                logger.error(f"[PLUGINS] Reload failed for {name}: {e}", exc_info=True)
                with self._lock:
                    if name in self._plugins:
                        self._plugins[name]["loaded"] = False

    def set_scheduler(self, scheduler):
        """Set the continuity scheduler for plugin schedule tasks.

        Also registers schedule tasks for plugins that were already loaded
        during scan() (before the scheduler existed).
        """
        self._scheduler = scheduler
        self._register_pending_schedules()

    def _register_pending_schedules(self):
        """Register schedule tasks for loaded plugins that missed registration during scan()."""
        if not self._scheduler:
            return
        for name, info in self._plugins.items():
            if not info.get("loaded"):
                continue
            if info.get("schedule_task_ids"):
                continue  # Already registered
            schedules = info["manifest"].get("capabilities", {}).get("schedule", [])
            if not schedules:
                continue
            plugin_dir = info["path"]
            task_ids = []
            for sched in schedules:
                try:
                    task = self._scheduler.create_task({
                        "name": sched.get("name", f"{name} task"),
                        "schedule": sched.get("cron", "0 9 * * *"),
                        "enabled": sched.get("enabled", True),
                        "chance": sched.get("chance", 100),
                        "initial_message": sched.get("description", "Plugin scheduled task"),
                        "source": f"plugin:{name}",
                        "handler": sched.get("handler", ""),
                        "plugin_dir": str(plugin_dir),
                    })
                    task_ids.append(task["id"])
                    logger.info(f"[PLUGINS] Deferred schedule registration: '{sched.get('name')}' for {name}")
                except Exception as e:
                    logger.error(f"[PLUGINS] Failed deferred schedule for {name}: {e}")
            info["schedule_task_ids"] = task_ids

    def rescan(self):
        """Scan for new plugins and clean up removed ones.

        Returns dict with 'added' and 'removed' plugin name lists.
        """
        enabled_list = self._get_enabled_list()
        new_found = []
        removed = []

        # Collect all plugin names currently on disk
        on_disk = set()
        for directory, band in [(SYSTEM_PLUGINS_DIR, "system"), (USER_PLUGINS_DIR, "user")]:
            if not directory.exists():
                continue
            for child in sorted(directory.iterdir()):
                if not child.is_dir():
                    continue
                manifest_path = child / "plugin.json"
                if not manifest_path.exists():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                name = manifest.get("name", child.name)
                on_disk.add(name)

                with self._lock:
                    if name in self._plugins:
                        continue

                if not self._validate_manifest(name, manifest):
                    continue

                with self._lock:
                    self._plugins[name] = {
                        "manifest": manifest,
                        "path": child,
                        "enabled": name in enabled_list,
                        "band": band,
                        "loaded": False,
                    }
                new_found.append(name)

                if name in enabled_list:
                    self._load_plugin(name)
                    logger.info(f"[PLUGINS] Rescan: loaded new plugin '{name}'")

        # Detect removed plugins (folder deleted while running)
        with self._lock:
            for name in list(self._plugins.keys()):
                if name not in on_disk:
                    removed.append(name)

        for name in removed:
            logger.info(f"[PLUGINS] Rescan: plugin '{name}' removed from disk, unloading")
            self.unload_plugin(name)
            with self._lock:
                self._plugins.pop(name, None)

        if new_found or removed:
            logger.info(f"[PLUGINS] Rescan: {len(new_found)} added, {len(removed)} removed")
        return {"added": new_found, "removed": removed}

    # ── Query methods ──

    def get_plugin_names(self) -> List[str]:
        """All discovered plugin names."""
        return list(self._plugins.keys())

    def get_enabled_plugins(self) -> List[str]:
        """Names of enabled plugins."""
        return [n for n, info in self._plugins.items() if info["enabled"]]

    def get_loaded_plugins(self) -> List[str]:
        """Names of currently loaded plugins."""
        return [n for n, info in self._plugins.items() if info.get("loaded")]

    def get_plugin_info(self, name: str) -> Optional[dict]:
        """Get plugin info dict (manifest, path, enabled, band)."""
        info = self._plugins.get(name)
        if not info:
            return None
        return {
            "name": name,
            "manifest": info["manifest"],
            "path": str(info["path"]),
            "enabled": info["enabled"],
            "band": info["band"],
            "loaded": info.get("loaded", False),
            "verified": info.get("verified"),
            "verify_msg": info.get("verify_msg"),
        }

    def get_all_plugin_info(self) -> List[dict]:
        """Get info for all discovered plugins."""
        return [self.get_plugin_info(n) for n in self._plugins]

    def get_plugin_state(self, name: str) -> PluginState:
        """Get the PluginState helper for a plugin."""
        return PluginState(name)

    # ── File watcher (dev mode) ──

    def start_watcher(self):
        """Start mtime-based file watcher for loaded plugins. Dev mode only."""
        if self._watcher_running:
            return
        self._watcher_running = True
        self._watcher_thread = threading.Thread(
            target=self._watcher_loop, daemon=True, name="PluginFileWatcher"
        )
        self._watcher_thread.start()
        logger.info("[PLUGINS] File watcher started (dev mode)")

    def stop_watcher(self):
        """Stop the file watcher."""
        self._watcher_running = False
        if self._watcher_thread:
            self._watcher_thread.join(timeout=5)
            self._watcher_thread = None

    def _watcher_loop(self):
        """Poll loaded plugin dirs for file changes, reload on change."""
        import time as _time

        # Snapshot initial mtimes
        mtimes: Dict[str, float] = {}
        with self._lock:
            snapshot = list(self._plugins.items())
        for name, info in snapshot:
            if info.get("loaded"):
                mtimes[name] = self._dir_mtime(info["path"])

        while self._watcher_running:
            _time.sleep(2)
            with self._lock:
                snapshot = list(self._plugins.items())
            for name, info in snapshot:
                if not info.get("loaded"):
                    # Track newly loaded plugins so first poll doesn't spuriously reload
                    if name not in mtimes and info["path"].exists():
                        mtimes[name] = self._dir_mtime(info["path"])
                    continue
                current = self._dir_mtime(info["path"])
                prev = mtimes.get(name, 0)
                if prev == 0:
                    # First time seeing this plugin loaded — snapshot, don't reload
                    mtimes[name] = current
                    continue
                if current > prev:
                    logger.info(f"[PLUGINS] File change detected in '{name}', reloading...")
                    self.reload_plugin(name)
                    mtimes[name] = self._dir_mtime(info["path"])

    @staticmethod
    def _dir_mtime(path: Path) -> float:
        """Get max mtime of all .py and .json files in a directory tree."""
        max_mtime = 0.0
        try:
            for f in path.rglob("*"):
                if f.suffix in (".py", ".json") and f.is_file():
                    mt = f.stat().st_mtime
                    if mt > max_mtime:
                        max_mtime = mt
        except Exception:
            pass
        return max_mtime


# Singleton
plugin_loader = PluginLoader()
