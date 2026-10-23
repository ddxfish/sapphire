# hooks/costume.py — prompt_inject: the spine's `session_prompt_piece`
# (2026-09-09). A game session's chat wears one or two sentences on top of
# the persona — the room default, or the game's override — e.g. "You're on
# the couch watching Krem play Doom." Long-lived per game, so it belongs on
# the system prompt (prompt_inject), not the per-turn ghost rail. Stories
# carry their own costumes (the referee's monolith) and are skipped.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def prompt_inject(event):
    try:
        chat = getattr(event, "chat_name", None)
        if not chat:
            return event
        import gameroom_core as gc
        gid = gc.game_session(chat)
        if not gid:
            return event
        piece = str(gc.effective(gid, session=chat).get("session_prompt_piece") or "").strip()
        if piece:
            event.context_parts.append(piece[:2000])
            logger.debug(f"[GAME] costume line -> '{chat}' ({gid})")
    except Exception as e:
        logger.warning(f"[GAME] costume inject failed: {e}")
    return event


handle = prompt_inject
