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
        messages request with max_tokens=1. Any error (auth, billing,
        network) means the provider is not usable.
        """
        try:
            self._client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
                timeout=self.health_check_timeout
            )
            return True
        except Exception as e:
            logger.debug(f"Claude health check failed: {e}")
            return False

    def test_connection(self) -> dict:
        """Test Claude with an actual API call, returning response text."""
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": "Say hello in exactly 5 words."}],
                timeout=self.health_check_timeout
            )
            # Skip thinking blocks — reasoning models return a ThinkingBlock first,
            # which has no `.text`. Grab the first text block.
            text = next((b.text for b in (response.content or [])
                         if getattr(b, 'type', None) == 'text'), '')
            return {"ok": True, "response": text}
        except anthropic.APIStatusError as e:
            return {"ok": False, "error": f"{e.status_code}: {e.message}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def _build_system_blocks(self, system_prompt: str, dynamic_system: str,
                             cache_enabled: bool, cache_system_prompt: bool,
                             cache_ttl: str):
        """
        Build system prompt as a single string or multi-block array.

        When caching is active:
          - Static prompt → cached block (cache_control: ephemeral)
          - Dynamic content (state vars, clues) → uncached block (no cache_control)
          This way dynamic story content changes without breaking the cache prefix.

        When caching is off:
          - Everything combined into a single string.
        """
        if cache_enabled and cache_system_prompt:
            cache_control = {"type": "ephemeral"}
            if cache_ttl == '1h':
                cache_control["ttl"] = "1h"
            blocks = [{"type": "text", "text": system_prompt, "cache_control": cache_control}]
            if dynamic_system:
                blocks.append({"type": "text", "text": dynamic_system})
            logger.info(f"[CACHE] Prompt caching active (TTL: {cache_ttl})"
                        f"{', +dynamic block' if dynamic_system else ''}")
            return blocks
        else:
            if cache_enabled and not cache_system_prompt:
                logger.info("[CACHE] Dynamic content detected - tools only, system prompt not cached")
            if dynamic_system:
                return f"{system_prompt}\n\n{dynamic_system}"
            return system_prompt

    def _get_cache_config(self) -> tuple:
        """
        Get cache settings dynamically from settings manager.
        
        Provider instances are cached, so we read from settings_manager
        at request time to support hot-reload of cache settings.
        
        System prompt caching is skipped when dynamic content is detected:
        - Spice: randomizes persona lines each request
        - Datetime injection: changes every minute
        - prompt_inject hook registered: plugin-injected content may
          vary per turn (conservative — caught at hook-count level)

        Tools are always cached (they don't change with these features).
        Skipping avoids 25% write penalty on guaranteed cache misses.
        
        Returns:
            (cache_enabled, cache_ttl, cache_system_prompt)
        """
        from core.settings_manager import settings
        providers_config = settings.get('LLM_PROVIDERS', {})
        claude_config = providers_config.get('claude', {})
        # Default True — caching cuts per-turn tool-schema cost ~10x, and
        # the dynamic-content gate below auto-disables system-prompt caching
        # when spice/datetime/state injection would cause guaranteed misses.
        # Existing users pre-2026-04-21 who never set this key land on True.
        cache_enabled = claude_config.get('cache_enabled', True)
        cache_ttl = claude_config.get('cache_ttl', '5m')
        
        # Skip system-prompt caching when we know the prompt text changes
        # between turns — those cause guaranteed cache misses plus a 25%
        # write penalty, so the net is worse than no cache.
        #
        # 2026-05-08 — spice and inject_datetime moved to the ghost-message
        # rail (core/ghost_messages.py), which lives OUTSIDE the cached
        # prefix. They no longer mutate the system prompt, so they no
        # longer disqualify it from caching. Pre-fix this gate disabled
        # cache for almost every user (spice defaults on). Removing those
        # checks here is THE big-win cache change.
        #
        # Still gated by:
        #   - prompt_inject hooks: a plugin handler can append to the
        #     system prompt's `context_parts` (see chat.py:_get_system_prompt).
        #     We don't know what the hook produces, so we assume dynamic.
        #     Conservative — if a plugin only injects static strings, the
        #     plugin author can move to ghost_inject for per-turn content
        #     instead, which keeps the cache live.
        # Tools still cache separately (they're stable across turns).
        cache_system_prompt = True
        if cache_enabled:
            try:
                from core.hooks import hook_runner
                if hook_runner.has_handlers("prompt_inject"):
                    cache_system_prompt = False
                    logger.debug("[CACHE] prompt_inject hook registered - skipping system prompt cache")
            except Exception as e:
                logger.debug(f"[CACHE] Could not check hook state: {e}")

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
        system_prompt, claude_messages, needs_thinking_disabled, dynamic_system = self._convert_messages(messages)

        request_kwargs = {
            "model": params.get('model') or self.model,
            "messages": claude_messages,
            "max_tokens": params.get("max_tokens", 4096),
        }

        # Prompt caching configuration (read dynamically for hot-reload)
        cache_enabled, cache_ttl, cache_system_prompt = self._get_cache_config()

        if system_prompt:
            request_kwargs["system"] = self._build_system_blocks(
                system_prompt, dynamic_system, cache_enabled, cache_system_prompt, cache_ttl
            )

        # Phase 1.5 — extend cache through history. System+tools already
        # caches; this adds a cache_control marker on the last history
        # message, which makes the cached prefix cover the full conversation
        # except the ghost + new user input. ~80% input cost reduction on
        # long chats. 2026-05-08.
        if cache_enabled:
            self._apply_history_cache_control(claude_messages, cache_ttl)

        # Sampling params (temperature/top_p/top_k) are removed on modern
        # Claude models (Opus 4.7+, Fable 5) and return a 400 — never sent.

        # Adaptive thinking (Opus 4.6+, Sonnet 4.6, Fable 5). The old
        # {type: "enabled", budget_tokens: N} contract 400s on Opus 4.7+.
        # Depth is steered via output_config.effort instead of a budget.
        thinking_enabled = self.config.get('thinking_enabled')
        if thinking_enabled is None:
            thinking_enabled = getattr(config, 'CLAUDE_THINKING_ENABLED', False)
        # B2: the Settings "disable thinking" checkbox lives on provider config —
        # honor it as a fallback when the per-request param isn't set (continue/
        # prefill set the param directly). Without this it was a no-op on Claude.
        disable_thinking = params.get('disable_thinking', self.config.get('disable_thinking', False))

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
            # Thinking spends from the max_tokens pool — floor it so a small
            # response cap doesn't truncate mid-thought.
            if request_kwargs["max_tokens"] < 16000:
                request_kwargs["max_tokens"] = 16000
            # display defaults to "omitted" on Opus 4.7+ (empty thinking text);
            # "summarized" restores the streamed thinking the UI shows.
            request_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            effort = self.config.get('reasoning_effort') or 'high'
            request_kwargs["output_config"] = {"effort": effort}
            logger.info(f"[THINK] Claude adaptive thinking enabled (effort: {effort})")

        if tools:
            request_kwargs["tools"] = self._convert_tools(tools, cache_enabled, cache_ttl)
        
        # Wrap in retry for rate limiting
        try:
            response = retry_on_rate_limit(
                self._client.messages.create,
                **request_kwargs
            )
        except Exception as e:
            from core.socks_proxy import maybe_llm_proxy_hint
            maybe_llm_proxy_hint('Claude', str(e))
            raise
        
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
        system_prompt, claude_messages, needs_thinking_disabled, dynamic_system = self._convert_messages(messages)

        request_kwargs = {
            "model": params.get('model') or self.model,
            "messages": claude_messages,
            "max_tokens": params.get("max_tokens", 4096),
        }

        # Prompt caching configuration (read dynamically for hot-reload)
        cache_enabled, cache_ttl, cache_system_prompt = self._get_cache_config()

        if system_prompt:
            request_kwargs["system"] = self._build_system_blocks(
                system_prompt, dynamic_system, cache_enabled, cache_system_prompt, cache_ttl
            )

        # Phase 1.5 — see chat_completion() for rationale. Extends cache
        # through full history; only ghost+new-user is fresh.
        if cache_enabled:
            self._apply_history_cache_control(claude_messages, cache_ttl)

        # Sampling params (temperature/top_p/top_k) are removed on modern
        # Claude models (Opus 4.7+, Fable 5) and return a 400 — never sent.

        # Adaptive thinking — see chat_completion() for rationale.
        thinking_enabled = self.config.get('thinking_enabled')
        if thinking_enabled is None:
            thinking_enabled = getattr(config, 'CLAUDE_THINKING_ENABLED', False)
        # B2: the Settings "disable thinking" checkbox lives on provider config —
        # honor it as a fallback when the per-request param isn't set (continue/
        # prefill set the param directly). Without this it was a no-op on Claude.
        disable_thinking = params.get('disable_thinking', self.config.get('disable_thinking', False))
        
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
            # Thinking spends from the max_tokens pool — floor it so a small
            # response cap doesn't truncate mid-thought.
            if request_kwargs["max_tokens"] < 16000:
                request_kwargs["max_tokens"] = 16000
            # display defaults to "omitted" on Opus 4.7+ (empty thinking text);
            # "summarized" restores the streamed thinking the UI shows.
            request_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            effort = self.config.get('reasoning_effort') or 'high'
            request_kwargs["output_config"] = {"effort": effort}
            logger.info(f"[THINK] Claude adaptive thinking enabled (effort: {effort})")
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
                                logger.info(f"[CACHE] HIT - {cache_read} tokens read from cache (90% savings)")
                            elif cache_write > 0 and cache_read == 0:
                                logger.info(f"[CACHE] MISS - {cache_write} tokens written to cache")
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
        
        tokens_dict = {
            "thinking": len(full_thinking.split()) if full_thinking else 0,  # Rough estimate
            "content": completion_tokens,
            "total": usage.get("total_tokens", 0) if usage else 0,
            "prompt": usage.get("prompt_tokens", 0) if usage else 0
        }
        # Forward cache stats from usage
        if usage:
            for k in ("cache_read_tokens", "cache_write_tokens"):
                if usage.get(k):
                    tokens_dict[k] = usage[k]

        metadata = {
            "provider": "claude",
            "model": params.get('model') or self.model,
            "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(start_time)),
            "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(end_time)),
            "duration_seconds": duration,
            "tokens": tokens_dict,
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
        # Anthropic API rejects tool_result blocks with empty/whitespace content
        # with a 400. The history-replay path (line ~790) substitutes
        # "(empty result)" — this live path didn't, so streams died mid-turn
        # when any tool returned an empty string. Now symmetric. 2026-05-16.
        if not result or not str(result).strip():
            result = "(empty result)"
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": self._sanitize_tool_id(tool_call_id),
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
            
            # Filter out thinking blocks (including redacted_thinking — API rejects both when thinking is disabled)
            filtered_content = [
                block for block in content
                if not (isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"))
            ]
            
            stripped_count = len(content) - len(filtered_content)
            if stripped_count > 0:
                logger.info(f"[THINK] Stripped {stripped_count} thinking block(s) from assistant message (thinking disabled)")
            
            if filtered_content:
                result.append({**msg, "content": filtered_content})
            elif content:  # Had content but all was thinking - skip empty message
                logger.debug("[THINK] Stripped assistant message that only contained thinking blocks")
        
        return result
    
    @staticmethod
    def _sanitize_tool_id(tool_id: str) -> str:
        """Sanitize tool call ID to match Claude's required pattern.

        Foreign provider IDs (chatcmpl-tool-*, call_*, etc.) are deterministically
        remapped to toolu_* so tool_use/tool_result pairs stay matched.
        """
        import re, hashlib
        if not tool_id:
            return f"toolu_{uuid.uuid4().hex[:24]}"
        if tool_id.startswith('toolu_'):
            return tool_id
        # Remap foreign IDs deterministically — same input always gives same output
        h = hashlib.sha256(tool_id.encode()).hexdigest()[:24]
        return f"toolu_{h}"

    def _apply_history_cache_control(self, claude_messages: list, cache_ttl: str) -> None:
        """Place a cache_control marker on the last message before ghost/new-user.

        Phase 1.5 of cache work (2026-05-08). System prompt + tools are
        already cached via separate markers in `_build_system_blocks` and
        `_convert_tools`. This method extends the cached prefix THROUGH the
        conversation history — only the ghost message + new user input are
        fresh tokens per turn.

        Detection: the new user message is always the LAST message. The
        ghost message (when present) is a user-role string starting with
        the envelope header from `core/ghost_messages.py`. If we find one,
        place the cache marker on the message BEFORE it (the actual last
        history turn). Otherwise place it on the second-to-last (which is
        then the last history turn directly).

        Anthropic auto-skips cache for blocks below the model's minimum
        cacheable size (~1024 tokens), so short histories silently no-op
        rather than paying the 1.25x cache-write premium for nothing.
        """
        if not claude_messages or len(claude_messages) < 2:
            return  # too short for any cacheable history

        # Detect the ghost envelope at second-to-last position.
        # Import the canonical sentinel so a future rename only touches one
        # file. Legacy prefix kept inline for transition compatibility.
        try:
            from core.ghost_messages import _ENVELOPE_HEADER as _GHOST_PREFIX
        except Exception:
            _GHOST_PREFIX = "[System context from Sapphire's own app"
        second_to_last = claude_messages[-2]
        _content = second_to_last.get("content") if isinstance(second_to_last.get("content"), str) else ""
        is_ghost = (
            second_to_last.get("role") == "user"
            and (
                _content.startswith(_GHOST_PREFIX)
                or _content.startswith("[Operator metadata for assistant")
            )
        )
        cache_idx = -3 if is_ghost else -2
        if abs(cache_idx) > len(claude_messages):
            return  # nothing to cache (e.g., only ghost + new user)

        cache_control = {"type": "ephemeral"}
        if cache_ttl == '1h':
            cache_control["ttl"] = "1h"

        target = claude_messages[cache_idx]
        content = target.get("content")
        if isinstance(content, str):
            # Wrap string content in a single text block with cache_control.
            target["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": cache_control,
            }]
        elif isinstance(content, list) and content:
            # Mark the last block. Copy the dict so we don't mutate any
            # block object that might be shared across requests.
            last_block = dict(content[-1])
            last_block["cache_control"] = cache_control
            content[-1] = last_block

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """
        Convert OpenAI-format messages to Claude format.
        
        Handles:
        - Empty assistant content
        - Empty tool results
        - thinking_raw blocks for tool cycle continuity
        
        Returns:
            (system_prompt, claude_messages, needs_thinking_disabled, dynamic_system)

        needs_thinking_disabled is True if the LAST assistant message with tool_calls
        has no thinking_raw AND tool results haven't been provided yet. This indicates
        an active tool cycle that started without thinking.
        
        Completed tool cycles (where tool results exist) don't require thinking_raw
        because Claude won't continue from that point.
        """
        system_prompt = None
        dynamic_system = None
        claude_messages = []
        needs_thinking_disabled = False

        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content", "") or ""

            if role == "system":
                if msg.get("_dynamic"):
                    dynamic_system = content
                else:
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
                            "id": self._sanitize_tool_id(tc.get("id", "")),
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
                            "tool_use_id": self._sanitize_tool_id(msg.get("tool_call_id", "")),
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
        
        return system_prompt, claude_messages, needs_thinking_disabled, dynamic_system
    
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
                    logger.info(f"[CACHE] HIT - {cache_read} tokens read from cache (90% savings)")
                elif cache_write > 0 and cache_read == 0:
                    logger.info(f"[CACHE] MISS - {cache_write} tokens written to cache")
                else:
                    logger.info(f"[CACHE] PARTIAL - {cache_read} read, {cache_write} written")
                
                # Add to usage dict for potential UI display
                usage["cache_read_tokens"] = cache_read
                usage["cache_write_tokens"] = cache_write
        
        # Prepend thinking as <think> tags to match streaming behavior
        if thinking_text:
            logger.info(f"[THINK] Non-stream response has thinking ({len(thinking_text)} chars)")
            final_content = f"<think>{thinking_text}</think>\n\n{content_text}"
        else:
            final_content = content_text

        return LLMResponse(
            content=final_content if final_content else None,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason,
            usage=usage
        )