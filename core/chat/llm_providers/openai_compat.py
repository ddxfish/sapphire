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
    
    def _transform_params_for_model(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Transform generation params for model compatibility.
        
        GPT-5+ and o1/o3 reasoning models:
        - Use max_completion_tokens instead of max_tokens
        - Don't support temperature, top_p, presence_penalty, frequency_penalty
        
        This handles conversions transparently so callers don't need to care.
        """
        if not params:
            return params
        
        result = dict(params)
        model_lower = (self.model or '').lower()
        
        # Detect reasoning models (GPT-5+, o1, o3)
        is_reasoning_model = (
            model_lower.startswith('gpt-5') or 
            model_lower.startswith('o1') or 
            model_lower.startswith('o3')
        )
        
        if is_reasoning_model:
            # max_tokens → max_completion_tokens
            if 'max_tokens' in result:
                result['max_completion_tokens'] = result.pop('max_tokens')
            
            # Remove unsupported sampling params (reasoning models don't use these)
            removed = []
            for unsupported in ['temperature', 'top_p', 'presence_penalty', 'frequency_penalty']:
                if unsupported in result:
                    result.pop(unsupported)
                    removed.append(unsupported)
            
            if removed:
                logger.debug(f"Filtered unsupported params for {self.model}: {removed}")
        
        return result
    
    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sanitize messages for OpenAI-compatible APIs.
        
        Handles cross-provider compatibility:
        - Strips Claude-specific fields (thinking_raw, thinking, metadata)
        - Converts content lists to strings (Claude uses content blocks)
        - Normalizes tool results from Claude format to OpenAI format
        - Ensures proper message structure for tool calls
        """
        clean = []
        
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content')
            
            # Handle Claude-format tool results: {"role": "user", "content": [{"type": "tool_result", ...}]}
            # Convert to OpenAI format: {"role": "tool", "tool_call_id": ..., "content": ...}
            if role == 'user' and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        clean.append({
                            'role': 'tool',
                            'tool_call_id': block.get('tool_use_id', ''),
                            'name': block.get('name', 'unknown'),
                            'content': block.get('content', '')
                        })
                # If we processed tool_result blocks, skip the original message
                if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
                    continue
            
            # Normalize content - ensure it's a string, not a list
            if isinstance(content, list):
                # Extract text from content blocks (Claude format)
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            text_parts.append(block.get('text', ''))
                        elif block.get('type') == 'thinking':
                            # Skip thinking blocks - they shouldn't be sent to other providers
                            continue
                        elif block.get('type') == 'tool_use':
                            # Tool use blocks are handled via tool_calls field
                            continue
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = ' '.join(text_parts).strip()
            
            # Build clean message with only allowed fields
            clean_msg = {'role': role}
            
            # Handle content
            if content is not None:
                clean_msg['content'] = str(content) if content else ''
            elif 'tool_calls' in msg:
                # OpenAI requires content field, use empty string if tool_calls present
                clean_msg['content'] = ''
            else:
                clean_msg['content'] = ''
            
            # Handle tool_calls (assistant messages)
            if msg.get('tool_calls'):
                # Normalize tool_calls format
                normalized_calls = []
                for tc in msg['tool_calls']:
                    if isinstance(tc, dict):
                        # Ensure proper structure
                        normalized_tc = {
                            'id': tc.get('id', ''),
                            'type': 'function',
                            'function': tc.get('function', {})
                        }
                        # Ensure function has name and arguments
                        if 'name' in tc and 'function' not in tc:
                            normalized_tc['function'] = {
                                'name': tc.get('name', ''),
                                'arguments': tc.get('arguments', '{}')
                            }
                        normalized_calls.append(normalized_tc)
                if normalized_calls:
                    clean_msg['tool_calls'] = normalized_calls
            
            # Handle tool results (tool messages)
            if role == 'tool':
                clean_msg['tool_call_id'] = msg.get('tool_call_id', '')
                clean_msg['name'] = msg.get('name', 'unknown')
            
            # Include name field for function calls if present
            if msg.get('name') and role != 'tool':
                clean_msg['name'] = msg['name']
            
            clean.append(clean_msg)
        
        return clean
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Send non-streaming chat completion request."""
        
        params = self._transform_params_for_model(generation_params or {})
        
        # Sanitize messages - only keep fields the OpenAI API understands
        clean_messages = self._sanitize_messages(messages)
        
        request_kwargs = {
            "model": self.model,
            "messages": clean_messages,
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
        
        params = self._transform_params_for_model(generation_params or {})
        
        # Sanitize messages - only keep fields the OpenAI API understands
        clean_messages = self._sanitize_messages(messages)
        
        request_kwargs = {
            "model": self.model,
            "messages": clean_messages,
            "stream": True,
            **params
        }
        
        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            request_kwargs["tool_choice"] = "auto"
        
        # Debug log message count and structure
        logger.debug(f"[SANITIZE] Sending {len(clean_messages)} messages to {self.model}")
        for i, msg in enumerate(clean_messages[:3]):  # Log first 3 for brevity
            logger.debug(f"  [{i}] role={msg.get('role')}, content_type={type(msg.get('content')).__name__}, "
                        f"has_tool_calls={'tool_calls' in msg}")
        
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