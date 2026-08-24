"""Capture birthdays on user profiles and schedule daily birthday wishes."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from plugins.discord.cognition.temporal_parse import extract_birthday_date, passes_birthday_capture_gate
from plugins.discord.lib.server_time import now_local
from plugins.discord.models.intentions import BirthdayWishIntention


class BirthdayService:
    def __init__(self, *, profile_repository=None, trace_repository=None):
        self.profile_repository = profile_repository
        self.trace_repository = trace_repository

    def try_capture_from_observation(self, observation, settings) -> list[str]:
        profile_settings = getattr(settings, 'profile', None)
        if not self.profile_repository or profile_settings is None:
            return []
        if not getattr(profile_settings, 'enabled', True):
            return []
        if not getattr(profile_settings, 'birthday_capture_enabled', True):
            return []
        if getattr(observation, 'author_id', '') == '':
            return []

        text = (getattr(observation, 'clean_content', '') or '').strip()
        if not text or not passes_birthday_capture_gate(text):
            return []

        now = now_local()
        parsed = extract_birthday_date(text, now)
        if not parsed:
            if self.trace_repository:
                self.trace_repository.record_trace('birthday_capture_failed', 'Could not parse birthday date', {
                    'channel_id': observation.channel_id,
                    'author_id': observation.author_id,
                    'text': text[:300],
                })
            return []

        month, day, when_label = parsed
        display = observation.display_name or observation.username or 'there'
        self.profile_repository.set_birthday(
            observation.account_name,
            observation.author_id,
            month=month,
            day=day,
            channel_id=observation.channel_id,
            username=observation.username,
            display_name=display,
        )
        self.profile_repository.add_fact(
            observation.account_name,
            observation.author_id,
            f'Birthday: {month:02d}-{day:02d} ({when_label})',
            source='birthday_capture',
            confidence=1.0,
        )
        if self.trace_repository:
            self.trace_repository.record_trace('birthday_captured', 'Stored birthday on profile', {
                'user_id': observation.author_id,
                'channel_id': observation.channel_id,
                'birthday_month': month,
                'birthday_day': day,
                'when_label': when_label,
            })
        return [self._capture_reply_hint(when_label, month, day)]

    def evaluate_wishes(self, account_name: str, settings, *, now: datetime | None = None) -> list[BirthdayWishIntention]:
        profile_settings = getattr(settings, 'profile', None)
        proactive = getattr(settings, 'proactive', None)
        if not self.profile_repository or profile_settings is None or proactive is None:
            return []
        if not getattr(profile_settings, 'enabled', True):
            return []
        if not getattr(profile_settings, 'birthday_followups_enabled', True):
            return []

        now = now or now_local()
        profiles = self.profile_repository.list_birthdays_on_date(account_name, now.month, now.day)
        pending = self._pending_recipients(profiles, now)
        if not pending:
            return []

        window_start, window_end = self._spread_window(settings, now)
        bulk_enabled = bool(getattr(profile_settings, 'birthday_bulk_enabled', True))
        bulk_threshold = max(1, int(getattr(profile_settings, 'birthday_bulk_threshold', 3) or 3))

        by_channel: dict[str, list[dict]] = {}
        for row in pending:
            by_channel.setdefault(row['channel_id'], []).append(row)

        intentions: list[BirthdayWishIntention] = []
        for channel_id, rows in by_channel.items():
            if bulk_enabled and len(rows) > bulk_threshold:
                intention = self._bulk_intention_if_due(
                    account_name,
                    channel_id,
                    rows,
                    now=now,
                    window_start=window_start,
                    window_end=window_end,
                )
                if intention:
                    intentions.append(intention)
            else:
                intentions.extend(
                    self._individual_intentions_if_due(
                        account_name,
                        rows,
                        now=now,
                        window_start=window_start,
                        window_end=window_end,
                    )
                )
        return intentions

    def mark_wished(self, intention: BirthdayWishIntention) -> None:
        if not self.profile_repository:
            return
        year = now_local().year
        metadata = intention.metadata or {}
        if metadata.get('bulk'):
            for recipient in metadata.get('recipients') or []:
                user_id = str(recipient.get('user_id') or '').strip()
                if user_id:
                    self.profile_repository.mark_birthday_wished(intention.account_name, user_id, year)
            return
        if intention.user_id:
            self.profile_repository.mark_birthday_wished(intention.account_name, intention.user_id, year)

    def _pending_recipients(self, profiles: list[dict], now: datetime) -> list[dict]:
        pending: list[dict] = []
        for row in profiles:
            if int(row.get('last_birthday_wish_year') or 0) == now.year:
                continue
            channel_id = str(row.get('birthday_channel_id') or '').strip()
            user_id = str(row.get('user_id') or '').strip()
            if not channel_id or not user_id:
                continue
            display = str(row.get('birthday_display_name') or row.get('birthday_username') or 'there').strip()
            pending.append({
                'channel_id': channel_id,
                'user_id': user_id,
                'display_name': display,
                'mention': f'<@{user_id}>',
                'birthday_month': int(row.get('birthday_month') or 0),
                'birthday_day': int(row.get('birthday_day') or 0),
                'birthday_wish_run_at': float(row.get('birthday_wish_run_at') or 0),
            })
        return pending

    def _individual_intentions_if_due(
        self,
        account_name: str,
        rows: list[dict],
        *,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> list[BirthdayWishIntention]:
        now_ts = now.timestamp()
        intentions: list[BirthdayWishIntention] = []
        for row in rows:
            run_at = row['birthday_wish_run_at']
            if not self._run_at_is_today(run_at, now):
                run_at = self._wish_run_at_for_user(row['user_id'], now, window_start, window_end)
                self.profile_repository.set_birthday_wish_run_at(account_name, row['user_id'], run_at)
                self._trace_scheduled(row['user_id'], run_at, window_start, window_end, bulk=False)
            if now_ts < run_at:
                continue
            intentions.append(self._individual_intention(account_name, row, run_at))
        return intentions

    def _bulk_intention_if_due(
        self,
        account_name: str,
        channel_id: str,
        rows: list[dict],
        *,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> BirthdayWishIntention | None:
        now_ts = now.timestamp()
        sample_run_at = rows[0]['birthday_wish_run_at']
        if not self._run_at_is_today(sample_run_at, now):
            run_at = self._wish_run_at_for_bulk(account_name, channel_id, now, window_start, window_end)
            for row in rows:
                self.profile_repository.set_birthday_wish_run_at(account_name, row['user_id'], run_at)
            self._trace_scheduled(
                f'bulk:{channel_id}',
                run_at,
                window_start,
                window_end,
                bulk=True,
                recipient_count=len(rows),
            )
        else:
            run_at = sample_run_at
        if now_ts < run_at:
            return None

        recipients = [
            {
                'user_id': row['user_id'],
                'display_name': row['display_name'],
                'mention': row['mention'],
            }
            for row in rows
        ]
        return BirthdayWishIntention(
            intention_type='birthday_wish',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='profile_birthday_bulk',
            user_id=recipients[0]['user_id'],
            metadata={
                'bulk': True,
                'recipients': recipients,
                'birthday_month': rows[0]['birthday_month'],
                'birthday_day': rows[0]['birthday_day'],
                'scheduled_run_at': run_at,
            },
        )

    def _individual_intention(self, account_name: str, row: dict, run_at: float) -> BirthdayWishIntention:
        return BirthdayWishIntention(
            intention_type='birthday_wish',
            account_name=account_name,
            channel_id=row['channel_id'],
            message_id='',
            reason='profile_birthday',
            user_id=row['user_id'],
            metadata={
                'display_name': row['display_name'],
                'mention': row['mention'],
                'birthday_month': row['birthday_month'],
                'birthday_day': row['birthday_day'],
                'scheduled_run_at': run_at,
            },
        )

    def _trace_scheduled(
        self,
        subject: str,
        run_at: float,
        window_start: datetime,
        window_end: datetime,
        *,
        bulk: bool,
        recipient_count: int = 1,
    ) -> None:
        if not self.trace_repository:
            return
        self.trace_repository.record_trace('birthday_wish_scheduled', 'Scheduled birthday wish', {
            'subject': subject,
            'run_at': run_at,
            'bulk': bulk,
            'recipient_count': recipient_count,
            'window_start': window_start.isoformat(),
            'window_end': window_end.isoformat(),
        })

    def _spread_window(self, settings, now: datetime) -> tuple[datetime, datetime]:
        proactive = settings.proactive
        start_hour = int(proactive.greeting_utc_hour) % 24
        end_hour = int(getattr(proactive, 'birthday_wish_spread_end_hour', 20) or 20) % 24
        start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        if end <= start:
            end = start + timedelta(hours=max(2, (end_hour - start_hour) % 24 or 10))
        if (end - start).total_seconds() < 3600:
            end = start + timedelta(hours=2)
        return start, end

    def _wish_run_at_for_user(
        self,
        user_id: str,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        span = max(0.0, (window_end - window_start).total_seconds())
        if span <= 0:
            return window_start.timestamp()
        seed = f'{user_id}:{now.year:04d}:{now.month:02d}:{now.day:02d}'
        ratio = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return window_start.timestamp() + ratio * span

    def _wish_run_at_for_bulk(
        self,
        account_name: str,
        channel_id: str,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        span = max(0.0, (window_end - window_start).total_seconds())
        if span <= 0:
            return window_start.timestamp()
        seed = f'bulk:{account_name}:{channel_id}:{now.year:04d}:{now.month:02d}:{now.day:02d}'
        ratio = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return window_start.timestamp() + ratio * span

    def _run_at_is_today(self, run_at: float, now: datetime) -> bool:
        if run_at <= 0:
            return False
        scheduled = datetime.fromtimestamp(run_at)
        return scheduled.date() == now.date()

    def _capture_reply_hint(self, when_label: str, month: int, day: int) -> str:
        return (
            f'You learned their birthday is {when_label} ({month:02d}-{day:02d}). '
            'Briefly acknowledge you will remember — one short sentence.'
        )
