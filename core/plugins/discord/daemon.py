"""Daemon entrypoint. Core calls start(plugin_loader, settings) / stop() /
on_settings_saved(settings); everything else reaches the runtime through get_runtime().

Core loads this file under its own module name and routes/tools import
plugins.discord.daemon — two module objects. The handle therefore lives in
runtime/daemon_state.py, which both import normally."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Awaitable, Optional

from plugins.discord.runtime import daemon_state
from plugins.discord.runtime.container import RuntimeContainer

logger = logging.getLogger(__name__)


def _reply_handler(task, event_data: dict, response_text: str):
    runtime = get_runtime()
    if not runtime:
        logger.warning('Discord reply handler called but runtime is unavailable')
        return None
    trigger_config = (task or {}).get('trigger_config') or {}
    message_id = str((event_data or {}).get('message_id', ''))
    # Clear FIRST: a listen-only task's reply is not delivered, but its
    # payload must not sit in the pending map either (M9).
    runtime.event_bridge.clear_pending_payload(message_id)
    if str(trigger_config.get('auto_reply', True)).lower() in {'false', '0'}:
        logger.info('Discord task is listen-only (auto_reply off) — response not delivered')
        runtime.conversation_service.discard_pending(message_id)
        return {'status': 'skipped', 'reason': 'auto_reply_disabled'}
    result = runtime.conversation_service.handle_llm_response(task, event_data or {}, response_text)
    if result and result.get('status') == 'sent':
        logger.info('Discord reply delivered for message %s (%s chunks)', message_id, result.get('chunks', 0))
    elif result and result.get('status') == 'error':
        logger.warning('Discord reply delivery issue for message %s: %s', message_id, result)
    return result


def start(plugin_loader, settings):
    with daemon_state.lifecycle_lock:
        current = daemon_state.handle
        if current and current.thread and current.thread.is_alive():
            return
        new = daemon_state.RuntimeHandle(plugin_name='discord', plugin_loader=plugin_loader,
                                         settings=dict(settings or {}))
        daemon_state.handle = new
        new.thread = threading.Thread(target=_run_loop, args=(new,), daemon=True, name='discord-daemon')
        new.thread.start()
        new.started.wait(timeout=10)
        if new.failed.is_set():
            daemon_state.handle = None
            raise RuntimeError(f'Discord plugin failed to start: {new.startup_error}')
        try:
            plugin_loader.register_reply_handler(new.plugin_name, _reply_handler)
        except Exception:
            # Without a handler every reply is answered into the void for the life of the process.
            logger.error('[DISCORD] Reply handler registration FAILED — replies will not be delivered', exc_info=True)
        logger.info('[DISCORD] Daemon started (health=%s)', get_health_state())


def stop():
    with daemon_state.lifecycle_lock:
        current = daemon_state.handle
        if not current:
            return
        if current.loop and current.loop.is_running() and current.container:
            future = asyncio.run_coroutine_threadsafe(current.container.stop(), current.loop)
            try:
                future.result(timeout=10)
            except Exception:
                logger.warning('[DISCORD] Container stop timed out/failed; forcing loop shutdown', exc_info=True)
            current.loop.call_soon_threadsafe(current.loop.stop)
        if current.thread and current.thread.is_alive():
            current.thread.join(timeout=10)
        daemon_state.handle = None
        # A handler bound to a dead runtime consumed events into "runtime is unavailable".
        try:
            unreg = getattr(current.plugin_loader, 'unregister_reply_handler', None)
            if callable(unreg):
                unreg(current.plugin_name)
        except Exception:
            logger.debug('Reply handler unregister failed', exc_info=True)


def on_settings_saved(settings: dict):
    """Core's settings-saved hook for daemon plugins."""
    runtime = get_runtime()
    if runtime:
        try:
            runtime.refresh_settings()
        except Exception:
            logger.debug('[DISCORD] settings refresh failed', exc_info=True)


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
        return 'starting' if (handle and handle.thread and handle.thread.is_alive()) else 'stopped'
    return runtime.health.state


def list_connected() -> list[str]:
    runtime = get_runtime()
    return runtime.transport.list_connected() if runtime else []


def get_client(account_name: str):
    """The live py-cord client for an account — a public seam for out-of-tree add-ons (no in-house caller)."""
    runtime = get_runtime()
    return runtime.transport.get_client(str(account_name or '').strip()) if runtime else None


def run_coroutine(coro: Awaitable):
    loop = get_loop()
    if not loop or not loop.is_running():
        raise RuntimeError('Discord runtime loop is not running')
    return asyncio.run_coroutine_threadsafe(coro, loop)


def _run_loop(current: daemon_state.RuntimeHandle):
    loop = asyncio.new_event_loop()
    current.loop = loop
    asyncio.set_event_loop(loop)

    async def _bootstrap():
        container = RuntimeContainer(plugin_name=current.plugin_name, plugin_loader=current.plugin_loader,
                                     settings=current.settings, loop=loop)
        current.container = container
        await container.start()

    try:
        loop.run_until_complete(_bootstrap())
        current.started.set()
        loop.run_forever()
    except BaseException as exc:
        current.startup_error = exc
        current.failed.set()
        current.started.set()
        logger.error('Discord daemon crashed: %s', exc, exc_info=True)
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
if __name__ != _CANONICAL and _CANONICAL not in sys.modules:
    sys.modules[_CANONICAL] = sys.modules[__name__]
