"""Session-based gating for replies to other Discord bots."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from plugins.discord.conversation.name_match import message_matches_bot_name


@dataclass
class ChannelBotSession:
    human_window_until: float = 0.0
    last_engagement_at: float = 0.0
    exchanges: int = 0
    peer_bot_id: str = ''


class BotSessionService:
    """Tracks debate windows and prevents unbounded bot-to-bot cascades."""

    def __init__(self):
        self._sessions: dict[tuple[str, str], ChannelBotSession] = {}
        self._our_last_message_id: dict[tuple[str, str], str] = {}

    def record_sent_message(self, account_name: str, channel_id: str, message_id: str) -> None:
        key = (str(account_name), str(channel_id))
        if message_id:
            self._our_last_message_id[key] = str(message_id)
        session = self._session(key)
        session.last_engagement_at = time.time()

    def evaluate(
        self,
        observation,
        settings,
        *,
        respond_trigger: bool,
        bot_names: set[str],
        name_match_enabled: bool,
    ) -> dict:
        bot_settings = getattr(settings, 'bot', None) if settings else None
        author_is_bot = bool(getattr(observation, 'author_is_bot', False))
        now = time.time()
        key = (str(observation.account_name), str(observation.channel_id))
        session = self._session(key)

        if not author_is_bot:
            if respond_trigger and bot_settings is not None:
                window = max(60, int(getattr(bot_settings, 'session_human_window_seconds', 300)))
                session.human_window_until = now + window
                # A human (re)opening the debate starts a fresh session: the
                # safety cap is per-debate, not a lifetime channel budget.
                session.exchanges = 0
                session.peer_bot_id = ''
            return {'allowed': True, 'reason': 'human_message'}

        if bot_settings is None or not getattr(bot_settings, 'enabled', True):
            return {'allowed': False, 'reason': 'bot_interaction_disabled'}

        mode = str(getattr(bot_settings, 'reply_mode', 'allowlist') or 'allowlist')
        allowlist = _normalize_allowlist(getattr(bot_settings, 'allowlist_ids', []))
        author_id = str(getattr(observation, 'author_id', '') or '')

        if mode == 'never':
            return {'allowed': False, 'reason': 'bot_reply_disabled'}

        if mode == 'allowlist' and author_id not in allowlist:
            return {'allowed': False, 'reason': 'bot_not_allowlisted'}

        if mode not in {'allowlist', 'mentions_only', 'all'}:
            return {'allowed': False, 'reason': 'bot_reply_disabled'}

        our_message_id = self._our_last_message_id.get(key, '')
        addresses_us = _bot_addresses_us(
            observation,
            bot_names,
            name_match_enabled=name_match_enabled,
        )
        replies_to_us = _is_reply_to_us(observation, our_message_id)

        silence_seconds = max(30, int(getattr(bot_settings, 'session_silence_seconds', 150)))
        safety_max = max(1, int(getattr(bot_settings, 'session_safety_max_exchanges', 20)))
        human_window_open = now < session.human_window_until
        engagement_fresh = (
            session.last_engagement_at > 0
            and (now - session.last_engagement_at) < silence_seconds
        )

        if session.exchanges >= safety_max:
            return {'allowed': False, 'reason': 'bot_session_safety_cap'}

        # A direct address (@mention or name) is always enough.
        if addresses_us:
            return self._allow(session, author_id, now)

        # Reply-chain volley: only inside a human-opened debate window that
        # hasn't gone silent, and only with the debate's peer bot. (Reply-chain
        # used to live inside addresses_us, which made the window and silence
        # settings decorative — any reply to our last message was allowed
        # forever.)
        if replies_to_us and human_window_open and engagement_fresh \
                and session.peer_bot_id in ('', author_id):
            return self._allow(session, author_id, now)

        if mode == 'mentions_only' and not replies_to_us:
            return {'allowed': False, 'reason': 'bot_mentions_only'}
        if replies_to_us and human_window_open:
            return {'allowed': False, 'reason': 'bot_session_silent'}
        if human_window_open:
            return {'allowed': False, 'reason': 'bot_side_comment'}
        # Idle allowlisted / all-mode bots may get an organic chance roll
        # upstream (bot_response_chance). Mentions-only never reaches here.
        if mode in {'allowlist', 'all'}:
            return {
                'allowed': True,
                'reason': 'bot_organic_candidate',
                'organic_candidate': True,
            }
        return {'allowed': False, 'reason': 'no_bot_session'}

    def _allow(self, session: ChannelBotSession, peer_bot_id: str, now: float) -> dict:
        session.last_engagement_at = now
        session.peer_bot_id = peer_bot_id
        session.exchanges += 1
        return {'allowed': True, 'reason': 'bot_session_active'}

    def _session(self, key: tuple[str, str]) -> ChannelBotSession:
        if key not in self._sessions:
            self._sessions[key] = ChannelBotSession()
        return self._sessions[key]


def _normalize_allowlist(raw) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, str):
        items = raw.replace(',', '\n').splitlines()
    else:
        items = list(raw)
    return {str(item).strip() for item in items if str(item).strip()}


def _bot_addresses_us(
    observation,
    bot_names: set[str],
    *,
    name_match_enabled: bool,
) -> bool:
    """Direct address only: @mention or name. Reply-chains are session-gated."""
    if bool(getattr(observation, 'mentioned', False)):
        return True
    return bool(name_match_enabled and message_matches_bot_name(
        getattr(observation, 'clean_content', '') or '',
        bot_names,
    ))


def _is_reply_to_us(observation, our_message_id: str) -> bool:
    reply_to = str(getattr(observation, 'reply_to_message_id', '') or '')
    return bool(reply_to and our_message_id and reply_to == our_message_id)
