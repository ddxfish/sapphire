import json
import logging
import time
import re
import uuid
from typing import Dict, Any, Optional, List

from .llm_providers import LLMResponse
from .llm_providers.base import BaseProvider

logger = logging.getLogger(__name__)


def filter_to_thinking_only(content: str) -> str:
    """
    Extract only <think> tags, removing all other content.
    Used for assistant messages with tool_calls to prevent premature responses.
    """
    if not content:
        return ""
    
    think_pattern = r'<(?:seed:)?think[^>]*>.*?</(?:seed:)?think[^>]*>'
    think_matches = re.findall(think_pattern, content, re.DOTALL | re.IGNORECASE)
    
    filtered = "\n\n".join(think_matches) if think_matches else ""
    
    # If no think tags but there's content, wrap FULL content (don't truncate!)
    if not filtered and content:
        filtered = f"<think>{content.strip()}</think>"
        logger.info(f"No think tags found, wrapped full content in think block")
    
    if filtered != content:
        logger.info(f"Stripped prose: {len(content)} -> {len(filtered)} chars")
    
    return filtered

def strip_ui_markers(content: str) -> str:
    """
    Strip UI-only markers from content before sending to LLM.
    
    Removes patterns like:
    - <<IMG::image_id>>
    - <<FILE::file_id>>
    - Any other <<MARKER::data>> patterns
    
    These markers are kept in history for UI parsing but removed from LLM context.
    """
    if not content:
        return content
    
    # Pattern to match <<TYPE::data>> markers
    marker_pattern = r'<<[A-Z]+::[^>]+>>\s*'
    
    # Remove all markers
    clean = re.sub(marker_pattern, '', content)
    
    # Log if we stripped anything
    if clean != content:
        markers_found = re.findall(marker_pattern, content)
        logger.info(f"[CLEANUP] Stripped {len(markers_found)} UI markers from tool result for LLM context")
        for marker in markers_found:
            logger.debug(f"   - {marker.strip()}")
    
    return clean.strip()


def wrap_tool_result(tool_call_id: str, function_name: str, result: str) -> Dict[str, Any]:
    """
    Wrap tool results in standard OpenAI tool format.
    
    Args:
        tool_call_id: The tool call ID
        function_name: Name of the function that was called
        result: The result string from the function (ALREADY STRIPPED of UI markers)
    
    Returns:
        Properly formatted message dict for the LLM
    """
    clean_result = strip_ui_markers(result) if '<<' in result else result
    
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": function_name,
        "content": clean_result
    }


