# hooks/boot.py — plugins_ready: re-register the prompt pack WITH the
# runtime-rendered story monoliths merged (user/story_saves/
# _dynamic_monoliths.json). The manifest registration during the scan
# carries only the shipped pack files, so after a reboot mid-story the
# role prompt ('rose') didn't exist as a name — and core's missing-prompt
# fallback then REWROTE the chat to 'default' (Sapph-not-Rose bug,
# 2026-08-05). This hook makes state.py's "restart-proof" promise true.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def plugins_ready(event):
    # v1.3: costumes live in the chat DB, reached through get_system() —
    # which isn't servable yet at plugins_ready (fires at scan end, before
    # the system finishes init). The file-era code could merge here; the
    # DB era must WAIT for the system, so the re-merge runs in a short
    # retry thread. Live-caught 2026-08-15 ('503: System not initialized'
    # at every boot — active story costumes silently unregistered).
    import threading
    import time

    def _merge_when_ready():
        from gameroom_story import session
        for _ in range(30):          # up to ~60s, then give up loudly
            try:
                from core.api_fastapi import get_system
                if get_system() is not None:
                    session._restore_pack()
                    logger.info("[STORY] boot costume re-merge done")
                    return
            except Exception:
                pass
            time.sleep(2)
        logger.warning("[STORY] boot pack re-merge never ran (system not "
                       "ready in 60s) — active story prompts won't resolve "
                       "until a story action runs")

    try:
        threading.Thread(target=_merge_when_ready, daemon=True,
                         name="gameroom-boot-remerge").start()
    except Exception as e:
        logger.warning(f"[STORY] boot pack re-merge failed to start: {e}")
    # v1.3 no-migration ruling (2026-08-15): playthroughs live in the chat
    # DB now. Old file saves are never read — one boot notice so leftovers
    # don't rot silently.
    try:
        from gameroom_story import rooms
        if rooms.SAVES_ROOT.is_dir() and any(rooms.SAVES_ROOT.iterdir()):
            logger.info("[STORY] legacy user/story_saves/ files present — "
                        "unused since v1.3 (saves live in the chat DB); "
                        "safe to clean up by hand")
    except Exception:
        pass
    return event
