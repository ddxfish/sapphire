"""
Provider Refactor Regression Tests

Tests the three-layer provider system (core, custom, plugin),
migration, cross-provider compatibility, and edge cases.

Run with: pytest tests/test_provider_refactor.py -v
"""
import pytest
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.chat.llm_providers import (
    provider_registry, ProviderRegistry,
    get_provider_by_key, get_available_providers, get_generation_params,
    get_provider, get_provider_for_url,
    PROVIDER_CLASSES, PROVIDER_METADATA,
    BaseProvider, LLMResponse, ToolCall,
    OpenAICompatProvider, ClaudeProvider, AnthropicCompatProvider,
    DEFAULT_GENERATION_PARAMS,
)


# =============================================================================
# Registry Core Tests
# =============================================================================

class TestProviderRegistry:
    """Test the ProviderRegistry class."""

    def test_core_providers_exist(self):
        """Registry should have exactly 3 core providers."""
        core = provider_registry.get_core_keys()
        assert 'claude' in core
        assert 'openai' in core
        assert 'gemini' in core
        assert len(core) == 3

    def test_core_metadata_has_model_options(self):
        """Core providers should have curated model options."""
        meta = provider_registry.get_metadata('claude')
        assert meta.get('model_options')
        assert 'claude-opus-5' in meta['model_options']

    def test_templates_available(self):
        """Should have 3 built-in templates."""
        templates = provider_registry.get_templates()
        template_keys = [t['key'] for t in templates]
        assert 'openai' in template_keys
        assert 'anthropic' in template_keys
        assert 'openai_responses' in template_keys

    def test_presets_loaded(self):
        """Presets should load from JSON."""
        # Clear cache to force reload
        provider_registry._presets = None
        presets = provider_registry.get_presets()
        assert len(presets) > 0
        assert 'fireworks' in presets
        assert 'openrouter' in presets
        assert 'lmstudio' in presets

    def test_preset_has_required_fields(self):
        """Each preset should have template, display_name, base_url."""
        presets = provider_registry.get_presets()
        for name, preset in presets.items():
            assert 'template' in preset, f"Preset '{name}' missing template"
            assert 'display_name' in preset, f"Preset '{name}' missing display_name"

    def test_is_core_provider(self):
        """Should correctly identify core vs custom."""
        assert provider_registry.is_core_provider('claude') is True
        assert provider_registry.is_core_provider('openai') is True
        assert provider_registry.is_core_provider('fireworks') is False
        assert provider_registry.is_core_provider('nonexistent') is False

    def test_classes_registry_complete(self):
        """All necessary provider classes should be registered."""
        classes = provider_registry._classes
        assert 'openai' in classes
        assert 'claude' in classes
        assert 'anthropic' in classes
        assert 'gemini' in classes
        assert 'openai_responses' in classes


# =============================================================================
# Plugin Registration Tests
# =============================================================================

class TestPluginRegistration:
    """Test plugin provider registration."""

    def test_register_plugin_provider(self):
        """Plugin should be able to register a custom provider class."""
        class FakeProvider(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, messages, tools=None, generation_params=None):
                return LLMResponse(content="test")
            def chat_completion_stream(self, messages, tools=None, generation_params=None):
                yield {"type": "done", "response": LLMResponse(content="test")}

        provider_registry.register_plugin_provider(
            type_key='test_plugin',
            display_name='Test Plugin',
            provider_class=FakeProvider,
            plugin_name='test-plugin',
            model_options=[{"id": "test-model", "name": "Test Model"}],
        )

        assert 'test_plugin' in provider_registry._classes
        assert 'test_plugin' in provider_registry._plugin_classes

        # Should appear in templates
        templates = provider_registry.get_templates()
        assert any(t['key'] == 'test_plugin' for t in templates)

        # Cleanup
        provider_registry.unregister_plugin_providers('test-plugin')
        assert 'test_plugin' not in provider_registry._classes
        assert 'test_plugin' not in provider_registry._plugin_classes

    def test_unregister_only_removes_owned(self):
        """Unregister should only remove providers from the named plugin."""
        class FakeA(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, m, t=None, g=None): return LLMResponse(content="a")
            def chat_completion_stream(self, m, t=None, g=None): yield {"type": "done", "response": LLMResponse(content="a")}

        class FakeB(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, m, t=None, g=None): return LLMResponse(content="b")
            def chat_completion_stream(self, m, t=None, g=None): yield {"type": "done", "response": LLMResponse(content="b")}

        provider_registry.register_plugin_provider('prov_a', 'A', FakeA, 'plugin-a')
        provider_registry.register_plugin_provider('prov_b', 'B', FakeB, 'plugin-b')

        provider_registry.unregister_plugin_providers('plugin-a')

        assert 'prov_a' not in provider_registry._classes
        assert 'prov_b' in provider_registry._classes

        # Cleanup
        provider_registry.unregister_plugin_providers('plugin-b')


