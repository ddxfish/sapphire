# core/plugins/remembrance/schedule.py — the daily offsite backup.
# One scheduler entry, bound to the plugin's settings (plugin.json: time_setting
# = offsite_cron_hour, enabled_setting = auto_enabled), so it runs once at the
# configured hour and not at all while automatic backup is off. 1.1 ticked
# hourly and guarded the hour here, and every silent tick logged "complete"
# in the notification center (2026-10-22).
# run(event) is the continuity-scheduler contract; a returned string surfaces to
# the user — so a FAILURE is the alert channel, success is silent.
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def cadence(now=None):
    """Calendar position, mirroring the local GFS rhythm."""
    now = now or datetime.now()
    if now.day == 1:
        return "monthly"
    if now.weekday() == 6:                 # Sunday
        return "weekly"
    return "daily"


def run(event):
    from plugins.remembrance import ops
    cad = cadence()
    r = ops.perform_offsite_backup(cadence=cad)
    if r.get("ok"):
        logger.info(f"[remembrance] scheduled offsite backup ok ({cad})")
        return None                        # success is silent
    logger.error(f"[remembrance] scheduled offsite backup failed: {r.get('error')}")
    return f"⚠ Offsite backup failed: {r.get('error')}"   # surfaces to the user
