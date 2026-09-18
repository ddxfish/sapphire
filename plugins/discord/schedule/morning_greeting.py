"""Sapphire continuity scheduler entrypoint for morning greetings + wake replay."""

from __future__ import annotations

from plugins.discord.lib.server_time import now_local, user_hour
from plugins.discord.proactive.targets import parse_target
from plugins.discord.schedule._runtime import connected_accounts, execute_proactive, reload_settings


def _wake_targets(runtime, settings, accounts) -> int:
    """Wake every greeting target at the wake hour — sleep-schedule owned, NOT
    behind greeting_enabled. Wake used to happen only inside the greeting leg or
    for channels with buffered mentions, so with greetings off a quiet channel
    stayed asleep forever: dropped traffic, no outreach, presence pinned to
    sleep (hunt 2.13.0, row 6)."""
    proactive = settings.proactive
    if not proactive.sleep_schedule_enabled or not runtime.sleep_service:
        return 0
    woke = 0
    for entry in proactive.greeting_targets or []:
        parsed = parse_target(entry)
        if not parsed or parsed[0] not in accounts:
            continue
        try:
            runtime.sleep_service.wake_channel(parsed[0], parsed[1])
            woke += 1
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Wake failed for %s/%s', parsed[0], parsed[1])
    return woke


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
    if user_hour(now) != int(proactive.greeting_utc_hour) % 24:
        return 0
    max_replies = max(0, int(getattr(proactive, 'sleep_buffered_reply_max', 3)))
    if not max_replies:
        return 0
    # Replay needs a bot that is REALLY online: connected_accounts() falls back
    # to task-configured names when nothing is connected, and the old drain
    # committed before the first send — a login stuck in `connecting` ate the
    # night's mentions with nothing posted (row 7). Buffers stay for a live bot.
    live = set(runtime.transport.list_connected()) if getattr(runtime, 'transport', None) else set()
    replayed = 0
    repo = runtime.sleep_service.proactive_repository
    for account_name in accounts:
        if account_name not in live:
            logger.warning('Wake replay skipped for %s — bot is not connected; buffered mentions kept', account_name)
            continue
        try:
            channels = repo.list_buffered_channels(account_name)
        except Exception:
            logger.exception('Wake replay: listing buffered channels failed for %s', account_name)
            continue
        for channel_id in channels:
            try:
                intentions = runtime.sleep_service.drain_wake_buffer(
                    account_name, channel_id, max_replies=max_replies, commit=False)
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
            try:
                runtime.sleep_service.commit_wake_drain(account_name, channel_id)
            except Exception:
                logger.exception('Wake replay: commit failed for %s/%s', account_name, channel_id)
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
    woke = _wake_targets(runtime, settings, accounts)
    if not settings.proactive.greeting_enabled:
        return f'Skipped greetings (disabled); {replayed} wake replies, {woke} channels woken'
    targets = settings.proactive.greeting_targets or []
    if not targets:
        return f'Skipped greetings (no channels selected); {replayed} wake replies'
    sent = 0
    static = 0
    skipped = 0
    for account_name in accounts:
        for intention in runtime.greeting_service.evaluate(account_name, settings, now=now):
            result = execute_proactive(runtime, intention, settings)
            if result.get('status') == 'sent' and result.get('delivery') == 'static':
                static += 1        # the fallback TEXT posted — no daemon task took the event (row 18)
            elif result.get('status') == 'sent':
                sent += 1
            else:
                skipped += 1
    return (f'Morning greeting complete ({sent} sent, {static} static fallback, {skipped} skipped, '
            f'{replayed} wake replies, hour={user_hour(now)})')
