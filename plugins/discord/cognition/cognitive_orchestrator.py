"""Unified cognitive intention orchestration."""

from __future__ import annotations

import time

from plugins.discord.models.intentions import ReplyMessageIntention


class CognitiveOrchestrator:
    def __init__(
        self,
        *,
        intent_engine,
        world_state_builder,
        world_model_service=None,
        greeting_service=None,
        outreach_service=None,
        sleep_service=None,
        birthday_service=None,
        trace_service=None,
    ):
        self.intent_engine = intent_engine
        self.world_state_builder = world_state_builder
        self.world_model_service = world_model_service
        self.greeting_service = greeting_service
        self.outreach_service = outreach_service
        self.sleep_service = sleep_service
        self.birthday_service = birthday_service
        self.trace_service = trace_service

    def evaluate_message_batch(self, batch, settings) -> list[ReplyMessageIntention]:
        if not getattr(settings.cognitive, 'enabled', True):
            return self._direct_reply_intention(batch)
        trigger = batch.observations[-1]
        world_state = self.world_state_builder.from_observation(trigger)
        intentions = self.intent_engine.generate(world_state, settings=settings)
        if not intentions:
            if self.trace_service:
                self.trace_service.record_policy_rejection('intent_suppressed', {
                    'channel_id': trigger.channel_id,
                    'message_id': trigger.message_id,
                    'activation': world_state.get('activation'),
                    'mode': settings.cognitive.mode,
                })
            return []
        for intention in intentions:
            intention.prompt = trigger.clean_content
            intention.metadata = {
                **(intention.metadata or {}),
                'world_state': {
                    'activation': world_state.get('activation'),
                    'mentioned': world_state.get('mentioned'),
                },
            }
            if self.trace_service:
                self.trace_service.record_intention(intention.intention_type, {
                    'channel_id': intention.channel_id,
                    'reason': intention.reason,
                    'confidence': intention.confidence,
                    'mode': settings.cognitive.mode,
                })
        return intentions

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
            world_state = self.world_state_builder.from_task(task, account_name=account_name)
            generated = self.intent_engine.generate_task_follow_up(world_state, task, settings=settings)
            if generated:
                intentions.append(generated)
                if self.trace_service:
                    self.trace_service.record_intention(generated.intention_type, {
                        'channel_id': generated.channel_id,
                        'reason': generated.reason,
                        'task_id': task.get('id'),
                    })
        return intentions

    def complete_task(self, task_id: int) -> None:
        if self.world_model_service:
            self.world_model_service.task_repository.update_task_status(task_id, 'completed')

    def _direct_reply_intention(self, batch) -> list[ReplyMessageIntention]:
        trigger = batch.observations[-1]
        return [
            ReplyMessageIntention(
                intention_type='reply_message',
                account_name=trigger.account_name,
                channel_id=trigger.channel_id,
                message_id=trigger.message_id,
                reason='direct_path',
                prompt=trigger.clean_content,
                metadata={'batch_size': batch.message_count},
            )
        ]
