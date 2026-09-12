"""Periodic scheduler loop for time-driven runtime work."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SchedulerLoop:
    def __init__(self, interval_seconds: float = 15.0, tick_handler=None):
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.tick_handler = tick_handler
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def set_tick_handler(self, handler) -> None:
        self.tick_handler = handler

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name='discord-cognitive-scheduler')

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self.tick_handler:
                    try:
                        await self._invoke_tick()
                    except Exception:
                        logger.exception('Scheduler tick failed')
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Scheduler loop failed')

    async def _invoke_tick(self) -> None:
        result = self.tick_handler()
        if asyncio.iscoroutine(result):
            await result
