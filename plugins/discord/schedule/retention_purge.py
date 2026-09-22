"""Sapphire continuity scheduler entrypoint for the daily retention purge."""

from __future__ import annotations

from plugins.discord.storage import retention


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime:
        return 'Skipped (runtime unavailable)'
    result = retention.purge(runtime.sqlite_service, runtime.settings_store.resolve())
    if result.get('status') != 'purged':
        return f"Skipped ({result.get('reason', 'unknown')})"
    counts = result.get('results') or {}
    detail = ', '.join(f'{k}={v}' for k, v in counts.items()) or 'nothing eligible'
    return f'Retention purge complete ({sum(counts.values())} rows: {detail})'
