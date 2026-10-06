# daemon.py - on load, pick the running jobs back up (a plugin reload leaves them running; a Sapphire restart under
# systemd stops them and they report `interrupted`) and keep publishing their progress to the page.
import logging
import sys
import threading
import time
from pathlib import Path

PLUGIN_DIR = Path(__file__).absolute().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.append(str(PLUGIN_DIR))

from wakeword_maker import jobs, paths, sampler_client   # noqa: E402

logger = logging.getLogger(__name__)


def _publish(event, data):
    try:
        from core.event_bus import publish
        publish(event, data, ephemeral=True)
    except Exception as e:
        logger.debug(f"[WWM] publish: {e}")


def start(plugin_loader, settings):
    root = paths.data_root(settings)
    if root and root.is_dir():
        try:
            jobs.attach_all(root, _publish)
            jobs.promote(root, _publish)
            live = jobs.running(root)
            if live:
                logger.info(f"[WWM] following {len(live)} running job(s): {', '.join(j['id'] for j in live)}")
        except Exception as e:
            logger.warning(f"[WWM] could not re-attach jobs: {e}")
        threading.Thread(target=_resume_watches, args=(plugin_loader, settings), name='wwm-watch-resume', daemon=True).start()


def _resume_watches(plugin_loader, settings):
    """The route handlers are exec'd into one namespace by the plugin loader; `watch_resume` lives there. Find it
    through any registered handler and let it rebuild a satellite watch a reload or restart interrupted (a watch is
    a thread, jobs are processes)."""
    def find():
        for row in (getattr(plugin_loader, '_routes', {}) or {}).get(PLUGIN_DIR.name) or []:
            fn = getattr(row[-1], '__globals__', {}).get('watch_resume')
            if fn:
                return fn
    for _ in range(60):                                  # up to ten minutes: a paused loop may be mid-chunk (300 s on a Pi)
        resume = find()
        if resume is not None:
            try:
                got, busy = resume(settings)
                if got:
                    logger.info(f"[WWM] resumed room watch: {', '.join(got)}")
                if not busy:
                    return
            except Exception as e:
                logger.warning(f"[WWM] watch resume: {e}")
                return
        time.sleep(10)


def stop():
    from wakeword_maker import watch
    jobs.detach_all()       # this module's tails stop following and promoting; the next load attaches its own
    watch.pause_all()       # a room watch is a thread: it pauses here and resumes on the next load
    sampler_client.stop()   # the jobs keep running on purpose; the sampler is a helper and goes with the plugin
