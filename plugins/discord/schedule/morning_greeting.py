"""Sapphire continuity scheduler entrypoint for morning greetings + wake replay."""

from __future__ import annotations

from plugins.discord.lib.server_time import now_local
from plugins.discord.schedule._runtime import connected_accounts, execute_proactive, reload_settings


def _replay_wake_buffers(runtime, settings, accounts, now) -> int:
    """At the wake hour, answer the mentions she slept through.

    Runs even with greetings disabled — the sleep schedule owns this leg.
    Bypasses execute_proactive's per-kind cooldown: these are replies to
    direct pings, already capped by sleep_buffered_reply_max per channel.
    Channels come from the buffer table itself, not greeting_targets —
    mentions buffer in EVERY watched channel during sleep hours.
    One failed send must not kill the rest of the replay or the greeting leg.
    """
    import logging
    logger = logging.getLogger(__name__)
    proactive = settings.proactive
    if not proactive.sleep_schedule_enabled or not runtime.sleep_service:
        return 0
    if now.hour != int(proactive.greeting_utc_hour) % 24:
        return 0
    max_replies = max(0, int(getattr(proactive, 'sleep_buffered_reply_max', 3)))
    if not max_replies:
        return 0
    replayed = 0
    repo = runtime.sleep_service.proactive_repository
    for account_name in accounts:
        try:
            channels = repo.list_buffered_channels(account_name)
        except Exception:
            logger.exception('Wake replay: listing buffered channels failed for %s', account_name)
            continue
        for channel_id in channels:
            try:
                intentions = runtime.sleep_service.drain_wake_buffer(
                    account_name, channel_id, max_replies=max_replies)
            except Exception:
                logger.exception('Wake replay: drain failed for %s/%s', account_name, channel_id)
                continue
            for intention in intentions:
                try:
                    result = runtime.proactive_executor.execute(intention)
                except Exception:
                    logger.exception('Wake replay: send failed for %s/%s', account_name, channel_id)
                    continue
                if result.get('status') in ('sent', 'queued'):
                    replayed += 1
    return replayed


def run(event):
    from plugins.discord.daemon import get_runtime

    runtime = get_runtime()
    if not runtime or not runtime.greeting_service or not runtime.proactive_executor:
        return 'Skipped (runtime unavailable)'
    settings = reload_settings(runtime)
    if not settings:
        return 'Skipped (no settings)'
    accounts = connected_accounts(runtime)
    if not accounts:
        return 'Skipped (no connected Discord accounts)'
    now = now_local()
    replayed = _replay_wake_buffers(runtime, settings, accounts, now)
    if not settings.proactive.greeting_enabled:
        return f'Skipped greetings (disabled); {replayed} wake replies'
    targets = settings.proactive.greeting_targets or []
    if not targets:
        return f'Skipped greetings (no channels selected); {replayed} wake replies'
    sent = 0
    skipped = 0
    for account_name in accounts:
        for intention in runtime.greeting_service.evaluate(account_name, settings, now=now):
            result = execute_proactive(runtime, intention, settings)
            if result.get('status') == 'sent':
                sent += 1
            else:
                skipped += 1
    return f'Morning greeting complete ({sent} sent, {skipped} skipped, {replayed} wake replies, hour={now.hour})'
