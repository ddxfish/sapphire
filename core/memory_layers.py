# core/memory_layers.py — Plugin memory-layer registry (v1.1, 2026-07-12)
#
# Plugins declare memory layers in their manifest (capabilities.memory_layers);
# the loader registers them here. Memory plugins (mindpalace) CONSUME this
# registry — core never imports them. The app runs fine with no memory plugin
# loaded and no layers registered; this module is a dumb, dependency-free
# holder (Krem's ruling: registry in core, memory optional).
#
# v1.1 contract is MIRROR-ONLY: a layer plugin syncs its content into the
# memory plugin's own store via that plugin's write API. spec['mode'] exists
# so a federated (live search) mode can slot in later without manifest breaks.

import logging
import threading

logger = logging.getLogger(__name__)

# The memory plugin's seed layers — plugin layers may never shadow these.
RESERVED_KEYS = frozenset({'self', 'events', 'entities', 'knowledge', 'goals'})

_lock = threading.Lock()
_layers = {}        # key → {label, icon, description, librarian, mode, plugin_name}
_consumers = set()  # plugin names whose tool schemas rebuild on registry change
_generation = 0     # bumped on every change — consumers use it for cache invalidation


def register_layer(key, spec, plugin_name):
    """Register one plugin layer. Returns True on accept. Never raises —
    a bad layer declaration must not break plugin load."""
    global _generation
    try:
        key = str(key or '').strip().lower()
        if not key.isidentifier():
            logger.warning(f"[MEMLAYERS] '{plugin_name}': layer key '{key}' "
                           f"is not a valid identifier — skipped")
            return False
        if key in RESERVED_KEYS:
            logger.warning(f"[MEMLAYERS] '{plugin_name}': layer key '{key}' "
                           f"is reserved — skipped")
            return False
        mode = str(spec.get('mode', 'mirror')).lower()
        if mode != 'mirror':
            logger.warning(f"[MEMLAYERS] '{plugin_name}': layer '{key}' mode "
                           f"'{mode}' not supported in v1.1 (mirror only) — skipped")
            return False
        with _lock:
            owner = _layers.get(key, {}).get('plugin_name')
            if owner and owner != plugin_name:
                logger.warning(f"[MEMLAYERS] layer '{key}' already registered by "
                               f"'{owner}' — '{plugin_name}' refused")
                return False
            _layers[key] = {
                'label': str(spec.get('label') or key.title()),
                'icon': str(spec.get('icon') or ''),
                'description': str(spec.get('description') or ''),
                'librarian': bool(spec.get('librarian', False)),
                # writable: the AI may save_memory into this layer directly.
                # Default False — mirror layers are usually sync-owned.
                'writable': bool(spec.get('writable', False)),
                'mode': mode,
                'plugin_name': plugin_name,
            }
            _generation += 1
        logger.info(f"[MEMLAYERS] Layer registered: '{key}' from '{plugin_name}'")
        _notify_consumers()
        return True
    except Exception as e:
        logger.warning(f"[MEMLAYERS] register_layer('{key}') failed: {e}")
        return False


def unregister_plugin(plugin_name):
    """Drop all layers a plugin registered (unload/disable path)."""
    global _generation
    with _lock:
        gone = [k for k, v in _layers.items() if v.get('plugin_name') == plugin_name]
        for k in gone:
            _layers.pop(k, None)
        if gone:
            _generation += 1
    if gone:
        logger.info(f"[MEMLAYERS] Unregistered layer(s) {gone} from '{plugin_name}'")
        _notify_consumers()


def get_layers():
    """Snapshot of registered plugin layers: {key: spec}."""
    with _lock:
        return {k: dict(v) for k, v in _layers.items()}


def generation():
    """Monotonic change counter — consumers cache against it."""
    return _generation


def add_consumer(plugin_name):
    """A memory plugin registers itself: its tool schemas are rebuilt
    (function_manager.refresh_plugin_tools) whenever the layer set changes."""
    _consumers.add(plugin_name)


def _notify_consumers():
    for name in list(_consumers):
        try:
            from core.plugin_loader import plugin_loader
            fm = plugin_loader._function_manager
            if fm:
                fm.refresh_plugin_tools(name)
        except Exception as e:
            logger.debug(f"[MEMLAYERS] consumer refresh '{name}' skipped: {e}")
