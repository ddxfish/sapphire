"""Discord voice channel connect/disconnect transport."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class VoiceTransport:
    def __init__(self, *, discord_transport=None):
        self.discord_transport = discord_transport
        self._connections: dict[tuple[str, str], dict] = {}

    def connect_sync(self, account_name: str, guild_id: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        if self.discord_transport:
            result = self.discord_transport.connect_voice_sync(account_name, str(channel_id))
            if result.get('status') == 'error':
                return result
            guild_id = str(result.get('guild_id') or guild_id or '')
            channel_id = str(result.get('channel_id') or channel_id)
            state = {
                'account_name': account_name,
                'guild_id': guild_id,
                'channel_id': channel_id,
                'state': 'connected',
                'health': 'ok',
                'reconnect_count': 0,
            }
            self._connections[key] = state
            return dict(state)
        state = {
            'account_name': account_name,
            'guild_id': guild_id,
            'channel_id': channel_id,
            'state': 'connected',
            'health': 'ok',
            'reconnect_count': 0,
        }
        self._connections[key] = state
        return dict(state)

    async def connect_async(self, account_name: str, guild_id: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        if self.discord_transport:
            result = await self.discord_transport.connect_voice_async(account_name, str(channel_id))
            if result.get('status') == 'error':
                return result
            guild_id = str(result.get('guild_id') or guild_id or '')
            channel_id = str(result.get('channel_id') or channel_id)
            state = {
                'account_name': account_name,
                'guild_id': guild_id,
                'channel_id': channel_id,
                'state': 'connected',
                'health': 'ok',
                'reconnect_count': 0,
            }
            self._connections[key] = state
            return dict(state)
        return self.connect_sync(account_name, guild_id, channel_id)

    async def disconnect_async(self, account_name: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        if self.discord_transport:
            result = await self.discord_transport.disconnect_voice_async(account_name, str(channel_id))
            self._connections.pop(key, None)
            if result.get('status') == 'not_connected':
                return {'status': 'not_connected', 'account_name': account_name, 'channel_id': channel_id}
            if result.get('status') == 'error':
                return result
            return {'status': 'disconnected', **result}
        return self.disconnect_sync(account_name, channel_id)

    def disconnect_sync(self, account_name: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        if self.discord_transport:
            result = self.discord_transport.disconnect_voice_sync(account_name, str(channel_id))
            self._connections.pop(key, None)
            if result.get('status') == 'not_connected':
                return {'status': 'not_connected', 'account_name': account_name, 'channel_id': channel_id}
            if result.get('status') == 'error':
                return result
            return {'status': 'disconnected', **result}
        state = self._connections.pop(key, None)
        if not state:
            return {'status': 'not_connected', 'account_name': account_name, 'channel_id': channel_id}
        state['state'] = 'disconnected'
        return {'status': 'disconnected', **state}

    def connection_health(self, account_name: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        if self.discord_transport:
            try:
                state = self.discord_transport.get_voice_channel_state_sync(account_name, str(channel_id))
                if state.get('bot_connected'):
                    return {'state': 'connected', 'health': 'ok'}
            except Exception:
                logger.debug('Voice health check failed for %s:%s', account_name, channel_id, exc_info=True)
        state = self._connections.get(key)
        if not state:
            return {'state': 'disconnected', 'health': 'unknown'}
        return {'state': state.get('state', 'connected'), 'health': state.get('health', 'ok')}

    def play_audio_sync(self, account_name: str, channel_id: str, audio_bytes: bytes, **kwargs) -> dict:
        key = (account_name, str(channel_id))
        if key not in self._connections and self.discord_transport:
            state = self.discord_transport.get_voice_channel_state_sync(account_name, str(channel_id))
            if not state.get('bot_connected'):
                return {'status': 'not_connected'}
        elif key not in self._connections:
            return {'status': 'not_connected'}
        if self.discord_transport:
            audio_format = str(kwargs.get('format') or kwargs.get('audio_format') or 'wav')
            result = self.discord_transport.play_voice_audio_sync(
                account_name,
                str(channel_id),
                audio_bytes,
                audio_format=audio_format,
            )
            return result
        return {
            'status': 'played',
            'account_name': account_name,
            'channel_id': channel_id,
            'bytes': len(audio_bytes or b''),
            **kwargs,
        }

    async def start_listening_async(self, account_name: str, channel_id: str, *, on_utterance, loop=None, **kwargs) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return await self.discord_transport.start_voice_listener_async(
            account_name,
            str(channel_id),
            on_utterance=on_utterance,
            loop=loop,
            **kwargs,
        )

    async def stop_listening_async(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return await self.discord_transport.stop_voice_listener_async(account_name, str(channel_id))

    async def play_audio_async(self, account_name: str, channel_id: str, audio_bytes: bytes, **kwargs) -> dict:
        key = (account_name, str(channel_id))
        if key not in self._connections and self.discord_transport:
            state = await self.discord_transport.get_voice_channel_state_async(account_name, str(channel_id))
            if not state.get('bot_connected'):
                return {'status': 'not_connected'}
        elif key not in self._connections:
            return {'status': 'not_connected'}
        if self.discord_transport:
            audio_format = str(kwargs.get('format') or kwargs.get('audio_format') or 'wav')
            return await self.discord_transport.play_voice_audio_async(
                account_name,
                str(channel_id),
                audio_bytes,
                audio_format=audio_format,
            )
        return {
            'status': 'played',
            'account_name': account_name,
            'channel_id': channel_id,
            'bytes': len(audio_bytes or b''),
            **kwargs,
        }

    async def stop_playback_async(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return await self.discord_transport.stop_voice_playback_async(account_name, str(channel_id))

    def start_listening_sync(self, account_name: str, channel_id: str, *, on_utterance, loop=None, **kwargs) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.start_voice_listener_sync(
            account_name,
            str(channel_id),
            on_utterance=on_utterance,
            loop=loop,
            **kwargs,
        )

    def stop_listening_sync(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.stop_voice_listener_sync(account_name, str(channel_id))

    def stop_playback_sync(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.stop_voice_playback_sync(account_name, str(channel_id))

    def start_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.start_streaming_playback_sync(account_name, str(channel_id))

    async def start_streaming_playback_async(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return await self.discord_transport.start_streaming_playback_async(account_name, str(channel_id))

    def feed_streaming_chunk_sync(self, account_name: str, channel_id: str, chunk: dict) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.feed_streaming_chunk_sync(account_name, str(channel_id), chunk)

    def finish_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.finish_streaming_playback_sync(account_name, str(channel_id))

    def stop_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.stop_streaming_playback_sync(account_name, str(channel_id))

    def wait_streaming_playback_sync(
        self,
        account_name: str,
        channel_id: str,
        *,
        timeout: float = 180.0,
    ) -> dict:
        if not self.discord_transport:
            return {'status': 'unavailable'}
        return self.discord_transport.wait_streaming_playback_sync(
            account_name,
            str(channel_id),
            timeout=timeout,
        )

    def list_connections(self, account_name: str | None = None) -> list[dict]:
        items = []
        for (acct, channel_id), state in sorted(self._connections.items()):
            if account_name and acct != account_name:
                continue
            items.append({'account_name': acct, 'channel_id': channel_id, **state})
        return items
