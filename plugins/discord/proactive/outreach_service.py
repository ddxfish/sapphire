"""Quiet-channel outreach intention generation."""

from __future__ import annotations

from datetime import datetime

from plugins.discord.lib.server_time import now_local
from plugins.discord.models.intentions import OutreachIntention
from plugins.discord.proactive.targets import parse_target


class OutreachService:
    def __init__(self, *, proactive_repository, channel_last_activity=None, trace_repository=None):
        self.proactive_repository = proactive_repository
        self.channel_last_activity = channel_last_activity or {}
        self.trace_repository = trace_repository

    def _greeting_blocked_hours(self, proactive) -> set[int]:
        if not proactive.greeting_enabled:
            return set()
        hour = int(proactive.greeting_utc_hour) % 24
        lead = max(0, min(6, int(proactive.greeting_outreach_lead_hours)))
        return {(hour - offset) % 24 for offset in range(lead + 1)}

    def _in_sleep_hours(self, proactive, now: datetime) -> bool:
        if not proactive.sleep_schedule_enabled:
            return False
        sleep = int(proactive.sleep_utc_hour) % 24
        wake = int(proactive.greeting_utc_hour) % 24
        hour = now.hour
        if sleep == wake:
            return False
        if sleep < wake:
            return sleep <= hour < wake
        return hour >= sleep or hour < wake

    def evaluate(self, account_name: str, settings, *, now: datetime | None = None, now_ts: float | None = None) -> list[OutreachIntention]:
        proactive = settings.proactive
        if not proactive.outreach_enabled:
            return []
        now = now or now_local()
        now_ts = now_ts if now_ts is not None else now.timestamp()
        if now.hour in self._greeting_blocked_hours(proactive):
            return []
        if self._in_sleep_hours(proactive, now):
            return []
        cooldown_seconds = max(3600, int(proactive.outreach_cooldown_hours) * 3600)
        stale_seconds = max(60, int(proactive.outreach_stale_minutes) * 60)
        intentions = []
        for entry in proactive.greeting_targets or []:
            parsed = parse_target(entry)
            if not parsed or parsed[0] != account_name:
                continue
            channel_id = parsed[1]
            state = self.proactive_repository.get_sleep_state(account_name, channel_id)
            if state.get('is_asleep'):
                continue
            if not self.proactive_repository.cooldown_elapsed(
                account_name, channel_id, 'outreach', min_seconds=cooldown_seconds, now=now_ts,
            ):
                continue
            last_activity = self.channel_last_activity.get(f'{account_name}:{channel_id}')
            if last_activity is None:
                last_activity = self.proactive_repository.last_channel_activity(account_name, channel_id)
            if last_activity and (now_ts - last_activity) < stale_seconds:
                continue
            prompt = 'Checking in — anything going on?'
            intentions.append(OutreachIntention(
                intention_type='outreach',
                account_name=account_name,
                channel_id=channel_id,
                message_id='',
                reason='quiet_outreach',
                prompt=prompt,
                confidence=0.6,
                metadata={'stale_seconds': stale_seconds},
            ))
        return intentions

    def mark_sent(self, intention: OutreachIntention) -> None:
        self.proactive_repository.record_cooldown(
            intention.account_name,
            intention.channel_id,
            'outreach',
        )
