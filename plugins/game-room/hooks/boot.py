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
        # Import inside its own try: a fault here previously killed the
        # daemon thread with a raw stderr traceback and no logger line.
        try:
            from gameroom_story import session
        except Exception as e:
            logger.error(f"[STORY] boot re-merge cannot import session: {e}")
            return
        # Wait leg: ONLY the 503-poll is swallowed. _restore_pack faults
        # were previously retried 30x and then misreported as "system not
        # ready" — a completely different diagnosis (hunt 2026-08-17 P8).
        system = None
        for _ in range(30):          # up to ~60s, then give up loudly
            try:
                from core.api_fastapi import get_system
                system = get_system()
            except Exception:
                system = None
            if system is not None:
                break
            time.sleep(2)
        if system is None:
            logger.warning("[STORY] boot pack re-merge never ran (system not "
                           "ready in 60s) — active story prompts won't resolve "
                           "until a story action runs")
            return
        # Stale-thread guard (P9): if game-room was toggled OFF while we
        # waited, re-registering the pack would resurrect a disabled
        # plugin's prompts until restart.
        try:
            from core.plugin_loader import plugin_loader
            info = plugin_loader._plugins.get("game-room") or {}
            if not (info.get("loaded") and info.get("enabled")):
                logger.info("[STORY] boot re-merge skipped — plugin no longer loaded")
                return
        except Exception:
            pass
        try:
            session._restore_pack()
            logger.info("[STORY] boot costume re-merge done")
        except Exception as e:
            logger.error(f"[STORY] boot costume re-merge FAILED: {e}")
            return
        # The core post-scan re-prime ran before this thread could land —
        # a chat wearing a dynamic costume ('rose') booted in the fallback
        # prompt. Now that the names exist, resolve again (P1).
        try:
            system.reprime_pack_prompt(moment="story costume re-merge")
        except Exception as e:
            logger.warning(f"[STORY] post-merge prompt re-prime failed: {e}")

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
