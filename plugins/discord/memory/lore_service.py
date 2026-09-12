"""Shared server/channel lore — guild facts separate from per-user profiles."""

from __future__ import annotations


class LoreService:
    def __init__(self, *, lore_repository):
        self.lore_repository = lore_repository

    def remember(
        self,
        account_name: str,
        content: str,
        *,
        guild_id: str = '',
        channel_id: str = '',
        source: str = 'explicit',
        confidence: float = 1.0,
        pinned: bool = False,
    ) -> int:
        text = str(content or '').strip()
        if not text:
            raise ValueError('content required')
        return self.lore_repository.add(
            account_name,
            text,
            guild_id=guild_id,
            channel_id=channel_id,
            source=source,
            confidence=confidence,
            pinned=pinned,
        )

    def build_context(
        self,
        account_name: str,
        *,
        guild_id: str = '',
        channel_id: str = '',
        limit: int = 8,
    ) -> list[dict]:
        if not guild_id:
            return []
        return self.lore_repository.list_lore(
            account_name,
            guild_id=guild_id,
            channel_id=channel_id or None,
            limit=limit,
        )

    def list_lore(
        self,
        account_name: str,
        *,
        guild_id: str | None = None,
        include_forgotten: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        return self.lore_repository.list_lore(
            account_name,
            guild_id=guild_id,
            include_forgotten=include_forgotten,
            limit=limit,
        )

    def update(self, lore_id: int, content: str) -> dict | None:
        text = str(content or '').strip()
        if not text:
            raise ValueError('content required')
        return self.lore_repository.update_content(lore_id, text)

    def set_pinned(self, lore_id: int, pinned: bool) -> dict | None:
        return self.lore_repository.set_pinned(lore_id, pinned)

    def soft_forget(self, lore_id: int) -> dict | None:
        return self.lore_repository.soft_forget(lore_id)

    def restore(self, lore_id: int) -> dict | None:
        return self.lore_repository.restore(lore_id)

    def delete(self, lore_id: int) -> bool:
        return self.lore_repository.delete(lore_id)
