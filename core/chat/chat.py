# chat.py — the turn CHASSIS, not an engine.
#
# LLMChat owns everything a turn needs — provider selection, system prompt
# assembly, RAG, spice, the per-request stream registry, tool-image
# injection — and hands it to THE one turn pipeline in chat_streaming.py.
# chat() below is a blocking CONSUMER of that pipeline, not a second one.
# (Until the 2026-08-17 "Million Dollar Bug Hunt" merge this file carried a
# parallel ~470-line blocking engine that drifted from streaming for a year.)
import logging
import re
from typing import Dict, Any, Optional, List

import config
from .history import ConversationHistory, ChatSessionManager, count_tokens
from .function_manager import FunctionManager
from core.hooks import hook_runner, HookEvent
from .chat_streaming import StreamingChat
from .chat_tool_calling import ToolCallingEngine
from .llm_providers import get_provider, get_provider_for_url, get_provider_by_key, get_first_available_provider

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


def fallback_error_text(e):
    """The doors' never-raise contract: map ANY exception to a spoken/shown
    string. friendly_llm_error first; the legacy blocking-lane heuristics
    (timeout/swarm/connection/json) as the catch-all tail."""
    friendly = friendly_llm_error(e)
    if friendly:
        return friendly
    if "timeout" in str(e).lower() or "APITimeoutError" in str(type(e).__name__):
        return "I ran into a timeout while processing your request. Please try breaking it into smaller parts."
    if "swarm" in str(e).lower() or (hasattr(e, '__module__') and 'httpx' in str(e.__module__)):
        return f"Local swarm server connection failed. Error: {str(e)}"
    if "connection" in str(e).lower() or "ConnectError" in str(type(e).__name__):
        return "I lost connection to my processing engine. Please check if services are running."
    if "json" in str(e).lower() or "JSON" in str(e):
        return "I encountered a data formatting issue while processing your request."
    return f"I encountered an unexpected technical issue. Error: {str(e)[:200]}"


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
        
        logger.info(f"Using LLM_PROVIDERS config with {len(config.LLM_PROVIDERS)} providers")

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
        prompt = prompt_template

        # Build context parts from chat settings
        context_parts = []
        chat_settings = self.session_manager.get_chat_settings()

        # Vault warmth: a turn actually WEARING a vault prompt is vault
        # activity — idle-lock is for walking away, not for chatting.
        # Listing/browsing reads of the merged views deliberately never
        # touch (an open Prompts page must not keep the vault warm).
        try:
            from core import prompt_vault, prompt_state
            if prompt_vault.vault_has_prompt(chat_settings.get('prompt')) \
                    or prompt_vault.vault_has_prompt(
                        prompt_state.get_active_preset_name()):
                prompt_vault.touch()
        except Exception:
            pass

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
            # surface stamp: presence plugins (avatar…) only inject where
            # they're actually shown — chat setting `surface`, default chat
            inject_event = HookEvent(context_parts=context_parts, config=config,
                                     surface=chat_settings.get("surface") or "chat")
            hook_runner.fire("prompt_inject", inject_event)

        # Combine all static context into main prompt
        if context_parts:
            prompt = f"{prompt}\n\n{chr(10).join(context_parts)}"

        # Template replacement runs AFTER the appends so {user_name} in
        # custom_context, spice, or plugin prompt_inject text renders too —
        # replacing first left those reaching the model as literal braces.
        prompt = prompt.replace("{user_name}", username).replace("{ai_name}", ai_name)

        return prompt, username, None

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
            # Settings gate: same pair ExecutionContext._resolve_tools applies.
            # Without it, stream-brain streams (phone calls, driver/daemon
            # targets) saw gated tools (switch_model et al) with their
            # Settings > Tools toggles OFF. Silent-default class. 2026-08-17.
            if hasattr(fm, "_apply_settings_gate"):
                tools = fm._apply_settings_gate(tools)
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

    def chat(self, user_input: str, on_event=None):
        """Blocking consumer of THE turn engine (chat_streaming.chat_stream).

        Runs the generator to completion, keeps the final text, discards the
        play-by-play — "blocking" is a consumer property, not a second
        pipeline. Callers: wake voice, POST /api/chat, body (all via
        process_llm_query). TTS stays with the caller (it speaks the
        returned blob); suppress_tts keeps the engine's streaming pump
        inert so nothing double-speaks.

        on_event: optional callback fired with EVERY engine event before the
        consumer processes it — lets a door observe the live turn (the wake
        door publishes VOICE_TURN_CHUNK to the web UI from it) without
        growing a second consumer. Exceptions in the callback are logged and
        never kill the turn.

        Million Dollar Bug Hunt merge, 2026-08-17: this replaced a second
        ~470-line blocking pipeline that had drifted from the streaming
        engine for a year (tool-cycle rows dropped thinking/metadata,
        per-iteration tokens under-reported, no cancel path, no tool
        events). THE TURN PIPELINE EXISTS EXACTLY ONCE. A future
        non-streaming provider fakes chat_completion_stream in the provider
        layer (~15 lines) — it never gets a second pipeline here.
        """
        stream, sid, chat_name = self.begin_stream(None)
        stream.suppress_tts = True   # the caller voices the blob; pump stays inert
        final_text = None
        fallback_parts = []
        try:
            for event in stream.chat_stream(user_input):
                if on_event is not None:
                    try:
                        on_event(event)
                    except Exception as cb_err:
                        logger.warning(f"chat on_event callback failed: {cb_err}")
                        on_event = None   # broken observer: stop calling, keep the turn
                if not isinstance(event, dict):
                    fallback_parts.append(str(event))
                    continue
                et = event.get("type")
                if et == "final":
                    final_text = event.get("text", "")
                elif et == "content":
                    fallback_parts.append(event.get("text", ""))
                elif et == "notice":
                    # REST door reads pending_notices as toasts; the voice
                    # door drains them in process_llm_query's finally.
                    self.pending_notices.append({
                        "message": event.get("message", ""),
                        "severity": event.get("severity", "warning"),
                    })
                # tool/tts/iteration events: play-by-play, nothing to keep
        except Exception as e:
            # The doors never see a raise. The engine already saved the
            # error row before raising (chat_stream's outer handlers), so
            # no history write here — one more would double-log the turn.
            logger.error(f"Chat error: {e}", exc_info=True)
            return fallback_error_text(e)
        finally:
            self.end_stream(sid, chat_name)

        if final_text is not None:
            return final_text
        # Defensive: an exit path missed its final event. Reconstruct from
        # the content stream — which wraps thinking in <think> tags for UI
        # rendering; strip them so voice doesn't read reasoning aloud.
        text = "".join(fallback_parts)
        if "<think" in text.lower():
            text = re.sub(r"<think>.*?(?:</think>|$)", "", text,
                          flags=re.DOTALL | re.IGNORECASE).strip()
        return text

    def _select_provider(self):
        """Select LLM provider using per-chat settings or fallback order. Returns (provider_key, provider, model_override) tuple or raises."""
        
        providers_config = {**config.LLM_PROVIDERS, **getattr(config, 'LLM_CUSTOM_PROVIDERS', {})}
        fallback_order = getattr(config, 'LLM_FALLBACK_ORDER', list(providers_config.keys()))
        
        # Check per-chat LLM settings
        chat_settings = self.session_manager.get_chat_settings()
        chat_primary = chat_settings.get('llm_primary', 'auto')
        chat_model = chat_settings.get('llm_model', '')  # Per-chat model override

        # Prompt-privacy gate — server-side, ALL doors. A prompt flagged
        # privacy_required refuses to run unless this chat has private_chat
        # on (which then forces a local provider below). Lives here because
        # every turn path converges on provider selection: web streaming,
        # /api/chat, voice/wakeword, phone target-chats. The old pre-flight
        # in chat_stream() covered only the web door and read the GLOBAL
        # active prompt (wrong chat for phone streams). get_chat_settings
        # is stream-override aware, so this reads the TARGET chat.
        try:
            from core import prompts as _prompts
            _pname = chat_settings.get('prompt')
            if _pname:
                _pdata = _prompts.get_prompt(_pname)
                # Unresolvable name → the runtime falls back to the
                # assembled default (never privacy_required), so the
                # gate follows suit. Falling back to the GLOBAL active
                # prompt here re-created the wrong-chat read this gate
                # exists to prevent (false-blocked calls when the UI
                # chat wore a private prompt).
                _priv_required = bool(isinstance(_pdata, dict)
                                      and _pdata.get('privacy_required', False))
            else:
                # No prompt setting at all → this chat runs whatever is
                # globally active, so the global flag IS the right one.
                _priv_required = _prompts.is_current_prompt_private()
            if _priv_required and not chat_settings.get('private_chat', False):
                raise ConnectionError(
                    "This prompt is marked private — unlock the vault and "
                    "send a message (talking marks the chat private), or "
                    "use Chat Manager's \U0001F5DD on it first.")
        except ConnectionError:
            raise
        except Exception as e:
            logger.error(f"Prompt-privacy check failed (defaulting to BLOCK): {e}")
            raise ConnectionError("Prompt-privacy check encountered an error — blocking for safety. Check logs.")

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

