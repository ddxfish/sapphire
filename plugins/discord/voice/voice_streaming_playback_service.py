"""Facade for Discord streaming voice playback (Phase 1 / Phase 2 sink)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class VoiceStreamingPlaybackService:
    def __init__(self, *, voice_transport=None):
        self.voice_transport = voice_transport

    def start(self, account_name: str, channel_id: str) -> dict:
        if not self.voice_transport:
            return {'status': 'unavailable'}
        return self.voice_transport.start_streaming_playback_sync(account_name, str(channel_id))

    async def start_async(self, account_name: str, channel_id: str) -> dict:
        if not self.voice_transport:
            return {'status': 'unavailable'}
        return await self.voice_transport.start_streaming_playback_async(account_name, str(channel_id))

    def feed_chunk(self, account_name: str, channel_id: str, chunk: dict) -> dict:
        if not self.voice_transport:
            return {'status': 'unavailable'}
        return self.voice_transport.feed_streaming_chunk_sync(account_name, str(channel_id), chunk)

    def finish(self, account_name: str, channel_id: str) -> dict:
        if not self.voice_transport:
            return {'status': 'unavailable'}
        return self.voice_transport.finish_streaming_playback_sync(account_name, str(channel_id))

    def stop(self, account_name: str, channel_id: str) -> dict:
        if not self.voice_transport:
            return {'status': 'unavailable'}
        return self.voice_transport.stop_streaming_playback_sync(account_name, str(channel_id))

    def wait(self, account_name: str, channel_id: str, *, timeout: float = 180.0) -> dict:
        if not self.voice_transport:
            return {'status': 'unavailable'}
        return self.voice_transport.wait_streaming_playback_sync(
            account_name,
            str(channel_id),
            timeout=timeout,
        )
