"""Voice status route: live sessions, connections, the auto-join view, the voice stack."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime
from plugins.discord.sapphire.voice_chat import voice_chat_name


def voice_status(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'status': 'daemon_offline', 'sessions': [], 'connections': [], 'auto_join': {}, 'voice_stack': {}}
    from plugins.discord.voice.voice_deps import voice_stack_info

    account_name = str((kwargs.get('query') or {}).get('account') or '').strip()
    if not account_name:
        connected = runtime.transport.list_connected()
        account_name = connected[0] if connected else ''
    runner = runtime.discord_conversation_runner
    sessions = []
    for session in (runtime.voice_sessions.list_active(account_name) if account_name else []):
        payload = session.to_dict()
        payload['chat_name'] = voice_chat_name(session.guild_id, session.channel_id)
        payload['conversation_active'] = bool(runner.is_active(session.session_id))
        sessions.append(payload)
    return {
        'status': 'ok',
        'account': account_name,
        'sessions': sessions,
        'connections': runtime.voice_transport.list_connections(account_name) if account_name else [],
        'auto_join': runtime.voice_auto_join_service.inspect(account_name) if account_name
        else {'enabled': False, 'reason': 'no_connected_account', 'targets': []},
        'conversations': runner.active_chat_names(),
        'voice_stack': voice_stack_info(),
    }
