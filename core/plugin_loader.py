# core/plugin_loader.py — Plugin discovery, loading, and lifecycle
#
# Scans plugins/ and user/plugins/ for plugin.json manifests.
# Registers hooks, voice commands, and (later) tools/web/schedule.

import json
import logging
import os
import re
import shutil
import stat
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple

from core.hooks import hook_runner
from core.plugin_verify import verify_plugin

logger = logging.getLogger(__name__)


def _rmtree_robust(path):
    """shutil.rmtree that survives Windows read-only files.

    Plain `shutil.rmtree` crashes on Windows the first time it hits a
    read-only file (.pyc, git-set permissions, AV-locked caches). The
    onerror handler clears the read-only bit and retries the single
    delete — if that still fails, we swallow the exception so a broken
    file doesn't abort the whole uninstall and leave a half-deleted tree.
    """
    def _on_error(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception as e:
            logger.warning(f"[PLUGINS] rmtree could not remove {p}: {e}")
    shutil.rmtree(path, onerror=_on_error)

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
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                # Quarantine the corrupted file before returning empty dict —
                # otherwise the next save() silently overwrites whatever was
                # salvageable (often the only copy of plugin auth/state).
                from datetime import datetime
                quarantine = self._path.with_suffix(
                    f'.json.bad-{datetime.utcnow().strftime("%Y%m%dT%H%M%S")}'
                )
                try:
                    self._path.rename(quarantine)
                    logger.error(
                        f"[PLUGIN-STATE] Corrupted state file for '{self._name}': {e}. "
                        f"Quarantined to {quarantine.name}; starting with empty state."
                    )
                except Exception as rename_err:
                    logger.error(
                        f"[PLUGIN-STATE] Corrupted {self._path}: {e}. "
                        f"Could not quarantine ({rename_err}); next save will overwrite."
                    )
        return {}

    def _save(self):
        PLUGIN_STATE_DIR.mkdir(parents=True, exist_ok=True)
        # Unique tmp suffix per-process so concurrent writers can't truncate
        # each other's tmp file before rename. Lock ordering still matters
        # for content correctness (use update_with_lock for RMW), but this
        # ensures the on-disk tmp never collides.
        import os as _os
        tmp = self._path.with_suffix(f'.json.tmp.{_os.getpid()}.{id(self):x}')
        try:
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        finally:
            if tmp.exists():
                try: tmp.unlink()
                except Exception: pass

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def save(self, key: str, value):
        with self._lock:
            self._data[key] = value
            self._save()

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)
            self._save()

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)

    def clear(self):
        with self._lock:
            self._data = {}
            self._save()

    def update_with_lock(self, key: str, mutator, default=None):
        """Atomic read-modify-write for a single key.

        mutator(current_value) -> new_value, called under self._lock so two
        concurrent updates can't clobber each other (the bug family that hit
        MCP, discord, telegram — read the dict, mutate, save, second writer
        loses the first writer's change).

        Returns the new value.
        """
        with self._lock:
            current = self._data.get(key, default)
            new_value = mutator(current)
            self._data[key] = new_value
            self._save()
            return new_value


