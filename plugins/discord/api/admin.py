"""Admin routes for retention, privacy, and import."""

from __future__ import annotations

from pathlib import Path

from plugins.discord.daemon import get_runtime
from plugins.discord.tools.import_from_leona import LeonaImportService


async def purge_retention(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.retention_service or not runtime.settings_store:
        return {'error': 'Runtime not available'}
    settings = runtime.settings_store.resolve()
    return runtime.retention_service.purge(settings)


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
    )


async def import_from_leona(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'error': 'Runtime not available'}
    body = kwargs.get('body') or {}
    leona_db_path = str(body.get('leona_db_path', '')).strip()
    if not leona_db_path:
        return {'error': 'leona_db_path required'}
    if not Path(leona_db_path).exists():
        return {'error': 'leona database not found'}
    service = LeonaImportService(
        leona_db_path=leona_db_path,
        memory_repository=runtime.memory_repository,
        profile_repository=runtime.profile_repository,
        sqlite_service=runtime.sqlite_service,
        settings_repository=runtime.channel_repository,
    )
    include = body.get('include') or ['pinned_memories', 'profile_facts', 'profile_summaries']
    leona_settings = body.get('leona_settings')
    return service.run(include=include, leona_settings=leona_settings)


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
        'affect': runtime.profile_service.get_affect(account).to_dict() if runtime.profile_service and account else {},
        'active_tasks': runtime.world_model_service.list_tasks(account, status='pending', limit=10) if runtime.world_model_service and account else [],
        'voice_sessions': [
            session.to_dict()
            for session in (runtime.voice_session_service.list_active(account) if runtime.voice_session_service and account else [])
        ],
        'connected_accounts': runtime.transport.list_connected() if runtime.transport else [],
    }
    return summary
