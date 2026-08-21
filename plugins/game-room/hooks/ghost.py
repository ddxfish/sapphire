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
    corrupting turn-gated reveals (finding 1.2).

    The hook RUNNER resolves this turn's identity before any handler fires
    (event.chat_name, ruling F2's privacy resolver) — trust it first. The
    metadata→system walk below is the legacy fallback; it silently broke
    on streaming turns (system arrived None) and every story turn lost its
    scene block (the qwen/Sonnet finding, 2026-08-21)."""
    name = getattr(event, "chat_name", None)
    if name:
        return name
    try:
        sys_obj = (event.metadata or {}).get("system")
        if not sys_obj:
            logger.info("[STORY] ghost fallback: no system in event metadata")
            return None
        sm = getattr(getattr(sys_obj, "llm_chat", None), "session_manager", None)
        if not sm:
            logger.info("[STORY] ghost fallback: no session_manager on system")
            return None
        eff = getattr(sm, "_effective_chat_name", None)
        return eff() if callable(eff) else sm.get_active_chat_name()
    except Exception as e:
        logger.info(f"[STORY] ghost fallback walk failed: {e!r}")
        return None


def handle(event):
    try:
        from gameroom_story import session, state as st, render
        chat = _chat_name(event)
        if not chat:
            logger.info("[STORY] ghost: no chat resolved — no block")
            return
        entry = st.get_active_entry(chat)   # hot path: no cross-chat sweep
        if not entry or entry.get("paused"):
            # the normal path for every non-story chat — debug, not noise
            logger.debug(f"[STORY] ghost: no active story on '{chat}'"
                         + (" (paused)" if entry else ""))
            return  # intermission: no ticks, no block — clock stops
        story, state = session.load_active(chat)
        if not story or state["ended"]:
            logger.info(f"[STORY] ghost: story unloadable/ended on '{chat}' — no block")
            return
        room = story["rooms"].get(state["room"])
        if not room:
            logger.info(f"[STORY] ghost: room {state.get('room')!r} not found "
                        f"on '{chat}' — no block")
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
        logger.info(f"[STORY] ghost block {len(block)}ch → '{chat}' "
                    f"turn {next_turn} room {room.get('id')}")
    except Exception as e:
        logger.warning(f"[STORY] ghost hook failed: {e}", exc_info=True)
