"""Health and trace routes."""

from __future__ import annotations

from plugins.discord.daemon import get_health_state, get_runtime, is_daemon_alive


def get_health(**kwargs):
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


def list_traces(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.trace_repository:
        return {'traces': [], 'trace_summary': {}, 'cognitive': {}, 'daemon_running': is_daemon_alive()}
    query = kwargs.get('query') or {}
    try:
        limit = int(query.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    trace_type = str(query.get('type') or query.get('trace_type') or '').strip() or None
    cognitive = {}
    if runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account = connected[0]
            cognitive = {
                'voice_sessions': [
                    session.to_dict()
                    for session in (runtime.voice_sessions.list_active(account) if runtime.voice_sessions else [])
                ],
            }
    return {
        'traces': runtime.trace_repository.list_traces(limit=limit, trace_type=trace_type),
        'trace_summary': runtime.trace_service.summary() if runtime.trace_service else {},
        'cognitive': cognitive,
        'daemon_running': is_daemon_alive(),
        'filter_type': trace_type or '',
    }
