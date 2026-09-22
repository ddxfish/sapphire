"""Live voice sessions, in memory (S6, 2026-09-22).

The voice_sessions / voice_transcripts / voice_summaries tables are gone: the
transport is the truth of where she is, and a session is a handle the listener,
the conversation runner and the event bridge share while she is in a channel.
"""
from __future__ import annotations

import threading
import time
import uuid

from plugins.discord.models.voice import VoiceSession


class VoiceSessions:
    def __init__(self, *, trace_repository=None):
        self.trace_repository = trace_repository
        self._by_key: dict[tuple[str, str], VoiceSession] = {}
        self._lock = threading.Lock()

    def start(self, account_name: str, guild_id: str, channel_id: str) -> VoiceSession:
        """The session for (account, channel) — created on first call, reused after."""
        key = (str(account_name), str(channel_id))
        with self._lock:
            session = self._by_key.get(key)
            if session is None:
                session = VoiceSession(session_id=uuid.uuid4().hex[:12], account_name=str(account_name),
                                       guild_id=str(guild_id or ''), channel_id=str(channel_id), started_at=time.time())
                self._by_key[key] = session
                created = True
            else:
                if guild_id and not session.guild_id:
                    session.guild_id = str(guild_id)
                created = False
        if created and self.trace_repository:
            self.trace_repository.record_trace('voice_session_started', 'Voice session created', session.to_dict())
        return session

    def get(self, account_name: str, channel_id: str) -> VoiceSession | None:
        return self._by_key.get((str(account_name), str(channel_id)))

    def get_by_id(self, session_id: str) -> VoiceSession | None:
        with self._lock:
            return next((s for s in self._by_key.values() if s.session_id == session_id), None)

    def get_by_guild_channel(self, guild_id: str, channel_id: str) -> VoiceSession | None:
        with self._lock:
            return next((s for s in self._by_key.values()
                         if s.channel_id == str(channel_id) and (not guild_id or s.guild_id == str(guild_id))), None)

    def set_health(self, session_id: str, health: str) -> VoiceSession | None:
        session = self.get_by_id(session_id)
        if session:
            session.health = str(health)
        return session

    def close(self, session_id: str) -> VoiceSession | None:
        with self._lock:
            key = next((k for k, s in self._by_key.items() if s.session_id == session_id), None)
            session = self._by_key.pop(key, None) if key else None
        if session and self.trace_repository:
            self.trace_repository.record_trace('voice_session_closed', 'Voice session closed', session.to_dict())
        return session

    def list_active(self, account_name: str | None = None) -> list[VoiceSession]:
        with self._lock:
            return [s for s in self._by_key.values() if account_name is None or s.account_name == str(account_name)]
