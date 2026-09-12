"""Format stored channel history for proactive LLM prompts."""

from __future__ import annotations

from plugins.discord.conversation.transcript_service import format_recent_history
from plugins.discord.proactive.bot_identity import bot_identity_fields, bot_name_aliases


def format_proactive_history(
    rows: list[dict],
    *,
    account_name: str = '',
    transport=None,
    account_repository=None,
    line_max_chars: int = 1000,
) -> list[str]:
    """Recent chat for greeting/goodnight — bot lines labeled as 'You'."""
    fields = bot_identity_fields(
        account_name,
        transport=transport,
        account_repository=account_repository,
    )
    bot_names = {name.lower() for name in bot_name_aliases(fields)}
    lines: list[str] = []
    for line in format_recent_history(rows, line_max_chars=line_max_chars):
        author, _, remainder = line.partition(':')
        author_key = author.strip().lower()
        if author_key in bot_names:
            line = f'You:{remainder}'
        lines.append(line)
    return lines
