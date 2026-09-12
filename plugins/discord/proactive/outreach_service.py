"""Quiet-channel outreach intention generation."""

from __future__ import annotations

from datetime import datetime

from plugins.discord.conversation.ignored_channels import is_channel_ignored
from plugins.discord.cognition.relationship_policy import (
    outreach_multiplier,
    relationship_policy_enabled,
    relationship_snapshot,
    relationship_strength,
)
from plugins.discord.lib.server_time import now_local
from plugins.discord.models.intentions import OutreachIntention
from plugins.discord.proactive.targets import parse_target


class OutreachService:
    def __init__(
        self,
        *,
        proactive_repository,
        channel_last_activity=None,
        trace_repository=None,
        message_repository=None,
        interest_service=None,
        channel_situation_service=None,
        profile_service=None,
        cognition_debug_service=None,
    ):
        self.proactive_repository = proactive_repository
        self.channel_last_activity = channel_last_activity or {}
        self.trace_repository = trace_repository
        self.message_repository = message_repository
        self.interest_service = interest_service
        self.channel_situation_service = channel_situation_service
        self.profile_service = profile_service
        self.cognition_debug_service = cognition_debug_service

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

    def _interest_context(self, account_name: str, channel_id: str) -> tuple[list[dict], str]:
        if not self.interest_service or not self.message_repository:
            return [], ''
        rows = self.message_repository.get_recent_messages(account_name, channel_id, limit=30)
        user_ids = []
        seen = set()
        for row in rows:
            uid = str(row.get('author_id') or '')
            if uid and uid not in seen:
                seen.add(uid)
                user_ids.append(uid)
        topics = self.interest_service.channel_topics(account_name, user_ids, limit=6)
        hint = self.interest_service.outreach_hint(topics)
        return topics, hint

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
            if is_channel_ignored(account_name, channel_id, settings):
                continue
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
            cognitive = getattr(settings, 'cognitive', None)
            if (
                self.channel_situation_service
                and cognitive is not None
                and getattr(cognitive, 'situation_enabled', True)
            ):
                situation = self.channel_situation_service.build(account_name, channel_id)
                allowed, reason = self.channel_situation_service.outreach_allowed(situation)
                if not allowed:
                    if self.trace_repository:
                        self.trace_repository.record_trace(
                            'outreach_skipped',
                            f'Outreach skipped: {reason}',
                            {
                                'channel_id': channel_id,
                                'reason': reason,
                                'vibe': situation.vibe,
                                'silence_seconds': situation.silence_seconds,
                            },
                        )
                    if self.cognition_debug_service:
                        self.cognition_debug_service.record_gate(
                            gate='outreach_skipped',
                            account_name=account_name,
                            channel_id=channel_id,
                            detail={
                                'reason': reason,
                                'vibe': situation.vibe,
                                'silence_seconds': situation.silence_seconds,
                            },
                        )
                    continue
            topics, interest_hint = self._interest_context(account_name, channel_id)
            prompt = 'Checking in — anything going on?'
            if interest_hint:
                prompt = interest_hint
            confidence = 0.6
            if self.profile_service and relationship_policy_enabled(settings):
                # Soft-weight by average familiarity of recent channel voices.
                rows = []
                if self.message_repository:
                    rows = self.message_repository.get_recent_messages(account_name, channel_id, limit=20)
                fams = []
                for row in rows[:8]:
                    uid = str(row.get('author_id') or '')
                    if not uid:
                        continue
                    profile = self.profile_service.profile_repository.get_or_create_profile(
                        account_name, uid,
                    )
                    fams.append(relationship_snapshot(profile))
                if fams:
                    avg = {
                        'familiarity': sum(s['familiarity'] for s in fams) / len(fams),
                        'fondness': sum(s['fondness'] for s in fams) / len(fams),
                    }
                    mult = outreach_multiplier(avg, strength=relationship_strength(settings))
                    confidence = max(0.2, min(0.95, confidence * mult))
                    if mult < 0.55:
                        if self.trace_repository:
                            self.trace_repository.record_trace(
                                'outreach_skipped',
                                'Outreach skipped: low relationship warmth',
                                {'channel_id': channel_id, 'multiplier': mult},
                            )
                        continue
            intentions.append(OutreachIntention(
                intention_type='outreach',
                account_name=account_name,
                channel_id=channel_id,
                message_id='',
                reason='quiet_outreach',
                prompt=prompt,
                confidence=confidence,
                metadata={
                    'stale_seconds': stale_seconds,
                    'interest_topics': [str(t.get('topic') or '') for t in topics if t.get('topic')],
                    'interest_hint': interest_hint,
                },
            ))
        return intentions

    def mark_sent(self, intention: OutreachIntention) -> None:
        self.proactive_repository.record_cooldown(
            intention.account_name,
            intention.channel_id,
            'outreach',
        )
