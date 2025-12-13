# llm_providers/claude.py
"""
Anthropic Claude provider.

Handles Claude-specific API differences:
- Different authentication header (x-api-key)
- Different message format for tool use
- Different streaming event format
- System prompt handling
- Tool result format differences
"""

import json
import logging
import uuid
from typing import Dict, Any, List, Optional, Generator

from .base import BaseProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# Try to import anthropic SDK
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic SDK not installed. Run: pip install anthropic")


class ClaudeProvider(BaseProvider):
    """
    Provider for Anthropic Claude API.
    
    Key differences from OpenAI:
    - Uses x-api-key header instead of Authorization: Bearer
    - System prompt is a separate parameter, not a message
    - Tool calls come as content blocks, not separate field
    - Tool results use role: "user" with tool_result block
    - Streaming uses different event types
    """
    
    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)
        
        if not ANTHROPIC_AVAILABLE:
            raise ImportError("anthropic SDK not installed. Run: pip install anthropic")
        
        # Claude uses api.anthropic.com by default
        base_url = self.base_url or "https://api.anthropic.com"
        
        self._client = anthropic.Anthropic(
            api_key=self.api_key,
            base_url=base_url,
            timeout=self.request_timeout
        )
        logger.info(f"Claude provider initialized: {base_url}")
    
    @property
    def provider_name(self) -> str:
        return 'claude'
    
    def health_check(self) -> bool:
        """
        Check Claude endpoint health.
        
        Claude doesn't have a models.list endpoint, so we do a minimal
        messages request with max_tokens=1.
        """
        try:
            self._client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
                timeout=self.health_check_timeout
            )
            return True
        except anthropic.APIStatusError as e:
            # 400/401/etc means API is reachable, just rejected our minimal request
            # That's fine for health check - we know it's up
            if e.status_code in (400, 401, 403):
                return True
            logger.debug(f"Claude health check failed: {e}")
            return False
        except Exception as e:
            logger.debug(f"Claude health check failed: {e}")
            return False
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Send non-streaming chat completion to Claude."""
        
        params = generation_params or {}
        
        # Extract system prompt from messages
        system_prompt, claude_messages = self._convert_messages(messages)
        
        request_kwargs = {
            "model": self.model,
            "messages": claude_messages,
            "max_tokens": params.get("max_tokens", 4096),
        }
        
        if system_prompt:
            request_kwargs["system"] = system_prompt
        
        # Add optional params (Claude doesn't allow both temperature and top_p)
        if "temperature" in params:
            request_kwargs["temperature"] = params["temperature"]
        
        # Convert and add tools
        if tools:
            request_kwargs["tools"] = self._convert_tools(tools)
        
        response = self._client.messages.create(**request_kwargs)
        
        return self._parse_response(response)
    
    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Send streaming chat completion to Claude."""
        
        params = generation_params or {}
        
        # Extract system prompt from messages
        system_prompt, claude_messages = self._convert_messages(messages)
        
        request_kwargs = {
            "model": self.model,
            "messages": claude_messages,
            "max_tokens": params.get("max_tokens", 4096),
        }
        
        if system_prompt:
            request_kwargs["system"] = system_prompt
        
        # Add optional params (Claude doesn't allow both temperature and top_p)
        if "temperature" in params:
            request_kwargs["temperature"] = params["temperature"]
        
        if tools:
            request_kwargs["tools"] = self._convert_tools(tools)
        
        # Track state for building response
        full_content = ""
        tool_calls_acc = {}  # id -> {name, arguments}
        current_tool_id = None
        current_tool_name = None
        finish_reason = None
        usage = None
        
        with self._client.messages.stream(**request_kwargs) as stream:
            for event in stream:
                event_type = event.type
                
                if event_type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_id = block.id
                        current_tool_name = block.name
                        tool_calls_acc[current_tool_id] = {
                            "name": current_tool_name,
                            "arguments": ""
                        }
                        yield {
                            "type": "tool_call",
                            "index": len(tool_calls_acc) - 1,
                            "id": current_tool_id,
                            "name": current_tool_name,
                            "arguments": ""
                        }
                
                elif event_type == "content_block_delta":
                    delta = event.delta
                    
                    if delta.type == "text_delta":
                        full_content += delta.text
                        yield {"type": "content", "text": delta.text}
                    
                    elif delta.type == "input_json_delta":
                        # Tool argument chunk
                        if current_tool_id and current_tool_id in tool_calls_acc:
                            tool_calls_acc[current_tool_id]["arguments"] += delta.partial_json
                            yield {
                                "type": "tool_call",
                                "index": len(tool_calls_acc) - 1,
                                "id": current_tool_id,
                                "name": tool_calls_acc[current_tool_id]["name"],
                                "arguments": tool_calls_acc[current_tool_id]["arguments"]
                            }
                
                elif event_type == "content_block_stop":
                    current_tool_id = None
                    current_tool_name = None
                
                elif event_type == "message_delta":
                    if hasattr(event, 'delta') and hasattr(event.delta, 'stop_reason'):
                        finish_reason = event.delta.stop_reason
                    if hasattr(event, 'usage'):
                        usage = {
                            "prompt_tokens": getattr(event.usage, 'input_tokens', 0),
                            "completion_tokens": getattr(event.usage, 'output_tokens', 0),
                            "total_tokens": getattr(event.usage, 'input_tokens', 0) + getattr(event.usage, 'output_tokens', 0)
                        }
                
                elif event_type == "message_stop":
                    pass  # Final event, handled below
        
        # Build final response
        final_tool_calls = [
            ToolCall(id=tid, name=tc["name"], arguments=tc["arguments"])
            for tid, tc in tool_calls_acc.items()
        ]
        
        final_response = LLMResponse(
            content=full_content if full_content else None,
            tool_calls=final_tool_calls,
            finish_reason=finish_reason,
            usage=usage
        )
        
        yield {"type": "done", "response": final_response}
    
    def format_tool_result(
        self,
        tool_call_id: str,
        function_name: str,
        result: str
    ) -> Dict[str, Any]:
        """
        Format tool result for Claude.
        
        Claude expects tool results as user messages with tool_result content blocks.
        """
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result
                }
            ]
        }
    
    def _convert_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """
        Convert OpenAI-format messages to Claude format.
        
        Returns:
            (system_prompt, claude_messages)
        """
        system_prompt = None
        claude_messages = []
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            
            if role == "system":
                # Claude uses system as separate parameter
                system_prompt = content
                continue
            
            if role == "assistant":
                # Check for tool calls
                if "tool_calls" in msg and msg["tool_calls"]:
                    # Build content blocks for Claude
                    content_blocks = []
                    
                    # Add text content if present
                    if content:
                        content_blocks.append({
                            "type": "text",
                            "text": content
                        })
                    
                    # Add tool_use blocks
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            args = {}
                        
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": func.get("name"),
                            "input": args
                        })
                    
                    claude_messages.append({
                        "role": "assistant",
                        "content": content_blocks
                    })
                else:
                    # Plain assistant message
                    claude_messages.append({
                        "role": "assistant",
                        "content": content
                    })
            
            elif role == "tool":
                # Convert tool result to Claude format
                # Claude expects tool results as user messages
                claude_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id"),
                            "content": content
                        }
                    ]
                })
            
            elif role == "user":
                # Check if content is already structured (list of blocks)
                if isinstance(content, list):
                    claude_messages.append({"role": "user", "content": content})
                else:
                    claude_messages.append({"role": "user", "content": content})
        
        return system_prompt, claude_messages
    
    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert OpenAI tool format to Claude format.
        
        OpenAI: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        Claude: {"name": ..., "description": ..., "input_schema": ...}
        """
        claude_tools = []
        
        for tool in tools:
            if tool.get("type") != "function":
                continue
            
            func = tool.get("function", {})
            
            claude_tools.append({
                "name": func.get("name"),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}})
            })
        
        return claude_tools
    
    def _parse_response(self, response) -> LLMResponse:
        """Parse Claude response into normalized LLMResponse."""
        
        content_text = ""
        tool_calls = []
        
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=json.dumps(block.input)
                ))
        
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens
            }
        
        return LLMResponse(
            content=content_text if content_text else None,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason,
            usage=usage
        )