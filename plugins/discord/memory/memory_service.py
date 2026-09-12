"""Channel memory recall and pinned memories."""

from __future__ import annotations


class MemoryService:
    def __init__(self, *, memory_repository, message_repository):
        self.memory_repository = memory_repository
        self.message_repository = message_repository

    def pin_memory(
        self,
        account_name: str,
        guild_id: str,
        channel_id: str,
        author_id: str,
        username: str,
        content: str,
    ) -> int:
        return self.memory_repository.pin_memory(
            account_name, guild_id, channel_id, author_id, username, content,
        )

    def get_pinned(self, account_name: str, *, guild_id: str | None = None, limit: int = 20) -> list[dict]:
        return self.memory_repository.list_pinned(account_name, guild_id=guild_id, limit=limit)

    def recall(self, account_name: str, guild_id: str, channel_id: str, needle: str, *, limit: int = 10) -> list[dict]:
        recent = self.message_repository.get_recent_messages(account_name, channel_id, limit=limit)
        pinned = self.memory_repository.search_pinned(account_name, guild_id, needle, limit=limit)
        items = []
        needle_lower = needle.lower()
        for row in recent:
            if needle_lower in (row.get('content') or '').lower():
                items.append({'kind': 'message', 'content': row['content'], 'created_at': row['created_at']})
        for row in pinned:
            items.append({'kind': 'pinned', 'content': row['content'], 'created_at': row['created_at']})
        items.sort(key=lambda item: item['created_at'], reverse=True)
        return items[:limit]

    def forget_user(self, account_name: str, user_id: str) -> None:
        self.memory_repository.forget_user(account_name, user_id)
