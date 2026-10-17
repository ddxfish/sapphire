# hooks/lifecycle.py — chat_cleared: story journals die with the transcript
# (Krem's ruling 2026-08-17). A journal's msg_index turn anchors point into
# the chat's messages; after a clear they reference nothing, and a later
# revert would align against phantom indices. Costumes, saves, and the
# active-story pointer survive — with an empty journal the story simply
# starts over from the beginning. Non-journal keys are untouched.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def chat_cleared(event):
    chat = (getattr(event, "metadata", None) or {}).get("chat")
    if not chat:
        return event
    try:
        from core.plugin_loader import plugin_loader
        cs = plugin_loader.get_chat_state("game-room")
        doomed = [k for k in cs.keys(chat)
                  if k.startswith("story:journal:") or k.startswith("story:reverted:")]
        for key in doomed:
            cs.delete(chat, key)
        if doomed:
            logger.info(f"[STORY] chat cleared — dropped {len(doomed)} "
                        f"journal key(s) with the transcript")
    except Exception as e:
        # A hidden (sealed) chat's delete raises by contract — but a sealed
        # chat can't be cleared either, so this is belt-and-suspenders.
        logger.warning(f"[STORY] journal cleanup on chat_cleared failed: {e}")
    return event