class ToolCallingEngine:
    def __init__(self, function_manager):
        self.function_manager = function_manager

    def call_llm_with_metrics(self, provider: BaseProvider, messages: List, gen_params: Dict, tools: List = None) -> LLMResponse:
        """Call LLM with performance metrics via provider abstraction."""
        logger.info(f"[TOOL] LLM CALL [{provider.provider_name}]: {len(messages)} messages, {sum(len(str(m)) for m in messages)} chars")
        
        start_time = time.time()
        
        response = provider.chat_completion(messages, tools=tools, generation_params=gen_params)
        
        elapsed = time.time() - start_time
        
        # Log performance
        try:
            if response.usage and response.usage.get('completion_tokens'):
                tps = response.usage['completion_tokens'] / elapsed
                logger.info(f"LLM ({provider.model}): {elapsed:.2f}s, {len(str(response.content))} chars, {tps:.1f} t/s")
            else:
                logger.info(f"LLM ({provider.model}): {elapsed:.2f}s, {len(str(response.content))} chars")
        except (AttributeError, ZeroDivisionError, TypeError):
            pass
        
        return response

    def extract_function_call_from_text(self, text: str) -> Optional[Dict]:
        """Extract function calls from text (LM Studio, Qwen3, GLM, etc compatibility)."""
        if not text:
            return None

        # Format 1: {"function_call": {"name": "...", "arguments": {...}}}
        pattern = r'(\{"function_call":\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})'
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)

        if match:
            try:
                parsed = json.loads(match.group(1))
                if "function_call" in parsed and "name" in parsed["function_call"]:
                    return parsed
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for format 1: {e}")

        # Format 2: <function_call>{"name": "...", "arguments": {...}}</function_call> (Qwen3, etc)
        xml_pattern = r'<function_call>\s*(\{.*?\})\s*</function_call>'
        xml_match = re.search(xml_pattern, text, re.IGNORECASE | re.DOTALL)

        if xml_match:
            try:
                inner = json.loads(xml_match.group(1))
                if "name" in inner:
                    return {"function_call": inner}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for XML format: {e}")

        # Format 3: <tool_call>{"name": "...", "arguments": {...}}</tool_call> (GLM, etc)
        tool_call_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        tool_call_match = re.search(tool_call_pattern, text, re.IGNORECASE | re.DOTALL)

        if tool_call_match:
            try:
                inner = json.loads(tool_call_match.group(1))
                if "name" in inner:
                    return {"function_call": inner}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for tool_call format: {e}")

        # Format 4: Raw JSON with name field (fallback) - {"name": "tool_name", "arguments": {...}}
        # Only match if it looks like a standalone tool call (not embedded in prose)
        raw_pattern = r'^\s*(\{"name":\s*"[^"]+",\s*"arguments":\s*\{[^{}]*\}\s*\})\s*$'
        raw_match = re.search(raw_pattern, text, re.MULTILINE)

        if raw_match:
            try:
                inner = json.loads(raw_match.group(1))
                if "name" in inner:
                    return {"function_call": inner}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for raw format: {e}")

        return None

    def format_tool_calls_for_conversation(self, tool_calls):
        """Convert tool_calls to proper format."""
        tool_calls_formatted = []
        for tool_call in tool_calls:
            tool_calls_formatted.append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                }
            })
        return tool_calls_formatted

    def execute_tool_calls(self, tool_calls, messages, history, provider: BaseProvider = None, scopes=None):
        """
        Execute tool calls and add results to messages array AND history.
        
        Key behaviors:
        - Tool results sent to LLM have UI markers STRIPPED (clean context)
        - Tool results saved to history contain raw content (structured JSON handles display)
        - Reset function results are NOT saved to history
        - Uses provider.format_tool_result() if provider given (for Claude compatibility)
        
        Returns number of tools executed.
        
        Note: Caller should slice tool_calls to MAX_PARALLEL_TOOLS before calling.
        """
        tools_executed = 0
        
        for tool_call in tool_calls:
            function_name = tool_call["function"]["name"]
            
            try:
                function_args = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                logger.error(f"Failed to parse tool arguments: {tool_call['function']['arguments']}")
                error_result = "Error: Invalid JSON arguments."
                
                if provider:
                    wrapped_msg = provider.format_tool_result(tool_call["id"], function_name, error_result)
                else:
                    wrapped_msg = wrap_tool_result(tool_call["id"], function_name, error_result)
                messages.append(wrapped_msg)
                
                if history and function_name != "end_and_reset_chat":
                    history.add_tool_result(tool_call["id"], function_name, error_result)
                continue

            try:
                function_result = self.function_manager.execute_function(function_name, function_args, scopes=scopes)
            except Exception as tool_error:
                logger.error(f"Tool execution failed for {function_name}: {tool_error}", exc_info=True)
                function_result = f"Tool '{function_name}' failed: {str(tool_error)}"

            result_str = str(function_result)
            clean_result = strip_ui_markers(result_str)

            if provider:
                wrapped_msg = provider.format_tool_result(tool_call["id"], function_name, clean_result)
            else:
                wrapped_msg = wrap_tool_result(tool_call["id"], function_name, clean_result)
            messages.append(wrapped_msg)
            
            logger.info(f"[OK] Tool result added to messages")
            logger.debug(f"   Message role: {wrapped_msg['role']}")
            logger.debug(f"   Content preview: {str(wrapped_msg.get('content', ''))[:100]}")
            
            # Don't save reset tool result - history is about to be wiped anyway
            if history and function_name != "end_and_reset_chat":
                logger.info(f"[SAVE] Saving tool result for: {function_name}")
                history.add_tool_result(tool_call["id"], function_name, result_str, inputs=function_args)
            elif not history:
                logger.debug(f"[ISOLATED] No history manager, skipping save for: {function_name}")
            else:
                logger.info(f"[RESET] Skipping history save for reset tool")

            tools_executed += 1
            logger.info(f"[OK] Executed tool: {function_name}")

        return tools_executed

    def execute_text_based_tool_call(self, function_call_data, filtered_content, messages, history, provider: BaseProvider = None, scopes=None):
        """
        Execute text-based function call (LM Studio compatibility).
        
        Args:
            function_call_data: The parsed function call data
            filtered_content: Pre-filtered content (thinking only) from caller
            messages: Messages array to append to
            history: History manager to save to
            provider: Optional provider for format_tool_result (Claude compatibility)
        
        Returns tool call ID.
        """
        tool_call_id = f"call_{uuid.uuid4().hex[:8]}"
        function_name = function_call_data["function_call"]["name"]
        function_args = function_call_data["function_call"]["arguments"]

        tool_calls_formatted = [{
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": function_name,
                "arguments": json.dumps(function_args)
            }
        }]
        
        messages.append({
            "role": "assistant",
            "content": filtered_content,
            "tool_calls": tool_calls_formatted
        })
        
        if history:
            history.add_assistant_with_tool_calls(filtered_content, tool_calls_formatted)

        try:
            function_result = self.function_manager.execute_function(function_name, function_args, scopes=scopes)
        except Exception as tool_error:
            logger.error(f"Text-based tool failed for {function_name}: {tool_error}")
            function_result = f"Tool '{function_name}' failed: {str(tool_error)}"
        
        result_str = str(function_result)
        clean_result = strip_ui_markers(result_str)
        
        if provider:
            wrapped_msg = provider.format_tool_result(tool_call_id, function_name, clean_result)
        else:
            wrapped_msg = wrap_tool_result(tool_call_id, function_name, clean_result)
        messages.append(wrapped_msg)
        
        # Don't save reset tool result
        if history and function_name != "end_and_reset_chat":
            history.add_tool_result(tool_call_id, function_name, result_str, inputs=function_args)
        elif not history:
            logger.debug(f"[ISOLATED] No history manager, skipping save for: {function_name}")
        else:
            logger.info(f"[RESET] Skipping history save for reset tool")
        
        logger.info(f"[OK] Executed text-based tool: {function_name}")
        return tool_call_id