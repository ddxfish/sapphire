"""Bot self-identity hints for proactive LLM messages."""

from __future__ import annotations

def bot_identity_fields(
    account_name: str,
    *,
    transport=None,
    account_repository=None,
) -> dict:
    """Resolve the connected bot's Discord identity for an account."""
    account_name = str(account_name or '').strip()
    if transport and account_name:
        try:
            health = transport.account_health(account_name) or {}
            bot_id = str(health.get('bot_id') or '').strip()
            bot_username = str(health.get('bot_name') or '').strip()
            if bot_id or bot_username:
                return {
                    'bot_id': bot_id,
                    'bot_username': bot_username,
                    'bot_display_name': bot_username,
                }
        except Exception:
            pass

    if account_repository and account_name:
        try:
            account = account_repository.get_account(account_name) or {}
            bot_id = str(account.get('bot_id') or '').strip()
            bot_username = str(account.get('bot_name') or '').strip()
            if bot_id or bot_username:
                return {
                    'bot_id': bot_id,
                    'bot_username': bot_username,
                    'bot_display_name': bot_username,
                }
        except Exception:
            pass
    return {}


def bot_name_aliases(fields: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for key in ('bot_display_name', 'bot_username'):
        value = str(fields.get(key) or '').strip()
        lower = value.lower()
        if value and lower not in seen:
            names.append(value)
            seen.add(lower)
    return names
