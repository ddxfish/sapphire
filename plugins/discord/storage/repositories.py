"""The host's three repositories over the one locked connection (S7)."""

from __future__ import annotations

import time

# Chunked deletes (H6, hunt 2026-09-12): one DELETE over a year of messages held
# the shared connection lock for seconds and stalled every reply. Each chunk
# commits and yields between rounds so the daemon loop and the voice pool get in.
PURGE_CHUNK = 5000
_CHUNK_PAUSE_SECONDS = 0.02


def delete_where(conn, table: str, where: str, params: tuple) -> int:
    total = 0
    while True:
        cursor = conn.execute(
            f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT {PURGE_CHUNK})',
            params,
        )
        conn.commit()
        deleted = int(cursor.rowcount or 0)
        total += deleted
        if deleted < PURGE_CHUNK:
            return total
        time.sleep(_CHUNK_PAUSE_SECONDS)


class AccountRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def list_accounts(self) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            'SELECT name, bot_name, bot_id, state, last_error, created_at, updated_at FROM accounts ORDER BY name'
        ).fetchall()
        return [dict(row) for row in rows]

    def get_account(self, name: str) -> dict | None:
        row = self.sqlite_service.connection().execute('SELECT * FROM accounts WHERE name = ?', (name,)).fetchone()
        return dict(row) if row else None

    def get_token(self, name: str) -> str | None:
        row = self.sqlite_service.connection().execute('SELECT token FROM accounts WHERE name = ?', (name,)).fetchone()
        return row['token'] if row else None

    def upsert_account(self, name: str, *, token: str, bot_name: str = '', bot_id: str = '',
                       state: str = 'disconnected', last_error: str = '') -> None:
        now = time.time()
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO accounts (name, token, bot_name, bot_id, state, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                token = excluded.token, bot_name = excluded.bot_name, bot_id = excluded.bot_id,
                state = excluded.state, last_error = excluded.last_error, updated_at = excluded.updated_at
            ''',
            (name, token, bot_name, bot_id, state, last_error, now, now),
        )
        conn.commit()

    def update_connection_state(self, name: str, state: str, *, bot_name: str = '', bot_id: str = '',
                                last_error: str = '') -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'UPDATE accounts SET state = ?, bot_name = ?, bot_id = ?, last_error = ?, updated_at = ? WHERE name = ?',
            (state, bot_name, bot_id, last_error, time.time(), name),
        )
        conn.commit()

    def delete_account(self, name: str) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute('DELETE FROM accounts WHERE name = ?', (name,))
        conn.commit()
        return int(cursor.rowcount or 0)


class ChannelRepository:
    """Guild / channel / user name caches — the pickers, the mention map and
    the transcript's author labels read them; inbound traffic fills them."""

    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def upsert_channel(self, channel_id: str, guild_id: str = '', name: str = '') -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'INSERT INTO channels (channel_id, guild_id, name) VALUES (?, ?, ?) '
            'ON CONFLICT(channel_id) DO UPDATE SET guild_id = excluded.guild_id, name = excluded.name',
            (channel_id, guild_id, name),
        )
        conn.commit()

    def get_channel(self, channel_id: str) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT channel_id, guild_id, name FROM channels WHERE channel_id = ?', (channel_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_guild(self, guild_id: str, name: str = '') -> None:
        if not guild_id:
            return
        conn = self.sqlite_service.connection()
        conn.execute(
            'INSERT INTO guilds (guild_id, name) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET name = excluded.name',
            (guild_id, name),
        )
        conn.commit()

    def get_guild_name(self, guild_id: str) -> str:
        if not guild_id:
            return ''
        row = self.sqlite_service.connection().execute(
            'SELECT name FROM guilds WHERE guild_id = ?', (str(guild_id),),
        ).fetchone()
        return str(row['name'] or '').strip() if row else ''

    def upsert_user(self, user_id: str, username: str = '', display_name: str = '') -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?) '
            'ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, display_name = excluded.display_name',
            (user_id, username, display_name),
        )
        conn.commit()

    def get_user(self, user_id: str) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT user_id, username, display_name FROM users WHERE user_id = ?', (user_id,),
        ).fetchone()
        return dict(row) if row else None


class MessageRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def save_message(self, observation) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            'INSERT OR REPLACE INTO messages (message_id, channel_id, author_id, content, created_at) VALUES (?, ?, ?, ?, ?)',
            (str(observation.message_id), str(observation.channel_id), str(observation.author_id),
             observation.clean_content, float(getattr(observation, 'created_at', time.time()))),
        )
        conn.commit()

    def get_recent_messages(self, account_name: str, channel_id: str, limit: int = 20) -> list[dict]:
        del account_name  # message rows are per channel, shared by every bot on this install
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT m.message_id, m.channel_id, m.author_id, m.content, m.created_at,
                   COALESCE(NULLIF(u.display_name, ''), NULLIF(u.username, ''), 'Unknown') AS author_name
            FROM messages m LEFT JOIN users u ON u.user_id = m.author_id
            WHERE m.channel_id = ? ORDER BY m.created_at DESC LIMIT ?
            ''',
            (str(channel_id), max(1, int(limit))),
        ).fetchall()
        items = [dict(row) for row in rows]
        items.reverse()
        return items

    def purge_before(self, cutoff_ts: float) -> int:
        return delete_where(self.sqlite_service.connection(), 'messages', 'created_at < ?', (float(cutoff_ts),))

    def forget_author(self, user_id: str) -> dict:
        """Every trace of one person: their message rows and their name cache row."""
        conn = self.sqlite_service.connection()
        removed = {'messages': delete_where(conn, 'messages', 'author_id = ?', (str(user_id),))}
        cursor = conn.execute('DELETE FROM users WHERE user_id = ?', (str(user_id),))
        conn.commit()
        removed['users'] = int(cursor.rowcount or 0)
        return removed
