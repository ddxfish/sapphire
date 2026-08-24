"""Account repository."""

from __future__ import annotations

import time


class AccountRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def list_accounts(self) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            'SELECT name, bot_name, bot_id, state, last_error, created_at, updated_at FROM accounts ORDER BY name'
        ).fetchall()
        return [dict(row) for row in rows]

    def get_account(self, name: str) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM accounts WHERE name = ?', (name,)
        ).fetchone()
        return dict(row) if row else None

    def get_token(self, name: str) -> str | None:
        row = self.sqlite_service.connection().execute(
            'SELECT token FROM accounts WHERE name = ?', (name,)
        ).fetchone()
        return row['token'] if row else None

    def upsert_account(self, name: str, *, token: str, bot_name: str = '', bot_id: str = '', state: str = 'disconnected', last_error: str = '') -> None:
        now = time.time()
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO accounts (name, token, bot_name, bot_id, state, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                token = excluded.token,
                bot_name = excluded.bot_name,
                bot_id = excluded.bot_id,
                state = excluded.state,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            ''',
            (name, token, bot_name, bot_id, state, last_error, now, now),
        )
        conn.commit()

    def update_connection_state(self, name: str, state: str, *, bot_name: str = '', bot_id: str = '', last_error: str = '') -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE accounts SET state = ?, bot_name = ?, bot_id = ?, last_error = ?, updated_at = ? WHERE name = ?',
            (state, bot_name, bot_id, last_error, time.time(), name),
        )
        conn.commit()

    def delete_account(self, name: str) -> None:
        conn = self.sqlite_service.connection()
        conn.execute('DELETE FROM accounts WHERE name = ?', (name,))
        conn.commit()
