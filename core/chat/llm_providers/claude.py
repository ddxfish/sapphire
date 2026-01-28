# llm_providers/claude.py
"""
Anthropic Claude provider.

Handles Claude-specific API differences:
- Different authentication header (x-api-key)
- Different message format for tool use
- Different streaming event format
- System prompt handling
- Tool result format differences
- Extended thinking with proper separation for cross-provider compatibility
"""

import json
import logging
import time
import uuid
from typing import Dict, Any, List, Optional, Generator

import config
from .base import BaseProvider, LLMResponse, ToolCall, retry_on_rate_limit

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
    - Extended thinking uses structured blocks, not text tags
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
    
    @property
    def supports_images(self) -> bool:
        return True
    
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
            if e.status_code in (400, 401, 403):
                return True
            logger.debug(f"Claude health check failed: {e}")
            return False
        except Exception as e:
            logger.debug(f"Claude health check failed: {e}")
            return False
    
    def _get_cache_config(self) -> tuple:
        """
        Get cache settings dynamically from settings manager.
        
        Provider instances are cached, so we read from settings_manager
        at request time to support hot-reload of cache settings.
        
        System prompt caching is skipped when dynamic content is detected:
        - Spice: randomizes injections each request
        - Datetime injection: changes every minute
        - State-in-prompt: includes turn count that changes each message
        
        Tools are always cached (they don't change with these features).
        Skipping avoids 25% write penalty on guaranteed cache misses.
        
        Returns:
            (cache_enabled, cache_ttl, cache_system_prompt)
        """
        from core.settings_manager import settings
        providers_config = settings.get('LLM_PROVIDERS', {})
        claude_config = providers_config.get('claude', {})
        cache_enabled = claude_config.get('cache_enabled', False)
        cache_ttl = claude_config.get('cache_ttl', '5m')
        
        # Check if spice, datetime, or state injection is enabled for active chat
        # All cause guaranteed cache misses on system prompt (25% penalty)
        cache_system_prompt = True
        if cache_enabled:
            try:
                # Import here to avoid circular dependency
                from core import system as sys_module
                if hasattr(sys_module, 'system_instance') and sys_module.system_instance:
                    chat_settings = sys_module.system_instance.llm_chat.session_manager.get_chat_settings()
                    if chat_settings.get('spice_enabled', False):
                        cache_system_prompt = False
                        logger.debug("[CACHE] Spice enabled - skipping system prompt cache")
                    elif chat_settings.get('inject_datetime', False):
                        cache_system_prompt = False
                        logger.debug("[CACHE] Datetime injection enabled - skipping system prompt cache")
                    elif chat_settings.get('state_engine_enabled', False) and chat_settings.get('state_in_prompt', True):
                        cache_system_prompt = False
                        logger.debug("[CACHE] State-in-prompt enabled - skipping system prompt cache")
            except Exception as e:
                logger.debug(f"[CACHE] Could not check chat settings: {e}")
        
        return cache_enabled, cache_ttl, cache_system_prompt
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Send non-streaming chat completion to Claude."""
        
        params = generation_params or {}
        
        # Extract system prompt from messages
        system_prompt, claude_messages, needs_thinking_disabled = self._convert_messages(messages)
        
        request_kwargs = {
            "model": params.get('model') or self.model,
            "messages": claude_messages,
            "max_tokens": params.get("max_tokens", 4096),
        }
        
        # Prompt caching configuration (read dynamically for hot-reload)
        cache_enabled, cache_ttl, cache_system_prompt = self._get_cache_config()
        
        if system_prompt:
            if cache_enabled and cache_system_prompt:
                # Use list format with cache_control for caching
                cache_control = {"type": "ephemeral"}
                if cache_ttl == '1h':
                    cache_control["ttl"] = "1h"
                request_kwargs["system"] = [
                    {"type": "text", "text": system_prompt, "cache_control": cache_control}
                ]
                logger.info(f"[CACHE] Prompt caching active (TTL: {cache_ttl})")
            else:
                request_kwargs["system"] = system_prompt
                if cache_enabled and not cache_system_prompt:
                    logger.info("[CACHE] Dynamic content detected - tools only, system prompt not cached")
        
        if "temperature" in params:
            request_kwargs["temperature"] = params["temperature"]
        
        # Add extended thinking if enabled (unless explicitly disabled)
        # Read from provider config first, fall back to global config
        thinking_enabled = self.config.get('thinking_enabled')
        if thinking_enabled is None:
            thinking_enabled = getattr(config, 'CLAUDE_THINKING_ENABLED', False)
        thinking_budget = self.config.get('thinking_budget')
        if thinking_budget is None:
            thinking_budget = getattr(config, 'CLAUDE_THINKING_BUDGET', 10000)
        disable_thinking = params.get('disable_thinking', False)
        
        # SAFETY: Auto-disable thinking if active tool cycle lacks thinking_raw
        if needs_thinking_disabled:
            if thinking_enabled and not disable_thinking:
                logger.info("[THINK] Auto-disabling thinking for this request: active tool cycle started without thinking")
            disable_thinking = True
        
        # SAFETY: Auto-disable thinking if last message is assistant (continue mode)
        if claude_messages and claude_messages[-1].get("role") == "assistant":
            if thinking_enabled and not disable_thinking:
                logger.info("[THINK] Auto-disabling thinking: last message is assistant (continue mode)")
            disable_thinking = True
        
        # CRITICAL: Strip thinking blocks from messages if thinking is disabled
        # Claude rejects thinking blocks in messages when thinking param is disabled
        if disable_thinking:
            claude_messages = self._strip_thinking_blocks(claude_messages)
            request_kwargs["messages"] = claude_messages  # Update reference!
        
        if thinking_enabled and not disable_thinking:
            if request_kwargs["max_tokens"] <= thinking_budget:
                request_kwargs["max_tokens"] = thinking_budget + 8000
                logger.info(f"[THINK] Bumped max_tokens to {request_kwargs['max_tokens']} (must exceed budget)")
            
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget
            }
            request_kwargs.pop("temperature", None)
            logger.info(f"[THINK] Claude extended thinking enabled (budget: {thinking_budget})")
        
        if tools:
            request_kwargs["tools"] = self._convert_tools(tools, cache_enabled, cache_ttl)
        
        # Wrap in retry for rate limiting
        response = retry_on_rate_limit(
            self._client.messages.create,
            **request_kwargs
        )
        
        return self._parse_response(response)
    
    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send streaming chat completion to Claude.
        
        Yields events:
            - {"type": "thinking", "text": "..."} - Thinking content (for UI)
            - {"type": "content", "text": "..."} - Visible response content
            - {"type": "tool_call", ...} - Tool call info
            - {"type": "done", "response": LLMResponse, "thinking": "...", "thinking_raw": [...]}
        """
        
        params = generation_params or {}
        start_time = time.time()
        
        # Extract system prompt from messages
        system_prompt, claude_messages, needs_thinking_disabled = self._convert_messages(messages)
        
        request_kwargs = {
            "model": params.get('model') or self.model,
            "messages": claude_messages,
            "max_tokens": params.get("max_tokens", 4096),
        }
        
        # Prompt caching configuration (read dynamically for hot-reload)
        cache_enabled, cache_ttl, cache_system_prompt = self._get_cache_config()
        
        if system_prompt:
            if cache_enabled and cache_system_prompt:
                # Use list format with cache_control for caching
                cache_control = {"type": "ephemeral"}
                if cache_ttl == '1h':
                    cache_control["ttl"] = "1h"
                request_kwargs["system"] = [
                    {"type": "text", "text": system_prompt, "cache_control": cache_control}
                ]
                logger.info(f"[CACHE] Prompt caching active (TTL: {cache_ttl})")
            else:
                request_kwargs["system"] = system_prompt
                if cache_enabled and not cache_system_prompt:
                    logger.info("[CACHE] Dynamic content detected - tools only, system prompt not cached")
        
        if "temperature" in params:
            request_kwargs["temperature"] = params["temperature"]
        
        # Add extended thinking if enabled (unless explicitly disabled for this request)
        # Read from provider config first, fall back to global config
        thinking_enabled = self.config.get('thinking_enabled')
        if thinking_enabled is None:
            thinking_enabled = getattr(config, 'CLAUDE_THINKING_ENABLED', False)
        thinking_budget = self.config.get('thinking_budget')
        if thinking_budget is None:
            thinking_budget = getattr(config, 'CLAUDE_THINKING_BUDGET', 10000)
        disable_thinking = params.get('disable_thinking', False)
        
        # SAFETY: Auto-disable thinking if active tool cycle lacks thinking_raw
        if needs_thinking_disabled:
            if thinking_enabled and not disable_thinking:
                logger.info("[THINK] Auto-disabling thinking for this request: active tool cycle started without thinking")
            disable_thinking = True
        
        # SAFETY: Auto-disable thinking if last message is assistant (continue mode)
        # Claude requires thinking blocks at start - can't inject into existing prefill
        if claude_messages and claude_messages[-1].get("role") == "assistant":
            if thinking_enabled and not disable_thinking:
                logger.info("[THINK] Auto-disabling thinking: last message is assistant (continue mode)")
            disable_thinking = True
        
        # CRITICAL: Strip thinking blocks from messages if thinking is disabled
        # Claude rejects thinking blocks in messages when thinking param is disabled
        if disable_thinking:
            claude_messages = self._strip_thinking_blocks(claude_messages)
            request_kwargs["messages"] = claude_messages  # Update reference!
        
        if thinking_enabled and not disable_thinking:
            if request_kwargs["max_tokens"] <= thinking_budget:
                request_kwargs["max_tokens"] = thinking_budget + 8000
                logger.info(f"[THINK] Bumped max_tokens to {request_kwargs['max_tokens']} (must exceed budget)")
            
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget
            }
            request_kwargs.pop("temperature", None)
            logger.info(f"[THINK] Claude extended thinking enabled (budget: {thinking_budget})")
        elif thinking_enabled and disable_thinking:
            logger.info(f"[THINK] Extended thinking disabled for this request")
        
        if tools:
            request_kwargs["tools"] = self._convert_tools(tools, cache_enabled, cache_ttl)
        
        # Track state for building response
        full_content = ""
        full_thinking = ""
        thinking_raw = []  # Store raw thinking blocks for tool cycle continuity
        current_thinking_block = None
        
        tool_calls_acc = {}
        current_tool_id = None
        current_tool_name = None
        finish_reason = None
        usage = None
        
        in_thinking_block = False
        first_chunk_time = None
        
        # Create stream with retry logic for rate limiting
        # Rate limit errors occur at stream creation, not during iteration
        def _create_stream():
            return self._client.messages.stream(**request_kwargs)
        
        stream_ctx = retry_on_rate_limit(_create_stream)
        
        with stream_ctx as stream:
            logger.debug(f"[STREAM] Context entered, waiting for events...")
            for event in stream:
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                    logger.info(f"[STREAM] First event received after {first_chunk_time - start_time:.2f}s")
                
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
                    elif block.type == "thinking":
                        in_thinking_block = True
                        current_thinking_block = {"type": "thinking", "thinking": ""}
                        logger.debug("[THINK] Thinking block started")
                
                elif event_type == "content_block_delta":
                    delta = event.delta
                    
                    if delta.type == "text_delta":
                        full_content += delta.text
                        yield {"type": "content", "text": delta.text}
                    
                    elif delta.type == "thinking_delta":
                        thinking_text = delta.thinking
                        full_thinking += thinking_text
                        if current_thinking_block:
                            current_thinking_block["thinking"] += thinking_text
                        # Emit thinking as separate event type
                        yield {"type": "thinking", "text": thinking_text}
                    
                    elif delta.type == "input_json_delta":
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
                    if in_thinking_block and current_thinking_block:
                        # Store raw thinking block for tool cycle continuity
                        thinking_raw.append(current_thinking_block)
                        current_thinking_block = None
                        in_thinking_block = False
                        logger.debug("[THINK] Thinking block ended")
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
                        # Check for cache statistics
                        cache_read = getattr(event.usage, 'cache_read_input_tokens', 0) or 0
                        cache_write = getattr(event.usage, 'cache_creation_input_tokens', 0) or 0
                        if cache_read > 0 or cache_write > 0:
                            if cache_read > 0 and cache_write == 0:
                                logger.info(f"[CACHE] ✓ HIT - {cache_read} tokens read from cache (90% savings)")
                            elif cache_write > 0 and cache_read == 0:
                                logger.info(f"[CACHE] ✗ MISS - {cache_write} tokens written to cache")
                            else:
                                logger.info(f"[CACHE] PARTIAL - {cache_read} read, {cache_write} written")
                            usage["cache_read_tokens"] = cache_read
                            usage["cache_write_tokens"] = cache_write
                
                elif event_type == "message_stop":
                    logger.debug(f"[STREAM] message_stop received")
            
            # Get the complete message with signatures for thinking blocks
            try:
                final_message = stream.get_final_message()
                # Extract complete thinking blocks (with signatures) for tool cycle continuity
                thinking_raw = []
                for block in final_message.content:
                    if block.type == "thinking":
                        # This has the signature - convert to dict
                        thinking_raw.append({
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": block.signature
                        })
                    elif block.type == "redacted_thinking":
                        thinking_raw.append({
                            "type": "redacted_thinking",
                            "data": block.data
                        })
                if thinking_raw:
                    logger.debug(f"[THINK] Captured {len(thinking_raw)} thinking blocks with signatures")
            except Exception as e:
                logger.warning(f"[THINK] Could not get final message for signatures: {e}")
        
        end_time = time.time()
        logger.info(f"[STREAM] Stream complete, total time: {end_time - start_time:.2f}s")
        
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
        
        # Build metadata
        duration = round(end_time - start_time, 2)
        completion_tokens = usage.get("completion_tokens", 0) if usage else 0
        
        metadata = {
            "provider": "claude",
            "model": params.get('model') or self.model,
            "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(start_time)),
            "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(end_time)),
            "duration_seconds": duration,
            "tokens": {
                "thinking": len(full_thinking.split()) if full_thinking else 0,  # Rough estimate
                "content": completion_tokens,
                "total": usage.get("total_tokens", 0) if usage else 0,
                "prompt": usage.get("prompt_tokens", 0) if usage else 0
            },
            "tokens_per_second": round(completion_tokens / duration, 1) if duration > 0 else 0
        }
        
        yield {
            "type": "done", 
            "response": final_response,
            "thinking": full_thinking if full_thinking else None,
            "thinking_raw": thinking_raw if thinking_raw else None,
            "metadata": metadata
        }
    
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
    
    def _strip_thinking_blocks(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Strip thinking blocks from converted Claude messages.
        
        Required when thinking is disabled but history contains thinking_raw blocks.
        Claude API rejects thinking blocks in messages when thinking param is disabled.
        """
        result = []
        for msg in messages:
            if msg.get("role") != "assistant":
                result.append(msg)
                continue
            
            content = msg.get("content")
            if not isinstance(content, list):
                result.append(msg)
                continue
            
            # Filter out thinking blocks
            filtered_content = [
                block for block in content 
                if not (isinstance(block, dict) and block.get("type") == "thinking")
            ]
            
            stripped_count = len(content) - len(filtered_content)
            if stripped_count > 0:
                logger.info(f"[THINK] Stripped {stripped_count} thinking block(s) from assistant message (thinking disabled)")
            
            if filtered_content:
                result.append({**msg, "content": filtered_content})
            elif content:  # Had content but all was thinking - skip empty message
                logger.debug("[THINK] Stripped assistant message that only contained thinking blocks")
        
        return result
    
    def _convert_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """
        Convert OpenAI-format messages to Claude format.
        
        Handles:
        - Empty assistant content
        - Empty tool results
        - thinking_raw blocks for tool cycle continuity
        
        Returns:
            (system_prompt, claude_messages, needs_thinking_disabled)
            
        needs_thinking_disabled is True if the LAST assistant message with tool_calls
        has no thinking_raw AND tool results haven't been provided yet. This indicates
        an active tool cycle that started without thinking.
        
        Completed tool cycles (where tool results exist) don't require thinking_raw
        because Claude won't continue from that point.
        """
        system_prompt = None
        claude_messages = []
        needs_thinking_disabled = False
        
        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content", "") or ""
            
            if role == "system":
                system_prompt = content
                continue
            
            if role == "assistant":
                if "tool_calls" in msg and msg["tool_calls"]:
                    # Build content blocks for Claude
                    content_blocks = []
                    
                    # Include thinking_raw blocks if present (required for tool cycles)
                    # These contain signatures from the original response
                    if msg.get("thinking_raw"):
                        for think_block in msg["thinking_raw"]:
                            content_blocks.append(think_block)
                    else:
                        # No thinking_raw but we have tool_calls
                        # Only disable thinking if this is an ACTIVE tool cycle
                        # (no tool results following this message yet)
                        has_tool_result_after = any(
                            m.get("role") == "tool" for m in messages[i+1:]
                        )
                        if not has_tool_result_after:
                            needs_thinking_disabled = True
                            logger.warning("[THINK] Active tool cycle has no thinking_raw - thinking must be disabled for this request")
                    
                    # Add text content if present (strip trailing whitespace)
                    if content and content.strip():
                        content_blocks.append({
                            "type": "text",
                            "text": content.rstrip()
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
                    # Plain assistant message - strip trailing whitespace (Claude rejects it)
                    if content and content.strip():
                        claude_messages.append({
                            "role": "assistant",
                            "content": content.rstrip()
                        })
            
            elif role == "tool":
                tool_content = content if content and content.strip() else "(empty result)"
                claude_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id"),
                            "content": tool_content
                        }
                    ]
                })
            
            elif role == "user":
                if isinstance(content, list):
                    if content:
                        # Convert internal image format to Claude format
                        claude_content = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "image":
                                # Internal: {"type": "image", "data": "...", "media_type": "..."}
                                # Claude:   {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
                                claude_content.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": block.get("media_type", "image/jpeg"),
                                        "data": block.get("data", "")
                                    }
                                })
                            else:
                                # Pass through text blocks and other content as-is
                                claude_content.append(block)
                        claude_messages.append({"role": "user", "content": claude_content})
                else:
                    if content and content.strip():
                        claude_messages.append({"role": "user", "content": content})
        
        return system_prompt, claude_messages, needs_thinking_disabled
    
    def _convert_tools(self, tools: List[Dict[str, Any]], cache_enabled: bool = False, cache_ttl: str = '5m') -> List[Dict[str, Any]]:
        """
        Convert OpenAI tool format to Claude format.
        
        If cache_enabled, adds cache_control to the last tool.
        Cache order is: tools → system → messages, so caching tools
        creates a cache breakpoint that includes all tools.
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
        
        # Add cache_control to the last tool if caching enabled
        if cache_enabled and claude_tools:
            cache_control = {"type": "ephemeral"}
            if cache_ttl == '1h':
                cache_control["ttl"] = "1h"
            claude_tools[-1]["cache_control"] = cache_control
            logger.info(f"[CACHE] Tool caching active on last tool (TTL: {cache_ttl})")
        
        return claude_tools
    
    def _parse_response(self, response) -> LLMResponse:
        """Parse Claude response into normalized LLMResponse."""
        
        content_text = ""
        thinking_text = ""
        thinking_raw = []
        tool_calls = []
        
        for block in response.content:
            if block.type == "thinking":
                thinking_text += block.thinking
                thinking_raw.append({"type": "thinking", "thinking": block.thinking})
            elif block.type == "text":
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
            
            # Log cache statistics if present
            cache_read = getattr(response.usage, 'cache_read_input_tokens', 0) or 0
            cache_write = getattr(response.usage, 'cache_creation_input_tokens', 0) or 0
            
            if cache_read > 0 or cache_write > 0:
                if cache_read > 0 and cache_write == 0:
                    logger.info(f"[CACHE] ✓ HIT - {cache_read} tokens read from cache (90% savings)")
                elif cache_write > 0 and cache_read == 0:
                    logger.info(f"[CACHE] ✗ MISS - {cache_write} tokens written to cache")
                else:
                    logger.info(f"[CACHE] PARTIAL - {cache_read} read, {cache_write} written")
                
                # Add to usage dict for potential UI display
                usage["cache_read_tokens"] = cache_read
                usage["cache_write_tokens"] = cache_write
        
        # Note: For non-streaming, thinking is not returned separately
        # The caller would need to handle this differently if needed
        return LLMResponse(
            content=content_text if content_text else None,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason,
            usage=usage
        )