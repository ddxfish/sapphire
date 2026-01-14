"""
Phase 2: LLM Provider & Chat Core Tests

Tests the LLM abstraction layer and chat orchestration.
Focus on data structures, message formatting, and provider dispatch.

Run with: pytest tests/test_llm_providers.py -v
"""
import pytest
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.chat.llm_providers.base import ToolCall, LLMResponse, BaseProvider
from core.chat.llm_providers import (
    get_provider, 
    get_provider_for_url, 
    get_provider_by_key,
    get_first_available_provider,
    get_generation_params
)


# =============================================================================
# ToolCall Dataclass Tests
# =============================================================================

class TestToolCall:
    """Test ToolCall dataclass functionality."""
    
    def test_tool_call_creation(self):
        """Should create ToolCall with all fields."""
        tc = ToolCall(
            id="call_123",
            name="web_search",
            arguments='{"query": "test"}'
        )
        
        assert tc.id == "call_123"
        assert tc.name == "web_search"
        assert tc.arguments == '{"query": "test"}'
    
    def test_tool_call_to_dict(self):
        """Should convert to OpenAI-style dict format."""
        tc = ToolCall(
            id="call_456",
            name="get_weather",
            arguments='{"location": "NYC"}'
        )
        
        result = tc.to_dict()
        
        assert result["id"] == "call_456"
        assert result["type"] == "function"
        assert result["function"]["name"] == "get_weather"
        assert result["function"]["arguments"] == '{"location": "NYC"}'
    
    def test_tool_call_with_complex_arguments(self):
        """Should handle complex JSON arguments."""
        args = json.dumps({
            "filters": {"type": "image", "size": "large"},
            "limit": 10,
            "nested": {"deep": {"value": True}}
        })
        
        tc = ToolCall(id="call_complex", name="search", arguments=args)
        
        # Verify arguments can be parsed back
        parsed = json.loads(tc.arguments)
        assert parsed["filters"]["type"] == "image"
        assert parsed["nested"]["deep"]["value"] is True


# =============================================================================
# LLMResponse Dataclass Tests
# =============================================================================

class TestLLMResponse:
    """Test LLMResponse dataclass functionality."""
    
    def test_response_with_content_only(self):
        """Should create response with just content."""
        resp = LLMResponse(content="Hello, world!")
        
        assert resp.content == "Hello, world!"
        assert resp.tool_calls == []
        assert resp.has_tool_calls is False
        assert resp.finish_reason is None
    
    def test_response_with_tool_calls(self):
        """Should create response with tool calls."""
        tool_calls = [
            ToolCall(id="tc1", name="func1", arguments="{}"),
            ToolCall(id="tc2", name="func2", arguments='{"x": 1}'),
        ]
        
        resp = LLMResponse(
            content=None,
            tool_calls=tool_calls,
            finish_reason="tool_calls"
        )
        
        assert resp.content is None
        assert len(resp.tool_calls) == 2
        assert resp.has_tool_calls is True
        assert resp.finish_reason == "tool_calls"
    
    def test_response_get_tool_calls_as_dicts(self):
        """Should convert tool calls to dict format."""
        tool_calls = [
            ToolCall(id="tc1", name="search", arguments='{"q": "test"}'),
        ]
        
        resp = LLMResponse(tool_calls=tool_calls)
        dicts = resp.get_tool_calls_as_dicts()
        
        assert len(dicts) == 1
        assert dicts[0]["id"] == "tc1"
        assert dicts[0]["function"]["name"] == "search"
    
    def test_response_with_usage(self):
        """Should track token usage."""
        resp = LLMResponse(
            content="Test",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        )
        
        assert resp.usage["prompt_tokens"] == 100
        assert resp.usage["total_tokens"] == 150
    
    def test_response_empty_tool_calls_not_has_tool_calls(self):
        """Empty tool_calls list should return has_tool_calls=False."""
        resp = LLMResponse(content="Hi", tool_calls=[])
        assert resp.has_tool_calls is False


# =============================================================================
# Provider URL Detection Tests
# =============================================================================

