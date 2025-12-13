# llm_providers/base.py
"""
Base provider interface for LLM abstraction.

All providers must implement these methods to ensure consistent behavior
across OpenAI-compatible APIs, Claude, and others.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Generator

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Normalized tool call representation."""
    id: str
    name: str
    arguments: str  # JSON string
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to OpenAI-style dict format (used internally)."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments
            }
        }


@dataclass
class LLMResponse:
    """
    Normalized LLM response that works across all providers.
    
    This is what chat.py sees - regardless of whether the underlying
    provider is OpenAI, Claude, or something else.
    """
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None  # {prompt_tokens, completion_tokens, total_tokens}
    
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
    
    def get_tool_calls_as_dicts(self) -> List[Dict[str, Any]]:
        """Get tool calls in OpenAI dict format for history/messages."""
        return [tc.to_dict() for tc in self.tool_calls]


class BaseProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    Implementations handle the specifics of each API while exposing
    a consistent interface to the rest of the application.
    """
    
    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        """
        Initialize provider with config.
        
        Args:
            llm_config: Dict containing base_url, api_key, model, timeout, enabled
            request_timeout: Overall request timeout
        """
        self.config = llm_config
        self.base_url = llm_config.get('base_url', '')
        self.api_key = llm_config.get('api_key', '')
        self.model = llm_config.get('model', '')
        self.health_check_timeout = llm_config.get('timeout', 0.5)
        self.request_timeout = request_timeout
        self._client = None
    
    @property
    def provider_name(self) -> str:
        """Return provider identifier string."""
        return self.config.get('provider', 'unknown')
    
    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if the provider endpoint is reachable.
        
        Returns:
            True if healthy, False otherwise
        """
        pass
    
    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """
        Send a chat completion request (non-streaming).
        
        Args:
            messages: List of message dicts with role/content
            tools: Optional list of tool definitions (OpenAI format)
            generation_params: Optional dict with max_tokens, temperature, etc.
        
        Returns:
            LLMResponse with content and/or tool_calls
        """
        pass
    
    @abstractmethod
    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send a streaming chat completion request.
        
        Args:
            messages: List of message dicts with role/content
            tools: Optional list of tool definitions (OpenAI format)
            generation_params: Optional dict with max_tokens, temperature, etc.
        
        Yields:
            Dicts with either:
                {"type": "content", "text": "..."} for text chunks
                {"type": "tool_call", "index": N, "id": "...", "name": "...", "arguments": "..."} for tool calls
                {"type": "done", "response": LLMResponse} for final response
        """
        pass
    
    def format_tool_result(
        self,
        tool_call_id: str,
        function_name: str,
        result: str
    ) -> Dict[str, Any]:
        """
        Format a tool result message for this provider.
        
        Default implementation returns OpenAI format.
        Claude provider overrides this.
        
        Args:
            tool_call_id: The tool call ID to respond to
            function_name: Name of the function that was called
            result: The result string
        
        Returns:
            Message dict to append to conversation
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": function_name,
            "content": result
        }
    
    def convert_messages_for_api(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert messages to provider-specific format if needed.
        
        Default implementation passes through unchanged (OpenAI format).
        Claude provider overrides this.
        
        Args:
            messages: Messages in OpenAI format
        
        Returns:
            Messages in provider-specific format
        """
        return messages
    
    def convert_tools_for_api(
        self,
        tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert tool definitions to provider-specific format if needed.
        
        Default implementation passes through unchanged (OpenAI format).
        Claude provider overrides this.
        
        Args:
            tools: Tool definitions in OpenAI format
        
        Returns:
            Tools in provider-specific format
        """
        return tools