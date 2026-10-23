"""Graceful asyncio event loop teardown for background daemon threads."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_DRAIN_INTERVAL_S = 0.05
_DRAIN_ATTEMPTS = 6


async def reap_subprocess(
    proc: asyncio.subprocess.Process | None,
    *,
    grace: float = 3.0,
) -> None:
    """Terminate/kill a subprocess and wait for exit on the owning loop."""
    if proc is None or proc.returncode is not None:
        return

    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
        except Exception:
            pass

    transport = getattr(proc, "_transport", None)
    if transport is not None and not transport.is_closing():
        try:
            transport.close()
        except Exception:
            pass


async def _prepare_loop_shutdown(loop: asyncio.AbstractEventLoop) -> None:
    await loop.shutdown_asyncgens()
    if hasattr(loop, "shutdown_default_executor"):
        await loop.shutdown_default_executor()

    # ThreadedChildWatcher delivers process-exit callbacks via call_soon_threadsafe.
    # Pump the loop briefly so those callbacks run before loop.close().
    for _ in range(_DRAIN_ATTEMPTS):
        await asyncio.sleep(_DRAIN_INTERVAL_S)


def close_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Shutdown asyncgens, drain child-watcher callbacks, and close the loop.

    Callers should exit their main coroutine and reap subprocesses before this.
    Prevents 'Event loop is closed' noise from BaseSubprocessTransport.__del__
    during process exit when subprocess pipes outlive their loop.
    """
    if loop is None or loop.is_closed():
        return
    try:
        loop.run_until_complete(_prepare_loop_shutdown(loop))
    except Exception as exc:
        logger.debug("Event loop shutdown: %s", exc)
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception as exc:
            logger.debug("Event loop close: %s", exc)
