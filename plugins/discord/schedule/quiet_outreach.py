"""Sapphire continuity scheduler entrypoint for quiet outreach."""

from __future__ import annotations

from plugins.discord.lib.server_time import now_local


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not runtime.outreach_service or not runtime.proactive_executor:
        return 'Skipped (runtime unavailable)'
    settings = runtime.settings_store.resolve() if runtime.settings_store else None
    if not settings:
        return 'Skipped (no settings)'
    accounts = runtime.transport.list_connected() if runtime.transport else []
    if not accounts and runtime.scheduler_bridge:
        accounts = sorted(runtime.scheduler_bridge.active_daemon_accounts('quiet_outreach'))
    sent = 0
    now = now_local()
    for account_name in accounts:
        for intention in runtime.outreach_service.evaluate(account_name, settings, now=now, now_ts=now.timestamp()):
            decision = runtime.policy_service.evaluate_proactive_intention(intention, settings)
            if not decision.get('allowed'):
                continue
            runtime.proactive_executor.execute(intention)
            sent += 1
    return f'Quiet outreach complete ({sent} sent)'
