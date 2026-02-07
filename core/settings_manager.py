"""
Settings Manager - Centralized configuration handling
Loads defaults applies path/URL construction merges user overrides
"""
import json
import os
import sys
import shutil
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'

class SettingsManager:
    """Manages application settings with hot-reload and persistence."""
    
    def __init__(self):
        self.BASE_DIR = Path(__file__).parent.parent
        self._defaults = {}
        self._user = {}
        self._config = {}
        self._reload_callbacks = {}
        self._lock = threading.Lock()
        
        # File watcher state
        self._watcher_thread = None
        self._watcher_running = False
        self._last_mtime = None
        self._last_check = 0
        
        # Restart tracking
        self._restart_pending = False
        self._pending_restart_keys = set()
        
        self._load_defaults()
        self._apply_construction()
        self._load_user_settings()
        self._merge_settings()
        self._ensure_example_file()
        self._update_mtime()
    
    def _flatten_dict(self, nested_dict, parent_key=''):
        """Flatten nested dict to single level for backward compatibility"""
        items = []
        for k, v in nested_dict.items():
            if k.startswith('_'):  # Skip metadata keys like _comment
                continue
            new_key = k if not parent_key else k  # Don't prepend parent, keep original keys
            if isinstance(v, dict) and not self._is_config_object(k):
                # Recurse into nested dicts (but not config objects like LLM_PRIMARY)
                items.extend(self._flatten_dict(v, new_key).items())
            else:
                items.append((new_key, v))
        return dict(items)
    
    def _is_config_object(self, key):
        """Check if a key represents a config object (not a category)"""
        config_objects = {
            'LLM_PRIMARY', 'LLM_FALLBACK', 'GENERATION_DEFAULTS',
            'FASTER_WHISPER_VAD_PARAMETERS', 'LLM_PROVIDERS'
        }
        return key in config_objects
    
    def _load_defaults(self):
        """Load core/settings_defaults.json"""
        defaults_path = self.BASE_DIR / 'core' / 'settings_defaults.json'
        try:
            with open(defaults_path, 'r', encoding='utf-8') as f:
                nested = json.load(f)
            self._defaults = self._flatten_dict(nested)
            logger.info(f"Loaded default settings from {defaults_path}")
        except Exception as e:
            logger.error(f"Failed to load defaults: {e}")
            self._defaults = {}
    
    def _apply_construction(self):
        """Apply programmatic path/URL construction and platform-specific defaults"""
        # Add BASE_DIR
        self._defaults['BASE_DIR'] = str(self.BASE_DIR)
        
        # Construct STT_SERVER_URL
        if 'STT_HOST' in self._defaults and 'STT_SERVER_PORT' in self._defaults:
            host = self._defaults['STT_HOST']
            port = self._defaults['STT_SERVER_PORT']
            self._defaults['STT_SERVER_URL'] = f"http://{host}:{port}"
        
        # Construct API_URL (for internal use by functions like meta.py)
        # Uses unified FastAPI server (WEB_UI_HOST/WEB_UI_PORT)
        api_host = self._defaults.get('WEB_UI_HOST', '127.0.0.1')
        if api_host == '0.0.0.0':
            api_host = '127.0.0.1'  # localhost for internal requests
        api_port = self._defaults.get('WEB_UI_PORT', 8073)
        api_proto = 'https' if self._defaults.get('WEB_UI_SSL_ADHOC', False) else 'http'
        self._defaults['API_URL'] = f"{api_proto}://{api_host}:{api_port}"
        
        # Apply platform-specific audio device defaults
        if IS_WINDOWS:
            platform_devices = self._defaults.get('RECORDER_PREFERRED_DEVICES_WINDOWS', ['default'])
        else:
            platform_devices = self._defaults.get('RECORDER_PREFERRED_DEVICES_LINUX', ['default'])
        self._defaults['RECORDER_PREFERRED_DEVICES'] = platform_devices
        
        # Auth is now handled entirely by core/setup.py using ~/.config/sapphire/secret_key or %APPDATA%\Sapphire\
        # Remove legacy env var handling
    
    def _load_user_settings(self):
        """Load user/settings.json if exists"""
        user_path = self.BASE_DIR / 'user' / 'settings.json'
        if user_path.exists():
            try:
                with open(user_path, 'r', encoding='utf-8') as f:
                    nested = json.load(f)
                self._user = self._flatten_dict(nested)
                logger.info(f"Loaded user settings from {user_path}")
            except Exception as e:
                logger.error(f"Failed to load user settings: {e}")
                self._user = {}
        else:
            logger.info("No user settings found, using defaults")
            self._user = {}
    
    def _merge_settings(self):
        """Merge defaults with user overrides, deep-merging LLM_PROVIDERS"""
        self._config = {**self._defaults, **self._user}

        # Deep-merge LLM_PROVIDERS so new provider fields from defaults aren't lost
        if 'LLM_PROVIDERS' in self._defaults and 'LLM_PROVIDERS' in self._user:
            merged_providers = {}
            for key, default_config in self._defaults['LLM_PROVIDERS'].items():
                if key in self._user['LLM_PROVIDERS']:
                    # Merge: defaults first, then user overrides
                    merged_providers[key] = {**default_config, **self._user['LLM_PROVIDERS'][key]}
                else:
                    merged_providers[key] = default_config
            # Include any user-added providers not in defaults
            for key, user_config in self._user['LLM_PROVIDERS'].items():
                if key not in merged_providers:
                    merged_providers[key] = user_config
            self._config['LLM_PROVIDERS'] = merged_providers

        # Initialize runtime PRIVACY_MODE from persistent START_IN_PRIVACY_MODE
        # PRIVACY_MODE is runtime-only (resets on restart), not persisted
        if 'PRIVACY_MODE' not in self._config:
            self._config['PRIVACY_MODE'] = self._config.get('START_IN_PRIVACY_MODE', False)
    
    def _ensure_example_file(self):
        """Create user/settings.example.json if it doesn't exist"""
        user_dir = self.BASE_DIR / 'user'
        user_dir.mkdir(exist_ok=True)
        
        example_path = user_dir / 'settings.example.json'
        if not example_path.exists():
            try:
                # Load the defaults again in nested form
                defaults_path = self.BASE_DIR / 'core' / 'settings_defaults.json'
                with open(defaults_path, 'r', encoding='utf-8') as f:
                    nested = json.load(f)
                
                # Remove auth section (has env vars and computed values)
                if 'auth' in nested:
                    del nested['auth']
                
                # Add helpful comment
                nested['_comment'] = 'Example settings - copy to settings.json and customize'
                
                with open(example_path, 'w', encoding='utf-8') as f:
                    json.dump(nested, f, indent=2)
                logger.info(f"Created {example_path}")
            except Exception as e:
                logger.error(f"Failed to create example file: {e}")
    
    def get(self, key, default=None):
        """Get a setting value"""
        with self._lock:
            return self._config.get(key, default)
    
    def set(self, key, value, persist=False):
        """
        Set a setting value.
        
        Args:
            key: Setting key
            value: New value
            persist: If True, save to user/settings.json
        """
        with self._lock:
            self._config[key] = value
            if persist:
                self._user[key] = value
                self.save()
                
                # Track if this setting requires restart
                tier = self.validate_tier(key)
                if tier == 'restart' and hasattr(self, '_pending_restart_keys'):
                    self._restart_pending = True
                    self._pending_restart_keys.add(key)
            
            # Trigger hot-reload callback if registered
            if key in self._reload_callbacks:
                try:
                    self._reload_callbacks[key](value)
                except Exception as e:
                    logger.error(f"Hot-reload callback failed for {key}: {e}")
    
    def set_many(self, settings_dict, persist=False):
        """Set multiple settings at once"""
        for key, value in settings_dict.items():
            self.set(key, value, persist=False)  # Don't save each individually
        
        if persist:
            self._user.update(settings_dict)
            self.save()
            
            # Track which settings require restart
            if hasattr(self, '_pending_restart_keys'):
                for key in settings_dict.keys():
                    tier = self.validate_tier(key)
                    if tier == 'restart':
                        self._restart_pending = True
                        self._pending_restart_keys.add(key)
    
    def save(self):
        """Persist current user settings to disk in nested format"""
        user_path = self.BASE_DIR / 'user' / 'settings.json'
        try:
            # Load existing nested structure or start fresh
            if user_path.exists():
                with open(user_path, 'r', encoding='utf-8') as f:
                    nested = json.load(f)
            else:
                nested = {"_comment": "Your custom settings - edit freely or use web UI"}
            
            # Deep update nested structure with flat changes
            nested = self._deep_update_from_flat(nested, self._user)
            
            user_path.parent.mkdir(exist_ok=True)
            with open(user_path, 'w', encoding='utf-8') as f:
                json.dump(nested, f, indent=2)
            
            self._update_mtime()
            logger.info(f"Saved user settings to {user_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save user settings: {e}")
            return False
    
    def _deep_update_from_flat(self, nested, flat_updates):
        """Update nested dict structure with flat key-value pairs"""
        # Load category mapping from defaults to know where keys belong
        defaults_path = self.BASE_DIR / 'core' / 'settings_defaults.json'
        try:
            with open(defaults_path, 'r', encoding='utf-8') as f:
                defaults_nested = json.load(f)
        except Exception:
            return nested
        
        # Find which category each flat key belongs to
        for key, value in flat_updates.items():
            category = self._find_category_for_key(defaults_nested, key)
            if category:
                if category not in nested:
                    nested[category] = {}
                
                # Special handling for nested config objects
                if isinstance(value, dict) and self._is_config_object(key):
                    # Deep merge: preserve existing nested values
                    if key in nested[category]:
                        nested[category][key] = {**nested[category][key], **value}
                    else:
                        nested[category][key] = value
                else:
                    nested[category][key] = value
            else:
                # Fallback: put at root level if category unknown
                nested[key] = value
        
        return nested
    
    def _find_category_for_key(self, nested_dict, target_key, current_category=None):
        """Recursively find which category a flat key belongs to"""
        for key, value in nested_dict.items():
            if key.startswith('_'):
                continue
            if key == target_key:
                return current_category
            if isinstance(value, dict) and not self._is_config_object(key):
                result = self._find_category_for_key(value, target_key, key)
                if result:
                    return result
        return None
    
    def reload(self):
        """Reload settings from disk"""
        with self._lock:
            self._load_user_settings()
            self._merge_settings()
            self._update_mtime()
            logger.info("Settings reloaded from disk")
    
    def reset_to_defaults(self):
        """Reset all settings to defaults (clears user overrides and file)"""
        with self._lock:
            self._user = {}
            self._merge_settings()
            
            # Actually delete/recreate the settings file
            user_path = self.BASE_DIR / 'user' / 'settings.json'
            try:
                if user_path.exists():
                    user_path.unlink()
                    logger.info(f"Deleted user settings file: {user_path}")
                
                # Write minimal fresh file
                user_path.parent.mkdir(exist_ok=True)
                with open(user_path, 'w', encoding='utf-8') as f:
                    json.dump({"_comment": "Your custom settings - edit freely or use web UI"}, f, indent=2)
                
                self._update_mtime()
                logger.info("Settings reset to defaults")
                return True
            except Exception as e:
                logger.error(f"Failed to reset settings file: {e}")
                return False
    
    def register_reload_callback(self, key, callback):
        """
        Register a callback to be called when a setting changes.
        
        Args:
            key: Setting key to watch
            callback: Function to call with new value
        """
        self._reload_callbacks[key] = callback
    
    def get_user_overrides(self):
        """Get only the user-overridden settings"""
        return self._user.copy()
    
    def get_all_settings(self):
        """Get all current settings (defaults + user overrides)"""
        return self._config.copy()
    
    def validate_tier(self, key):
        """
        Check if a setting can be hot-reloaded or requires restart.
        
        Returns:
            'hot': Can be applied immediately (read per-request)
            'restart': Requires full service restart
        """
        # Hot-reload: These are read per-request, no restart needed
        hot_reload = {
            'DEFAULT_USERNAME', 'DEFAULT_AI_NAME',
            'GENERATION_DEFAULTS', 'LLM_MAX_HISTORY', 'CONTEXT_LIMIT',
            'FORCE_THINKING', 'THINKING_PREFILL',
            'LLM_PROVIDERS', 'LLM_FALLBACK_ORDER', 'LLM_REQUEST_TIMEOUT',
            # SOCKS can be hot-reloaded - session cache is cleared on change
            'SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT',
            # Privacy mode is runtime-only, always hot
            'PRIVACY_MODE', 'PRIVACY_NETWORK_WHITELIST', 'START_IN_PRIVACY_MODE'
        }
        
        # Everything else requires restart (TTS, STT, modules, etc. are initialized at startup)
        if key in hot_reload:
            return 'hot'
        else:
            return 'restart'
    
    def is_restart_required(self):
        """Check if any settings changes require restart."""
        return getattr(self, '_restart_pending', False)
    
    def get_pending_restart_keys(self):
        """Get list of changed keys that need restart."""
        return list(getattr(self, '_pending_restart_keys', set()))
    
    def clear_restart_pending(self):
        """Clear restart pending flag (call after restart)."""
        with self._lock:
            self._restart_pending = False
            if hasattr(self, '_pending_restart_keys'):
                self._pending_restart_keys.clear()
    
    def _update_mtime(self):
        """Update last known mtime of user settings file"""
        user_path = self.BASE_DIR / 'user' / 'settings.json'
        try:
            if user_path.exists():
                self._last_mtime = user_path.stat().st_mtime
            else:
                self._last_mtime = None
        except Exception as e:
            logger.error(f"Failed to update mtime: {e}")
    
    def _file_watcher_loop(self):
        """Background thread that watches for file changes"""
        user_path = self.BASE_DIR / 'user' / 'settings.json'
        logger.info("File watcher started")
        
        while self._watcher_running:
            try:
                time.sleep(2)  # Poll every 2 seconds
                
                if not user_path.exists():
                    continue
                
                current_mtime = user_path.stat().st_mtime
                
                # Check if file was modified
                if self._last_mtime is not None and current_mtime != self._last_mtime:
                    # Debounce: wait 0.5s to ensure file write is complete
                    now = time.time()
                    if now - self._last_check < 0.5:
                        continue
                    
                    self._last_check = now
                    time.sleep(0.5)
                    
                    # Reload settings
                    logger.info("Detected settings file change, reloading...")
                    self.reload()
                    
                    # Trigger all registered callbacks
                    with self._lock:
                        for key, callback in self._reload_callbacks.items():
                            if key in self._config:
                                try:
                                    callback(self._config[key])
                                except Exception as e:
                                    logger.error(f"Callback failed for {key}: {e}")
            
            except Exception as e:
                logger.error(f"File watcher error: {e}")
                time.sleep(5)  # Back off on errors
        
        logger.info("File watcher stopped")
    
    def start_file_watcher(self):
        """Start the background file watcher thread"""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            logger.warning("File watcher already running")
            return
        
        self._watcher_running = True
        self._watcher_thread = threading.Thread(
            target=self._file_watcher_loop,
            daemon=True,
            name="SettingsFileWatcher"
        )
        self._watcher_thread.start()
        logger.info("File watcher thread started")
    
    def stop_file_watcher(self):
        """Stop the background file watcher thread"""
        if self._watcher_thread is None:
            return
        
        self._watcher_running = False
        if self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=5)
        logger.info("File watcher stopped")
    
    def remove_user_override(self, key):
        """
        Remove a user override for a key, reverting to default.
        
        Args:
            key: Setting key to remove override for
            
        Returns:
            bool: True if removed, False if no override existed
        """
        with self._lock:
            if key in self._user:
                del self._user[key]
                self._merge_settings()
                # Directly remove from file instead of using save()
                self._remove_key_from_file(key)
                logger.info(f"Removed user override for '{key}'")
                return True
            return False
    
    def _remove_key_from_file(self, key):
        """Remove a specific key from the user settings file"""
        user_path = self.BASE_DIR / 'user' / 'settings.json'
        try:
            if not user_path.exists():
                return
            
            with open(user_path, 'r', encoding='utf-8') as f:
                nested = json.load(f)
            
            # Find and remove the key from nested structure
            removed = self._remove_from_nested(nested, key)
            
            if removed:
                with open(user_path, 'w', encoding='utf-8') as f:
                    json.dump(nested, f, indent=2)
                self._update_mtime()
                logger.debug(f"Removed '{key}' from settings file")
        except Exception as e:
            logger.error(f"Failed to remove key from file: {e}")
    
    def _remove_from_nested(self, nested, target_key):
        """Recursively remove a key from nested dict structure"""
        # Check root level first
        if target_key in nested:
            del nested[target_key]
            return True
        
        # Search in nested categories
        for cat_key, cat_value in list(nested.items()):
            if cat_key.startswith('_'):
                continue
            if isinstance(cat_value, dict) and not self._is_config_object(cat_key):
                if target_key in cat_value:
                    del cat_value[target_key]
                    # Clean up empty categories
                    if not cat_value or all(k.startswith('_') for k in cat_value):
                        del nested[cat_key]
                    return True
        return False
    
    # Make this act like a module for attribute access
    def __getattr__(self, key):
        """Allow settings.KEY_NAME access"""
        if key.startswith('_'):
            return object.__getattribute__(self, key)
        with self._lock:
            if key in self._config:
                return self._config[key]
        raise AttributeError(f"Setting '{key}' not found")
    
    def __contains__(self, key):
        """Allow 'key in settings' checks"""
        with self._lock:
            return key in self._config
    
    def __repr__(self):
        return f"<SettingsManager: {len(self._config)} settings>"


# Create singleton instance
settings = SettingsManager()