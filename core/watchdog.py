"""A teardown watchdog: if stopping wedges, say where and end the process.

Shutdown is a serial chain of stops with per-step timeouts and no ceiling,
and after run() returns the interpreter still joins every non-daemon thread.
One driver that never returned left Sapphire "restarting" forever, with the
runner (and the launcher) waiting on a child that would never exit
(2026-10-16, tmp/single-instance-plan.md row 12).
"""
import faulthandler
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)


def arm(seconds, exit_code, what='shutdown'):
    """Start a daemon timer. When it fires, every thread's stack goes to stderr
    (console, journal, launcher pane) and the process ends with `exit_code`,
    a number or a callable evaluated at that moment. Returns the timer; it is
    harmless to let it run, since a finished teardown ends the process first."""
    def _fire():
        code = exit_code() if callable(exit_code) else exit_code
        logger.error(f"[WATCHDOG] {what} did not finish within {seconds}s; dumping thread stacks and exiting with {code}")
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass
        try:
            if sys.stderr is not None:
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            pass
        os._exit(code)

    t = threading.Timer(seconds, _fire)
    t.daemon = True
    t.name = f'watchdog-{what}'
    t.start()
    return t
