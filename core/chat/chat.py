# chat.py
import json
import logging
import time
import re
import uuid
from typing import Dict, Any, Optional, List

import config
from .history import ConversationHistory, ChatSessionManager, count_tokens
from .function_manager import FunctionManager
from core.hooks import hook_runner, HookEvent
from core.metrics import metrics as token_metrics
from .chat_streaming import StreamingChat
from .chat_tool_calling import ToolCallingEngine, filter_to_thinking_only
from .llm_providers import get_provider, get_provider_for_url, get_provider_by_key, get_first_available_provider, get_generation_params

logger = logging.getLogger(__name__)


def _detect_image_media_type(b64_data: str) -> str:
    """Best-effort detect media_type from base64 image bytes.

    Tools that return images often forget to set `media_type`. Pre-fix
    we defaulted to 'image/jpeg', which Claude rejects with a 400 when
    the actual bytes are PNG (header/data mismatch). Peeking the magic
    bytes covers PNG/JPEG/GIF/WEBP — the four formats real tool-image
    outputs actually produce. Returns 'image/png' as the safer default
    since most tool-gen images are PNG (image-gen tools, screenshots,
    matplotlib charts). Wildcard scout 2026-05-07 multimodal #2.
    """
    if not b64_data:
        return 'image/png'
    try:
        import base64 as _b64
        # Decode just the prefix — a 24-byte head is plenty for sigs
        head = _b64.b64decode(b64_data[:64], validate=False)[:16]
    except Exception:
        return 'image/png'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
        return 'image/gif'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image/webp'
    # Unknown — PNG default is safer than JPEG (broader provider support)
    return 'image/png'


def _inject_tool_images(messages, tool_images, provider=None):
    """Inject tool-returned images as a user message for the next LLM turn.

    Images are added as content blocks so providers can convert them
    to their native format (Claude source blocks, OpenAI image_url, etc).

    The message rides the `user` role because OpenAI-compatible APIs can't
    carry images in a `tool` message. But it must NOT read as the user
    talking - models (notably Qwen) otherwise treat it as a fresh request and
    re-call the image tool. So the text self-labels as tool output and carries
    a gentle brake. A tool can override the framing via an `inject_note` key on
    its image dict; otherwise a super-explicit generic note is used. 2026-06-14.

    If the provider doesn't support vision, fall back to a text-only
    placeholder. Without this the next LLM call blows up with a 400
    "model does not support image inputs" the moment any image-returning
    tool (body_see, webcam, etc) fires on a text-only model. 2026-05-15.
    """
    supports = bool(provider and getattr(provider, 'supports_images', False))
    if not supports:
        messages.append({
            "role": "user",
            "content": f"[Tool returned {len(tool_images)} image(s), saved and shown to the user. "
                       f"The current model does not support image inputs, so the image "
                       f"contents are not available for analysis this turn.]"
        })
        logger.info(f"[TOOL] Skipped image injection ({len(tool_images)} image(s)) - provider does not support vision")
        return

    # Self-labeling, non-imperative framing that carries the brake into the
    # highest-authority/most-recent message (where the model actually listens),
    # instead of leaving it stranded in the distrusted tool result.
    generic_note = (
        "[These image(s) are the result of the tool call you just made and have "
        "already been shown to the user. This is NOT a message from the user. No "
        "further action is needed; do not call the tool again unless the user asks "
        "for a change. Continue your reply.]"
    )
    note = next((img.get("inject_note") for img in tool_images
                 if isinstance(img, dict) and img.get("inject_note")), None) or generic_note
    content = [{"type": "text", "text": note}]
    for img in tool_images:
        data = img.get("data", "")
        media_type = img.get("media_type") or _detect_image_media_type(data)
        content.append({
            "type": "image",
            "data": data,
            "media_type": media_type,
        })
    messages.append({"role": "user", "content": content})
    logger.info(f"[TOOL] Injected {len(tool_images)} tool image(s) into conversation")