class TestProviderURLDetection:
    """Test automatic provider detection from URLs."""
    
    def test_detect_claude_from_url(self):
        """Should detect Claude from Anthropic URL."""
        provider = get_provider_for_url("https://api.anthropic.com/v1")
        assert provider == "claude"
    
    def test_detect_fireworks_from_url(self):
        """Should detect Fireworks from fireworks.ai URL."""
        provider = get_provider_for_url("https://api.fireworks.ai/v1")
        assert provider == "fireworks"
    
    def test_detect_openai_from_localhost(self):
        """Should detect openai provider from localhost."""
        provider = get_provider_for_url("http://localhost:1234/v1")
        assert provider == "openai"
        
        provider2 = get_provider_for_url("http://127.0.0.1:8080/v1")
        assert provider2 == "openai"
    
    def test_detect_openai_from_openai_url(self):
        """Should detect openai provider from OpenAI URL."""
        provider = get_provider_for_url("https://api.openai.com/v1")
        assert provider == "openai"
    
    def test_unknown_url_defaults_to_openai(self):
        """Unknown URLs should default to openai."""
        provider = get_provider_for_url("https://some-random-api.com/v1")
        assert provider == "openai"


# =============================================================================
# Provider Factory Tests
# =============================================================================

class TestProviderFactory:
    """Test provider instantiation."""
    
    def test_get_provider_openai(self):
        """Should create OpenAI-compatible provider."""
        llm_config = {
            "provider": "openai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "not-needed",
            "model": "local-model",
            "enabled": True
        }
        
        provider = get_provider(llm_config)
        
        assert provider is not None
        from core.chat.llm_providers.openai_compat import OpenAICompatProvider
        assert isinstance(provider, OpenAICompatProvider)
    
    def test_get_provider_disabled_returns_none(self):
        """Disabled provider should return None."""
        llm_config = {
            "provider": "openai",
            "base_url": "http://localhost:1234/v1",
            "enabled": False
        }
        
        provider = get_provider(llm_config)
        
        assert provider is None
    
    def test_get_provider_unknown_defaults_to_openai(self):
        """Unknown provider type should default to openai."""
        llm_config = {
            "provider": "nonexistent_provider",
            "base_url": "http://example.com",
            "enabled": True
        }
        
        provider = get_provider(llm_config)
        
        # Implementation defaults unknown providers to openai
        from core.chat.llm_providers.openai_compat import OpenAICompatProvider
        assert isinstance(provider, OpenAICompatProvider)
    
    def test_get_provider_claude_requires_sdk(self):
        """Claude provider should fail gracefully without anthropic SDK."""
        llm_config = {
            "provider": "claude",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "test-key",
            "model": "claude-3-sonnet",
            "enabled": True
        }
        
        # This will return None if anthropic SDK not installed
        # or a ClaudeProvider if it is
        provider = get_provider(llm_config)
        
        # Either result is acceptable depending on environment
        if provider is not None:
            from core.chat.llm_providers.claude import ClaudeProvider
            assert isinstance(provider, ClaudeProvider)


# =============================================================================
# Generation Params Tests
# =============================================================================

class TestGenerationParams:
    """Test generation parameter handling."""
    
    def test_get_generation_params_defaults(self):
        """Should return default generation params."""
        # The function takes provider_key, model, and providers_config
        params = get_generation_params("claude", "claude-3-sonnet", {})
        
        # Should have default values
        assert "temperature" in params
        assert "max_tokens" in params
        assert isinstance(params["temperature"], (int, float))
    
    def test_get_generation_params_has_required_keys(self):
        """Should include all required generation parameters."""
        params = get_generation_params("openai", "gpt-4", {})
        
        required_keys = ["temperature", "max_tokens"]
        for key in required_keys:
            assert key in params, f"Missing required key: {key}"


# =============================================================================
# Base Provider Tests
# =============================================================================

