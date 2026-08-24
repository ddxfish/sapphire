"""Generate birthday wish text via Sapphire LLM."""

from __future__ import annotations

import logging

from plugins.discord.proactive.bot_identity import bot_identity_fields, build_proactive_post_hint
from plugins.discord.proactive.proactive_llm import run_proactive_llm

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = (
    'Write a short, warm happy-birthday message for a Discord channel member. '
    'Use their @mention exactly as provided. One or two sentences, natural tone.'
)

BULK_DEFAULT_INSTRUCTIONS = (
    'Write one warm happy-birthday message for several Discord channel members celebrating today. '
    'Include every @mention exactly as provided. Keep it natural — one short paragraph, not a list.'
)


def generate_birthday_wish(
    system,
    *,
    account: str = '',
    guild_name: str = '',
    channel_name: str = '',
    display_name: str = '',
    mention: str = '',
    instructions: str = '',
    recent_chat: list | None = None,
    provider_key: str = '',
    model_name: str = '',
    max_tokens: int = 180,
    transport=None,
    account_repository=None,
) -> str:
    """Return birthday wish text from the LLM, or empty string on failure."""
    identity_hint = build_proactive_post_hint(
        bot_identity_fields(account, transport=transport, account_repository=account_repository),
        purpose='birthday wish',
    )

    instructions = (instructions or '').strip() or DEFAULT_INSTRUCTIONS
    context_parts = []
    if identity_hint:
        context_parts.append(identity_hint)
    if guild_name:
        context_parts.append(f'Server: {guild_name}')
    if channel_name:
        context_parts.append(f'Channel: #{channel_name}')
    if display_name:
        context_parts.append(f'Birthday person: {display_name}')
    if mention:
        context_parts.append(f'Mention to include: {mention}')
    if recent_chat:
        lines = recent_chat[-8:]
        if lines:
            context_parts.append('Recent channel activity:\n' + '\n'.join(lines))

    prompt = instructions
    if context_parts:
        prompt += '\n\n---\nContext:\n' + '\n'.join(context_parts)
    prompt += (
        '\n\n---\nWrite ONLY the message to post in Discord — no quotes, labels, or explanation. '
        'Wish them a happy birthday and include their mention.'
    )

    return run_proactive_llm(
        system,
        prompt=prompt,
        account=account,
        provider_key=provider_key,
        model_name=model_name,
        max_tokens=max_tokens,
        log_label='Birthday wish',
        transport=transport,
        account_repository=account_repository,
    )


def generate_bulk_birthday_wish(
    system,
    *,
    account: str = '',
    guild_name: str = '',
    channel_name: str = '',
    recipients: list[dict] | None = None,
    instructions: str = '',
    recent_chat: list | None = None,
    provider_key: str = '',
    model_name: str = '',
    max_tokens: int = 220,
    transport=None,
    account_repository=None,
) -> str:
    """Return a combined birthday wish for multiple recipients."""
    identity_hint = build_proactive_post_hint(
        bot_identity_fields(account, transport=transport, account_repository=account_repository),
        purpose='birthday wish',
    )

    instructions = (instructions or '').strip() or BULK_DEFAULT_INSTRUCTIONS
    context_parts = []
    if identity_hint:
        context_parts.append(identity_hint)
    if guild_name:
        context_parts.append(f'Server: {guild_name}')
    if channel_name:
        context_parts.append(f'Channel: #{channel_name}')
    recipient_lines = []
    for recipient in recipients or []:
        display_name = str(recipient.get('display_name') or '').strip()
        mention = str(recipient.get('mention') or '').strip()
        if display_name and mention:
            recipient_lines.append(f'- {display_name} ({mention})')
        elif mention:
            recipient_lines.append(f'- {mention}')
    if recipient_lines:
        context_parts.append('Birthday people today:\n' + '\n'.join(recipient_lines))
    if recent_chat:
        lines = recent_chat[-8:]
        if lines:
            context_parts.append('Recent channel activity:\n' + '\n'.join(lines))

    prompt = instructions
    if context_parts:
        prompt += '\n\n---\nContext:\n' + '\n'.join(context_parts)
    prompt += (
        '\n\n---\nWrite ONLY the message to post in Discord — no quotes, labels, or explanation. '
        'Wish them all a happy birthday and include every mention.'
    )

    return run_proactive_llm(
        system,
        prompt=prompt,
        account=account,
        provider_key=provider_key,
        model_name=model_name,
        max_tokens=max_tokens,
        log_label='Bulk birthday wish',
        transport=transport,
        account_repository=account_repository,
    )
