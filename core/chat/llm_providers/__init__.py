# llm_providers/__init__.py
"""
Provider registry — manages core providers, user instances, and plugin providers.

Three layers:
1. Core: Claude, OpenAI, Gemini — dedicated classes, custom behavior
2. Custom: User-created instances from templates (OpenAI/Anthropic/Responses compat)
3. Plugin: Plugin-registered custom provider classes

Usage:
    from core.chat.llm_providers import provider_registry
    provider = provider_registry.get_provider_by_key('claude')

    # Backward compat still works:
    from core.chat.llm_providers import get_provider_by_key
    provider = get_provider_by_key('claude', config.LLM_PROVIDERS)
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

from .base import BaseProvider, LLMResponse, ToolCall
from .openai_compat import OpenAICompatProvider
from .openai_responses import OpenAIResponsesProvider
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .anthropic_compat import AnthropicCompatProvider

logger = logging.getLogger(__name__)

# Default generation params for new instances
DEFAULT_GENERATION_PARAMS = {
    'temperature': 0.7,
    'top_p': 0.9,
    'max_tokens': 4096,
    'presence_penalty': 0.1,
    'frequency_penalty': 0.1,
}


class ProviderRegistry:
    """
    Central registry for all LLM providers.

    Manages core providers (Claude, OpenAI, Gemini), user-created custom
    instances (from templates), and plugin-registered provider classes.
    """

    def __init__(self):
        # Template classes: type_key -> provider class
        self._classes: Dict[str, type] = {
            'openai': OpenAICompatProvider,
            'openai_responses': OpenAIResponsesProvider,
            'claude': ClaudeProvider,
            'anthropic': AnthropicCompatProvider,
            'gemini': GeminiProvider,
        }

        # Core provider metadata (fixed, ships with Sapphire)
        self._core_providers: Dict[str, dict] = {
            'claude': {
                'display_name': 'Claude',
                'provider_class': 'claude',
                'required_fields': ['api_key', 'model'],
                'optional_fields': ['timeout'],
                'model_options': {
                    'claude-opus-4-6': 'Opus 4.6',
                    'claude-sonnet-4-6': 'Sonnet 4.6',
                    'claude-sonnet-4-5': 'Sonnet 4.5',
                    'claude-haiku-4-5': 'Haiku 4.5',
                    'claude-opus-4-5': 'Opus 4.5',
                },
                'is_local': False,
                'default_timeout': 10.0,
                'api_key_env': 'ANTHROPIC_API_KEY',
            },
            'openai': {
                'display_name': 'OpenAI',
                'provider_class': 'openai',
                'required_fields': ['base_url', 'api_key', 'model'],
                'optional_fields': ['timeout', 'reasoning_effort'],
                'model_options': {
                    'gpt-5.2': 'GPT-5.2 (Flagship)',
                    'gpt-5.1': 'GPT-5.1',
                    'gpt-5-mini': 'GPT-5 Mini',
                    'gpt-4o': 'GPT-4o (Legacy)',
                    'gpt-4o-mini': 'GPT-4o Mini (Legacy)',
                },
                'is_local': False,
                'default_timeout': 10.0,
                'api_key_env': 'OPENAI_API_KEY',
                'supports_reasoning': True,
            },
            'gemini': {
                'display_name': 'Gemini',
                'provider_class': 'gemini',
                'required_fields': ['api_key', 'model'],
                'optional_fields': ['timeout', 'reasoning_effort'],
                'model_options': {
                    'gemini-2.5-flash': 'Gemini 2.5 Flash (Thinking)',
                    'gemini-2.5-pro': 'Gemini 2.5 Pro (Thinking)',
                    'gemini-2.0-flash': 'Gemini 2.0 Flash',
                    'gemini-2.0-flash-lite': 'Gemini 2.0 Flash Lite',
                },
                'is_local': False,
                'default_timeout': 10.0,
                'api_key_env': 'GOOGLE_API_KEY',
                'supports_reasoning': True,
            },
        }

        # Plugin-registered classes: type_key -> {class, display_name, plugin_name, ...}
        self._plugin_classes: Dict[str, dict] = {}

        # Cached presets
        self._presets: Optional[dict] = None

    # =========================================================================
    # CLASS REGISTRATION (for plugins)
    # =========================================================================

    def register_plugin_provider(self, type_key: str, display_name: str,
                                  provider_class: type, plugin_name: str,
                                  required_fields: list = None,
                                  model_options: list = None):
        """Register a custom provider class from a plugin."""
        self._classes[type_key] = provider_class
        self._plugin_classes[type_key] = {
            'class': provider_class,
            'display_name': display_name,
            'plugin_name': plugin_name,
            'required_fields': required_fields or ['base_url', 'api_key', 'model'],
            'model_options': model_options,
        }
        logger.info(f"Plugin provider registered: {type_key} ({display_name}) from {plugin_name}")

    def unregister_plugin_providers(self, plugin_name: str):
        """Remove all provider classes from a plugin."""
        to_remove = [k for k, v in self._plugin_classes.items() if v['plugin_name'] == plugin_name]
        for key in to_remove:
            self._plugin_classes.pop(key, None)
            self._classes.pop(key, None)
            logger.info(f"Plugin provider unregistered: {key}")

    # =========================================================================
    # PROVIDER CREATION
    # =========================================================================

    def get_provider_by_key(self, provider_key: str,
                             providers_config: Dict[str, Dict[str, Any]] = None,
                             request_timeout: float = 240.0,
                             model_override: str = '') -> Optional[BaseProvider]:
        """
        Create provider instance by key.

        Checks core providers (LLM_PROVIDERS) and custom providers (LLM_CUSTOM_PROVIDERS).
        """
        if providers_config is None:
            providers_config = self._get_all_configs()

        if provider_key not in providers_config:
            logger.error(f"Unknown provider key: {provider_key}")
            return None

        config = providers_config[provider_key]

        if not config.get('enabled', False):
            logger.debug(f"Provider '{provider_key}' is disabled")
            return None

        # Determine provider class — template (custom) or provider (core)
        provider_type = config.get('template') or config.get('provider', 'openai')
        model = model_override or config.get('model', '')

        # Auto-select Responses API for OpenAI reasoning models
        if provider_type == 'openai' and OpenAIResponsesProvider.should_use_responses_api(model):
            provider_type = 'openai_responses'
            logger.info(f"[AUTO-SELECT] Using Responses API for model '{model}'")

        if provider_type not in self._classes:
            logger.error(f"Unknown provider type: {provider_type}")
            return None

        provider_class = self._classes[provider_type]

        # Build config for provider init
        api_key = self.get_api_key(provider_key, config)

        # Resolve timeout: config -> core metadata -> preset -> 5.0
        default_timeout = self._core_providers.get(provider_key, {}).get('default_timeout', 5.0)
        if config.get('is_local'):
            default_timeout = 0.3

        llm_config = {
            'provider': provider_type,
            'base_url': config.get('base_url', ''),
            'api_key': api_key,
            'model': model,
            'timeout': config.get('timeout', default_timeout),
            'enabled': True,
            # Claude-specific
            'thinking_enabled': config.get('thinking_enabled'),
            'thinking_budget': config.get('thinking_budget'),
            'cache_enabled': config.get('cache_enabled', False),
            'cache_ttl': config.get('cache_ttl', '5m'),
            # Responses API / reasoning
            'reasoning_effort': config.get('reasoning_effort', 'medium'),
            'reasoning_summary': config.get('reasoning_summary', 'auto'),
            # Config hints from presets
            'session_affinity': config.get('session_affinity', False),
            'strip_penalties': config.get('strip_penalties', False),
        }

        try:
            provider = provider_class(llm_config, request_timeout)
            logger.info(f"Created provider '{provider_key}' [{provider_type}]")
            return provider
        except Exception as e:
            logger.error(f"Failed to create provider '{provider_key}': {e}")
            return None

    # =========================================================================
    # PROVIDER LISTING (for UI)
    # =========================================================================

    def get_all_providers(self, providers_config: Dict[str, Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Return merged list of all providers for UI rendering."""
        if providers_config is None:
            providers_config = self._get_all_configs()

        result = []
        for key, config in providers_config.items():
            is_core = key in self._core_providers
            metadata = self._core_providers.get(key, {})

            api_key = self.get_api_key(key, config)
            has_api_key = bool(api_key and api_key != 'not-needed')
            needs_api_key = 'api_key' in metadata.get('required_fields', config.get('required_fields', []))

            # Key source info
            try:
                from core.credentials_manager import credentials
                has_config_key = credentials.has_stored_api_key(key)
                has_env_key = credentials.has_env_api_key(key)
                env_var = credentials.get_env_var_name(key)
            except (ImportError, AttributeError):
                has_config_key = False
                has_env_key = bool(config.get('api_key_env') and os.environ.get(config.get('api_key_env', ''), '').strip())
                env_var = config.get('api_key_env', metadata.get('api_key_env', ''))

            result.append({
                'key': key,
                'display_name': config.get('display_name', metadata.get('display_name', key)),
                'enabled': config.get('enabled', False),
                'has_api_key': has_api_key or not needs_api_key,
                'is_local': config.get('is_local', metadata.get('is_local', False)),
                'is_core': is_core,
                'template': config.get('template', config.get('provider', '')),
                'model': config.get('model', ''),
                'model_options': metadata.get('model_options') if is_core else None,
                'suggested_models': config.get('suggested_models'),
                'has_config_key': has_config_key,
                'has_env_key': has_env_key,
                'env_var': env_var,
                'base_url': config.get('base_url', ''),
                'supports_reasoning': metadata.get('supports_reasoning', False),
                'generation_params': config.get('generation_params'),
            })

        return result

    def get_core_keys(self) -> list:
        """Return list of core provider keys."""
        return list(self._core_providers.keys())

    def get_metadata(self, provider_key: str) -> Dict[str, Any]:
        """Get metadata for a specific provider (core only)."""
        return self._core_providers.get(provider_key, {})

    # =========================================================================
    # FALLBACK / AUTO MODE
    # =========================================================================

    def get_first_available_provider(self,
                                      providers_config: Dict[str, Dict[str, Any]],
                                      fallback_order: List[str],
                                      request_timeout: float = 240.0,
                                      exclude: Optional[List[str]] = None,
                                      force_privacy: bool = False) -> Optional[tuple]:
        """Get first available provider following fallback order."""
        exclude = exclude or []

        for provider_key in fallback_order:
            if provider_key in exclude:
                continue

            if provider_key not in providers_config:
                continue

            config = providers_config[provider_key]

            if not config.get('enabled', False):
                continue

            if not config.get('use_as_fallback', True):
                logger.debug(f"Provider '{provider_key}' excluded from Auto mode (use_as_fallback=False)")
                continue

            # Privacy mode
            try:
                from core.privacy import is_privacy_mode, is_allowed_endpoint
                if is_privacy_mode() or force_privacy:
                    is_local = config.get('is_local', self._core_providers.get(provider_key, {}).get('is_local', False))
                    if is_local:
                        pass  # local is always OK
                    elif config.get('privacy_check_whitelist', self._core_providers.get(provider_key, {}).get('privacy_check_whitelist')):
                        base_url = config.get('base_url', '')
                        if not is_allowed_endpoint(base_url):
                            logger.debug(f"Provider '{provider_key}' excluded in privacy mode (base_url not in whitelist)")
                            continue
                    else:
                        logger.debug(f"Provider '{provider_key}' excluded in privacy mode (cloud provider)")
                        continue
            except ImportError:
                pass

            provider = self.get_provider_by_key(provider_key, providers_config, request_timeout)
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

    # =========================================================================
    # GENERATION PARAMS
    # =========================================================================

    def get_generation_params(self, provider_key: str, model: str,
                               providers_config: Dict[str, Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get generation parameters for a provider instance.

        Resolution:
        1. Instance generation_params (if set)
        2. MODEL_GENERATION_PROFILES lookup by model name
        3. MODEL_GENERATION_PROFILES __fallback__
        4. Default params
        """
        if providers_config is None:
            providers_config = self._get_all_configs()

        config = providers_config.get(provider_key, {})

        # 1. Instance-level override (highest priority)
        if config.get('generation_params'):
            return {**DEFAULT_GENERATION_PARAMS, **config['generation_params']}

        # 2. Model-name lookup in profiles (preserves tuned params per model)
        try:
            import config as app_config
            profiles = getattr(app_config, 'MODEL_GENERATION_PROFILES', {})
            if model and model in profiles:
                return {**DEFAULT_GENERATION_PARAMS, **profiles[model]}
            if '__fallback__' in profiles:
                return {**DEFAULT_GENERATION_PARAMS, **profiles['__fallback__']}
        except ImportError:
            pass

        return dict(DEFAULT_GENERATION_PARAMS)

    # =========================================================================
    # API KEYS
    # =========================================================================

    def get_api_key(self, provider_key: str, config: Dict[str, Any] = None) -> str:
        """
        Get API key for any provider (core or custom).

        Priority:
        1. credentials_manager stored key (under provider_key)
        2. Environment variable (from config.api_key_env or core metadata)
        3. Explicit api_key in config (backward compat)
        4. 'not-needed' for local providers
        """
        # Stored credential
        try:
            from core.credentials_manager import credentials
            key = credentials.get_llm_api_key(provider_key)
            if key:
                return key
        except ImportError:
            pass

        # Env var — check config, then core metadata
        if config is None:
            config = self._get_all_configs().get(provider_key, {})
        env_var = config.get('api_key_env') or self._core_providers.get(provider_key, {}).get('api_key_env', '')
        if env_var:
            val = os.environ.get(env_var, '')
            if val and val.strip():
                logger.debug(f"Using API key from env var {env_var} for {provider_key}")
                return val

        # Backward compat: explicit config value
        explicit_key = config.get('api_key', '')
        if explicit_key and explicit_key.strip():
            return explicit_key

        # Local providers
        is_local = config.get('is_local', self._core_providers.get(provider_key, {}).get('is_local', False))
        if is_local:
            return 'not-needed'

        return ''

    # =========================================================================
    # PRESETS
    # =========================================================================

    def get_presets(self) -> dict:
        """Load curated presets from provider_presets.json."""
        if self._presets is None:
            presets_path = Path(__file__).parent.parent.parent / "provider_presets.json"
            try:
                self._presets = json.loads(presets_path.read_text(encoding='utf-8')).get('presets', {})
            except Exception as e:
                logger.warning(f"Could not load provider presets: {e}")
                self._presets = {}
        return self._presets

    def get_templates(self) -> list:
        """Return available template types for '+ Add Provider' UI."""
        templates = [
            {'key': 'openai', 'name': 'OpenAI Compatible'},
            {'key': 'anthropic', 'name': 'Anthropic Compatible'},
            {'key': 'responses', 'name': 'Responses API'},
        ]
        for key, info in self._plugin_classes.items():
            templates.append({'key': key, 'name': info['display_name']})
        return templates

    # =========================================================================
    # MODEL DISCOVERY
    # =========================================================================

    def discover_models(self, provider_key: str,
                         providers_config: Dict[str, Dict[str, Any]] = None) -> Optional[list]:
        """
        Discover available models from a provider.

        For OpenAI-compat providers, hits /v1/models.
        Returns list of {"id": str, "name": str} or None.
        """
        provider = self.get_provider_by_key(provider_key, providers_config)
        if not provider:
            return None

        if hasattr(provider, 'list_models'):
            try:
                return provider.list_models()
            except Exception as e:
                logger.debug(f"Model discovery failed for {provider_key}: {e}")
                return None

        return None

    # =========================================================================
    # CONFIG HELPERS
    # =========================================================================

    def _get_all_configs(self) -> Dict[str, Dict[str, Any]]:
        """Merge core and custom provider configs from settings."""
        try:
            from core.settings_manager import settings
            core = settings.get('LLM_PROVIDERS', {})
            custom = settings.get('LLM_CUSTOM_PROVIDERS', {})
            return {**core, **custom}
        except ImportError:
            return {}

    def is_core_provider(self, key: str) -> bool:
        return key in self._core_providers


# Singleton
provider_registry = ProviderRegistry()


# =============================================================================
# BACKWARD COMPATIBILITY EXPORTS
# =============================================================================
# These functions maintain the old API so chat.py, execution_context.py, etc.
# don't need immediate changes. They delegate to the registry.

# Legacy dicts — kept as properties that read from registry
PROVIDER_CLASSES = provider_registry._classes

def _build_legacy_metadata():
    """Build PROVIDER_METADATA from registry for backward compat."""
    meta = dict(provider_registry._core_providers)
    # Add custom providers from settings as metadata entries
    try:
        from core.settings_manager import settings
        custom = settings.get('LLM_CUSTOM_PROVIDERS', {})
        for key, config in custom.items():
            meta[key] = {
                'display_name': config.get('display_name', key),
                'provider_class': config.get('template', 'openai'),
                'required_fields': ['base_url', 'api_key', 'model'],
                'is_local': config.get('is_local', False),
                'default_timeout': config.get('timeout', 5.0),
                'api_key_env': config.get('api_key_env', ''),
                'model_options': None,
                'privacy_check_whitelist': config.get('is_local', False),
            }
    except ImportError:
        pass
    return meta

# Lazy property — rebuilt when accessed
class _MetadataProxy(dict):
    def __getitem__(self, key):
        return _build_legacy_metadata()[key]
    def get(self, key, default=None):
        return _build_legacy_metadata().get(key, default)
    def __contains__(self, key):
        return key in _build_legacy_metadata()
    def items(self):
        return _build_legacy_metadata().items()
    def keys(self):
        return _build_legacy_metadata().keys()
    def values(self):
        return _build_legacy_metadata().values()

PROVIDER_METADATA = _MetadataProxy()


def get_api_key(provider_config: Dict[str, Any], provider_key: str) -> str:
    """Legacy — delegates to registry."""
    return provider_registry.get_api_key(provider_key, provider_config)


def get_generation_params(provider_key: str, model: str,
                           providers_config: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Legacy — delegates to registry."""
    return provider_registry.get_generation_params(provider_key, model, providers_config)


def get_provider_by_key(provider_key: str,
                         providers_config: Dict[str, Dict[str, Any]],
                         request_timeout: float = 240.0,
                         model_override: str = '') -> Optional[BaseProvider]:
    """Legacy — delegates to registry."""
    return provider_registry.get_provider_by_key(provider_key, providers_config, request_timeout, model_override)


def get_first_available_provider(providers_config: Dict[str, Dict[str, Any]],
                                  fallback_order: List[str],
                                  request_timeout: float = 240.0,
                                  exclude: Optional[List[str]] = None,
                                  force_privacy: bool = False) -> Optional[tuple]:
    """Legacy — delegates to registry."""
    return provider_registry.get_first_available_provider(
        providers_config, fallback_order, request_timeout, exclude, force_privacy)


def get_available_providers(providers_config: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Legacy — delegates to registry."""
    return provider_registry.get_all_providers(providers_config)


def get_provider_metadata(provider_key: str) -> Dict[str, Any]:
    """Legacy — delegates to registry."""
    return provider_registry.get_metadata(provider_key)


# Legacy functions that may still be used
def get_provider(llm_config: Dict[str, Any], request_timeout: float = 240.0) -> Optional[BaseProvider]:
    """Legacy — create provider directly from config dict."""
    if not llm_config.get('enabled', False):
        return None
    provider_type = llm_config.get('provider', 'openai')
    provider_class = provider_registry._classes.get(provider_type, OpenAICompatProvider)
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
    elif 'generativelanguage.googleapis.com' in url_lower:
        return 'gemini'
    return 'openai'


def migrate_legacy_config(old_primary: Dict, old_fallback: Dict) -> tuple:
    """Convert old LLM_PRIMARY/LLM_FALLBACK to new format."""
    providers = {}
    fallback_order = []

    def detect_type(url: str) -> tuple:
        url_lower = url.lower()
        if 'anthropic.com' in url_lower:
            return ('claude', 'claude')
        elif '127.0.0.1' in url or 'localhost' in url_lower:
            return ('lmstudio', 'openai')
        else:
            return ('openai', 'openai')

    if old_primary.get('enabled'):
        key, ptype = detect_type(old_primary.get('base_url', ''))
        providers[key] = {
            'provider': ptype,
            'display_name': provider_registry._core_providers.get(key, {}).get('display_name', key),
            'base_url': old_primary.get('base_url', ''),
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
            'display_name': provider_registry._core_providers.get(key, {}).get('display_name', key),
            'base_url': old_fallback.get('base_url', ''),
            'model': old_fallback.get('model', ''),
            'timeout': old_fallback.get('timeout', 0.3),
            'enabled': True,
        }
        fallback_order.append(key)

    return providers, fallback_order


__all__ = [
    'provider_registry',
    'ProviderRegistry',
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
    'OpenAIResponsesProvider',
    'ClaudeProvider',
    'GeminiProvider',
    'AnthropicCompatProvider',
    'PROVIDER_CLASSES',
    'PROVIDER_METADATA',
    'DEFAULT_GENERATION_PARAMS',
]
