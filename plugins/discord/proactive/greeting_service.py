"""Morning greeting intention generation."""

from __future__ import annotations

from datetime import datetime

from plugins.discord.lib.server_time import now_local
from plugins.discord.models.intentions import GreetChannelIntention
from plugins.discord.proactive.targets import parse_target


class GreetingService:
    def __init__(self, *, proactive_repository, trace_repository=None, sleep_service=None):
        self.proactive_repository = proactive_repository
        self.trace_repository = trace_repository
        self.sleep_service = sleep_service

    def evaluate(self, account_name: str, settings, *, now: datetime | None = None) -> list[GreetChannelIntention]:
        proactive = settings.proactive
        if not proactive.greeting_enabled:
            return []
        now = now or now_local()
        if now.hour != int(proactive.greeting_utc_hour) % 24:
            return []
        intentions = []
        for entry in proactive.greeting_targets or []:
            parsed = parse_target(entry)
            if not parsed or parsed[0] != account_name:
                continue
            channel_id = parsed[1]
            if self.sleep_service:
                self.sleep_service.wake_channel(account_name, channel_id)
            elif self.proactive_repository.get_sleep_state(account_name, channel_id).get('is_asleep'):
                self.proactive_repository.set_sleep_state(account_name, channel_id, is_asleep=0, goodnight_sent=0)
            prompt = ''
            intentions.append(GreetChannelIntention(
                intention_type='greet_channel',
                account_name=account_name,
                channel_id=channel_id,
                message_id='',
                reason='morning_greeting',
                prompt=prompt,
                metadata={'local_hour': now.hour},
            ))
        return intentions

    def mark_sent(self, intention: GreetChannelIntention) -> None:
        self.proactive_repository.record_cooldown(intention.account_name, intention.channel_id, 'greeting')
