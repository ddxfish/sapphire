"""Proactive and task-follow-up intention orchestration."""

from __future__ import annotations

import json
import time

from plugins.discord.conversation.ignored_channels import is_channel_ignored
from plugins.discord.models.intentions import ReplyMessageIntention


def _task_payload(task: dict) -> dict:
    raw = task.get('payload_json')
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


class CognitiveOrchestrator:
    def __init__(
        self,
        *,
        world_model_service=None,
        greeting_service=None,
        outreach_service=None,
        sleep_service=None,
        birthday_service=None,
        trace_service=None,
        channel_situation_service=None,
    ):
        self.world_model_service = world_model_service
        self.greeting_service = greeting_service
        self.outreach_service = outreach_service
        self.sleep_service = sleep_service
        self.birthday_service = birthday_service
        self.trace_service = trace_service
        self.channel_situation_service = channel_situation_service

    def evaluate_proactive(self, account_name: str, settings, *, now, now_ts: float) -> list:
        intentions = []
        if self.greeting_service:
            intentions.extend(self.greeting_service.evaluate(account_name, settings, now=now))
        if self.outreach_service:
            intentions.extend(self.outreach_service.evaluate(account_name, settings, now=now, now_ts=now_ts))
        if self.sleep_service:
            intentions.extend(self.sleep_service.evaluate_goodnight(account_name, settings, now=now))
        if self.birthday_service:
            intentions.extend(self.birthday_service.evaluate_wishes(account_name, settings, now=now))
        if getattr(settings.cognitive, 'task_follow_up_enabled', True):
            intentions.extend(self.evaluate_task_intentions(account_name, settings))
        return intentions

    def evaluate_task_intentions(self, account_name: str, settings) -> list[ReplyMessageIntention]:
        if not self.world_model_service:
            return []
        tasks = self.world_model_service.list_due_tasks(account_name, now_ts=time.time(), limit=10)
        intentions = []
        for task in tasks:
            generated = self._generate_task_follow_up(account_name, task, settings=settings)
            if generated:
                intentions.append(generated)
                if self.trace_service:
                    self.trace_service.record_intention(generated.intention_type, {
                        'channel_id': generated.channel_id,
                        'reason': generated.reason,
                        'task_id': task.get('id'),
                    })
        return intentions

    def _generate_task_follow_up(self, account_name: str, task: dict, *, settings=None) -> ReplyMessageIntention | None:
        if not getattr(getattr(settings, 'cognitive', None), 'task_follow_up_enabled', True):
            return None
        channel_id = task.get('target_id') or ''
        if not channel_id:
            return None
        # A task whose target is an ignored channel never posts there (H6) —
        # greeting/outreach/sleep already check; this lane didn't.
        if settings and is_channel_ignored(account_name, channel_id, settings):
            return None
        task_type = task.get('task_type') or 'follow_up'
        # Explicit user reminders are a promise — deliver on time even if the room
        # looks busy/heated. Soft commitments / social check-ins still wait.
        bypass_situation = task_type in {'reminder_follow_up'}
        if (
            not bypass_situation
            and self.channel_situation_service
            and getattr(getattr(settings, 'cognitive', None), 'situation_enabled', True)
        ):
            situation = self.channel_situation_service.build(account_name, channel_id)
            allowed, reason = self.channel_situation_service.outreach_allowed(situation)
            if not allowed:
                if self.trace_service:
                    self.trace_service.record_policy_rejection(reason, {
                        'channel_id': channel_id,
                        'task_id': task.get('id'),
                        'task_type': task_type,
                        'vibe': getattr(situation, 'vibe', ''),
                    })
                return None
        payload = _task_payload(task)
        instruction = payload.get('instruction', '') or payload.get('prompt', '')
        when_label = str(payload.get('when_label') or '').strip()
        commitment = str(payload.get('commitment') or '').strip()
        display = str(payload.get('display_name') or payload.get('username') or 'them').strip()
        if task_type == 'commitment_follow_up' and not instruction:
            when_bit = f' ({when_label})' if when_label else ''
            instruction = (
                f"Earlier {display} mentioned{when_bit} they would: \"{commitment or 'something'}\". "
                f"@mention them and ask gently how it went — natural, not pushy."
            )
        prompts = {
            'voice_follow_up': 'Following up after the recent voice session.',
            'follow_up': 'Following up on an earlier topic.',
            'birthday_follow_up': instruction or 'Wish them a happy birthday.',
            'commitment_follow_up': instruction or 'Follow up on what they said they would do.',
            'reminder_follow_up': instruction or 'Deliver the reminder they asked for.',
            'social_check_in': instruction or 'Check in briefly if the channel is still calm.',
        }
        prompt = prompts.get(task_type, instruction or 'Following up.')
        use_llm = task_type in {
            'birthday_follow_up', 'commitment_follow_up', 'reminder_follow_up', 'social_check_in',
        } and bool(instruction)
        metadata = {
            'task_id': task.get('id'),
            'task_type': task_type,
            'use_llm': use_llm,
        }
        if use_llm:
            metadata['event_payload'] = {
                'account': account_name,
                'channel_id': channel_id,
                'message_id': f'task-followup-{task.get("id")}',
                'content': instruction,
                'username': payload.get('username', ''),
                'display_name': payload.get('display_name', ''),
                'author_id': payload.get('user_id', '') or payload.get('author_id', ''),
                'mentioned': 'true',
                'reply_to_message_id': '',
                'reply_instructions': instruction,
                'task_follow_up': 'true',
                'task_id': str(task.get('id') or ''),
                'reminder': payload.get('reminder', ''),
                'when_label': when_label,
                'commitment': commitment,
            }
        return ReplyMessageIntention(
            intention_type='reply_message',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason=f'task:{task_type}',
            prompt=prompt,
            confidence=float(task.get('confidence') or 0.6),
            urgency=float(task.get('urgency') or 0.5),
            metadata=metadata,
        )

    def complete_task(self, task_id: int) -> None:
        if self.world_model_service:
            self.world_model_service.task_repository.update_task_status(task_id, 'completed')
