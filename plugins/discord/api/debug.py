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


async def get_cognition_debug(**kwargs):
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'cognition_debug_service', None):
        return {
            'situations': [],
            'intentions': [],
            'gates': [],
            'daemon_running': is_daemon_alive(),
            'settings': {},
        }
    settings = runtime.settings_store.resolve() if runtime.settings_store else None
    cognitive = getattr(settings, 'cognitive', None) if settings else None
    profile = getattr(settings, 'profile', None) if settings else None
    presence = getattr(settings, 'presence', None) if settings else None
    snap = runtime.cognition_debug_service.snapshot()
    snap['daemon_running'] = is_daemon_alive()
    snap['settings'] = {
        'situation_enabled': bool(getattr(cognitive, 'situation_enabled', False)),
        'situation_in_prompt': bool(getattr(cognitive, 'situation_in_prompt', False)),
        'intention_competition_enabled': bool(getattr(cognitive, 'intention_competition_enabled', False)),
        'relationship_policy_enabled': bool(getattr(profile, 'relationship_policy_enabled', False)),
        'relationship_policy_strength': str(getattr(profile, 'relationship_policy_strength', 'normal') or 'normal'),
        'situation_presence_enabled': bool(getattr(presence, 'situation_presence_enabled', False)),
    }
    return snap
