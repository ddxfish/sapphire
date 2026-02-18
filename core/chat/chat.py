# chat.py
import json
import logging
import time
import re
import uuid
from typing import Dict, Any, Optional, List

import config
from .history import ConversationHistory, ChatSessionManager, count_tokens
from .module_loader import ModuleLoader
from .function_manager import FunctionManager
from .chat_streaming import StreamingChat
from .chat_tool_calling import ToolCallingEngine, filter_to_thinking_only
from .llm_providers import get_provider, get_provider_for_url, get_provider_by_key, get_first_available_provider, get_generation_params

logger = logging.getLogger(__name__)


def friendly_llm_error(e):
    """Convert LLM provider exceptions to user-friendly messages. Returns None if unrecognized."""
    error_str = str(e).lower()
    type_name = type(e).__name__

    # Connection errors — detect local providers like LM Studio
    if isinstance(e, ConnectionError) or 'ConnectError' in type_name or 'connection' in error_str:
        if any(h in error_str for h in ('127.0.0.1', 'localhost', '0.0.0.0')):
            return "Can't reach LM Studio — open LM Studio, load a model, and enable its local server."
        return "Lost connection to the LLM server. Check that the service is running."

    status = getattr(e, 'status_code', None)
    if not status:
        return None

    if status == 400:
        if 'model' in error_str and any(k in error_str for k in ('not found', 'not loaded', 'does not exist')):
            return "Model not found or not loaded. If using LM Studio, make sure a model is loaded and running."
        return f"LLM request rejected (400). {str(e)[:200]}"

    if status == 401:
        return "API key is invalid or missing. Check your API key in Settings."

    if status == 403:
        return "Access denied. Your API key may not have permission for this model or resource."

    if status == 404:
        if 'model' in error_str:
            return "Model not found. Check that the model name is correct in Settings."
        return f"LLM endpoint not found (404). Check your API URL in Settings."

    if status in (402, 429) and any(k in error_str for k in ('billing', 'quota', 'credit', 'insufficient', 'budget', 'exceeded')):
        return "Account billing limit reached — out of credits or over budget. Check your provider's billing page."

    if status == 429:
        return "Rate limited — too many requests. Wait 30-60 seconds before trying again."

    if status == 529:
        return "Claude's servers are at capacity (529). This is temporary — wait a minute and resend."

    if status >= 500:
        return f"Server error ({status}) from LLM provider. The service may be experiencing issues."

    return None


# Extension → language map for fenced code blocks
TEXT_EXTENSIONS = {
    '.py': 'python', '.txt': 'text', '.md': 'markdown',
    '.js': 'javascript', '.ts': 'typescript', '.json': 'json',
    '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
    '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini',
    '.sh': 'bash', '.bash': 'bash',
    '.html': 'html', '.css': 'css', '.xml': 'xml',
    '.csv': 'csv', '.log': 'text', '.env': 'bash',
    '.rs': 'rust', '.go': 'go', '.java': 'java',
    '.c': 'c', '.cpp': 'cpp', '.h': 'c',
}

def _ext_to_lang(filename: str) -> str:
    """Map filename extension to language identifier for fenced code blocks."""
    import os
    ext = os.path.splitext(filename)[1].lower()
    return TEXT_EXTENSIONS.get(ext, 'text')


