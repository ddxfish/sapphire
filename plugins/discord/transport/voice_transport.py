"""Voice-channel facade over the Discord transport: connect / disconnect / listen /
play / streaming playback, plus the plugin's own list of where each bot is."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class VoiceTransport:
    def __init__(self, *, discord_transport):
        self.discord_transport = discord_transport
        self._connections: dict[tuple[str, str], dict] = {}

    def _remember(self, account_name: str, guild_id: str, channel_id: str, result: dict) -> dict:
        state = {
            'account_name': account_name,
            'guild_id': str(result.get('guild_id') or guild_id or ''),
            'channel_id': str(result.get('channel_id') or channel_id),
            'state': 'connected',
            'health': 'ok',
            'reconnect_count': 0,
        }
        self._connections[(account_name, str(channel_id))] = state
        return dict(state)

    def _forget(self, account_name: str, channel_id: str, result: dict) -> dict:
        self._connections.pop((account_name, str(channel_id)), None)
        if result.get('status') == 'not_connected':
            return {'status': 'not_connected', 'account_name': account_name, 'channel_id': channel_id}
        if result.get('status') == 'error':
            return result
        return {'status': 'disconnected', **result}

    def connect_sync(self, account_name: str, guild_id: str, channel_id: str) -> dict:
        result = self.discord_transport.connect_voice_sync(account_name, str(channel_id))
        if result.get('status') == 'error':
            return result
        return self._remember(account_name, guild_id, channel_id, result)

    async def connect_async(self, account_name: str, guild_id: str, channel_id: str) -> dict:
        result = await self.discord_transport.connect_voice_async(account_name, str(channel_id))
        if result.get('status') == 'error':
            return result
        return self._remember(account_name, guild_id, channel_id, result)

    def disconnect_sync(self, account_name: str, channel_id: str) -> dict:
        return self._forget(account_name, channel_id,
                            self.discord_transport.disconnect_voice_sync(account_name, str(channel_id)))

    async def disconnect_async(self, account_name: str, channel_id: str) -> dict:
        return self._forget(account_name, channel_id,
                            await self.discord_transport.disconnect_voice_async(account_name, str(channel_id)))

    def play_audio_sync(self, account_name: str, channel_id: str, audio_bytes: bytes, **kwargs) -> dict:
        if (account_name, str(channel_id)) not in self._connections:
            state = self.discord_transport.get_voice_channel_state_sync(account_name, str(channel_id))
            if not state.get('bot_connected'):
                return {'status': 'not_connected'}
        audio_format = str(kwargs.get('format') or kwargs.get('audio_format') or 'wav')
        return self.discord_transport.play_voice_audio_sync(account_name, str(channel_id), audio_bytes,
                                                            audio_format=audio_format)

    async def start_listening_async(self, account_name: str, channel_id: str, *, on_utterance, loop=None, **kwargs) -> dict:
        return await self.discord_transport.start_voice_listener_async(
            account_name, str(channel_id), on_utterance=on_utterance, loop=loop, **kwargs)

    async def stop_listening_async(self, account_name: str, channel_id: str) -> dict:
        return await self.discord_transport.stop_voice_listener_async(account_name, str(channel_id))

    def start_listening_sync(self, account_name: str, channel_id: str, *, on_utterance, loop=None, **kwargs) -> dict:
        return self.discord_transport.start_voice_listener_sync(
            account_name, str(channel_id), on_utterance=on_utterance, loop=loop, **kwargs)

    def stop_listening_sync(self, account_name: str, channel_id: str) -> dict:
        return self.discord_transport.stop_voice_listener_sync(account_name, str(channel_id))

    # ── streaming playback (the conversation source's speaker) ──

    def start_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        return self.discord_transport.start_streaming_playback_sync(account_name, str(channel_id))

    async def start_streaming_playback_async(self, account_name: str, channel_id: str) -> dict:
        return await self.discord_transport.start_streaming_playback_async(account_name, str(channel_id))

    def feed_streaming_chunk_sync(self, account_name: str, channel_id: str, chunk: dict) -> dict:
        return self.discord_transport.feed_streaming_chunk_sync(account_name, str(channel_id), chunk)

    def finish_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        return self.discord_transport.finish_streaming_playback_sync(account_name, str(channel_id))

    def stop_streaming_playback_sync(self, account_name: str, channel_id: str) -> dict:
        return self.discord_transport.stop_streaming_playback_sync(account_name, str(channel_id))

    def wait_streaming_playback_sync(self, account_name: str, channel_id: str, *, timeout: float = 180.0) -> dict:
        return self.discord_transport.wait_streaming_playback_sync(account_name, str(channel_id), timeout=timeout)

    def list_connections(self, account_name: str | None = None) -> list[dict]:
        return [{'account_name': acct, 'channel_id': channel_id, **state}
                for (acct, channel_id), state in sorted(self._connections.items())
                if not account_name or acct == account_name]
