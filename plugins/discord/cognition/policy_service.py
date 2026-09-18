from __future__ import annotations

import time


class PolicyService:
    def __init__(self):
        self._last_reply_at: dict[tuple[str, str], float] = {}
        self._last_proactive_at: dict[tuple[str, str, str], float] = {}

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

    def evaluate_proactive_intention(self, intention, settings) -> dict:
        metadata = getattr(intention, 'metadata', None) or {}
        if metadata.get('task_id') or str(getattr(intention, 'reason', '')).startswith('task:'):
            return {'allowed': True, 'reason': 'scheduled_task'}
        action = intention.intention_type
        key = (intention.account_name, intention.channel_id, action)
        cooldown_hours = max(1, int(getattr(settings.safety, 'proactive_cooldown_hours', 6)))
        now = time.time()
        if now - self._last_proactive_at.get(key, 0) < cooldown_hours * 3600:
            return {'allowed': False, 'reason': 'proactive_cooldown'}
        self._last_proactive_at[key] = now
        return {'allowed': True, 'reason': 'allowed'}

    def evaluate_voice_speak(self, intention, settings) -> dict:
        voice = settings.voice
        if not voice.enabled:
            return {'allowed': False, 'reason': 'voice_disabled'}
        if not voice.speaking_enabled and intention.reason != 'explicit_command':
            return {'allowed': False, 'reason': 'speaking_disabled'}
        if voice.mode in {'listen_only', 'transcribe_only', 'summarize_only'} and intention.reason != 'explicit_command':
            return {'allowed': False, 'reason': f'mode_{voice.mode}'}
        return {'allowed': True, 'reason': 'allowed'}