class PluginLoader:
    """Discovers, validates, and loads plugins from plugins/ and user/plugins/."""

    def __init__(self):
        # {plugin_name: {manifest, path, enabled, band, state}}
        self._plugins: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_errors: list = []  # Accumulates startup errors for frontend display
        self._function_manager = None  # Set via scan() for plugin tool loading
        self._scheduler = None  # Set via set_scheduler() for plugin schedule tasks
        self._watcher_running = False
        self._watcher_thread = None
        # Route registry: {plugin_name: [(method, compiled_regex, param_names, handler_func), ...]}
        self._routes: Dict[str, list] = {}
        # Daemon event source registry: {plugin_name: [source_defs]}
        self._event_sources: Dict[str, list] = {}
        # Daemon reply handlers: {plugin_name: callable(task, event_data_dict, response_text)}
        self._reply_handlers: Dict[str, Callable] = {}
        # Per-plugin reload locks — serializes reload against concurrent toggle/watcher
        self._reload_locks: Dict[str, threading.Lock] = {}
        self._reload_locks_lock = threading.Lock()

    def _is_managed(self):
        """Check if running in managed/Docker mode (single source of truth)."""
        from core.settings_manager import settings
        return settings.is_managed()

    def scan(self, function_manager=None):
        """Discover all plugins and load enabled ones.

        Args:
            function_manager: Optional FunctionManager for plugin tool registration.
        """
        self._function_manager = function_manager
        self._plugins.clear()
        enabled_list = self._get_enabled_list()
        disabled_list = self._get_disabled_list()

        # System plugins (priority band 0-99)
        self._scan_dir(SYSTEM_PLUGINS_DIR, band="system", enabled_list=enabled_list, disabled_list=disabled_list)

        # User plugins (priority band 100-199)
        self._scan_dir(USER_PLUGINS_DIR, band="user", enabled_list=enabled_list, disabled_list=disabled_list)

        # Load enabled plugins
        loaded = 0
        blocked = []
        for name, info in self._plugins.items():
            if info["enabled"]:
                if self._load_plugin(name):
                    loaded += 1
                else:
                    # Plugin failed verification/load. Mark in-memory enabled=False
                    # so UI and toolset registration reflect reality. DO NOT remove
                    # from user's enabled list on disk — user intent survives
                    # temporary failures (e.g. signing regressions, missing deps).
                    # Next restart with the underlying issue fixed = loads clean,
                    # no UI click needed.
                    info["enabled"] = False
                    blocked.append(name)

        if blocked:
            logger.warning(f"[PLUGINS] Enabled but blocked (intent preserved, retry next restart): {blocked}")

        logger.info(f"[PLUGINS] Scan complete: {len(self._plugins)} found, {loaded} loaded")

    def _scan_dir(self, directory: Path, band: str, enabled_list: list, disabled_list: list = None):
        """Scan a directory for plugin.json manifests."""
        if not directory.exists():
            return
        if disabled_list is None:
            disabled_list = []

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

            # Skip plugins hidden in managed mode
            if manifest.get("managed_hide") and self._is_managed():
                logger.debug(f"[PLUGINS] Skipping {name} (managed_hide)")
                continue

            # Enabled if user enabled it, OR manifest default_enabled AND user didn't explicitly disable it
            is_enabled = (name in enabled_list) or (
                manifest.get("default_enabled", False) and name not in disabled_list
            )

            # Verify signature on discovery (before any code loads)
            verified, verify_msg, verify_meta = verify_plugin(child)

            try:
                manifest_mtime = manifest_path.stat().st_mtime
            except Exception:
                manifest_mtime = 0

            self._plugins[name] = {
                "manifest": manifest,
                "path": child,
                "enabled": is_enabled,
                "band": band,
                "loaded": False,
                "verified": verified,
                "verify_msg": verify_msg,
                "verify_tier": verify_meta.get("tier", "unsigned"),
                "verified_author": verify_meta.get("author"),
                "_manifest_mtime": manifest_mtime,
            }
            logger.debug(f"[PLUGINS] Found: {name} ({band}, enabled={is_enabled}, {verify_msg})")

    def _validate_manifest(self, name: str, manifest: dict) -> bool:
        """Basic manifest validation."""
        if "name" not in manifest:
            logger.warning(f"[PLUGINS] {name}: manifest missing 'name' field")
            return False
        return True

    @staticmethod
    def _check_dependencies(manifest: dict) -> list:
        """Check if pip_dependencies from manifest are installed.

        Returns list of missing package specifiers (empty = all good).
        """
        deps = manifest.get("pip_dependencies", [])
        if not deps:
            return []

        import importlib.metadata
        import re

        missing = []
        for spec in deps:
            # Extract package name from specifier like "telethon>=1.34"
            pkg_name = re.split(r'[><=!~\[]', spec)[0].strip()
            if not pkg_name:
                continue
            try:
                importlib.metadata.version(pkg_name)
            except importlib.metadata.PackageNotFoundError:
                missing.append(spec)
        return missing

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

    def _get_disabled_list(self) -> list:
        """Read explicitly-disabled plugins (overrides default_enabled)."""
        for path in (USER_PLUGINS_JSON, STATIC_PLUGINS_JSON):
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return data.get("disabled", [])
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
            if verify_msg != "unsigned":
                # Tampered signature — always block
                logger.error(f"[PLUGINS] BLOCKED {name}: {verify_msg}")
                return False

            try:
                import config
                allow_unsigned = config.ALLOW_UNSIGNED_PLUGINS
            except Exception as e:
                logger.warning(f"[PLUGINS] Could not read ALLOW_UNSIGNED_PLUGINS: {e}")
                allow_unsigned = False

            if allow_unsigned:
                logger.warning(f"[PLUGINS] {name}: unsigned plugin (sideloading enabled)")
            elif self._is_managed():
                # Managed mode: validate code instead of requiring signature
                from core.code_validator import validate_plugin_files
                ok, err = validate_plugin_files(info["path"], strictness='strict')
                if ok:
                    logger.info(f"[PLUGINS] {name}: unsigned but passed strict validation")
                    info["verify_tier"] = "validated"
                else:
                    logger.warning(f"[PLUGINS] BLOCKED {name}: failed code validation — {err}")
                    return False
            else:
                logger.warning(f"[PLUGINS] BLOCKED {name}: unsigned plugin (sideloading disabled)")
                return False
        else:
            logger.info(f"[PLUGINS] {name}: signature verified")

        manifest = info["manifest"]
        plugin_dir = info["path"]
        band = info["band"]
        base_priority = manifest.get("priority", 50)

        # Pre-flight dependency check — before any code loads
        missing = self._check_dependencies(manifest)
        info.pop("missing_deps", None)  # Clear stale dep state on reload
        if missing:
            info["missing_deps"] = missing
            pip_cmd = f"pip install {' '.join(missing)}"
            logger.warning(f"[PLUGINS] {name}: missing dependencies: {missing} — {pip_cmd}")
            err_data = {
                "plugin": name,
                "error": f"Missing dependencies: {', '.join(missing)}",
                "hint": pip_cmd,
                "missing_deps": missing,
            }
            self._load_errors.append(err_data)
            from core.event_bus import publish, Events
            publish(Events.PLUGIN_LOAD_ERROR, err_data)
            # Stay enabled but not loaded — user can install deps and reload
            return True  # Don't block/disable, just skip loading code

        # Offset user plugins into 100-199 band
        if band == "user":
            base_priority = min(base_priority + 100, 199)

        capabilities = manifest.get("capabilities", {})

        # Register scope ContextVars BEFORE daemons and tools (Phase 3).
        # Plugin tool and daemon modules may do `from core.chat.function_manager
        # import scope_email` (etc.) at call time; those imports resolve via
        # function_manager.__getattr__ which looks up SCOPE_REGISTRY. If the
        # scope hasn't been registered yet, the import raises AttributeError.
        # Order MUST be: scope register → daemons → tools → hooks → routes.
        scope_defs = capabilities.get("scopes", [])
        if scope_defs:
            try:
                from core.chat.function_manager import register_plugin_scope
                for scope_def in scope_defs:
                    key = scope_def.get("key")
                    if not key:
                        continue
                    # Manifest can override the default value (e.g. None for disabled-by-default)
                    default = scope_def.get("default", "default")
                    register_plugin_scope(key, plugin_name=name, default=default)
            except Exception as e:
                logger.error(f"[PLUGINS] {name}: failed to register scopes: {e}", exc_info=True)

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

        # Register HTTP routes
        routes = capabilities.get("routes", [])
        if routes:
            self._register_routes(name, plugin_dir, routes)

        # Register providers (TTS, STT, Embedding, LLM)
        providers_decl = capabilities.get("providers", {})
        if providers_decl:
            registered = []
            for system_name, prov_config in providers_decl.items():
                registry = self._get_provider_registry(system_name)
                if not registry:
                    logger.warning(f"[PLUGINS] Unknown provider system '{system_name}' in {name}")
                    continue
                entry_file = prov_config.get("entry", "provider.py")
                class_name = prov_config.get("class_name")
                prov_key = prov_config.get("key", name)
                display_name = prov_config.get("display_name", name)
                if not class_name:
                    logger.warning(f"[PLUGINS] Provider in {name} missing class_name for {system_name}")
                    continue
                provider_path = plugin_dir / entry_file
                if not provider_path.exists():
                    logger.warning(f"[PLUGINS] Provider file not found: {provider_path}")
                    continue
                try:
                    ns = {"__file__": str(provider_path), "__name__": f"plugin_provider_{name}_{system_name}"}
                    exec(compile(provider_path.read_text(encoding='utf-8'), str(provider_path), 'exec'), ns)
                    provider_class = ns.get(class_name)
                    if not provider_class:
                        logger.error(f"[PLUGINS] Class '{class_name}' not found in {provider_path}")
                        continue
                    # Pass through extra metadata
                    extra = {k: v for k, v in prov_config.items()
                             if k not in ('entry', 'class_name', 'key', 'display_name')}
                    registry.register_plugin(prov_key, provider_class, display_name, name, **extra)
                    registered.append((system_name, prov_key))
                except Exception as e:
                    logger.error(f"[PLUGINS] Failed to load provider {class_name} for {name}: {e}", exc_info=True)
            if registered:
                info["registered_providers"] = registered
                logger.info(f"[PLUGINS] {name}: registered {len(registered)} provider(s)")

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

        # Register daemon event sources
        daemon_config = capabilities.get("daemon", {})
        if daemon_config:
            event_sources = daemon_config.get("event_sources", [])
            if event_sources:
                with self._lock:
                    self._event_sources[name] = [{
                        "name": src.get("name", f"{name}_event"),
                        "label": src.get("label", src.get("name", name)),
                        "plugin": name,
                        "filter_fields": src.get("filter_fields", []),
                        "task_fields": src.get("task_fields", []),
                        "description": src.get("description", ""),
                    } for src in event_sources]
                logger.info(f"[PLUGINS] Registered {len(event_sources)} event source(s) for {name}")

            # Load daemon module (start is deferred until scheduler is ready)
            daemon_entry = daemon_config.get("entry")
            if daemon_entry:
                try:
                    daemon_mod = self._load_daemon_module(plugin_dir, daemon_entry)
                    if daemon_mod and hasattr(daemon_mod, "start"):
                        info["daemon_module"] = daemon_mod
                        if self._scheduler:
                            # Scheduler already set — start immediately
                            settings = self.get_plugin_settings(name)
                            daemon_mod.start(self, settings)
                            info["daemon_started"] = True
                            logger.info(f"[PLUGINS] Started daemon thread for {name}")
                        else:
                            logger.info(f"[PLUGINS] Daemon for {name} deferred until scheduler ready")
                except ModuleNotFoundError as e:
                    logger.error(f"[PLUGINS] Missing dependency for daemon '{name}': {e}")
                    err_data = {
                        "plugin": name, "error": f"Missing pip package: {e.name or e}",
                        "hint": f"pip install {e.name}" if e.name else str(e)
                    }
                    self._load_errors.append(err_data)
                    from core.event_bus import publish, Events
                    publish(Events.PLUGIN_LOAD_ERROR, err_data)
                except Exception as e:
                    logger.error(f"[PLUGINS] Failed to load daemon for {name}: {e}", exc_info=True)

        info["loaded"] = True

        # Seed default settings if manifest declares schema and no settings file exists
        settings_schema = capabilities.get("settings", [])
        if settings_schema:
            settings_file = PROJECT_ROOT / "user" / "webui" / "plugins" / f"{name}.json"
            if not settings_file.exists():
                defaults = {f["key"]: f["default"] for f in settings_schema if "key" in f and "default" in f}
                if defaults:
                    settings_file.parent.mkdir(parents=True, exist_ok=True)
                    tmp = settings_file.with_suffix('.json.tmp')
                    tmp.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
                    tmp.replace(settings_file)
                    logger.debug(f"[PLUGINS] Seeded default settings for {name}")

        logger.info(f"[PLUGINS] Loaded: {name} (priority {base_priority}, {band})")
        return True

    def _load_daemon_module(self, plugin_dir: Path, entry_path: str):
        """Load a daemon module from a plugin. Returns the module namespace."""
        full_path = plugin_dir / entry_path
        try:
            full_path.resolve().relative_to(plugin_dir.resolve())
        except ValueError:
            logger.error(f"[PLUGINS] Path traversal blocked in daemon entry: {entry_path}")
            return None
        if not full_path.exists():
            logger.warning(f"[PLUGINS] Daemon entry not found: {full_path}")
            return None

        try:
            import importlib.util
            import sys

            # Derive the natural package import path so that tools doing
            # "from plugins.telegram.daemon import X" find this same module
            # instead of importing a second copy with separate state.
            try:
                rel = full_path.resolve().relative_to(Path.cwd())
                pkg_name = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
            except ValueError:
                pkg_name = f"plugin_daemon_{plugin_dir.name}"

            spec = importlib.util.spec_from_file_location(pkg_name, str(full_path))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[pkg_name] = mod
            spec.loader.exec_module(mod)
            mod._pkg_name = pkg_name  # For sys.modules cleanup on unload
            return mod
        except Exception as e:
            logger.error(f"[PLUGINS] Failed to load daemon module {full_path}: {e}", exc_info=True)
            return None

    def _load_handler(self, plugin_dir: Path, handler_path: str, hook_name: str, ns_cache: dict = None):
        """Import a Python handler from a plugin directory.

        Args:
            plugin_dir: Plugin root (e.g., plugins/stop/)
            handler_path: Relative path (e.g., "hooks/stop.py")
            hook_name: The hook this handler is for (used as function name to look up)
            ns_cache: Optional dict to cache namespaces per file path (for shared state)

        Returns:
            Callable or None
        """
        if not handler_path:
            return None

        full_path = plugin_dir / handler_path
        try:
            full_path.resolve().relative_to(plugin_dir.resolve())
        except ValueError:
            logger.error(f"[PLUGINS] Path traversal blocked in handler: {handler_path}")
            return None
        if not full_path.exists():
            logger.warning(f"[PLUGINS] Handler not found: {full_path}")
            return None

        try:
            source = full_path.read_text(encoding="utf-8")
            namespace = {"__file__": str(full_path), "__name__": f"plugin_{plugin_dir.name}_{full_path.stem}"}
            exec(compile(source, str(full_path), "exec"), namespace)

            # Cache namespace so other handlers from the same file share module-level state
            if ns_cache is not None:
                ns_cache[handler_path] = namespace

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

    def _get_provider_registry(self, system_name: str):
        """Get the provider registry for a system (tts, stt, embedding, llm)."""
        try:
            if system_name == 'tts':
                from core.tts.providers import tts_registry
                return tts_registry
            elif system_name == 'stt':
                from core.stt.providers import stt_registry
                return stt_registry
            elif system_name == 'embedding':
                from core.embeddings import embedding_registry
                return embedding_registry
            elif system_name == 'llm':
                from core.chat.llm_providers import provider_registry
                return provider_registry
        except ImportError as e:
            logger.error(f"[PLUGINS] Failed to import {system_name} provider registry: {e}")
        return None

    def unload_plugin(self, name: str):
        """Unload a plugin — deregister all hooks, tools, routes, providers, schedule tasks, event sources, and scopes."""
        hook_runner.unregister_plugin(name)
        if self._function_manager:
            self._function_manager.unregister_plugin_tools(name)
        self._unregister_routes(name)

        # Unregister scopes this plugin contributed so a later manifest edit
        # with a different default takes effect on re-register instead of
        # hitting register_plugin_scope's idempotent early-return.
        try:
            info_for_scopes = self._plugins.get(name, {})
            scope_defs = info_for_scopes.get("manifest", {}).get("capabilities", {}).get("scopes", [])
            if scope_defs:
                from core.chat.function_manager import unregister_plugin_scope
                for sd in scope_defs:
                    key = sd.get("key")
                    if key:
                        unregister_plugin_scope(key)
        except Exception as e:
            logger.warning(f"[PLUGINS] {name}: failed to unregister scopes: {e}")

        # Unregister providers — reset active setting if it pointed to this plugin
        info = self._plugins.get(name, {})
        for system_name, prov_key in info.get("registered_providers", []):
            registry = self._get_provider_registry(system_name)
            if registry:
                if registry.get_active_key() == prov_key:
                    from core.settings_manager import settings_manager
                    settings_manager.set(registry.setting_key, 'none')
                    logger.info(f"[PLUGINS] Reset {registry.setting_key} to 'none' (was '{prov_key}' from disabled plugin)")
                registry.unregister_plugin(name)
        # Remove plugin schedule tasks and event sources
        # Snapshot daemon_mod under lock, then stop OUTSIDE lock to avoid
        # ABBA deadlock: _lock → _lifecycle_lock vs _lifecycle_lock → _lock
        daemon_mod = None
        with self._lock:
            if self._scheduler and name in self._plugins:
                for tid in self._plugins[name].get("schedule_task_ids", []):
                    try:
                        self._scheduler.delete_task(tid)
                    except Exception as e:
                        logger.warning(f"[PLUGINS] Failed to delete schedule task {tid}: {e}")
                self._plugins[name].pop("schedule_task_ids", None)
            self._event_sources.pop(name, None)
            self._reply_handlers.pop(name, None)
            if name in self._plugins:
                daemon_mod = self._plugins[name].get("daemon_module")

        # Stop daemon outside _lock (daemon.stop() acquires _lifecycle_lock)
        if daemon_mod and hasattr(daemon_mod, "stop"):
            try:
                daemon_mod.stop()
                logger.info(f"[PLUGINS] Stopped daemon for {name}")
            except Exception as e:
                logger.warning(f"[PLUGINS] Failed to stop daemon for {name}: {e}")
            # Clean sys.modules so file handles are released (needed for Windows rmtree)
            pkg = getattr(daemon_mod, "_pkg_name", None)
            if pkg:
                import sys
                sys.modules.pop(pkg, None)

        # Finalize state under lock
        with self._lock:
            if name in self._plugins:
                self._plugins[name].pop("daemon_module", None)
                self._plugins[name]["loaded"] = False
        logger.info(f"[PLUGINS] Unloaded: {name}")

    def enforce_unsigned_policy(self) -> list:
        """Unload and disable any enabled unsigned plugins. Returns list of names affected."""
        affected = []
        with self._lock:
            candidates = [
                (name, info) for name, info in self._plugins.items()
                if info.get("enabled") and not info.get("verified")
                and info.get("verify_msg") == "unsigned"
            ]
        for name, info in candidates:
            if info.get("loaded"):
                self.unload_plugin(name)
            with self._lock:
                info["enabled"] = False
            affected.append(name)
            logger.info(f"[PLUGINS] Unsigned policy: disabled '{name}'")

        # Remove from enabled list on disk
        if affected:
            self._remove_from_enabled_list(affected)

        return affected

    def _remove_from_enabled_list(self, names: list):
        """Remove plugin names from the persisted enabled list."""
        if not USER_PLUGINS_JSON.exists():
            return
        try:
            data = json.loads(USER_PLUGINS_JSON.read_text(encoding="utf-8"))
            enabled = data.get("enabled", [])
            data["enabled"] = [n for n in enabled if n not in names]
            tmp_path = USER_PLUGINS_JSON.with_suffix('.tmp')
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(USER_PLUGINS_JSON)
        except Exception as e:
            logger.warning(f"[PLUGINS] Failed to update enabled list: {e}")

    def _get_reload_lock(self, name: str) -> threading.Lock:
        with self._reload_locks_lock:
            if name not in self._reload_locks:
                self._reload_locks[name] = threading.Lock()
            return self._reload_locks[name]

    def reload_plugin(self, name: str):
        """Unload and reload a plugin. Safe — if reload fails, plugin stays unloaded.

        Re-reads manifest from disk so code/settings changes take effect.
        Re-verifies signature to catch tampering since initial scan.
        Also re-enables plugin tools in the active toolset.
        Per-plugin lock prevents concurrent reload/toggle races.
        """
        with self._get_reload_lock(name):
            self.unload_plugin(name)
            with self._lock:
                should_load = name in self._plugins and self._plugins[name]["enabled"]
                if name in self._plugins:
                    plugin_path = self._plugins[name]["path"]
                    # Re-read manifest from disk (tool code or settings may have changed)
                    manifest_path = plugin_path / "plugin.json"
                    if manifest_path.exists():
                        try:
                            self._plugins[name]["manifest"] = json.loads(
                                manifest_path.read_text(encoding="utf-8")
                            )
                        except Exception as e:
                            logger.warning(f"[PLUGINS] Failed to re-read manifest for {name}: {e}")
                    # Re-verify signature (code may have been tampered with since scan)
                    verified, verify_msg, verify_meta = verify_plugin(plugin_path)
                    self._plugins[name]["verified"] = verified
                    self._plugins[name]["verify_msg"] = verify_msg
                    self._plugins[name]["verified_author"] = verify_meta.get("author")
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

    def uninstall_plugin(self, name: str):
        """Fully remove a user plugin — unload, delete files, settings, and state."""
        info = self._plugins.get(name)
        if not info:
            raise ValueError(f"Unknown plugin: {name}")
        if info["band"] != "user":
            raise ValueError(f"Cannot uninstall system plugin: {name}")

        # Unload if loaded
        if info.get("loaded"):
            self.unload_plugin(name)

        # Remove from internal dict
        with self._lock:
            self._plugins.pop(name, None)

        # Remove from enabled list on disk
        self._remove_from_enabled_list([name])

        # Delete plugin directory (use actual path, not name — they may differ)
        plugin_dir = info["path"] if info else USER_PLUGINS_DIR / name
        if plugin_dir.exists():
            _rmtree_robust(plugin_dir)

        # Delete settings
        settings_file = PROJECT_ROOT / "user" / "webui" / "plugins" / f"{name}.json"
        settings_file.unlink(missing_ok=True)

        # Delete state
        state_file = PLUGIN_STATE_DIR / f"{name}.json"
        state_file.unlink(missing_ok=True)

        # Sweep sibling files/dirs in plugin_state whose name starts with
        # the plugin name (+ '-' or '_' separator). Catches conventions
        # like `{name}-logs/`, `{name}_sessions/`, `{name}-sessions.json`.
        # Without this, e.g. uninstalling telegram leaves
        # `telegram_sessions/` with live credentials on disk (H5).
        try:
            for prefix in (f"{name}-", f"{name}_"):
                for extra in PLUGIN_STATE_DIR.glob(f"{prefix}*"):
                    try:
                        if extra.is_dir():
                            _rmtree_robust(extra)
                        else:
                            extra.unlink()
                        logger.info(f"[PLUGINS] Uninstall: removed sibling {extra.name}")
                    except Exception as e:
                        logger.warning(f"[PLUGINS] Could not remove {extra}: {e}")
        except Exception as e:
            logger.warning(f"[PLUGINS] Uninstall sibling sweep failed: {e}")

        # Manifest-declared extra cleanup paths — for plugins whose state
        # files/dirs DON'T follow the `{name}-*` convention (e.g. Google
        # Calendar's `gcal-csrf.json`). Paths are relative to project root
        # or user/; we resolve inside user/ and refuse anything that
        # escapes that sandbox.
        try:
            manifest = info.get("manifest", {}) if info else {}
            extra_paths = manifest.get("capabilities", {}).get("cleanup_paths", [])
            user_root = (PROJECT_ROOT / "user").resolve()
            for rel in extra_paths:
                if not isinstance(rel, str):
                    continue
                candidate = (user_root / rel).resolve()
                try:
                    candidate.relative_to(user_root)
                except ValueError:
                    logger.warning(f"[PLUGINS] Uninstall cleanup refused path outside user/: {rel}")
                    continue
                if candidate.exists():
                    try:
                        if candidate.is_dir():
                            _rmtree_robust(candidate)
                        else:
                            candidate.unlink()
                        logger.info(f"[PLUGINS] Uninstall: removed declared {rel}")
                    except Exception as e:
                        logger.warning(f"[PLUGINS] Could not remove {candidate}: {e}")
        except Exception as e:
            logger.warning(f"[PLUGINS] Uninstall manifest-cleanup failed: {e}")

        # Evict cached PluginState so reinstall gets fresh instance
        with self._plugin_state_cache_lock:
            self._plugin_state_cache.pop(name, None)

        logger.info(f"[PLUGINS] Uninstalled: {name}")

    def set_scheduler(self, scheduler):
        """Set the continuity scheduler for plugin schedule tasks.

        Also registers schedule tasks for plugins that were already loaded
        during scan() (before the scheduler existed).
        """
        self._scheduler = scheduler
        self._register_pending_schedules()
        self._start_pending_daemons()
        self._reactivate_plugin_providers()

    def _register_pending_schedules(self):
        """Register schedule tasks for loaded plugins that missed registration during scan()."""
        if not self._scheduler:
            return
        with self._lock:
            snapshot = list(self._plugins.items())
        for name, info in snapshot:
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

    def _reactivate_plugin_providers(self):
        """Re-trigger provider switches for TTS/STT if they point to a now-available plugin provider.

        At boot, system init runs before plugins load. If TTS_PROVIDER=elevenlabs
        but the plugin hasn't registered yet, TTS falls back to null. After plugins
        load and set_scheduler is called (from start_server), we re-check and switch
        if the provider is now available.
        """
        try:
            import config as cfg
            from core.api_fastapi import get_system
            try:
                system = get_system()
            except Exception:
                logger.debug("[PLUGINS] Provider reactivation skipped: system not available yet")
                return

            # TTS
            tts_key = getattr(cfg, 'TTS_PROVIDER', 'none')
            if tts_key and tts_key != 'none':
                from core.tts.providers import tts_registry
                if tts_registry.has_key(tts_key):
                    current = getattr(system.tts, 'provider', None)
                    from core.tts.providers.null import NullTTSProvider
                    if isinstance(current, NullTTSProvider) or current is None:
                        logger.info(f"[PLUGINS] Re-activating TTS provider '{tts_key}' (was null at boot)")
                        system.switch_tts_provider(tts_key)
                        # switch_tts_provider now re-applies chat settings
                        # internally (Wolf-Claude finding 2026-04-21), so the
                        # persona voice survives plugin-driven late activation
                        # without a second call here.
                        # Notify frontend so voice dropdown and speed range refresh
                        try:
                            from core.event_bus import publish, Events
                            publish(Events.SETTINGS_CHANGED, {"key": "TTS_PROVIDER", "value": tts_key})
                        except Exception:
                            pass

            # STT
            stt_key = getattr(cfg, 'STT_PROVIDER', 'none')
            if stt_key and stt_key != 'none':
                from core.stt.providers import stt_registry
                if stt_registry.has_key(stt_key):
                    from core.stt.stt_null import NullWhisperClient
                    current = getattr(system, 'whisper_client', None)
                    if isinstance(current, NullWhisperClient) or current is None:
                        logger.info(f"[PLUGINS] Re-activating STT provider '{stt_key}' (was null at boot)")
                        system.switch_stt_provider(stt_key)

        except Exception as e:
            logger.debug(f"[PLUGINS] Provider reactivation skipped: {e}")

    def _start_pending_daemons(self):
        """Start daemon threads for plugins that were loaded before scheduler was ready."""
        with self._lock:
            snapshot = list(self._plugins.items())
        for name, info in snapshot:
            if not info.get("loaded") or not info.get("daemon_module"):
                continue
            if info.get("daemon_started"):
                continue
            daemon_mod = info["daemon_module"]
            try:
                settings = self.get_plugin_settings(name)
                daemon_mod.start(self, settings)
                info["daemon_started"] = True
                logger.info(f"[PLUGINS] Started deferred daemon for {name}")
            except Exception as e:
                logger.error(f"[PLUGINS] Failed to start deferred daemon for {name}: {e}", exc_info=True)

    def rescan(self):
        """Scan for new plugins and clean up removed ones.

        Returns dict with 'added' and 'removed' plugin name lists.
        """
        enabled_list = self._get_enabled_list()
        disabled_list = self._get_disabled_list()
        new_found = []
        removed = []
        needs_reload = []

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
                        # Check if manifest changed on disk (mtime)
                        existing = self._plugins[name]
                        try:
                            disk_mtime = manifest_path.stat().st_mtime
                            cached_mtime = existing.get("_manifest_mtime", 0)
                            if disk_mtime > cached_mtime and existing.get("loaded"):
                                needs_reload.append(name)
                        except Exception:
                            pass
                        continue

                    if not self._validate_manifest(name, manifest):
                        continue

                    # Skip plugins hidden in managed mode
                    if manifest.get("managed_hide") and self._is_managed():
                        logger.debug(f"[PLUGINS] Rescan: skipping {name} (managed_hide)")
                        continue

                    verified, verify_msg, verify_meta = verify_plugin(child)
                    is_enabled = (name in enabled_list) or (
                        manifest.get("default_enabled", False) and name not in disabled_list
                    )

                    try:
                        mtime = manifest_path.stat().st_mtime
                    except Exception:
                        mtime = 0

                    self._plugins[name] = {
                        "manifest": manifest,
                        "path": child,
                        "enabled": is_enabled,
                        "band": band,
                        "loaded": False,
                        "verified": verified,
                        "verify_msg": verify_msg,
                        "verify_tier": verify_meta.get("tier", "unsigned"),
                        "verified_author": verify_meta.get("author"),
                        "_manifest_mtime": mtime,
                    }
                new_found.append(name)

                if is_enabled:
                    if self._load_plugin(name):
                        logger.info(f"[PLUGINS] Rescan: loaded new plugin '{name}'")
                    else:
                        # Failed verification — mark in-memory False but preserve
                        # enabled list on disk so user intent survives.
                        self._plugins[name]["enabled"] = False
                        logger.warning(f"[PLUGINS] Rescan: plugin '{name}' enabled but blocked (retry next restart)")

        # Reload plugins whose manifests changed on disk (outside lock to avoid deadlock)
        for rname in needs_reload:
            logger.info(f"[PLUGINS] Rescan: '{rname}' changed on disk, reloading")
            self.reload_plugin(rname)
            new_found.append(f"{rname} (reloaded)")

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

    # ── Route helpers ──

    def _register_routes(self, name: str, plugin_dir: Path, routes: list):
        """Register HTTP route handlers declared in plugin manifest."""
        registered = []
        # Cache namespaces per file so multiple handlers from the same file share state
        ns_cache = {}
        for route_def in routes:
            method = route_def.get("method", "GET").upper()
            path = route_def.get("path", "")
            handler_ref = route_def.get("handler", "")

            if not path or not handler_ref:
                logger.warning(f"[PLUGINS] {name}: route missing path or handler")
                continue

            if method not in ("GET", "POST", "PUT", "DELETE"):
                logger.warning(f"[PLUGINS] {name}: unsupported route method '{method}'")
                continue

            # Parse handler reference: "routes/file.py:func_name"
            if ":" in handler_ref:
                file_path, func_name = handler_ref.rsplit(":", 1)
            else:
                file_path = handler_ref
                func_name = "handle"

            # Load from cached namespace if same file, otherwise exec fresh
            if file_path in ns_cache:
                ns = ns_cache[file_path]
                handler_func = ns.get(func_name)
                if not handler_func or not callable(handler_func):
                    logger.warning(f"[PLUGINS] {name}: no '{func_name}' in cached {file_path}")
                    continue
            else:
                handler_func = self._load_handler(plugin_dir, file_path, func_name, ns_cache=ns_cache)
                if not handler_func:
                    logger.warning(f"[PLUGINS] {name}: failed to load route handler '{handler_ref}'")
                    continue

            # Convert path pattern like "capture/{request_id}" to regex
            param_names = re.findall(r'\{(\w+)\}', path)
            regex_pattern = re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', path)
            compiled = re.compile(f'^{regex_pattern}$')

            registered.append((method, compiled, param_names, handler_func))
            logger.info(f"[PLUGINS] Registered route: {method} /api/plugin/{name}/{path}")

        if registered:
            with self._lock:
                self._routes[name] = registered

    def _unregister_routes(self, name: str):
        """Remove all route handlers for a plugin."""
        with self._lock:
            if name in self._routes:
                del self._routes[name]
                logger.info(f"[PLUGINS] Unregistered routes for: {name}")

    def get_route_handler(self, plugin_name: str, method: str, path: str) -> Optional[Tuple[Callable, dict]]:
        """Find a matching route handler. Returns (handler_func, path_params) or None."""
        with self._lock:
            routes = self._routes.get(plugin_name)
        if not routes:
            return None

        method = method.upper()
        for route_method, pattern, param_names, handler in routes:
            if route_method != method:
                continue
            match = pattern.match(path)
            if match:
                return handler, match.groupdict()
        return None

    # ── Event source helpers ──

    def get_event_sources(self) -> List[dict]:
        """Get all registered daemon event sources across loaded plugins."""
        sources = []
        with self._lock:
            for plugin_sources in self._event_sources.values():
                sources.extend(plugin_sources)
        return sources

    def register_reply_handler(self, plugin_name: str, handler: Callable):
        """Register a reply handler for a daemon plugin.

        The handler is called when an event-triggered task completes:
            handler(task: dict, event_data: dict, response_text: str)
        """
        with self._lock:
            self._reply_handlers[plugin_name] = handler
        logger.info(f"[PLUGINS] Registered reply handler for {plugin_name}")

    def _get_reply_handler(self, source_name: str) -> Optional[Callable]:
        """Find the reply handler for an event source by looking up its plugin."""
        with self._lock:
            for plugin_name, sources in self._event_sources.items():
                for src in sources:
                    if src["name"] == source_name:
                        return self._reply_handlers.get(plugin_name)
        return None

    def emit_daemon_event(self, source_name: str, event_data: str):
        """Emit an event from a daemon plugin, triggering matching tasks.

        Args:
            source_name: The event source name (matches trigger_config.source)
            event_data: String payload to pass to the task
        """
        if not self._scheduler:
            logger.warning(f"[PLUGINS] Cannot emit event '{source_name}': no scheduler")
            return

        tasks = self._scheduler.find_tasks_by_event(source_name)
        if not tasks:
            logger.debug(f"[PLUGINS] No tasks listening for event source '{source_name}'")
            return

        reply_handler = self._get_reply_handler(source_name)
        any_accepted = False
        for task in tasks:
            result = self._scheduler.fire_event_task(task["id"], event_data, reply_callback=reply_handler)
            if result.get("success", False) or result.get("error") not in ("Event filtered out", "Account mismatch"):
                any_accepted = True
        return any_accepted

    def active_daemon_accounts(self, source_name: str) -> set:
        """Return set of account names with enabled daemon tasks for a given event source."""
        if not self._scheduler:
            return set()
        return self._scheduler.active_daemon_accounts(source_name)

    # ── Settings helpers ──

    def get_plugin_settings(self, name: str) -> dict:
        """Read plugin settings, merged with manifest defaults."""
        defaults = {}
        info = self._plugins.get(name)
        if info:
            schema = info["manifest"].get("capabilities", {}).get("settings", [])
            defaults = {f["key"]: f["default"] for f in schema if "key" in f and "default" in f}
        path = PROJECT_ROOT / "user" / "webui" / "plugins" / f"{name}.json"
        stored = {}
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {**defaults, **stored}

    # ── Query methods ──

    def get_plugin_names(self) -> List[str]:
        """All discovered plugin names."""
        return list(self._plugins.keys())

    def get_enabled_plugins(self) -> List[str]:
        """Names of enabled plugins."""
        return [n for n, info in self._plugins.items() if info["enabled"]]

    def get_load_errors(self) -> list:
        """Get accumulated plugin load errors (for startup toast display)."""
        errors = list(self._load_errors)
        self._load_errors.clear()
        return errors

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
            "verify_tier": info.get("verify_tier", "unsigned"),
            "verified_author": info.get("verified_author"),
            "missing_deps": info.get("missing_deps", []),
        }

    def get_all_plugin_info(self) -> List[dict]:
        """Get info for all discovered plugins."""
        return [self.get_plugin_info(n) for n in self._plugins]

    _plugin_state_cache: dict = {}
    _plugin_state_cache_lock = threading.Lock()

    def get_plugin_state(self, name: str) -> PluginState:
        """Get the PluginState helper for a plugin (cached singleton per name)."""
        with self._plugin_state_cache_lock:
            if name not in self._plugin_state_cache:
                self._plugin_state_cache[name] = PluginState(name)
            return self._plugin_state_cache[name]

    def get_credentials(self):
        """Get the credentials manager singleton. Convenience for plugins."""
        from core.credentials_manager import credentials
        return credentials

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
