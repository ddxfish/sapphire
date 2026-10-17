"""Daemon health route."""

from __future__ import annotations

from plugins.discord.daemon import get_health_state, get_runtime, is_daemon_alive


def get_health(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'state': get_health_state(), 'daemon_running': is_daemon_alive(), 'connected_accounts': []}
    payload = runtime.health.as_dict()
    payload['daemon_running'] = True
    payload['connected_accounts'] = runtime.transport.list_connected()
    return payload
