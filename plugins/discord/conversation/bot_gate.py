"""Replies to other bots: on/off + allowlist + a cascade brake (S4, 2026-09-22).

The debate sessions are gone. An allowlisted bot's message flows like a
human's (mention → reply, otherwise the channel's bot organic chance). The
brake: after MAX_CONSECUTIVE replies in a row triggered by bots in one channel
with no human in between, bots are ignored there until a human speaks.
"""
from __future__ import annotations

MAX_CONSECUTIVE = 5


def _ids(raw) -> set[str]:
    items = raw.replace(',', '\n').splitlines() if isinstance(raw, str) else list(raw or [])
    return {str(i).strip() for i in items if str(i).strip()}


class BotGate:
    def __init__(self):
        self._streak: dict[tuple[str, str], int] = {}

    def evaluate(self, observation, settings) -> dict:
        key = (str(observation.account_name), str(observation.channel_id))
        if not bool(getattr(observation, 'author_is_bot', False)):
            self._streak.pop(key, None)                 # a human speaking resets the brake
            return {'allowed': True, 'reason': 'human_message'}
        bot = getattr(settings, 'bot', None) if settings else None
        if bot is None or not getattr(bot, 'enabled', True):
            return {'allowed': False, 'reason': 'bot_interaction_disabled'}
        if str(getattr(observation, 'author_id', '') or '') not in _ids(getattr(bot, 'allowlist_ids', [])):
            return {'allowed': False, 'reason': 'bot_not_allowlisted'}
        if self._streak.get(key, 0) >= MAX_CONSECUTIVE:
            return {'allowed': False, 'reason': 'bot_cascade_cap'}
        return {'allowed': True, 'reason': 'bot_allowlisted'}

    def note_reply(self, account_name: str, channel_id: str, *, author_is_bot: bool) -> None:
        key = (str(account_name), str(channel_id))
        self._streak[key] = self._streak.get(key, 0) + 1 if author_is_bot else 0
