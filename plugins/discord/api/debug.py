"""Operator debug routes."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime, is_daemon_alive


async def list_llm_debug(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.llm_debug_service:
        return {
            'entries': [],
            'daemon_running': is_daemon_alive(),
        }
    query = kwargs.get('query') or {}
    try:
        limit = int(query.get('limit', 10))
    except (TypeError, ValueError):
        limit = 10
    return {
        'entries': runtime.llm_debug_service.list_entries(limit=limit),
        'daemon_running': is_daemon_alive(),
    }


def clear_llm_debug(**kwargs):
    runtime = get_runtime()
    service = getattr(runtime, 'llm_debug_service', None) if runtime else None
    if service is None:
        return {'status': 'unavailable', 'cleared': 0}
    return {'status': 'cleared', 'cleared': service.clear()}
