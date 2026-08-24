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


def resolve_discord_llm_provider(system, provider_key: str, model_name: str = ''):
    """Return (provider_key, provider, gen_params) for plugin-configured LLM calls."""
    llm = getattr(system, 'llm_chat', None)
    if llm is None:
        return None, None, None

    from core.chat.llm_providers import get_generation_params, get_provider_by_key

    primary = str(provider_key or 'auto').strip() or 'auto'
    model = str(model_name or '').strip()

    if primary in ('', 'auto'):
        selected_key, provider, model_override = llm._select_provider()
        effective_model = model_override or provider.model
        gen_params = get_generation_params(selected_key, effective_model, _providers_config())
        if model_override:
            gen_params['model'] = model_override
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
