"""Proactive schedule test and diagnostics routes."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime, is_daemon_alive
from plugins.discord.proactive.test_paths import collect_proactive_diagnostics, run_proactive_test


async def proactive_diagnostics(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {
            'error': 'runtime_unavailable',
            'daemon_running': is_daemon_alive(),
        }
    payload = collect_proactive_diagnostics(runtime)
    payload['daemon_running'] = is_daemon_alive()
    return payload


async def test_proactive(**kwargs):
    runtime = get_runtime()
    if not runtime:
        return {'error': 'runtime_unavailable', 'daemon_running': is_daemon_alive()}
    body = kwargs.get('body') or {}
    kind = str(body.get('kind') or '').strip().lower()
    account_name = str(body.get('account_name') or '').strip() or None
    channel_id = str(body.get('channel_id') or '').strip() or None
    dry_run = bool(body.get('dry_run'))
    reset_sleep_state = bool(body.get('reset_sleep_state', True))
    result = run_proactive_test(
        runtime,
        kind,
        account_name=account_name,
        channel_id=channel_id,
        dry_run=dry_run,
        reset_sleep_state=reset_sleep_state,
    )
    result['daemon_running'] = is_daemon_alive()
    return result
