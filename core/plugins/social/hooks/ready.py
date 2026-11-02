# core/plugins/social/hooks/ready.py - re-read every loaded manifest for providers
#
# plugins_ready fires after the boot scan and after any reload, rescan or
# toggle-on. A provider plugin that came up after this one, or came back,
# is found here. Idempotent (scan.pull registers an identical spec as a no-op).
import logging

logger = logging.getLogger(__name__)


def plugins_ready(event):
    try:
        from core.plugin_loader import plugin_loader
        if not plugin_loader.switched_on('social'):
            return
        from plugins.social import scan
        n = scan.pull()
        logger.debug(f"[SOCIAL] plugins_ready: {n} provider declaration(s) seen")
    except Exception as e:
        logger.warning(f"[SOCIAL] plugins_ready scan failed: {e}")
