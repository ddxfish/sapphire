# llm_providers/__init__.py
"""
Multi-provider LLM abstraction layer.

Supports:
- openai: LM Studio, llama.cpp, OpenRouter, any OpenAI-compatible API
- fireworks: Fireworks.ai (OpenAI-compatible with different base URL)
- claude: Anthropic Claude API

Usage:
    from llm_providers import get_provider
    
    provider = get_provider(config.LLM_PRIMARY)
    response = provider.chat_completion(messages, tools, params)
"""

import logging
from typing import Dict, Any, Optional

from .base import BaseProvider, LLMResponse, ToolCall
from .openai_compat import OpenAICompatProvider
from .claude import ClaudeProvider

logger = logging.getLogger(__name__)

# Provider registry
PROVIDERS = {
    'openai': OpenAICompatProvider,
    'fireworks': OpenAICompatProvider,  # Fireworks is OpenAI-compatible
    'claude': ClaudeProvider,
}

# Default provider for backward compatibility (no 'provider' field in config)
DEFAULT_PROVIDER = 'openai'


def get_provider(llm_config: Dict[str, Any], request_timeout: float = 240.0) -> Optional[BaseProvider]:
    """
    Factory function to create appropriate provider based on config.
    
    Args:
        llm_config: Dict with keys: provider, base_url, api_key, model, timeout, enabled
        request_timeout: Overall request timeout (from config.LLM_REQUEST_TIMEOUT)
    
    Returns:
        Provider instance or None if disabled/error
    """
    if not llm_config.get('enabled', False):
        logger.info(f"LLM config disabled")
        return None
    
    provider_name = llm_config.get('provider', DEFAULT_PROVIDER).lower()
    
    if provider_name not in PROVIDERS:
        logger.error(f"Unknown provider: {provider_name}. Available: {list(PROVIDERS.keys())}")
        return None
    
    provider_class = PROVIDERS[provider_name]
    
    try:
        provider = provider_class(llm_config, request_timeout)
        logger.info(f"Created {provider_name} provider: {llm_config.get('base_url', 'N/A')}")
        return provider
    except Exception as e:
        logger.error(f"Failed to create {provider_name} provider: {e}")
        return None


def get_provider_for_url(base_url: str) -> str:
    """
    Auto-detect provider based on URL (fallback when provider field missing).
    
    Args:
        base_url: The API base URL
    
    Returns:
        Provider name string
    """
    url_lower = base_url.lower()
    
    if 'anthropic.com' in url_lower:
        return 'claude'
    elif 'fireworks.ai' in url_lower:
        return 'fireworks'
    else:
        # Default to OpenAI-compatible for everything else
        # (LM Studio, llama.cpp, OpenRouter, local servers, etc.)
        return 'openai'


__all__ = [
    'get_provider',
    'get_provider_for_url',
    'BaseProvider',
    'LLMResponse',
    'ToolCall',
    'OpenAICompatProvider',
    'ClaudeProvider',
    'PROVIDERS',
]