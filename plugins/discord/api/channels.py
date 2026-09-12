"""Discord guild/channel discovery routes."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime, run_coroutine


def _channel_type(kwargs) -> str:
    query = kwargs.get('query') or {}
    return str(query.get('channel_type', 'text') or 'text').strip().lower()


async def _list_targets(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.transport:
        return {'targets': [], 'connected': False, 'error': 'Daemon offline'}
    connected = runtime.transport.list_connected()
    if not connected:
        return {'targets': [], 'connected': False, 'error': 'No connected bots'}
    channel_type = _channel_type(kwargs)
    list_fn = (
        runtime.transport.list_voice_targets
        if channel_type == 'voice'
        else runtime.transport.list_proactive_targets
    )
    try:
        future = run_coroutine(list_fn())
        targets = future.result(timeout=60)
    except Exception as exc:
        return {'targets': [], 'connected': False, 'error': str(exc)}
    return {'targets': targets, 'connected': bool(targets), 'channel_type': channel_type}


async def list_proactive_targets(**kwargs):
    return await _list_targets(**kwargs)


async def list_voice_targets(**kwargs):
    kwargs = dict(kwargs)
    query = dict(kwargs.get('query') or {})
    query.setdefault('channel_type', 'voice')
    kwargs['query'] = query
    return await _list_targets(**kwargs)


async def list_bot_allowlist_candidates(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.transport:
        return {'bots': [], 'connected': False, 'error': 'Daemon offline'}
    connected = runtime.transport.list_connected()
    if not connected:
        return {'bots': [], 'connected': False, 'error': 'No connected bots'}
    try:
        future = run_coroutine(runtime.transport.list_guild_bots())
        bots = future.result(timeout=60)
    except Exception as exc:
        return {'bots': [], 'connected': False, 'error': str(exc)}
    return {'bots': bots, 'connected': bool(bots)}