# =============================================================================
# Backward Compatibility Tests
# =============================================================================

class TestBackwardCompat:
    """Test that old API still works."""

    def test_provider_classes_dict_accessible(self):
        """PROVIDER_CLASSES should still work as a dict."""
        assert 'claude' in PROVIDER_CLASSES
        assert 'openai' in PROVIDER_CLASSES
        assert PROVIDER_CLASSES['claude'] is ClaudeProvider

    def test_provider_metadata_proxy(self):
        """PROVIDER_METADATA should work as a dict-like proxy."""
        assert 'claude' in PROVIDER_METADATA
        meta = PROVIDER_METADATA.get('claude', {})
        assert meta.get('display_name') == 'Claude'

    def test_legacy_get_provider_works(self):
        """Legacy get_provider() should still work."""
        config = {
            "provider": "openai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "not-needed",
            "model": "test",
            "enabled": True,
        }
        provider = get_provider(config)
        assert provider is not None
        assert isinstance(provider, OpenAICompatProvider)

    def test_legacy_get_provider_disabled(self):
        """Legacy get_provider() should return None when disabled."""
        config = {"provider": "openai", "enabled": False}
        assert get_provider(config) is None

    def test_generation_params_returns_defaults(self):
        """get_generation_params should return sane defaults."""
        params = get_generation_params('claude', 'claude-sonnet-4-6', {})
        assert 'temperature' in params
        assert 'max_tokens' in params
        assert params['temperature'] == DEFAULT_GENERATION_PARAMS['temperature']

    def test_generation_params_instance_override(self):
        """Instance generation_params should override defaults."""
        config = {'claude': {'generation_params': {'temperature': 0.3, 'max_tokens': 8192}}}
        params = get_generation_params('claude', 'claude-sonnet-4-6', config)
        assert params['temperature'] == 0.3
        assert params['max_tokens'] == 8192


# =============================================================================
# Cross-Provider Message Compatibility
# =============================================================================

