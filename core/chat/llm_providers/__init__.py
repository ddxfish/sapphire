# llm_providers/__init__.py
"""
Multi-provider LLM abstraction layer.

Supports:
- lmstudio: LM Studio (local, no API key needed)
- claude: Anthropic Claude API
- fireworks: Fireworks.ai 
- openai: OpenAI or compatible APIs

Usage:
    from llm_providers import get_provider_by_key, get_available_providers
    
    provider = get_provider_by_key('claude', config.LLM_PROVIDERS)
    response = provider.chat_completion(messages, tools, params)
"""

import os
import logging
from typing import Dict, Any, Optional, List

from .base import BaseProvider, LLMResponse, ToolCall
from .openai_compat import OpenAICompatProvider
from .claude import ClaudeProvider

logger = logging.getLogger(__name__)

# Provider class registry
PROVIDER_CLASSES = {
    'openai': OpenAICompatProvider,
    'fireworks': OpenAICompatProvider,
    'claude': ClaudeProvider,
}

# Provider metadata for UI rendering
PROVIDER_METADATA = {
    'lmstudio': {
        'display_name': 'LM Studio',
        'provider_class': 'openai',
        'required_fields': ['base_url'],
        'optional_fields': ['timeout'],
        'model_options': None,  # No model selection for LM Studio
        'is_local': True,
        'default_timeout': 0.3,
    },
    'claude': {
        'display_name': 'Claude',
        'provider_class': 'claude',
        'required_fields': ['api_key', 'model'],
        'optional_fields': ['timeout'],
        'model_options': {
            'claude-sonnet-4-5': 'Sonnet 4.5',
            'claude-haiku-4-5': 'Haiku 4.5',
            'claude-opus-4-5': 'Opus 4.5',
        },
        'is_local': False,
        'default_timeout': 10.0,
        'api_key_env': 'ANTHROPIC_API_KEY',
    },
    'fireworks': {
        'display_name': 'Fireworks',
        'provider_class': 'fireworks',
        'required_fields': ['base_url', 'api_key', 'model'],
        'optional_fields': ['timeout'],
        'model_options': {
            'accounts/fireworks/models/qwen3-235b-a22b-thinking-2507': 'Qwen3 235B Thinking',
            'accounts/fireworks/models/qwen3-coder-480b-a35b-instruct': 'Qwen3 Coder 480B',
            'accounts/fireworks/models/kimi-k2-thinking': 'Kimi K2 Thinking',
            'accounts/fireworks/models/qwq-32b': 'QwQ 32B',
            'accounts/fireworks/models/gpt-oss-120b': 'GPT-OSS 120B',
            'accounts/fireworks/models/deepseek-v3p2': 'DeepSeek V3.2',
            'accounts/fireworks/models/qwen3-vl-235b-a22b-thinking': 'Qwen3 VL 235B Thinking',
            'accounts/fireworks/models/glm-4p7': 'GLM 4.7',
            'accounts/fireworks/models/minimax-m2p1': 'MiniMax M2.1',
        },
        'is_local': False,
        'default_timeout': 10.0,
        'api_key_env': 'FIREWORKS_API_KEY',
    },
    'openai': {
        'display_name': 'OpenAI',
        'provider_class': 'openai',
        'required_fields': ['base_url', 'api_key', 'model'],
        'optional_fields': ['timeout'],
        'model_options': {
            'gpt-4o': 'GPT-4o',
            'gpt-4o-mini': 'GPT-4o Mini',
            'gpt-4-turbo': 'GPT-4 Turbo',
            'o1': 'o1',
            'o1-mini': 'o1 Mini',
        },
        'is_local': False,
        'default_timeout': 10.0,
        'api_key_env': 'OPENAI_API_KEY',
    },
    'other': {
        'display_name': 'Other (OpenAI Compatible)',
        'provider_class': 'openai',
        'required_fields': ['base_url', 'api_key', 'model'],
        'optional_fields': ['timeout'],
        'model_options': None,  # Free-form model entry
        'is_local': False,
        'default_timeout': 10.0,
    },
}


def get_api_key(provider_config: Dict[str, Any], provider_key: str) -> str:
    """
    Get API key for a provider.
    
    Delegates to credentials_manager which handles:
    1. Stored credential in credentials.json (priority)
    2. Environment variable fallback
    
    Also checks explicit api_key in provider_config for backwards compatibility.
    """
    # Check credentials_manager (handles stored + env logic)
    try:
        from core.credentials_manager import credentials
        key = credentials.get_llm_api_key(provider_key)
        if key:
            return key
    except ImportError:
        pass
    
    # Backwards compat: check explicit config value
    explicit_key = provider_config.get('api_key', '')
    if explicit_key and explicit_key.strip():
        return explicit_key
    
    # Local providers don't need keys
    metadata = PROVIDER_METADATA.get(provider_key, {})
    if metadata.get('is_local', False):
        return 'not-needed'
    
    return ''


