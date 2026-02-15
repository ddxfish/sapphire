import json
import logging
import re
import time
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

    def chat_stream(self, user_input: str, prefill: str = None, skip_user_message: bool = False, images: list = None, files: list = None) -> Generator[Union[str, Dict[str, Any]], None, None]:
        """
        Stream chat responses. Yields typed events:
        - {"type": "stream_started"} immediately when processing begins
        - {"type": "content", "text": "..."} for text content
        - {"type": "thinking", "text": "..."} for thinking content (rendered with tags by UI)
        - {"type": "tool_start", "id": "...", "name": "...", "args": {...}} when tool begins
        - {"type": "tool_end", "id": "...", "result": "...", "error": bool} when tool completes
        - {"type": "iteration_start", "iteration": N} before each LLM call
        - {"type": "reload"} for page reload signal
        - str for legacy compatibility (module responses, prefills)

        Args:
            user_input: Text input from user
            prefill: Optional assistant prefill for continue mode
            skip_user_message: Don't add user message to history (continue mode)
            images: Optional list of {"type": "image", "data": "...", "media_type": "..."}
            files: Optional list of {"filename": "...", "text": "..."}
        """
        logger.info(f"[START] [STREAMING START] cancel_flag={self.cancel_flag}, prefill={bool(prefill)}, skip_user={skip_user_message}, images={len(images) if images else 0}, files={len(files) if files else 0}")
        
        # Publish typing start event
        self.is_streaming = True
        publish(Events.AI_TYPING_START)
        
        # Immediate feedback that backend received the request
        yield {"type": "stream_started"}

        # Check if current prompt requires privacy mode
        try:
            from core.modules.system.prompt_state import is_current_prompt_private
            from core.privacy import is_privacy_mode
            if is_current_prompt_private() and not is_privacy_mode():
                yield {"type": "error", "text": "This prompt requires Privacy Mode to be enabled."}
                return
        except ImportError:
            pass

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

            # Update state engine FIRST (before building messages) based on current settings
            self.main_chat._update_state_engine()
            
            messages = self.main_chat._build_base_messages(user_input, images=images, files=files)

            if not skip_user_message:
                # Build content list if files or images present, otherwise just text
                if files or images:
                    user_content = [{"type": "text", "text": user_input}]
                    for f in (files or []):
                        user_content.append({
                            "type": "file",
                            "filename": f.get("filename", ""),
                            "text": f.get("text", "")
                        })
                    for img in (images or []):
                        user_content.append({
                            "type": "image",
                            "data": img.get("data", ""),
                            "media_type": img.get("media_type", "image/jpeg")
                        })
                    self.main_chat.session_manager.add_user_message(user_content)
                else:
                    self.main_chat.session_manager.add_user_message(user_input)
            else:
                logger.info("[CONTINUE] Skipping user message addition (continuing from existing)")
            
            # Handle manual continue prefill
            has_prefill = bool(prefill)
            if has_prefill:
                # Strip trailing whitespace - Claude API rejects it
                clean_prefill = prefill.rstrip()
                messages.append({"role": "assistant", "content": clean_prefill})
                logger.info(f"[CONTINUE] Continuing with {len(clean_prefill)} char prefill")
                yield {"type": "content", "text": prefill}  # Show original to user
            
            # Handle forced thinking prefill - disabled when continuing
            force_prefill = None
            if getattr(config, 'FORCE_THINKING', False) and not has_prefill:
                force_prefill = getattr(config, 'THINKING_PREFILL', '<think>')
                # Strip trailing whitespace for Claude compatibility
                messages.append({"role": "assistant", "content": force_prefill.rstrip()})
                logger.info(f"[THINK] Forced thinking prefill: {force_prefill}")
                yield {"type": "content", "text": force_prefill}
            
            # Set memory and goal scopes for this chat context
            chat_settings = self.main_chat.session_manager.get_chat_settings()
            memory_scope = chat_settings.get('memory_scope', 'default')
            self.main_chat.function_manager.set_memory_scope(memory_scope if memory_scope != 'none' else None)
            goal_scope = chat_settings.get('goal_scope', 'default')
            self.main_chat.function_manager.set_goal_scope(goal_scope if goal_scope != 'none' else None)
            knowledge_scope = chat_settings.get('knowledge_scope', 'default')
            self.main_chat.function_manager.set_knowledge_scope(knowledge_scope if knowledge_scope != 'none' else None)
            people_scope = chat_settings.get('people_scope', 'default')
            self.main_chat.function_manager.set_people_scope(people_scope if people_scope != 'none' else None)

            # Send only enabled tools - model should only know about active tools
            enabled_tools = self.main_chat.function_manager.enabled_tools
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
            
            # CRITICAL: Disable thinking for continue operations
            # Claude requires thinking blocks with signatures - we can't fake them
            if has_prefill:
                gen_params['disable_thinking'] = True
                logger.info("[CONTINUE] Disabled thinking for continue (can't replay signatures)")

            tool_call_count = 0

            for iteration in range(config.MAX_TOOL_ITERATIONS):
                if self.cancel_flag:
                    logger.info(f"[STOP] [STREAMING] Cancelled at iteration {iteration + 1}")
                    break
                
                logger.info(f"--- Streaming Iteration {iteration + 1}/{config.MAX_TOOL_ITERATIONS} ---")
                
                # Signal UI that we're starting a new LLM call (useful after tool completion)
                yield {"type": "iteration_start", "iteration": iteration + 1}
                
                # Track content and thinking separately
                current_content = ""
                current_thinking = ""
                thinking_raw = None
                metadata = None
                in_thinking = False
                
                tool_calls = []
                final_response = None
                first_chunk_time = None  # Track when generation actually starts
                
                try:
                    logger.info(f"[STREAM] Creating provider stream [{provider.provider_name}] (effective_model={effective_model})")
                    self.current_stream = provider.chat_completion_stream(
                        messages,
                        tools=enabled_tools if enabled_tools else None,
                        generation_params=gen_params
                    )
                    
                    chunk_count = 0
                    for event in self.current_stream:
                        chunk_count += 1
                        
                        # Start timing from first actual chunk (not stream creation)
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                        
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled at chunk {chunk_count}")
                            self._cleanup_stream()
                            break
                        
                        event_type = event.get("type")
                        
                        if event_type == "content":
                            text = event.get("text", "")
                            current_content += text
                            yield {"type": "content", "text": text}
                        
                        elif event_type == "thinking":
                            # Thinking from Claude - emit as content with tags for UI
                            text = event.get("text", "")
                            current_thinking += text
                            
                            # Emit thinking wrapped in tags for UI rendering
                            if not in_thinking:
                                yield {"type": "content", "text": "<think>"}
                                in_thinking = True
                            yield {"type": "content", "text": text}
                        
                        elif event_type == "tool_call":
                            # Close thinking tag if open before tool calls
                            if in_thinking:
                                yield {"type": "content", "text": "</think>\n\n"}
                                in_thinking = False
                            
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
                            # Close thinking tag if still open
                            if in_thinking:
                                yield {"type": "content", "text": "</think>\n\n"}
                                in_thinking = False
                            
                            final_response = event.get("response")
                            # Capture thinking data from done event
                            if event.get("thinking"):
                                current_thinking = event["thinking"]
                            if event.get("thinking_raw"):
                                thinking_raw = event["thinking_raw"]
                            if event.get("metadata"):
                                metadata = event["metadata"]
                    
                    logger.info(f"[STREAM] Stream iteration complete ({chunk_count} chunks)")
                    self._cleanup_stream()
                    
                    if self.cancel_flag:
                        break
                
                except Exception as e:
                    logger.error(f"[ERR] [STREAMING] Iteration {iteration + 1} failed: {e}", exc_info=True)
                    self._cleanup_stream()
                    raise
                
                # Build metadata if not provided by provider
                if not metadata:
                    iteration_end_time = time.time()
                    # Use first chunk time if available, else end time (edge case: no chunks)
                    gen_start = first_chunk_time or iteration_end_time
                    duration = round(iteration_end_time - gen_start, 2)
                    # Rough token estimate: ~4 chars per token
                    est_content_tokens = len(current_content) // 4 if current_content else 0
                    est_thinking_tokens = len(current_thinking) // 4 if current_thinking else 0
                    total_tokens = est_content_tokens + est_thinking_tokens
                    
                    metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(gen_start)),
                        "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(iteration_end_time)),
                        "duration_seconds": duration,
                        "tokens": {
                            "content": est_content_tokens,
                            "thinking": est_thinking_tokens,
                            "total": total_tokens,
                            "estimated": True  # Flag that these are estimates
                        },
                        "tokens_per_second": round(total_tokens / duration, 1) if duration > 0 else 0
                    }
                
                # Generate fallback IDs for tool calls missing them (GLM, some OpenAI-compat APIs)
                for tc in tool_calls:
                    if tc.get("function", {}).get("name") and not tc.get("id"):
                        tc["id"] = f"call_{iteration}_{tool_calls.index(tc)}"
                        logger.info(f"[TOOL] Generated fallback ID for tool call: {tc['function']['name']}")

                if tool_calls and any(tc.get("id") and tc.get("function", {}).get("name") for tc in tool_calls):
                    logger.info(f"[TOOL] Processing {len(tool_calls)} tool call(s)")
                    
                    tool_calls_to_execute = tool_calls[:config.MAX_PARALLEL_TOOLS]
                    
                    # Combine prefill with current content for history
                    full_content = prefill + current_content if has_prefill else current_content
                    
                    # Store message with tool calls - include thinking_raw for Claude tool cycles
                    messages.append({
                        "role": "assistant",
                        "content": full_content,
                        "tool_calls": tool_calls_to_execute,
                        "thinking_raw": thinking_raw  # Has signatures for Claude API
                    })
                    
                    # Save to history with new schema
                    self.main_chat.session_manager.add_assistant_with_tool_calls(
                        content=full_content,
                        tool_calls=tool_calls_to_execute,
                        thinking=current_thinking if current_thinking else None,
                        thinking_raw=thinking_raw,
                        metadata=metadata
                    )
                    
                    for tool_call in tool_calls_to_execute:
                        if self.cancel_flag:
                            logger.info(f"[STOP] [STREAMING] Cancelled before tool execution")
                            break
                        
                        if not tool_call.get("id") or not tool_call.get("function", {}).get("name"):
                            continue
                        
                        tool_call_count += 1
                        function_name = tool_call["function"]["name"]
                        tool_call_id = tool_call["id"]
                        
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
                        
                        # Publish to event bus for avatar/plugins
                        publish(Events.TOOL_EXECUTING, {"name": function_name})
                        
                        try:
                            function_result = self.main_chat.function_manager.execute_function(function_name, function_args)
                            result_str = str(function_result)
                            clean_result = strip_ui_markers(result_str)
                            
                            publish(Events.TOOL_COMPLETE, {"name": function_name, "success": True})
                            
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
                            
                            publish(Events.TOOL_COMPLETE, {"name": function_name, "success": False})
                            
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

                # Check for text-based tool calls (LM Studio, Qwen, GLM compatibility)
                # Check both content AND thinking - GLM puts tool calls in reasoning_content
                else:
                    function_call_data = None
                    # Check content first
                    if current_content:
                        function_call_data = self.tool_engine.extract_function_call_from_text(current_content)
                    # Also check thinking content (GLM reasoning_content may contain tool calls)
                    if not function_call_data and current_thinking:
                        function_call_data = self.tool_engine.extract_function_call_from_text(current_thinking)
                        if function_call_data:
                            logger.info("[TOOL] Found text-based tool call in thinking/reasoning content")
                    if function_call_data:
                        text_tool_name = function_call_data["function_call"]["name"]
                        logger.info(f"[TOOL] Text-based tool call detected: {text_tool_name}")

                        tool_call_count += 1
                        full_content = prefill + current_content if has_prefill else current_content

                        # Execute text-based tool call (function_manager returns error if not active)
                        self.tool_engine.execute_text_based_tool_call(
                            function_call_data,
                            full_content,
                            messages,
                            self.main_chat.session_manager,
                            provider
                        )

                        # Emit tool events for UI
                        tool_name = function_call_data["function_call"]["name"]
                        tool_args = function_call_data["function_call"].get("arguments", {})
                        yield {"type": "tool_start", "id": f"text_{iteration}", "name": tool_name, "args": tool_args}

                        # Get the result that was added to messages
                        last_msg = messages[-1] if messages else {}
                        result = last_msg.get("content", "Tool executed")
                        is_error = "Error:" in result or "not currently available" in result

                        yield {"type": "tool_end", "id": f"text_{iteration}", "name": tool_name, "result": result[:500], "error": is_error}

                        logger.info(f"[TOOL] Text-based tool iteration {iteration + 1} completed")
                        continue

                    logger.info(f"[OK] Final response received after {iteration + 1} iteration(s)")

                    full_content = current_content

                    if has_prefill:
                        full_content = prefill + full_content

                    if force_prefill:
                        full_content = force_prefill + full_content

                    # Save final response with thinking separated
                    self.main_chat.session_manager.add_assistant_final(
                        content=full_content,
                        thinking=current_thinking if current_thinking else None,
                        metadata=metadata
                    )
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
                
                final_content = ""
                final_thinking = ""
                final_metadata = None
                in_thinking = False
                final_start_time = time.time()
                
                for event in final_stream:
                    if self.cancel_flag:
                        break
                    
                    event_type = event.get("type")
                    
                    if event_type == "content":
                        chunk = event.get("text", "")
                        final_content += chunk
                        yield {"type": "content", "text": chunk}
                    
                    elif event_type == "thinking":
                        text = event.get("text", "")
                        final_thinking += text
                        if not in_thinking:
                            yield {"type": "content", "text": "<think>"}
                            in_thinking = True
                        yield {"type": "content", "text": text}
                    
                    elif event_type == "done":
                        if in_thinking:
                            yield {"type": "content", "text": "</think>\n\n"}
                        if event.get("thinking"):
                            final_thinking = event["thinking"]
                        if event.get("metadata"):
                            final_metadata = event["metadata"]
                        break
                
                if not final_metadata:
                    final_end_time = time.time()
                    duration = round(final_end_time - final_start_time, 2)
                    est_content_tokens = len(final_content) // 4 if final_content else 0
                    est_thinking_tokens = len(final_thinking) // 4 if final_thinking else 0
                    
                    final_metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(final_start_time)),
                        "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(final_end_time)),
                        "duration_seconds": duration,
                        "tokens": {
                            "content": est_content_tokens,
                            "thinking": est_thinking_tokens,
                            "total": est_content_tokens + est_thinking_tokens,
                            "estimated": True
                        },
                        "tokens_per_second": round(est_content_tokens / duration, 1) if duration > 0 else 0
                    }
                
                if final_content:
                    full_final = (force_prefill or "") + final_content
                    self.main_chat.session_manager.add_assistant_final(
                        content=full_final,
                        thinking=final_thinking if final_thinking else None,
                        metadata=final_metadata
                    )
                else:
                    fallback = f"I used {tool_call_count} tools and gathered information."
                    yield {"type": "content", "text": fallback}
                    self.main_chat.session_manager.add_assistant_final(
                        content=fallback,
                        metadata=final_metadata
                    )
                    
            except Exception as final_error:
                logger.error(f"[STREAMING] Forced final response failed: {final_error}")
                error_msg = f"I completed {tool_call_count} tool calls but encountered an error generating the final response."
                yield {"type": "content", "text": error_msg}
                self.main_chat.session_manager.add_assistant_final(error_msg)

        except ConnectionError as e:
            logger.warning(f"[STREAMING] {e}")
            self._cleanup_stream()
            raise
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