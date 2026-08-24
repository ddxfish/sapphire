"""Voice subsystem facade coordinating transport, sessions, and modes."""

from __future__ import annotations

import logging

from plugins.discord.models.intentions import JoinVoiceIntention, LeaveVoiceIntention, SpeakVoiceIntention
from plugins.discord.models.voice import VoiceMode

logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(
        self,
        *,
        voice_transport,
        voice_session_service,
        voice_perception_service,
        voice_execution_service,
        voice_listener_service=None,
        settings_store=None,
        channel_repository=None,
        trace_repository=None,
        loop=None,
    ):
        self.voice_transport = voice_transport
        self.voice_session_service = voice_session_service
        self.voice_perception_service = voice_perception_service
        self.voice_execution_service = voice_execution_service
        self.voice_listener_service = voice_listener_service
        self.settings_store = settings_store
        self.channel_repository = channel_repository
        self.trace_repository = trace_repository
        self.loop = loop

    def _reload_settings_from_storage(self) -> None:
        if self.channel_repository:
            self.settings_store = self.channel_repository.load_settings_store()

    def _start_listener(self, session) -> dict | None:
        if not self.voice_listener_service:
            return None
        return self.voice_listener_service.start(session, loop=self.loop)

    async def _start_listener_async(self, session) -> dict | None:
        if not self.voice_listener_service:
            return None
        return await self.voice_listener_service.start_async(session, loop=self.loop)

    def _stop_listener(self, account_name: str, channel_id: str) -> dict | None:
        if not self.voice_listener_service:
            return None
        return self.voice_listener_service.stop(account_name, channel_id)

    async def _stop_listener_async(self, account_name: str, channel_id: str) -> dict | None:
        if not self.voice_listener_service:
            return None
        return await self.voice_listener_service.stop_async(account_name, channel_id)

    def _resolve_listener_session(self, account_name: str, channel_id: str, *, guild_id: str = ''):
        self._reload_settings_from_storage()
        settings = self.settings_store.resolve(
            guild_id=guild_id,
            channel_id=channel_id,
        ) if self.settings_store else None
        if settings and (not settings.voice.enabled or settings.voice.emergency_disabled):
            return None, {'status': 'skipped', 'reason': 'voice_disabled'}
        mode = settings.voice.mode if settings else VoiceMode.LISTEN_ONLY.value
        logger.debug(
            'Voice session resolve %s:%s guild=%s mode=%s speaking=%s',
            account_name,
            channel_id,
            guild_id,
            mode,
            getattr(settings.voice, 'speaking_enabled', False) if settings else False,
        )
        session = self.voice_session_service.get_active_session(account_name, channel_id)
        if not session:
            session = self.voice_session_service.start_session(
                account_name,
                guild_id,
                channel_id,
                mode=mode,
            )
        elif settings:
            session = self.voice_session_service.start_session(
                account_name,
                guild_id or session.guild_id,
                channel_id,
                mode=mode,
            )
        return session, None

    def ensure_listener(self, account_name: str, channel_id: str, *, guild_id: str = '') -> dict:
        """Ensure an active session and voice listener for a connected channel."""
        session, blocked = self._resolve_listener_session(account_name, channel_id, guild_id=guild_id)
        if blocked:
            return blocked
        listener = self._start_listener(session)
        return listener or {'status': 'no_listener'}

    async def ensure_listener_async(self, account_name: str, channel_id: str, *, guild_id: str = '') -> dict:
        """Async ensure for daemon event loop contexts."""
        session, blocked = self._resolve_listener_session(account_name, channel_id, guild_id=guild_id)
        if blocked:
            return blocked
        listener = await self._start_listener_async(session)
        return listener or {'status': 'no_listener'}

    def join(self, intention: JoinVoiceIntention) -> dict:
        self._reload_settings_from_storage()
        settings = self.settings_store.resolve(
            guild_id=intention.guild_id,
            channel_id=intention.channel_id,
        ) if self.settings_store else None
        if settings and (not settings.voice.enabled or settings.voice.emergency_disabled):
            return {'status': 'blocked', 'reason': 'voice_disabled'}
        transport_result = self.voice_transport.connect_sync(
            intention.account_name,
            intention.guild_id,
            intention.channel_id,
        )
        if transport_result.get('status') == 'error':
            return {
                'status': 'error',
                'reason': transport_result.get('error', 'voice_connect_failed'),
                'transport': transport_result,
            }
        mode = intention.mode or (settings.voice.mode if settings else VoiceMode.LISTEN_ONLY.value)
        session = self.voice_session_service.start_session(
            intention.account_name,
            intention.guild_id,
            intention.channel_id,
            mode=mode,
        )
        listener = self._start_listener(session)
        payload = {'status': 'joined', 'transport': transport_result, 'session': session.to_dict()}
        if listener:
            payload['listener'] = listener
        return payload

    async def join_async(self, intention: JoinVoiceIntention) -> dict:
        self._reload_settings_from_storage()
        settings = self.settings_store.resolve(
            guild_id=intention.guild_id,
            channel_id=intention.channel_id,
        ) if self.settings_store else None
        if settings and (not settings.voice.enabled or settings.voice.emergency_disabled):
            return {'status': 'blocked', 'reason': 'voice_disabled'}
        transport_result = await self.voice_transport.connect_async(
            intention.account_name,
            intention.guild_id,
            intention.channel_id,
        )
        if transport_result.get('status') == 'error':
            return {
                'status': 'error',
                'reason': transport_result.get('error', 'voice_connect_failed'),
                'transport': transport_result,
            }
        mode = intention.mode or (settings.voice.mode if settings else VoiceMode.LISTEN_ONLY.value)
        session = self.voice_session_service.start_session(
            intention.account_name,
            intention.guild_id,
            intention.channel_id,
            mode=mode,
        )
        listener = await self._start_listener_async(session)
        payload = {'status': 'joined', 'transport': transport_result, 'session': session.to_dict()}
        if listener:
            payload['listener'] = listener
        return payload

    def leave(self, intention: LeaveVoiceIntention) -> dict:
        session = None
        if intention.session_id:
            session = self.voice_session_service.close_session(intention.session_id)
        elif intention.channel_id:
            active = self.voice_session_service.get_active_session(intention.account_name, intention.channel_id)
            if active:
                session = self.voice_session_service.close_session(active.session_id)
        transport_result = self.voice_transport.disconnect_sync(intention.account_name, intention.channel_id)
        self._stop_listener(intention.account_name, intention.channel_id)
        summary = {}
        if session:
            summary = self.voice_session_service.summarize_session(session.session_id)
        return {'status': 'left', 'transport': transport_result, 'summary': summary}

    async def leave_async(self, intention: LeaveVoiceIntention) -> dict:
        session = None
        if intention.session_id:
            session = self.voice_session_service.close_session(intention.session_id)
        elif intention.channel_id:
            active = self.voice_session_service.get_active_session(intention.account_name, intention.channel_id)
            if active:
                session = self.voice_session_service.close_session(active.session_id)
        transport_result = await self.voice_transport.disconnect_async(intention.account_name, intention.channel_id)
        await self._stop_listener_async(intention.account_name, intention.channel_id)
        summary = {}
        if session:
            summary = self.voice_session_service.summarize_session(session.session_id)
        return {'status': 'left', 'transport': transport_result, 'summary': summary}

    def speak(self, intention: SpeakVoiceIntention) -> dict:
        return self.voice_execution_service.execute(intention)

    def process_audio(self, session_id: str, audio_bytes: bytes, **kwargs) -> dict:
        settings = self.settings_store.resolve() if self.settings_store else None
        if settings and not settings.voice.transcription_enabled and not settings.voice.enabled:
            return {'status': 'blocked', 'reason': 'transcription_disabled'}
        return self.voice_perception_service.process_audio(session_id, audio_bytes=audio_bytes, **kwargs)
