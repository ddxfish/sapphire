from __future__ import annotations

from dataclasses import dataclass, field

from plugins.discord.models.observations import TextMessageObservation, TypingObservation


@dataclass
class ChannelBatch:
    account_name: str
    guild_id: str
    guild_name: str
    channel_id: str
    channel_name: str
    is_dm: bool
    observations: list[TextMessageObservation] = field(default_factory=list)
    flush_at: float = 0.0
    typing_extended: bool = False
    urgency: bool = False

    @property
    def message_count(self) -> int:
        return len(self.observations)

    @property
    def message_ids(self) -> list[str]:
        return [item.message_id for item in self.observations]


class BatchingService:
    def __init__(self, *, default_window_seconds: float = 8.0, typing_extension_seconds: float = 4.0):
        self.default_window_seconds = default_window_seconds
        self.typing_extension_seconds = typing_extension_seconds
        self._batches: dict[tuple[str, str], ChannelBatch] = {}

    def add_message(self, observation: TextMessageObservation) -> ChannelBatch:
        key = (observation.account_name, observation.channel_id)
        batch = self._batches.get(key)
        if batch is None:
            batch = ChannelBatch(
                account_name=observation.account_name,
                guild_id=observation.guild_id,
                guild_name=observation.guild_name,
                channel_id=observation.channel_id,
                channel_name=observation.channel_name,
                is_dm=observation.is_dm,
            )
            self._batches[key] = batch
        batch.observations.append(observation)
        urgency = observation.mentioned or observation.clean_content.strip().endswith('?')
        batch.urgency = batch.urgency or urgency
        delay = self.default_window_seconds * (0.5 if urgency else 1.0)
        batch.flush_at = observation.created_at + delay
        return batch

    def record_typing(self, observation: TypingObservation) -> None:
        key = (observation.account_name, observation.channel_id)
        batch = self._batches.get(key)
        if not batch:
            return
        batch.typing_extended = True
        batch.flush_at = max(batch.flush_at, observation.created_at + self.typing_extension_seconds)

    def flush_ready(self, *, now: float) -> list[ChannelBatch]:
        ready = []
        for key, batch in list(self._batches.items()):
            if batch.flush_at <= now:
                ready.append(batch)
                self._batches.pop(key, None)
        ready.sort(key=lambda item: item.flush_at)
        return ready
