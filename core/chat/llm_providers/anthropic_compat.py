# llm_providers/anthropic_compat.py
"""
Generic Anthropic Messages API compatible provider.

For services that expose the Anthropic wire format (MiniMax, etc.)
without Claude-specific features like prompt caching, thinking signatures,
or vision. Uses the anthropic SDK pointed at any base_url.

Key differences from ClaudeProvider:
- No prompt caching (provider-specific semantics)
- No thinking safety guards (no signature extraction, no auto-disable)
- No vision support
- Temperature clamped to (0.0, 1.0] (MiniMax rejects exactly 0.0)
- Simpler tool ID handling (no forced toolu_ prefix)
- Own config slot and API key
"""

import json
import logging
import time
import uuid
from typing import Dict, Any, List, Optional, Generator

from .base import BaseProvider, LLMResponse, ToolCall, retry_on_rate_limit

logger = logging.getLogger(__name__)

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic SDK not installed. Run: pip install anthropic")


class AnthropicCompatProvider(BaseProvider):
    """
    Generic provider for Anthropic Messages API compatible endpoints.

    Uses the anthropic SDK but without Claude-specific assumptions.
    Suitable for MiniMax, and other providers exposing the Anthropic wire format.
    """

    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)

        if not ANTHROPIC_AVAILABLE:
            raise ImportError("anthropic SDK not installed. Run: pip install anthropic")

        base_url = self.base_url
        if not base_url:
            raise ValueError("base_url is required for Anthropic-compatible provider")

        self._client = anthropic.Anthropic(
            api_key=self.api_key,
            base_url=base_url,
            timeout=self.request_timeout
        )

        self._thinking_enabled = llm_config.get('thinking_enabled', False)
        self._thinking_budget = llm_config.get('thinking_budget', 10000)

        logger.info(f"Anthropic-compatible provider initialized: {base_url}")

    @property
    def provider_name(self) -> str:
        return 'anthropic'

    # supports_images inherited from BaseProvider: the vision checkbox
    # (config `supports_images`) decides; default False. Anthropic-compatible
    # endpoints are a mixed bag (MiniMax M3 sees, many don't) — the user
    # declares it per provider. Was hardcoded False, which silently discarded
    # tool images even when the endpoint had vision. Fixed 2026-06-12.

    def _clamp_temperature(self, temp: float) -> float:
        """Clamp temperature to (0.0, 1.0] — some endpoints reject exactly 0.0."""
        if temp is None:
            return 0.7
        if temp <= 0.0:
            return 0.01
        if temp > 1.0:
            return 1.0
        return temp

    def health_check(self) -> bool:
        """Lightweight health check via minimal messages request."""
        try:
            self._client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
                timeout=self.health_check_timeout
            )
            return True
        except Exception as e:
            logger.debug(f"Anthropic-compat health check failed: {e}")
            return False

    def test_connection(self) -> dict:
        """Test with an actual API call."""
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": "Say hello in exactly 5 words."}],
                timeout=self.health_check_timeout
            )
            # Skip thinking blocks — reasoning models (e.g. MiniMax M3) return a
            # ThinkingBlock first, which has no `.text`. Grab the first text block.
            text = next((b.text for b in (response.content or [])
                         if getattr(b, 'type', None) == 'text'), '')
            return {"ok": True, "response": text}
        except Exception as e:
            status = getattr(e, 'status_code', '')
            msg = getattr(e, 'message', str(e))
            return {"ok": False, "error": f"{status}: {msg}" if status else str(e)}

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """
        Convert OpenAI-format messages to Anthropic format.

        Returns:
            (system_prompt, api_messages)
        """
        system_prompt = None
        api_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "") or ""

            if role == "system":
                # Combine system messages (static + dynamic)
                if system_prompt is None:
                    system_prompt = content
                else:
                    system_prompt = f"{system_prompt}\n\n{content}"
                continue

            if role == "assistant":
                if "tool_calls" in msg and msg["tool_calls"]:
                    content_blocks = []

                    # Include thinking_raw blocks if present
                    if msg.get("thinking_raw"):
                        for block in msg["thinking_raw"]:
                            content_blocks.append(block)

                    # Add text content
                    if content and content.strip():
                        content_blocks.append({"type": "text", "text": content.rstrip()})

                    # Add tool_use blocks
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            args = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                            "name": func.get("name"),
                            "input": args
                        })

                    api_messages.append({"role": "assistant", "content": content_blocks})
                else:
                    if content and content.strip():
                        api_messages.append({"role": "assistant", "content": content.rstrip()})

            elif role == "tool":
                tool_content = content if content and content.strip() else "(empty result)"
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": tool_content
                    }]
                })

            elif role == "user":
                if isinstance(content, list):
                    if self.supports_images:
                        # Vision ON (the 👁 checkbox / supports_images). Convert the
                        # internal image block {type:image, data, media_type} → the
                        # Anthropic content-block shape {type:image, source:{type:
                        # base64, media_type, data}} and pass it through. Mirrors
                        # claude.py._convert_messages; this is the format an
                        # Anthropic-compatible vision endpoint (e.g. MiniMax M3 via
                        # api.minimax.io/anthropic) expects. Was unconditionally
                        # stripped pre-fix — the checkbox was made configurable
                        # 2026-06-12 but this request-builder never got the road.
                        # 2026-06-14.
                        out_blocks = []
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "image":
                                if "source" in b:
                                    out_blocks.append(b)  # already Anthropic-shaped
                                else:
                                    out_blocks.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": b.get("media_type", "image/jpeg"),
                                            "data": b.get("data", ""),
                                        },
                                    })
                            else:
                                out_blocks.append(b)
                        if out_blocks:
                            api_messages.append({"role": "user", "content": out_blocks})
                    else:
                        # Vision OFF: strip image blocks (this provider/endpoint is
                        # text-only). Pre-fix this stripped silently; the model's "I
                        # don't see an image" reply confused users who'd just
                        # attached one. Log so the cause is visible. 2026-05-07 M3.
                        image_count = sum(
                            1 for b in content
                            if isinstance(b, dict) and b.get("type") == "image"
                        )
                        text_blocks = [
                            b for b in content
                            if not (isinstance(b, dict) and b.get("type") == "image")
                        ]
                        if image_count:
                            logger.warning(
                                f"[ANTHROPIC-COMPAT] Stripping {image_count} image "
                                f"block(s) from user message — vision is off for this "
                                f"provider (enable the 👁 checkbox if the endpoint "
                                f"supports images). Model will not see them."
                            )
                        # When the message is image-only (no caption), substitute a
                        # placeholder so the user turn doesn't get silently dropped.
                        # Without this, the API payload skips a user turn, causing
                        # consecutive-assistants alternation violation → 400 →
                        # permanent chat wedge that survives across messages
                        # (each retry re-sends the same wedged history). 2026-05-14.
                        if not text_blocks and image_count:
                            text_blocks = [{
                                "type": "text",
                                "text": "[Image attached but not shown — this provider does not support images.]",
                            }]
                        if text_blocks:
                            api_messages.append({"role": "user", "content": text_blocks})
                else:
                    if content and content.strip():
                        api_messages.append({"role": "user", "content": content})

        # Anthropic requires the first message to be role=user. An assistant-
        # initiated chat (e.g. a phone greeting recorded before the caller
        # speaks) starts with assistant → 400. Prepend a minimal user turn so
        # the conversation stays valid without dropping the opening line.
        if api_messages and api_messages[0].get("role") == "assistant":
            api_messages.insert(0, {"role": "user", "content": "[conversation started]"})

        return system_prompt, api_messages

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI tool format to Anthropic format."""
        result = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            func = tool.get("function", {})
            result.append({
                "name": func.get("name"),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}})
            })
        return result

    def format_tool_result(self, tool_call_id: str, function_name: str, result: str) -> Dict[str, Any]:
        """Format tool result in Anthropic format."""
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": result
            }]
        }

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Non-streaming chat completion via Anthropic Messages API."""
        params = generation_params or {}
        system_prompt, api_messages = self._convert_messages(messages)

        request_kwargs = {
            "model": params.get('model') or self.model,
            "messages": api_messages,
            "max_tokens": params.get("max_tokens") or 4096,  # null-safe: blanked Max Tok persists JSON null (2026-09-20)
        }

        if system_prompt:
            request_kwargs["system"] = system_prompt

        if "temperature" in params:
            request_kwargs["temperature"] = self._clamp_temperature(params["temperature"])

        # Thinking support (opt-in, no safety guards)
        # B2: honor the provider-config "disable thinking" checkbox as a fallback
        # when the per-request param isn't set (else the checkbox was a no-op here).
        disable_thinking = params.get('disable_thinking', self.config.get('disable_thinking', False))
        if self._thinking_enabled and not disable_thinking:
            if request_kwargs["max_tokens"] <= self._thinking_budget:
                request_kwargs["max_tokens"] = self._thinking_budget + 8000
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget
            }
            request_kwargs.pop("temperature", None)

        if tools:
            request_kwargs["tools"] = self._convert_tools(tools)

        response = retry_on_rate_limit(self._client.messages.create, **request_kwargs)
        return self._parse_response(response)

    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming chat completion via Anthropic Messages API."""
        params = generation_params or {}
        start_time = time.time()

        system_prompt, api_messages = self._convert_messages(messages)

        request_kwargs = {
            "model": params.get('model') or self.model,
            "messages": api_messages,
            "max_tokens": params.get("max_tokens") or 4096,  # null-safe: blanked Max Tok persists JSON null (2026-09-20)
        }

        if system_prompt:
            request_kwargs["system"] = system_prompt

        if "temperature" in params:
            request_kwargs["temperature"] = self._clamp_temperature(params["temperature"])

        # Thinking support (opt-in, no safety guards)
        # B2: honor the provider-config "disable thinking" checkbox as a fallback
        # when the per-request param isn't set (else the checkbox was a no-op here).
        disable_thinking = params.get('disable_thinking', self.config.get('disable_thinking', False))
        if self._thinking_enabled and not disable_thinking:
            if request_kwargs["max_tokens"] <= self._thinking_budget:
                request_kwargs["max_tokens"] = self._thinking_budget + 8000
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget
            }
            request_kwargs.pop("temperature", None)

        if tools:
            request_kwargs["tools"] = self._convert_tools(tools)

        # Streaming state
        full_content = ""
        full_thinking = ""
        thinking_raw = []
        current_thinking_block = None
        tool_calls_acc = {}
        current_tool_id = None
        finish_reason = None
        usage = None
        in_thinking_block = False
        first_chunk_time = None

        def _create_stream():
            return self._client.messages.stream(**request_kwargs)

        stream_ctx = retry_on_rate_limit(_create_stream)

        with stream_ctx as stream:
            for event in stream:
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                    logger.info(f"[STREAM] First event after {first_chunk_time - start_time:.2f}s")

                event_type = event.type

                if event_type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_id = block.id
                        tool_calls_acc[current_tool_id] = {"name": block.name, "arguments": ""}
                        yield {
                            "type": "tool_call",
                            "index": len(tool_calls_acc) - 1,
                            "id": current_tool_id,
                            "name": block.name,
                            "arguments": ""
                        }
                    elif block.type == "thinking":
                        in_thinking_block = True
                        current_thinking_block = {"type": "thinking", "thinking": ""}

                elif event_type == "content_block_delta":
                    delta = event.delta

                    if delta.type == "text_delta":
                        full_content += delta.text
                        yield {"type": "content", "text": delta.text}

                    elif delta.type == "thinking_delta":
                        text = delta.thinking
                        full_thinking += text
                        if current_thinking_block:
                            current_thinking_block["thinking"] += text
                        yield {"type": "thinking", "text": text}

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
                        thinking_raw.append(current_thinking_block)
                        current_thinking_block = None
                        in_thinking_block = False
                    current_tool_id = None

                elif event_type == "message_delta":
                    if hasattr(event, 'delta') and hasattr(event.delta, 'stop_reason'):
                        finish_reason = event.delta.stop_reason
                    if hasattr(event, 'usage'):
                        usage = {
                            "prompt_tokens": getattr(event.usage, 'input_tokens', 0),
                            "completion_tokens": getattr(event.usage, 'output_tokens', 0),
                            "total_tokens": getattr(event.usage, 'input_tokens', 0) + getattr(event.usage, 'output_tokens', 0)
                        }

            # Try to capture thinking blocks with signatures from final message
            try:
                final_message = stream.get_final_message()
                captured = []
                for block in final_message.content:
                    if block.type == "thinking":
                        captured.append({
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": getattr(block, 'signature', None)
                        })
                    elif block.type == "redacted_thinking":
                        captured.append({
                            "type": "redacted_thinking",
                            "data": getattr(block, 'data', '')
                        })
                if captured:
                    thinking_raw = captured
            except Exception as e:
                logger.debug(f"Could not get final message for thinking blocks: {e}")

        end_time = time.time()

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

        duration = round(end_time - start_time, 2)
        completion_tokens = usage.get("completion_tokens", 0) if usage else 0

        metadata = {
            "provider": "anthropic",
            "model": params.get('model') or self.model,
            "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(start_time)),
            "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(end_time)),
            "duration_seconds": duration,
            "tokens": {
                "thinking": len(full_thinking.split()) if full_thinking else 0,
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

    def _parse_response(self, response) -> LLMResponse:
        """Parse Anthropic response into normalized LLMResponse."""
        content_text = ""
        thinking_text = ""
        tool_calls = []

        for block in response.content:
            if block.type == "thinking":
                thinking_text += block.thinking
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

        if thinking_text:
            final_content = f"<think>{thinking_text}</think>\n\n{content_text}"
        else:
            final_content = content_text

        return LLMResponse(
            content=final_content if final_content else None,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason,
            usage=usage
        )
