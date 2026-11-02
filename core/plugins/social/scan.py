# core/plugins/social/scan.py - find providers in loaded plugins' manifests
#
# Any plugin may declare `capabilities.social: [spec, ...]` (registry.py says
# the shape). The loader does not know the key; THIS plugin reads every loaded
# manifest and registers what it finds - at its own on_load() and at every
# plugins_ready (boot, reload, rescan, toggle-on), so the order plugins come
# up in never matters. Idempotent: registering the same spec twice is a no-op.
# A provider whose plugin is gone is dropped here; one whose plugin is merely
# switched off is caught by the engine on use (no unload event exists).

import logging

from . import registry

logger = logging.getLogger(__name__)

PLUGIN = 'social'


def pull():
    """Register every loaded plugin's providers; drop those whose plugin left."""
    try:
        from core.plugin_loader import plugin_loader
        infos = plugin_loader.get_all_plugin_info()
    except Exception as e:
        logger.debug(f"[SOCIAL] no plugin loader to scan: {e}")
        return 0
    live = {}
    for info in infos:
        # this plugin's own on_load() runs before the loader marks it loaded
        if info.get('enabled') and (info.get('loaded') or info.get('name') == PLUGIN):
            live[info['name']] = info
    count = 0
    for name, info in live.items():
        decls = ((info.get('manifest') or {}).get('capabilities') or {}).get('social') or []
        if not isinstance(decls, list):
            logger.warning(f"[SOCIAL] {name}: capabilities.social must be a list")
            continue
        for d in decls:
            if isinstance(d, dict) and registry.register_provider(d.get('provider'), d, name):
                count += 1
    for p in registry.list_providers():
        if p['plugin_name'] not in live:
            registry.unregister_plugin(p['plugin_name'])
    return count
