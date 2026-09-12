"""Manual proactive pathway tests and schedule diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from plugins.discord.lib.server_time import now_local
from plugins.discord.models.intentions import GoodnightIntention, GreetChannelIntention, OutreachIntention
from plugins.discord.proactive.targets import parse_target
from plugins.discord.schedule._runtime import connected_accounts, execute_proactive, reload_settings


def _iter_targets(
    settings,
    *,
    account_name: str | None = None,
    channel_id: str | None = None,
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for entry in settings.proactive.greeting_targets or []:
        parsed = parse_target(entry)
        if not parsed:
            continue
        account, channel = parsed
        if account_name and account != account_name:
            continue
        if channel_id and channel != channel_id:
            continue
        rows.append((account, channel))
    return rows


def _sleep_state(runtime, account_name: str, channel_id: str) -> dict:
    if not runtime or not runtime.proactive_repository:
        return {}
    return dict(runtime.proactive_repository.get_sleep_state(account_name, channel_id) or {})


def _preview_text(runtime, intention) -> str:
    if not runtime or not runtime.proactive_executor:
        return ''
    try:
        return str(
            runtime.proactive_executor._resolve_message_text(
                intention,
                account_name=intention.account_name,
            )
            or ''
        ).strip()
    except Exception as exc:
        return f'[preview error: {exc}]'


def collect_proactive_diagnostics(runtime) -> dict[str, Any]:
    settings = reload_settings(runtime)
    now = now_local()
    if not runtime:
        return {'error': 'runtime_unavailable', 'server_time': now.isoformat()}
    if not settings:
        return {'error': 'no_settings', 'server_time': now.isoformat()}

    proactive = settings.proactive
    accounts = connected_accounts(runtime)
    targets = _iter_targets(settings)
    greeting_hour = int(proactive.greeting_utc_hour) % 24
    sleep_hour = int(proactive.sleep_utc_hour) % 24

    greeting_hints: list[str] = []
    if not proactive.greeting_enabled:
        greeting_hints.append('Morning greetings are disabled (proactive.greeting_enabled).')
    if now.hour != greeting_hour:
        greeting_hints.append(
            f'Current server hour is {now.hour}; greetings only fire at hour {greeting_hour}. '
            f'Sapphire continuity cron runs hourly (plugin schedule morning_greeting).'
        )
    if not targets:
        greeting_hints.append('No greeting channels selected.')
    if not accounts:
        greeting_hints.append('No connected Discord bot accounts.')

    scheduled_greeting = []
    if runtime.greeting_service:
        for account in accounts:
            scheduled_greeting.extend(runtime.greeting_service.evaluate(account, settings, now=now))

    outreach_hints: list[str] = []
    if not proactive.outreach_enabled:
        outreach_hints.append('Quiet outreach is disabled (proactive.outreach_enabled).')
    if runtime.outreach_service:
        if now.hour in runtime.outreach_service._greeting_blocked_hours(proactive):
            outreach_hints.append(
                f'Outreach is blocked near greeting hour (lead={proactive.greeting_outreach_lead_hours}h).'
            )
        if runtime.outreach_service._in_sleep_hours(proactive, now):
            outreach_hints.append('Currently in sleep hours — outreach is skipped.')
    if not targets:
        outreach_hints.append('No greeting channels selected.')
    if not accounts:
        outreach_hints.append('No connected Discord bot accounts.')

    scheduled_outreach = []
    if runtime.outreach_service:
        for account in accounts:
            scheduled_outreach.extend(
                runtime.outreach_service.evaluate(
                    account,
                    settings,
                    now=now,
                    now_ts=now.timestamp(),
                )
            )

    goodnight_hints: list[str] = []
    if not proactive.sleep_schedule_enabled:
        goodnight_hints.append('Sleep schedule is disabled (proactive.sleep_schedule_enabled).')
    elif runtime.sleep_service and not runtime.sleep_service.in_sleep_hours(settings, now=now):
        goodnight_hints.append(
            f'Not in sleep window (sleep hour {sleep_hour} → wake/greeting hour {greeting_hour}).'
        )
    if proactive.sleep_schedule_enabled and now.minute not in getattr(
        runtime.sleep_service, 'GOODNIGHT_MINUTES', (0, 15, 30, 45)
    ):
        goodnight_hints.append(
            f'Goodnight only fires at minutes {getattr(runtime.sleep_service, "GOODNIGHT_MINUTES", (0, 15, 30, 45))}; '
            f'now is :{now.minute:02d}. Sapphire cron runs every 15 minutes.'
        )
    if not targets:
        goodnight_hints.append('No greeting channels selected.')
    if not accounts:
        goodnight_hints.append('No connected Discord bot accounts.')

    scheduled_goodnight = []
    if runtime.sleep_service:
        for account in accounts:
            scheduled_goodnight.extend(
                runtime.sleep_service.evaluate_goodnight(account, settings, now=now)
            )

    channel_rows = []
    for account, channel in targets:
        row = {
            'account_name': account,
            'channel_id': channel,
            'connected': account in accounts,
            'sleep_state': _sleep_state(runtime, account, channel),
        }
        channel_rows.append(row)

    return {
        'server_time': now.isoformat(),
        'server_hour': now.hour,
        'server_minute': now.minute,
        'connected_accounts': accounts,
        'greeting_targets': [f'{a}:{c}' for a, c in targets],
        'greeting': {
            'enabled': bool(proactive.greeting_enabled),
            'configured_hour': greeting_hour,
            'would_fire_now': len(scheduled_greeting) > 0,
            'scheduled_intentions': len(scheduled_greeting),
            'hints': greeting_hints,
        },
        'outreach': {
            'enabled': bool(proactive.outreach_enabled),
            'would_fire_now': len(scheduled_outreach) > 0,
            'scheduled_intentions': len(scheduled_outreach),
            'hints': outreach_hints,
        },
        'goodnight': {
            'enabled': bool(proactive.sleep_schedule_enabled),
            'sleep_hour': sleep_hour,
            'would_fire_now': len(scheduled_goodnight) > 0,
            'scheduled_intentions': len(scheduled_goodnight),
            'hints': goodnight_hints,
        },
        'channels': channel_rows,
    }


def _build_test_intentions(
    kind: str,
    settings,
    *,
    account_name: str | None = None,
    channel_id: str | None = None,
) -> list:
    targets = _iter_targets(settings, account_name=account_name, channel_id=channel_id)
    intentions = []
    for account, channel in targets:
        meta = {'manual_test': True, 'test_kind': kind}
        if kind == 'greeting':
            intentions.append(GreetChannelIntention(
                intention_type='greet_channel',
                account_name=account,
                channel_id=channel,
                message_id='',
                reason='manual_test_greeting',
                prompt='',
                metadata=dict(meta),
            ))
        elif kind == 'goodnight':
            intentions.append(GoodnightIntention(
                intention_type='goodnight',
                account_name=account,
                channel_id=channel,
                message_id='',
                reason='manual_test_goodnight',
                prompt='',
                metadata=dict(meta),
            ))
        elif kind == 'outreach':
            intentions.append(OutreachIntention(
                intention_type='outreach',
                account_name=account,
                channel_id=channel,
                message_id='',
                reason='manual_test_outreach',
                prompt='Checking in — anything going on?',
                confidence=0.6,
                metadata=dict(meta),
            ))
    return intentions


def _execute_test_intention(runtime, intention, settings, *, dry_run: bool) -> dict:
    preview = _preview_text(runtime, intention)
    row = {
        'account_name': intention.account_name,
        'channel_id': intention.channel_id,
        'intention_type': intention.intention_type,
        'preview': preview[:500],
    }
    if dry_run:
        row['status'] = 'dry_run'
        if not preview:
            row['preview_error'] = 'empty_preview'
        return row
    if not runtime.proactive_executor:
        row['status'] = 'error'
        row['error'] = 'executor_unavailable'
        return row

    metadata = getattr(intention, 'metadata', None) or {}
    if metadata.get('manual_test'):
        affect = {}
        if runtime.profile_service:
            affect = runtime.profile_service.get_affect(intention.account_name).to_dict()
        decision = {'allowed': True, 'reason': 'manual_test'}
    else:
        affect = {}
        if runtime.profile_service:
            affect = runtime.profile_service.get_affect(intention.account_name).to_dict()
        decision = runtime.policy_service.evaluate_proactive_intention(intention, settings, affect=affect)

    if not decision.get('allowed'):
        row['status'] = 'skipped'
        row['reason'] = decision.get('reason', 'blocked')
        return row

    try:
        result = runtime.proactive_executor.execute(intention)
    except Exception as exc:
        row['status'] = 'error'
        row['error'] = str(exc)
        return row
    row.update(result or {})
    if intention.intention_type == 'greet_channel' and result.get('status') == 'sent':
        if runtime.greeting_service:
            runtime.greeting_service.mark_sent(intention)
    if intention.intention_type == 'outreach' and result.get('status') == 'sent':
        if runtime.outreach_service:
            runtime.outreach_service.mark_sent(intention)
    return row


def run_proactive_test(
    runtime,
    kind: str,
    *,
    account_name: str | None = None,
    channel_id: str | None = None,
    dry_run: bool = False,
    reset_sleep_state: bool = False,
) -> dict[str, Any]:
    kind = str(kind or '').strip().lower()
    if kind not in {'greeting', 'goodnight', 'outreach'}:
        return {'error': 'kind must be greeting, goodnight, or outreach'}

    settings = reload_settings(runtime)
    if not runtime:
        return {'error': 'runtime_unavailable'}
    if not settings:
        return {'error': 'no_settings'}

    diagnostics = collect_proactive_diagnostics(runtime)
    accounts = connected_accounts(runtime)
    if not accounts:
        return {
            'error': 'no_connected_accounts',
            'kind': kind,
            'dry_run': dry_run,
            'diagnostics': diagnostics,
        }

    intentions = _build_test_intentions(
        kind,
        settings,
        account_name=account_name or None,
        channel_id=channel_id or None,
    )
    if not intentions:
        return {
            'error': 'no_matching_targets',
            'kind': kind,
            'dry_run': dry_run,
            'diagnostics': diagnostics,
            'hint': 'Select greeting channels in the targets picker above and save settings.',
        }

    if reset_sleep_state and runtime.sleep_service:
        for intention in intentions:
            runtime.sleep_service.wake_channel(intention.account_name, intention.channel_id)

    results = []
    for intention in intentions:
        results.append(_execute_test_intention(runtime, intention, settings, dry_run=dry_run))

    sent = sum(1 for item in results if item.get('status') == 'sent')
    return {
        'kind': kind,
        'dry_run': dry_run,
        'reset_sleep_state': reset_sleep_state,
        'results': results,
        'sent': sent,
        'diagnostics': diagnostics,
    }
