import json
import logging
import re
import time
from typing import Generator, Union, Dict, Any
import config
from .chat_tool_calling import strip_ui_markers, wrap_tool_result, _extract_tool_images
from .llm_providers import LLMResponse, get_generation_params
from core.event_bus import publish, Events
from core.hooks import hook_runner, HookEvent
from core.metrics import metrics as token_metrics

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
            from core.prompt_state import is_current_prompt_private
            from core.privacy import is_privacy_mode
            if is_current_prompt_private() and not is_privacy_mode():
                chat_settings = self.main_chat.session_manager.get_chat_settings()
                if not chat_settings.get('private_chat', False):
                    yield {"type": "error", "text": "This prompt requires Privacy Mode to be enabled."}
                    return
        except ImportError:
            pass

        try:
            self.main_chat.refresh_spice_if_needed()
            self.cancel_flag = False
            self.current_stream = None
            self.ephemeral = False

            # Update story engine FIRST (before building messages) based on current settings
            self.main_chat._update_story_engine()

            # Plugin pre_chat hook — can modify input, bypass LLM, or stop propagation
            if hook_runner.has_handlers("pre_chat"):
                hook_event = HookEvent(input=user_input, config=config,
                                       metadata={"system": self.main_chat.system})
                hook_runner.fire("pre_chat", hook_event)
                if hook_event.skip_llm:
                    response = hook_event.response or ""
                    if response:
                        if not hook_event.ephemeral:
                            self.main_chat.session_manager.add_user_message(user_input)
                            self.main_chat.session_manager.add_assistant_final(response)
                        else:
                            self.ephemeral = True
                        yield {"type": "content", "text": response}
                    publish(Events.AI_TYPING_END)
                    self.is_streaming = False
                    return
                user_input = hook_event.input  # may have been mutated

            messages = self.main_chat._build_base_messages(user_input, images=images, files=files)

            if not skip_user_message:
                # Build content list if files or images present, otherwise just text
                if files or images:
                    user_content = []
                    if user_input:
                        user_content.append({"type": "text", "text": user_input})
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
            
            # Set scopes for this chat context
            chat_settings = self.main_chat.session_manager.get_chat_settings()
            self.main_chat.function_manager.apply_scopes(chat_settings)
            chat_name = self.main_chat.session_manager.get_active_chat_name()
            self.main_chat.function_manager.set_rag_scope(f"__rag__:{chat_name}")

            # Snapshot scopes as a plain dict — survives across Starlette's
            # per-yield context resets, re-applied in execute_function()
            _scopes = self.main_chat.function_manager.snapshot_scopes()

            # Send only enabled tools - model should only know about active tools
            # Snapshot names too — used to validate tool calls against what LLM actually received
            enabled_tools = self.main_chat.function_manager.enabled_tools
            _allowed_tool_names = {t["function"]["name"] for t in enabled_tools if "function" in t}
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
            # Accumulate token usage across all iterations for final summary
            cumulative_tokens = {"prompt": 0, "completion": 0, "thinking": 0, "total": 0,
                                 "cache_read": 0, "cache_write": 0, "iterations": 0}

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
                tool_pending_sent = set()  # Track which tool indices got early UI hint
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

                            # Early UI hint: show accordion as soon as we know the tool name
                            # Only for indices within MAX_PARALLEL_TOOLS — excess tools won't execute
                            tc = tool_calls[idx]
                            if idx not in tool_pending_sent and tc["function"]["name"] and idx < config.MAX_PARALLEL_TOOLS:
                                tool_pending_sent.add(idx)
                                yield {"type": "tool_pending", "name": tc["function"]["name"], "index": idx}
                        
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
                    gen_start = first_chunk_time or iteration_end_time
                    duration = round(iteration_end_time - gen_start, 2)

                    # Try real usage from provider response first, fall back to estimate
                    resp_usage = final_response.usage if final_response and hasattr(final_response, 'usage') else None
                    if resp_usage:
                        content_tokens = resp_usage.get("completion_tokens", 0)
                        prompt_tokens = resp_usage.get("prompt_tokens", 0)
                        total_tokens = resp_usage.get("total_tokens", 0)
                        estimated = False
                    else:
                        content_tokens = len(current_content) // 4 if current_content else 0
                        prompt_tokens = 0
                        total_tokens = content_tokens + (len(current_thinking) // 4 if current_thinking else 0)
                        estimated = True

                    thinking_tokens = len(current_thinking) // 4 if current_thinking else 0

                    metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(gen_start)),
                        "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(iteration_end_time)),
                        "duration_seconds": duration,
                        "tokens": {
                            "content": content_tokens,
                            "thinking": thinking_tokens,
                            "prompt": prompt_tokens,
                            "total": total_tokens,
                            "estimated": estimated
                        },
                        "tokens_per_second": round(content_tokens / duration, 1) if duration > 0 else 0
                    }
                    # Forward cache stats from provider
                    if resp_usage:
                        for k in ("cache_read_tokens", "cache_write_tokens"):
                            if resp_usage.get(k):
                                metadata["tokens"][k] = resp_usage[k]
                
                # Accumulate tokens across iterations
                if metadata and metadata.get("tokens"):
                    t = metadata["tokens"]
                    cumulative_tokens["prompt"] += t.get("prompt", 0)
                    cumulative_tokens["completion"] += t.get("content", 0)
                    cumulative_tokens["thinking"] += t.get("thinking", 0)
                    cumulative_tokens["total"] += t.get("total", 0)
                    cumulative_tokens["cache_read"] += t.get("cache_read_tokens", 0)
                    cumulative_tokens["cache_write"] += t.get("cache_write_tokens", 0)
                    cumulative_tokens["iterations"] += 1

                    # Record per-call metrics
                    call_type = "tool_call" if tool_calls else "conversation"
                    estimated = metadata["tokens"].get("estimated", False)
                    try:
                        chat_name = self.main_chat.session_manager.get_active_chat_name()
                        token_metrics.record(chat_name, provider_key, effective_model,
                                             call_type, metadata, estimated=estimated)
                    except Exception:
                        pass  # Metrics are best-effort

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
                    
                    iteration_tool_images = []

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
                            function_result = self.main_chat.function_manager.execute_function(function_name, function_args, scopes=_scopes, allowed_tools=_allowed_tool_names)
                            result_str, tool_imgs = _extract_tool_images(function_result, self.main_chat.session_manager)
                            if tool_imgs:
                                iteration_tool_images.extend(tool_imgs)
                                logger.info(f"[TOOL] {function_name} returned {len(tool_imgs)} image(s)")
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

                            self.main_chat.session_manager.add_tool_result(
                                tool_call_id,
                                function_name,
                                result_str,
                                inputs=function_args
                            )

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

                            self.main_chat.session_manager.add_tool_result(
                                tool_call_id,
                                function_name,
                                error_result,
                                inputs=function_args
                            )

                    # Inject tool-returned images for next LLM turn
                    if iteration_tool_images:
                        from .chat import _inject_tool_images
                        _inject_tool_images(messages, iteration_tool_images)

                    # Refresh tools list — tool_load may have added new tools
                    enabled_tools = self.main_chat.function_manager.enabled_tools

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
                        _, text_tool_images = self.tool_engine.execute_text_based_tool_call(
                            function_call_data,
                            full_content,
                            messages,
                            self.main_chat.session_manager,
                            provider,
                            scopes=_scopes
                        )

                        # Inject tool-returned images for next LLM turn
                        if text_tool_images:
                            from .chat import _inject_tool_images
                            _inject_tool_images(messages, text_tool_images)

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

                    # post_llm hook — plugins can mutate response before save + TTS
                    if hook_runner.has_handlers("post_llm"):
                        llm_event = hook_runner.fire("post_llm", HookEvent(
                            input=user_input, response=full_content,
                            config=config, metadata={"system": self.main_chat.system}
                        ))
                        full_content = llm_event.response or full_content

                    # Attach cumulative token stats from all iterations
                    if cumulative_tokens["iterations"] > 1 and metadata:
                        metadata["cumulative_tokens"] = {
                            "prompt": cumulative_tokens["prompt"],
                            "completion": cumulative_tokens["completion"],
                            "thinking": cumulative_tokens["thinking"],
                            "total": cumulative_tokens["total"],
                            "iterations": cumulative_tokens["iterations"]
                        }
                        if cumulative_tokens["cache_read"]:
                            metadata["cumulative_tokens"]["cache_read"] = cumulative_tokens["cache_read"]
                        if cumulative_tokens["cache_write"]:
                            metadata["cumulative_tokens"]["cache_write"] = cumulative_tokens["cache_write"]

                    # Save final response with thinking separated
                    self.main_chat.session_manager.add_assistant_final(
                        content=full_content,
                        thinking=current_thinking if current_thinking else None,
                        metadata=metadata
                    )

                    if hook_runner.has_handlers("post_chat"):
                        hook_runner.fire("post_chat", HookEvent(
                            input=user_input, response=full_content,
                            config=config, metadata={"system": self.main_chat.system}
                        ))

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
                forced_final_response = None
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
                        forced_final_response = event.get("response")
                        break

                if not final_metadata:
                    final_end_time = time.time()
                    duration = round(final_end_time - final_start_time, 2)

                    resp_usage = forced_final_response.usage if forced_final_response and hasattr(forced_final_response, 'usage') else None
                    if resp_usage:
                        content_tokens = resp_usage.get("completion_tokens", 0)
                        prompt_tokens = resp_usage.get("prompt_tokens", 0)
                        total_tokens = resp_usage.get("total_tokens", 0)
                        estimated = False
                    else:
                        content_tokens = len(final_content) // 4 if final_content else 0
                        prompt_tokens = 0
                        total_tokens = content_tokens + (len(final_thinking) // 4 if final_thinking else 0)
                        estimated = True

                    thinking_tokens = len(final_thinking) // 4 if final_thinking else 0

                    final_metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(final_start_time)),
                        "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(final_end_time)),
                        "duration_seconds": duration,
                        "tokens": {
                            "content": content_tokens,
                            "thinking": thinking_tokens,
                            "prompt": prompt_tokens,
                            "total": total_tokens,
                            "estimated": estimated
                        },
                        "tokens_per_second": round(content_tokens / duration, 1) if duration > 0 else 0
                    }
                    if resp_usage:
                        for k in ("cache_read_tokens", "cache_write_tokens"):
                            if resp_usage.get(k):
                                final_metadata["tokens"][k] = resp_usage[k]
                
                if final_content:
                    full_final = (force_prefill or "") + final_content
                else:
                    full_final = f"I used {tool_call_count} tools and gathered information."
                    yield {"type": "content", "text": full_final}

                # post_llm hook — plugins can mutate forced-final response
                if hook_runner.has_handlers("post_llm"):
                    llm_event = hook_runner.fire("post_llm", HookEvent(
                        input=user_input, response=full_final,
                        config=config, metadata={"system": self.main_chat.system}
                    ))
                    full_final = llm_event.response or full_final

                self.main_chat.session_manager.add_assistant_final(
                    content=full_final,
                    thinking=final_thinking if final_thinking and final_content else None,
                    metadata=final_metadata
                )
                _post_response = full_final

                if hook_runner.has_handlers("post_chat"):
                    hook_runner.fire("post_chat", HookEvent(
                        input=user_input, response=_post_response,
                        config=config, metadata={"system": self.main_chat.system}
                    ))

            except Exception as final_error:
                logger.error(f"[STREAMING] Forced final response failed: {final_error}")
                error_msg = f"I completed {tool_call_count} tool calls but encountered an error generating the final response."
                yield {"type": "content", "text": error_msg}
                self.main_chat.session_manager.add_assistant_final(error_msg)

        except ConnectionError as e:
            logger.warning(f"[STREAMING] {e}")
            # Save error so history doesn't end with a dangling user message
            self.main_chat.session_manager.add_assistant_final(
                f"[Connection error: {e}]"
            )
            self._cleanup_stream()
            raise
        except Exception as e:
            logger.error(f"[ERR] [STREAMING FATAL] Unhandled error: {e}", exc_info=True)
            self._cleanup_stream()
            raise
        
        finally:
            logger.info(f"[CLEANUP] [STREAMING FINALLY] Cleaning up, cancel_flag={self.cancel_flag}")
            # Close any open tool cycle so history isn't left in a broken state
            # (e.g. user hit Stop mid-tool-execution)
            if self.main_chat.session_manager._in_tool_cycle:
                logger.info("[CLEANUP] Closing orphaned tool cycle from cancelled stream")
                self.main_chat.session_manager.add_assistant_final(
                    content="[Cancelled during tool execution]"
                )
            self._cleanup_stream()
            self.cancel_flag = False
            self.is_streaming = False
            publish(Events.AI_TYPING_END)