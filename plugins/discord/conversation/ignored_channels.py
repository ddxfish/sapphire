"""Global ignored-channel list (`account:channel_id` entries)."""

from __future__ import annotations

from plugins.discord.proactive.targets import parse_target


def ignored_channel_entries(settings) -> list:
    channel = getattr(settings, 'channel', None) if settings else None
    return list(getattr(channel, 'ignored_channels', None) or [])


def is_channel_ignored(account_name: str, channel_id: str, settings) -> bool:
    """True when this bot+channel is on the operator ignore list."""
    account = str(account_name or '').strip()
    channel = str(channel_id or '').strip()
    if not account or not channel:
        return False
    for entry in ignored_channel_entries(settings):
        parsed = parse_target(entry)
        if not parsed:
            # Bare channel id still matches (single-bot installs).
            if str(entry or '').strip() == channel:
                return True
            continue
        entry_account, entry_channel = parsed
        if entry_channel == channel and (entry_account == account or not entry_account):
            return True
    return False
