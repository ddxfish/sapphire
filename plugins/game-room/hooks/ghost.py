# hooks/ghost.py — per-turn story context on the ghost rail.
#
# Fires once per player message. When this chat has an active story:
#   1. append a turn_tick to the journal (turn numbers stay replayable —
#      the ONE write the read-rail owns, it IS the turn boundary marker)
#   2. contribute the current-state block (render.ghost_block) — rebuilt
#      fresh every call, never persisted, solutions never included.
import logging
import sys
from pathlib import Path

# .absolute(), never .resolve(): symlinked plugin dirs must not escape.
_PLUGIN_ROOT = str(Path(__file__).absolute().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

logger = logging.getLogger(__name__)


def _chat_name(event):
    """THIS turn's chat, not the globally active one. A phone call or
    background conversation running on another chat would otherwise get the
    story's ghost block injected AND tick a turn into the story's journal,
    corrupting turn-gated reveals (finding 1.2)."""
    try:
        sys_obj = (event.metadata or {}).get("system")
        if not sys_obj:
            return None
        sm = getattr(getattr(sys_obj, "llm_chat", None), "session_manager", None)
        if not sm:
            return None
        eff = getattr(sm, "_effective_chat_name", None)
        return eff() if callable(eff) else sm.get_active_chat_name()
    except Exception:
        return None


def handle(event):
    try:
        from gameroom_story import session, state as st, render
        chat = _chat_name(event)
        if not chat:
            return
        entry = st.get_active().get(chat)
        if not entry or entry.get("paused"):
            return  # intermission: no ticks, no block — clock stops
        story, state = session.load_active(chat)
        if not story or state["ended"]:
            return
        room = story["rooms"].get(state["room"])
        if not room:
            return

        next_turn = state["turn"] + 1
        # Render FIRST, tick second. The tick is the turn boundary marker —
        # burning it for a turn she narrates blind means turn-gated reveals
        # and hints fire against a clock the player never actually advanced
        # (finding 4.7). A render failure now costs nothing but the block.
        tick = {"event": "turn_tick", "turn": next_turn}
        block = render.ghost_block(story, st.apply_event(dict(state), tick), room)
        st.append(entry["story"], chat, tick)
        event.ghost_text = block
    except Exception as e:
        logger.warning(f"[STORY] ghost hook failed: {e}", exc_info=True)
