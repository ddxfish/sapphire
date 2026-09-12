"""Channel mention maps for resolving @names in outbound Discord messages."""

from __future__ import annotations

import threading

from plugins.discord.conversation.mentions import (
    apply_mention_map,
    build_mention_format_hint,
    merge_user_into_mention_map,
    mentioned_users_from_discord,
)


class MentionMapService:
    def __init__(self, *, message_repository=None, channel_repository=None, transport=None):
        self.message_repository = message_repository
        self.channel_repository = channel_repository
        self.transport = transport
        self._maps: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def channel_key(account_name: str, channel_id: str) -> str:
        return f'{account_name}:{channel_id}'

    def set_transport(self, transport) -> None:
        self.transport = transport

    def get_map(self, account_name: str, channel_id: str) -> dict[str, str]:
        key = self.channel_key(account_name, channel_id)
        with self._lock:
            return dict(self._maps.get(key) or {})

    def store_map(self, account_name: str, channel_id: str, mention_map: dict) -> dict[str, str]:
        key = self.channel_key(account_name, channel_id)
        stored = dict(mention_map or {})
        with self._lock:
            self._maps[key] = stored
        return stored

    def clear_all(self) -> None:
        with self._lock:
            self._maps.clear()

    def build_for_channel(
        self,
        account_name: str,
        channel_id: str,
        *,
        author_id: str = '',
        username: str = '',
        display_name: str = '',
        mentioned_users: list[dict] | None = None,
        limit: int = 50,
    ) -> dict[str, str]:
        rows = []
        if self.message_repository:
            rows = self.message_repository.get_recent_messages(account_name, channel_id, limit=limit)
        mention_map = dict(self.get_map(account_name, channel_id))
        for row in rows:
            user_id = str(row.get('author_id') or '').strip()
            if not user_id:
                continue
            uname, dname = self._user_names(user_id, row)
            merge_user_into_mention_map(
                mention_map,
                user_id,
                username=uname,
                display_name=dname,
            )
        if author_id:
            merge_user_into_mention_map(
                mention_map,
                author_id,
                username=username,
                display_name=display_name,
            )
        for user in mentioned_users or []:
            if not isinstance(user, dict):
                continue
            merge_user_into_mention_map(
                mention_map,
                str(user.get('id') or ''),
                username=str(user.get('username') or ''),
                display_name=str(user.get('display_name') or ''),
            )
        return self.store_map(account_name, channel_id, mention_map)

    def update_from_discord_message(self, account_name: str, message, observation) -> dict[str, str]:
        mentioned_users = mentioned_users_from_discord(message)
        return self.build_for_channel(
            account_name,
            observation.channel_id,
            author_id=observation.author_id,
            username=observation.username,
            display_name=observation.display_name,
            mentioned_users=mentioned_users,
        )

    def apply_text(
        self,
        text: str,
        account_name: str,
        channel_id: str,
        guild_id: str = '',
        *,
        mention_map: dict | None = None,
    ) -> str:
        if not text or '@' not in text and '<@' not in text:
            return text
        working = dict(mention_map or self.get_map(account_name, channel_id))
        resolved = apply_mention_map(
            text,
            working,
            transport=self.transport,
            account=account_name,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        self.store_map(account_name, channel_id, working)
        return resolved

    def mention_format_hint(self) -> str:
        return build_mention_format_hint()

    def _user_names(self, user_id: str, row: dict) -> tuple[str, str]:
        username = ''
        display_name = ''
        if self.channel_repository:
            user = self.channel_repository.get_user(user_id) or {}
            username = str(user.get('username') or '').strip()
            display_name = str(user.get('display_name') or '').strip()
        author_name = str(row.get('author_name') or '').strip()
        if not username:
            username = author_name
        if not display_name:
            display_name = author_name
        return username, display_name
