"""Resolve Discord plugin LLM settings for events and direct calls."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def cognitive_llm_from_settings(settings) -> tuple[str, str]:
    """Return (llm_primary, llm_model) from resolved EffectiveSettings."""
    cognitive = getattr(settings, 'cognitive', None)
    if cognitive is None:
        return 'auto', ''
    primary = str(getattr(cognitive, 'llm_primary', 'auto') or 'auto').strip() or 'auto'
    model = str(getattr(cognitive, 'llm_model', '') or '').strip()
    return primary, model


def llm_event_fields(settings) -> dict[str, str]:
    """Fields to merge into discord_message event payloads."""
    primary, model = cognitive_llm_from_settings(settings)
    if primary in ('', 'auto'):
        return {}
    fields = {'llm_primary': primary}
    if model:
        fields['llm_model'] = model
    return fields


def _providers_config() -> dict[str, Any]:
    import config

    return {
        **(getattr(config, 'LLM_PROVIDERS', None) or {}),
        **(getattr(config, 'LLM_CUSTOM_PROVIDERS', None) or {}),
    }


def distill_llm_from_settings(settings) -> tuple[str, str]:
    """Resolve ambient-distill LLM, inheriting from Reply LLM when unset."""
    profile = getattr(settings, 'profile', None)
    cognitive_primary, cognitive_model = cognitive_llm_from_settings(settings)
    if profile is None:
        return cognitive_primary, cognitive_model
    provider = str(getattr(profile, 'distill_model_provider', '') or '').strip()
    model = str(getattr(profile, 'distill_model_name', '') or '').strip()
    return (provider, model) if provider else (cognitive_primary, cognitive_model)    # pair (row 28)


def side_lanes_local_only() -> bool:
    """cognitive.side_lanes_local_only, read live from core's plugin settings
    (M6). Unreadable → True: the safe direction for other people's chatter."""
    try:
        from core.plugin_loader import plugin_loader
        if not plugin_loader.get_plugin_info('discord'):
            return True
        value = (plugin_loader.get_plugin_settings('discord') or {}).get('cognitive.side_lanes_local_only', True)
    except Exception:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


def resolve_discord_llm_provider(system, provider_key: str, model_name: str = '', *, local_only: bool | None = None):
    """Return (provider_key, provider, gen_params) for plugin-configured LLM calls.

    Thin door onto core.chat.llm_providers.resolve — the ONE resolver
    (2026-09-21). local_only (default: the cognitive.side_lanes_local_only
    setting) narrows the 'auto' scan to providers marked local; an explicit
    pick always wins (Krem's ruling 2026-09-21). Every refusal — 'none', a
    dead pin, no local provider — is logged by name (loud), never a silent
    None with no reason. This never reads the operator's active WEB chat:
    a Discord voice reply in auto mode used to run on whatever provider the
    browser tab had selected (2026-08-06)."""
    llm = getattr(system, 'llm_chat', None)
    if llm is None:
        return None, None, None

    from core.chat.llm_providers import get_generation_params
    from core.chat.llm_providers.resolve import resolve, ProviderRefused

    primary = str(provider_key or 'auto').strip() or 'auto'
    model = str(model_name or '').strip()
    if local_only is None:
        local_only = side_lanes_local_only()
    try:
        sel = resolve(primary, model, private=bool(local_only) if primary == 'auto' else False)
    except ProviderRefused as exc:
        logger.warning('Discord LLM (%s%s): %s', primary,
                       ' local-only' if primary == 'auto' and local_only else '', exc)
        return None, None, None
    providers_config = _providers_config()
    gen_params = get_generation_params(sel.key, sel.effective_model, providers_config)
    gen_params['model'] = sel.effective_model
    return sel.key, sel.provider, gen_params


def resolve_task_llm(
    task: dict | None,
    event_data: dict | None = None,
    *,
    plugin_loader=None,
) -> dict[str, str]:
    """Best-effort provider/model resolution mirroring the continuity executor."""
    task = dict(task or {})
    event_data = dict(event_data or {})
    configured_primary = str(task.get('provider') or 'auto').strip() or 'auto'
    configured_model = str(task.get('model') or '').strip()
    event_primary = str(event_data.get('llm_primary') or '').strip()
    event_model = str(event_data.get('llm_model') or '').strip()

    effective_primary = configured_primary
    effective_model = configured_model
    if event_primary and event_primary not in ('', 'auto'):
        effective_primary = event_primary
        effective_model = event_model

    resolved_primary = effective_primary
    resolved_model = effective_model

    if resolved_primary in ('', 'auto') and plugin_loader is not None:
        from plugins.discord.sapphire.scheduler_bridge import SapphireSchedulerBridge

        account = str(event_data.get('account') or '').strip()
        daemon_primary, daemon_model = SapphireSchedulerBridge(plugin_loader).daemon_task_llm(
            'discord_message',
            account=account or None,
        )
        if daemon_primary:
            resolved_primary = daemon_primary
            resolved_model = daemon_model or resolved_model

    if resolved_primary in ('', 'auto'):
        # DRY RUN of the real resolver (Krem's ruling 2026-09-21): same
        # privacy the text lane applies (the task carrier), no health probes,
        # so the label names the provider the turn would actually get instead
        # of a mirror that drifted (the old copy scanned with local_only=False
        # while the lane used True).
        try:
            from core.chat.llm_providers.resolve import resolve
            sel = resolve('auto', resolved_model, private=bool(task.get('privacy_required')),
                          health='skip')
            resolved_primary = sel.key
            resolved_model = sel.effective_model or resolved_model
        except Exception:
            logger.debug('Could not resolve auto LLM provider for debug', exc_info=True)

    return {
        'configured_primary': configured_primary,
        'configured_model': configured_model,
        'event_primary': event_primary,
        'event_model': event_model,
        'resolved_primary': resolved_primary,
        'resolved_model': resolved_model,
        'task_name': str(task.get('name') or ''),
        'task_id': str(task.get('id') or ''),
    }
