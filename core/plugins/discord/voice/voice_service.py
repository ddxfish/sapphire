"""Voice subsystem facade: join / leave / listen, gated by the voice task (S6)."""

from __future__ import annotations

import logging

from plugins.discord.models.intentions import JoinVoiceIntention, LeaveVoiceIntention

logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(self, *, voice_transport, sessions, gate=None, voice_listener_service=None, loop=None, on_leave=None):
        self.voice_transport = voice_transport
        self.sessions = sessions
        self.gate = gate
        # on_leave(account, channel_id, reason): every leave, whatever asked for it — the auto-join latch.
        self.on_leave = on_leave
        self.voice_listener_service = voice_listener_service
        self.loop = loop

    def _blocked(self, account_name: str, channel_id: str) -> dict | None:
        if self.gate is not None and not self.gate.allowed(account_name, channel_id):
            return {'status': 'blocked', 'reason': 'no_voice_task'}
        return None

    async def ensure_listener_async(self, account_name: str, channel_id: str, *, guild_id: str = '') -> dict:
        if (blocked := self._blocked(account_name, channel_id)):
            return blocked
        session = self.sessions.start(account_name, guild_id, channel_id)
        if not self.voice_listener_service:
            return {'status': 'no_listener'}
        return await self.voice_listener_service.start_async(session, loop=self.loop)

    def join(self, intention: JoinVoiceIntention) -> dict:
        if (blocked := self._blocked(intention.account_name, intention.channel_id)):
            return blocked
        transport_result = self.voice_transport.connect_sync(intention.account_name, intention.guild_id, intention.channel_id)
        if transport_result.get('status') == 'error':
            return {'status': 'error', 'reason': transport_result.get('error', 'voice_connect_failed'), 'transport': transport_result}
        session = self.sessions.start(intention.account_name, intention.guild_id, intention.channel_id)
        payload = {'status': 'joined', 'transport': transport_result, 'session': session.to_dict()}
        if self.voice_listener_service:
            payload['listener'] = self.voice_listener_service.start(session, loop=self.loop)
        return payload

    async def join_async(self, intention: JoinVoiceIntention) -> dict:
        if (blocked := self._blocked(intention.account_name, intention.channel_id)):
            return blocked
        transport_result = await self.voice_transport.connect_async(intention.account_name, intention.guild_id, intention.channel_id)
        if transport_result.get('status') == 'error':
            return {'status': 'error', 'reason': transport_result.get('error', 'voice_connect_failed'), 'transport': transport_result}
        session = self.sessions.start(intention.account_name, intention.guild_id, intention.channel_id)
        payload = {'status': 'joined', 'transport': transport_result, 'session': session.to_dict()}
        if self.voice_listener_service:
            payload['listener'] = await self.voice_listener_service.start_async(session, loop=self.loop)
        return payload

    def leave(self, intention: LeaveVoiceIntention) -> dict:
        session = self.sessions.get(intention.account_name, intention.channel_id)
        if session:
            self.sessions.close(session.session_id)
        # Listener first, then the socket: stopping after the disconnect raised
        # on "not connected" before the sink could clean up (row 37).
        if self.voice_listener_service:
            self.voice_listener_service.stop(intention.account_name, intention.channel_id)
        transport_result = self.voice_transport.disconnect_sync(intention.account_name, intention.channel_id)
        self._note_leave(intention)
        return {'status': 'left', 'transport': transport_result}

    async def leave_async(self, intention: LeaveVoiceIntention) -> dict:
        session = self.sessions.get(intention.account_name, intention.channel_id)
        if session:
            self.sessions.close(session.session_id)
        transport_result = await self.voice_transport.disconnect_async(intention.account_name, intention.channel_id)
        if self.voice_listener_service:
            await self.voice_listener_service.stop_async(intention.account_name, intention.channel_id)
        self._note_leave(intention)
        return {'status': 'left', 'transport': transport_result}

    def _note_leave(self, intention: LeaveVoiceIntention) -> None:
        if not callable(self.on_leave):
            return
        try:
            self.on_leave(intention.account_name, str(intention.channel_id), str(intention.reason or ''))
        except Exception:
            logger.debug('on_leave hook failed', exc_info=True)
