"""Detect future-oriented statements and schedule world-model follow-up tasks."""

from __future__ import annotations

import json
from datetime import datetime

from plugins.discord.cognition.temporal_parse import (
    extract_commitment_run_at,
    extract_reminder_run_at,
    looks_like_reminder_request,
    passes_birthday_capture_gate,
    passes_commitment_gate,
)
from plugins.discord.lib.server_time import now_local


class CommitmentService:
    """Schedules world-model tasks for reminders and future commitments."""

    def __init__(self, *, world_model_service=None, trace_repository=None):
        self.world_model_service = world_model_service
        self.trace_repository = trace_repository

    def scan_and_schedule(self, observation, settings) -> tuple[list[int], list[str]]:
        cognitive = getattr(settings, 'cognitive', None)
        if not self.world_model_service or getattr(observation, 'author_id', '') == '':
            return [], []

        text = (observation.clean_content or '').strip()
        if not text:
            return [], []

        now = now_local()
        candidates = []
        reminder_enabled = cognitive is None or getattr(cognitive, 'reminder_followups_enabled', True)
        commitment_enabled = cognitive is None or getattr(cognitive, 'commitment_followups_enabled', True)

        if reminder_enabled and looks_like_reminder_request(text):
            reminder_cands = self._reminder_candidates(observation, text, now)
            if not reminder_cands and self.trace_repository:
                self.trace_repository.record_trace('reminder_parse_failed', 'Could not parse reminder time from message', {
                    'channel_id': observation.channel_id,
                    'author_id': observation.author_id,
                    'text': text[:300],
                })
            candidates.extend(reminder_cands)
        if commitment_enabled and not observation.is_dm and passes_commitment_gate(text):
            candidates.extend(self._commitment_candidates(observation, text, now))

        created: list[int] = []
        hints: list[str] = []
        for item in candidates:
            if self._has_similar_pending(
                observation.account_name,
                observation.author_id,
                item['task_type'],
                item['run_at'],
            ):
                continue
            task_id = self.world_model_service.create_task(
                observation.account_name,
                item['task_type'],
                target_id=observation.channel_id,
                reason=item['reason'],
                run_at=item['run_at'],
                payload=item['payload'],
            )
            self.world_model_service.record_scheduled_task(
                account_name=observation.account_name,
                channel_id=observation.channel_id,
                task_id=task_id,
                task_type=item['task_type'],
                run_at=item['run_at'],
                payload=item['payload'],
            )
            created.append(task_id)
            hints.extend(item.get('reply_hints') or [])
            if self.trace_repository:
                self.trace_repository.record_trace('commitment_scheduled', item['reason'], {
                    'task_id': task_id,
                    'task_type': item['task_type'],
                    'run_at': item['run_at'],
                    'channel_id': observation.channel_id,
                    'author_id': observation.author_id,
                })
        return created, hints

    def _reminder_reply_hint(self, reminder: str, when_label: str) -> str:
        return (
            f'You scheduled a reminder for {when_label}: "{reminder}". '
            'Briefly confirm in your reply that you will remind them. '
            'Do not say you cannot set reminders — one short sentence is enough.'
        )

    def _reminder_candidates(self, observation, text: str, now: datetime) -> list[dict]:
        parsed = extract_reminder_run_at(text, now)
        if not parsed:
            return []
        run_at_dt, reminder, when_label = parsed
        display = observation.display_name or observation.username or 'there'
        return [{
            'task_type': 'reminder_follow_up',
            'run_at': run_at_dt.timestamp(),
            'reason': 'user_reminder',
            'reply_hints': [self._reminder_reply_hint(reminder, when_label)],
            'payload': {
                'user_id': observation.author_id,
                'username': observation.username,
                'display_name': display,
                'quote': text[:500],
                'reminder': reminder,
                'when_label': when_label,
                'mention': f'<@{observation.author_id}>',
                'instruction': (
                    f"{display} asked to be reminded {when_label} about: \"{reminder}\". "
                    f"Send a brief friendly reminder that @mentions them. One sentence, clear and warm."
                ),
            },
        }]

    def _commitment_candidates(self, observation, text: str, now: datetime) -> list[dict]:
        if passes_birthday_capture_gate(text):
            return []
        parsed = extract_commitment_run_at(text, now)
        if not parsed:
            return []
        run_at_dt, commitment = parsed
        display = observation.display_name or observation.username or 'they'
        return [{
            'task_type': 'commitment_follow_up',
            'run_at': run_at_dt.timestamp(),
            'reason': 'future_commitment',
            'payload': {
                'user_id': observation.author_id,
                'username': observation.username,
                'display_name': display,
                'quote': text[:500],
                'commitment': commitment,
                'mention': f'<@{observation.author_id}>',
                'instruction': (
                    f"Earlier {display} said they would: \"{commitment}\". "
                    f"Write a friendly follow-up that @mentions them and asks how it went — "
                    f"curious, not pushy. One or two sentences."
                ),
            },
        }]

    def _has_similar_pending(self, account_name: str, user_id: str, task_type: str, run_at: float) -> bool:
        repo = self.world_model_service.task_repository
        pending = repo.list_tasks(account_name, status='pending', limit=50)
        window = 300 if task_type == 'reminder_follow_up' else 3600
        for row in pending:
            if row.get('task_type') != task_type:
                continue
            payload = _payload_dict(row.get('payload_json'))
            if str(payload.get('user_id', '')) != str(user_id):
                continue
            existing_run = float(row.get('run_at') or 0)
            if abs(existing_run - run_at) < window:
                return True
        return False


def _payload_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}
