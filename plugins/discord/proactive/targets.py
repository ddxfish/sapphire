"""Parse proactive channel target entries from settings."""

from __future__ import annotations


def parse_target(entry) -> tuple[str, str] | None:
    """Return (account_name, channel_id) for a greeting/outreach/sleep target."""
    if isinstance(entry, dict):
        account = str(entry.get('account', '')).strip()
        channel_id = str(entry.get('channel_id', '')).strip()
        if account and channel_id:
            return account, channel_id
        return None
    text = str(entry or '').strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(':') if part.strip()]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) >= 3:
        return parts[0], parts[-1]
    # A typo'd target used to vanish silently and the panel echoed only the
    # parsed ones (hunt 2.13.0, row 43).
    import logging
    logging.getLogger(__name__).warning('Discord proactive target %r is not account:channel_id — ignored', text)
    return None
