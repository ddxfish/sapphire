"""Conservative, explainable intention generation."""

from __future__ import annotations

from plugins.discord.models.intentions import ReplyMessageIntention

MODE_THRESHOLDS = {
    'conservative': 0.35,
    'integrated': 0.35,
    'expressive': 0.25,
}


class IntentEngine:
    REPLY_THRESHOLD = 0.35

    def __init__(self, *, goal_engine):
        self.goal_engine = goal_engine

    def generate(self, world_state: dict, *, settings=None) -> list[ReplyMessageIntention]:
        settings = settings
        activation = float(world_state.get('activation', 0.0))
        mentioned = bool(world_state.get('mentioned'))
        name_matched = bool(world_state.get('name_matched'))
        respond_trigger = bool(world_state.get('respond_trigger')) or mentioned or name_matched
        cognitive = getattr(settings, 'cognitive', None) if settings else None
        mode = getattr(cognitive, 'mode', 'integrated') if cognitive else 'integrated'
        is_dm = bool(world_state.get('is_dm'))

        if not respond_trigger and mode != 'expressive' and not is_dm:
            return []

        if respond_trigger:
            activation = max(activation, 0.8)
        threshold = self._effective_threshold(world_state, settings)
        if activation < threshold:
            return []

        goals = self.goal_engine.active_goals({**world_state, 'activation': activation, 'mentioned': respond_trigger})
        if not goals and not respond_trigger:
            return []

        confidence = min(1.0, activation)
        if mentioned:
            reason = 'mentioned'
        elif name_matched:
            reason = 'name_matched'
        else:
            reason = 'high_activation'
        return [
            ReplyMessageIntention(
                intention_type='reply_message',
                account_name=world_state.get('account_name', ''),
                channel_id=world_state.get('channel_id', ''),
                message_id=world_state.get('message_id', ''),
                reason=reason,
                confidence=confidence,
                urgency=confidence,
                metadata={'goals': [goal['name'] for goal in goals], 'activation': activation, 'threshold': threshold},
            )
        ]

    def generate_task_follow_up(self, world_state: dict, task: dict, *, settings=None) -> ReplyMessageIntention | None:
        if not getattr(getattr(settings, 'cognitive', None), 'task_follow_up_enabled', True):
            return None
        channel_id = task.get('target_id') or world_state.get('channel_id') or ''
        if not channel_id:
            return None
        task_type = task.get('task_type') or 'follow_up'
        payload = _task_payload(task)
        instruction = payload.get('instruction', '')
        prompts = {
            'voice_follow_up': 'Following up after the recent voice session.',
            'follow_up': 'Following up on an earlier topic.',
            'birthday_follow_up': instruction or 'Wish them a happy birthday.',
            'commitment_follow_up': instruction or 'Follow up on what they said they would do.',
            'reminder_follow_up': instruction or 'Deliver the reminder they asked for.',
        }
        prompt = prompts.get(task_type, instruction or 'Following up.')
        use_llm = task_type in {'birthday_follow_up', 'commitment_follow_up', 'reminder_follow_up'} and bool(instruction)
        metadata = {
            'task_id': task.get('id'),
            'task_type': task_type,
            'use_llm': use_llm,
        }
        if use_llm:
            metadata['event_payload'] = {
                'account': world_state.get('account_name', ''),
                'channel_id': channel_id,
                'message_id': f'task-followup-{task.get("id")}',
                'content': instruction,
                'username': payload.get('username', ''),
                'display_name': payload.get('display_name', ''),
                'author_id': payload.get('user_id', ''),
                'mentioned': 'true',
                'reply_to_message_id': '',
                'reply_instructions': instruction,
                'task_follow_up': 'true',
                'task_id': str(task.get('id') or ''),
                'reminder': payload.get('reminder', ''),
                'when_label': payload.get('when_label', ''),
            }
        return ReplyMessageIntention(
            intention_type='reply_message',
            account_name=world_state.get('account_name', ''),
            channel_id=channel_id,
            message_id='',
            reason=f'task:{task_type}',
            prompt=prompt,
            confidence=float(task.get('confidence') or 0.6),
            urgency=float(task.get('urgency') or 0.5),
            metadata=metadata,
        )

    def _effective_threshold(self, world_state: dict, settings) -> float:
        cognitive = getattr(settings, 'cognitive', None) if settings else None
        mode = getattr(cognitive, 'mode', 'integrated') if cognitive else 'integrated'
        base = MODE_THRESHOLDS.get(mode, self.REPLY_THRESHOLD)
        if cognitive and not getattr(cognitive, 'affect_modulation_enabled', True):
            return base
        relationship = world_state.get('relationship') or {}
        affect = world_state.get('affect') or {}
        fondness = float(relationship.get('fondness', 0.5))
        irritability = float(affect.get('irritability', 0.2))
        energy = float(affect.get('energy', 0.7))
        if fondness < 0.3:
            base += 0.1
        elif fondness > 0.7:
            base -= 0.05
        if irritability > 0.7:
            base += 0.1
        if energy < 0.25:
            base += 0.1
        return min(0.9, max(0.1, base))


def _task_payload(task: dict) -> dict:
    raw = task.get('payload_json')
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    import json
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}