def get_generation_params(provider_key: str, model: str, providers_config: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Get generation parameters for a model.
    
    Resolution order:
    1. Provider-specific generation_params (for custom/other models)
    2. MODEL_GENERATION_PROFILES lookup by model name
    3. Fallback profile (__fallback__)
    
    Args:
        provider_key: The provider key (e.g., 'claude', 'other')
        model: The model name string
        providers_config: The LLM_PROVIDERS dict
    
    Returns:
        Dict with temperature, top_p, max_tokens, presence_penalty, frequency_penalty
    """
    import config as app_config
    
    default_params = {
        'temperature': 0.7,
        'top_p': 0.9,
        'max_tokens': 4096,
        'presence_penalty': 0.1,
        'frequency_penalty': 0.1
    }
    
    # 1. Check provider-specific generation_params (for Other/custom models)
    provider_config = providers_config.get(provider_key, {})
    if provider_config.get('generation_params'):
        params = provider_config['generation_params']
        if params:
            logger.debug(f"Using provider-specific generation_params for {provider_key}")
            return {**default_params, **params}
    
    # 2. Look up MODEL_GENERATION_PROFILES
    profiles = getattr(app_config, 'MODEL_GENERATION_PROFILES', {})
    if model and model in profiles:
        logger.debug(f"Using generation profile for model '{model}'")
        return {**default_params, **profiles[model]}
    
    # 3. Fallback profile
    if '__fallback__' in profiles:
        logger.debug(f"Using __fallback__ generation profile for '{model}'")
        return {**default_params, **profiles['__fallback__']}
    
    # 4. Ultimate fallback - return defaults
    logger.debug(f"Using hardcoded default generation params for '{model}'")
    return default_params


def get_provider_by_key(
    provider_key: str,
    providers_config: Dict[str, Dict[str, Any]],
    request_timeout: float = 240.0
) -> Optional[BaseProvider]:
    """
    Create provider instance by key from LLM_PROVIDERS config.
    
    Args:
        provider_key: Key in LLM_PROVIDERS (e.g., 'claude', 'lmstudio')
        providers_config: The LLM_PROVIDERS dict from settings
        request_timeout: Overall request timeout
    
    Returns:
        Provider instance or None if disabled/error
    """
    if provider_key not in providers_config:
        logger.error(f"Unknown provider key: {provider_key}")
        return None
    
    config = providers_config[provider_key]
    
    if not config.get('enabled', False):
        logger.debug(f"Provider '{provider_key}' is disabled")
        return None
    
    # Determine provider class
    provider_type = config.get('provider', 'openai')
    if provider_type not in PROVIDER_CLASSES:
        logger.error(f"Unknown provider type: {provider_type}")
        return None
    
    provider_class = PROVIDER_CLASSES[provider_type]
    
    # Build config for provider init
    api_key = get_api_key(config, provider_key)
    
    llm_config = {
        'provider': provider_type,
        'base_url': config.get('base_url', ''),
        'api_key': api_key,
        'model': config.get('model', ''),
        'timeout': config.get('timeout', PROVIDER_METADATA.get(provider_key, {}).get('default_timeout', 5.0)),
        'enabled': True,
    }
    
    try:
        provider = provider_class(llm_config, request_timeout)
        logger.info(f"Created provider '{provider_key}' [{provider_type}]")
        return provider
    except Exception as e:
        logger.error(f"Failed to create provider '{provider_key}': {e}")
        return None


def get_first_available_provider(
    providers_config: Dict[str, Dict[str, Any]],
    fallback_order: List[str],
    request_timeout: float = 240.0,
    exclude: Optional[List[str]] = None
) -> Optional[tuple]:
    """
    Get first available provider following fallback order.
    
    Only considers providers with use_as_fallback=True (default).
    Providers with use_as_fallback=False are excluded from Auto mode
    and can only be used when explicitly selected per-chat.
    
    Args:
        providers_config: The LLM_PROVIDERS dict
        fallback_order: List of provider keys in priority order
        request_timeout: Overall request timeout
        exclude: Provider keys to skip
    
    Returns:
        Tuple of (provider_key, provider_instance) or None
    """
    exclude = exclude or []
    
    for provider_key in fallback_order:
        if provider_key in exclude:
            continue
        
        if provider_key not in providers_config:
            continue
        
        config = providers_config[provider_key]
        
        if not config.get('enabled', False):
            continue
        
        # Skip providers not in Auto fallback pool
        if not config.get('use_as_fallback', True):
            logger.debug(f"Provider '{provider_key}' excluded from Auto mode (use_as_fallback=False)")
            continue
        
        provider = get_provider_by_key(provider_key, providers_config, request_timeout)
        if provider:
            try:
                if provider.health_check():
                    logger.info(f"Selected provider '{provider_key}' (healthy)")
                    return (provider_key, provider)
                else:
                    logger.debug(f"Provider '{provider_key}' failed health check")
            except Exception as e:
                logger.debug(f"Provider '{provider_key}' health check error: {e}")
    
    return None


def get_available_providers(providers_config: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Get list of all configured providers with their metadata.
    Used by UI to render provider options.
    
    Returns:
        List of dicts with key, display_name, enabled, has_api_key, etc.
    """
    result = []
    
    for key, config in providers_config.items():
        metadata = PROVIDER_METADATA.get(key, {})
        
        # Check if API key is available
        api_key = get_api_key(config, key)
        has_api_key = bool(api_key and api_key != 'not-needed')
        needs_api_key = 'api_key' in metadata.get('required_fields', [])
        
        result.append({
            'key': key,
            'display_name': config.get('display_name', metadata.get('display_name', key)),
            'enabled': config.get('enabled', False),
            'has_api_key': has_api_key or not needs_api_key,
            'is_local': metadata.get('is_local', False),
            'model': config.get('model', ''),
            'model_options': metadata.get('model_options'),
        })
    
    return result


def get_provider_metadata(provider_key: str) -> Dict[str, Any]:
    """Get metadata for a specific provider."""
    return PROVIDER_METADATA.get(provider_key, {})


# Legacy compatibility functions
def get_provider(llm_config: Dict[str, Any], request_timeout: float = 240.0) -> Optional[BaseProvider]:
    """
    Legacy function for old LLM_PRIMARY/LLM_FALLBACK config format.
    Creates provider directly from config dict.
    """
    if not llm_config.get('enabled', False):
        return None
    
    provider_type = llm_config.get('provider', 'openai')
    if provider_type not in PROVIDER_CLASSES:
        # Auto-detect from URL
        provider_type = get_provider_for_url(llm_config.get('base_url', ''))
    
    provider_class = PROVIDER_CLASSES.get(provider_type, OpenAICompatProvider)
    
    try:
        return provider_class(llm_config, request_timeout)
    except Exception as e:
        logger.error(f"Failed to create provider: {e}")
        return None


def get_provider_for_url(base_url: str) -> str:
    """Auto-detect provider type from URL."""
    url_lower = base_url.lower()
    if 'anthropic.com' in url_lower:
        return 'claude'
    elif 'fireworks.ai' in url_lower:
        return 'fireworks'
    return 'openai'


def migrate_legacy_config(old_primary: Dict, old_fallback: Dict) -> tuple:
    """
    Convert old LLM_PRIMARY/LLM_FALLBACK to new LLM_PROVIDERS format.
    Returns (providers_dict, fallback_order).
    """
    providers = {}
    fallback_order = []
    
    def detect_type(url: str) -> tuple:
        url_lower = url.lower()
        if 'anthropic.com' in url_lower:
            return ('claude', 'claude')
        elif 'fireworks.ai' in url_lower:
            return ('fireworks', 'fireworks')
        elif '127.0.0.1' in url or 'localhost' in url_lower:
            return ('lmstudio', 'openai')
        else:
            return ('openai', 'openai')
    
    if old_primary.get('enabled'):
        key, ptype = detect_type(old_primary.get('base_url', ''))
        providers[key] = {
            'provider': ptype,
            'display_name': PROVIDER_METADATA.get(key, {}).get('display_name', key),
            'base_url': old_primary.get('base_url', ''),
            'api_key': old_primary.get('api_key', ''),
            'model': old_primary.get('model', ''),
            'timeout': old_primary.get('timeout', 0.3),
            'enabled': True,
        }
        fallback_order.append(key)
    
    if old_fallback.get('enabled'):
        key, ptype = detect_type(old_fallback.get('base_url', ''))
        if key in providers:
            key = f"{key}_fallback"
        providers[key] = {
            'provider': ptype,
            'display_name': PROVIDER_METADATA.get(key, {}).get('display_name', key),
            'base_url': old_fallback.get('base_url', ''),
            'api_key': old_fallback.get('api_key', ''),
            'model': old_fallback.get('model', ''),
            'timeout': old_fallback.get('timeout', 0.3),
            'enabled': True,
        }
        fallback_order.append(key)
    
    return providers, fallback_order


__all__ = [
    'get_provider_by_key',
    'get_first_available_provider',
    'get_available_providers',
    'get_provider_metadata',
    'get_api_key',
    'get_generation_params',
    'get_provider',
    'get_provider_for_url',
    'migrate_legacy_config',
    'BaseProvider',
    'LLMResponse',
    'ToolCall',
    'OpenAICompatProvider',
    'ClaudeProvider',
    'PROVIDER_CLASSES',
    'PROVIDER_METADATA',
]