"""Live message ingestion: observations → batching → conversation processing."""

from __future__ import annotations

import asyncio
import logging
import time

from plugins.discord import hooks_out
from plugins.discord.models.observations import TextMessageObservation, TypingObservation

logger = logging.getLogger(__name__)


class MessagePipelineService:
    def __init__(self, *, batching_service, conversation_service, flush_interval_seconds: float = 1.0):
        self.batching_service = batching_service
        self.conversation_service = conversation_service
        self.flush_interval_seconds = max(0.25, float(flush_interval_seconds))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._flush_loop(), name='discord-message-pipeline')

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.flush_due(time.time())

    def handle_message(self, observation: TextMessageObservation) -> None:
        self.batching_service.add_message(observation)
        # S0 door: add-ons see every inbound message (worker thread — this
        # runs on the gateway loop).
        hooks_out.fire_threaded('discord_message_observed', hooks_out.observed_payload(observation))

    def handle_typing(self, observation: TypingObservation) -> None:
        self.batching_service.record_typing(observation)

    def flush_due(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        results = []
        for batch in self.batching_service.flush_ready(now=now):
            try:
                accepted = self.conversation_service.process_batch(batch)
            except Exception:
                logger.exception('Failed to process batch for %s/%s', batch.account_name, batch.channel_id)
                continue
            results.append({'account_name': batch.account_name, 'channel_id': batch.channel_id,
                            'message_ids': batch.message_ids, 'accepted': accepted})
        return results

    async def _flush_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self.flush_due()
                await asyncio.sleep(self.flush_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Message pipeline flush loop failed')
