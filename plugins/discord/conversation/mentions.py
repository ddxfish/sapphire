"""Custom emoji resolution and @name mention replacement."""

from __future__ import annotations

import re

_SNOWFLAKE_RE = re.compile(r'^\d{17,20}$')
_ANGLE_MENTION_RE = re.compile(r'<@!?([^>]+)>')
_BARE_AT_FALLBACK_RE = re.compile(
    r'@([\w][\w .\'\u2019-]*?)'
    r'(?=\s*[—–-]|\s{2,}|[,.;:!?)\]]|\s+and\s|\s+or\s|$)',
    re.UNICODE | re.IGNORECASE,
)


def mentioned_users_from_discord(message) -> list[dict]:
    """Discord message.mentions as serializable dicts for the mention map."""
    out = []
    for user in getattr(message, 'mentions', None) or []:
        out.append({
            'id': str(user.id),
            'username': user.name or '',
            'display_name': getattr(user, 'display_name', None) or user.name or '',
        })
    return out


def merge_user_into_mention_map(
    mention_map: dict,
    user_id: str,
    *,
    username: str = '',
    display_name: str = '',
) -> None:
    if not user_id:
        return
    uid = str(user_id)
    if username:
        mention_map[username.strip().lower()] = uid
    dname = (display_name or '').strip()
    uname = (username or '').strip()
    if dname and dname.lower() != uname.lower():
        mention_map[dname.lower()] = uid


def build_mention_format_hint() -> str:
    return (
        'When @mentioning other users, write @DisplayName or @username only '
        '(e.g. @Spike le Vain). Do not use <@DisplayName> or made-up IDs — '
        'the plugin converts @DisplayName to real Discord pings.'
    )


def _get_guild(transport, account: str, guild_id: str):
    if not transport or not account or not guild_id:
        return None
    state = getattr(transport, '_accounts', {}).get(account) or {}
    client = state.get('client')
    if not client or not getattr(client, 'is_ready', lambda: False)():
        return None
    try:
        return client.get_guild(int(guild_id))
    except (TypeError, ValueError):
        return None


def resolve_guild_for_channel(transport, account: str, channel_id: str):
    if not transport or not account:
        return None
    state = getattr(transport, '_accounts', {}).get(account) or {}
    client = state.get('client')
    if not client or not getattr(client, 'is_ready', lambda: False)():
        return None
    try:
        channel_id_int = int(str(channel_id).strip())
    except (TypeError, ValueError):
        return None
    for guild in getattr(client, 'guilds', []) or []:
        if guild.get_channel(channel_id_int):
            return guild
    return None


def resolve_user_id(name: str, mention_map: dict, guild) -> str | None:
    key = (name or '').strip().lower()
    if not key:
        return None
    uid = mention_map.get(key)
    if uid:
        return str(uid)
    if guild:
        member = guild.get_member_named(name.strip())
        if member:
            mention_map[key] = str(member.id)
            return str(member.id)
    return None


def _lookup_name_keys(mention_map: dict, guild) -> list[str]:
    keys = set(mention_map.keys())
    if guild:
        for member in guild.members:
            keys.add(member.display_name.lower())
            keys.add(member.name.lower())
    return sorted(keys, key=len, reverse=True)


def _replace_angle_mentions(text: str, mention_map: dict, guild) -> str:
    def _fix(match):
        inner = match.group(1).strip()
        if _SNOWFLAKE_RE.match(inner):
            return match.group(0)
        uid = resolve_user_id(inner, mention_map, guild)
        return f'<@{uid}>' if uid else match.group(0)

    return _ANGLE_MENTION_RE.sub(_fix, text)


def _replace_bare_at_mentions(text: str, mention_map: dict, guild) -> str:
    if '@' not in text:
        return text

    lookup_keys = _lookup_name_keys(mention_map, guild)
    out = []
    i = 0
    n = len(text)

    while i < n:
        if text[i] == '@' and (i == 0 or text[i - 1] != '<'):
            rest_lower = text[i + 1 :].lower()
            matched = False
            for key in lookup_keys:
                if key and rest_lower.startswith(key):
                    end = i + 1 + len(key)
                    if end == n or text[end] in " \t\n,;:.!?)]'\"—–-":
                        uid = resolve_user_id(key, mention_map, guild)
                        if uid:
                            out.append(f'<@{uid}>')
                            i = end
                            matched = True
                            break
            if matched:
                continue

            match = _BARE_AT_FALLBACK_RE.match(text[i:])
            if match:
                candidate = match.group(1).strip()
                uid = resolve_user_id(candidate, mention_map, guild)
                if uid:
                    out.append(f'<@{uid}>')
                    i += match.end()
                    continue

        out.append(text[i])
        i += 1

    return ''.join(out)


def apply_mention_map(
    text: str,
    mention_map: dict,
    *,
    transport=None,
    account: str = '',
    guild_id: str = '',
    channel_id: str = '',
) -> str:
    if not text:
        return text

    working = dict(mention_map or {})
    guild = _get_guild(transport, account, guild_id)
    if guild is None and transport and account and channel_id:
        guild = resolve_guild_for_channel(transport, account, channel_id)
        if guild and not guild_id:
            guild_id = str(guild.id)
    text = _replace_angle_mentions(text, working, guild)
    text = _replace_bare_at_mentions(text, working, guild)
    if mention_map is not None:
        mention_map.update(working)
    return text
