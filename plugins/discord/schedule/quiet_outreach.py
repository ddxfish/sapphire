"""Sapphire continuity scheduler entrypoint for quiet outreach."""

from __future__ import annotations

from plugins.discord.lib.server_time import now_local
from plugins.discord.schedule._runtime import connected_accounts, execute_proactive, reload_settings


def run(event):
    """Same helpers as the other schedule legs (hunt 2.13.0, row 42): this one
    used to inline the policy call (no proactive_skipped trace), read a stale
    settings store, and count every attempt as sent."""
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not runtime.outreach_service or not runtime.proactive_executor:
        return 'Skipped (runtime unavailable)'
    settings = reload_settings(runtime)
    if not settings:
        return 'Skipped (no settings)'
    accounts = connected_accounts(runtime)
    if not accounts:
        return 'Skipped (no connected Discord accounts)'
    sent = 0
    skipped = 0
    now = now_local()
    for account_name in accounts:
        for intention in runtime.outreach_service.evaluate(account_name, settings, now=now, now_ts=now.timestamp()):
            result = execute_proactive(runtime, intention, settings)
            if result.get('status') == 'sent':
                sent += 1
            else:
                skipped += 1
    return f'Quiet outreach complete ({sent} sent, {skipped} skipped)'
