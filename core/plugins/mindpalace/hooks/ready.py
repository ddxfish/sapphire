# hooks/ready.py — plugins_ready: an install that switched to the palace
# but still holds classic rows it never imported gets a row in the
# notification center, re-fired on every boot while any remain and
# dismissed once the Import tab has brought them in (Krem's ruling
# 2026-10-30). Read-only toward the classic databases.
import logging

logger = logging.getLogger(__name__)


def plugins_ready(event):
    # Boot, or the palace's own switch flipping on: the install that just
    # swapped engines is the one with classic rows waiting, and nothing else
    # pointed at Import until a restart (hunt 2026-11-01, B6). Every other
    # toggle, reload and rescan re-read both classic databases for nothing
    # and re-posted the row (2026-10-31).
    try:
        meta = getattr(event, 'metadata', None) or {}
        reason = meta.get('reason')
        if reason not in (None, 'scan') and not (reason == 'toggle' and meta.get('plugin') == 'mindpalace'):
            return
        import config
        from plugins.mindpalace.tools import import_tools
        import_tools.nudge_if_pending(config)
    except Exception as e:
        logger.debug(f"[mindpalace] import nudge at plugins_ready skipped: {e}")
