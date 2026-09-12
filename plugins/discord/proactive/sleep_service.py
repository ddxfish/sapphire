"""Sleep schedule, mention buffering, and wake replay."""

from __future__ import annotations

from datetime import datetime

from plugins.discord.lib.server_time import now_local
from plugins.discord.models.intentions import GoodnightIntention, ReplyMessageIntention
from plugins.discord.proactive.targets import parse_target


class SleepService:
    GOODNIGHT_MINUTES = (0, 15, 30, 45)
    JUST_WOKEN_HINT = (
        "[You were asleep for the night but repeated @mentions woke you up. Reply helpfully, "
        "but briefly complain or grumble that people woke you — you're tired and will go back "
        "to sleep soon.]"
    )
    STILL_AWAKE_HINT = (
        '[You were woken up earlier and are still awake for a little while. '
        'You may grumble lightly, but answer the message.]'
    )

    def __init__(self, *, proactive_repository, trace_repository=None):
        self.proactive_repository = proactive_repository
        self.trace_repository = trace_repository

    def is_asleep(self, account_name: str, channel_id: str) -> bool:
        return bool(self.proactive_repository.get_sleep_state(account_name, channel_id).get('is_asleep'))

    def in_sleep_hours(self, settings, *, now: datetime | None = None) -> bool:
        proactive = settings.proactive
        if not proactive.sleep_schedule_enabled:
            return False
        now = now or now_local()
        sleep = int(proactive.sleep_utc_hour) % 24
        wake = int(proactive.greeting_utc_hour) % 24
        hour = now.hour
        if sleep == wake:
            return False
        if sleep < wake:
            return sleep <= hour < wake
        return hour >= sleep or hour < wake

    def account_sleep_state(self, account_name: str, settings, *, now_ts: float | None = None) -> tuple[bool, bool]:
        """Return (asleep, forced_wake) for an account across greeting target channels."""
        from time import time as _time

        asleep = False
        forced_wake = False
        now_ts = float(now_ts if now_ts is not None else _time())
        for entry in settings.proactive.greeting_targets or []:
            parsed = parse_target(entry)
            if not parsed or parsed[0] != account_name:
                continue
            state = self.proactive_repository.get_sleep_state(account_name, parsed[1])
            if state.get('is_asleep'):
                asleep = True
            if float(state.get('forced_wake_until', 0)) > now_ts:
                forced_wake = True
        if not asleep and self.in_sleep_hours(settings):
            asleep = True
        return asleep, forced_wake

    def voice_blocked_for_sleep(self, account_name: str, settings, *, now_ts: float | None = None) -> bool:
        asleep, forced_wake = self.account_sleep_state(account_name, settings, now_ts=now_ts)
        return asleep and not forced_wake

    def is_channel_dormant(self, account_name: str, channel_id: str, settings) -> bool:
        if self.is_asleep(account_name, channel_id):
            return True
        return bool(settings.proactive.sleep_schedule_enabled and self.in_sleep_hours(settings))

    def is_forced_awake(self, account_name: str, channel_id: str, *, now_ts: float | None = None) -> bool:
        from time import time as _time

        now_ts = float(now_ts if now_ts is not None else _time())
        state = self.proactive_repository.get_sleep_state(account_name, channel_id)
        return float(state.get('forced_wake_until', 0)) > now_ts

    def should_drop_observation(self, observation, settings) -> bool:
        """Drop non-mention traffic while the channel is dormant."""
        if not self.is_channel_dormant(observation.account_name, observation.channel_id, settings):
            return False
        return not bool(getattr(observation, 'mentioned', False))

    def evaluate_reply_gate(
        self,
        observation,
        settings,
        *,
        respond_trigger: bool,
        mentioned: bool,
        now_ts: float | None = None,
    ) -> dict:
        """Decide whether an incoming batch should receive a reply during sleep."""
        from time import time as _time

        now_ts = float(now_ts if now_ts is not None else _time())
        account_name = observation.account_name
        channel_id = observation.channel_id
        if not self.is_channel_dormant(account_name, channel_id, settings):
            return {'allow': True, 'reason': 'awake'}
        if not respond_trigger:
            return {'allow': False, 'reason': 'sleep_dormant'}
        if not mentioned:
            return {'allow': False, 'reason': 'sleep_mentions_only'}
        if self.is_forced_awake(account_name, channel_id, now_ts=now_ts):
            return {'allow': True, 'reason': 'forced_wake_active', 'hint': self.STILL_AWAKE_HINT}
        self.buffer_mention(
            account_name,
            channel_id,
            message_id=observation.message_id,
            author_id=observation.author_id,
            content=observation.clean_content,
            mentioned=True,
            settings=settings,
        )
        if self.check_forced_wake(account_name, channel_id, settings, now_ts=now_ts):
            return {'allow': True, 'reason': 'forced_wake_triggered', 'hint': self.JUST_WOKEN_HINT}
        return {'allow': False, 'reason': 'sleep_buffered_mention'}

    def set_asleep(self, account_name: str, channel_id: str) -> None:
        self.proactive_repository.set_sleep_state(account_name, channel_id, is_asleep=1)

    def wake_channel(self, account_name: str, channel_id: str) -> None:
        self.proactive_repository.set_sleep_state(
            account_name, channel_id, is_asleep=0, goodnight_sent=0, mention_count=0, forced_wake_until=0
        )

    def evaluate_goodnight(self, account_name: str, settings, *, now: datetime | None = None) -> list[GoodnightIntention]:
        proactive = settings.proactive
        if not proactive.sleep_schedule_enabled:
            return []
        now = now or now_local()
        if not self.in_sleep_hours(settings, now=now):
            return []
        if now.minute not in self.GOODNIGHT_MINUTES:
            return []
        intentions = []
        for entry in proactive.greeting_targets or []:
            parsed = parse_target(entry)
            if not parsed or parsed[0] != account_name:
                continue
            channel_id = parsed[1]
            state = self.proactive_repository.get_sleep_state(account_name, channel_id)
            if state.get('goodnight_sent'):
                continue
            prompt = ''
            intentions.append(GoodnightIntention(
                intention_type='goodnight',
                account_name=account_name,
                channel_id=channel_id,
                message_id='',
                reason='sleep_goodnight',
                prompt=prompt,
            ))
        return intentions

    def mark_goodnight_sent(self, intention: GoodnightIntention) -> None:
        self.proactive_repository.set_sleep_state(
            intention.account_name, intention.channel_id, is_asleep=1, goodnight_sent=1
        )

    def buffer_mention(
        self,
        account_name: str,
        channel_id: str,
        *,
        message_id: str,
        author_id: str,
        content: str,
        mentioned: bool,
        settings=None,
    ) -> None:
        if settings is not None:
            if not self.is_channel_dormant(account_name, channel_id, settings):
                return
        elif not self.is_asleep(account_name, channel_id):
            return
        self.proactive_repository.buffer_mention(
            account_name, channel_id, message_id=message_id, author_id=author_id, content=content, mentioned=mentioned
        )
        if mentioned:
            state = self.proactive_repository.get_sleep_state(account_name, channel_id)
            count = int(state.get('mention_count', 0)) + 1
            self.proactive_repository.set_sleep_state(account_name, channel_id, mention_count=count)

    def list_buffered(self, account_name: str, channel_id: str) -> list[dict]:
        return self.proactive_repository.list_buffered(account_name, channel_id)

    def drain_wake_buffer(self, account_name: str, channel_id: str, *, max_replies: int = 3) -> list[ReplyMessageIntention]:
        buffered = self.proactive_repository.list_buffered(account_name, channel_id, limit=max_replies)
        intentions = []
        ids = []
        for row in buffered:
            intentions.append(ReplyMessageIntention(
                intention_type='reply_message',
                account_name=account_name,
                channel_id=channel_id,
                message_id=row['message_id'],
                reason='wake_buffer_replay',
                prompt=row['content'],
                metadata={'buffered': True},
            ))
            ids.append(row['id'])
        self.proactive_repository.mark_buffered_processed(ids)
        self.wake_channel(account_name, channel_id)
        return intentions

    def check_forced_wake(self, account_name: str, channel_id: str, settings, *, now_ts: float) -> bool:
        proactive = settings.proactive
        state = self.proactive_repository.get_sleep_state(account_name, channel_id)
        threshold = max(1, int(proactive.forced_wake_mention_threshold))
        if int(state.get('mention_count', 0)) < threshold:
            return False
        wake_until = now_ts + max(60, int(proactive.forced_wake_minutes) * 60)
        self.proactive_repository.set_sleep_state(
            account_name, channel_id, is_asleep=0, forced_wake_until=wake_until, mention_count=0
        )
        return True
