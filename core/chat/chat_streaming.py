import json
import logging
import re
from typing import Generator, Union, Dict, Any
import config
from .chat_tool_calling import strip_ui_markers, wrap_tool_result
from .llm_providers import LLMResponse, get_generation_params
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


class StreamingChat:
    def __init__(self, main_chat):
        self.main_chat = main_chat
        self.tool_engine = main_chat.tool_engine
        self.cancel_flag = False
        self.current_stream = None
        self.ephemeral = False
        self.is_streaming = False

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

    def chat_stream(self, user_input: str, prefill: str = None, skip_user_message: bool = False) -> Generator[Union[str, Dict[str, Any]], None, None]:
        """
        Stream chat responses. Yields typed events:
        - {"type": "stream_started"} immediately when processing begins
        - {"type": "content", "text": "..."} for text content
        - {"type": "tool_start", "id": "...", "name": "...", "args": {...}} when tool begins
        - {"type": "tool_end", "id": "...", "result": "...", "error": bool} when tool completes
        - {"type": "iteration_start", "iteration": N} before each LLM call
        - {"type": "reload"} for page reload signal
        - str for legacy compatibility (module responses, prefills)
        """
        logger.info(f"[START] [STREAMING START] cancel_flag={self.cancel_flag}, prefill={bool(prefill)}, skip_user={skip_user_message}")
        
        # Publish typing start event
        self.is_streaming = True
        publish(Events.AI_TYPING_START)
        
        # Immediate feedback that backend received the request
        yield {"type": "stream_started"}
        
        try:
            self.main_chat.refresh_spice_if_needed()
            self.cancel_flag = False
            self.current_stream = None
            self.ephemeral = False

            module_name, module_info, processed_text = self.main_chat.module_loader.detect_module(user_input)
            if module_name:
                logger.info(f"[MODULE] Module detected: {module_name}")
                module_config = self.main_chat.module_loader.modules.get(module_name, {})
                should_save = module_config.get("save_to_history", True)
                self.ephemeral = not should_save
                active_chat = self.main_chat.session_manager.get_active_chat_name()
                
                if should_save:
                    self.main_chat.session_manager.add_user_message(user_input)
                
                response_text = self.main_chat.module_loader.process_direct(module_name, processed_text, active_chat)
                
                if should_save:
                    self.main_chat.session_manager.add_assistant_final(response_text)
                
                # Module responses as content events
                yield {"type": "content", "text": response_text}
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
                yield {"type": "content", "text": prefill}
            
            # Handle forced thinking prefill
            force_prefill = None
            if getattr(config, 'FORCE_THINKING', False) and not has_prefill:
                force_prefill = getattr(config, 'THINKING_PREFILL', '<think>')
                messages.append({"role": "assistant", "content": force_prefill})
                logger.info(f"[THINK] Forced thinking prefill: {force_prefill}")
                yield {"type": "content", "text": force_prefill}
            
            active_tools = self.main_chat.function_manager.enabled_tools
            provider_key, provider, model_override = self.main_chat._select_provider()
            
            # Determine effective model (per-chat override or provider default)
            effective_model = model_override if model_override else provider.model
            
            gen_params = get_generation_params(
                provider_key,
                effective_model,
                getattr(config, 'LLM_PROVIDERS', {})
            )
            
            # Pass model override to provider if set
            if model_override:
                gen_params['model'] = model_override

            tool_call_count = 0

            for iteration in range(config.MAX_TOOL_ITERATIONS):
                if self.cancel_flag:
                    logger.info(f"[STOP] [STREAMING] Cancelled at iteration {iteration + 1}")
                    break
                
                logger.info(f"--- Streaming Iteration {iteration + 1}/{config.MAX_TOOL_ITERATIONS} ---")
                
                # Signal UI that we're starting a new LLM call (useful after tool completion)
                yield {"type": "iteration_start", "iteration": iteration + 1}
                
                current_response = ""
                tool_calls = []
                final_response = None
                
                try:
                    logger.info(f"[STREAM] Creating provider stream [{provider.provider_name}] (effective_model={effective_model})")
                    self.current_stream = provider.chat_completion_stream(
                        messages,
                        tools=active_tools if active_tools else None,
                        generation_params=gen_params
                    )
                    
                    chunk_count = 0
                    for event in self.current_stream:
                        chunk_count += 1
                        
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled at chunk {chunk_count}")
                            self._cleanup_stream()
                            break
                        
                        event_type = event.get("type")
                        
                        if event_type == "content":
                            text = event.get("text", "")
                            current_response += text
                            yield {"type": "content", "text": text}
                        
                        elif event_type == "tool_call":
                            idx = event.get("index", 0)
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
                        break
                
                except Exception as e:
                    logger.error(f"[ERR] [STREAMING] Iteration {iteration + 1} failed: {e}", exc_info=True)
                    self._cleanup_stream()
                    raise
                
                if tool_calls and any(tc.get("id") and tc.get("function", {}).get("name") for tc in tool_calls):
                    logger.info(f"[TOOL] Processing {len(tool_calls)} tool call(s)")
                    
                    tool_calls_to_execute = tool_calls[:config.MAX_PARALLEL_TOOLS]
                    
                    # Combine prefill with current response for history
                    full_content = prefill + current_response if has_prefill else current_response
                    
                    # Store the actual content (no filtering/wrapping)
                    messages.append({
                        "role": "assistant",
                        "content": full_content,
                        "tool_calls": tool_calls_to_execute
                    })
                    self.main_chat.session_manager.add_assistant_with_tool_calls(full_content, tool_calls_to_execute)
                    
                    for tool_call in tool_calls_to_execute:
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled before tool execution")
                            break
                        
                        if not tool_call.get("id") or not tool_call.get("function", {}).get("name"):
                            continue
                        
                        tool_call_count += 1
                        function_name = tool_call["function"]["name"]
                        tool_call_id = tool_call["id"]
                        
                        # Close any open think tag in streamed content
                        if '<think>' in current_response and current_response.rfind('<think>') > current_response.rfind('</think>'):
                            yield {"type": "content", "text": "</think>\n\n"}
                        
                        try:
                            function_args = json.loads(tool_call["function"]["arguments"])
                        except json.JSONDecodeError:
                            function_args = {}
                        
                        # Emit typed tool_start event
                        yield {
                            "type": "tool_start",
                            "id": tool_call_id,
                            "name": function_name,
                            "args": function_args
                        }
                        
                        try:
                            function_result = self.main_chat.function_manager.execute_function(function_name, function_args)
                            result_str = str(function_result)
                            clean_result = strip_ui_markers(result_str)
                            
                            # Emit typed tool_end event
                            yield {
                                "type": "tool_end",
                                "id": tool_call_id,
                                "name": function_name,
                                "result": clean_result[:500] if len(clean_result) > 500 else clean_result,
                                "error": False
                            }
                            
                            wrapped_msg = provider.format_tool_result(
                                tool_call_id,
                                function_name,
                                clean_result
                            )
                            messages.append(wrapped_msg)
                            logger.info(f"[OK] [STREAMING] Tool {function_name} executed successfully")
                            
                            if function_name != "end_and_reset_chat":
                                self.main_chat.session_manager.add_tool_result(
                                    tool_call_id,
                                    function_name,
                                    result_str,
                                    inputs=function_args
                                )
                            
                            if function_name == "end_and_reset_chat":
                                logger.info("[RESET] [STREAMING] Chat reset detected")
                                yield {"type": "reload"}
                                return

                        except Exception as tool_error:
                            logger.error(f"Tool execution error: {tool_error}", exc_info=True)
                            error_result = f"Error: {str(tool_error)}"
                            
                            yield {
                                "type": "tool_end",
                                "id": tool_call_id,
                                "name": function_name,
                                "result": error_result,
                                "error": True
                            }
                            
                            wrapped_msg = provider.format_tool_result(
                                tool_call_id,
                                function_name,
                                error_result
                            )
                            messages.append(wrapped_msg)

                            if function_name != "end_and_reset_chat":
                                self.main_chat.session_manager.add_tool_result(
                                    tool_call_id,
                                    function_name,
                                    error_result,
                                    inputs=function_args
                                )
                    
                    if self.cancel_flag:
                        break

                    continue
                
                else:
                    logger.info(f"[OK] Final response received after {iteration + 1} iteration(s)")
                    
                    full_response = current_response
                    
                    if has_prefill:
                        full_response = prefill + full_response
                    
                    if force_prefill:
                        full_response = force_prefill + full_response
                    
                    self.main_chat.session_manager.add_assistant_final(full_response)
                    return
            
            # Loop exhausted - force final response
            logger.warning(f"[STREAMING] Exceeded max iterations ({config.MAX_TOOL_ITERATIONS}). Forcing final answer.")
            
            messages.append({
                "role": "user",
                "content": "You've used tools multiple times. Stop using tools now and provide your final answer based on the information you gathered."
            })
            
            try:
                yield {"type": "content", "text": "\n\n"}
                
                final_stream = provider.chat_completion_stream(
                    messages,
                    tools=None,
                    generation_params=gen_params
                )
                
                final_response = ""
                for event in final_stream:
                    if self.cancel_flag:
                        break
                    
                    if event.get("type") == "content":
                        chunk = event.get("text", "")
                        final_response += chunk
                        yield {"type": "content", "text": chunk}
                    elif event.get("type") == "done":
                        break
                
                if final_response:
                    full_final = (force_prefill or "") + final_response
                    self.main_chat.session_manager.add_assistant_final(full_final)
                else:
                    fallback = f"I used {tool_call_count} tools and gathered information."
                    yield {"type": "content", "text": fallback}
                    self.main_chat.session_manager.add_assistant_final(fallback)
                    
            except Exception as final_error:
                logger.error(f"[STREAMING] Forced final response failed: {final_error}")
                error_msg = f"I completed {tool_call_count} tool calls but encountered an error generating the final response."
                yield {"type": "content", "text": error_msg}
                self.main_chat.session_manager.add_assistant_final(error_msg)

        except Exception as e:
            logger.error(f"[ERR] [STREAMING FATAL] Unhandled error: {e}", exc_info=True)
            self._cleanup_stream()
            raise
        
        finally:
            logger.info(f"[CLEANUP] [STREAMING FINALLY] Cleaning up, cancel_flag={self.cancel_flag}")
            self._cleanup_stream()
            self.cancel_flag = False
            self.is_streaming = False
            publish(Events.AI_TYPING_END)