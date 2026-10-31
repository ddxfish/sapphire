# hooks/ready.py — plugins_ready: an install that switched to the palace
# but still holds classic rows it never imported gets a row in the
# notification center, re-fired on every boot while any remain and
# dismissed once the Import tab has brought them in (Krem's ruling
# 2026-10-30). Read-only toward the classic databases.
import logging

logger = logging.getLogger(__name__)


def plugins_ready(event):
    # Boot only: plugins_ready also fires on every toggle, reload and
    # rescan, and each one re-read both classic databases in the request
    # and re-posted the row (2026-10-31).
    try:
        reason = (getattr(event, 'metadata', None) or {}).get('reason')
        if reason not in (None, 'scan'):
            return
        import config
        from plugins.mindpalace.tools import import_tools
        import_tools.nudge_if_pending(config)
    except Exception as e:
        logger.debug(f"[mindpalace] import nudge at plugins_ready skipped: {e}")
