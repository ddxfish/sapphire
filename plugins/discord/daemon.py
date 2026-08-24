"""Daemon entrypoint for the Discord cognitive plugin."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import MutableMapping
from typing import Any, Awaitable, Iterator, Optional

from plugins.discord.lib.core_compat import (
    ensure_discord_llm_provider_override,
    ensure_execution_context_images_support,
)
from plugins.discord.runtime.container import RuntimeContainer
from plugins.discord.runtime import daemon_state

logger = logging.getLogger(__name__)


class _ClientsView(MutableMapping):
    """Legacy-compatible view of account → py-cord client.

    Older callers (Mission Control health digest, routes) import
    ``get_client`` / ``_clients`` from this module. The cognitive runtime
    keeps clients on ``transport._accounts``; this view mirrors that map.
    """

    def _snapshot(self) -> dict[str, Any]:
        runtime = get_runtime()
        if not runtime or not runtime.transport:
            return {}
        return runtime.transport.client_map()

    def __getitem__(self, key: str) -> Any:
        client = self._snapshot().get(key)
        if client is None:
            raise KeyError(key)
        return client

    def __setitem__(self, key: str, value: Any) -> None:
        runtime = get_runtime()
        if not runtime or not runtime.transport:
            raise RuntimeError('Discord runtime is not available')
        state = runtime.transport._accounts.setdefault(key, {'name': key, 'client': None})
        state['client'] = value

    def __delitem__(self, key: str) -> None:
        runtime = get_runtime()
        if not runtime or not runtime.transport:
            raise KeyError(key)
        state = runtime.transport._accounts.get(key)
        if not state or state.get('client') is None:
            raise KeyError(key)
        state['client'] = None

    def __iter__(self) -> Iterator[str]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._snapshot())

    def __repr__(self) -> str:
        return f'_ClientsView({self._snapshot()!r})'


def _reply_handler(task, event_data: dict, response_text: str):
    runtime = get_runtime()
    if not runtime or not runtime.conversation_service:
        logger.warning('Discord reply handler called but runtime is unavailable')
        return None
    message_id = str((event_data or {}).get('message_id', ''))
    if runtime.event_bridge:
        runtime.event_bridge.clear_pending_payload(message_id)
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
            logger.debug('Reply handler registration unavailable', exc_info=True)
        ensure_execution_context_images_support()
        ensure_discord_llm_provider_override()
        logger.info('[discord_cognitive] Daemon started (health=%s)', get_health_state())


def stop():
    with daemon_state.lifecycle_lock:
        handle = daemon_state.handle
        if not handle:
            return
        if handle.loop and handle.loop.is_running() and handle.container:
            future = asyncio.run_coroutine_threadsafe(handle.container.stop(), handle.loop)
            future.result(timeout=10)
            handle.loop.call_soon_threadsafe(handle.loop.stop)
        if handle.thread and handle.thread.is_alive():
            handle.thread.join(timeout=10)
        daemon_state.handle = None


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


# Module-level aliases kept for ``from plugins.discord.daemon import _clients, _loop``.
_clients = _ClientsView()


def run_coroutine(coro: Awaitable):
    loop = get_loop()
    if not loop or not loop.is_running():
        raise RuntimeError('Discord cognitive runtime loop is not running')
    return asyncio.run_coroutine_threadsafe(coro, loop)


def __getattr__(name: str):
    # Legacy modules import ``_loop`` as a module attribute.
    if name == '_loop':
        return get_loop()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
