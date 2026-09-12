"""Voice session API routes."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime
from plugins.discord.sapphire.voice_chat import voice_chat_name


async def list_voice_sessions(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.voice_session_service:
        return {'sessions': []}
    query = kwargs.get('query') or {}
    account_name = query.get('account') or ''
    if not account_name and runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account_name = connected[0]
    if not account_name:
        return {'sessions': []}
    runner = getattr(runtime, 'discord_conversation_runner', None)
    sessions = []
    for session in runtime.voice_session_service.list_active(account_name):
        payload = session.to_dict()
        payload['chat_name'] = voice_chat_name(session.guild_id, session.channel_id)
        payload['conversation_active'] = bool(
            runner and runner.is_active(session.session_id)
        )
        sessions.append(payload)
    connections = runtime.voice_transport.list_connections(account_name) if runtime.voice_transport else []
    return {'sessions': sessions, 'connections': connections}


async def voice_diagnostics(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'status': 'daemon_offline'}
    from plugins.discord.voice.voice_deps import voice_stack_info

    runner = getattr(runtime, 'discord_conversation_runner', None)
    bridge = getattr(runtime, 'voice_event_bridge', None)
    payload = {
        'status': 'ok',
        'voice_stack': voice_stack_info(),
        'conversation_runner': {
            'active_count': len(runner._sessions) if runner else 0,
            'sessions': list(runner._sessions.keys()) if runner else [],
        },
        'event_bridge': bridge.diagnostics() if bridge else {'bridge_running': False},
    }
    return payload


async def auto_join_status(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.voice_auto_join_service:
        return {'enabled': False, 'reason': 'daemon_offline', 'targets': []}
    query = kwargs.get('query') or {}
    account_name = str(query.get('account') or '').strip()
    if not account_name and runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account_name = connected[0]
    if not account_name:
        return {'enabled': False, 'reason': 'no_connected_account', 'targets': []}
    return runtime.voice_auto_join_service.inspect(account_name)
