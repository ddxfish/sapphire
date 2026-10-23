# hooks/ghost.py — per-turn story/game context on the ghost rail.
#
# Fires once per player message. When this chat has an active story:
#   1. append a turn_tick to the journal (turn numbers stay replayable —
#      the ONE write the read-rail owns, it IS the turn boundary marker)
#   2. contribute the current-state block (render.ghost_block) — rebuilt
#      fresh every call, never persisted, solutions never included.
# When this chat is a GAME session (F6 port, 2026-09-09): contribute the
# engine's PUBLIC table view (gameroom_core.ghost_block) — read-only, no
# tick, never hidden info (her hole cards stay with the sealed seat).
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
        if not entry:
            _game_block(event, chat)
            return
        if entry.get("paused"):
            # the normal path for every non-story chat — debug, not noise
            logger.debug(f"[STORY] ghost: story paused on '{chat}'")
            return  # intermission: no ticks, no block — clock stops
        # Two-ledger regen (plan tmp/regen-two-ledger-plan.md, F1-A): if the
        # chat was rewound (regenerate/delete), the journal tail is anchored
        # past the current message count — rewind the world to match BEFORE
        # ticking, so the re-run turn plays on the state the player actually
        # sees. Detection only on the active chat: anchors are only
        # comparable there (the same rule _stamp_anchor lives by). This also
        # retires the regen double-tick: the stray tick gets REVERTED.
        try:
            from core.api_fastapi import get_system
            system = get_system()
            sm = system.llm_chat.session_manager if system else None
            if sm is not None and sm.get_active_chat_name() == chat:
                count = len(sm.get_messages_for_display())
                rw = st.rewind_to_match(entry["story"], chat, count)
                if rw:
                    logger.info(f"[STORY] chat rewound on '{chat}' — world follows: "
                                f"turn {rw['turn']}, {rw['dropped']} event(s) off, "
                                f"{rw['salvaged']} seal(s) salvaged")
                    session.refresh_prompt(system, session=chat)
                    from core.event_bus import publish, Events
                    publish(Events.PLUGIN_NOTICE, {
                        "plugin": "game-room", "severity": "info",
                        "message": f"Story rewound to turn {rw['turn']} to match "
                                   f"the regenerated chat"})
        except Exception as e:
            logger.warning(f"[STORY] regen rewind check failed: {e}")

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


def _game_block(event, chat):
    """Game session -> the table's public view on the rail. Silent for every
    plain chat (debug); loud only when a game lookup itself fails."""
    try:
        import gameroom_core as gc
        gid = gc.game_session(chat)
        if not gid:
            logger.debug(f"[STORY] ghost: no active story on '{chat}'")
            return
        block = gc.ghost_block(gid, chat)
        if block:
            event.ghost_text = block
            logger.info(f"[GAME] ghost block {len(block)}ch -> '{chat}' ({gid})")
        else:
            logger.debug(f"[GAME] ghost: no block for '{chat}' ({gid})")
    except Exception as e:
        logger.warning(f"[GAME] ghost block failed on '{chat}': {e}")
