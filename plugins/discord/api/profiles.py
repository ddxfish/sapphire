"""Profile and affect visibility routes."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime


async def list_profiles(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'profiles': [], 'affect': {}}
    query = kwargs.get('query') or {}
    account_name = query.get('account') or ''
    if not account_name and runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account_name = connected[0]
    if not account_name:
        return {'profiles': [], 'affect': {}}
    profiles = runtime.profile_service.list_profiles(account_name, limit=50)
    affect = runtime.profile_service.get_affect(account_name).to_dict()
    activation = []
    if runtime.attention_service:
        activation = runtime.attention_service.top_entities(account_name, limit=10)
    return {'profiles': profiles, 'affect': affect, 'activation': activation}
