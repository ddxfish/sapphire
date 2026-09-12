"""Sapphire continuity scheduler entrypoint for morning greetings."""

from __future__ import annotations

from plugins.discord.lib.server_time import now_local
from plugins.discord.schedule._runtime import connected_accounts, execute_proactive, reload_settings


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not runtime.greeting_service or not runtime.proactive_executor:
        return 'Skipped (runtime unavailable)'
    settings = reload_settings(runtime)
    if not settings:
        return 'Skipped (no settings)'
    if not settings.proactive.greeting_enabled:
        return 'Skipped (morning greetings disabled)'
    targets = settings.proactive.greeting_targets or []
    if not targets:
        return 'Skipped (no greeting channels selected)'
    accounts = connected_accounts(runtime)
    if not accounts:
        return 'Skipped (no connected Discord accounts)'
    sent = 0
    skipped = 0
    now = now_local()
    for account_name in accounts:
        for intention in runtime.greeting_service.evaluate(account_name, settings, now=now):
            result = execute_proactive(runtime, intention, settings)
            if result.get('status') == 'sent':
                sent += 1
            else:
                skipped += 1
    return f'Morning greeting complete ({sent} sent, {skipped} skipped, hour={now.hour})'
