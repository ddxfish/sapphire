"""Presence state persistence."""

from __future__ import annotations

import time


class PresenceRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def get_presence(self, account_name: str) -> dict:
        row = self.sqlite_service.connection().execute(
            'SELECT account_name, status, activity, updated_at FROM presence_state WHERE account_name = ?',
            (account_name,),
        ).fetchone()
        if not row:
            return {'account_name': account_name, 'status': 'online', 'activity': '', 'updated_at': 0.0}
        return dict(row)

    def save_presence(self, account_name: str, status: str, activity: str = '') -> dict:
        conn = self.sqlite_service.connection()
        now = time.time()
        conn.execute(
            '''
            INSERT INTO presence_state (account_name, status, activity, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_name) DO UPDATE SET
                status = excluded.status,
                activity = excluded.activity,
                updated_at = excluded.updated_at
            ''',
            (account_name, status, activity, now),
        )
        conn.commit()
        return self.get_presence(account_name)
