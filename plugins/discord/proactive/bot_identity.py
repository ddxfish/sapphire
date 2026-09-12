"""Bot self-identity hints for proactive LLM messages."""

from __future__ import annotations

import re


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


def build_proactive_post_hint(fields: dict, *, purpose: str = 'greeting') -> str:
    """Framing for greeting/goodnight LLM — the bot is the speaker, not the audience."""
    names = bot_name_aliases(fields)
    if not names:
        return (
            'You are posting a proactive Discord message as this bot account. '
            'Address the humans in the channel, not yourself.'
        )

    who = names[0]
    if len(names) > 1 and fields.get('bot_username'):
        username = str(fields.get('bot_username') or '').strip()
        if username and username != who:
            who = f'{who} (@{username})'

    lines = [
        f'You are {who} posting a message in this Discord channel.',
        'Write TO the people in the channel — you are the speaker, not the audience.',
    ]
    if purpose == 'greeting':
        lines.append(
            'This is a good-morning style greeting for the channel or whoever is around — '
            'not a message to yourself.'
        )
    elif purpose == 'goodnight':
        lines.append(
            'This is a good-night sign-off for the channel — you are going to sleep until morning. '
            'Not a message to yourself.'
        )
    else:
        lines.append('Casually check in with the channel — do not address yourself by name.')

    quoted = ', '.join(f'"{name}"' for name in names)
    lines.append(f'Never greet or address {quoted} — that is you.')
    lines.append('Lines labeled "You:" in recent chat are your own prior messages.')
    return '\n'.join(lines)


def strip_self_address(text: str, fields: dict) -> str:
    """Remove accidental self-greetings like 'Morning, Remmi —'."""
    out = (text or '').strip()
    if not out:
        return out

    for name in bot_name_aliases(fields):
        pattern = re.compile(
            rf'^((?:Good\s+)?Morning|Hey|Hi|Hello)\s*,?\s*{re.escape(name)}\s*(?:[,—–-]\s*)?',
            re.IGNORECASE,
        )
        out = pattern.sub(r'\1 — ', out, count=1)
        out = re.sub(
            rf',\s*{re.escape(name)}\s*([,—–-])',
            r' \1',
            out,
            count=1,
            flags=re.IGNORECASE,
        )
    return out.strip()
