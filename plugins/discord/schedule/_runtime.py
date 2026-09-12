"""Shared helpers for Sapphire continuity schedule handlers."""

from __future__ import annotations


def reload_settings(runtime):
    if not runtime:
        return None
    if getattr(runtime, 'channel_repository', None):
        runtime.settings_store = runtime.channel_repository.load_settings_store()
    return runtime.settings_store.resolve() if runtime.settings_store else None


def connected_accounts(runtime) -> list[str]:
    if not runtime or not runtime.transport:
        return []
    accounts = runtime.transport.list_connected()
    if accounts:
        return sorted(accounts)
    if runtime.scheduler_bridge:
        return sorted(runtime.scheduler_bridge.active_daemon_accounts('proactive'))
    return []


def execute_proactive(runtime, intention, settings) -> dict:
    affect = {}
    if runtime.profile_service:
        affect = runtime.profile_service.get_affect(intention.account_name).to_dict()
    decision = runtime.policy_service.evaluate_proactive_intention(intention, settings, affect=affect)
    if not decision.get('allowed'):
        if runtime.trace_repository:
            runtime.trace_repository.record_trace('proactive_skipped', decision.get('reason', 'blocked'), {
                'intention_type': intention.intention_type,
                'channel_id': intention.channel_id,
                'reason': intention.reason,
            })
        return {'status': 'skipped', 'reason': decision.get('reason', 'blocked')}
    return runtime.proactive_executor.execute(intention)
