"""Live message ingestion: observations → batching → conversation processing."""

from __future__ import annotations

import asyncio
import logging
import time

from plugins.discord.models.observations import TextMessageObservation, TypingObservation

logger = logging.getLogger(__name__)


class MessagePipelineService:
    def __init__(
        self,
        *,
        batching_service,
        conversation_service,
        trace_repository=None,
        flush_interval_seconds: float = 1.0,
    ):
        self.batching_service = batching_service
        self.conversation_service = conversation_service
        self.trace_repository = trace_repository
        self.flush_interval_seconds = max(0.25, float(flush_interval_seconds))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._flush_loop(), name='discord-cognitive-message-pipeline')

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
        batch = self.batching_service.add_message(observation)
        if self.trace_repository:
            self.trace_repository.record_trace('batch_queued', 'Message added to channel batch', {
                'account_name': observation.account_name,
                'channel_id': observation.channel_id,
                'message_id': observation.message_id,
                'batch_size': batch.message_count,
                'flush_at': batch.flush_at,
            })

    def handle_typing(self, observation: TypingObservation) -> None:
        self.batching_service.record_typing(observation)
        if self.trace_repository:
            self.trace_repository.record_trace('batch_typing', 'Typing extended channel batch window', {
                'account_name': observation.account_name,
                'channel_id': observation.channel_id,
            })

    def flush_due(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        results = []
        for batch in self.batching_service.flush_ready(now=now):
            if self.trace_repository:
                self.trace_repository.record_trace('batch_flushed', 'Processing ready message batch', {
                    'account_name': batch.account_name,
                    'channel_id': batch.channel_id,
                    'message_count': batch.message_count,
                    'message_ids': batch.message_ids,
                })
            try:
                accepted = self.conversation_service.process_batch(batch)
                results.append({
                    'account_name': batch.account_name,
                    'channel_id': batch.channel_id,
                    'message_ids': batch.message_ids,
                    'accepted': accepted,
                })
            except Exception:
                logger.exception(
                    'Failed to process batch for %s/%s',
                    batch.account_name,
                    batch.channel_id,
                )
                if self.trace_repository:
                    self.trace_repository.record_trace('batch_failed', 'Batch processing failed', {
                        'account_name': batch.account_name,
                        'channel_id': batch.channel_id,
                        'message_ids': batch.message_ids,
                    })
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
