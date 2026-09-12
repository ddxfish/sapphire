"""Channel metadata and settings overlay repository."""

from __future__ import annotations

import json
import time

from plugins.discord.models.settings import SettingsOverlay, SettingsStore


class ChannelRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def upsert_channel(self, channel_id: str, guild_id: str = '', name: str = '') -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO channels (channel_id, guild_id, name) VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET guild_id = excluded.guild_id, name = excluded.name
            ''',
            (channel_id, guild_id, name),
        )
        conn.commit()

    def get_channel(self, channel_id: str) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT channel_id, guild_id, name FROM channels WHERE channel_id = ?',
            (channel_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_guild(self, guild_id: str, name: str = '') -> None:
        if not guild_id:
            return
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO guilds (guild_id, name) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET name = excluded.name
            ''',
            (guild_id, name),
        )
        conn.commit()

    def upsert_user(self, user_id: str, username: str = '', display_name: str = '') -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name
            ''',
            (user_id, username, display_name),
        )
        conn.commit()

    def get_guild_name(self, guild_id: str) -> str:
        if not guild_id:
            return ''
        row = self.sqlite_service.connection().execute(
            'SELECT name FROM guilds WHERE guild_id = ?',
            (str(guild_id),),
        ).fetchone()
        return str(row['name'] or '').strip() if row else ''

    def get_user(self, user_id: str) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT user_id, username, display_name FROM users WHERE user_id = ?',
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def save_settings_override(self, scope_type: str, scope_id: str, overlay: SettingsOverlay) -> None:
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO settings_overrides (scope_type, scope_id, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_id) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at
            ''',
            (scope_type, scope_id, json.dumps(overlay.to_dict()), time.time()),
        )
        conn.commit()

    def load_settings_store(self) -> SettingsStore:
        conn = self.sqlite_service.connection()
        rows = conn.execute('SELECT scope_type, scope_id, payload_json FROM settings_overrides').fetchall()
        payload = {'global': {}, 'guilds': {}, 'channels': {}, 'dms': {}}
        for row in rows:
            data = json.loads(row['payload_json'])
            if row['scope_type'] == 'global':
                payload['global'] = data
            elif row['scope_type'] == 'guild':
                payload['guilds'][row['scope_id']] = data
            elif row['scope_type'] == 'channel':
                payload['channels'][row['scope_id']] = data
            elif row['scope_type'] == 'dm':
                payload['dms'][row['scope_id']] = data
        return SettingsStore.from_dict(payload)
