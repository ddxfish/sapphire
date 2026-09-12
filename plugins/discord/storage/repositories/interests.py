"""Per-user interest topic graph."""

from __future__ import annotations

import time


class InterestRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def bump(
        self,
        account_name: str,
        user_id: str,
        topic: str,
        *,
        weight_delta: float = 1.0,
        seen_at: float | None = None,
    ) -> dict:
        topic = str(topic or '').strip().lower()
        if not topic:
            raise ValueError('topic required')
        now = float(seen_at if seen_at is not None else time.time())
        conn = self.sqlite_service.connection()
        row = conn.execute(
            '''
            SELECT * FROM interest_topics
            WHERE account_name = ? AND user_id = ? AND topic = ?
            ''',
            (account_name, user_id, topic),
        ).fetchone()
        if row:
            conn.execute(
                '''
                UPDATE interest_topics
                SET weight = weight + ?, mention_count = mention_count + 1, last_seen_at = ?
                WHERE account_name = ? AND user_id = ? AND topic = ?
                ''',
                (float(weight_delta), now, account_name, user_id, topic),
            )
        else:
            conn.execute(
                '''
                INSERT INTO interest_topics
                (account_name, user_id, topic, weight, mention_count, last_seen_at, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ''',
                (account_name, user_id, topic, float(weight_delta), now, now),
            )
        conn.commit()
        return dict(conn.execute(
            '''
            SELECT * FROM interest_topics
            WHERE account_name = ? AND user_id = ? AND topic = ?
            ''',
            (account_name, user_id, topic),
        ).fetchone())

    def list_for_user(
        self,
        account_name: str,
        user_id: str,
        *,
        limit: int = 10,
    ) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT * FROM interest_topics
            WHERE account_name = ? AND user_id = ?
            ORDER BY weight DESC, last_seen_at DESC
            LIMIT ?
            ''',
            (account_name, user_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def top_for_users(
        self,
        account_name: str,
        user_ids: list[str],
        *,
        limit: int = 8,
    ) -> list[dict]:
        """Aggregate top topics across a set of users (for channel outreach)."""
        ids = [str(uid) for uid in user_ids if uid]
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        rows = self.sqlite_service.connection().execute(
            f'''
            SELECT topic, SUM(weight) AS weight, SUM(mention_count) AS mention_count,
                   MAX(last_seen_at) AS last_seen_at
            FROM interest_topics
            WHERE account_name = ? AND user_id IN ({placeholders})
            GROUP BY topic
            ORDER BY weight DESC, last_seen_at DESC
            LIMIT ?
            ''',
            [account_name, *ids, max(1, int(limit))],
        ).fetchall()
        return [dict(row) for row in rows]

    def forget_user(self, account_name: str, user_id: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'DELETE FROM interest_topics WHERE account_name = ? AND user_id = ?',
            (account_name, user_id),
        )
        conn.commit()
