# hooks/mirror.py — pre_chat / post_chat: the talk mirror (F6 port, 2026-09-09).
#
# Since the port, table talk IS the chat: the real rail is transplanted into
# the room and every line is a normal chat turn. The sealed seat (moves only)
# still hears the table through state['talk'] — so each chat line on a GAME
# session is mirrored there: the player's message before the LLM runs, her
# reply after it's saved. Read-only for the chat; a write to the game row.
#
# Identity: event.chat_name is THIS turn's chat (runner-stamped) — a phone or
# background turn on another chat never lands in a table's ears. No saved
# game state (fresh session, nothing dealt) -> nothing to mirror.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def _mirror(event, who, text):
    try:
        chat = getattr(event, "chat_name", None)
        if not chat or not text:
            return
        import gameroom_core as gc
        gid = gc.game_session(chat)
        if not gid:
            return
        if gc.record_talk(gid, chat, who, text):
            logger.debug(f"[GAME] talk mirrored ({who}) -> '{chat}' ({gid})")
    except Exception as e:
        # Never touch the turn — a mirror miss costs the seat one line.
        logger.warning(f"[GAME] talk mirror ({who}) failed: {e}")


def pre_chat(event):
    _mirror(event, "player", getattr(event, "input", None))
    return event


def post_chat(event):
    _mirror(event, "ai", getattr(event, "response", None))
    return event
