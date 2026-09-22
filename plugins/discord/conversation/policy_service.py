from __future__ import annotations

import time


class PolicyService:
    def __init__(self):
        self._last_reply_at: dict[tuple[str, str], float] = {}

    def evaluate_text_observation(self, observation, resolved_settings=None) -> dict:
        resolved_settings = resolved_settings or None
        if getattr(observation, 'author_id', '') == '':
            return {'allowed': False, 'reason': 'missing_author'}
        key = (observation.account_name, observation.channel_id)
        cooldown = getattr(getattr(resolved_settings, 'safety', None), 'rate_limit_seconds', 0) if resolved_settings else 0
        now = time.time()
        if cooldown and now - self._last_reply_at.get(key, 0) < cooldown:
            return {'allowed': False, 'reason': 'cooldown'}
        self._last_reply_at[key] = now
        return {'allowed': True, 'reason': 'allowed'}
