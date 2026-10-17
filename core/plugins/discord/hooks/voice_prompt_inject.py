"""Inject Discord voice-conversation instructions into the system prompt."""

from __future__ import annotations

import logging

from plugins.discord.sapphire.voice_prompt import (
    VOICE_CONTEXT_MARKER,
    is_voice_conversation_chat,
    resolve_voice_conversation_context,
)

logger = logging.getLogger(__name__)


def prompt_inject(event) -> None:
    # The hook runner stamps event.chat_name before any handler runs
    # (core/hooks.py). The old metadata→system walk re-derived it through a
    # private session-manager method with two silent bails — the class closed
    # house-wide on 2026-08-21 (hunt 2026-09-12, M11).
    chat_name = getattr(event, 'chat_name', None)
    if chat_name is None:
        logger.warning('[DISCORD] voice prompt hook: event carries no chat_name — skipping')
        return
    if not is_voice_conversation_chat(chat_name):
        return
    if any(VOICE_CONTEXT_MARKER in str(part) for part in (event.context_parts or [])):
        return
    block = resolve_voice_conversation_context(str(chat_name or ''))
    event.context_parts.append(block)
    logger.debug('[DISCORD] voice conversation prompt injected for %s', chat_name)
