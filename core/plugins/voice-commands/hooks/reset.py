# Reset plugin — clears current chat history
#
# Triggered by: "reset", "reset chat", "clear chat", "clear history"
# Bypasses LLM entirely — instant wipe + confirmation.

import logging

logger = logging.getLogger(__name__)


def pre_chat(event):
    """Clear the current chat's history."""
    system = event.metadata.get("system")

    if system and hasattr(system, "llm_chat"):
        session = system.llm_chat.session_manager
        # EFFECTIVE chat, not active: on a phone/background stream this
        # command runs against ITS chat — reporting the operator's open web
        # chat's name to a caller was a cross-surface leak (P3-T10; stop.py
        # had this right already; session.clear() itself is stream-aware).
        chat_name = (session._effective_chat_name()
                     if hasattr(session, "_effective_chat_name")
                     else session.get_active_chat_name()) or "chat"
        try:
            session.clear()
            logger.info(f"[RESET] Cleared history for '{chat_name}'")
            event.response = f"Chat '{chat_name}' has been reset."
        except Exception as e:
            logger.error(f"[RESET] Failed: {e}")
            event.response = f"Reset failed: {e}"
    else:
        event.response = "Reset failed — system not available."

    event.skip_llm = True
    event.ephemeral = True
    event.stop_propagation = True