def friendly_llm_error(e):
    """Convert LLM provider exceptions to user-friendly messages. Returns None if unrecognized."""
    error_str = str(e).lower()
    type_name = type(e).__name__

    # Privacy/private chat blocks — pass through the specific message
    if isinstance(e, ConnectionError) and ('privacy' in error_str or 'private' in error_str):
        return str(e)

    # Connection errors — detect local providers like LM Studio
    if isinstance(e, ConnectionError) or 'ConnectError' in type_name or 'connection' in error_str:
        if 'no llm' in error_str or 'no providers' in error_str:
            return "No LLM providers are configured or available. Go to Settings to add an API key and enable a provider."
        if any(h in error_str for h in ('127.0.0.1', 'localhost', '0.0.0.0')):
            return "Can't reach LM Studio — open LM Studio, load a model, and enable its local server."
        return "Lost connection to the LLM server. Check that the service is running."

    status = getattr(e, 'status_code', None)
    if not status:
        return None

    # Context size exceeded — catch before status code checks (some providers raise without HTTP status)
    if any(k in error_str for k in ('context size', 'context length', 'context_length', 'maximum context', 'token limit')):
        return "Context limit exceeded — conversation is too long for this model. Lower CONTEXT_LIMIT in Settings or start a new chat."

    if status == 400:
        if 'model' in error_str and any(k in error_str for k in ('not found', 'not loaded', 'does not exist')):
            return "Model not found or not loaded. If using LM Studio, make sure a model is loaded and running."
        if any(k in error_str for k in ('image', 'vision', 'multimodal', 'content_type')):
            return "This model doesn't support images. Load a vision model to use image attachments."
        if 'invalid tool call' in error_str or 'tool call arguments' in error_str:
            return "This provider rejected a tool call in your chat history (strict tool-call validation). Try starting a new chat, or switch to a more lenient provider (OpenAI, Fireworks)."
        if 'tool' in error_str and any(k in error_str for k in ('not support', "doesn't support", 'unsupported')):
            return "This model doesn't support tool calls. Switch to a tool-capable model or disable your toolset."
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
        
        self.current_system_prompt = None
        self.function_manager = FunctionManager()
        # Notices to surface to the user as toasts (e.g. dangling toolset
        # detected, empty-content fallback). Populated by chat() and the
        # streaming generator; consumed + cleared by the API route layer.
        self.pending_notices: list = []
        
        self.tool_engine = ToolCallingEngine(self.function_manager)

        # Per-request StreamingChat isolation — H4 2026-04-22.
        # Was: `self.streaming_chat = StreamingChat(self)` (one shared
        # instance; two tabs corrupt each other's state).
        # Now: each chat_stream call gets its own StreamingChat via
        # begin_stream(); tracked in dicts below so /api/cancel can target
        # per-chat, and status can report any-streaming. Enables the
        # many-personas/heartbeats isolation Krem wants.
        import threading as _threading
        self._streams_by_id = {}       # {stream_id: StreamingChat}
        self._streams_by_chat = {}     # {chat_name: set(stream_id)}
        self._streams_lock = _threading.Lock()

        logger.info("LLMChat.__init__ completed")

    # ── Per-request streaming state API ──

    def begin_stream(self, chat_name=None):
        """Create a fresh StreamingChat, register it. Caller owns the ref.

        Returns (stream, stream_id, chat_name_used). Pair with end_stream().
        """
        import secrets as _secrets
        stream = StreamingChat(self)
        stream.target_chat = chat_name   # A1: explicit target (None = web/active)
        sid = _secrets.token_hex(8)
        if chat_name is None:
            try:
                chat_name = self.session_manager.get_active_chat_name() or ''
            except Exception:
                chat_name = ''
        with self._streams_lock:
            self._streams_by_id[sid] = stream
            self._streams_by_chat.setdefault(chat_name, set()).add(sid)
        stream.active_chat_name = chat_name
        return stream, sid, chat_name

    def end_stream(self, stream_id, chat_name):
        """Unregister a stream. Idempotent."""
        with self._streams_lock:
            self._streams_by_id.pop(stream_id, None)
            ids = self._streams_by_chat.get(chat_name)
            if ids is not None:
                ids.discard(stream_id)
                if not ids:
                    self._streams_by_chat.pop(chat_name, None)

    def _target_stream_ids(self, chat_name, exclude_chats):
        """Shared targeting for cancel/stop: one chat's streams, or all streams
        minus exclude_chats (e.g. live phone-call chats — surface isolation)."""
        if chat_name:
            return list(self._streams_by_chat.get(chat_name, set()))
        ids = list(self._streams_by_id.keys())
        if exclude_chats:
            skip = set()
            for c in exclude_chats:
                skip |= self._streams_by_chat.get(c, set())
            ids = [i for i in ids if i not in skip]
        return ids

    def cancel_streams(self, chat_name=None, exclude_chats=None):
        """Set cancel_flag on active streams. If chat_name given, only that
        chat's streams (including every tab concurrently on it). Otherwise
        all active streams across all chats, minus exclude_chats (live
        phone-call chats — their streams belong to the phone surface).
        Returns count of streams flagged.
        """
        with self._streams_lock:
            ids = self._target_stream_ids(chat_name, exclude_chats)
            targets = [self._streams_by_id[i] for i in ids if i in self._streams_by_id]
        for s in targets:
            s.cancel_flag = True
        return len(targets)

    def stop_tts_streams(self, chat_name=None, exclude_chats=None):
        """Mute the VOICE on active streams (left-button "stop TTS") WITHOUT
        cancelling generation — the LLM keeps writing, only this message's audio
        stops. Mirrors cancel_streams' targeting. Returns count of streams muted.
        """
        with self._streams_lock:
            ids = self._target_stream_ids(chat_name, exclude_chats)
            targets = [self._streams_by_id[i] for i in ids if i in self._streams_by_id]
        for s in targets:
            try:
                s.stop_tts()
            except Exception:
                pass
        return len(targets)

    def any_streaming(self):
        """True if at least one stream is active."""
        with self._streams_lock:
            return bool(self._streams_by_id)

    def streams_for_chat(self, chat_name):
        """List of active StreamingChat instances for a chat (may be empty)."""
        with self._streams_lock:
            ids = list(self._streams_by_chat.get(chat_name, set()))
            return [self._streams_by_id[i] for i in ids if i in self._streams_by_id]

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
        from core import prompts

        # Transient prompt pieces ride the same per-turn rail as spice: drop
        # expired ones and re-snapshot so a tried-on mood falls off by itself.
        try:
            if prompts.expire_transients() and prompts.is_assembled_mode():
                prompt_data = prompts.get_current_prompt()
                content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
                self.set_system_prompt(content)
                logger.info("[PIECES] Transient piece(s) expired — reassembled prompt")
        except Exception as e:
            logger.error(f"[PIECES] Transient expiry check failed: {e}")

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
        # Per-stream brain override: a stream on a non-active chat uses THAT chat's
        # persona prompt, not the globally-primed active-chat one. Unset → active.
        prompt_template = self.current_system_prompt or "System prompt not loaded."
        try:
            from core.chat.stream_brain import get_override
            _o = get_override()
            if _o and _o.get("system_prompt") is not None:
                prompt_template = _o["system_prompt"] or "System prompt not loaded."
        except Exception:
            pass
        prompt = prompt_template.replace("{user_name}", username).replace("{ai_name}", ai_name)

        # Build context parts from chat settings
        context_parts = []
        chat_settings = self.session_manager.get_chat_settings()

        # Datetime moved to the ghost-message rail (core/ghost_messages.py)
        # 2026-05-08 — same per-turn freshness, but injected as a separate
        # operator-metadata message right before the new user input. Keeps
        # the system prompt cacheable across turns.

        # Inject custom context if present (LONG-LIVED character info — stays
        # in system prompt where caching it is fine and the AI treats it as
        # part of "who I am"). Per-turn ephemera goes through ghost instead.
        custom_ctx = chat_settings.get('custom_context', '').strip()
        if custom_ctx:
            context_parts.append(custom_ctx)

        # Spice in system-prompt mode (SPICE_DELIVERY='system'): woven in
        # unattributed so the AI wears it as its own inclination. Ghost mode
        # (default) delivers it as labeled app context instead — cache-friendly
        # but third-person. Rebuilt every call, so rotation lands immediately;
        # the prompt mutating on rotation turns is this mode's documented cost.
        if (getattr(config, 'SPICE_DELIVERY', 'ghost') == 'system'
                and chat_settings.get('spice_enabled', True)):
            from core import prompts
            sp = (prompts.get_current_spice() or '').strip()
            if sp:
                context_parts.append(sp)

        # Plugin prompt_inject hook — append to context_parts
        if hook_runner.has_handlers("prompt_inject"):
            inject_event = HookEvent(context_parts=context_parts, config=config)
            hook_runner.fire("prompt_inject", inject_event)

        # Combine all static context into main prompt
        if context_parts:
            prompt = f"{prompt}\n\n{chr(10).join(context_parts)}"

        return prompt, username, None

    def resolve_stream_brain(self, chat_name):
        """Resolve a non-active chat's brain — settings, base system prompt, tool
        list — WITHOUT activating it or mutating any global state. For a conversation
        stream that must run in its own chat concurrently with the UI. Returns the
        override dict, or None when chat_name is the active chat (None = OFF-path, no
        override needed, behavior identical to before)."""
        try:
            active = self.session_manager.get_active_chat_name()
            if not chat_name or chat_name == active:
                return None
            settings = self.session_manager.get_settings_for(chat_name)
            if settings is None:
                return None
            from core import prompts
            pdata = prompts.get_prompt(settings.get("prompt", "default"))
            system_prompt = (pdata.get("content", "") if isinstance(pdata, dict) else "") or ""
            tools = self._resolve_toolset_tools(settings.get("toolset", "all"),
                                                settings.get("extra_toolsets"))
            return {"chat": chat_name, "settings": settings,
                    "system_prompt": system_prompt, "tools": tools}
        except Exception as e:
            logger.warning(f"resolve_stream_brain('{chat_name}') failed: {e}")
            return None

    def _resolve_toolset_tools(self, toolset_name, extra_toolsets=None):
        """Toolset name -> tool-schema list, READ-ONLY (mirrors
        ExecutionContext._resolve_tools; no function_manager mutation).
        extra_toolsets unions module/toolset function sets on top — same
        semantics as update_enabled_functions (chat setting `extra_toolsets`;
        'none' + extras is the story engine's default)."""
        fm = self.function_manager
        if (not toolset_name or toolset_name == "none") and not extra_toolsets:
            return None
        try:
            from core.toolsets import toolset_manager

            def _fn_names(name):
                if name in getattr(fm, "function_modules", {}):
                    return fm.function_modules[name]["available_functions"]
                if toolset_manager.toolset_exists(name):
                    return toolset_manager.get_toolset_functions(name)
                return [name]

            if not toolset_name or toolset_name == "none":
                tools = []
            elif toolset_name == "all":
                tools = list(fm.all_possible_tools)
            else:
                fn_set = set(_fn_names(toolset_name))
                tools = [t for t in fm.all_possible_tools if t["function"]["name"] in fn_set]
            if extra_toolsets:
                have = {t["function"]["name"] for t in tools}
                extra_set = set()
                for name in extra_toolsets:
                    extra_set |= set(_fn_names(name))
                extra_set -= have
                tools += [t for t in fm.all_possible_tools if t["function"]["name"] in extra_set]
            if hasattr(fm, "_apply_mode_filter"):
                tools = fm._apply_mode_filter(tools)
            return tools or None
        except Exception as e:
            logger.warning(f"_resolve_toolset_tools('{toolset_name}') failed: {e}")
            return None

    def _build_base_messages(self, user_input: str, images: list = None, files: list = None):
        system_prompt, user_name, dynamic_context = self._get_system_prompt()

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
            user_content = []
            if user_input:
                user_content.append({"type": "text", "text": user_input})
            for img in images:
                user_content.append({
                    "type": "image",
                    "data": img.get("data", ""),
                    "media_type": img.get("media_type", "image/jpeg")
                })
        else:
            user_content = user_input

        # Ghost message — per-turn ephemera (spice, datetime, plugin context)
        # injected as a labeled operator-metadata user-role message between
        # history and the new user input. Never persisted to chat history.
        # Keeps the system prompt + history cacheable across turns. See
        # core/ghost_messages.py for design + envelope format. 2026-05-08.
        # `system` attr may be unset in test fixtures; getattr default keeps
        # build_ghost_message happy (its hook firing tolerates a None system).
        from core.ghost_messages import build_ghost_message
        chat_settings_for_ghost = self.session_manager.get_chat_settings()
        ghost_text = build_ghost_message(
            getattr(self, 'system', None),
            chat_settings_for_ghost,
            user_input or "",
        )

        messages = [
            {"role": "system", "content": system_prompt},
            *history_messages,
        ]
        if ghost_text:
            messages.append({"role": "user", "content": ghost_text})
        messages.append({"role": "user", "content": user_content})

        # Dynamic story context — injected as separate system content for cache efficiency
        # This changes every turn (state vars, clues, exits) while the main system prompt stays cached
        if dynamic_context:
            messages.insert(1, {"role": "system", "content": dynamic_context, "_dynamic": True})

        # RAG injection — if chat has uploaded documents, search and inject
        rag_context = self._get_rag_context(user_input)
        if rag_context:
            messages.insert(-1, {"role": "user", "content": rag_context})

        return messages

    # Per-chat RAG context levels: (top_k, max_tokens)
    _RAG_LEVELS = {
        'light':  (2, 1500),
        'normal': (5, 4000),
        'heavy':  (10, 8000),
    }

    def _get_rag_context(self, user_input):
        """Search per-chat RAG documents and return context string, or None."""
        chat_settings = self.session_manager.get_chat_settings()
        rag_level = chat_settings.get('rag_context', 'normal')
        if rag_level == 'off':
            return None

        # Effective chat, not active (matches the get_chat_settings() above): a phone
        # call / background conversation reads ITS chat's RAG docs, never the operator's
        # active-chat documents. Without this a stranger's call could pull owner files.
        chat_name = self.session_manager._effective_chat_name()
        rag_scope = f"__rag__:{chat_name}"

        try:
            from plugins.memory.tools import knowledge_tools as knowledge
            entries = knowledge.get_entries_by_scope(rag_scope)
            if not entries:
                return None

            top_k, max_tokens = self._RAG_LEVELS.get(rag_level, self._RAG_LEVELS['normal'])

            results = knowledge.search_rag(
                user_input, rag_scope,
                limit=top_k,
                threshold=config.RAG_SIMILARITY_THRESHOLD,
                max_tokens=max_tokens
            )
            if not results:
                return None
            parts = ["[Reference Documents]"]
            for r in results:
                parts.append(f"--- {r['filename']} (relevance: {r['score']:.0%}) ---\n{r['content']}")
            return "\n\n".join(parts)
        except Exception as e:
            logger.error(f"[RAG] Failed to get context: {e}", exc_info=True)
            return f"[RAG documents are configured but failed to load: {e}]"

    def chat(self, user_input: str):
        try:
            chat_start_time = time.time()
            self.refresh_spice_if_needed()
            logger.info(f"[CHAT] CHAT: user said something here")

            # Plugin pre_chat hook — can modify input, bypass LLM, or stop propagation
            if hook_runner.has_handlers("pre_chat"):
                hook_event = HookEvent(input=user_input, config=config,
                                       metadata={"system": self.system})
                hook_runner.fire("pre_chat", hook_event)
                if hook_event.skip_llm:
                    response = hook_event.response or ""
                    if response and not hook_event.ephemeral:
                        self.session_manager.add_user_message(user_input)
                        self.session_manager.add_assistant_final(response)
                    return response
                user_input = hook_event.input  # may have been mutated

            messages = self._build_base_messages(user_input)
            self.session_manager.add_user_message(user_input)

            # Drain any dangling-toolset state that update_enabled_functions
            # left for us (e.g. on chat-activation). Surface as a toast on
            # this turn's response so the user sees it the moment they engage.
            # getattr is defensive: some tests mock function_manager without
            # this attribute.
            bad_ts = getattr(self.function_manager, 'last_dangling_toolset', None)
            if bad_ts:
                self.pending_notices.append({
                    "message": f"Toolset '{bad_ts}' is missing — tools disabled for this chat. Fix in chat settings.",
                    "severity": "warning",
                })
                self.function_manager.last_dangling_toolset = None

            # Set scopes for this chat context
            # Reset first to prevent bleed: when a chat's saved settings don't include
            # a newly-registered plugin scope, apply_scopes would leave the previous
            # chat's value in place. reset_scopes() puts every scope back to its default
            # before we apply the chat's specific values on top.
            from core.chat.function_manager import reset_scopes
            reset_scopes()
            chat_settings = self.session_manager.get_chat_settings()
            self.function_manager.apply_scopes(chat_settings)
            # Effective chat (matches the get_chat_settings() applied above) — a stream
            # on a non-active chat reads ITS RAG scope, not the operator's active chat.
            chat_name = self.session_manager._effective_chat_name()
            self.function_manager.set_rag_scope(f"__rag__:{chat_name}")
            _scopes = self.function_manager.snapshot_scopes()

            # Send only enabled tools - model should only know about active tools
            # Snapshot names for validation — prevents race if plugins reload mid-chat
            enabled_tools = self.function_manager.enabled_tools
            _allowed_tool_names = {t["function"]["name"] for t in enabled_tools if "function" in t}
            # Snapshot executor map too — protects against plugin reload mid-chat
            # yanking executors out from under in-flight tool calls. Streaming
            # path captures this at chat_streaming.py:179; non-streaming was
            # missing the same protection. 2026-05-16.
            _executor_snapshot = self.function_manager.snapshot_executors()

            # DIAGNOSTIC: Log what tools are being sent
            enabled_names = [t['function']['name'] for t in enabled_tools] if enabled_tools else []
            logger.info(f"[TOOLS] Sending {len(enabled_names)} tools to LLM: {enabled_names}")
            logger.info(f"[TOOLS] Current toolset: {self.function_manager.current_toolset_name}")
            logger.info(f"[TOOLS] Prompt mode: {self.function_manager._get_current_prompt_mode()}")
            
            provider_key, provider, model_override = self._select_provider()

            # Determine effective model (per-chat override or provider default)
            effective_model = model_override if model_override else provider.model

            # Provenance for tool executors (mindpalace metadata). Patches the
            # _scopes snapshot taken above, since the model resolves only here.
            from core.chat.function_manager import set_tool_context
            set_tool_context(_scopes, chat=chat_name,
                             persona=chat_settings.get('prompt'),
                             model=effective_model)

            # Get generation params for this provider/model
            gen_params = get_generation_params(
                provider_key, 
                effective_model, 
                {**getattr(config, 'LLM_PROVIDERS', {}), **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
            )
            
            # Pass model override to provider if set
            if model_override:
                gen_params['model'] = model_override

            tool_call_count = 0
            last_tool_name = None
            loop_counts = {}  # per-turn per-tool call counts (loop guard); turn-local by design
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
                    
                    error_brief = str(llm_error)[:200]
                    timeout_text = f"LLM call to {provider_key} failed after {iteration_time:.1f}s: {error_brief}"
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
                        logger.warning(f"[TOOLS] [!] LLM called tools NOT in active set: {unexpected}")
                    
                    logger.info(f"Processing {len(response_msg.tool_calls)} tool call(s) from LLM")
                    
                    # Always filter thinking content from tool call responses
                    filtered_content = filter_to_thinking_only(response_msg.content or "")
                    
                    tool_calls_formatted = response_msg.get_tool_calls_as_dicts()
                    
                    # Slice to MAX_PARALLEL_TOOLS limit
                    tool_calls_to_execute = tool_calls_formatted[:config.MAX_PARALLEL_TOOLS]
                    if len(tool_calls_to_execute) < len(tool_calls_formatted):
                        logger.info(f"[LIMIT] Executing {len(tool_calls_to_execute)}/{len(tool_calls_formatted)} tools (MAX_PARALLEL_TOOLS={config.MAX_PARALLEL_TOOLS})")
                    
                    # Include `thinking` so DeepSeek-reasoner's required
                    # reasoning_content round-trip works on subsequent iterations.
                    # See chat_streaming.py and openai_compat.py:427-430. 2026-05-14.
                    _thinking = getattr(response_msg, "thinking", None)
                    messages.append({
                        "role": "assistant",
                        "content": filtered_content,
                        "tool_calls": tool_calls_to_execute,
                        "thinking": _thinking,
                    })
                    self.session_manager.add_assistant_with_tool_calls(filtered_content, tool_calls_to_execute)

                    # Track last tool name
                    if tool_calls_to_execute:
                        last_tool_name = tool_calls_to_execute[0]["function"]["name"]

                    tools_executed, tool_images = self.tool_engine.execute_tool_calls(
                        tool_calls_to_execute,
                        messages,
                        self.session_manager,
                        provider,
                        scopes=_scopes,
                        allowed_tools=_allowed_tool_names,
                        executor_snapshot=_executor_snapshot,
                        loop_counts=loop_counts,
                    )
                    tool_call_count += tools_executed

                    # Inject tool-returned images as user message for next LLM turn
                    if tool_images:
                        _inject_tool_images(messages, tool_images, provider)

                    # Refresh tools list — tool_load may have added new tools.
                    # Also refresh executor snapshot so newly-loaded tools become
                    # callable (and stale executors get released) on next iter.
                    enabled_tools = self.function_manager.enabled_tools
                    _allowed_tool_names = {t["function"]["name"] for t in enabled_tools if "function" in t}
                    _executor_snapshot = self.function_manager.snapshot_executors()

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
                            logger.warning(f"[TOOLS] [!] Text-based call for tool NOT in active set: {text_tool_name}")
                        
                        tool_call_count += 1
                        logger.info("Processing text-based function call")

                        # Always filter thinking content from tool call responses
                        filtered_content = filter_to_thinking_only(response_msg.content)

                        last_tool_name = function_call_data["function_call"]["name"]

                        _, tool_images = self.tool_engine.execute_text_based_tool_call(
                            function_call_data,
                            filtered_content,
                            messages,
                            self.session_manager,
                            provider,
                            scopes=_scopes,
                            allowed_tools=_allowed_tool_names,
                            executor_snapshot=_executor_snapshot,
                            loop_counts=loop_counts,
                        )

                        if tool_images:
                            _inject_tool_images(messages, tool_images, provider)

                        logger.info(f"Text-based tool iteration {i+1} completed")
                        continue

                logger.info(f"No more tool calls. Final response. (Total tools: {tool_call_count})")
                # When the LLM stops without returning content, persist a short
                # honest placeholder in chat history and surface the actionable
                # hint as a toast. Previously substituted "I have completed the
                # requested actions" — confident lie when the real cause was
                # usually a tool error. 2026-05-16.
                if response_msg.content:
                    final_response_content = response_msg.content
                else:
                    final_response_content = "(no response)"
                    if tool_call_count > 0:
                        self.pending_notices.append({
                            "message": (
                                f"Generation ended without a reply after {tool_call_count} tool call(s). "
                                f"A tool likely errored or isn't in the active toolset — check the logs, or rephrase."
                            ),
                            "severity": "warning",
                        })
                    else:
                        self.pending_notices.append({
                            "message": "Model returned no content. Try rephrasing or check the LLM provider settings.",
                            "severity": "warning",
                        })
                
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
                    for k in ("cache_read_tokens", "cache_write_tokens"):
                        if response_msg.usage.get(k):
                            tokens_info[k] = response_msg.usage[k]
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

                # Record metrics — effective chat so a phone call's tokens attribute
                # to ITS chat, not the operator's active chat.
                try:
                    chat_name = self.session_manager._effective_chat_name()
                    token_metrics.record(chat_name, provider_key, effective_model,
                                         "conversation", metadata,
                                         estimated=tokens_info.get("estimated", False))
                except Exception:
                    pass
                
                # post_llm hook — plugins can mutate response before save + TTS
                if hook_runner.has_handlers("post_llm"):
                    llm_event = hook_runner.fire("post_llm", HookEvent(
                        input=user_input, response=final_response_content,
                        config=config, metadata={"system": self.system}
                    ))
                    final_response_content = llm_event.response or final_response_content

                self.session_manager.add_assistant_final(final_response_content, metadata=metadata)

                if hook_runner.has_handlers("post_chat"):
                    hook_runner.fire("post_chat", HookEvent(
                        input=user_input, response=final_response_content,
                        config=config, metadata={"system": self.system}
                    ))

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
                for k in ("cache_read_tokens", "cache_write_tokens"):
                    if final_response_msg.usage.get(k):
                        tokens_info[k] = final_response_msg.usage[k]
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

            try:
                # effective chat: attribute a stream's tokens to ITS chat.
                chat_name = self.session_manager._effective_chat_name()
                token_metrics.record(chat_name, provider_key, effective_model,
                                     "conversation", metadata,
                                     estimated=tokens_info.get("estimated", False))
            except Exception:
                pass

            # post_llm hook — plugins can mutate forced-final response
            if hook_runner.has_handlers("post_llm"):
                llm_event = hook_runner.fire("post_llm", HookEvent(
                    input=user_input, response=final_response_content,
                    config=config, metadata={"system": self.system}
                ))
                final_response_content = llm_event.response or final_response_content

            self.session_manager.add_assistant_final(final_response_content, metadata=metadata)

            if hook_runner.has_handlers("post_chat"):
                hook_runner.fire("post_chat", HookEvent(
                    input=user_input, response=final_response_content,
                    config=config, metadata={"system": self.system}
                ))

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
            providers_config = {**config.LLM_PROVIDERS, **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
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
                # Private chat: provider must be marked local/private-safe
                try:
                    from core.chat.llm_providers import PROVIDER_METADATA
                    if chat_settings.get('private_chat', False):
                        pconf = providers_config.get(chat_primary, {})
                        meta = PROVIDER_METADATA.get(chat_primary, {})
                        if not pconf.get('is_local', meta.get('is_local', False)):
                            raise ConnectionError(f"Provider '{chat_primary}' is not marked local/private-safe and is blocked in this private chat. Tick 'Local / private server' on the model or turn off private chat.")
                except ConnectionError:
                    raise
                except Exception as e:
                    logger.error(f"Privacy check failed (defaulting to BLOCK): {e}")
                    raise ConnectionError("Privacy check encountered an error — blocking provider for safety. Check logs.")

                # Per-chat first-token/read deadline (e.g. phone call chats get a
                # snappy 20s from the twilio daemon; everything else keeps the
                # 240s system default — slower chat models are unaffected).
                try:
                    _rt = float(chat_settings.get('llm_request_timeout') or 0)
                except (TypeError, ValueError):
                    _rt = 0.0
                provider = get_provider_by_key(chat_primary, providers_config,
                                               _rt if _rt > 0 else config.LLM_REQUEST_TIMEOUT,
                                               model_override=chat_model)
                if not provider:
                    raise ConnectionError(f"Provider '{chat_primary}' not configured or disabled")

                # Pinned-provider health TTL: this path re-runs EVERY turn, and
                # the pre-flight models.list round-trip was pure added latency on
                # phone turns (a pinned provider has no fallback — it just raises).
                # A pass is trusted for 60s; between checks the completion call
                # itself is the health signal. 2026-07-15.
                import time as _time
                _hc = getattr(self, "_pinned_health_cache", None)
                if _hc is None:
                    _hc = self._pinned_health_cache = {}
                healthy = _time.time() < _hc.get(chat_primary, 0)
                if not healthy:
                    try:
                        healthy = bool(provider.health_check())
                    except Exception:
                        healthy = False
                    if healthy:
                        _hc[chat_primary] = _time.time() + 60.0
                if healthy:
                    logger.info(f"Using chat-specific provider '{chat_primary}'" +
                               (f" with model '{chat_model}'" if chat_model else ""))
                    return (chat_primary, provider, chat_model)

                raise ConnectionError(f"Provider '{chat_primary}' failed health check - no fallback for specific provider selection")
            
            # Auto mode - use global fallback order
            result = get_first_available_provider(
                providers_config,
                fallback_order,
                config.LLM_REQUEST_TIMEOUT,
                force_privacy=chat_settings.get('private_chat', False)
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
        from core.chat.function_manager import reset_scopes
        reset_scopes()
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
        original_toolset = self.function_manager.current_toolset_name
        # Capture the active chat's extras too — restoring by name only
        # strips them from the enabled set (story-tools decay, 2026-08-03)
        try:
            original_extras = (self.session_manager.get_chat_settings() or {}).get('extra_toolsets') or None
        except Exception:
            original_extras = None

        try:
            # Build system prompt from task settings
            prompt_name = task_settings.get("prompt", "sapphire")
            from core import prompts
            prompt_data = prompts.get_prompt(prompt_name)
            if prompt_data:
                system_prompt = prompt_data.get("content") if isinstance(prompt_data, dict) else str(prompt_data)
            else:
                system_prompt = "You are a helpful assistant."
            
            # Apply name substitutions
            username = getattr(config, 'DEFAULT_USERNAME', 'Human')
            ai_name = 'Sapphire'
            system_prompt = system_prompt.replace("{user_name}", username).replace("{ai_name}", ai_name)
            
            # Inject datetime if enabled (user's timezone)
            if task_settings.get("inject_datetime"):
                try:
                    from zoneinfo import ZoneInfo
                    tz_name = getattr(config, 'USER_TIMEZONE', 'UTC') or 'UTC'
                    now = datetime.now(ZoneInfo(tz_name))
                    tz_label = f" ({tz_name})"
                except Exception:
                    now = datetime.now()
                    tz_label = ""
                system_prompt = f"{system_prompt}\n\nCurrent date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}{tz_label}"
            
            # Build messages - just system + user, no history for ephemeral
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ]
            
            # Get tools if toolset specified
            tools = None
            toolset = task_settings.get("toolset")
            if toolset and toolset not in ("none", ""):
                # Temporarily set scopes for tool execution
                # First reset all to defaults so stale chat state doesn't leak into tasks,
                # then apply task-specific overrides on top
                from core.chat.function_manager import reset_scopes
                reset_scopes()
                self.function_manager.apply_scopes(task_settings)
                self.function_manager.set_rag_scope(None)
                self.function_manager.set_private_chat(False)
                self.function_manager.update_enabled_functions([toolset])
                tools = self.function_manager.enabled_tools
                _allowed_tool_names = {t["function"]["name"] for t in tools if "function" in t}
                _scopes = self.function_manager.snapshot_scopes()
                logger.info(f"[ISOLATED] Using toolset '{toolset}' with {len(tools)} tools")
            else:
                _scopes = None
                _allowed_tool_names = None

            # Select provider
            provider_key = task_settings.get("provider", "auto")
            model_override = task_settings.get("model", "")
            
            if provider_key and provider_key not in ("auto", ""):
                providers_config = {**getattr(config, 'LLM_PROVIDERS', {}), **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
                provider = get_provider_by_key(provider_key, providers_config, config.LLM_REQUEST_TIMEOUT, model_override=model_override)
                if not provider:
                    raise ConnectionError(f"Provider '{provider_key}' not available")
            else:
                provider_key, provider, model_override = self._select_provider()
            
            effective_model = model_override if model_override else provider.model
            gen_params = get_generation_params(
                provider_key, 
                effective_model, 
                {**getattr(config, 'LLM_PROVIDERS', {}), **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
            )
            if model_override:
                gen_params['model'] = model_override
            
            logger.info(f"[ISOLATED] Using provider '{provider_key}', model '{effective_model}'")
            
            # Agentic tool loop — call LLM, execute tools, feed results back
            max_iterations = task_settings.get("max_tool_rounds") or config.MAX_TOOL_ITERATIONS
            max_parallel = task_settings.get("max_parallel_tools") or config.MAX_PARALLEL_TOOLS
            context_limit = task_settings.get("context_limit") or getattr(config, 'CONTEXT_LIMIT', 0)

            logger.info(f"[ISOLATED] Limits: max_iterations={max_iterations}, max_parallel={max_parallel}, context_limit={context_limit}")

            final_content = None
            response_msg = None
            tool_call_count = 0
            loop_counts = {}  # per-turn per-tool call counts (loop guard); turn-local by design

            for i in range(max_iterations):
                # Context limit check — bail if messages are getting too large
                if context_limit > 0:
                    total_tokens = sum(count_tokens(str(m.get("content", ""))) for m in messages)
                    if total_tokens > context_limit * 0.9:  # 90% threshold
                        logger.warning(f"[ISOLATED] Context limit approaching ({total_tokens}/{context_limit} tokens). Forcing final answer.")
                        break

                response_msg = self.tool_engine.call_llm_with_metrics(
                    provider, messages, gen_params, tools=tools
                )

                if response_msg.has_tool_calls:
                    filtered = filter_to_thinking_only(response_msg.content or "")
                    tool_calls = response_msg.get_tool_calls_as_dicts()[:max_parallel]
                    messages.append({
                        "role": "assistant", "content": filtered,
                        "tool_calls": tool_calls
                    })
                    tools_executed, tool_images = self.tool_engine.execute_tool_calls(
                        tool_calls, messages, None, provider, scopes=_scopes,
                        allowed_tools=_allowed_tool_names, loop_counts=loop_counts
                    )
                    tool_call_count += tools_executed
                    if tool_images:
                        _inject_tool_images(messages, tool_images)
                    logger.info(f"[ISOLATED] Loop {i+1}: executed {tools_executed} tools (total: {tool_call_count})")
                    continue

                elif response_msg.content:
                    fn_data = self.tool_engine.extract_function_call_from_text(response_msg.content)
                    if fn_data:
                        filtered = filter_to_thinking_only(response_msg.content)
                        _, tool_images = self.tool_engine.execute_text_based_tool_call(
                            fn_data, filtered, messages, None, provider, scopes=_scopes, loop_counts=loop_counts
                        )
                        if tool_images:
                            _inject_tool_images(messages, tool_images)
                        tool_call_count += 1
                        logger.info(f"[ISOLATED] Loop {i+1}: text-based tool call (total: {tool_call_count})")
                        continue

                final_content = response_msg.content
                break

            # Hit max iterations without a prose response — force one
            if final_content is None and tool_call_count > 0:
                logger.warning(f"[ISOLATED] Max iterations ({max_iterations}) hit. Forcing final answer.")
                messages.append({
                    "role": "user",
                    "content": "You've used tools multiple times. Stop using tools now and provide your final answer based on the information you gathered."
                })
                try:
                    forced = self.tool_engine.call_llm_with_metrics(
                        provider, messages, gen_params, tools=None
                    )
                    final_content = forced.content or f"I used {tool_call_count} tools and gathered information, but couldn't formulate a final answer."
                except Exception as e:
                    logger.error(f"[ISOLATED] Forced final response failed: {e}")
                    final_content = f"I used {tool_call_count} tools but encountered technical difficulties."
            elif final_content is None:
                final_content = response_msg.content if response_msg else None

            if final_content:
                content = re.sub(r'<think>.*?</think>\s*', '', final_content, flags=re.DOTALL).strip()
                logger.info(f"[ISOLATED] Done: {tool_call_count} tool calls, {len(content)} chars content")
                return content if content else final_content
            else:
                logger.warning("[ISOLATED] Empty response from provider")
                return "No response received."
                
        except Exception as e:
            logger.error(f"[ISOLATED] Chat failed: {e}", exc_info=True)
            return f"Error: {e}"
        finally:
            self.function_manager.update_enabled_functions([original_toolset], extra_toolsets=original_extras)