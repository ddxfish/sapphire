"""Shared guild/channel lore — separate from per-user profile facts."""

from __future__ import annotations

import time


class LoreRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def add(
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
        now = time.time()
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO server_lore
            (account_name, guild_id, channel_id, content, source, confidence, pinned, forgotten, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''',
            (
                account_name,
                str(guild_id or ''),
                str(channel_id or ''),
                content,
                source,
                float(confidence),
                1 if pinned else 0,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_lore(
        self,
        account_name: str,
        *,
        guild_id: str | None = None,
        channel_id: str | None = None,
        include_forgotten: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """List lore for a bot, optionally scoped to a guild/channel.

        Guild-wide lore (empty channel_id) is included when a channel_id is
        requested so channel prompts see both shared and channel-specific facts.
        """
        query = 'SELECT * FROM server_lore WHERE account_name = ?'
        params: list = [account_name]
        if not include_forgotten:
            query += ' AND forgotten = 0'
        if guild_id is not None:
            query += ' AND guild_id = ?'
            params.append(str(guild_id))
        if channel_id is not None:
            # Channel-scoped view: channel facts + guild-wide (no channel).
            query += ' AND (channel_id = ? OR channel_id = \'\')'
            params.append(str(channel_id))
        query += ' ORDER BY pinned DESC, updated_at DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get(self, lore_id: int) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM server_lore WHERE id = ?',
            (int(lore_id),),
        ).fetchone()
        return dict(row) if row else None

    def update_content(self, lore_id: int, content: str) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE server_lore SET content = ?, updated_at = ? WHERE id = ?',
            (content, time.time(), int(lore_id)),
        )
        conn.commit()
        return self.get(lore_id)

    def set_pinned(self, lore_id: int, pinned: bool) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE server_lore SET pinned = ?, updated_at = ? WHERE id = ?',
            (1 if pinned else 0, time.time(), int(lore_id)),
        )
        conn.commit()
        return self.get(lore_id)

    def soft_forget(self, lore_id: int) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE server_lore SET forgotten = 1, updated_at = ? WHERE id = ?',
            (time.time(), int(lore_id)),
        )
        conn.commit()
        return self.get(lore_id)

    def restore(self, lore_id: int) -> dict | None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE server_lore SET forgotten = 0, updated_at = ? WHERE id = ?',
            (time.time(), int(lore_id)),
        )
        conn.commit()
        return self.get(lore_id)

    def delete(self, lore_id: int) -> bool:
        conn = self.sqlite_service.connection()
        cursor = conn.execute('DELETE FROM server_lore WHERE id = ?', (int(lore_id),))
        conn.commit()
        return cursor.rowcount > 0
