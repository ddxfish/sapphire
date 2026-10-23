"""post_chat hook — the <<HANG UP>> sentinel.

Sapphire ends a phone call by writing <<HANG UP>> in her reply (the per-turn
phone-context ghost tells her it's available). Detection happens here on the
RAW reply text — the TTS cleaner's HTML-tag stripper (`<[^>]+>` in
core/tts/streaming.py) destroys angle-bracket tags before the chunk layer, so
a tts_chunk_text hook can never see the marker intact. That same stripping is
what keeps the tag silent to the caller: we detect on raw text, the cleaner
mutes it, and the call ends after her final words drain (twilio_source.wait).

Works on every call regardless of toolset — an outside line must never leave
her unable to hang up. Gated exactly like phone_context: only while a phone
call is active AND only for that call's own stream.
"""
import logging
import re

logger = logging.getLogger(__name__)

_MARKER = re.compile(r"<<\s*HANG[\s_-]*UP\s*>>", re.IGNORECASE)


def post_chat(event):
    text = getattr(event, "response", None)
    if not text or "<<" not in text:
        return
    system = event.metadata.get("system")
    if not system:
        return
    # Per-chat lookup: the reply's chat resolves to ITS OWN call, so with N
    # concurrent calls the sentinel only ever hangs up the call it was said on.
    calls = getattr(system, "_twilio_active_calls", None)
    if not calls:
        return
    try:
        current = system.llm_chat.session_manager._effective_chat_name()
    except Exception:
        return
    call = calls.get(current)
    if not call or call.get("session") is None:
        return
    if not _MARKER.search(text):
        return
    call["session"]._hangup_after_drain = True
    logger.info(f"[TWILIO] <<HANG UP>> sentinel seen in '{current}' — ending that call after the reply drains")
