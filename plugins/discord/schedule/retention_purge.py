"""Sapphire continuity scheduler entrypoint for the daily retention purge."""

from __future__ import annotations

from plugins.discord.schedule._runtime import reload_settings


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not runtime.retention_service:
        return 'Skipped (runtime unavailable)'
    settings = reload_settings(runtime)
    if not settings:
        return 'Skipped (no settings)'
    result = runtime.retention_service.purge(settings)
    if result.get('status') != 'purged':
        return f"Skipped ({result.get('reason', 'unknown')})"
    counts = result.get('results') or {}
    total = sum(counts.values())
    detail = ', '.join(f'{k}={v}' for k, v in counts.items()) or 'nothing eligible'
    return f'Retention purge complete ({total} rows: {detail})'
