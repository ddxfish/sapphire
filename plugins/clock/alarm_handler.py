# plugins/clock/alarm_handler.py
# Fired by the continuity scheduler when an alarm task hits its cron time.
# event = {system, config, task, plugin_state}

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def run(event):
    """Play the ping when an alarm fires. delete_after_run on the task removes
    the alarm afterward (so it stays one-shot)."""
    task = event.get('task') or {}
    name = task.get('name', '?')

    # Import the ping helper from the tool module — same plugin dir.
    try:
        import sys
        plugin_dir = Path(__file__).resolve().parent
        tools_dir = plugin_dir / 'tools'
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        from clock_tools import _play_ping
        _play_ping()
        logger.info(f"[clock] alarm '{name}' fired and pinged")
        return f"Alarm '{name}' fired"
    except Exception as e:
        logger.error(f"[clock] alarm '{name}' handler error: {e}", exc_info=True)
        return f"Alarm '{name}' fired but ping failed: {e}"