class TestCrossProviderCompat:
    """Test message format compatibility across providers."""

    def test_claude_tool_id_sanitization(self):
        """Claude should remap foreign tool IDs to toolu_ format."""
        from core.chat.llm_providers.claude import ClaudeProvider
        # Foreign ID (OpenAI style)
        result = ClaudeProvider._sanitize_tool_id("call_abc123def456")
        assert result.startswith("toolu_")
        assert len(result) == len("toolu_") + 24

    def test_claude_sanitize_deterministic(self):
        """Same foreign ID should always map to same toolu_ ID."""
        from core.chat.llm_providers.claude import ClaudeProvider
        id1 = ClaudeProvider._sanitize_tool_id("call_abc123")
        id2 = ClaudeProvider._sanitize_tool_id("call_abc123")
        assert id1 == id2

    def test_claude_native_id_passthrough(self):
        """Claude IDs should pass through unchanged."""
        from core.chat.llm_providers.claude import ClaudeProvider
        result = ClaudeProvider._sanitize_tool_id("toolu_existing_id_here")
        assert result == "toolu_existing_id_here"

    def test_openai_sanitize_messages_handles_claude_tool_results(self):
        """OpenAI provider should handle Claude-format tool results in history."""
        provider = OpenAICompatProvider({
            "provider": "openai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "test",
            "model": "test",
        })

        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "toolu_abc", "type": "function", "function": {"name": "test", "arguments": "{}"}}
            ]},
            # Claude-style tool result (role=user with tool_result block)
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_abc", "content": "result text"}
            ]},
        ]

        sanitized = provider._sanitize_messages(messages)
        # Should convert Claude tool_result to OpenAI tool format
        tool_msg = [m for m in sanitized if m.get('role') == 'tool']
        assert len(tool_msg) == 1
        assert tool_msg[0]['content'] == 'result text'

    def test_claude_convert_messages_handles_system(self):
        """Claude should extract system prompt from messages."""
        provider = ClaudeProvider({
            "provider": "claude",
            "api_key": "test",
            "model": "claude-sonnet-4-5",
        })

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]

        system, claude_msgs, needs_thinking_disabled, dynamic = provider._convert_messages(messages)
        assert system == "You are helpful"
        assert len(claude_msgs) == 1
        assert claude_msgs[0]["role"] == "user"

    def test_claude_convert_tools_format(self):
        """Claude should convert OpenAI tool format to input_schema."""
        provider = ClaudeProvider({
            "provider": "claude",
            "api_key": "test",
            "model": "claude-sonnet-4-5",
        })

        tools = [{
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}
            }
        }]

        converted = provider._convert_tools(tools)
        assert len(converted) == 1
        assert converted[0]["name"] == "test_tool"
        assert "input_schema" in converted[0]
        assert converted[0]["input_schema"]["properties"]["x"]["type"] == "string"

    def test_anthropic_compat_format_tool_result(self):
        """Anthropic-compat should format tool results as user with tool_result block."""
        provider = AnthropicCompatProvider({
            "provider": "anthropic",
            "base_url": "https://api.minimax.io/anthropic",
            "api_key": "test",
            "model": "test",
        })

        result = provider.format_tool_result("call_123", "my_func", "the result")
        assert result["role"] == "user"
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "call_123"

    def test_anthropic_compat_strips_images(self):
        """Anthropic-compat should filter image blocks (not supported)."""
        provider = AnthropicCompatProvider({
            "provider": "anthropic",
            "base_url": "https://test.com",
            "api_key": "test",
            "model": "test",
        })

        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "What's this?"},
                {"type": "image", "data": "base64data", "media_type": "image/jpeg"},
            ]},
        ]

        system, api_msgs = provider._convert_messages(messages)
        # Image should be filtered, text kept
        assert len(api_msgs) == 1
        content = api_msgs[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "text"


# =============================================================================
# Provider Creation from Registry
# =============================================================================

class TestProviderCreation:
    """Test creating providers through the registry."""

    def test_create_core_provider(self):
        """Should create a core provider instance."""
        config = {
            'claude': {
                'provider': 'claude',
                'api_key': 'test-key',
                'model': 'claude-sonnet-4-5',
                'enabled': True,
            }
        }
        provider = provider_registry.get_provider_by_key('claude', config)
        assert provider is not None
        assert isinstance(provider, ClaudeProvider)

    def test_create_custom_provider_by_template(self):
        """Should create custom provider using template field."""
        config = {
            'my-fireworks': {
                'template': 'openai',
                'base_url': 'https://api.fireworks.ai/inference/v1',
                'api_key': 'test-key',
                'model': 'deepseek-v3',
                'enabled': True,
            }
        }
        provider = provider_registry.get_provider_by_key('my-fireworks', config)
        assert provider is not None
        assert isinstance(provider, OpenAICompatProvider)

    def test_create_anthropic_compat_provider(self):
        """Should create Anthropic-compat provider from template."""
        config = {
            'minimax': {
                'template': 'anthropic',
                'base_url': 'https://api.minimax.io/anthropic',
                'api_key': 'test-key',
                'model': 'MiniMax-M2.5',
                'enabled': True,
            }
        }
        provider = provider_registry.get_provider_by_key('minimax', config)
        assert provider is not None
        assert isinstance(provider, AnthropicCompatProvider)

    def test_disabled_provider_returns_none(self):
        """Disabled provider should return None."""
        config = {
            'test': {
                'template': 'openai',
                'base_url': 'http://localhost:1234/v1',
                'enabled': False,
            }
        }
        assert provider_registry.get_provider_by_key('test', config) is None

    def test_unknown_key_returns_none(self):
        """Unknown provider key should return None."""
        assert provider_registry.get_provider_by_key('nonexistent', {}) is None

    def test_openai_responses_auto_select(self):
        """GPT-5.x models should auto-select Responses API."""
        config = {
            'my-openai': {
                'template': 'openai',
                'base_url': 'https://api.openai.com/v1',
                'api_key': 'test-key',
                'model': 'gpt-5.2',
                'enabled': True,
            }
        }
        provider = provider_registry.get_provider_by_key('my-openai', config)
        assert provider is not None
        from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
        assert isinstance(provider, OpenAIResponsesProvider)


# =============================================================================
# API Key Resolution
# =============================================================================

class TestAPIKeyResolution:
    """Test API key resolution across providers."""

    def test_local_provider_gets_not_needed(self):
        """Local providers should get 'not-needed' as key."""
        config = {'is_local': True}
        key = provider_registry.get_api_key('lmstudio', config)
        assert key == 'not-needed'

    def test_explicit_key_in_config(self):
        """Explicit api_key in config should work (backward compat)."""
        config = {'api_key': 'my-explicit-key'}
        key = provider_registry.get_api_key('test', config)
        assert key == 'my-explicit-key'

    def test_empty_key_returns_empty(self):
        """No key configured should return empty string."""
        config = {}
        key = provider_registry.get_api_key('nonexistent_provider_xyz', config)
        assert key == ''


# =============================================================================
# Config Merging
# =============================================================================

class TestConfigMerging:
    """Test that core and custom configs merge correctly."""

    def test_get_all_configs_merges(self):
        """Should merge LLM_PROVIDERS and LLM_CUSTOM_PROVIDERS."""
        configs = provider_registry._get_all_configs()
        # Should have core providers
        assert 'claude' in configs
        # Should also have custom providers (from migration)
        # (specific keys depend on user's settings)

    def test_get_all_providers_includes_is_core(self):
        """Each provider in the list should have is_core flag."""
        providers = provider_registry.get_all_providers()
        for p in providers:
            assert 'is_core' in p
            if p['key'] in ('claude', 'openai', 'gemini'):
                assert p['is_core'] is True


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_model_string(self):
        """Provider with empty model should still create."""
        config = {
            'test': {
                'template': 'openai',
                'base_url': 'http://localhost:1234/v1',
                'model': '',
                'enabled': True,
            }
        }
        provider = provider_registry.get_provider_by_key('test', config)
        assert provider is not None
        assert provider.model == ''

    def test_strip_penalties_config_flag(self):
        """strip_penalties config flag should affect param transformation."""
        provider = OpenAICompatProvider({
            "provider": "openai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "test",
            "model": "some-model",
            "strip_penalties": True,
        })

        params = provider._transform_params_for_model({
            'temperature': 0.7,
            'presence_penalty': 0.5,
            'frequency_penalty': 0.3,
        })

        assert 'presence_penalty' not in params
        assert 'frequency_penalty' not in params
        assert params['temperature'] == 0.7

    def test_generation_params_no_model_profiles(self):
        """Should work when MODEL_GENERATION_PROFILES doesn't exist."""
        params = provider_registry.get_generation_params('test', 'unknown-model', {})
        assert params == DEFAULT_GENERATION_PARAMS

    def test_tool_call_empty_arguments(self):
        """Tool call with empty arguments should work."""
        tc = ToolCall(id="tc1", name="get_time", arguments="{}")
        d = tc.to_dict()
        assert d["function"]["arguments"] == "{}"

    def test_provider_metadata_proxy_iteration(self):
        """PROVIDER_METADATA proxy should support iteration."""
        keys = list(PROVIDER_METADATA.keys())
        assert 'claude' in keys
        items = list(PROVIDER_METADATA.items())
        assert len(items) > 0

    def test_reserved_core_key_detection(self):
        """Core keys should be detected as reserved."""
        assert provider_registry.is_core_provider('claude')
        assert provider_registry.is_core_provider('openai')
        assert provider_registry.is_core_provider('gemini')
        assert not provider_registry.is_core_provider('my-custom')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
