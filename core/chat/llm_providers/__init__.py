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
        'model_options': [
            'claude-sonnet-4-20250514',
            'claude-opus-4-20250514',
            'claude-3-5-sonnet-20241022',
            'claude-3-5-haiku-20241022',
            'claude-3-opus-20240229',
        ],
        'is_local': False,
        'default_timeout': 5.0,
        'api_key_env': 'ANTHROPIC_API_KEY',
    },
    'fireworks': {
        'display_name': 'Fireworks',
        'provider_class': 'fireworks',
        'required_fields': ['base_url', 'api_key', 'model'],
        'optional_fields': ['timeout'],
        'model_options': [
            'accounts/fireworks/models/glm-4p7',
            'accounts/fireworks/models/llama-v3p1-70b-instruct',
            'accounts/fireworks/models/llama-v3p1-405b-instruct',
            'accounts/fireworks/models/qwen2p5-72b-instruct',
        ],
        'is_local': False,
        'default_timeout': 5.0,
        'api_key_env': 'FIREWORKS_API_KEY',
    },
    'openai': {
        'display_name': 'OpenAI',
        'provider_class': 'openai',
        'required_fields': ['base_url', 'api_key', 'model'],
        'optional_fields': ['timeout'],
        'model_options': [
            'gpt-4o',
            'gpt-4o-mini',
            'gpt-4-turbo',
            'o1',
            'o1-mini',
        ],
        'is_local': False,
        'default_timeout': 5.0,
        'api_key_env': 'OPENAI_API_KEY',
    },
}


def get_api_key(provider_config: Dict[str, Any], provider_key: str) -> str:
    """
    Get API key from config or environment variable.
    
    Priority:
    1. Explicit api_key in config (if non-empty)
    2. Environment variable specified by api_key_env
    3. Default env var for known providers
    4. Empty string (for local providers that don't need keys)
    """
    # Check explicit config value
    explicit_key = provider_config.get('api_key', '')
    if explicit_key and explicit_key.strip():
        return explicit_key
    
    # Check env var from config
    env_var = provider_config.get('api_key_env', '')
    if env_var:
        env_value = os.environ.get(env_var, '')
        if env_value:
            logger.debug(f"Using API key from env var {env_var} for {provider_key}")
            return env_value
    
    # Check default env var from metadata
    metadata = PROVIDER_METADATA.get(provider_key, {})
    default_env = metadata.get('api_key_env', '')
    if default_env:
        env_value = os.environ.get(default_env, '')
        if env_value:
            logger.debug(f"Using API key from default env var {default_env} for {provider_key}")
            return env_value
    
    # Local providers don't need keys
    if metadata.get('is_local', False):
        return 'not-needed'
    
    return ''


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
        
        if not providers_config[provider_key].get('enabled', False):
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