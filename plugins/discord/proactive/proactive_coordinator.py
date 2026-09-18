"""Proactive evaluation coordinator for scheduler ticks."""

from __future__ import annotations

from datetime import datetime

from plugins.discord.lib.server_time import now_local, user_hour
from plugins.discord.models.intentions import ReplyMessageIntention, UpdatePresenceIntention
from plugins.discord.proactive.targets import parse_target


class ProactiveCoordinator:
    def __init__(self, *, settings_store, greeting_service, outreach_service, sleep_service, presence_service, profile_service, proactive_executor, policy_service, cognitive_orchestrator=None, transport=None, trace_repository=None):
        self.settings_store = settings_store
        self.greeting_service = greeting_service
        self.outreach_service = outreach_service
        self.sleep_service = sleep_service
        self.presence_service = presence_service
        self.profile_service = profile_service
        self.proactive_executor = proactive_executor
        self.policy_service = policy_service
        self.cognitive_orchestrator = cognitive_orchestrator
        self.transport = transport
        self.trace_repository = trace_repository

    def tick(self, account_name: str) -> list[dict]:
        settings = self.settings_store.resolve() if self.settings_store else None
        if not settings:
            return []
        now = now_local()
        results = []
        if self.cognitive_orchestrator:
            intentions = self.cognitive_orchestrator.evaluate_proactive(account_name, settings, now=now, now_ts=now.timestamp())
        else:
            intentions = []
            intentions.extend(self.greeting_service.evaluate(account_name, settings, now=now))
            intentions.extend(self.outreach_service.evaluate(account_name, settings, now=now, now_ts=now.timestamp()))
            intentions.extend(self.sleep_service.evaluate_goodnight(account_name, settings, now=now))
        for intention in intentions:
            decision = self.policy_service.evaluate_proactive_intention(intention, settings)
            if not decision.get('allowed'):
                if self.trace_repository:
                    self.trace_repository.record_trace('proactive_skipped', decision.get('reason', 'blocked'), {'intention_type': intention.intention_type, 'channel_id': intention.channel_id})
                continue
            result = self.proactive_executor.execute(intention)
            if isinstance(intention, ReplyMessageIntention) and intention.metadata.get('task_id') and result.get('status') == 'sent' and self.cognitive_orchestrator:
                self.cognitive_orchestrator.complete_task(intention.metadata['task_id'])
            results.append(result)
        self._maybe_update_presence(account_name, settings, now)
        return results

    async def tick_async(self, account_name: str) -> list[dict]:
        settings = self.settings_store.resolve() if self.settings_store else None
        if not settings:
            return []
        now = now_local()
        results = []
        if self.cognitive_orchestrator:
            intentions = self.cognitive_orchestrator.evaluate_proactive(account_name, settings, now=now, now_ts=now.timestamp())
        else:
            intentions = []
            intentions.extend(self.greeting_service.evaluate(account_name, settings, now=now))
            intentions.extend(self.outreach_service.evaluate(account_name, settings, now=now, now_ts=now.timestamp()))
            intentions.extend(self.sleep_service.evaluate_goodnight(account_name, settings, now=now))
        for intention in intentions:
            decision = self.policy_service.evaluate_proactive_intention(intention, settings)
            if not decision.get('allowed'):
                if self.trace_repository:
                    self.trace_repository.record_trace('proactive_skipped', decision.get('reason', 'blocked'), {'intention_type': intention.intention_type, 'channel_id': intention.channel_id})
                continue
            if hasattr(self.proactive_executor, 'execute_async'):
                result = await self.proactive_executor.execute_async(intention)
            else:
                result = self.proactive_executor.execute(intention)
            if isinstance(intention, ReplyMessageIntention) and intention.metadata.get('task_id') and result.get('status') == 'sent' and self.cognitive_orchestrator:
                self.cognitive_orchestrator.complete_task(intention.metadata['task_id'])
            results.append(result)
        await self._maybe_update_presence_async(account_name, settings, now)
        return results

    def _sleep_state_for_presence(self, account_name: str, settings, now: datetime) -> tuple[bool, bool]:
        if not self.sleep_service:
            return (False, False)
        return self.sleep_service.account_sleep_state(account_name, settings, now_ts=now.timestamp())

    def _maybe_update_presence(self, account_name: str, settings, now: datetime) -> None:
        intention = self._presence_intention_if_due(account_name, settings, now)
        if intention:
            self.proactive_executor.execute(intention)

    async def _maybe_update_presence_async(self, account_name: str, settings, now: datetime) -> None:
        intention = self._presence_intention_if_due(account_name, settings, now)
        if not intention:
            return
        if hasattr(self.proactive_executor, 'execute_presence_async'):
            await self.proactive_executor.execute_presence_async(intention)
        else:
            self.proactive_executor.execute(intention)

    async def apply_presence_now_async(self, account_name: str, *, force: bool = True) -> dict | None:
        settings = self.settings_store.resolve() if self.settings_store else None
        if not settings:
            return None
        intention = self._presence_intention_if_due(account_name, settings, now_local(), force=force)
        if not intention:
            return None
        if hasattr(self.proactive_executor, 'execute_presence_async'):
            return await self.proactive_executor.execute_presence_async(intention)
        return self.proactive_executor.execute(intention)

    def _presence_intention_if_due(
        self,
        account_name: str,
        settings,
        now: datetime,
        *,
        force: bool = False,
    ) -> UpdatePresenceIntention | None:
        if not self.presence_service:
            return None
        asleep, forced_wake = self._sleep_state_for_presence(account_name, settings, now)
        situation = None
        if (
            getattr(settings.presence, 'situation_presence_enabled', False)
            and self.cognitive_orchestrator
            and getattr(self.cognitive_orchestrator, 'channel_situation_service', None)
        ):
            situation_service = self.cognitive_orchestrator.channel_situation_service
            targets = list(getattr(settings.proactive, 'greeting_targets', None) or [])
            for entry in targets:
                parsed = parse_target(entry)      # the ONE parser (row 43)
                if parsed and parsed[0] == account_name and parsed[1]:
                    situation = situation_service.build(account_name, parsed[1])
                    break
        choice = self.presence_service.select_presence(
            settings,
            asleep=asleep,
            forced_wake=forced_wake,
            local_hour=user_hour(now),
            situation=situation,
        )
        mode = choice.get('mode', 'awake')
        presence = settings.presence
        interval = float(presence.cycle_interval_seconds or 300)
        if not self.presence_service.should_update(account_name, mode=mode, interval_seconds=interval, force=force):
            return None
        metadata = {'mode': mode}
        if choice.get('situation_vibe'):
            metadata['situation_vibe'] = choice['situation_vibe']
        return UpdatePresenceIntention(
            intention_type='update_presence',
            account_name=account_name,
            channel_id='',
            message_id='',
            reason='scheduler_presence',
            status=choice['status'],
            activity=choice['activity'],
            metadata=metadata,
        )
