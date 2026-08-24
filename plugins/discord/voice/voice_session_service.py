"""Voice session lifecycle management."""

from __future__ import annotations

from plugins.discord.models.voice import VoiceMode, VoiceSession


class VoiceSessionService:
    def __init__(self, *, voice_session_repository, world_model_service=None, trace_repository=None):
        self.voice_session_repository = voice_session_repository
        self.world_model_service = world_model_service
        self.trace_repository = trace_repository
        self._reconnect_counts: dict[str, int] = {}

    def start_session(
        self,
        account_name: str,
        guild_id: str,
        channel_id: str,
        *,
        mode: VoiceMode | str = VoiceMode.LISTEN_ONLY,
    ) -> VoiceSession:
        existing = self.voice_session_repository.get_active_session(account_name, channel_id)
        mode_value = mode.value if isinstance(mode, VoiceMode) else str(mode)
        if existing:
            current = existing.mode.value if isinstance(existing.mode, VoiceMode) else str(existing.mode)
            if current != mode_value:
                updated = self.voice_session_repository.update_session(existing.session_id, mode=mode_value)
                if updated:
                    return updated
            return existing
        session = self.voice_session_repository.create_session(
            account_name, guild_id, channel_id, mode=mode_value,
        )
        if self.trace_repository:
            self.trace_repository.record_trace('voice_session_started', 'Voice session created', session.to_dict())
        return session

    def get_active_session(self, account_name: str, channel_id: str) -> VoiceSession | None:
        return self.voice_session_repository.get_active_session(account_name, channel_id)

    def get_active_by_guild_channel(self, guild_id: str, channel_id: str) -> VoiceSession | None:
        return self.voice_session_repository.get_active_by_guild_channel(guild_id, channel_id)

    def set_health(self, session_id: str, health: str) -> VoiceSession | None:
        session = self.voice_session_repository.update_session(session_id, health=health)
        if session and self.trace_repository:
            self.trace_repository.record_trace('voice_session_health', 'Voice session health updated', {
                'session_id': session_id,
                'health': health,
            })
        return session

    def note_reconnect(self, session_id: str, *, threshold: int = 3) -> VoiceSession | None:
        count = int(self._reconnect_counts.get(session_id, 0)) + 1
        self._reconnect_counts[session_id] = count
        health = 'degraded' if count >= threshold else 'conversational'
        if self.trace_repository:
            self.trace_repository.record_trace('voice_reconnect', 'Voice session reconnect noted', {
                'session_id': session_id,
                'reconnect_count': count,
                'health': health,
            })
        return self.set_health(session_id, health)

    def update_participants(self, session_id: str, participants: list[str]) -> VoiceSession | None:
        return self.voice_session_repository.update_session(session_id, participants=participants)

    def close_session(self, session_id: str) -> VoiceSession | None:
        session = self.voice_session_repository.close_session(session_id)
        if session and self.trace_repository:
            self.trace_repository.record_trace('voice_session_closed', 'Voice session closed', session.to_dict())
        return session

    def list_active(self, account_name: str) -> list[VoiceSession]:
        return self.voice_session_repository.list_active_sessions(account_name)

    def summarize_session(self, session_id: str, *, llm_bridge=None) -> dict:
        session = self.voice_session_repository.get_session(session_id)
        if not session:
            return {'status': 'missing'}
        transcripts = self.voice_session_repository.list_transcripts(session_id, limit=200)
        if not transcripts:
            return {'status': 'empty'}
        lines = [f"{row.get('speaker_name') or row.get('speaker_id') or 'speaker'}: {row['text']}" for row in transcripts]
        transcript_text = '\n'.join(lines)
        summary = transcript_text[:500]
        if llm_bridge and hasattr(llm_bridge, 'summarize_text'):
            try:
                summary = llm_bridge.summarize_text(transcript_text)
            except Exception:
                pass
        summary_id = self.voice_session_repository.save_summary(
            session_id, session.account_name, session.channel_id, summary,
        )
        if self.world_model_service:
            self.world_model_service.create_task(
                session.account_name,
                'voice_follow_up',
                target_id=session.channel_id,
                reason='voice_session_summary',
            )
        return {'status': 'summarized', 'summary_id': summary_id, 'summary': summary}
