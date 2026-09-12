"""Profile buffer rows awaiting ambient distill."""

from __future__ import annotations

import time


class ProfileBufferRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def add(
        self,
        account_name: str,
        user_id: str,
        content: str,
        *,
        created_at: float | None = None,
    ) -> int:
        text = str(content or '').strip()
        if not text:
            return 0
        now = float(created_at if created_at is not None else time.time())
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO profile_buffers (account_name, user_id, content, created_at, processed)
            VALUES (?, ?, ?, ?, 0)
            ''',
            (account_name, user_id, text[:500], now),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_pending_users(self, account_name: str, *, min_count: int = 1) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT user_id, COUNT(*) AS pending_count, MIN(created_at) AS oldest_at, MAX(created_at) AS newest_at
            FROM profile_buffers
            WHERE account_name = ? AND processed = 0
            GROUP BY user_id
            HAVING COUNT(*) >= ?
            ORDER BY pending_count DESC
            ''',
            (account_name, max(1, int(min_count))),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_unprocessed_for_user(
        self,
        account_name: str,
        user_id: str,
        *,
        limit: int = 40,
    ) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT id, account_name, user_id, content, created_at, processed
            FROM profile_buffers
            WHERE account_name = ? AND user_id = ? AND processed = 0
            ORDER BY created_at ASC
            LIMIT ?
            ''',
            (account_name, user_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_processed(self, buffer_ids: list[int]) -> int:
        ids = [int(i) for i in buffer_ids if i]
        if not ids:
            return 0
        conn = self.sqlite_service.connection()
        placeholders = ','.join('?' for _ in ids)
        cursor = conn.execute(
            f'UPDATE profile_buffers SET processed = 1 WHERE id IN ({placeholders})',
            ids,
        )
        conn.commit()
        return cursor.rowcount

    def pending_count(self, account_name: str, user_id: str | None = None) -> int:
        if user_id:
            row = self.sqlite_service.connection().execute(
                '''
                SELECT COUNT(*) FROM profile_buffers
                WHERE account_name = ? AND user_id = ? AND processed = 0
                ''',
                (account_name, user_id),
            ).fetchone()
        else:
            row = self.sqlite_service.connection().execute(
                '''
                SELECT COUNT(*) FROM profile_buffers
                WHERE account_name = ? AND processed = 0
                ''',
                (account_name,),
            ).fetchone()
        return int(row[0] if row else 0)

    def forget_user(self, account_name: str, user_id: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'DELETE FROM profile_buffers WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        )
        conn.commit()
