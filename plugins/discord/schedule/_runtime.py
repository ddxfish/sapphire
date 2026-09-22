"""Shared helpers for Sapphire continuity schedule handlers."""

from __future__ import annotations


def reload_settings(runtime):
    """The effective settings (read live from core on every resolve)."""
    if not runtime or not getattr(runtime, 'settings_store', None):
        return None
    return runtime.settings_store.resolve()


def connected_accounts(runtime) -> list[str]:
    if not runtime or not runtime.transport:
        return []
    accounts = runtime.transport.list_connected()
    if accounts:
        return sorted(accounts)
    if runtime.scheduler_bridge:
        return sorted(runtime.scheduler_bridge.selected_accounts())
    return []
