"""Relationship milestone persistence."""

from __future__ import annotations

import sqlite3
import time


class MilestoneRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def record(
        self,
        account_name: str,
        user_id: str,
        *,
        milestone_type: str,
        milestone_key: str,
        detail: str = '',
        created_at: float | None = None,
    ) -> dict | None:
        """Insert a milestone if the key is new. Returns the row, or None if duplicate."""
        now = float(created_at if created_at is not None else time.time())
        conn = self.sqlite_service.connection()
        try:
            cursor = conn.execute(
                '''
                INSERT INTO relationship_milestones
                (account_name, user_id, milestone_type, milestone_key, detail, acknowledged, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                ''',
                (account_name, user_id, milestone_type, milestone_key, detail, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # UNIQUE(account_name, user_id, milestone_key) — already recorded.
            return None
        row = conn.execute(
            'SELECT * FROM relationship_milestones WHERE id = ?',
            (int(cursor.lastrowid),),
        ).fetchone()
        return dict(row) if row else None

    def list_for_user(
        self,
        account_name: str,
        user_id: str,
        *,
        limit: int = 20,
        unacknowledged_only: bool = False,
    ) -> list[dict]:
        query = '''
            SELECT * FROM relationship_milestones
            WHERE account_name = ? AND user_id = ?
        '''
        params: list = [account_name, user_id]
        if unacknowledged_only:
            query += ' AND acknowledged = 0'
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(max(1, int(limit)))
        rows = self.sqlite_service.connection().execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def acknowledge(self, milestone_id: int) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE relationship_milestones SET acknowledged = 1 WHERE id = ?',
            (int(milestone_id),),
        )
        conn.commit()

    def acknowledge_many(self, milestone_ids: list[int]) -> int:
        ids = [int(mid) for mid in milestone_ids if mid]
        if not ids:
            return 0
        conn = self.sqlite_service.connection()
        placeholders = ','.join('?' for _ in ids)
        cursor = conn.execute(
            f'UPDATE relationship_milestones SET acknowledged = 1 WHERE id IN ({placeholders})',
            ids,
        )
        conn.commit()
        return cursor.rowcount

    def forget_user(self, account_name: str, user_id: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'DELETE FROM relationship_milestones WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        )
        conn.commit()
