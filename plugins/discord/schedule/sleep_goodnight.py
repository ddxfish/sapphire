"""Sapphire continuity scheduler entrypoint for sleep goodnight messages."""

from __future__ import annotations

from plugins.discord.lib.server_time import now_local
from plugins.discord.schedule._runtime import connected_accounts, execute_proactive, reload_settings


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not runtime.sleep_service or not runtime.proactive_executor:
        return 'Skipped (runtime unavailable)'
    settings = reload_settings(runtime)
    if not settings:
        return 'Skipped (no settings)'
    if not settings.proactive.sleep_schedule_enabled:
        return 'Skipped (sleep schedule disabled)'
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
        for intention in runtime.sleep_service.evaluate_goodnight(account_name, settings, now=now):
            result = execute_proactive(runtime, intention, settings)
            if result.get('status') == 'sent':
                sent += 1
            else:
                skipped += 1
    return f'Sleep goodnight complete ({sent} sent, {skipped} skipped, hour={now.hour})'
