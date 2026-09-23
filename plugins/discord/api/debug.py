"""The Debug tab's one feed: recent decisions (ids only, no content)."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime, is_daemon_alive


def list_decisions(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'decisions': [], 'daemon_running': is_daemon_alive()}
    query = kwargs.get('query') or {}
    try:
        limit = int(query.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    return {'decisions': runtime.decisions.list(limit=limit), 'daemon_running': True}


def clear_decisions(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'status': 'unavailable', 'cleared': 0}
    return {'status': 'cleared', 'cleared': runtime.decisions.clear()}
