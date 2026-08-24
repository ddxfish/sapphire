"""Health and trace routes."""

from __future__ import annotations

from plugins.discord.daemon import get_health_state, get_runtime, is_daemon_alive


async def get_health(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {
            'state': get_health_state(),
            'daemon_running': is_daemon_alive(),
            'connected_accounts': [],
        }
    payload = runtime.health.as_dict()
    payload['daemon_running'] = True
    payload['connected_accounts'] = runtime.transport.list_connected() if runtime.transport else []
    return payload


async def list_traces(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.trace_repository:
        return {'traces': [], 'cognitive': {}}
    query = kwargs.get('query') or {}
    try:
        limit = int(query.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    cognitive = {}
    if runtime.profile_service and runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account = connected[0]
            cognitive = {
                'affect': runtime.profile_service.get_affect(account).to_dict(),
                'activation': runtime.attention_service.top_entities(account, limit=5) if runtime.attention_service else [],
                'tasks': runtime.world_model_service.list_tasks(account, status='pending', limit=5) if runtime.world_model_service else [],
                'voice_sessions': [
                    session.to_dict()
                    for session in (runtime.voice_session_service.list_active(account) if runtime.voice_session_service else [])
                ],
            }
    return {
        'traces': runtime.trace_repository.list_traces(limit=limit),
        'trace_summary': runtime.trace_service.summary() if runtime.trace_service else {},
        'cognitive': cognitive,
    }
