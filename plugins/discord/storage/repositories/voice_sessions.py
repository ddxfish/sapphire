"""Voice session, transcript, and summary persistence."""

from __future__ import annotations

import json
import time
import uuid

from plugins.discord.models.voice import VoiceMode, VoiceSession


class VoiceSessionRepository:
    def __init__(self, sqlite_service):
        self.sqlite_service = sqlite_service

    def create_session(
        self,
        account_name: str,
        guild_id: str,
        channel_id: str,
        *,
        mode: str = 'listen_only',
        session_id: str | None = None,
    ) -> VoiceSession:
        session_id = session_id or f'voice:{account_name}:{channel_id}:{uuid.uuid4().hex[:8]}'
        now = time.time()
        conn = self.sqlite_service.connection()
        conn.execute(
            '''
            INSERT INTO voice_sessions
            (session_id, account_name, guild_id, channel_id, state, mode, started_at, ended_at,
             participants_json, health)
            VALUES (?, ?, ?, ?, 'active', ?, ?, 0, '[]', 'connected')
            ''',
            (session_id, account_name, guild_id, channel_id, mode, now),
        )
        conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> VoiceSession | None:
        row = self.sqlite_service.connection().execute(
            '''
            SELECT id, session_id, account_name, guild_id, channel_id, state, mode,
                   started_at, ended_at, participants_json, health
            FROM voice_sessions WHERE session_id = ?
            ''',
            (session_id,),
        ).fetchone()
        if not row:
            return None
        payload = dict(row)
        if not payload.get('session_id'):
            payload['session_id'] = str(payload.get('id'))
        return VoiceSession.from_row(payload)

    def get_active_session(self, account_name: str, channel_id: str) -> VoiceSession | None:
        row = self.sqlite_service.connection().execute(
            '''
            SELECT id, session_id, account_name, guild_id, channel_id, state, mode,
                   started_at, ended_at, participants_json, health
            FROM voice_sessions
            WHERE account_name = ? AND channel_id = ? AND state = 'active'
            ORDER BY started_at DESC LIMIT 1
            ''',
            (account_name, channel_id),
        ).fetchone()
        if not row:
            return None
        payload = dict(row)
        if not payload.get('session_id'):
            payload['session_id'] = str(payload.get('id'))
        return VoiceSession.from_row(payload)

    def update_session(self, session_id: str, **fields) -> VoiceSession | None:
        session = self.get_session(session_id)
        if not session:
            return None
        allowed = {'mode', 'participants_json', 'health', 'state', 'ended_at'}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if 'participants' in fields:
            updates['participants_json'] = json.dumps(list(fields['participants']))
        if not updates:
            return session
        assignments = ', '.join(f'{key} = ?' for key in updates)
        params = list(updates.values()) + [session_id]
        conn = self.sqlite_service.connection()
        conn.execute(f'UPDATE voice_sessions SET {assignments} WHERE session_id = ?', params)
        conn.commit()
        return self.get_session(session_id)

    def close_session(self, session_id: str) -> VoiceSession | None:
        return self.update_session(session_id, state='closed', ended_at=time.time(), health='disconnected')

    def add_transcript(
        self,
        session_id: str,
        account_name: str,
        channel_id: str,
        text: str,
        *,
        speaker_id: str = '',
        speaker_name: str = '',
        confidence: float = 0.5,
        started_at: float | None = None,
        ended_at: float = 0.0,
    ) -> int:
        now = time.time()
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO voice_transcripts
            (session_id, account_name, channel_id, speaker_id, speaker_name, text,
             confidence, started_at, ended_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                session_id, account_name, channel_id, speaker_id, speaker_name, text,
                confidence, started_at or now, ended_at, now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def list_transcripts(self, session_id: str, *, limit: int = 100) -> list[dict]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT id, session_id, account_name, channel_id, speaker_id, speaker_name,
                   text, confidence, started_at, ended_at, created_at
            FROM voice_transcripts WHERE session_id = ? ORDER BY created_at ASC LIMIT ?
            ''',
            (session_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_summary(self, session_id: str, account_name: str, channel_id: str, summary: str) -> int:
        conn = self.sqlite_service.connection()
        cursor = conn.execute(
            '''
            INSERT INTO voice_summaries (session_id, account_name, channel_id, summary, created_at)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (session_id, account_name, channel_id, summary, time.time()),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def get_summary(self, session_id: str) -> dict | None:
        row = self.sqlite_service.connection().execute(
            'SELECT * FROM voice_summaries WHERE session_id = ? ORDER BY created_at DESC LIMIT 1',
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_active_sessions(self, account_name: str) -> list[VoiceSession]:
        rows = self.sqlite_service.connection().execute(
            '''
            SELECT id, session_id, account_name, guild_id, channel_id, state, mode,
                   started_at, ended_at, participants_json, health
            FROM voice_sessions WHERE account_name = ? AND state = 'active'
            ORDER BY started_at DESC
            ''',
            (account_name,),
        ).fetchall()
        sessions = []
        for row in rows:
            payload = dict(row)
            if not payload.get('session_id'):
                payload['session_id'] = str(payload.get('id'))
            sessions.append(VoiceSession.from_row(payload))
        return sessions

    def get_active_by_guild_channel(self, guild_id: str, channel_id: str) -> VoiceSession | None:
        row = self.sqlite_service.connection().execute(
            '''
            SELECT id, session_id, account_name, guild_id, channel_id, state, mode,
                   started_at, ended_at, participants_json, health
            FROM voice_sessions
            WHERE guild_id = ? AND channel_id = ? AND state = 'active'
            ORDER BY started_at DESC LIMIT 1
            ''',
            (str(guild_id), str(channel_id)),
        ).fetchone()
        if not row:
            return None
        payload = dict(row)
        if not payload.get('session_id'):
            payload['session_id'] = str(payload.get('id'))
        return VoiceSession.from_row(payload)
