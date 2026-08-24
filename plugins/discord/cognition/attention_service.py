"""Conservative activation scoring for attention allocation."""

from __future__ import annotations

import time


class AttentionService:
    def __init__(self, *, decay_rate: float = 0.05, profile_repository=None):
        self.decay_rate = decay_rate
        self.profile_repository = profile_repository
        self._scores: dict[tuple[str, str, str], float] = {}

    def _key(self, entity_type: str, entity_id: str, account_name: str) -> tuple[str, str, str]:
        return (entity_type, entity_id, account_name)

    def apply_signal(
        self,
        entity_type: str,
        entity_id: str,
        account_name: str,
        *,
        boost: float,
        reason: str = '',
        now: float | None = None,
    ) -> float:
        del reason
        key = self._key(entity_type, entity_id, account_name)
        current = self._scores.get(key, 0.0)
        score = min(1.0, current + boost)
        self._scores[key] = score
        if self.profile_repository:
            self.profile_repository.upsert_activation(entity_type, entity_id, account_name, score)
        return score

    def decay(self, account_name: str, *, now: float | None = None) -> None:
        del now
        factor = max(0.0, 1.0 - self.decay_rate)
        for key, score in list(self._scores.items()):
            if key[2] != account_name:
                continue
            self._scores[key] = score * factor
        if self.profile_repository:
            self.profile_repository.decay_activation(account_name, factor)

    def top_entities(self, account_name: str, *, limit: int = 10) -> list[dict]:
        items = [
            {'entity_type': key[0], 'entity_id': key[1], 'score': score}
            for key, score in self._scores.items()
            if key[2] == account_name
        ]
        items.sort(key=lambda item: item['score'], reverse=True)
        return items[:limit]

    def channel_activation(self, account_name: str, channel_id: str) -> float:
        return self._scores.get(self._key('channel', channel_id, account_name), 0.0)
