# hooks/ready.py — plugins_ready: 1.1 kept the backup hour and switch in
# PluginState; 1.2's schedule entry binds to the plugin-settings store, so
# the old values move there once (ops.migrate_prefs), then the live task is
# resynced. Idempotent: the hook fires on every (re)load wave.
import logging

logger = logging.getLogger(__name__)


def plugins_ready(event):
    try:
        from plugins.remembrance import ops
        ops.migrate_prefs()
    except Exception as e:
        logger.warning(f"[remembrance] prefs migration at plugins_ready failed: {e}")
