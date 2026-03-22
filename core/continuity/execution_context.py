# core/continuity/execution_context.py — Isolated execution environment
#
# Each task (heartbeat, daemon, foreground) gets its own ExecutionContext.
# Zero shared mutable state — no singleton mutations, no bleed between tasks.
#
# The FunctionManager is treated as a READ-ONLY registry.
# Prompt, tools, scopes, provider are all resolved at construction time.

import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

import config
from core.chat.llm_providers import get_provider_by_key, get_first_available_provider, get_generation_params

logger = logging.getLogger(__name__)


class ExecutionContext:
    """Self-contained execution environment for a single task run.

    Resolves prompt, tools, scopes, and provider at construction.
    Runs LLM + tool loop without touching any singleton state.
    """

    def __init__(self, function_manager, tool_engine, task_settings: Dict[str, Any]):
        self.fm = function_manager
        self.tool_engine = tool_engine
        self.task_settings = task_settings

        # Resolve everything upfront — all read-only operations
        self.system_prompt = self._build_prompt()
        self.tools = self._resolve_tools()
        self._allowed_tool_names = {t["function"]["name"] for t in self.tools if "function" in t} if self.tools else None
        self.scopes = self._build_scopes()
        self.provider_key, self.provider, self.model_override = self._resolve_provider()
        self.gen_params = self._build_gen_params()
        self.tool_log = []  # List of tool names called during run()

    # ── Construction (read-only) ──

    def _build_prompt(self) -> str:
        """Build system prompt from task settings. No global mutation."""
        prompt_name = self.task_settings.get("prompt", "sapphire")
        from core import prompts

        prompt_data = prompts.get_prompt(prompt_name)
        if prompt_data:
            system_prompt = prompt_data.get("content") if isinstance(prompt_data, dict) else str(prompt_data)
        else:
            system_prompt = "You are a helpful assistant."

        # Name substitutions
        username = getattr(config, 'DEFAULT_USERNAME', 'Human')
        ai_name = 'Sapphire'
        system_prompt = system_prompt.replace("{user_name}", username).replace("{ai_name}", ai_name)

        # Datetime injection
        if self.task_settings.get("inject_datetime"):
            try:
                from zoneinfo import ZoneInfo
                tz_name = getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC'
                now = datetime.now(ZoneInfo(tz_name))
                tz_label = f" ({tz_name})"
            except Exception:
                now = datetime.now()
                tz_label = ""
            system_prompt = f"{system_prompt}\n\nCurrent date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}{tz_label}"

        return system_prompt

    def _resolve_tools(self) -> Optional[List[Dict]]:
        """Resolve toolset to tool list. READ-ONLY — no mutation of FunctionManager."""
        toolset_name = self.task_settings.get("toolset", "none")

        if not toolset_name or toolset_name == "none":
            return None

        if toolset_name == "all":
            tools = self.fm.all_possible_tools.copy()
        else:
            # Resolve toolset name to function names — same logic as update_enabled_functions
            # but without mutating _enabled_tools or current_toolset_name
            from core.toolsets import toolset_manager

            if toolset_name in self.fm.function_modules:
                fn_names = self.fm.function_modules[toolset_name]['available_functions']
            elif toolset_manager.toolset_exists(toolset_name):
                fn_names = toolset_manager.get_toolset_functions(toolset_name)
            else:
                fn_names = [toolset_name]

            fn_set = set(fn_names)
            tools = [t for t in self.fm.all_possible_tools
                     if t['function']['name'] in fn_set]

            if not tools:
                logger.warning(f"[ExecCtx] Toolset '{toolset_name}' resolved to 0 tools")

        # Apply mode filter (read-only)
        tools = self.fm._apply_mode_filter(tools)
        logger.info(f"[ExecCtx] Toolset '{toolset_name}': {len(tools)} tools")
        return tools if tools else None

    def _build_scopes(self) -> Optional[Dict]:
        """Build scopes from task settings. Sets ContextVars for this thread only.
        Always resets scopes to prevent bleed between queued task iterations."""
        from core.chat.function_manager import apply_scopes_from_settings, reset_scopes, snapshot_all_scopes

        # Always reset first — prevents scope bleed when queue drains multiple
        # iterations on the same thread (previous task's scopes would linger)
        reset_scopes()

        if not self.tools:
            return None

        # Apply task-specific scopes to this thread's ContextVars
        apply_scopes_from_settings(self.fm, self.task_settings)
        # Also clear rag/private since tasks don't use those
        self.fm.set_rag_scope(None)
        self.fm.set_private_chat(False)

        return snapshot_all_scopes()

    def _resolve_provider(self) -> Tuple:
        """Select LLM provider from task settings. Returns (key, provider, model_override)."""
        provider_key = self.task_settings.get("provider", "auto")
        model_override = self.task_settings.get("model", "")

        providers_config = getattr(config, 'LLM_PROVIDERS', {})

        if provider_key and provider_key not in ("auto", ""):
            provider = get_provider_by_key(
                provider_key, providers_config,
                config.LLM_REQUEST_TIMEOUT,
                model_override=model_override
            )
            if not provider:
                raise ConnectionError(f"Provider '{provider_key}' not available")
            return provider_key, provider, model_override

        # Auto mode — fallback order
        fallback_order = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config.keys()))
        result = get_first_available_provider(
            providers_config, fallback_order, config.LLM_REQUEST_TIMEOUT
        )
        if result:
            pk, prov = result
            return pk, prov, model_override

        raise ConnectionError("No LLM providers available")

    def _build_gen_params(self) -> Dict:
        """Build generation parameters for the resolved provider/model."""
        effective_model = self.model_override if self.model_override else self.provider.model
        params = get_generation_params(
            self.provider_key, effective_model,
            getattr(config, 'LLM_PROVIDERS', {})
        )
        if self.model_override:
            params['model'] = self.model_override
        return params

    # ── Execution ──

    def run(self, user_input: str, history_messages: List[Dict] = None) -> str:
        """Run LLM + tool loop in complete isolation. Returns response text.

        Args:
            user_input: The user/event message
            history_messages: Optional prior messages for foreground chat continuity.
                              If None, runs ephemeral (system + user only).
        """
        from core.chat.chat import filter_to_thinking_only, _inject_tool_images

        # Build messages
        if history_messages is not None:
            # Foreground mode — use existing chat history
            messages = [{"role": "system", "content": self.system_prompt}] + history_messages
            messages.append({"role": "user", "content": user_input})
        else:
            # Ephemeral — no history
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_input}
            ]

        max_iterations = self.task_settings.get("max_tool_rounds") or config.MAX_TOOL_ITERATIONS
        max_parallel = self.task_settings.get("max_parallel_tools") or config.MAX_PARALLEL_TOOLS
        context_limit = self.task_settings.get("context_limit") or getattr(config, 'CONTEXT_LIMIT', 0)

        logger.info(f"[ExecCtx] Running: provider='{self.provider_key}', "
                     f"tools={len(self.tools) if self.tools else 0}, "
                     f"history={len(history_messages) if history_messages else 0} msgs")

        final_content = None

        for i in range(max_iterations):
            # Context limit check
            if context_limit > 0:
                from core.chat.history import count_tokens
                total_tokens = sum(count_tokens(str(m.get("content", ""))) for m in messages)
                if total_tokens > context_limit * 0.9:
                    logger.warning(f"[ExecCtx] Context limit approaching ({total_tokens}/{context_limit})")
                    break

            response_msg = self.tool_engine.call_llm_with_metrics(
                self.provider, messages, self.gen_params, tools=self.tools
            )

            if response_msg.has_tool_calls:
                filtered = filter_to_thinking_only(response_msg.content or "")
                tool_calls = response_msg.get_tool_calls_as_dicts()[:max_parallel]
                messages.append({
                    "role": "assistant", "content": filtered,
                    "tool_calls": tool_calls
                })
                self.tool_log.extend(tc.get('function', {}).get('name', '?') for tc in tool_calls)
                tools_executed, tool_images = self.tool_engine.execute_tool_calls(
                    tool_calls, messages, None, self.provider, scopes=self.scopes,
                    allowed_tools=self._allowed_tool_names
                )
                if tool_images:
                    _inject_tool_images(messages, tool_images)
                logger.info(f"[ExecCtx] Loop {i+1}: {tools_executed} tools executed")
                continue

            elif response_msg.content:
                fn_data = self.tool_engine.extract_function_call_from_text(response_msg.content)
                if fn_data:
                    self.tool_log.append(fn_data.get('name', '?'))
                    filtered = filter_to_thinking_only(response_msg.content)
                    _, tool_images = self.tool_engine.execute_text_based_tool_call(
                        fn_data, filtered, messages, None, self.provider, scopes=self.scopes,
                        allowed_tools=self._allowed_tool_names
                    )
                    if tool_images:
                        _inject_tool_images(messages, tool_images)
                    continue

                final_content = response_msg.content
                break
            else:
                logger.warning("[ExecCtx] Empty response from LLM")
                break

        # If we exhausted iterations, try to extract last content
        if final_content is None and messages:
            last = messages[-1]
            if last.get("role") == "assistant" and last.get("content"):
                final_content = last["content"]

        return final_content or ""
