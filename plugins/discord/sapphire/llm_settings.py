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


def proactive_llm_from_settings(settings, *, kind: str = 'greeting') -> tuple[str, str]:
    """Resolve proactive LLM provider/model, inheriting from Reply LLM when unset."""
    proactive = getattr(settings, 'proactive', None)
    cognitive_primary, cognitive_model = cognitive_llm_from_settings(settings)
    if proactive is None:
        return cognitive_primary, cognitive_model

    greeting_provider = str(getattr(proactive, 'greeting_model_provider', '') or '').strip()
    greeting_model = str(getattr(proactive, 'greeting_model_name', '') or '').strip()
    goodnight_provider = str(getattr(proactive, 'goodnight_model_provider', '') or '').strip()
    goodnight_model = str(getattr(proactive, 'goodnight_model_name', '') or '').strip()

    if kind == 'goodnight':
        provider = goodnight_provider or greeting_provider or cognitive_primary
        model = goodnight_model or greeting_model or cognitive_model
    else:
        provider = greeting_provider or cognitive_primary
        model = greeting_model or cognitive_model
    return provider, model


def distill_llm_from_settings(settings) -> tuple[str, str]:
    """Resolve ambient-distill LLM, inheriting from Reply LLM when unset."""
    profile = getattr(settings, 'profile', None)
    cognitive_primary, cognitive_model = cognitive_llm_from_settings(settings)
    if profile is None:
        return cognitive_primary, cognitive_model
    provider = str(getattr(profile, 'distill_model_provider', '') or '').strip()
    model = str(getattr(profile, 'distill_model_name', '') or '').strip()
    return (provider or cognitive_primary), (model or cognitive_model)


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

    local_only (default: the cognitive.side_lanes_local_only setting) narrows
    the 'auto' scan to providers marked local — explicit picks are untouched."""
    llm = getattr(system, 'llm_chat', None)
    if llm is None:
        return None, None, None

    from core.chat.llm_providers import get_generation_params, get_provider_by_key

    primary = str(provider_key or 'auto').strip() or 'auto'
    model = str(model_name or '').strip()

    if primary in ('', 'auto'):
        # Auto = global fallback order — NEVER llm._select_provider(), which
        # reads the operator's active WEB chat settings: a Discord voice reply
        # in auto mode was literally running on whatever provider the browser
        # tab had selected. Mirror the continuity executor's auto path instead.
        # 2026-08-06.
        import config
        from core.chat.llm_providers import get_first_available_provider
        providers_config = _providers_config()
        fallback_order = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config.keys()))
        if local_only is None:
            local_only = side_lanes_local_only()
        result = get_first_available_provider(
            providers_config, fallback_order,
            getattr(config, 'LLM_REQUEST_TIMEOUT', 60.0),
            force_privacy=bool(local_only))
        if not result:
            logger.warning('Discord LLM auto mode: no %sproviders available',
                           'local ' if local_only else '')
            return None, None, None
        selected_key, provider = result
        gen_params = get_generation_params(selected_key, provider.model, providers_config)
        return selected_key, provider, gen_params

    import config

    provider = get_provider_by_key(
        primary,
        _providers_config(),
        getattr(config, 'LLM_REQUEST_TIMEOUT', 60.0),
        model_override=model or None,
    )
    if not provider:
        logger.warning('Discord LLM provider %r is not available', primary)
        return None, None, None

    effective_model = model or provider.model
    gen_params = get_generation_params(primary, effective_model, _providers_config())
    gen_params['model'] = effective_model
    return primary, provider, gen_params


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
        try:
            from core.api_fastapi import get_system

            selected_key, _provider, gen_params = resolve_discord_llm_provider(
                get_system(),
                'auto',
                resolved_model,
            )
            if selected_key:
                resolved_primary = selected_key
                resolved_model = str((gen_params or {}).get('model') or resolved_model or '')
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
