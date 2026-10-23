# Stop plugin — halts TTS and cancels LLM generation
#
# Triggered by exact voice commands: "stop", "halt", "be quiet", "shut up"
# Bypasses LLM entirely for instant response.

import logging
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


def pre_chat(event):
    """Cancel TTS playback and streaming generation — scoped to the surface
    that said "stop". The A1 brain override is installed before pre_chat fires,
    so the effective chat names the live phone call for caller turns and the
    active chat otherwise (2026-07-06 herring hunt: unscoped cancel was the one
    caller the call-isolation pass missed — operator "stop" killed live calls,
    and a caller saying "stop" stopped the operator's browsers)."""
    system = event.metadata.get("system")

    if system:
        try:
            mgr = system.get_conversation_manager()
            ext = mgr.external_chats() if mgr else set()
        except Exception:
            ext = set()
        try:
            chat = system.llm_chat.session_manager._effective_chat_name()
        except Exception:
            chat = None

        if chat in ext:
            # A phone caller said "stop": cancel THEIR call's stream only.
            # The operator's local speakers and browser audio are not theirs.
            if hasattr(system, "cancel_generation"):
                if system.cancel_generation(chat_name=chat):
                    logger.info(f"[STOP] Generation cancelled (phone chat '{chat}')")
        else:
            # Operator said "stop": halt local TTS + browser audio, cancel all
            # streams EXCEPT live phone calls (their audio belongs to callers).
            if hasattr(system, "tts") and system.tts:
                try:
                    system.tts.stop()
                    logger.info("[STOP] TTS stopped")
                except Exception as e:
                    logger.warning(f"[STOP] TTS stop failed: {e}")

            # Broadcast to web UI clients to stop browser TTS
            publish(Events.TTS_STOPPED)

            if hasattr(system, "cancel_generation"):
                if system.cancel_generation(exclude_chats=ext):
                    logger.info("[STOP] Generation cancelled")

    event.skip_llm = True
    event.ephemeral = True
    event.response = "Stopped."
    event.stop_propagation = True
