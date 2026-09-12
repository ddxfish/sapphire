"""Inject Discord voice-conversation instructions into the system prompt."""

from __future__ import annotations

import logging

from plugins.discord.sapphire.voice_prompt import (
    VOICE_CONTEXT_MARKER,
    is_voice_conversation_chat,
    resolve_voice_conversation_context,
)

logger = logging.getLogger(__name__)


def _effective_chat_name() -> str | None:
    try:
        from core.chat.stream_brain import get_override
        override = get_override()
        if override and override.get('chat'):
            return str(override['chat'])
    except Exception:
        pass
    try:
        from core.api_fastapi import get_system
        system = get_system()
        sm = getattr(getattr(system, 'llm_chat', None), 'session_manager', None)
        if sm is not None:
            getter = getattr(sm, '_effective_chat_name', None)
            if callable(getter):
                return getter()
            return sm.get_active_chat_name()
    except Exception:
        pass
    return None


def prompt_inject(event) -> None:
    chat_name = _effective_chat_name()
    if not is_voice_conversation_chat(chat_name):
        return
    if any(VOICE_CONTEXT_MARKER in str(part) for part in (event.context_parts or [])):
        return
    block = resolve_voice_conversation_context(str(chat_name or ''))
    event.context_parts.append(block)
    logger.debug('[DISCORD] voice conversation prompt injected for %s', chat_name)
