import json
import logging
import re
from typing import Generator
import config
from .chat_tool_calling import strip_ui_markers, wrap_tool_result, filter_to_thinking_only
from .llm_providers import LLMResponse, get_generation_params

logger = logging.getLogger(__name__)


class StreamingChat:
    def __init__(self, main_chat):
        self.main_chat = main_chat
        self.tool_engine = main_chat.tool_engine
        self.cancel_flag = False
        self.current_stream = None
        self.ephemeral = False  # True when response shouldn't persist (save_to_history: false)

    def _cleanup_stream(self):
        """Safely close current stream if it exists."""
        if self.current_stream:
            try:
                self.current_stream.close()
                logger.info("[CLEANUP] Stream closed")
            except Exception as e:
                logger.warning(f"[CLEANUP] Stream close warning: {e}")
            finally:
                self.current_stream = None

    def chat_stream(self, user_input: str, prefill: str = None, skip_user_message: bool = False) -> Generator[str, None, None]:
        logger.info(f"[START] [STREAMING START] User: said something here, cancel_flag={self.cancel_flag}, prefill={bool(prefill)}, skip_user={skip_user_message}")
        
        try:
            self.main_chat.refresh_spice_if_needed()
            self.cancel_flag = False
            self.current_stream = None
            self.ephemeral = False  # Reset for each stream

            module_name, module_info, processed_text = self.main_chat.module_loader.detect_module(user_input)
            if module_name:
                logger.info(f"[MODULE] Module detected: {module_name}")
                module_config = self.main_chat.module_loader.modules.get(module_name, {})
                should_save = module_config.get("save_to_history", True)
                self.ephemeral = not should_save  # Signal frontend to skip TTS/swap
                active_chat = self.main_chat.session_manager.get_active_chat_name()
                
                # Check save_to_history BEFORE adding any messages
                if should_save:
                    self.main_chat.session_manager.add_user_message(user_input)
                
                response_text = self.main_chat.module_loader.process_direct(module_name, processed_text, active_chat)
                
                if should_save:
                    self.main_chat.session_manager.add_assistant_final(response_text)
                
                yield response_text
                return

            messages = self.main_chat._build_base_messages(user_input)
            
            if not skip_user_message:
                self.main_chat.session_manager.add_user_message(user_input)
            else:
                logger.info("[CONTINUE] Skipping user message addition (continuing from existing)")
            
            # Handle manual continue prefill
            has_prefill = bool(prefill)
            if has_prefill:
                messages.append({"role": "assistant", "content": prefill})
                logger.info(f"[CONTINUE] Continuing with {len(prefill)} char prefill")
                yield prefill
            
            # Handle forced thinking prefill
            force_prefill = None
            if getattr(config, 'FORCE_THINKING', False) and not has_prefill:
                force_prefill = getattr(config, 'THINKING_PREFILL', '<think>')
                messages.append({"role": "assistant", "content": force_prefill})
                logger.info(f"[THINK] Forced thinking prefill: {force_prefill}")
                yield force_prefill
            
            active_tools = self.main_chat.function_manager.enabled_tools
            provider_key, provider = self.main_chat._select_provider()
            
            # Get generation params for this provider/model
            gen_params = get_generation_params(
                provider_key,
                provider.model,
                getattr(config, 'LLM_PROVIDERS', {})
            )

            tool_call_count = 0
            last_tool_name = None
            
            should_filter_thinking = getattr(config, 'DELETE_EARLY_THINK_PROSE', True)

            for iteration in range(config.MAX_TOOL_ITERATIONS):
                if self.cancel_flag:
                    logger.info(f"[STOP] [STREAMING] Cancelled at iteration {iteration + 1}")
                    break
                
                logger.info(f"--- Streaming Iteration {iteration + 1}/{config.MAX_TOOL_ITERATIONS} ---")
                
                if getattr(config, 'DEBUG_TOOL_CALLING', False):
                    logger.info(f"[MSGS] [STREAMING] Messages being sent ({len(messages)} total):")
                    for i, msg in enumerate(messages[-5:]):
                        role = msg.get("role")
                        content = str(msg.get("content", ""))
                        has_tools = "tool_calls" in msg
                        
                        if role == "assistant" and has_tools:
                            logger.info(f"  [{i}] {role}: {len(content)} chars + tool_calls")
                        elif role == "tool":
                            logger.info(f"  [{i}] {role}: {msg.get('name')}")
                        else:
                            logger.info(f"  [{i}] {role}: {content[:80]}...")
                
                current_response = ""
                tool_calls = []
                final_response = None
                
                try:
                    logger.info(f"[STREAM] Creating provider stream [{provider.provider_name}] (model={provider.model})")
                    self.current_stream = provider.chat_completion_stream(
                        messages,
                        tools=active_tools if active_tools else None,
                        generation_params=gen_params
                    )
                    logger.info(f"[STREAM] Stream created, starting iteration")
                    
                    chunk_count = 0
                    for event in self.current_stream:
                        chunk_count += 1
                        
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled at chunk {chunk_count}")
                            self._cleanup_stream()
                            break
                        
                        event_type = event.get("type")
                        
                        if event_type == "content":
                            current_response += event.get("text", "")
                            yield event.get("text", "")
                        
                        elif event_type == "tool_call":
                            idx = event.get("index", 0)
                            # Expand tool_calls list as needed
                            while len(tool_calls) <= idx:
                                tool_calls.append({
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                })
                            
                            if event.get("id"):
                                tool_calls[idx]["id"] = event["id"]
                            if event.get("name"):
                                tool_calls[idx]["function"]["name"] = event["name"]
                            if event.get("arguments"):
                                tool_calls[idx]["function"]["arguments"] = event["arguments"]
                        
                        elif event_type == "done":
                            final_response = event.get("response")
                    
                    logger.info(f"[STREAM] Stream iteration complete ({chunk_count} chunks)")
                    self._cleanup_stream()
                    
                    if self.cancel_flag:
                        logger.info(f"[STOP] [STREAMING] Exiting after stream cancellation")
                        break
                
                except Exception as e:
                    logger.error(f"[ERR] [STREAMING] Iteration {iteration + 1} failed: {e}", exc_info=True)
                    self._cleanup_stream()
                    raise
                
                if tool_calls and any(tc.get("id") and tc.get("function", {}).get("name") for tc in tool_calls):
                    logger.info(f"[TOOL] Processing {len(tool_calls)} tool call(s)")
                    
                    # Slice to MAX_PARALLEL_TOOLS limit
                    tool_calls_to_execute = tool_calls[:config.MAX_PARALLEL_TOOLS]
                    if len(tool_calls_to_execute) < len(tool_calls):
                        logger.info(f"[LIMIT] Executing {len(tool_calls_to_execute)}/{len(tool_calls)} tools (MAX_PARALLEL_TOOLS={config.MAX_PARALLEL_TOOLS})")
                    
                    # Combine manual prefill or force prefill with current response
                    full_content = prefill + current_response if has_prefill else current_response
                    
                    if should_filter_thinking:
                        filtered_content = filter_to_thinking_only(full_content)
                        logger.info(f"[TRIM] [STREAMING] Thinking filter ENABLED")
                    else:
                        filtered_content = full_content
                        logger.info(f"[TRIM] [STREAMING] Thinking filter DISABLED")
                    
                    messages.append({
                        "role": "assistant",
                        "content": filtered_content,
                        "tool_calls": tool_calls_to_execute
                    })
                    self.main_chat.session_manager.add_assistant_with_tool_calls(filtered_content, tool_calls_to_execute)
                    
                    for tool_call in tool_calls_to_execute:
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled before tool execution")
                            break
                        
                        if not tool_call.get("id") or not tool_call.get("function", {}).get("name"):
                            continue
                        
                        tool_call_count += 1
                        function_name = tool_call["function"]["name"]
                        last_tool_name = function_name
                        
                        if '<think>' in current_response and current_response.rfind('<think>') > current_response.rfind('</think>'):
                            yield '</think>\n\n'
                        
                        try:
                            function_args = json.loads(tool_call["function"]["arguments"])
                        except json.JSONDecodeError:
                            function_args = {}
                        
                        yield f"\n\nRunning {function_name}...\n\n"
                        
                        try:
                            function_result = self.main_chat.function_manager.execute_function(function_name, function_args)
                            result_str = str(function_result)
                            clean_result = strip_ui_markers(result_str)
                            
                            # Use provider for format_tool_result (Claude compatibility)
                            wrapped_msg = provider.format_tool_result(
                                tool_call["id"],
                                function_name,
                                clean_result
                            )
                            messages.append(wrapped_msg)
                            logger.info(f"[OK] [STREAMING] Tool {function_name} executed successfully")
                            
                           
                            if function_name != "end_and_reset_chat":
                                self.main_chat.session_manager.add_tool_result(
                                    tool_call["id"],
                                    function_name,
                                    result_str,
                                    inputs=function_args
                                )
                            
                            if function_name == "end_and_reset_chat":
                                logger.info("[RESET] [STREAMING] Chat reset detected, ending stream without final response")
                                yield "\n\n<<RELOAD_PAGE>>"
                                return

                        except Exception as tool_error:
                            logger.error(f"Tool execution error: {tool_error}", exc_info=True)
                            error_result = f"Error: {str(tool_error)}"
                            
                            wrapped_msg = provider.format_tool_result(
                                tool_call["id"],
                                function_name,
                                error_result
                            )
                            messages.append(wrapped_msg)
                            

                            if function_name != "end_and_reset_chat":
                                self.main_chat.session_manager.add_tool_result(
                                    tool_call["id"],
                                    function_name,
                                    error_result,
                                    inputs=function_args
                                )
                    
                    if self.cancel_flag:
                        logger.info(f"[STOP] [STREAMING] Exiting after tool cancellation")
                        break

                    continue
                
                else:
                    logger.info(f"[OK] Final response received after {iteration + 1} iteration(s)")
                    
                    full_response = current_response
                    
                    # Prepend manual prefill
                    if has_prefill:
                        full_response = prefill + full_response
                        logger.info(f"[SAVE] Added manual prefill: {len(prefill)} chars")
                    
                    # Prepend force prefill
                    if force_prefill:
                        full_response = force_prefill + full_response
                        logger.info(f"[SAVE] Added force prefill: {len(force_prefill)} chars")
                    
                    if has_prefill or force_prefill:
                        logger.info(f"[SAVE] Total combined response: {len(full_response)} chars")
                    
                    self.main_chat.session_manager.add_assistant_final(full_response)
                    break
            
            logger.info(f"[OK] Streaming chat completed successfully")

        except Exception as e:
            logger.error(f"[ERR] [STREAMING FATAL] Unhandled error: {e}", exc_info=True)
            self._cleanup_stream()
            raise
        
        finally:
            logger.info(f"[CLEANUP] [STREAMING FINALLY] Cleaning up, cancel_flag={self.cancel_flag}")
            self._cleanup_stream()
            self.cancel_flag = False