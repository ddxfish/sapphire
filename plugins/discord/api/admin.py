"""Admin routes for retention and privacy."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime


async def purge_retention(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.retention_service or not runtime.settings_store:
        return {'error': 'Runtime not available'}
    settings = runtime.settings_store.resolve()
    # The purge scans/deletes on the shared connection — run it off the event
    # loop so the whole web UI doesn't freeze for the duration.
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, runtime.retention_service.purge, settings)


async def forget_user(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.retention_service:
        return {'error': 'Runtime not available'}
    body = kwargs.get('body') or {}
    account_name = str(body.get('account_name', '')).strip()
    user_id = str(body.get('user_id', '')).strip()
    if not account_name or not user_id:
        return {'error': 'account_name and user_id required'}
    return runtime.retention_service.forget_user(
        account_name,
        user_id,
        memory_repository=runtime.memory_repository,
        profile_repository=runtime.profile_repository,
        milestone_repository=getattr(runtime, 'milestone_repository', None),
        interest_repository=getattr(runtime, 'interest_repository', None),
    )


async def operator_summary(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'error': 'Runtime not available'}
    account = ''
    if runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account = connected[0]
    summary = {
        'health': runtime.health.as_dict(),
        'trace_summary': runtime.trace_service.summary() if runtime.trace_service else {},
        'active_tasks': runtime.world_model_service.list_tasks(account, status='pending', limit=10) if runtime.world_model_service and account else [],
        'voice_sessions': [
            session.to_dict()
            for session in (runtime.voice_session_service.list_active(account) if runtime.voice_session_service and account else [])
        ],
        'connected_accounts': runtime.transport.list_connected() if runtime.transport else [],
    }
    return summary
