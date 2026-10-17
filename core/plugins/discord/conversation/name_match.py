"""Detect when a message addresses the bot by name (without @mention)."""

from __future__ import annotations


def bot_names_for_account(account_name: str, *, transport=None, account_repository=None) -> set[str]:
    names: set[str] = set()
    account_name = str(account_name or '').strip()
    if account_name:
        names.add(account_name)
    if transport:
        health = transport.account_health(account_name)
        bot_name = str(health.get('bot_name') or '').strip()
        if bot_name:
            names.add(bot_name)
    if account_repository:
        account = account_repository.get_account(account_name)
        if account:
            bot_name = str(account.get('bot_name') or '').strip()
            if bot_name:
                names.add(bot_name)
    return names


def message_matches_bot_name(
    content: str,
    bot_names: set[str],
    *,
    case_sensitive: bool = False,
) -> bool:
    if not content or not bot_names:
        return False
    if case_sensitive:
        return any(name in content for name in bot_names if name)
    lower = content.lower()
    return any(name.lower() in lower for name in bot_names if name)
