# plugins/remembrance/schedule.py — cron handler for automatic offsite backups.
# Fires HOURLY (cheap); only backs up at the configured hour when auto-backup is on.
# Default hour = local BACKUPS_HOUR + 1 (a fresh state just after the local run).
# run(event) is the continuity-scheduler contract; a returned string surfaces to the
# user — so a FAILURE is the alert channel, success is silent.
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def run(event):
    from plugins.remembrance import ops
    prefs = ops.get_prefs()
    if not prefs.get("auto_enabled"):
        return None

    hour = prefs.get("offsite_cron_hour")
    if hour is None:
        base = int(getattr(event.get("config"), "BACKUPS_HOUR", 3) or 3)
        hour = (base + 1) % 24            # default: one hour after the local backup
    now = datetime.now()
    if now.hour != int(hour):
        return None                        # not the configured hour — silent no-op

    # Cadence by calendar position, mirroring the local GFS rhythm.
    if now.day == 1:
        cadence = "monthly"
    elif now.weekday() == 6:               # Sunday
        cadence = "weekly"
    else:
        cadence = "daily"

    r = ops.perform_offsite_backup(cadence=cadence)
    if r.get("ok"):
        logger.info(f"[remembrance] cron offsite backup ok ({cadence})")
        return None                        # success is silent
    logger.error(f"[remembrance] cron offsite backup failed: {r.get('error')}")
    return f"⚠ Offsite backup failed: {r.get('error')}"   # surfaces to the user
