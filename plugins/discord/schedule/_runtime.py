"""Shared helpers for Sapphire continuity schedule handlers."""

from __future__ import annotations


def reload_settings(runtime):
    if not runtime:
        return None
    if getattr(runtime, 'channel_repository', None):
        fresh = runtime.channel_repository.load_settings_store()
        current = getattr(runtime, 'settings_store', None)
        if current is not None and hasattr(current, 'replace_from'):
            current.replace_from(fresh)      # in place: every service holds this object (row 44)
        else:
            runtime.settings_store = fresh
    return runtime.settings_store.resolve() if runtime.settings_store else None


def connected_accounts(runtime) -> list[str]:
    if not runtime or not runtime.transport:
        return []
    accounts = runtime.transport.list_connected()
    if accounts:
        return sorted(accounts)
    if runtime.scheduler_bridge:
        return sorted(runtime.scheduler_bridge.selected_accounts())
    return []
