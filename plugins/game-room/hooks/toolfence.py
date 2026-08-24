# hooks/toolfence.py — per-scenario AI tools fence (core tools_filter hook).
# Core fires with THIS turn's final tool schema; we subtract the active
# playthrough's fenced tools so she never even sees them (Krem's B ruling
# 2026-08-24 — a fence she can't see through beats a sign saying "don't").
# The fence lives on the active entry, rides scenario save/load like slots.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)

# story_act is the engine door — never fenceable, whatever storage says.
NEVER_FENCED = {"story_act"}


def handle(event):
    try:
        if not event.tools:
            return
        chat = getattr(event, "chat_name", None)
        if not chat:
            return
        from gameroom_story import state as st
        entry = st.get_active_entry(chat)
        if not entry or entry.get("paused"):
            return                      # intermission: her full self, no fence
        fence = set(entry.get("fence") or []) - NEVER_FENCED
        if not fence:
            return
        before = len(event.tools)
        event.tools = [t for t in event.tools
                       if t.get("function", {}).get("name") not in fence]
        if len(event.tools) < before:
            logger.debug(f"[STORY] tools fence on '{chat}': {sorted(fence)} withheld")
    except Exception as e:
        # Fail open — a broken fence must never cost her the whole toolset.
        logger.warning(f"[STORY] tools fence failed (nothing withheld): {e}")
