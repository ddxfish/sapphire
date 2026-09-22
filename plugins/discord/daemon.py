"""Daemon entrypoint for the Discord cognitive plugin."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Awaitable, Optional

from plugins.discord.runtime.container import RuntimeContainer
from plugins.discord.runtime import daemon_state

logger = logging.getLogger(__name__)


def _reply_handler(task, event_data: dict, response_text: str):
    runtime = get_runtime()
    if not runtime or not runtime.conversation_service:
        logger.warning('Discord reply handler called but runtime is unavailable')
        return None
    trigger_config = (task or {}).get('trigger_config') or {}
    message_id = str((event_data or {}).get('message_id', ''))
    if runtime.event_bridge:
        # Clear FIRST: a listen-only task's reply is not delivered, but its
        # payload must not sit in the pending map either (M9).
        runtime.event_bridge.clear_pending_payload(message_id)
    if str(trigger_config.get('auto_reply', True)).lower() in {'false', '0'}:
        logger.info('Discord task is listen-only (auto_reply off) — response not delivered')
        # Drop the per-message latches too: this lane never reached
        # handle_llm_response, so _pending (full payload) and the tool-sent /
        # gif-sent sets grew forever on the Help Bot task (row 24).
        try:
            runtime.conversation_service.discard_pending(message_id)
        except Exception:
            logger.debug('discard_pending failed for %s', message_id, exc_info=True)
        return {'status': 'skipped', 'reason': 'auto_reply_disabled'}
    result = runtime.conversation_service.handle_llm_response(task, event_data or {}, response_text)
    if result and result.get('status') == 'sent':
        logger.info('Discord reply delivered for message %s (%s chunks)', message_id, result.get('chunks', 0))
        return result
    if result and result.get('status') == 'error':
        logger.warning('Discord reply delivery issue for message %s: %s', message_id, result)
    return result


def start(plugin_loader, settings):
    with daemon_state.lifecycle_lock:
        handle = daemon_state.handle
        if handle and handle.thread and handle.thread.is_alive():
            return
        handle = daemon_state.RuntimeHandle(
            plugin_name='discord',
            plugin_loader=plugin_loader,
            settings=dict(settings or {}),
        )
        daemon_state.handle = handle
        handle.thread = threading.Thread(
            target=_run_loop,
            args=(handle,),
            daemon=True,
            name='discord-cognitive-daemon',
        )
        handle.thread.start()
        handle.started.wait(timeout=10)
        if handle.failed.is_set():
            daemon_state.handle = None
            raise RuntimeError(f'Discord cognitive plugin failed to start: {handle.startup_error}')
        try:
            plugin_loader.register_reply_handler(handle.plugin_name, _reply_handler)
        except Exception:
            # Without a handler every reply is answered into the void for the
            # life of the process — this was a DEBUG line (row 62).
            logger.error('[DISCORD] Reply handler registration FAILED — replies will not be delivered', exc_info=True)
        logger.info('[DISCORD] Daemon started (health=%s)', get_health_state())


def stop():
    with daemon_state.lifecycle_lock:
        handle = daemon_state.handle
        if not handle:
            return
        if handle.loop and handle.loop.is_running() and handle.container:
            future = asyncio.run_coroutine_threadsafe(handle.container.stop(), handle.loop)
            try:
                future.result(timeout=10)
            except Exception:
                logger.warning('[DISCORD] Container stop timed out/failed; forcing loop shutdown', exc_info=True)
            handle.loop.call_soon_threadsafe(handle.loop.stop)
        if handle.thread and handle.thread.is_alive():
            handle.thread.join(timeout=10)
        daemon_state.handle = None
        # A handler bound to a dead runtime consumed events into "runtime is
        # unavailable" (row 85). Core's unload pops it too; a bare stop didn't.
        try:
            unreg = getattr(handle.plugin_loader, 'unregister_reply_handler', None)
            if callable(unreg):
                unreg(handle.plugin_name)
        except Exception:
            logger.debug('Reply handler unregister failed', exc_info=True)


def on_settings_saved(settings: dict):
    """Core's settings-saved hook for daemon plugins (hunt 2.13.0, row 80).
    The store reads core live on every resolve; only construct-time scalars
    need a nudge."""
    runtime = get_runtime()
    if not runtime:
        return
    try:
        store = getattr(runtime, 'settings_store', None)
        # The one construct-time scalar (row 80): the batch window.
        batching = getattr(runtime, 'batching_service', None)
        if batching is not None and store is not None and hasattr(batching, 'default_window_seconds'):
            batching.default_window_seconds = max(1.0, float(store.resolve().channel.batching_seconds))
    except Exception:
        logger.debug('[DISCORD] settings store reload failed', exc_info=True)


def get_runtime() -> Optional[RuntimeContainer]:
    handle = daemon_state.handle
    return handle.container if handle else None


def get_loop() -> Optional[asyncio.AbstractEventLoop]:
    handle = daemon_state.handle
    return handle.loop if handle else None


def is_daemon_alive() -> bool:
    handle = daemon_state.handle
    return bool(handle and handle.thread and handle.thread.is_alive() and handle.container)


def get_health_state() -> str:
    runtime = get_runtime()
    if not runtime:
        handle = daemon_state.handle
        if handle and handle.thread and handle.thread.is_alive():
            return 'starting'
        return 'stopped'
    return runtime.health.state


def list_connected() -> list[str]:
    runtime = get_runtime()
    if not runtime or not runtime.transport:
        return []
    return runtime.transport.list_connected()


def get_client(account_name: str):
    """Legacy shim: return the live py-cord client for an account.

    Used by external plugins (e.g. Mission Control health digest) that still
    call ``from plugins.discord.daemon import get_client``. Prefer
    ``get_runtime().transport`` for new code.
    """
    runtime = get_runtime()
    if not runtime or not runtime.transport:
        return None
    return runtime.transport.get_client(str(account_name or '').strip())


def run_coroutine(coro: Awaitable):
    loop = get_loop()
    if not loop or not loop.is_running():
        raise RuntimeError('Discord cognitive runtime loop is not running')
    return asyncio.run_coroutine_threadsafe(coro, loop)


def _run_loop(handle: daemon_state.RuntimeHandle):
    loop = asyncio.new_event_loop()
    handle.loop = loop
    asyncio.set_event_loop(loop)

    async def _bootstrap():
        container = RuntimeContainer(
            plugin_name=handle.plugin_name,
            plugin_loader=handle.plugin_loader,
            settings=handle.settings,
            loop=loop,
        )
        handle.container = container
        await container.start()

    try:
        loop.run_until_complete(_bootstrap())
        handle.started.set()
        loop.run_forever()
    except BaseException as exc:
        handle.startup_error = exc
        handle.failed.set()
        handle.started.set()
        logger.error('Discord cognitive daemon crashed: %s', exc, exc_info=True)
    finally:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


_CANONICAL = 'plugins.discord.daemon'
if __name__ != _CANONICAL:
    sys.modules[_CANONICAL] = sys.modules[__name__]
