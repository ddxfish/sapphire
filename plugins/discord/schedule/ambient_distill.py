"""Sapphire continuity scheduler entrypoint for ambient profile distill."""

from __future__ import annotations

from plugins.discord.schedule._runtime import connected_accounts, reload_settings


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'distill_service', None):
        return 'Skipped (runtime unavailable)'
    settings = reload_settings(runtime)
    if not settings:
        return 'Skipped (no settings)'
    profile = getattr(settings, 'profile', None)
    if not profile or not getattr(profile, 'enabled', True):
        return 'Skipped (memory disabled)'
    if not getattr(profile, 'ambient_distill_enabled', False):
        return 'Skipped (ambient distill disabled)'

    accounts = connected_accounts(runtime)
    if not accounts:
        return 'Skipped (no connected accounts)'

    totals = {'accounts': 0, 'users': 0, 'facts': 0, 'skipped': 0}
    details = []
    for account in accounts:
        result = runtime.distill_service.run_for_account(account, settings, force=False)
        totals['accounts'] += 1
        if result.get('status') == 'ok':
            totals['users'] += int(result.get('users_considered') or 0)
            totals['facts'] += int(result.get('facts_added') or 0)
        else:
            totals['skipped'] += 1
        details.append(f"{account}:{result.get('status')}:{result.get('facts_added', result.get('reason', ''))}")
    return (
        f"Ambient distill accounts={totals['accounts']} users={totals['users']} "
        f"facts={totals['facts']} skipped={totals['skipped']} ({'; '.join(details)})"
    )
