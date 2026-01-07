# chat.py
import json
import logging
import time
import re
import uuid
from typing import Dict, Any, Optional, List

import config
from .history import ConversationHistory, ChatSessionManager
from .module_loader import ModuleLoader
from .function_manager import FunctionManager
from .chat_streaming import StreamingChat
from .chat_tool_calling import ToolCallingEngine, filter_to_thinking_only
from .llm_providers import get_provider, get_provider_for_url, get_provider_by_key, get_first_available_provider, get_generation_params

logger = logging.getLogger(__name__)



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
        
        # Check per-chat spice setting
        chat_settings = self.session_manager.get_chat_settings()
        if not chat_settings.get('spice_enabled', True):
            from core.modules.system import prompts
            prompts.clear_spice()
            return False
        
        spice_turns = chat_settings.get('spice_turns', 3)
        if turn_count % spice_turns == 0:
            logger.info(f"[SPICE] SPICE REFRESH at turn {turn_count}")
            try:
                from core.modules.system import prompts
                # Only apply spice in assembled mode, skip for monoliths
                if not prompts.is_assembled_mode():
                    logger.info(f"[SPICE] Skipping spice - monolith mode active")
                    return False
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
        ai_name = getattr(config, 'DEFAULT_AI_NAME', 'Sapphire')
        prompt_template = self.current_system_prompt or "System prompt not loaded."
        prompt = prompt_template.replace("{user_name}", username).replace("{ai_name}", ai_name)
        
        # Build context parts from chat settings
        context_parts = []
        chat_settings = self.session_manager.get_chat_settings()
        
        # Inject datetime if enabled
        if chat_settings.get('inject_datetime', False):
            from datetime import datetime
            now = datetime.now()
            context_parts.append(f"Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}")
        
        # Inject custom context if present
        custom_ctx = chat_settings.get('custom_context', '').strip()
        if custom_ctx:
            context_parts.append(custom_ctx)
        
        # Combine all context
        if context_parts:
            prompt = f"{prompt}\n\n{chr(10).join(context_parts)}"
        
        return prompt, username

    def _build_base_messages(self, user_input: str):
        system_prompt, user_name = self._get_system_prompt()
        history_messages = self.session_manager.get_messages_for_llm()
        
        return [
            {"role": "system", "content": system_prompt},
            *history_messages,
            {"role": "user", "content": user_input}
        ]

    def chat_stream(self, user_input: str, prefill: str = None, skip_user_message: bool = False):
        return self.streaming_chat.chat_stream(user_input, prefill=prefill, skip_user_message=skip_user_message)

    def chat(self, user_input: str):
        try:
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

            messages = self._build_base_messages(user_input)
            self.session_manager.add_user_message(user_input)
            
            active_tools = self.function_manager.enabled_tools
            provider_key, provider = self._select_provider()
            
            # Get generation params for this provider/model
            gen_params = get_generation_params(
                provider_key, 
                provider.model, 
                getattr(config, 'LLM_PROVIDERS', {})
            )

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
                        provider, messages, gen_params, tools=active_tools
                    )
                except Exception as llm_error:
                    iteration_time = time.time() - iteration_start_time
                    logger.error(f"LLM call failed on iteration {i+1} after {iteration_time:.1f}s: {llm_error}")
                    
                    timeout_text = f"I completed {tool_call_count} tool calls but got stuck during processing (timeout after {iteration_time:.1f}s)."
                    if force_prefill:
                        timeout_text = force_prefill + timeout_text
                    self.session_manager.add_assistant_final(timeout_text)
                    return timeout_text

                iteration_time = time.time() - iteration_start_time
                per_iteration_timeout = config.LLM_REQUEST_TIMEOUT / config.MAX_TOOL_ITERATIONS
                if iteration_time > per_iteration_timeout:
                    logger.warning(f"Iteration {i+1} exceeded {per_iteration_timeout:.0f}s timeout")
                    timeout_text = f"I completed {tool_call_count} tool calls but processing got stuck (iteration timeout)."
                    if force_prefill:
                        timeout_text = force_prefill + timeout_text
                    self.session_manager.add_assistant_final(timeout_text)
                    return timeout_text

                logger.info(f"Iteration {i+1} completed in {iteration_time:.1f}s")

                if response_msg.has_tool_calls:
                    logger.info(f"Processing {len(response_msg.tool_calls)} tool call(s) from LLM")
                    
                    should_filter = getattr(config, 'DELETE_EARLY_THINK_PROSE', True)
                    
                    if should_filter:
                        filtered_content = filter_to_thinking_only(response_msg.content or "")
                        logger.info(f"[TRIM] Thinking filter ENABLED")
                    else:
                        filtered_content = response_msg.content or ""
                        logger.info(f"[TRIM] Thinking filter DISABLED - using full content")
                    
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
                    if function_call_data and active_tools:
                        tool_call_count += 1
                        logger.info("Processing text-based function call")

                        should_filter = getattr(config, 'DELETE_EARLY_THINK_PROSE', True)
                        
                        if should_filter:
                            filtered_content = filter_to_thinking_only(response_msg.content)
                            logger.info(f"[TRIM] Thinking filter ENABLED (text-based)")
                        else:
                            filtered_content = response_msg.content
                            logger.info(f"[TRIM] Thinking filter DISABLED (text-based)")

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
                
                self.session_manager.add_assistant_final(final_response_content)
                return final_response_content

            logger.warning(f"Exceeded max iterations ({config.MAX_TOOL_ITERATIONS}). Forcing final answer.")
            
            messages.append({
                "role": "user",
                "content": "You've used tools multiple times. Stop using tools now and provide your final answer based on the information you gathered."
            })

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

            self.session_manager.add_assistant_final(final_response_content)
            return final_response_content

        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            
            if "timeout" in str(e).lower() or "APITimeoutError" in str(type(e).__name__):
                error_text = "I ran into a timeout while processing your request. Please try breaking it into smaller parts."
            elif "swarm" in str(e).lower() or (hasattr(e, '__module__') and 'httpx' in str(e.__module__)):
                error_text = f"Local swarm server connection failed. Error: {str(e)}"
            elif "connection" in str(e).lower() or "ConnectError" in str(type(e).__name__):
                error_text = "I lost connection to my processing engine. Please check if services are running."
            elif "json" in str(e).lower() or "JSON" in str(e):
                error_text = "I encountered a data formatting issue while processing your request."
            else:
                error_text = f"I encountered an unexpected technical issue. Error: {str(e)[:200]}"

            self.session_manager.add_user_message(user_input)
            self.session_manager.add_assistant_final(error_text)
            return error_text

    def _select_provider(self):
        """Select LLM provider using per-chat settings or fallback order. Returns (provider_key, provider) tuple or raises."""
        
        if self._use_new_config:
            providers_config = config.LLM_PROVIDERS
            fallback_order = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config.keys()))
            
            # Check per-chat LLM settings
            chat_settings = self.session_manager.get_chat_settings()
            chat_primary = chat_settings.get('llm_primary', 'auto')
            chat_fallback = chat_settings.get('llm_fallback', 'auto')
            
            # Handle "none" - explicitly disabled
            if chat_primary == 'none':
                raise ConnectionError("LLM disabled for this chat (llm_primary=none)")
            
            # If chat has specific provider set (not "auto"), try that first
            if chat_primary and chat_primary != 'auto':
                provider = get_provider_by_key(chat_primary, providers_config, config.LLM_REQUEST_TIMEOUT)
                if provider:
                    try:
                        if provider.health_check():
                            logger.info(f"Using chat-specific primary '{chat_primary}' [{provider.provider_name}]")
                            return (chat_primary, provider)
                    except Exception as e:
                        logger.warning(f"Chat primary '{chat_primary}' health check failed: {e}")
                
                # Try chat-specific fallback if primary failed (unless fallback is "none")
                if chat_fallback and chat_fallback not in ('auto', 'none'):
                    provider = get_provider_by_key(chat_fallback, providers_config, config.LLM_REQUEST_TIMEOUT)
                    if provider:
                        try:
                            if provider.health_check():
                                logger.info(f"Using chat-specific fallback '{chat_fallback}' [{provider.provider_name}]")
                                return (chat_fallback, provider)
                        except Exception as e:
                            logger.warning(f"Chat fallback '{chat_fallback}' health check failed: {e}")
                
                # If chat_fallback is "none", don't try global fallback
                if chat_fallback == 'none':
                    raise ConnectionError(f"Chat primary '{chat_primary}' failed and fallback disabled")
            
            # Fall through to global fallback order (auto mode)
            result = get_first_available_provider(
                providers_config,
                fallback_order,
                config.LLM_REQUEST_TIMEOUT
            )
            
            if result:
                provider_key, provider = result
                logger.info(f"Using provider '{provider_key}' [{provider.provider_name}]: {provider.model}")
                return (provider_key, provider)
            
            raise ConnectionError("No LLM providers available")
        
        else:
            # Legacy config: LLM_PRIMARY/LLM_FALLBACK
            if self.provider_primary and getattr(config, 'LLM_PRIMARY', {}).get("enabled"):
                try:
                    if self.provider_primary.health_check():
                        logger.info(f"Using primary LLM [{self.provider_primary.provider_name}]: {self.provider_primary.model}")
                        return ('legacy_primary', self.provider_primary)
                except Exception as e:
                    logger.warning(f"Primary LLM health check failed: {e}")
            
            if self.provider_fallback and getattr(config, 'LLM_FALLBACK', {}).get("enabled"):
                try:
                    if self.provider_fallback.health_check():
                        logger.info(f"Using fallback LLM [{self.provider_fallback.provider_name}]: {self.provider_fallback.model}")
                        return ('legacy_fallback', self.provider_fallback)
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