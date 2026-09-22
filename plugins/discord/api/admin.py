"""Privacy route: forget one person (their message rows and name cache row)."""

from __future__ import annotations

from plugins.discord.api.storage_access import open_storage
from plugins.discord.storage import retention


def forget_user(**kwargs):
    body = kwargs.get('body') or {}
    user_id = str(body.get('user_id', '')).strip()
    if not user_id:
        return {'error': 'user_id required'}
    with open_storage() as storage:
        return retention.forget_user(storage.sqlite_service, user_id)
