"""Voice channel occupancy helpers for auto-join and channel pickers."""

from __future__ import annotations


def _bot_id_int(bot_id: str | int | None) -> int:
    text = str(bot_id or '').strip()
    return int(text) if text.isdigit() else 0


def voice_channel_occupancy(channel, bot_id: str | int | None = None) -> dict:
    """Return human_count, member_count, and bot_connected for a voice channel."""
    guild = getattr(channel, 'guild', None)
    channel_id = getattr(channel, 'id', None)
    bot_id_int = _bot_id_int(bot_id)

    humans = 0
    members = 0
    bot_in_channel = False

    voice_states = []
    if guild is not None:
        raw_states = getattr(guild, 'voice_states', None)
        if raw_states is not None:
            try:
                voice_states = list(raw_states)
            except TypeError:
                voice_states = list(raw_states.values()) if hasattr(raw_states, 'values') else []

    if voice_states and channel_id is not None:
        for state in voice_states:
            state_channel = getattr(state, 'channel', None)
            if state_channel is None or getattr(state_channel, 'id', None) != channel_id:
                continue
            members += 1
            user = getattr(state, 'user', None) or getattr(state, 'member', None)
            uid = getattr(user, 'id', None)
            if uid is not None and int(uid) == bot_id_int:
                bot_in_channel = True
            elif uid is not None:
                humans += 1

    if members == 0:
        raw_members = list(getattr(channel, 'members', []) or [])
        members = len(raw_members)
        for member in raw_members:
            mid = getattr(member, 'id', None)
            if mid is not None and int(mid) == bot_id_int:
                bot_in_channel = True
            else:
                humans += 1

    if guild is not None and channel_id is not None:
        voice_client = getattr(guild, 'voice_client', None)
        if voice_client and getattr(voice_client, 'channel', None):
            if str(voice_client.channel.id) == str(channel_id):
                bot_in_channel = True

    return {
        'human_count': humans,
        'member_count': members,
        'bot_connected': bot_in_channel,
    }