class LLMChat:
    def __init__(self, history=None, system=None):
        logger.info("LLMChat.__init__ starting...")
        self.system = system
        
        # Provider cache - populated lazily
        self._provider_cache = {}
        
        # Support both old and new config formats
        if hasattr(config, 'LLM_PROVIDERS') and config.LLM_PROVIDERS:
            # New format: LLM_PROVIDERS dict + LLM_FALLBACK_ORDER
            self._use_new_config = True
            logger.info(f"Using new LLM_PROVIDERS config with {len(config.LLM_PROVIDERS)} providers")
        else:
            # Legacy format: LLM_PRIMARY/LLM_FALLBACK
            self._use_new_config = False
            self.provider_primary = self._init_provider_legacy(getattr(config, 'LLM_PRIMARY', {}), "primary")
            self.provider_fallback = self._init_provider_legacy(getattr(config, 'LLM_FALLBACK', {}), "fallback")
            logger.info("Using legacy LLM_PRIMARY/LLM_FALLBACK config")
        
        if isinstance(history, ChatSessionManager):
            self.session_manager = history
        elif isinstance(history, ConversationHistory):
            self.session_manager = ChatSessionManager(max_history=config.LLM_MAX_HISTORY)
            if history.messages:
                self.session_manager.current_chat.messages = history.messages.copy()
                self.session_manager._save_current_chat()
        else:
            self.session_manager = ChatSessionManager(max_history=config.LLM_MAX_HISTORY)
        
        self.history = self.session_manager
        
        self.module_loader = ModuleLoader()
        self.current_system_prompt = None
        self.module_loader.set_system(self.system) if hasattr(self, 'system') else None
        self.function_manager = FunctionManager()
        
        self.tool_engine = ToolCallingEngine(self.function_manager)
        self.streaming_chat = StreamingChat(self)
        
        logger.info("LLMChat.__init__ completed")

    def _init_provider_legacy(self, llm_config, name):
        """Initialize an LLM provider from legacy config dict."""
        if not llm_config.get("enabled", False):
            logger.info(f"LLM {name} is disabled")
            return None
        
        if "provider" not in llm_config:
            base_url = llm_config.get("base_url", "")
            detected = get_provider_for_url(base_url)
            llm_config = {**llm_config, "provider": detected}
        
        try:
            provider = get_provider(llm_config, config.LLM_REQUEST_TIMEOUT)
            if provider:
                logger.info(f"Initialized {name} provider [{provider.provider_name}]: {llm_config.get('base_url', 'N/A')}")
            return provider
        except Exception as e:
            logger.error(f"Failed to init {name} provider: {e}")
            return None
            
    def set_system_prompt(self, prompt_content: str) -> bool:
        self.current_system_prompt = prompt_content
        return True

    def get_system_prompt_template(self) -> Optional[str]:
        return self.current_system_prompt

    def refresh_spice_if_needed(self):
        turn_count = self.session_manager.get_turn_count()
        from core.modules.system import prompts

        # Check per-chat spice setting
        chat_settings = self.session_manager.get_chat_settings()
        if not chat_settings.get('spice_enabled', True):
            if prompts.get_current_spice():
                # Clear stale spice AND reassemble prompt so AI stops seeing it
                prompts.clear_spice()
                if prompts.is_assembled_mode():
                    prompt_data = prompts.get_current_prompt()
                    content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
                    self.set_system_prompt(content)
                    logger.info("[SPICE] Spice disabled — cleared and reassembled prompt")
            return False

        if not prompts.is_assembled_mode():
            return False

        spice_turns = chat_settings.get('spice_turns', 3)
        current_spice = prompts.get_current_spice()

        # Pick spice if: none set (just enabled) OR rotation interval hit
        if not current_spice or turn_count % spice_turns == 0:
            logger.info(f"[SPICE] SPICE REFRESH at turn {turn_count} (had_spice={bool(current_spice)})")
            try:
                spice_result = prompts.set_random_spice()
                prompt_data = prompts.get_current_prompt()
                content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
                self.set_system_prompt(content)
                logger.info(f"[SPICE] Spice refresh completed: {spice_result}")
                return True
            except Exception as e:
                logger.error(f"[SPICE] Error refreshing spice: {e}")
                return False
        return False


    def _get_system_prompt(self):
        username = getattr(config, 'DEFAULT_USERNAME', 'Human Scum')
        ai_name = 'Sapphire'
        # Sanitize curly brackets to prevent template injection
        username = username.replace('{', '').replace('}', '')
        prompt_template = self.current_system_prompt or "System prompt not loaded."
        prompt = prompt_template.replace("{user_name}", username).replace("{ai_name}", ai_name)

        # Build context parts from chat settings
        context_parts = []
        chat_settings = self.session_manager.get_chat_settings()

        # Debug logging for story engine
        story_enabled = chat_settings.get('story_engine_enabled', chat_settings.get('state_engine_enabled', False))
        story_engine = self.function_manager.get_story_engine()
        logger.info(f"[STORY] _get_system_prompt: enabled={story_enabled}, engine_exists={story_engine is not None}")

        # Story prompt override: if story has prompt.md, replace character prompt entirely
        if story_enabled and story_engine:
            story_prompt = story_engine.story_prompt
            if story_prompt:
                prompt = story_prompt.replace("{user_name}", username).replace("{ai_name}", ai_name)
                logger.info(f"[STORY] Using story prompt override ({len(story_prompt)} chars)")

        # Inject datetime if enabled
        if chat_settings.get('inject_datetime', False):
            from datetime import datetime
            now = datetime.now()
            context_parts.append(f"Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}")

        # Inject custom context if present
        custom_ctx = chat_settings.get('custom_context', '').strip()
        if custom_ctx:
            context_parts.append(custom_ctx)

        # Inject story engine block if enabled
        if story_enabled:
            if story_engine:
                vars_in_prompt = chat_settings.get('story_vars_in_prompt', chat_settings.get('state_vars_in_prompt', False))
                story_in_prompt = chat_settings.get('story_in_prompt', chat_settings.get('state_story_in_prompt', True))

                logger.info(f"[STORY] Prompt injection: vars={vars_in_prompt}, story={story_in_prompt}, preset={story_engine.preset_name}")

                if vars_in_prompt or story_in_prompt:
                    # +1 because we're building prompt for the INCOMING message
                    turn = self.session_manager.get_turn_count() + 1
                    state_block = story_engine.format_for_prompt(
                        include_vars=vars_in_prompt,
                        include_story=story_in_prompt,
                        current_turn=turn
                    )
                    logger.info(f"[STORY] State block length: {len(state_block)} chars")
                    context_parts.append(f"<state turn=\"{turn}\">\n{state_block}\n</state>")
        
        # Combine all context
        if context_parts:
            prompt = f"{prompt}\n\n{chr(10).join(context_parts)}"
        
        return prompt, username

    def _update_story_engine(self):
        """Initialize or update story engine based on chat settings."""
        chat_settings = self.session_manager.get_chat_settings()

        # Fast path: story engine disabled (99% of users)
        story_enabled = chat_settings.get('story_engine_enabled', chat_settings.get('state_engine_enabled', False))
        if not story_enabled:
            if self.function_manager.get_story_engine():
                self.function_manager.set_story_engine(None)
                logger.debug("[STORY] Story engine disabled")
            return

        # Story engine is enabled - check if current engine is still valid
        chat_name = self.session_manager.get_active_chat_name()
        new_preset = chat_settings.get('story_preset', chat_settings.get('state_preset'))
        current_engine = self.function_manager.get_story_engine()

        # Fast path: existing engine is valid for this chat+preset
        if (current_engine and
            current_engine.chat_name == chat_name and
            current_engine.preset_name == new_preset):
            return

        # Need to create or update engine
        from core.story_engine import StoryEngine
        db_path = self.session_manager._db_path

        if current_engine and current_engine.preset_name != new_preset:
            logger.info(f"[STORY] Preset changed: '{current_engine.preset_name}' → '{new_preset}'")

        # Create new story engine for this chat
        engine = StoryEngine(chat_name, db_path)

        if new_preset:
            db_preset = engine.preset_name
            needs_fresh_load = (db_preset != new_preset) or engine.is_empty()

            if needs_fresh_load:
                turn = self.session_manager.get_turn_count()
                success, msg = engine.load_preset(new_preset, turn)
                if success:
                    logger.info(f"[STORY] Loaded preset '{new_preset}' for chat '{chat_name}'")
                else:
                    logger.warning(f"[STORY] Failed to load preset '{new_preset}': {msg}")
            else:
                engine.reload_preset_config(new_preset)
                logger.info(f"[STORY] Reloaded config for existing state in '{chat_name}'")

        self.function_manager.set_story_engine(
            engine,
            lambda: self.session_manager.get_turn_count()
        )
        logger.info(f"[STORY] Story engine enabled for chat '{chat_name}'")

    def _build_base_messages(self, user_input: str, images: list = None, files: list = None):
        system_prompt, user_name = self._get_system_prompt()

        # Flatten files into user_input as fenced code blocks
        if files:
            parts = [user_input]
            for f in files:
                lang = _ext_to_lang(f.get('filename', ''))
                parts.append(f"```{lang}\n# {f['filename']}\n{f['text']}\n```")
            user_input = "\n\n".join(parts)

        # Reserve space for system prompt + current user message in context budget
        reserved_tokens = count_tokens(system_prompt) + count_tokens(user_input)
        history_messages = self.session_manager.get_messages_for_llm(reserved_tokens)

        # Build user message content - list if images, string otherwise
        if images:
            user_content = [{"type": "text", "text": user_input}]
            for img in images:
                user_content.append({
                    "type": "image",
                    "data": img.get("data", ""),
                    "media_type": img.get("media_type", "image/jpeg")
                })
        else:
            user_content = user_input

        return [
            {"role": "system", "content": system_prompt},
            *history_messages,
            {"role": "user", "content": user_content}
        ]

    def chat_stream(self, user_input: str, prefill: str = None, skip_user_message: bool = False, images: list = None, files: list = None):
        return self.streaming_chat.chat_stream(user_input, prefill=prefill, skip_user_message=skip_user_message, images=images, files=files)

    def chat(self, user_input: str):
        try:
            chat_start_time = time.time()
            self.refresh_spice_if_needed()
            logger.info(f"[CHAT] CHAT: user said something here")

            module_name, module_info, processed_text = self.module_loader.detect_module(user_input)
            if module_name:
                logger.info(f"[MODULE] Module detected: {module_name}")
                module_config = self.module_loader.modules.get(module_name, {})
                should_save = module_config.get("save_to_history", True)
                active_chat = self.session_manager.get_active_chat_name()
                
                # Check save_to_history BEFORE adding any messages
                if should_save:
                    self.session_manager.add_user_message(user_input)
                
                response_text = self.module_loader.process_direct(module_name, processed_text, active_chat)
                
                if should_save:
                    self.session_manager.add_assistant_final(response_text)
                
                return response_text

            # Update story engine FIRST (before building messages) based on current settings
            self._update_story_engine()
            
            messages = self._build_base_messages(user_input)
            self.session_manager.add_user_message(user_input)
            
            # Set memory and goal scopes for this chat context
            chat_settings = self.session_manager.get_chat_settings()
            memory_scope = chat_settings.get('memory_scope', 'default')
            self.function_manager.set_memory_scope(memory_scope if memory_scope != 'none' else None)
            goal_scope = chat_settings.get('goal_scope', 'default')
            self.function_manager.set_goal_scope(goal_scope if goal_scope != 'none' else None)
            knowledge_scope = chat_settings.get('knowledge_scope', 'default')
            self.function_manager.set_knowledge_scope(knowledge_scope if knowledge_scope != 'none' else None)
            people_scope = chat_settings.get('people_scope', 'default')
            self.function_manager.set_people_scope(people_scope if people_scope != 'none' else None)

            # Send only enabled tools - model should only know about active tools
            enabled_tools = self.function_manager.enabled_tools

            # DIAGNOSTIC: Log what tools are being sent
            enabled_names = [t['function']['name'] for t in enabled_tools] if enabled_tools else []
            logger.info(f"[TOOLS] Sending {len(enabled_names)} tools to LLM: {enabled_names}")
            logger.info(f"[TOOLS] Current toolset: {self.function_manager.current_toolset_name}")
            logger.info(f"[TOOLS] Prompt mode: {self.function_manager._get_current_prompt_mode()}")
            
            provider_key, provider, model_override = self._select_provider()
            
            # Determine effective model (per-chat override or provider default)
            effective_model = model_override if model_override else provider.model
            
            # Get generation params for this provider/model
            gen_params = get_generation_params(
                provider_key, 
                effective_model, 
                getattr(config, 'LLM_PROVIDERS', {})
            )
            
            # Pass model override to provider if set
            if model_override:
                gen_params['model'] = model_override

            tool_call_count = 0
            last_tool_name = None
            force_prefill = None

            # Inject thinking prefill if enabled
            if getattr(config, 'FORCE_THINKING', False):
                force_prefill = getattr(config, 'THINKING_PREFILL', '<think>')
                messages.append({"role": "assistant", "content": force_prefill})
                logger.info(f"[THINK] Forced thinking prefill: {force_prefill}")

            for i in range(config.MAX_TOOL_ITERATIONS):
                iteration_start_time = time.time()

                logger.info(f"--- Iteration {i + 1}/{config.MAX_TOOL_ITERATIONS} (Total tools used: {tool_call_count}) ---")

                if getattr(config, 'DEBUG_TOOL_CALLING', False):
                    logger.info(f"[MSGS] Messages being sent ({len(messages)} total):")
                    for idx, msg in enumerate(messages[-5:]):
                        role = msg.get("role")
                        content = str(msg.get("content", ""))
                        has_tools = "tool_calls" in msg
                        preview = content[:80] if content else "(empty)"
                        logger.info(f"  [{idx}] {role}: {preview}... (has_tools={has_tools})")

                try:
                    response_msg = self.tool_engine.call_llm_with_metrics(
                        provider, messages, gen_params, tools=enabled_tools
                    )
                except Exception as llm_error:
                    iteration_time = time.time() - iteration_start_time
                    logger.error(f"LLM call failed on iteration {i+1} after {iteration_time:.1f}s: {llm_error}")
                    
                    timeout_text = f"I completed {tool_call_count} tool calls but got stuck during processing (timeout after {iteration_time:.1f}s)."
                    if force_prefill:
                        timeout_text = force_prefill + timeout_text
                    
                    # Build error metadata
                    chat_end_time = time.time()
                    duration = round(chat_end_time - chat_start_time, 2)
                    metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "duration_seconds": duration,
                        "error": True
                    }
                    self.session_manager.add_assistant_final(timeout_text, metadata=metadata)
                    return timeout_text

                iteration_time = time.time() - iteration_start_time
                per_iteration_timeout = config.LLM_REQUEST_TIMEOUT / config.MAX_TOOL_ITERATIONS
                if iteration_time > per_iteration_timeout:
                    logger.warning(f"Iteration {i+1} exceeded {per_iteration_timeout:.0f}s timeout")
                    timeout_text = f"I completed {tool_call_count} tool calls but processing got stuck (iteration timeout)."
                    if force_prefill:
                        timeout_text = force_prefill + timeout_text
                    
                    # Build error metadata
                    chat_end_time = time.time()
                    duration = round(chat_end_time - chat_start_time, 2)
                    metadata = {
                        "provider": provider_key,
                        "model": effective_model,
                        "duration_seconds": duration,
                        "error": True
                    }
                    self.session_manager.add_assistant_final(timeout_text, metadata=metadata)
                    return timeout_text

                logger.info(f"Iteration {i+1} completed in {iteration_time:.1f}s")

                if response_msg.has_tool_calls:
                    called_tools = [tc.name for tc in response_msg.tool_calls]
                    logger.info(f"[TOOLS] LLM called tools via tool_calls: {called_tools}")
                    
                    # Check if any called tools are NOT in enabled_tools
                    active_names = set(t['function']['name'] for t in enabled_tools) if enabled_tools else set()
                    unexpected = [t for t in called_tools if t not in active_names]
                    if unexpected:
                        logger.warning(f"[TOOLS] ⚠️ LLM called tools NOT in active set: {unexpected}")
                    
                    logger.info(f"Processing {len(response_msg.tool_calls)} tool call(s) from LLM")
                    
                    # Always filter thinking content from tool call responses
                    filtered_content = filter_to_thinking_only(response_msg.content or "")
                    
                    tool_calls_formatted = response_msg.get_tool_calls_as_dicts()
                    
                    # Slice to MAX_PARALLEL_TOOLS limit
                    tool_calls_to_execute = tool_calls_formatted[:config.MAX_PARALLEL_TOOLS]
                    if len(tool_calls_to_execute) < len(tool_calls_formatted):
                        logger.info(f"[LIMIT] Executing {len(tool_calls_to_execute)}/{len(tool_calls_formatted)} tools (MAX_PARALLEL_TOOLS={config.MAX_PARALLEL_TOOLS})")
                    
                    messages.append({
                        "role": "assistant",
                        "content": filtered_content,
                        "tool_calls": tool_calls_to_execute
                    })
                    self.session_manager.add_assistant_with_tool_calls(filtered_content, tool_calls_to_execute)

                    # Track last tool name
                    if tool_calls_to_execute:
                        last_tool_name = tool_calls_to_execute[0]["function"]["name"]

                    tools_executed = self.tool_engine.execute_tool_calls(
                        tool_calls_to_execute,
                        messages, 
                        self.session_manager,
                        provider
                    )
                    tool_call_count += tools_executed

                    # Track last tool name
                    if tool_calls_to_execute:
                        last_tool_name = tool_calls_to_execute[0]["function"]["name"]
                        
                        # Exit immediately after chat reset
                        if last_tool_name == "end_and_reset_chat":
                            logger.info("[RESET] Chat reset detected, ending without final response")
                            return "Chat history has been reset."

                    logger.info(f"Tool execution iteration {i+1} completed")
                    continue

                elif response_msg.content:
                    function_call_data = self.tool_engine.extract_function_call_from_text(response_msg.content)
                    if function_call_data:
                        text_tool_name = function_call_data["function_call"]["name"]
                        logger.info(f"[TOOLS] Text-based tool call detected: {text_tool_name}")

                        # Check if this is in active tools (execute anyway - function_manager returns error)
                        active_names = set(t['function']['name'] for t in enabled_tools) if enabled_tools else set()
                        if text_tool_name not in active_names:
                            logger.warning(f"[TOOLS] ⚠️ Text-based call for tool NOT in active set: {text_tool_name}")
                        
                        tool_call_count += 1
                        logger.info("Processing text-based function call")

                        # Always filter thinking content from tool call responses
                        filtered_content = filter_to_thinking_only(response_msg.content)

                        last_tool_name = function_call_data["function_call"]["name"]

                        self.tool_engine.execute_text_based_tool_call(
                            function_call_data, 
                            filtered_content,
                            messages,
                            self.session_manager,
                            provider
                        )

                        logger.info(f"Text-based tool iteration {i+1} completed")
                        continue

                logger.info(f"No more tool calls. Final response. (Total tools: {tool_call_count})")
                final_response_content = response_msg.content or "I have completed the requested actions."
                
                # Prepend force prefill if used
                if force_prefill:
                    final_response_content = force_prefill + final_response_content
                    logger.info(f"[THINK] Combined response: {len(force_prefill)} prefill + {len(response_msg.content or '')} response")
                
                # Build metadata for UI display
                chat_end_time = time.time()
                duration = round(chat_end_time - chat_start_time, 2)
                
                # Get token counts from response if available
                tokens_info = {}
                if response_msg.usage:
                    tokens_info = {
                        "prompt": response_msg.usage.get("prompt_tokens", 0),
                        "content": response_msg.usage.get("completion_tokens", 0),
                        "total": response_msg.usage.get("total_tokens", 0),
                    }
                else:
                    # Estimate from content length
                    est_tokens = len(final_response_content) // 4
                    tokens_info = {"content": est_tokens, "total": est_tokens, "estimated": True}
                
                metadata = {
                    "provider": provider_key,
                    "model": effective_model,
                    "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(chat_start_time)),
                    "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(chat_end_time)),
                    "duration_seconds": duration,
                    "tokens": tokens_info,
                    "tokens_per_second": round(tokens_info.get("content", 0) / duration, 1) if duration > 0 else 0
                }
                
                self.session_manager.add_assistant_final(final_response_content, metadata=metadata)
                return final_response_content

            logger.warning(f"Exceeded max iterations ({config.MAX_TOOL_ITERATIONS}). Forcing final answer.")
            
            messages.append({
                "role": "user",
                "content": "You've used tools multiple times. Stop using tools now and provide your final answer based on the information you gathered."
            })

            final_response_msg = None
            try:
                final_response_msg = self.tool_engine.call_llm_with_metrics(
                    provider, messages, gen_params, tools=None
                )
                final_response_content = final_response_msg.content or f"I used {tool_call_count} tools and gathered information, but couldn't formulate a final answer."
                
                # Prepend force prefill if used
                if force_prefill:
                    final_response_content = force_prefill + final_response_content
                    
            except Exception as final_error:
                logger.error(f"Final forced response failed: {final_error}")
                final_response_content = f"I successfully used {tool_call_count} tools but encountered technical difficulties."
                if force_prefill:
                    final_response_content = force_prefill + final_response_content

            # Build metadata for UI display
            chat_end_time = time.time()
            duration = round(chat_end_time - chat_start_time, 2)
            
            tokens_info = {}
            if final_response_msg and final_response_msg.usage:
                tokens_info = {
                    "prompt": final_response_msg.usage.get("prompt_tokens", 0),
                    "content": final_response_msg.usage.get("completion_tokens", 0),
                    "total": final_response_msg.usage.get("total_tokens", 0),
                }
            else:
                est_tokens = len(final_response_content) // 4
                tokens_info = {"content": est_tokens, "total": est_tokens, "estimated": True}
            
            metadata = {
                "provider": provider_key,
                "model": effective_model,
                "start_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(chat_start_time)),
                "end_time": time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(chat_end_time)),
                "duration_seconds": duration,
                "tokens": tokens_info,
                "tokens_per_second": round(tokens_info.get("content", 0) / duration, 1) if duration > 0 else 0
            }

            self.session_manager.add_assistant_final(final_response_content, metadata=metadata)
            return final_response_content

        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)

            friendly = friendly_llm_error(e)
            if friendly:
                error_text = friendly
            elif "timeout" in str(e).lower() or "APITimeoutError" in str(type(e).__name__):
                error_text = "I ran into a timeout while processing your request. Please try breaking it into smaller parts."
            elif "swarm" in str(e).lower() or (hasattr(e, '__module__') and 'httpx' in str(e.__module__)):
                error_text = f"Local swarm server connection failed. Error: {str(e)}"
            elif "connection" in str(e).lower() or "ConnectError" in str(type(e).__name__):
                error_text = "I lost connection to my processing engine. Please check if services are running."
            elif "json" in str(e).lower() or "JSON" in str(e):
                error_text = "I encountered a data formatting issue while processing your request."
            else:
                error_text = f"I encountered an unexpected technical issue. Error: {str(e)[:200]}"

            # Build error metadata (may not have provider info if error was early)
            chat_end_time = time.time()
            duration = round(chat_end_time - chat_start_time, 2)
            metadata = {
                "duration_seconds": duration,
                "error": True
            }
            
            self.session_manager.add_assistant_final(error_text, metadata=metadata)
            return error_text

    def _select_provider(self):
        """Select LLM provider using per-chat settings or fallback order. Returns (provider_key, provider, model_override) tuple or raises."""
        
        if self._use_new_config:
            providers_config = config.LLM_PROVIDERS
            fallback_order = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config.keys()))
            
            # Check per-chat LLM settings
            chat_settings = self.session_manager.get_chat_settings()
            chat_primary = chat_settings.get('llm_primary', 'auto')
            chat_model = chat_settings.get('llm_model', '')  # Per-chat model override
            
            # Handle "none" - explicitly disabled
            if chat_primary == 'none':
                raise ConnectionError("LLM disabled for this chat (llm_primary=none)")
            
            # If chat has specific provider set (not "auto"), use ONLY that provider - no fallback
            if chat_primary and chat_primary != 'auto':
                # Privacy mode check for explicitly selected provider
                try:
                    from core.privacy import is_privacy_mode, is_allowed_endpoint
                    from core.chat.llm_providers import PROVIDER_METADATA
                    if is_privacy_mode():
                        metadata = PROVIDER_METADATA.get(chat_primary, {})
                        if metadata.get('privacy_check_whitelist'):
                            base_url = providers_config.get(chat_primary, {}).get('base_url', '')
                            if not is_allowed_endpoint(base_url):
                                raise ConnectionError(f"Provider '{chat_primary}' base URL is not in the privacy whitelist. Update whitelist or disable privacy mode.")
                        elif not metadata.get('is_local', False):
                            raise ConnectionError(f"Provider '{chat_primary}' is a cloud provider and blocked in privacy mode. Use a local LLM or disable privacy mode.")
                except ConnectionError:
                    raise
                except Exception:
                    pass

                provider = get_provider_by_key(chat_primary, providers_config, config.LLM_REQUEST_TIMEOUT, model_override=chat_model)
                if not provider:
                    raise ConnectionError(f"Provider '{chat_primary}' not configured or disabled")

                try:
                    if provider.health_check():
                        logger.info(f"Using chat-specific provider '{chat_primary}'" +
                                   (f" with model '{chat_model}'" if chat_model else ""))
                        return (chat_primary, provider, chat_model)
                except Exception as e:
                    pass  # Fall through to error

                raise ConnectionError(f"Provider '{chat_primary}' failed health check - no fallback for specific provider selection")
            
            # Auto mode - use global fallback order
            result = get_first_available_provider(
                providers_config,
                fallback_order,
                config.LLM_REQUEST_TIMEOUT
            )
            
            if result:
                provider_key, provider = result
                logger.info(f"Auto mode: using '{provider_key}' ({provider.model})")
                return (provider_key, provider, '')  # No model override in auto mode
            
            raise ConnectionError("No LLM providers available")
        
        else:
            # Legacy config: LLM_PRIMARY/LLM_FALLBACK
            if self.provider_primary and getattr(config, 'LLM_PRIMARY', {}).get("enabled"):
                try:
                    if self.provider_primary.health_check():
                        logger.info(f"Using primary LLM [{self.provider_primary.provider_name}]: {self.provider_primary.model}")
                        return ('legacy_primary', self.provider_primary, '')
                except Exception as e:
                    logger.warning(f"Primary LLM health check failed: {e}")
            
            if self.provider_fallback and getattr(config, 'LLM_FALLBACK', {}).get("enabled"):
                try:
                    if self.provider_fallback.health_check():
                        logger.info(f"Using fallback LLM [{self.provider_fallback.provider_name}]: {self.provider_fallback.model}")
                        return ('legacy_fallback', self.provider_fallback, '')
                except Exception as e:
                    logger.error(f"Fallback LLM health check failed: {e}")
            
            raise ConnectionError("No LLM endpoints available")

    def reset(self):
        self.session_manager.clear()
        return True

    def list_chats(self) -> List[Dict[str, Any]]:
        return self.session_manager.list_chat_files()

    def create_chat(self, chat_name: str) -> bool:
        return self.session_manager.create_chat(chat_name)

    def delete_chat(self, chat_name: str) -> bool:
        return self.session_manager.delete_chat(chat_name)

    def switch_chat(self, chat_name: str) -> bool:
        return self.session_manager.set_active_chat(chat_name)

    def get_active_chat(self) -> str:
        return self.session_manager.get_active_chat_name()

    def isolated_chat(self, user_input: str, task_settings: Dict[str, Any] = None) -> str:
        """
        Run a chat in complete isolation - no session state changes.
        Used for background continuity tasks that shouldn't affect UI.
        
        Args:
            user_input: The user message
            task_settings: Dict with prompt, toolset, provider, model, inject_datetime, memory_scope
            
        Returns:
            The assistant's response text
        """
        import time
        from datetime import datetime
        
        task_settings = task_settings or {}
        logger.info(f"[ISOLATED] Starting isolated chat with settings: {list(task_settings.keys())}")
        
        try:
            # Build system prompt from task settings
            prompt_name = task_settings.get("prompt", "default")
            from core.modules.system import prompts
            prompt_data = prompts.get_prompt(prompt_name)
            if prompt_data:
                system_prompt = prompt_data.get("content") if isinstance(prompt_data, dict) else str(prompt_data)
            else:
                system_prompt = "You are a helpful assistant."
            
            # Apply name substitutions
            username = getattr(config, 'DEFAULT_USERNAME', 'Human')
            ai_name = 'Sapphire'
            system_prompt = system_prompt.replace("{user_name}", username).replace("{ai_name}", ai_name)
            
            # Inject datetime if enabled
            if task_settings.get("inject_datetime"):
                now = datetime.now()
                system_prompt = f"{system_prompt}\n\nCurrent date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"
            
            # Build messages - just system + user, no history for ephemeral
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ]
            
            # Get tools if toolset specified
            tools = None
            toolset = task_settings.get("toolset")
            if toolset and toolset not in ("none", ""):
                # Temporarily set memory and goal scopes for tool execution
                memory_scope = task_settings.get("memory_scope", "default")
                self.function_manager.set_memory_scope(memory_scope if memory_scope != "none" else None)
                goal_scope = task_settings.get("goal_scope", "default")
                self.function_manager.set_goal_scope(goal_scope if goal_scope != "none" else None)
                self.function_manager.update_enabled_functions([toolset])
                tools = self.function_manager.enabled_tools
                logger.info(f"[ISOLATED] Using toolset '{toolset}' with {len(tools)} tools")
            
            # Select provider
            provider_key = task_settings.get("provider", "auto")
            model_override = task_settings.get("model", "")
            
            if provider_key and provider_key not in ("auto", ""):
                providers_config = getattr(config, 'LLM_PROVIDERS', {})
                provider = get_provider_by_key(provider_key, providers_config, config.LLM_REQUEST_TIMEOUT, model_override=model_override)
                if not provider:
                    raise ConnectionError(f"Provider '{provider_key}' not available")
            else:
                provider_key, provider, model_override = self._select_provider()
            
            effective_model = model_override if model_override else provider.model
            gen_params = get_generation_params(
                provider_key, 
                effective_model, 
                getattr(config, 'LLM_PROVIDERS', {})
            )
            if model_override:
                gen_params['model'] = model_override
            
            logger.info(f"[ISOLATED] Using provider '{provider_key}', model '{effective_model}'")
            
            # Simple single-shot call (no tool loop for now - keep it simple)
            response = provider.chat_completion(messages, tools=tools, generation_params=gen_params)
            
            if response and response.content:
                # Strip thinking blocks — return only the actual spoken content
                import re
                content = re.sub(r'<think>.*?</think>\s*', '', response.content, flags=re.DOTALL).strip()
                logger.info(f"[ISOLATED] Got response: {len(response.content)} chars total, {len(content)} chars content")
                return content if content else response.content
            else:
                logger.warning("[ISOLATED] Empty response from provider")
                return "No response received."
                
        except Exception as e:
            logger.error(f"[ISOLATED] Chat failed: {e}", exc_info=True)
            return f"Error: {e}"