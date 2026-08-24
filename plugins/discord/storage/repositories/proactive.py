"""Sleep state, mention buffers, and proactive cooldown persistence."""

from __future__ import annotations

import time


class ProactiveRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def get_sleep_state(self, account_name: str, channel_id: str) -> dict:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM sleep_state WHERE account_name = ? AND channel_id = ?',
            (account_name, channel_id),
        ).fetchone()
        if not row:
            return {
                'account_name': account_name,
                'channel_id': channel_id,
                'is_asleep': 0,
                'goodnight_sent': 0,
                'forced_wake_until': 0.0,
                'mention_count': 0,
            }
        return dict(row)

    def set_sleep_state(self, account_name: str, channel_id: str, **fields) -> dict:
        current = self.get_sleep_state(account_name, channel_id)
        current.update(fields)
        current['updated_at'] = time.time()
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO sleep_state
            (account_name, channel_id, is_asleep, goodnight_sent, forced_wake_until, mention_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_name, channel_id) DO UPDATE SET
                is_asleep = excluded.is_asleep,
                goodnight_sent = excluded.goodnight_sent,
                forced_wake_until = excluded.forced_wake_until,
                mention_count = excluded.mention_count,
                updated_at = excluded.updated_at
            ''',
            (
                account_name,
                channel_id,
                int(current.get('is_asleep', 0)),
                int(current.get('goodnight_sent', 0)),
                float(current.get('forced_wake_until', 0)),
                int(current.get('mention_count', 0)),
                current['updated_at'],
            ),
        )
        conn.commit()
        return self.get_sleep_state(account_name, channel_id)

    def buffer_mention(
        self,
        account_name: str,
        channel_id: str,
        *,
        message_id: str,
        author_id: str,
        content: str,
        mentioned: bool,
    ) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO sleep_buffer
            (account_name, channel_id, message_id, author_id, content, mentioned, created_at, processed)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ''',
            (account_name, channel_id, message_id, author_id, content, int(mentioned), time.time()),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_buffered(self, account_name: str, channel_id: str, *, limit: int = 10) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT * FROM sleep_buffer
            WHERE account_name = ? AND channel_id = ? AND processed = 0
            ORDER BY created_at ASC LIMIT ?
            ''',
            (account_name, channel_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_buffered_processed(self, buffer_ids: list[int]) -> None:
        if not buffer_ids:
            return
        conn = self.sqlite_service.connection()
        placeholders = ','.join('?' for _ in buffer_ids)
        conn.execute(
            f'UPDATE sleep_buffer SET processed = 1 WHERE id IN ({placeholders})',
            buffer_ids,
        )
        conn.commit()

    def record_cooldown(self, account_name: str, channel_id: str, action_type: str, sent_at: float | None = None) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO proactive_cooldowns (account_name, channel_id, action_type, last_sent_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_name, channel_id, action_type) DO UPDATE SET
                last_sent_at = excluded.last_sent_at
            ''',
            (account_name, channel_id, action_type, sent_at or time.time()),
        )
        conn.commit()

    def cooldown_elapsed(self, account_name: str, channel_id: str, action_type: str, *, min_seconds: float, now: float | None = None) -> bool:
        now = now or time.time()
        row = self.sqlite_service.connection().execute(
            '''
            SELECT last_sent_at FROM proactive_cooldowns
            WHERE account_name = ? AND channel_id = ? AND action_type = ?
            ''',
            (account_name, channel_id, action_type),
        ).fetchone()
        if not row:
            return True
        return (now - float(row['last_sent_at'])) >= min_seconds

    def record_channel_activity(self, account_name: str, channel_id: str, activity_at: float | None = None) -> None:
        self.record_cooldown(account_name, channel_id, 'human_activity', activity_at or time.time())

    def last_channel_activity(self, account_name: str, channel_id: str) -> float:
        row = self.sqlite_service.connection().execute(
            '''
            SELECT last_sent_at FROM proactive_cooldowns
            WHERE account_name = ? AND channel_id = ? AND action_type = ?
            ''',
            (account_name, channel_id, 'human_activity'),
        ).fetchone()
        return float(row['last_sent_at']) if row else 0.0
