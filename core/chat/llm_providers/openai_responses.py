# llm_providers/openai_responses.py
"""
OpenAI Responses API provider.

Handles the new Responses API format for GPT-5.x reasoning models:
- Uses /v1/responses endpoint instead of /v1/chat/completions
- Supports reasoning summaries (visible chain-of-thought)
- Different message/output structure
- Stateful conversation via previous_response_id

This provider can also be used generically for other APIs adopting
the Responses format (Open Responses standard).
"""

import json
import logging
import time
from typing import Dict, Any, List, Optional, Generator

from openai import OpenAI

from .base import BaseProvider, LLMResponse, ToolCall, retry_on_rate_limit

logger = logging.getLogger(__name__)


class OpenAIResponsesProvider(BaseProvider):
    """
    Provider for OpenAI Responses API.
    
    Key differences from Chat Completions:
    - Endpoint: /v1/responses instead of /v1/chat/completions
    - Input: 'input' field (string or array) instead of 'messages'
    - Output: 'output' array with typed items (message, reasoning, tool_use, etc.)
    - Reasoning: 'reasoning' param with effort and summary settings
    - State: previous_response_id for multi-turn without resending history
    """
    
    # Models that should use Responses API (reasoning models)
    RESPONSES_MODELS = {
        'gpt-5', 'gpt-5.1', 'gpt-5.2',
        'gpt-5-mini', 'gpt-5-nano',
        'gpt-5.2-codex',
        'o1', 'o3', 'o3-mini', 'o4-mini',
    }
    
    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)
        
        # Default to OpenAI API
        base_url = self.base_url or "https://api.openai.com/v1"
        
        self._client = OpenAI(
            base_url=base_url,
            api_key=self.api_key,
            timeout=self.request_timeout
        )
        
        # Reasoning config
        self.reasoning_effort = llm_config.get('reasoning_effort', 'medium')
        self.reasoning_summary = llm_config.get('reasoning_summary', 'detailed')  # 'detailed' forces summaries
        
        logger.info(f"OpenAI Responses provider initialized: {base_url} (effort={self.reasoning_effort})")
    
    @classmethod
    def should_use_responses_api(cls, model: str) -> bool:
        """Check if a model should use Responses API instead of Chat Completions."""
        if not model:
            return False
        model_lower = model.lower()
        # Check prefix matches for model families
        return any(model_lower.startswith(m) for m in cls.RESPONSES_MODELS)
    
    @property
    def provider_name(self) -> str:
        return 'openai_responses'
    
    @property
    def supports_images(self) -> bool:
        """Responses API supports multimodal."""
        return True
    
    def health_check(self) -> bool:
        """Check endpoint health via models.list()."""
        try:
            self._client.models.list(timeout=self.health_check_timeout)
            return True
        except Exception as e:
            logger.debug(f"Health check failed for {self.base_url}: {e}")
            return False
    
    def _convert_messages_to_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert Chat Completions message format to Responses API input format.
        
        Chat Completions: [{"role": "user", "content": "..."}]
        Responses API: [{"role": "user", "content": "..."}] or just "string" for single turn
        
        Also handles system/developer messages and tool results.
        """
        input_items = []
        
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            
            # System messages become developer messages in Responses API
            if role == 'system':
                # Will be passed as 'instructions' parameter instead
                continue
            
            # Tool results
            if role == 'tool':
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get('tool_call_id', ''),
                    "output": content if isinstance(content, str) else json.dumps(content)
                })
                continue
            
            # Assistant messages with tool calls
            if role == 'assistant' and msg.get('tool_calls'):
                # Add any text content first
                if content and content.strip():
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": content
                    })
                
                # Add function calls
                for tc in msg['tool_calls']:
                    func = tc.get('function', {})
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc.get('id', ''),
                        "name": func.get('name', ''),
                        "arguments": func.get('arguments', '{}')
                    })
                continue
            
            # Regular user/assistant messages
            if role in ('user', 'assistant'):
                # Handle multimodal content
                if isinstance(content, list):
                    # Convert to Responses format
                    resp_content = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                resp_content.append({
                                    "type": "input_text",
                                    "text": block.get('text', '')
                                })
                            elif block.get('type') == 'image':
                                resp_content.append({
                                    "type": "input_image",
                                    "image_url": f"data:{block.get('media_type', 'image/jpeg')};base64,{block.get('data', '')}"
                                })
                    input_items.append({
                        "type": "message",
                        "role": role,
                        "content": resp_content
                    })
                else:
                    input_items.append({
                        "type": "message",
                        "role": role,
                        "content": content
                    })
        
        return input_items
    
    def _convert_tools_for_api(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI function tools format for Responses API."""
        resp_tools = []
        
        for tool in tools:
            if tool.get('type') != 'function':
                continue
            
            func = tool.get('function', {})
            resp_tools.append({
                "type": "function",
                "name": func.get('name', ''),
                "description": func.get('description', ''),
                "parameters": func.get('parameters', {"type": "object", "properties": {}})
            })
        
        return resp_tools
    
    def _extract_system_prompt(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Extract system prompt from messages (becomes 'instructions' in Responses API)."""
        for msg in messages:
            if msg.get('role') == 'system':
                return msg.get('content', '')
        return None
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> LLMResponse:
        """Send non-streaming request to Responses API."""
        
        params = generation_params or {}
        
        # Extract system prompt for instructions
        instructions = self._extract_system_prompt(messages)
        
        # Convert messages to input format
        input_items = self._convert_messages_to_input(messages)
        
        request_kwargs = {
            "model": params.get('model') or self.model,
            "input": input_items,
        }
        
        if instructions:
            request_kwargs["instructions"] = instructions
        
        # Reasoning settings
        request_kwargs["reasoning"] = {
            "effort": params.get('reasoning_effort', self.reasoning_effort),
            "summary": params.get('reasoning_summary', self.reasoning_summary)
        }
        
        # Max tokens
        if params.get('max_tokens'):
            request_kwargs["max_output_tokens"] = params['max_tokens']
        
        # Tools
        if tools:
            request_kwargs["tools"] = self._convert_tools_for_api(tools)
        
        logger.info(f"[RESPONSES] Non-streaming: {len(input_items)} items to {request_kwargs['model']}")
        
        # Use responses.create
        response = retry_on_rate_limit(
            self._client.responses.create,
            **request_kwargs
        )
        
        return self._parse_response(response)
    
    def chat_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        generation_params: Optional[Dict[str, Any]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Send streaming request to Responses API."""
        
        params = generation_params or {}
        
        # Extract system prompt for instructions
        instructions = self._extract_system_prompt(messages)
        
        # Convert messages to input format
        input_items = self._convert_messages_to_input(messages)
        
        request_kwargs = {
            "model": params.get('model') or self.model,
            "input": input_items,
            "stream": True,
        }
        
        if instructions:
            request_kwargs["instructions"] = instructions
        
        # Reasoning settings
        request_kwargs["reasoning"] = {
            "effort": params.get('reasoning_effort', self.reasoning_effort),
            "summary": params.get('reasoning_summary', self.reasoning_summary)
        }
        
        # Max tokens
        if params.get('max_tokens'):
            request_kwargs["max_output_tokens"] = params['max_tokens']
        
        # Tools
        if tools:
            request_kwargs["tools"] = self._convert_tools_for_api(tools)
        
        logger.info(f"[RESPONSES] Streaming to {request_kwargs['model']} (effort={self.reasoning_effort})")
        
        try:
            stream = retry_on_rate_limit(
                self._client.responses.create,
                **request_kwargs
            )
        except AttributeError as e:
            # SDK doesn't have responses.create - need newer version
            logger.error(f"[RESPONSES] SDK missing responses.create - update openai package: pip install -U openai")
            raise RuntimeError("OpenAI SDK too old - run: pip install -U openai") from e
        except Exception as e:
            logger.error(f"[RESPONSES] REQUEST FAILED: {e}")
            raise
        
        # Track state
        full_content = ""
        full_thinking = ""
        tool_calls_acc = {}  # call_id -> {name, arguments, item_id}
        item_id_to_call_id = {}  # item_id -> call_id (for mapping delta events)
        finish_reason = None
        event_count = 0
        seen_event_types = set()  # Track all event types for debugging
        
        for event in stream:
            event_count += 1
            event_type = event.type
            seen_event_types.add(event_type)
            
            # Reasoning summary events (GPT-5.x thinking summaries)
            if event_type == "response.reasoning_summary_text.delta":
                delta_text = getattr(event, 'delta', '')
                if delta_text:
                    full_thinking += delta_text
                    yield {"type": "thinking", "text": delta_text}
            
            elif event_type == "response.reasoning_summary_text.done":
                # Reasoning summary complete for this item
                pass
            
            # Content text events
            elif event_type == "response.output_text.delta":
                delta_text = getattr(event, 'delta', '')
                if delta_text:
                    full_content += delta_text
                    yield {"type": "content", "text": delta_text}
            
            elif event_type == "response.output_text.done":
                # Text output complete
                pass
            
            # Function call events
            elif event_type == "response.function_call_arguments.delta":
                # Delta events use item_id, not call_id
                event_id = getattr(event, 'call_id', '') or getattr(event, 'item_id', '')
                delta_args = getattr(event, 'delta', '')
                
                # Map item_id to call_id if needed
                call_id = item_id_to_call_id.get(event_id, event_id)
                
                if call_id and call_id not in tool_calls_acc:
                    tool_calls_acc[call_id] = {"name": "", "arguments": "", "item_id": event_id}
                
                if call_id and delta_args:
                    tool_calls_acc[call_id]["arguments"] += delta_args
                    
                    # Only yield if we have a name (from output_item.added)
                    if tool_calls_acc[call_id]["name"]:
                        yield {
                            "type": "tool_call",
                            "index": list(tool_calls_acc.keys()).index(call_id),
                            "id": call_id,
                            "name": tool_calls_acc[call_id]["name"],
                            "arguments": tool_calls_acc[call_id]["arguments"]
                        }
            
            elif event_type == "response.function_call_arguments.done":
                # Function call complete - emit final tool_call event
                event_id = getattr(event, 'call_id', '') or getattr(event, 'item_id', '')
                call_id = item_id_to_call_id.get(event_id, event_id)
                
                if call_id and call_id in tool_calls_acc and tool_calls_acc[call_id]["name"]:
                    yield {
                        "type": "tool_call",
                        "index": list(tool_calls_acc.keys()).index(call_id),
                        "id": call_id,
                        "name": tool_calls_acc[call_id]["name"],
                        "arguments": tool_calls_acc[call_id]["arguments"]
                    }
            
            # Response lifecycle events
            elif event_type == "response.completed" or event_type == "response.done":
                response_obj = getattr(event, 'response', None)
                if response_obj:
                    finish_reason = getattr(response_obj, 'status', 'completed')
            
            # Output item events - capture function names here!
            elif event_type == "response.output_item.added":
                item = getattr(event, 'item', None)
                if item:
                    item_type = getattr(item, 'type', '')
                    item_id = getattr(item, 'id', '')
                    
                    if item_type == 'function_call':
                        # Get both IDs - call_id is for Chat Completions compat, item.id is for streaming events
                        call_id = getattr(item, 'call_id', '') or item_id
                        func_name = getattr(item, 'name', '')
                        
                        # Map item_id to call_id for delta events
                        if item_id:
                            item_id_to_call_id[item_id] = call_id
                        
                        if call_id:
                            tool_calls_acc[call_id] = {"name": func_name, "arguments": "", "item_id": item_id}
                            logger.info(f"[RESPONSES] Function call: {func_name}")
                    else:
                        logger.debug(f"[RESPONSES] Output item: {item_type}")
            
            elif event_type == "response.output_item.done":
                # Output item complete
                pass
            
            # Log unknown events for debugging
            elif not event_type.startswith("response.created") and not event_type.startswith("response.in_progress"):
                logger.debug(f"[RESPONSES] Unhandled event: {event_type}")
        
        # Build final response
        logger.info(f"[RESPONSES] Complete: {len(full_content)}ch content, {len(full_thinking)}ch thinking, {len(tool_calls_acc)} tools")
        
        final_tool_calls = [
            ToolCall(id=call_id, name=tc["name"], arguments=tc["arguments"])
            for call_id, tc in tool_calls_acc.items()
            if tc["name"]
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
        """Parse Responses API response into normalized LLMResponse."""
        
        content_text = ""
        thinking_text = ""
        tool_calls = []
        
        # Output is an array of items
        for item in response.output:
            item_type = getattr(item, 'type', '')
            
            if item_type == 'reasoning':
                # Reasoning summary
                summary = getattr(item, 'summary', [])
                for s in summary:
                    if hasattr(s, 'text'):
                        thinking_text += s.text
            
            elif item_type == 'message':
                # Text content
                content = getattr(item, 'content', [])
                for c in content:
                    if hasattr(c, 'text'):
                        content_text += c.text
            
            elif item_type == 'function_call':
                # Tool call
                tool_calls.append(ToolCall(
                    id=getattr(item, 'call_id', ''),
                    name=getattr(item, 'name', ''),
                    arguments=getattr(item, 'arguments', '{}')
                ))
        
        # Usage
        usage = None
        if hasattr(response, 'usage') and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, 'input_tokens', 0),
                "completion_tokens": getattr(response.usage, 'output_tokens', 0),
                "total_tokens": getattr(response.usage, 'input_tokens', 0) + getattr(response.usage, 'output_tokens', 0)
            }
            # Reasoning tokens
            if hasattr(response.usage, 'output_tokens_details'):
                details = response.usage.output_tokens_details
                reasoning_tokens = getattr(details, 'reasoning_tokens', 0)
                if reasoning_tokens:
                    usage["reasoning_tokens"] = reasoning_tokens
                    logger.info(f"[RESPONSES] Reasoning tokens: {reasoning_tokens}")
        
        if thinking_text:
            logger.info(f"[RESPONSES] Got reasoning summary ({len(thinking_text)} chars)")
        
        return LLMResponse(
            content=content_text if content_text else None,
            tool_calls=tool_calls,
            finish_reason=getattr(response, 'status', None),
            usage=usage
        )
    
    def format_tool_result(
        self,
        tool_call_id: str,
        function_name: str,
        result: str
    ) -> Dict[str, Any]:
        """Format tool result for Responses API."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": function_name,
            "content": result
        }