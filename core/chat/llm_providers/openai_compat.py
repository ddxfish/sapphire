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

from .base import BaseProvider, LLMResponse, ToolCall, retry_on_rate_limit

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
    def supports_images(self) -> bool:
        """Whether this provider instance supports vision/image inputs."""
        return self._supports_multimodal()
    
    def _supports_multimodal(self) -> bool:
        """
        Check if this specific provider instance supports multimodal (image) inputs.
        
        Conservative approach: only enable for known vision-capable endpoints.
        Local models (LM Studio, llama.cpp) typically don't support multimodal
        unless running specific VLM models.
        """
        base_url = (self.base_url or '').lower()
        model = (self.model or '').lower()
        
        # OpenAI official API - supports vision with gpt-4-vision, gpt-4o, etc.
        if 'api.openai.com' in base_url:
            logger.debug(f"[MULTIMODAL] OpenAI API detected, enabling multimodal")
            return True
        
        # Fireworks - supports vision with specific VLM models
        if 'fireworks.ai' in base_url:
            # Check for known vision models
            vision_indicators = ['llava', 'vision', 'vl', 'pixtral', 'qwen2-vl']
            supported = any(ind in model for ind in vision_indicators)
            logger.debug(f"[MULTIMODAL] Fireworks: model={model}, multimodal={supported}")
            return supported
        
        # OpenRouter - check model name for vision capability
        if 'openrouter.ai' in base_url:
            vision_indicators = ['vision', 'vl', 'llava', 'pixtral', 'gpt-4o']
            supported = any(ind in model for ind in vision_indicators)
            logger.debug(f"[MULTIMODAL] OpenRouter: model={model}, multimodal={supported}")
            return supported
        
        # Local endpoints (LM Studio, llama.cpp, etc.) - check model name
        if any(local in base_url for local in ['localhost', '127.0.0.1', '0.0.0.0']):
            # Only enable if model name suggests vision capability
            vision_indicators = ['llava', 'vision', 'vl', 'bakllava', 'cogvlm', 'minicpm-v']
            supported = any(ind in model for ind in vision_indicators)
            logger.debug(f"[MULTIMODAL] Local endpoint: model={model}, multimodal={supported}")
            return supported
        
        # Unknown endpoint - be conservative, disable multimodal
        logger.debug(f"[MULTIMODAL] Unknown endpoint {base_url}, disabling multimodal")
        return False
    
    def _is_fireworks_reasoning_model(self) -> bool:
        """
        Check if this is a Fireworks reasoning model that needs reasoning_effort param.
        
        These models output thinking in reasoning_content field when reasoning_effort is set.
        """
        base_url = (self.base_url or '').lower()
        model = (self.model or '').lower()
        
        if 'fireworks.ai' not in base_url:
            return False
        
        # Models known to support reasoning_effort parameter
        reasoning_indicators = [
            'deepseek',      # DeepSeek V3
            'glm',           # GLM 4.7
            'kimi',          # Kimi K2
            'thinking',      # Any model with "thinking" in name
            'qwq',           # QwQ
        ]
        
        return any(ind in model for ind in reasoning_indicators)
    
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
                        tool_use_id = block.get('tool_use_id', '')
                        # Convert Claude tool ID format if needed
                        if tool_use_id.startswith('toolu_'):
                            tool_use_id = 'call_' + tool_use_id[6:]
                        clean.append({
                            'role': 'tool',
                            'tool_call_id': tool_use_id,
                            'name': block.get('name', 'unknown'),
                            'content': block.get('content', '')
                        })
                # If we processed tool_result blocks, skip the original message
                if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
                    continue
            
            # Normalize content - handle multimodal content lists
            if isinstance(content, list):
                # Check if content has images
                has_images = any(
                    isinstance(b, dict) and b.get('type') == 'image' 
                    for b in content
                )
                
                if has_images and self._supports_multimodal():
                    # Convert to OpenAI multimodal format for capable providers
                    openai_content = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                openai_content.append({"type": "text", "text": block.get('text', '')})
                            elif block.get('type') == 'image':
                                # Internal: {"type": "image", "data": "...", "media_type": "..."}
                                # OpenAI:   {"type": "image_url", "image_url": {"url": "data:...;base64,..."}}
                                media_type = block.get('media_type', 'image/jpeg')
                                data = block.get('data', '')
                                openai_content.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{media_type};base64,{data}"}
                                })
                            elif block.get('type') in ('thinking', 'tool_use'):
                                # Skip thinking and tool_use blocks
                                continue
                        elif isinstance(block, str):
                            openai_content.append({"type": "text", "text": block})
                    content = openai_content if openai_content else ""
                else:
                    # No images OR provider doesn't support multimodal - flatten to string
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
                            elif block.get('type') == 'image':
                                # Provider doesn't support images - add placeholder
                                text_parts.append('[image]')
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = ' '.join(text_parts).strip()
            
            # Build clean message with only allowed fields
            clean_msg = {'role': role}
            
            # Handle content - preserve list for multimodal, stringify otherwise
            if content is not None:
                if isinstance(content, list):
                    clean_msg['content'] = content  # Keep list for multimodal
                else:
                    clean_msg['content'] = str(content) if content else ''
            elif 'tool_calls' in msg:
                # OpenAI requires content field, use empty string if tool_calls present
                clean_msg['content'] = ''
            else:
                clean_msg['content'] = ''
            
            # Handle tool_calls (assistant messages)
            if msg.get('tool_calls'):
                # Normalize tool_calls format for OpenAI-compat APIs
                normalized_calls = []
                for tc in msg['tool_calls']:
                    if isinstance(tc, dict):
                        func = tc.get('function', {})
                        
                        # Get arguments - ensure it's valid JSON
                        args = func.get('arguments', '{}')
                        if not args or args == '':
                            args = '{}'
                        
                        # Get tool ID - convert Claude format if needed
                        tool_id = tc.get('id', '')
                        if tool_id.startswith('toolu_'):
                            # Convert Claude ID to OpenAI-compatible format
                            tool_id = 'call_' + tool_id[6:]
                        
                        normalized_tc = {
                            'id': tool_id,
                            'type': 'function',
                            'function': {
                                'name': func.get('name', ''),
                                'arguments': args
                            }
                        }
                        normalized_calls.append(normalized_tc)
                if normalized_calls:
                    clean_msg['tool_calls'] = normalized_calls
            
            # Handle tool results (tool messages)
            if role == 'tool':
                tool_call_id = msg.get('tool_call_id', '')
                # Convert Claude tool ID format if needed
                if tool_call_id.startswith('toolu_'):
                    tool_call_id = 'call_' + tool_call_id[6:]
                clean_msg['tool_call_id'] = tool_call_id
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
        
        logger.debug(f"[OPENAI-COMPAT] Non-streaming: {len(clean_messages)} messages to {self.model}")
        
        request_kwargs = {
            "model": self.model,
            "messages": clean_messages,
            **params
        }
        
        # Add reasoning_effort for Fireworks reasoning models
        if self._is_fireworks_reasoning_model():
            request_kwargs["reasoning_effort"] = params.get("reasoning_effort", "medium")
        
        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            request_kwargs["tool_choice"] = "auto"
        
        # Wrap in retry for rate limiting
        response = retry_on_rate_limit(
            self._client.chat.completions.create,
            **request_kwargs
        )
        
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
        
        # DEBUG: Log message structure (reduced verbosity)
        logger.info(f"[OPENAI-COMPAT] Sending {len(clean_messages)} messages to {self.base_url} model={self.model}")
        logger.debug(f"[OPENAI-COMPAT] Multimodal supported: {self._supports_multimodal()}")
        for i, msg in enumerate(clean_messages):
            role = msg.get('role')
            content = msg.get('content')
            content_type = type(content).__name__
            has_tc = 'tool_calls' in msg
            
            if isinstance(content, str):
                preview = content[:60] + '...' if len(content) > 60 else content
            elif isinstance(content, list):
                preview = f"[list with {len(content)} items]"
            else:
                preview = str(content)[:60]
            
            logger.debug(f"[OPENAI-COMPAT]   [{i}] role={role}, content_type={content_type}, has_tool_calls={has_tc}, preview={preview}")
        
        request_kwargs = {
            "model": self.model,
            "messages": clean_messages,
            "stream": True,
            **params
        }
        
        # Add reasoning_effort for Fireworks reasoning models to enable thinking output
        if self._is_fireworks_reasoning_model():
            request_kwargs["reasoning_effort"] = params.get("reasoning_effort", "medium")
            logger.info(f"[REASONING] Enabled reasoning_effort={request_kwargs['reasoning_effort']} for {self.model}")
        
        if tools:
            request_kwargs["tools"] = self.convert_tools_for_api(tools)
            request_kwargs["tool_choice"] = "auto"
        
        logger.info(f"[OPENAI-COMPAT] Request params: model={request_kwargs.get('model')}, tools={len(request_kwargs.get('tools', []))}")
        
        # Wrap in retry for rate limiting
        try:
            stream = retry_on_rate_limit(
                self._client.chat.completions.create,
                **request_kwargs
            )
        except Exception as e:
            # Log summary of what failed
            logger.error(f"[OPENAI-COMPAT] REQUEST FAILED: {e}")
            logger.error(f"[OPENAI-COMPAT] Message count: {len(clean_messages)}, has tool_calls: {any('tool_calls' in m for m in clean_messages)}")
            raise
        
        # Track accumulated state for final response
        full_content = ""
        full_thinking = ""
        tool_calls_acc = []  # List of dicts being built
        finish_reason = None
        
        for chunk in stream:
            if not chunk.choices:
                continue
            
            choice = chunk.choices[0]
            delta = choice.delta
            
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            
            # Reasoning content (Fireworks GLM, DeepSeek, etc.)
            # These models return thinking in reasoning_content field instead of <think> tags
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                full_thinking += reasoning
                yield {"type": "thinking", "text": reasoning}
            
            # Debug: log all delta attributes on first content chunk to diagnose missing reasoning
            if delta.content and not full_content:
                delta_attrs = [a for a in dir(delta) if not a.startswith('_')]
                logger.debug(f"[REASONING] Delta attributes: {delta_attrs}")
                # Check for alternative reasoning field names
                for attr in ['reasoning', 'reasoning_content', 'thought', 'thinking']:
                    val = getattr(delta, attr, None)
                    if val:
                        logger.info(f"[REASONING] Found {attr}: {val[:100]}...")
            
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
        
        done_event = {"type": "done", "response": final_response}
        if full_thinking:
            done_event["thinking"] = full_thinking
        yield done_event
    
    def _parse_response(self, response) -> LLMResponse:
        """Parse OpenAI response into normalized LLMResponse."""
        
        choice = response.choices[0]
        message = choice.message
        
        # Check for reasoning_content (Fireworks reasoning models)
        reasoning = getattr(message, 'reasoning_content', None)
        if reasoning:
            logger.info(f"[REASONING] Non-stream response has reasoning_content ({len(reasoning)} chars)")
        
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