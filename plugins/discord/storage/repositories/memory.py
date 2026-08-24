"""Pinned memory and recall repository."""

from __future__ import annotations

import time


class MemoryRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def pin_memory(
        self,
        account_name: str,
        guild_id: str,
        channel_id: str,
        author_id: str,
        username: str,
        content: str,
    ) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO pinned_memories
            (account_name, guild_id, channel_id, author_id, username, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (account_name, guild_id, channel_id, author_id, username, content, time.time()),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_pinned(
        self,
        account_name: str,
        *,
        guild_id: str | None = None,
        channel_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        query = 'SELECT * FROM pinned_memories WHERE account_name = ?'
        params: list = [account_name]
        if guild_id is not None:
            query += ' AND guild_id = ?'
            params.append(guild_id)
        if channel_id is not None:
            query += ' AND channel_id = ?'
            params.append(channel_id)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def search_pinned(self, account_name: str, guild_id: str, needle: str, limit: int = 10) -> list[dict]:
        pattern = f'%{needle}%'
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT * FROM pinned_memories
            WHERE account_name = ? AND guild_id = ? AND content LIKE ?
            ORDER BY created_at DESC LIMIT ?
            ''',
            (account_name, guild_id, pattern, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def forget_user(self, account_name: str, user_id: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'DELETE FROM pinned_memories WHERE account_name = ? AND author_id = ?',
            (account_name, user_id),
        )
        conn.commit()
