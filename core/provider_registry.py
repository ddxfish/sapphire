# core/provider_registry.py — Shared provider registry pattern
#
# All provider systems (TTS, STT, Embedding, LLM) inherit from this.
# Handles registration, factory creation, listing, and plugin lifecycle.
# System-specific behavior (GPU release, subprocess management, etc.)
# is handled by subclass overrides.

import logging
import threading
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class BaseProviderRegistry:
    """
    Abstract registry for any provider system.

    Subclasses set system_name, setting_key, and register their core providers.
    Plugins register at runtime via register_plugin().
    Thread-safe via _lock for concurrent register/unregister/create/list.
    """

    def __init__(self, system_name: str, setting_key: str):
        self.system_name = system_name
        self.setting_key = setting_key
        self._lock = threading.Lock()
        self._core: Dict[str, dict] = {}       # key → {class, display_name, ...metadata}
        self._plugins: Dict[str, dict] = {}    # key → {class, display_name, plugin_name, ...}

    # ── Registration ──

    def register_core(self, key: str, provider_class, display_name: str = '', **metadata):
        """Register a built-in provider."""
        self._core[key] = {
            'class': provider_class,
            'display_name': display_name or key,
            **metadata,
        }
        logger.debug(f"[{self.system_name}] Core provider registered: {key}")

    def register_plugin(self, key: str, provider_class, display_name: str,
                         plugin_name: str, **metadata):
        """Plugin registers a custom provider."""
        with self._lock:
            if key in self._core:
                logger.warning(f"[{self.system_name}] Plugin '{plugin_name}' tried to register "
                              f"key '{key}' which is a core provider — skipping")
                return
            self._plugins[key] = {
                'class': provider_class,
                'display_name': display_name,
                'plugin_name': plugin_name,
                **metadata,
            }
        logger.info(f"[{self.system_name}] Plugin provider registered: {key} ({display_name}) "
                    f"from {plugin_name}")

    def unregister_plugin(self, plugin_name: str):
        """Remove all providers registered by a plugin."""
        with self._lock:
            to_remove = [k for k, v in self._plugins.items()
                         if v.get('plugin_name') == plugin_name]
            for key in to_remove:
                self._plugins.pop(key, None)
                logger.info(f"[{self.system_name}] Plugin provider unregistered: {key}")

    # ── Factory ──

    def create(self, key: str, **kwargs) -> Optional[Any]:
        """Create a provider instance by key. Returns None if not found."""
        with self._lock:
            entry = self._core.get(key) or self._plugins.get(key)
            if not entry:
                if key and key != 'none':
                    logger.warning(f"[{self.system_name}] Unknown provider '{key}'")
                entry = self._core.get('none')
                if not entry:
                    return None
            cls = entry['class']
        try:
            return cls(**kwargs)
        except Exception as e:
            logger.error(f"[{self.system_name}] Failed to create '{key}': {e}")
            return None

    # ── Listing (for UI) ──

    def get_all(self) -> List[Dict[str, Any]]:
        """Return all available providers for UI rendering."""
        with self._lock:
            snapshot = {**self._core, **self._plugins}
        result = []
        for key, entry in snapshot.items():
            result.append({
                'key': key,
                'display_name': entry.get('display_name', key),
                'is_core': key in self._core,
                'plugin_name': entry.get('plugin_name'),
                'requires_api_key': entry.get('requires_api_key', False),
                'api_key_env': entry.get('api_key_env', ''),
                'is_local': entry.get('is_local', False),
                # one line under the name on the provider card (register_core's
                # description=, or a plugin manifest's providers.<system>.description)
                'description': entry.get('description', ''),
            })
        return result

    def get_keys(self) -> list:
        """All registered provider keys."""
        return list(self._core.keys()) + list(self._plugins.keys())

    def has_key(self, key: str) -> bool:
        """Check if a provider key is registered."""
        return key in self._core or key in self._plugins

    def get_entry(self, key: str) -> Optional[dict]:
        """Get the raw registration entry for a key."""
        return self._core.get(key) or self._plugins.get(key)

    # ── Settings bridge ──

    def get_active_key(self) -> str:
        """Get the currently configured provider key from settings."""
        try:
            import config
            return getattr(config, self.setting_key, 'none') or 'none'
        except ImportError:
            return 'none'
