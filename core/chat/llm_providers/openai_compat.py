# llm_providers/openai_compat.py
"""
OpenAI-compatible provider.

Handles:
- LM Studio (local)
- llama.cpp server (local)
- Fireworks.ai (cloud)
- OpenRouter (cloud)
- Any OpenAI-compatible API

This is the default provider and your 99% use case.
"""

import logging
from typing import Dict, Any, List, Optional, Generator

from openai import OpenAI

from .base import BaseProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class OpenAICompatProvider(BaseProvider):
    """
    Provider for OpenAI-compatible APIs.
    
    Works with any server implementing the OpenAI chat completions API:
    - POST /v1/chat/completions
    - GET /v1/models (for health check)
    """
    
    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)
        
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.request_timeout
        )
        logger.info(f"OpenAI-compat provider initialized: {self.base_url}")
    
    @property
    def provider_name(self) -> str:
        return self.config.get('provider', 'openai')
    
    @property
    def client(self) -> OpenAI:
        """Access the underlying OpenAI client if needed."""
        return self._client
    
    def health_check(self) -> bool:
        """Check endpoint health via models.list()."""
        try:
            self._client.models.list(timeout=self.health_check_timeout)
            return True
        except Exception as e:
            logger.debug(f"Health check failed for {self.base_url}: {e}")
            return False
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Send non-streaming chat completion request."""
        
        params = generation_params or {}
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            **params
        }
        
        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            request_kwargs["tool_choice"] = "auto"
        
        response = self._client.chat.completions.create(**request_kwargs)
        
        return self._parse_response(response)
    
    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Send streaming chat completion request."""
        
        params = generation_params or {}
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **params
        }
        
        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            request_kwargs["tool_choice"] = "auto"
        
        stream = self._client.chat.completions.create(**request_kwargs)
        
        # Track accumulated state for final response
        full_content = ""
        tool_calls_acc = []  # List of dicts being built
        finish_reason = None
        
        for chunk in stream:
            if not chunk.choices:
                continue
            
            choice = chunk.choices[0]
            delta = choice.delta
            
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            
            # Content chunk
            if delta.content:
                full_content += delta.content
                yield {"type": "content", "text": delta.content}
            
            # Tool call chunks
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    
                    # Expand list if needed
                    while len(tool_calls_acc) <= idx:
                        tool_calls_acc.append({
                            "id": "",
                            "name": "",
                            "arguments": ""
                        })
                    
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        tool_calls_acc[idx]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments
                    
                    yield {
                        "type": "tool_call",
                        "index": idx,
                        "id": tool_calls_acc[idx]["id"],
                        "name": tool_calls_acc[idx]["name"],
                        "arguments": tool_calls_acc[idx]["arguments"]
                    }
        
        # Build final response
        final_tool_calls = [
            ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
            for tc in tool_calls_acc
            if tc["id"] and tc["name"]
        ]
        
        final_response = LLMResponse(
            content=full_content if full_content else None,
            tool_calls=final_tool_calls,
            finish_reason=finish_reason
        )
        
        yield {"type": "done", "response": final_response}
    
    def _parse_response(self, response) -> LLMResponse:
        """Parse OpenAI response into normalized LLMResponse."""
        
        choice = response.choices[0]
        message = choice.message
        
        # Parse tool calls if present
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments
                ))
        
        # Parse usage if present
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage
        )