class TestBaseProvider:
    """Test BaseProvider default implementations."""
    
    def test_format_tool_result_openai_format(self):
        """Default format_tool_result should return OpenAI format."""
        # Create a concrete subclass for testing
        class TestProvider(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, messages, tools=None, generation_params=None):
                return LLMResponse(content="test")
            def chat_completion_stream(self, messages, tools=None, generation_params=None):
                yield {"type": "done", "response": LLMResponse(content="test")}
        
        provider = TestProvider({"provider": "test"})
        
        result = provider.format_tool_result(
            tool_call_id="call_123",
            function_name="search",
            result="Search results here"
        )
        
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_123"
        assert result["name"] == "search"
        assert result["content"] == "Search results here"
    
    def test_convert_tools_strips_internal_fields(self):
        """Should strip internal fields like 'network' from tools."""
        class TestProvider(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, messages, tools=None, generation_params=None):
                return LLMResponse(content="test")
            def chat_completion_stream(self, messages, tools=None, generation_params=None):
                yield {"type": "done", "response": LLMResponse(content="test")}
        
        provider = TestProvider({"provider": "test"})
        
        tools = [
            {
                "type": "function",
                "network": True,  # Internal field
                "function": {"name": "web_search", "parameters": {}}
            },
            {
                "type": "function",
                "function": {"name": "local_tool", "parameters": {}}
            }
        ]
        
        converted = provider.convert_tools_for_api(tools)
        
        assert len(converted) == 2
        assert "network" not in converted[0]
        assert converted[0]["function"]["name"] == "web_search"


# =============================================================================
# Provider Fallback Tests
# =============================================================================

class TestProviderFallback:
    """Test provider fallback logic."""
    
    def test_get_first_available_provider_function_exists(self):
        """Should have get_first_available_provider function."""
        assert callable(get_first_available_provider)
    
    def test_provider_by_key_returns_none_for_unknown(self):
        """Should return None for unknown provider key."""
        providers_config = {
            "known": {"provider": "openai", "enabled": True}
        }
        
        result = get_provider_by_key("unknown_key", providers_config)
        assert result is None


# =============================================================================
# Chat Core Integration Tests
# =============================================================================

class TestChatCoreIntegration:
    """Integration tests for LLMChat class."""
    
    def test_llm_chat_imports(self):
        """LLMChat should import without errors."""
        from core.chat.chat import LLMChat
        assert LLMChat is not None
    
    def test_llm_chat_has_expected_methods(self):
        """LLMChat should have all expected public methods."""
        from core.chat.chat import LLMChat
        
        expected_methods = [
            'set_system_prompt',
            'get_system_prompt_template',
            'chat',
            'chat_stream',
        ]
        
        for method in expected_methods:
            assert hasattr(LLMChat, method), f"Missing method: {method}"
    
    def test_llm_chat_class_exists(self):
        """LLMChat class should be importable and have __init__."""
        from core.chat.chat import LLMChat
        
        # Verify it's a class with __init__
        assert callable(LLMChat)
        assert hasattr(LLMChat, '__init__')


# =============================================================================
# Message Format Tests
# =============================================================================

class TestMessageFormats:
    """Test message format handling across providers."""
    
    def test_openai_message_format(self):
        """OpenAI format should pass through unchanged."""
        class TestProvider(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, messages, tools=None, generation_params=None):
                return LLMResponse(content="test")
            def chat_completion_stream(self, messages, tools=None, generation_params=None):
                yield {"type": "done", "response": LLMResponse(content="test")}
        
        provider = TestProvider({"provider": "test"})
        
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        
        converted = provider.convert_messages_for_api(messages)
        
        # Default implementation passes through
        assert converted == messages
    
    def test_tool_result_message_format(self):
        """Tool result messages should have correct structure."""
        class TestProvider(BaseProvider):
            def health_check(self): return True
            def chat_completion(self, messages, tools=None, generation_params=None):
                return LLMResponse(content="test")
            def chat_completion_stream(self, messages, tools=None, generation_params=None):
                yield {"type": "done", "response": LLMResponse(content="test")}
        
        provider = TestProvider({"provider": "test"})
        
        result = provider.format_tool_result(
            tool_call_id="call_abc123",
            function_name="get_time",
            result="The current time is 3:00 PM"
        )
        
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_abc123"
        assert "3:00 PM" in result["content"]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])