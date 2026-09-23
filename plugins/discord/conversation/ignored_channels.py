"""Global ignored-channel list (`account:channel_id` entries)."""

from __future__ import annotations



def parse_target(entry) -> tuple[str, str] | None:
    """Return (account_name, channel_id) for an `account:channel_id` entry
    (or a dict with those keys)."""
    if isinstance(entry, dict):
        account = str(entry.get('account', '')).strip()
        channel_id = str(entry.get('channel_id', '')).strip()
        return (account, channel_id) if account and channel_id else None
    text = str(entry or '').strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(':') if part.strip()]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) >= 3:
        return parts[0], parts[-1]
    import logging
    logging.getLogger(__name__).warning('Discord channel target %r is not account:channel_id — ignored', text)
    return None


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
