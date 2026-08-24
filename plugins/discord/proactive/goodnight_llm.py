"""Generate goodnight message text via Sapphire LLM."""

from __future__ import annotations

import logging

from plugins.discord.proactive.bot_identity import bot_identity_fields, build_proactive_post_hint
from plugins.discord.proactive.proactive_llm import run_proactive_llm

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = (
    'Write a short, warm good-night message for this Discord channel. '
    'Sound like a friendly community member signing off for the night — not a bot announcement. '
    'One or two sentences. Vary your wording.'
)


def generate_goodnight(
    system,
    *,
    account: str = '',
    guild_name: str = '',
    channel_name: str = '',
    instructions: str = '',
    recent_chat: list | None = None,
    provider_key: str = '',
    model_name: str = '',
    max_tokens: int = 180,
    transport=None,
    account_repository=None,
) -> str:
    """Return goodnight text from the LLM, or empty string on failure."""
    identity_hint = build_proactive_post_hint(
        bot_identity_fields(account, transport=transport, account_repository=account_repository),
        purpose='goodnight',
    )

    instructions = (instructions or '').strip() or DEFAULT_INSTRUCTIONS
    context_parts = []
    if identity_hint:
        context_parts.append(identity_hint)
    if guild_name:
        context_parts.append(f'Server: {guild_name}')
    if channel_name:
        context_parts.append(f'Channel: #{channel_name}')
    if recent_chat:
        lines = recent_chat[-8:]
        if lines:
            context_parts.append('Recent channel activity:\n' + '\n'.join(lines))

    prompt = instructions
    if context_parts:
        prompt += '\n\n---\nContext:\n' + '\n'.join(context_parts)
    prompt += (
        '\n\n---\nWrite ONLY the message to post in Discord — no quotes, labels, or explanation. '
        'Say goodnight to the channel — never yourself by name.'
    )

    return run_proactive_llm(
        system,
        prompt=prompt,
        account=account,
        provider_key=provider_key,
        model_name=model_name,
        max_tokens=max_tokens,
        log_label='Goodnight',
        transport=transport,
        account_repository=account_repository,
    )
