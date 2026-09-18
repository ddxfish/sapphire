"""Morning greeting intention generation."""

from __future__ import annotations

from datetime import datetime

from plugins.discord.conversation.ignored_channels import is_channel_ignored
from plugins.discord.lib.server_time import now_local, user_hour
from plugins.discord.models.intentions import GreetChannelIntention
from plugins.discord.proactive.targets import parse_target

# One greeting per channel per day, DURABLY: the proactive_cooldowns row was
# written on every greeting and read by nobody, so dedupe rested on an
# in-memory dict a restart wiped (hunt 2.13.0, row 21).
GREETING_MIN_SECONDS = 20 * 3600


class GreetingService:
    def __init__(self, *, proactive_repository, trace_repository=None, sleep_service=None):
        self.proactive_repository = proactive_repository
        self.trace_repository = trace_repository
        self.sleep_service = sleep_service

    def evaluate(self, account_name: str, settings, *, now: datetime | None = None,
                 wake: bool = True) -> list[GreetChannelIntention]:
        """wake=False = read-only (diagnostics): opening the Proactive tab in the
        greeting hour used to wake every channel through this call (row 17)."""
        proactive = settings.proactive
        if not proactive.greeting_enabled:
            return []
        now = now or now_local()
        if user_hour(now) != int(proactive.greeting_utc_hour) % 24:
            return []
        intentions = []
        for entry in proactive.greeting_targets or []:
            parsed = parse_target(entry)
            if not parsed or parsed[0] != account_name:
                continue
            channel_id = parsed[1]
            if is_channel_ignored(account_name, channel_id, settings):
                continue
            if not self.proactive_repository.cooldown_elapsed(
                account_name, channel_id, 'greeting', min_seconds=GREETING_MIN_SECONDS, now=now.timestamp(),
            ):
                continue
            if wake and self.sleep_service:
                self.sleep_service.wake_channel(account_name, channel_id)
            elif wake and self.proactive_repository.get_sleep_state(account_name, channel_id).get('is_asleep'):
                self.proactive_repository.set_sleep_state(account_name, channel_id, is_asleep=0, goodnight_sent=0)
            prompt = ''
            intentions.append(GreetChannelIntention(
                intention_type='greet_channel',
                account_name=account_name,
                channel_id=channel_id,
                message_id='',
                reason='morning_greeting',
                prompt=prompt,
                metadata={'local_hour': user_hour(now)},
            ))
        return intentions

    def mark_sent(self, intention: GreetChannelIntention) -> None:
        self.proactive_repository.record_cooldown(intention.account_name, intention.channel_id, 'greeting')
