from __future__ import annotations

import time


class MessageRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def save_message(self, observation) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO messages (message_id, channel_id, author_id, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(observation.message_id),
                str(observation.channel_id),
                str(observation.author_id),
                observation.clean_content,
                float(getattr(observation, 'created_at', time.time())),
            ),
        )
        conn.commit()

    def get_recent_messages(self, account_name: str, channel_id: str, limit: int = 20) -> list[dict]:
        del account_name
        rows = self.sqlite_service.connection().execute(
            """
            SELECT
                m.message_id,
                m.channel_id,
                m.author_id,
                m.content,
                m.created_at,
                COALESCE(NULLIF(u.display_name, ''), NULLIF(u.username, ''), 'Unknown') AS author_name
            FROM messages m
            LEFT JOIN users u ON u.user_id = m.author_id
            WHERE m.channel_id = ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (str(channel_id), max(1, int(limit))),
        ).fetchall()
        items = [dict(row) for row in rows]
        items.reverse()
        return items
