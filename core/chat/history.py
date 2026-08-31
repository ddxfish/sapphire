# history.py - Chat history with SQLite storage for atomic writes
import logging
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional, Any, Union
from pathlib import Path
import tiktoken
import config
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


def _vault_ref_sync(new_name, old_name):
    """Prompt-reference bookkeeping for the vault references index (chat
    settings are one of the three referrer classes). Best-effort — never
    lets index bookkeeping break a settings write."""
    try:
        from core.prompt_crud import vault_ref_sync
        vault_ref_sync(new_name, old_name)
    except Exception:
        pass


def _vault_sealed() -> bool:
    """Vaulted chats Phase 1: True while a prompt vault EXISTS and is LOCKED —
    the window where private chats are invisible everywhere. No vault at all
    keeps private_chat's pre-vault meaning (local-only, always visible).
    Unreadable vault state seals (fail closed) — never leak on a glitch."""
    try:
        from core import prompt_vault
        s = prompt_vault.vault_status()
        return bool(s.get('exists')) and not bool(s.get('unlocked'))
    except Exception as e:
        logger.warning(f"vault state unreadable — sealing private chats (fail closed): {e}")
        return True

# Static (non-scope) system defaults for chat settings.
# Scope defaults are merged in dynamically by get_system_defaults() from SCOPE_REGISTRY.
# Primary source is user/settings/chat_defaults.json or factory chat_defaults.json
_STATIC_SYSTEM_DEFAULTS = {
    "prompt": "sapphire",
    "toolset": "all",
    "voice": "af_heart",
    "pitch": 0.98,
    "speed": 1.3,
    "spice_enabled": True,
    "spice_turns": 3,
    "spice_set": "default",
    "inject_datetime": False,
    "custom_context": "",
    "ghost_context": "",        # operator per-turn ghost-message override (sidebar)
    "llm_primary": "auto",      # "auto", "none", or provider key like "claude"
    "llm_model": "",            # Empty = use provider default, or specific model override
    "trim_color": "",
    "persona": None
}


def get_system_defaults() -> dict:
    """Return the current system defaults dict, including dynamic scope defaults
    from SCOPE_REGISTRY. This is the source of truth for chat setting defaults.

    Function (not constant) because plugins can register new scopes at any time —
    a snapshot at module-import would miss them. Safe to call repeatedly.
    """
    from core.chat.function_manager import scope_defaults_dict
    defaults = dict(_STATIC_SYSTEM_DEFAULTS)
    # Merge scope defaults; static keys win if there's a collision
    for setting_key, default_val in scope_defaults_dict().items():
        if setting_key not in defaults:
            defaults[setting_key] = default_val
    return defaults


def __getattr__(name):
    """Module-level backcompat shim for `from core.chat.history import SYSTEM_DEFAULTS`.

    External read-only callers still work transparently — they get the current dict
    (with dynamic scope keys). Internal callers in this module use get_system_defaults()
    directly. Tests that PATCH `SYSTEM_DEFAULTS` must migrate to patching
    `get_system_defaults` (returned by this shim is a fresh dict, not mutable state).
    """
    if name == 'SYSTEM_DEFAULTS':
        return get_system_defaults()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def sanitize_chat_name(name: str) -> str:
    """The chat-name normal form. Single source: create_chat AND the route
    that echoes the created name must agree, or frontends re-derive it
    (each slightly differently) and target a chat that doesn't exist."""
    safe = "".join(c for c in (name or '') if c.isalnum() or c in (' ', '-', '_')).strip()
    return safe.replace(' ', '_').lower()


def get_user_defaults() -> Dict[str, Any]:
    """
    Get user's custom chat defaults, falling back to system defaults.
    Priority: get_system_defaults() < chat_defaults.json < DEFAULT_PERSONA
    """
    merged = get_system_defaults()

    # User chat_defaults.json as base layer (if it exists)
    user_defaults_path = Path(__file__).parent.parent.parent / "user" / "settings" / "chat_defaults.json"
    if user_defaults_path.exists():
        try:
            with open(user_defaults_path, 'r', encoding='utf-8') as f:
                user_defaults = json.load(f)
            merged.update(user_defaults)
            logger.debug(f"Applied user chat defaults from {user_defaults_path}")
        except Exception as e:
            logger.error(f"Failed to load user chat defaults: {e}")

    # Default persona overrides on top (most specific wins)
    default_persona = getattr(config, 'DEFAULT_PERSONA', '') or ''
    if default_persona:
        try:
            from core.personas import persona_manager
            persona = persona_manager.get(default_persona)
            if persona and persona.get('settings'):
                merged.update(persona['settings'])
                merged['persona'] = default_persona
                logger.debug(f"Using default persona '{default_persona}' for new chat")
        except Exception as e:
            logger.warning(f"Failed to load default persona '{default_persona}': {e}")

    # Ensure voice matches active TTS provider
    from core.tts.utils import validate_voice
    voice = merged.get('voice', '')
    if voice:
        merged['voice'] = validate_voice(voice)

    return merged

_tokenizer = None

def get_tokenizer():
    """Lazy load tokenizer once."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
    return _tokenizer

def count_tokens(text: str) -> int:
    """Token count for budgeting. Soft-fails to an estimate rather than raising —
    counting is telemetry, never worth crashing a chat turn over."""
    if not text:
        return 0
    try:
        # disallowed_special=() -> count special-token strings like '<|endoftext|>'
        # as normal text instead of raising. We only use the length, never the IDs.
        return len(get_tokenizer().encode(text, disallowed_special=()))
    except Exception as e:
        logger.warning(f"count_tokens fell back to estimate: {type(e).__name__}: {e}")
        return max(1, len(str(text)) // 3)


def count_message_tokens(content, include_images: bool = False) -> int:
    """
    Count tokens in message content, handling multimodal content correctly.
    
    Args:
        content: String or list (multimodal content with text and images)
        include_images: If True, estimate tokens for images (~1 token per 750 pixels).
                       If False, images are ignored (matching LLM history behavior).
    
    Returns:
        Token count for the content
    """
    if not content:
        return 0
    
    # Simple string content
    if isinstance(content, str):
        return count_tokens(content)
    
    # Multimodal content (list of blocks)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get('type') == 'text':
                    total += count_tokens(block.get('text', ''))
                elif block.get('type') == 'file':
                    total += count_tokens(block.get('text', ''))
                elif block.get('type') == 'image' and include_images:
                    # Estimate image tokens: Claude uses ~1 token per 750 pixels
                    # A 1920x1080 image ≈ 2700 tokens, 1024x1024 ≈ 1400 tokens
                    # Without dimensions, estimate conservatively at 1500 tokens
                    total += 1500
            elif isinstance(block, str):
                total += count_tokens(block)
        return total
    
    # Fallback: stringify and count
    return count_tokens(str(content))


def turn_spans(messages: List[Dict[str, Any]]) -> List[tuple]:
    """Split a message list into turn spans: (start, end) index pairs.

    A turn starts at each user message and runs until the next one, so
    tool_call/tool-result chains never split across a span (providers 400
    on orphaned tool messages). Any leading non-user prefix (assistant
    greeting) glues to the first turn. No user messages at all → one span
    covering everything.
    """
    if not messages:
        return []
    starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not starts:
        return [(0, len(messages))]
    starts[0] = 0
    return [(s, starts[k + 1] if k + 1 < len(starts) else len(messages))
            for k, s in enumerate(starts)]


def _extract_thinking_from_content(content: str) -> tuple:
    """
    Extract thinking from content that uses <think> tags.
    Used for backward compatibility with old messages and non-Claude providers.
    
    Returns:
        (clean_content, thinking_text) - thinking_text is empty if none found
    """
    if not content:
        return content, ""
    
    thinking_parts = []
    
    # Extract all think blocks (standard and seed variants)
    pattern = r'<(?:seed:)?think[^>]*>(.*?)</(?:seed:think|seed:cot_budget_reflect|think)>'
    
    def extract_match(match):
        thinking_parts.append(match.group(1))
        return ''
    
    clean = re.sub(pattern, extract_match, content, flags=re.DOTALL | re.IGNORECASE)
    
    # Handle orphan close tags - content before them is thinking
    orphan_close = re.search(
        r'^(.*?)</(?:seed:think|seed:cot_budget_reflect|think)>',
        clean, flags=re.DOTALL | re.IGNORECASE
    )
    if orphan_close:
        thinking_parts.append(orphan_close.group(1))
        clean = clean[orphan_close.end():]
    
    # Handle orphan open tags - content after them is thinking
    orphan_open = re.search(
        r'<(?:seed:)?think[^>]*>(.*)$',
        clean, flags=re.DOTALL | re.IGNORECASE
    )
    if orphan_open:
        thinking_parts.append(orphan_open.group(1))
        clean = clean[:orphan_open.start()]
    
    clean = clean.strip()
    thinking = "\n\n".join(thinking_parts).strip()
    
    return clean, thinking


def _reconstruct_thinking_content(content: str, thinking: str) -> str:
    """
    Reconstruct content with <think> tags for UI display.
    """
    if not thinking:
        return content or ""
    
    think_block = f"<think>{thinking}</think>"
    if content:
        return f"{think_block}\n\n{content}"
    return think_block


class ConversationHistory:
    # Whether THIS chat is mid-tool-cycle (Claude thinking_raw handling). Lives on
    # the history object so concurrent streams — a phone call and the web UI each
    # have their own ConversationHistory — can't clobber each other's cycle state.
    # Class-attr default so every instance reads False before its first turn.
    _in_tool_cycle = False

    def __init__(self, max_history: int = 30):
        self.max_history = max_history
        self._messages = []
        # Rowify: True means the persisted rows may not match this list below
        # the watermark — the next save must full-resync instead of appending.
        # Set STRUCTURALLY by the `messages` setter (catches every raw
        # assignment: import, load, slice-removals) and explicitly by the
        # in-place mutators (remove_tool_call, edit_message_by_content).
        # Append-only growth (the add_* hot path) never sets it.
        self._needs_full_resync = False

    @property
    def messages(self):
        return self._messages

    @messages.setter
    def messages(self, value):
        self._messages = value
        self._needs_full_resync = True

    def add_user_message(self, content: Union[str, List[Dict[str, Any]]], persona: Optional[str] = None):
        """Add user message - accepts string or content list with images."""
        msg = {
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if persona:
            msg["persona"] = persona
        self.messages.append(msg)

    def add_assistant_with_tool_calls(
        self,
        content: Optional[str],
        tool_calls: List[Dict],
        thinking: Optional[str] = None,
        thinking_raw: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None,
        persona: Optional[str] = None
    ):
        """
        Add assistant message that includes tool calls.

        Args:
            content: The visible response content (no thinking tags)
            tool_calls: List of tool call dicts
            thinking: Extracted thinking text (for UI display)
            thinking_raw: Original structured thinking blocks (for Claude continuity)
            metadata: Provider info, timing, tokens
            persona: Active persona name at time of generation
        """
        msg = {
            "role": "assistant",
            "content": content or "",
            "tool_calls": tool_calls,
            "timestamp": datetime.now().isoformat()
        }

        if thinking:
            msg["thinking"] = thinking
        if thinking_raw:
            msg["thinking_raw"] = thinking_raw
        if metadata:
            msg["metadata"] = metadata
        if persona:
            msg["persona"] = persona

        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, name: str, content: str, inputs: Optional[Dict] = None):
        """Add tool result message with optional inputs - NO TRIMMING."""
        msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if inputs:
            msg["tool_inputs"] = inputs
        self.messages.append(msg)

    def add_assistant_final(
        self,
        content: str,
        thinking: Optional[str] = None,
        metadata: Optional[Dict] = None,
        persona: Optional[str] = None
    ):
        """
        Add final assistant message (no tool calls).

        Args:
            content: The visible response content (no thinking tags)
            thinking: Extracted thinking text (for UI display)
            metadata: Provider info, timing, tokens
            persona: Active persona name at time of generation
        """
        msg = {
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat()
        }

        if thinking:
            msg["thinking"] = thinking
        if metadata:
            msg["metadata"] = metadata
        if persona:
            msg["persona"] = persona

        self.messages.append(msg)

    def add_message_pair(self, user_content: str, assistant_content: str):
        """Legacy method for adding simple user/assistant pairs - NO TRIMMING."""
        timestamp = datetime.now().isoformat()
        self.messages.append({"role": "user", "content": user_content, "timestamp": timestamp})
        self.messages.append({"role": "assistant", "content": assistant_content, "timestamp": timestamp})

    def get_messages(self) -> List[Dict[str, str]]:
        """Get ALL messages (with timestamps for storage) - NO TRIMMING."""
        return self.messages.copy()

    def get_messages_for_display(self) -> List[Dict[str, Any]]:
        """
        Get messages formatted for UI display.
        Reconstructs <think> tags from separate thinking field for rendering.
        """
        display_msgs = []
        
        for msg in self.messages:
            display_msg = msg.copy()
            
            if msg["role"] == "assistant":
                content = msg.get("content", "")
                thinking = msg.get("thinking", "")
                
                # If we have separate thinking, reconstruct with tags for UI
                if thinking:
                    display_msg["content"] = _reconstruct_thinking_content(content, thinking)
                # Backward compat: if content has <think> tags but no thinking field, leave as-is
                # (old messages before this schema change)
            
            display_msgs.append(display_msg)
        
        return display_msgs

    def get_messages_for_llm(
        self,
        reserved_tokens: int = 0,
        provider: str = None,
        in_tool_cycle: bool = False,
        context_limit: int = None
    ) -> List[Dict[str, Any]]:
        """
        Get messages formatted for LLM with TRIMMING applied.

        Args:
            reserved_tokens: Tokens to reserve for system prompt + current user message.
            provider: Target provider ('claude', 'lmstudio', etc) for format decisions.
            in_tool_cycle: True if we're mid-tool-cycle and need thinking_raw for Claude.
            context_limit: Per-call override for the token trim budget. None =
                the global CONTEXT_LIMIT setting. Continuity tasks that carry
                their own limit (the librarian's night sessions) pass it here —
                before this, the global setting silently capped them.
        
        Notes:
            - Thinking is NEVER sent to LLMs (they don't need previous reasoning)
            - Exception: Claude needs thinking_raw during active tool cycles
            - Set LLM_MAX_HISTORY to 0 to disable turn-based trimming
            - Set CONTEXT_LIMIT to 0 to disable token-based trimming
        """
        msgs = []
        
        for msg in self.messages:
            role = msg["role"]
            
            if role == "assistant":
                # Get clean content (no thinking)
                content = msg.get("content", "")
                
                # Handle content stored as list (shouldn't happen but be safe)
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text_parts.append(block.get('text', ''))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = ' '.join(text_parts).strip()
                
                # Backward compat: extract thinking from old messages with embedded tags
                if not msg.get("thinking") and content and '<think' in content.lower():
                    content, _ = _extract_thinking_from_content(content)
                
                llm_msg = {"role": "assistant", "content": content}
                
                # Include tool_calls if present
                if msg.get("tool_calls"):
                    llm_msg["tool_calls"] = msg["tool_calls"]

                    # Claude needs thinking_raw during tool cycles (has signatures)
                    if provider == "claude" and in_tool_cycle and msg.get("thinking_raw"):
                        llm_msg["thinking_raw"] = msg["thinking_raw"]

                    # Carry `thinking` through on tool-calling assistant turns so
                    # providers that require reasoning round-trip can pull from it
                    # via their sanitizer (DeepSeek-reasoner official enforces this
                    # — 400 without it on request 2+ of a tool cycle). Providers
                    # that don't need it ignore the field. 2026-05-11.
                    if msg.get("thinking"):
                        llm_msg["thinking"] = msg["thinking"]
                
            elif role == "tool":
                # Tolerate tool messages missing `name` — historically
                # placeholder writes from execution_context._patch_dangling_tool_calls
                # (and any future hand-edited / partial history) lacked the
                # field. A bare msg["name"] KeyError used to swallow into the
                # outer except and return [] — silently empty history poisoned
                # every subsequent heartbeat read of that chat. 2026-05-10.
                llm_msg = {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "name": msg.get("name", "tool"),
                    "content": msg.get("content", "")
                }
                
            elif role == "user":
                content = msg.get("content", "")
                # Handle content stored as list (multimodal: text + files + images)
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                text_parts.append(block.get('text', ''))
                            elif block.get('type') == 'file':
                                # Flatten file to fenced code block
                                from core.chat.chat import _ext_to_lang
                                lang = _ext_to_lang(block.get('filename', ''))
                                text_parts.append(f"```{lang}\n# {block['filename']}\n{block.get('text', '')}\n```")
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = '\n\n'.join(text_parts).strip()
                llm_msg = {"role": "user", "content": content}
                
            else:
                # System or other - pass through
                llm_msg = {"role": role, "content": msg.get("content", "")}

            # <<TYPE::data>> markers (tool/event images, files) are UI-only.
            # The live tool cycle strips them from its wire copy, but history
            # replay didn't — every later turn re-sent them to the LLM as
            # literal text. Same pattern as strip_ui_markers. 2026-08-09.
            c = llm_msg.get("content")
            if isinstance(c, str) and '<<' in c:
                llm_msg["content"] = re.sub(r'<<[A-Z]+::[^>]+>>\s*', '', c).strip()

            msgs.append(llm_msg)
        
        # TRIMMING STEP 1: Turn-based trimming (skip if max_history is 0)
        max_history = getattr(config, 'LLM_MAX_HISTORY', 30)
        if max_history > 0 and len(msgs) > max_history:
            user_count = sum(1 for m in msgs if m["role"] == "user")
            max_pairs = max_history // 2
            
            if user_count > max_pairs:
                user_turns_to_remove = user_count - max_pairs
                removed_users = 0
                
                while removed_users < user_turns_to_remove and len(msgs) > 0:
                    if msgs[0]["role"] == "user":
                        removed_users += 1
                    msgs.pop(0)
        
        # TRIMMING STEP 2: Token-based trimming (skip if context_limit is 0)
        if context_limit is None:
            context_limit = getattr(config, 'CONTEXT_LIMIT', 32000)

        if context_limit > 0:
            safety_buffer = int(context_limit * 0.01) + 512
            effective_limit = context_limit - safety_buffer - reserved_tokens

            # Use the multimodal-aware helper. Pre-fix this used
            # `count_tokens(str(m.get("content", "")))` which stringifies a
            # multimodal list to include the base64 image data — a 5MB JPEG
            # was scoring ~1.7M tokens and tripping the trim to nuke ALL
            # history the moment an image landed in the chat. The user-
            # visible symptom was "I sent an image and Sapphire suddenly
            # forgot the last 30 turns." `count_message_tokens` (defined
            # above at L122-160) handles dict-typed multimodal blocks
            # correctly and excludes images by default. Wildcard scout
            # 2026-05-07 multimodal #1.
            total_tokens = sum(
                count_message_tokens(m.get("content", ""), include_images=False)
                for m in msgs
            )

            while total_tokens > effective_limit and len(msgs) > 1:
                removed = msgs.pop(0)
                total_tokens -= count_message_tokens(
                    removed.get("content", ""), include_images=False
                )

        # Clean up orphaned tool results at the front.
        # Trimming can remove an assistant message with tool_calls while leaving
        # its tool_result messages behind — LLM APIs reject these.
        while len(msgs) > 1 and msgs[0].get("role") in ("tool",):
            removed = msgs.pop(0)
            if context_limit > 0:
                total_tokens -= count_message_tokens(
                    removed.get("content", ""), include_images=False
                )

        # Clean up orphaned tool_use blocks.
        # If server shuts down mid-tool-call, an assistant message with tool_calls
        # gets saved but the matching tool_result never arrives. Claude (and others)
        # reject the conversation. Strip tool_calls from any assistant message
        # whose tool IDs don't have matching tool results immediately after.
        for i, msg in enumerate(msgs):
            if msg.get("role") != "assistant" or not msg.get("tool_calls"):
                continue
            # Collect tool_result IDs in the messages immediately following
            result_ids = set()
            for j in range(i + 1, len(msgs)):
                if msgs[j].get("role") == "tool":
                    result_ids.add(msgs[j].get("tool_call_id", ""))
                else:
                    break
            # Check if every tool_call has a matching result
            call_ids = {tc.get("id", "") for tc in msg["tool_calls"]}
            if not call_ids.issubset(result_ids):
                # Orphaned — strip tool_calls so it's just a text message
                del msg["tool_calls"]
                msg.pop("thinking_raw", None)

        # Drop assistant messages that ended up with no tool_calls AND no
        # content. Without this, the orphan strip above produces an empty-
        # content assistant; Claude/Anthropic providers (claude.py:715,
        # anthropic_compat.py:145/164) DROP empty assistants from the API
        # payload via `if content and content.strip()`. The result is two
        # adjacent user messages — alternation violation → API 400 → every
        # subsequent send fails with the same 400 → user has to delete the
        # chat to recover. Filtering the empty assistant here keeps the
        # alternation valid. Voice mode amplifies (more crash windows mid-
        # tool-call). Wildcard scout 2026-05-07 chat-wedge.
        msgs = [
            m for m in msgs
            if not (
                m.get("role") == "assistant"
                and not m.get("tool_calls")
                and not str(m.get("content", "")).strip()
            )
        ]

        return msgs

    def clear_thinking_raw(self):
        """
        Clear thinking_raw from all messages.
        Called after tool cycle completes - we don't need raw blocks anymore.

        Deliberately does NOT set _needs_full_resync: thinking_raw is never
        persisted to rows (stripped in _row_json), so clearing it in memory
        creates no drift vs the rows store. Flagging here would force an
        O(N) resync EVERY tool turn — the exact O(N^2) rowify exists to kill.
        """
        for msg in self.messages:
            if "thinking_raw" in msg:
                del msg["thinking_raw"]

    def get_turn_count(self) -> int:
        """Count user messages (turns) in full storage."""
        return sum(1 for msg in self.messages if msg["role"] == "user")

    def remove_last_messages(self, count: int) -> bool:
        """Remove last N messages from storage (for user actions like delete/regen)."""
        if count <= 0 or count > len(self.messages):
            return False
        self.messages = self.messages[:-count]
        return True

    def remove_from_user_message(self, user_content: str) -> bool:
        """Remove all messages starting from a specific user message to the end.

        Matches on the message's TEXT content. For multimodal messages (image
        paste, file attachments), `content` is a list-of-parts shape rather
        than a string — direct equality with user_content always fails. We
        extract the text portion before comparing so deletion/resend works
        regardless of attachment shape. 2026-04-25 user report: pasting an
        image and trying to delete the message produced a 404 because the
        match couldn't find a list ≠ string.
        """
        if not user_content:
            return False

        def _text_of(content):
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            return ""

        user_index = -1
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            if msg["role"] == "user" and _text_of(msg.get("content")) == user_content:
                user_index = i
                break

        if user_index == -1:
            # Never the text itself — logs are plaintext at rest (ruling F3).
            logger.warning(f"User message not found for deletion ({len(user_content)} chars)")
            return False

        messages_to_delete = len(self.messages) - user_index
        self.messages = self.messages[:user_index]
        logger.info(f"Deleted {messages_to_delete} messages from user message at index {user_index}")
        return True

    def remove_from_assistant_timestamp(self, timestamp: str) -> bool:
        """Remove all messages starting from a specific assistant message (by timestamp) to the end."""
        if not timestamp:
            return False
        
        assistant_index = -1
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "assistant" and msg.get("timestamp") == timestamp:
                assistant_index = i
                break
        
        if assistant_index == -1:
            logger.warning(f"Assistant message not found for timestamp: {timestamp}")
            return False
        
        messages_to_delete = len(self.messages) - assistant_index
        self.messages = self.messages[:assistant_index]
        logger.info(f"Deleted {messages_to_delete} messages from assistant at index {assistant_index}")
        return True

    def remove_tool_call(self, tool_call_id: str) -> bool:
        """
        Remove a specific tool call and its result from history.
        
        Finds the assistant message containing the tool_call_id, removes that call.
        If no calls remain in the assistant message, removes the whole message.
        Also removes the corresponding tool result message.
        """
        if not tool_call_id:
            return False
        
        # Find and remove the tool result message
        tool_result_idx = -1
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
                tool_result_idx = i
                break
        
        if tool_result_idx != -1:
            self.messages.pop(tool_result_idx)
            self._needs_full_resync = True  # in-place pop bypasses the setter
            logger.info(f"Removed tool result for {tool_call_id}")
        
        # Find assistant message with this tool call
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                for j, tc in enumerate(tool_calls):
                    if tc.get("id") == tool_call_id:
                        # Found it - remove this specific call
                        tool_calls.pop(j)
                        self._needs_full_resync = True  # in-place mutation
                        logger.info(f"Removed tool call {tool_call_id} from assistant message")
                        
                        # If no tool calls remain and no content, remove the whole message
                        if not tool_calls and not msg.get("content", "").strip():
                            self.messages.pop(i)
                            logger.info(f"Removed empty assistant message at index {i}")
                        
                        return True
        
        logger.warning(f"Tool call not found: {tool_call_id}")
        return tool_result_idx != -1  # Return True if at least the result was removed

    def clear(self):
        """Clear all messages from storage."""
        self.messages = []

    def __len__(self):
        return len(self.messages)

    def edit_message_by_content(self, role: str, original_content: str, new_content: str) -> bool:
        """Edit a message by matching content."""
        for msg in self.messages:
            if msg.get("role") == role and msg.get("content") == original_content:
                msg["content"] = new_content
                self._needs_full_resync = True  # in-place edit bypasses the setter
                return True
        return False
    

# ── DB watchdog (2026-08-21, the frozen-close incident): a live conn.close()
# hung in C for 5½ minutes and — because SQLite's unix VFS takes one
# process-global mutex during open AND close — every later connect() queued
# behind it, freezing all plugin/chat data with zero diagnostics until a
# manual SIGABRT. The watchdog registers every _get_connection context; an op
# stuck past the threshold gets ALL thread stacks dumped to stderr (the
# journal) once, plus a heartbeat line each further minute. Diagnostic only —
# it never kills anything. No legitimate op here should approach 30s; a rare
# false positive (huge vault re-encrypt) costs one loud log block.
_DB_WATCHDOG_SECS = 30.0
_DB_WATCHDOG_POLL = 5.0
_db_ops = {}                     # token -> {started, thread, dumped, beat}
_db_ops_lock = threading.Lock()
_db_watchdog_started = False


def _db_watchdog_scan():
    """One scan pass — separated from the loop so tests can drive it
    deterministically. Returns how many stuck ops it reported on."""
    now = time.monotonic()
    dump_needed = False
    reported = 0
    msgs = []
    # Collect under the lock, LOG outside it: logging handlers can block
    # (disk, a wedged stderr pipe), and a watchdog that stalls while holding
    # its own registry lock would freeze every _get_connection at register
    # time — the exact failure class it exists to diagnose (2026-08-21 hunt).
    with _db_ops_lock:
        for op in _db_ops.values():
            age = now - op['started']
            if age <= _DB_WATCHDOG_SECS:
                continue
            reported += 1
            if not op['dumped']:
                op['dumped'] = True
                dump_needed = True
                msgs.append(
                    f"[DB-WATCHDOG] db op stuck {int(age)}s on thread "
                    f"'{op['thread']}' — dumping all thread stacks to stderr")
            elif int(age) // 60 > op['beat']:
                op['beat'] = int(age) // 60
                msgs.append(
                    f"[DB-WATCHDOG] still stuck: {int(age)}s on thread "
                    f"'{op['thread']}'")
    for m in msgs:
        logger.error(m)
    if dump_needed:
        try:
            import faulthandler
            faulthandler.dump_traceback(all_threads=True)
        except Exception:
            pass
    return reported


def _db_watchdog_loop():
    while True:
        time.sleep(_DB_WATCHDOG_POLL)
        try:
            _db_watchdog_scan()
        except Exception:
            pass                 # the watchdog must never die of its own bug


def _db_watchdog_ensure():
    global _db_watchdog_started
    with _db_ops_lock:
        if _db_watchdog_started:
            return
        _db_watchdog_started = True
    threading.Thread(target=_db_watchdog_loop, name="db-watchdog",
                     daemon=True).start()


class ChatSessionManager:
    """
    Manages chat sessions with SQLite storage for atomic writes.
    
    Storage: user/history/sapphire_history.db (WAL mode)
    Schema: chats(name TEXT PRIMARY KEY, settings JSON, messages JSON, updated_at TEXT)
    
    Features:
    - Atomic writes via SQLite transactions
    - Auto-recovery if DB deleted while running
    - One-time migration from legacy JSON files
    """
    
    def __init__(self, max_history: int = 30, history_dir: str = "user/history"):
        self.max_history = max_history
        self.history_dir = Path(history_dir)
        if not self.history_dir.is_absolute():
            # Anchor to the project root, never CWD: a Windows shortcut with
            # a wrong "Start in" (or a service unit without WorkingDirectory)
            # would otherwise create a fresh empty history DB wherever the
            # shell happened to be — "all my chats are gone".
            self.history_dir = Path(__file__).absolute().parent.parent.parent / self.history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        self._db_path = self.history_dir / "sapphire_history.db"
        self._lock = threading.RLock()
        # SWITCH MEANS APPLY (2026-08-22): runtime-apply hook, installed by
        # the system AFTER the plugin scan (sapphire.py) — None until then
        # and in every bare-store test. See set_active_chat.
        self.on_switched = None
        self._switch_gen = 0
        
        self.current_chat = ConversationHistory(max_history=max_history)
        self.active_chat_name = "default"
        self.current_settings = get_system_defaults()
        
        # Rowify watermark state, keyed by CHAT NAME (not "the active one" —
        # per-stream overrides save non-active chats through the same path).
        # offset = seq of the first in-memory message (non-zero after a
        # capped load of a huge chat); count = how many in-memory messages
        # are already persisted as rows. Incremental save INSERTs message i
        # at seq offset+i for i in [count, len). Missing entry or a set
        # _needs_full_resync flag → full window resync.
        self._rows_state = {}
        # Read-only-degraded latch (F2 Wave 2, 2026-08-17): chats whose load
        # skipped unreadable rows — {name: {skipped, causes{decrypt, parse},
        # at}}. IN-MEMORY ONLY, never persisted: a reload/unlock re-evaluates
        # (decrypt-cause may be transient); parse-cause stands until repair.
        # Latched chats load READABLE but refuse every write path (save/
        # append/replace/vault) so the corrupt-but-recoverable rows are never
        # overwritten from a partial in-memory list. clear/delete stay
        # allowed as escape hatches (they unlatch). Pre-latch behavior was
        # WORSE both ways: a bad-JSON row bricked the whole chat (load →
        # False), and undecryptable rows vanished silently.
        self._rows_degraded = {}
        # One degraded-save toast per chat per session — the log stays loud
        # on every refusal, but a toast per turn would bury the operator.
        self._degraded_toasted = set()

        # Track if we're in an active tool cycle (for Claude thinking_raw)
        self._in_tool_cycle = False
        # Prevent chat switching during active streaming (would corrupt both chats).
        # 2026-04-22 — converted from single bool to counter. H4 made streaming
        # state per-request (each /api/chat call gets its own StreamingChat) but
        # this flag stayed a shared single-bool on session_manager — two
        # concurrent streams on the same chat had the first finisher set
        # False while the second was still running, defeating the append /
        # delete / save guards. Counter represents how many streams are
        # currently active; `_is_streaming` property reads > 0.
        self._streaming_count = 0
        # Event signals "no streams currently active." append_messages_to_chat
        # waits on this instead of polling _streaming_count every 200ms — so
        # heartbeat appends fire as soon as the stream ends, not up to 200ms
        # later. Initially set (no streams). Cleared on first begin, set when
        # the last end_streaming brings the counter back to 0. Race scout
        # 2026-05-07 #1 — replaces the 15s poll-and-fall-through path that
        # corrupted history mid-tool-call under voice cadence.
        self._no_streams_event = threading.Event()
        self._no_streams_event.set()
        
        # Initialize database
        self._init_db()

        # Vault hunt G3: hand prompt_vault ground truth for its chat-key
        # mint guard — a fresh key must never be minted while sealed rows
        # exist (older vault file restored beside a newer chat DB).
        try:
            from core import prompt_vault as _pv
            _pv.set_sealed_rows_probe(self._any_vaulted_rows)
        except Exception as e:
            logger.warning(f"sealed-rows probe registration failed: {e}")

        # Migrate any existing JSON files
        self._migrate_json_files()
        
        # Ensure default chat exists and load last active (or default)
        self._ensure_default_exists()
        last_active = self._read_last_active()
        if last_active and last_active != "default":
            if self._load_chat(last_active):
                self.active_chat_name = last_active
                logger.info(f"Restored last active chat: {last_active}")
            else:
                self._load_chat("default")
        else:
            self._load_chat("default")

        # Vaulted chats: boot always comes up sealed (the key never
        # persists) — if the restored active chat is private, evict NOW,
        # before any route can serve its name or content. Without this, a
        # restart mid-private-session resurrected the chat via the
        # active-chat exemption (live-caught 2026-08-14).
        try:
            self.evict_private_active()
        except Exception as e:
            logger.warning(f"Boot private-active eviction skipped: {e}")

        # Rowify snapshot RETIRED (vaulted chats Phase 2, Krem's ruling
        # 2026-08-15: "rowify is working great"). The one-shot pre-rowify
        # DB copy was migration safety scaffolding — and a full PLAINTEXT
        # copy of every chat, sitting outside all encryption forever. Delete
        # any existing snapshot and latch the marker so nothing recreates it.
        try:
            marker = self.history_dir / ".pre_rowify_snapshot_done"
            for snap in self.history_dir.glob("pre_rowify_*.db"):
                snap.unlink()
                logger.info(f"Deleted retired pre-rowify snapshot {snap.name}")
            if not marker.exists():
                marker.write_text("retired 2026-08-15 (vaulted chats Phase 2)")
        except Exception as e:
            logger.warning(f"Pre-rowify snapshot retirement skipped: {e}")

        logger.info(f"ChatSessionManager initialized with SQLite storage")

    # ── Streaming state (counter-backed) ──
    # Per-request StreamingChat instances each own their own cancel_flag,
    # ephemeral, current_stream — but the `am I streaming?` guard used by
    # append_messages_to_chat / delete_chat / save-ordering needs to know
    # whether ANY stream is active. That's a counter, not a bool.
    # Writers use begin_streaming() / end_streaming(). Readers use the
    # `_is_streaming` property. 2026-04-22 H4 follow-up.

    @property
    def _is_streaming(self) -> bool:
        """True if at least one stream is active."""
        return getattr(self, '_streaming_count', 0) > 0

    @_is_streaming.setter
    def _is_streaming(self, val):
        """Back-compat setter — tests and legacy code that flip this bool
        directly still work. Real writers should use begin/end_streaming()
        for atomic concurrency-safe counting.

        Keeps the no-streams event consistent with the legacy bool path so
        tests that toggle this directly don't leave waiters stuck.
        """
        self._streaming_count = 1 if val else 0
        evt = getattr(self, '_no_streams_event', None)
        if evt is not None:
            if val:
                evt.clear()
            else:
                evt.set()

    def begin_streaming(self):
        """Increment active-stream counter. Safe for concurrent streams.

        Clears the no-streams event on the 0→1 transition so any append
        waiters block until end_streaming brings the counter back to 0.
        """
        with self._lock:
            prev = getattr(self, '_streaming_count', 0)
            self._streaming_count = prev + 1
            if prev == 0:
                # Lazy-init guard for the legacy bool setter path that
                # bypasses __init__ in some test fixtures.
                evt = getattr(self, '_no_streams_event', None)
                if evt is not None:
                    evt.clear()

    def end_streaming(self):
        """Decrement active-stream counter (floored at 0). Safe for
        concurrent streams. A double-decrement (bug elsewhere) is silent
        — counter stays at 0.

        Sets the no-streams event when the counter reaches 0 so any
        append waiters can proceed immediately.
        """
        _no_streams = False
        with self._lock:
            cur = getattr(self, '_streaming_count', 0)
            self._streaming_count = cur - 1 if cur > 0 else 0
            if self._streaming_count == 0:
                _no_streams = True
                evt = getattr(self, '_no_streams_event', None)
                if evt is not None:
                    evt.set()
                # Rowify step 3: the 1→0 transition is THE conversion point
                # for the active chat (see _maybe_convert_active_chat).
                # Guarded getattr: some fixtures build this object without
                # __init__. Never raises — this runs inside request finallys.
                if getattr(self, '_rows_state', None) is not None:
                    self._maybe_convert_active_chat()
        # Vault hunt R2 (2026-08-15): a seal that landed mid-stream had its
        # eviction refused ("staying — streaming"). The 1→0 boundary is the
        # retry point. evict_private_active is idempotent (cheap early-out
        # when unsealed or active isn't private) and never raises. Outside
        # the lock: the eviction publishes a switch event — keep bus fan-out
        # out of the history lock.
        if _no_streams:
            try:
                if _vault_sealed():
                    self.evict_private_active()
            except Exception:
                pass

    @contextmanager
    def _get_connection(self):
        """Yield a database connection; close explicitly on exit.

        sqlite3.Connection.__exit__ only commits/rolls back — it does NOT
        close the conn. Prior code relied on GC to eventually close, which
        under rapid use could accumulate handles whose finalizers block
        interpreter shutdown on SQLite's WAL mutex (the root of months of
        stuck-pytest-shell reports).

        WAL + synchronous are set once in _init_db (persisted in db header).
        busy_timeout IS honored during active transactions; sqlite3.connect's
        timeout= kwarg is ignored once BEGIN fires (CPython #124510).

        Watchdog (2026-08-21, the frozen-close incident): the WHOLE context
        — connect through close, both proven hang points — is registered so
        a stuck op gets its stacks dumped instead of wedging silently.
        """
        _db_watchdog_ensure()
        token = object()
        with _db_ops_lock:
            _db_ops[token] = {'started': time.monotonic(),
                              'thread': threading.current_thread().name,
                              'dumped': False, 'beat': 0}
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=30.0)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                # synchronous is a per-connection PRAGMA — must set at every open.
                # journal_mode=WAL is persistent in the db header; no need to re-set.
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.row_factory = sqlite3.Row
                yield conn
            finally:
                conn.close()
        finally:
            with _db_ops_lock:
                _db_ops.pop(token, None)

    def _init_db(self):
        """Initialize SQLite database with schema."""
        try:
            with self._get_connection() as conn:
                # WAL + synchronous=NORMAL: massively reduces write-lock
                # contention vs the default rollback journal, lets readers
                # proceed while a writer holds the WAL. journal_mode=WAL
                # persists in the db header — set once. synchronous=NORMAL
                # is per-connection but cheap and we set it at every
                # connection open via PRAGMA below. auto_vacuum=INCREMENTAL
                # lets the file shrink after deletes (ours: chat purge,
                # tool_image prune) without the full-VACUUM pause. All
                # three are no-ops on subsequent inits. Longevity scout
                # 2026-05-07 — backup.py wal_checkpoint was a silent no-op
                # before this lands.
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chats (
                        name TEXT PRIMARY KEY,
                        settings TEXT NOT NULL,
                        messages TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        storage_format TEXT NOT NULL DEFAULT 'blob',
                        conversion_failed INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT,
                        vaulted INTEGER NOT NULL DEFAULT 0
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tool_images (
                        id TEXT PRIMARY KEY,
                        chat_name TEXT NOT NULL,
                        data BLOB NOT NULL,
                        media_type TEXT NOT NULL DEFAULT 'image/jpeg',
                        created_at TEXT NOT NULL
                    )
                """)

                # Rowify (tmp/chat-storage-rowify-plan.md): one row = one message
                # dict stored verbatim as JSON. message_json is authoritative;
                # role is a denormalized sidecar for role-only queries. The
                # composite PRIMARY KEY doubles as the (chat_name, seq) index —
                # no separate CREATE INDEX needed.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        chat_name    TEXT NOT NULL,
                        seq          INTEGER NOT NULL,
                        role         TEXT,
                        message_json TEXT NOT NULL,
                        PRIMARY KEY (chat_name, seq)
                    )
                """)

                # Chat-scoped plugin data (vault v1.3, tmp/v13-chat-scoped-
                # storage-plan.md): rows follow the chat — sealed in
                # vault_chat, strictly unsealed in unvault_chat, renamed and
                # deleted with it. Values are JSON text; '@enc1:' ciphertext
                # while the chat is vaulted. seq=0 is the put/get slot;
                # appends allocate 1.. so journal-shaped keys cost one row
                # per event, not a whole-blob rewrite per turn.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS plugin_chat_data (
                        plugin     TEXT NOT NULL,
                        chat_name  TEXT NOT NULL,
                        key        TEXT NOT NULL,
                        seq        INTEGER NOT NULL DEFAULT 0,
                        value      TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (plugin, chat_name, key, seq)
                    )
                """)
                # rename/delete/vault walk by chat_name alone; the PK leads
                # with plugin, so they need their own index.
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_plugin_chat_data_chat
                    ON plugin_chat_data(chat_name)
                """)

                # F2 Wave 4 (2026-08-17): repair quarantine — unreadable
                # message rows move here VERBATIM (ciphertext stays
                # ciphertext: if the matching vault backup ever returns,
                # the rows decrypt again; a plaintext copy would break the
                # vault's at-rest guarantee). Rides rename/delete with the
                # chat. Never read on any hot path.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages_quarantine (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_name      TEXT NOT NULL,
                        orig_seq       INTEGER NOT NULL,
                        role           TEXT,
                        message_json   TEXT NOT NULL,
                        reason         TEXT NOT NULL,
                        quarantined_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_quarantine_chat
                    ON chat_messages_quarantine(chat_name)
                """)

                # Guarded ALTERs for pre-rowify databases (the CREATE above only
                # shapes FRESH installs; ALTER isn't idempotent, hence the
                # PRAGMA check). storage_format: 'blob' | 'rows'.
                # conversion_failed: latch — a chat whose blob→rows conversion
                # hit a data-shape error stays on blob forever (transient
                # errors do NOT latch). created_at: chat-manager rider
                # (2026-07-09) — NULL for chats that predate the column.
                existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)")}
                if "storage_format" not in existing_cols:
                    conn.execute("ALTER TABLE chats ADD COLUMN storage_format TEXT NOT NULL DEFAULT 'blob'")
                if "conversion_failed" not in existing_cols:
                    conn.execute("ALTER TABLE chats ADD COLUMN conversion_failed INTEGER NOT NULL DEFAULT 0")
                if "created_at" not in existing_cols:
                    conn.execute("ALTER TABLE chats ADD COLUMN created_at TEXT")
                # vaulted (Phase 2, 2026-08-15): PLAINTEXT marker column —
                # encrypted settings can't answer json_extract, so hiding/
                # routing keys on this. 1 ⇒ rows+settings+images encrypted
                # under the vault's chat data key. private_chat=1&&vaulted=0
                # = pre-encryption legacy, swept at every unlock.
                if "vaulted" not in existing_cols:
                    conn.execute("ALTER TABLE chats ADD COLUMN vaulted INTEGER NOT NULL DEFAULT 0")

                conn.commit()
            logger.debug(f"Database initialized at {self._db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _ensure_db(self):
        """Ensure database exists - recreate if deleted while running."""
        if not self._db_path.exists():
            logger.warning("Database file missing - recreating")
            self._init_db()
            self._ensure_default_exists()

    def _migrate_json_files(self):
        """One-time migration from legacy JSON files to SQLite."""
        json_files = list(self.history_dir.glob("*.json"))
        if not json_files:
            return
        
        migrated = 0
        for json_path in json_files:
            chat_name = json_path.stem
            
            # Check if already in DB
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT 1 FROM chats WHERE name = ?", 
                        (chat_name,)
                    )
                    if cursor.fetchone():
                        # Already migrated, remove JSON file
                        json_path.unlink()
                        logger.debug(f"Removed already-migrated JSON: {chat_name}")
                        continue
            except Exception as e:
                logger.error(f"Error checking migration status for {chat_name}: {e}")
                continue
            
            # Load JSON and migrate
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Handle both formats
                if isinstance(data, dict) and "messages" in data:
                    messages = data["messages"]
                    settings = data.get("settings", get_system_defaults())
                elif isinstance(data, list):
                    messages = data
                    settings = get_system_defaults()
                else:
                    logger.warning(f"Unknown JSON format in {json_path}, skipping")
                    continue
                
                # Insert into SQLite
                with self._get_connection() as conn:
                    conn.execute(
                        """INSERT INTO chats (name, settings, messages, updated_at) 
                           VALUES (?, ?, ?, ?)""",
                        (
                            chat_name,
                            json.dumps(settings),
                            json.dumps(messages),
                            datetime.now().isoformat()
                        )
                    )
                    conn.commit()
                
                # Remove JSON file after successful migration
                json_path.unlink()
                migrated += 1
                logger.info(f"Migrated chat '{chat_name}' from JSON to SQLite")
                
            except Exception as e:
                logger.error(f"Failed to migrate {json_path}: {e}")
        
        if migrated:
            logger.info(f"Migration complete: {migrated} chats migrated to SQLite")

    # Rows chats cap the load at the last N messages — the rows analogue of
    # the blob path's 50MB OOM guard, and the seed of future pagination.
    _ROWS_LOAD_CAP = 5000

    @staticmethod
    # ── Vaulted-chat crypto seams (Phase 2, 2026-08-15) ──
    # One decrypt funnel, one encrypt funnel. A vaulted chat's data NEVER
    # falls back to plaintext on error: encrypt raises (callers' existing
    # failure paths fire — watermark unadvanced, memory ahead of disk),
    # decrypt returns None (locked = expected, tampered-while-unlocked =
    # loud error log; callers skip or refuse).

    @staticmethod
    def _dec_value(value, what: str, chat_name: str):
        """Passthrough for plaintext; decrypt for '@enc1:' values. Returns
        str or None (locked/tampered)."""
        from core import prompt_vault
        if not prompt_vault.is_chat_encrypted(value):
            return value
        out = prompt_vault.decrypt_chat_blob(value)
        if out is None:
            # Locked = EXPECTED (boot with a sealed vault reads the last-
            # active chat's settings to evict it — that's the design, not
            # an incident). Key present + undecryptable = tampered = loud.
            if prompt_vault.chat_data_key() is None:
                logger.debug(f"{what} of a vaulted chat unreadable (vault locked)")
            else:
                logger.error(f"{what} of vaulted chat '{chat_name}' unreadable "
                             f"while the key is present — tampered?")
            return None
        return out.decode('utf-8')

    @staticmethod
    def _enc_value(value: str) -> str:
        from core import prompt_vault
        out = prompt_vault.encrypt_chat_blob(value.encode('utf-8'))
        if out is None:
            raise RuntimeError("vault sealed — cannot write vaulted-chat data")
        return out

    def _is_vaulted_conn(self, conn, chat_name: str) -> bool:
        row = conn.execute("SELECT vaulted FROM chats WHERE name = ?",
                           (chat_name,)).fetchone()
        return bool(row and row["vaulted"])

    def _row_payload(self, vaulted: bool, msg: Dict[str, Any]) -> str:
        s = self._row_json(msg)
        return self._enc_value(s) if vaulted else s

    def _settings_payload(self, conn, chat_name: str, settings: Dict[str, Any]) -> str:
        s = json.dumps(settings)
        return self._enc_value(s) if self._is_vaulted_conn(conn, chat_name) else s

    def _settings_dict(self, value, chat_name: str):
        """Stored settings value → dict, decrypting when needed. None when a
        vaulted chat's settings are unreadable (sealed) — sweeps and readers
        treat that as 'not visible right now'."""
        v = self._dec_value(value, "settings", chat_name)
        if v is None:
            return None
        try:
            return json.loads(v) if v else {}
        except Exception as e:
            logger.error(f"settings of chat '{chat_name}' unparseable: {e}")
            return None

    @staticmethod
    def _row_json(msg: Dict[str, Any]) -> str:
        """Serialize one message dict for a chat_messages row.

        thinking_raw is NEVER persisted to rows (rowify correction #3): it's
        only needed in-memory mid-tool-cycle, and persisting it would turn
        clear_thinking_raw into a below-watermark mutation forcing an O(N)
        resync every tool turn.
        """
        if "thinking_raw" in msg:
            msg = {k: v for k, v in msg.items() if k != "thinking_raw"}
        return json.dumps(msg)

    def _read_rows_messages(self, conn, chat_name: str):
        """Read a rows-format chat's messages from chat_messages, oldest first.

        Caps at the newest _ROWS_LOAD_CAP messages (DESC + reverse) so a huge
        chat can't OOM the load — same invariant as the blob path's 50MB guard.

        Returns (messages, skip_info): skip_info counts rows the reader could
        not surface — decrypt-cause (sealed / tampered / foreign-vault
        ciphertext) and parse-cause (corrupt JSON, which used to THROW here
        and brick the whole chat via _load_chat's blanket except).
        _load_chat latches skipped > 0 as read-only-degraded; transient
        readers may ignore the second element.
        """
        rows = conn.execute(
            "SELECT message_json FROM chat_messages WHERE chat_name = ? "
            "ORDER BY seq DESC LIMIT ?",
            (chat_name, self._ROWS_LOAD_CAP)
        ).fetchall()
        out = []
        skip_info = {"skipped": 0, "causes": {"decrypt": 0, "parse": 0}}
        for r in reversed(rows):
            v = self._dec_value(r["message_json"], "message row", chat_name)
            if v is None:
                # sealed/tampered — already logged in the funnel
                skip_info["skipped"] += 1
                skip_info["causes"]["decrypt"] += 1
                continue
            try:
                out.append(json.loads(v))
            except Exception as e:
                skip_info["skipped"] += 1
                skip_info["causes"]["parse"] += 1
                logger.error(f"corrupt message row in chat '{chat_name}' "
                             f"skipped (bad JSON): {e}")
        return out, skip_info

    def _load_chat(self, chat_name: str) -> bool:
        """Load chat from SQLite database (format-aware: blob or rows)."""
        self._ensure_db()

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT settings, messages, storage_format FROM chats WHERE name = ?",
                    (chat_name,)
                )
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"Chat not found in database: {chat_name}")
                    return False

                # Vault hunt R1 (2026-08-15): build into LOCALS and commit to
                # self.* only after the sealed-settings gate passes. The old
                # order assigned current_chat.messages first, so a racing seal
                # could leave THIS chat's partial plaintext living under the
                # previous chat's name in the live singleton — and a later
                # full-window resync could write it into that chat's rows.
                if row["storage_format"] == "rows":
                    new_messages, row_skips = self._read_rows_messages(conn, chat_name)
                    # Watermark: memory now mirrors the store exactly. offset
                    # is non-zero when the load was capped (we hold only the
                    # newest window; older rows stay untouched on disk).
                    total = conn.execute(
                        "SELECT COALESCE(MAX(seq) + 1, 0) FROM chat_messages "
                        "WHERE chat_name = ?", (chat_name,)
                    ).fetchone()[0]
                    loaded = len(new_messages)
                    new_rows_state = {"offset": total - loaded, "count": loaded}
                else:
                    raw_messages = row["messages"]
                    # Guard against OOM on massive chat blobs (>50MB)
                    if len(raw_messages) > 50 * 1024 * 1024:
                        logger.warning(f"Chat '{chat_name}' messages blob too large ({len(raw_messages) // 1024 // 1024}MB), truncating to last 5000 messages")
                        all_msgs = json.loads(raw_messages)
                        new_messages = all_msgs[-5000:]
                    else:
                        new_messages = json.loads(raw_messages)
                    new_rows_state = None
                    row_skips = None
                file_settings = self._settings_dict(row["settings"], chat_name)
                if file_settings is None:
                    # Vaulted chat while sealed — must not become active with
                    # defaults masquerading as its settings. Nothing committed:
                    # the previous chat's live state is untouched.
                    logger.warning(f"Cannot load '{chat_name}' — vaulted and sealed")
                    return False
                # ── Commit point — all-or-nothing from here ──
                self.current_chat.messages = new_messages
                if new_rows_state is not None:
                    self._rows_state[chat_name] = new_rows_state
                else:
                    self._rows_state.pop(chat_name, None)
                # Degraded latch: unreadable rows were skipped — the chat is
                # READABLE (that's the fix; this used to brick the load) but
                # every write path refuses until repair so the bad-but-
                # recoverable rows are never clobbered by a partial resync.
                if row_skips and row_skips["skipped"]:
                    self._rows_degraded[chat_name] = {
                        **row_skips, "at": datetime.now().isoformat()}
                    c = row_skips["causes"]
                    logger.error(
                        f"Chat '{chat_name}' loaded DEGRADED — "
                        f"{row_skips['skipped']} unreadable row(s) skipped "
                        f"(decrypt: {c['decrypt']}, parse: {c['parse']}). "
                        f"Chat is READ-ONLY until repaired.")
                else:
                    self._rows_degraded.pop(chat_name, None)
                    self._degraded_toasted.discard(chat_name)
                # The assignment above tripped the setter's resync flag —
                # memory matches the store right now, so clear it.
                self.current_chat._needs_full_resync = False
                self.current_settings = get_system_defaults()
                self.current_settings.update(file_settings)

                # Belt-and-suspenders: if the loaded history ends with an
                # assistant(tool_calls) whose tool_results are missing, the
                # previous run was killed mid-tool-cycle. Arm `_in_tool_cycle`
                # so the next stream's cancel-cleanup `finally` fires and
                # injects placeholder tool_results — without this the orphan
                # state is invisible to the streamer until something already
                # tried to use it. The read-time strip in
                # `get_messages_for_llm` ALSO defends, but that path only
                # fires at LLM-call time; arming here closes the gap for any
                # other consumer of `current_chat.messages`. Wildcard scout
                # 2026-05-07 chat-wedge belt-and-suspenders.
                msgs = self.current_chat.messages
                if msgs:
                    last_asst_idx = None
                    for k in range(len(msgs) - 1, -1, -1):
                        if msgs[k].get("role") == "assistant":
                            last_asst_idx = k
                            break
                    if last_asst_idx is not None:
                        last_asst = msgs[last_asst_idx]
                        if last_asst.get("tool_calls"):
                            call_ids = {tc.get("id", "") for tc in last_asst["tool_calls"]}
                            result_ids = set()
                            for m in msgs[last_asst_idx + 1:]:
                                if m.get("role") == "tool":
                                    result_ids.add(m.get("tool_call_id", ""))
                            if not call_ids.issubset(result_ids):
                                self._in_tool_cycle = True
                                logger.info(
                                    f"Chat '{chat_name}' loaded with unresolved "
                                    f"tool_calls — arming _in_tool_cycle for cleanup"
                                )

                logger.info(f"Loaded chat '{chat_name}' with {len(self.current_chat.messages)} messages")
                return True
                
        except Exception as e:
            logger.error(f"Failed to load chat '{chat_name}': {e}")
            return False

    def _save_current_chat(self) -> bool:
        """Save current chat to SQLite atomically. Returns True only when the
        write actually landed — every drop path (invariant breach, deleted
        chat, sealed vault) returns False so callers can stop reporting
        success for writes the DB never took (hunt 2026-08-17)."""
        self._ensure_db()

        # A1: save to the EFFECTIVE chat (a per-stream override's chat, else the
        # active one). For an override we persist messages ONLY — the target chat's
        # settings/markers are owned elsewhere (the daemon/reaper) and must not be
        # clobbered on every turn. For the active chat, behavior is unchanged.
        eff_chat = self._effective_chat()
        eff_name = self._effective_chat_name()
        is_override = eff_chat is not self.current_chat

        # Invariant guard (2026-08-17): the name and the history object must
        # divert TOGETHER. An override that names another chat but carries no
        # history would fall into the non-override branch below and persist
        # the ACTIVE chat's settings+messages under the override's name — a
        # cross-chat clobber (how trinity got default's row). Fail loudly,
        # never write.
        if not is_override and eff_name != self.active_chat_name:
            logger.error(
                f"Save invariant breach: stream override names '{eff_name}' "
                f"but carries no history — dropping save to protect it. "
                f"Fix the override producer to include 'history'."
            )
            return False

        with self._lock:
            try:
                with self._get_connection() as conn:
                    # Format probe under the SAME lock+connection as the write
                    # (delete_chat also holds self._lock, so no TOCTOU here).
                    # Missing row = chat deleted → drop the save; UPDATE (not
                    # INSERT OR REPLACE) downstream keeps that guarantee —
                    # create_chat is the sole path that creates rows.
                    fmt_row = conn.execute(
                        "SELECT storage_format FROM chats WHERE name = ?",
                        (eff_name,)
                    ).fetchone()
                    if not fmt_row:
                        logger.warning(
                            f"Save to chat '{eff_name}' — chat was deleted. "
                            f"Dropping save to avoid resurrecting it."
                        )
                        return False

                    if fmt_row["storage_format"] == "rows":
                        if not self._save_rows_chat(conn, eff_chat, eff_name, is_override):
                            return False
                    elif is_override:
                        cur = conn.execute(
                            """UPDATE chats SET messages = ?, updated_at = ? WHERE name = ?""",
                            (json.dumps(eff_chat.messages), datetime.now().isoformat(), eff_name)
                        )
                        conn.commit()
                        if cur.rowcount == 0:
                            logger.warning(
                                f"Save to chat '{eff_name}' affected 0 rows — "
                                f"chat was deleted. Dropping save to avoid resurrecting it."
                            )
                            return False
                    else:
                        cur = conn.execute(
                            """UPDATE chats SET settings = ?, messages = ?, updated_at = ?
                               WHERE name = ?""",
                            (
                                json.dumps(self.current_settings),
                                json.dumps(eff_chat.messages),
                                datetime.now().isoformat(),
                                eff_name,
                            )
                        )
                        conn.commit()
                        if cur.rowcount == 0:
                            logger.warning(
                                f"Save to chat '{eff_name}' affected 0 rows — "
                                f"chat was deleted. Dropping save to avoid resurrecting it."
                            )
                            return False
                # Write-through (2026-07-05): the operator may be VIEWING the
                # override's chat (watching a live call). Keep the in-memory
                # singleton in sync so /api/history serves fresh turns and a
                # later switch-away can't clobber the row with a stale snapshot
                # (the mid-call-amnesia + vanishing-bubble root cause).
                if is_override and self.current_chat is not None \
                        and eff_name == self.active_chat_name:
                    self.current_chat.messages = [dict(m) for m in eff_chat.messages]
                logger.debug(f"Saved chat '{eff_name}' ({len(eff_chat.messages)} messages)")
                return True
            except Exception as e:
                # The vault-sealed refusal is EXPECTED at lock time: eviction
                # switches away from the private chat and the switch-away
                # flush finds the key already dropped (by design — key first,
                # then evict). Rows are already at rest encrypted; the resync
                # latch below rewrites any in-memory delta on the next save
                # after unlock. Calm words, not a data-loss scare
                # (Krem live-hit this 2026-08-15).
                sealed = "vault sealed" in str(e)
                degraded = "chat degraded" in str(e)
                if sealed:
                    logger.info(f"Chat save deferred — vault sealed mid-switch; "
                                f"re-syncs on next unlocked save")
                elif degraded:
                    logger.error(f"Save refused — chat '{eff_name}' is degraded "
                                 f"(unreadable rows on disk); read-only until "
                                 f"repaired. This turn is NOT persisted.")
                else:
                    logger.error(f"Failed to save chat '{eff_name}': {e}")
                # Restore the blob path's self-healing property for rows
                # chats (scout, 2026-07-20): after ANY failed save, force a
                # full window resync so the next save rebuilds from the
                # in-memory truth. Flag ONLY — never pop _rows_state: the
                # offset must survive (a stateless resync deletes from seq 0
                # and would erase below-window history on capped chats).
                try:
                    eff_chat._needs_full_resync = True
                except Exception:
                    pass
                try:
                    if degraded:
                        # Once per chat per session — the log stays loud on
                        # every refusal; a toast per turn would bury the user.
                        if eff_name not in self._degraded_toasted:
                            self._degraded_toasted.add(eff_name)
                            publish(Events.CONTINUITY_TASK_ERROR, {
                                "task": "Chat Save",
                                "error": (f"Chat '{eff_name}' has unreadable rows and is "
                                          f"READ-ONLY — new messages are NOT being saved. "
                                          f"Repair it from Chat Manager, or clear/start a "
                                          f"new chat.")
                            })
                    else:
                        publish(Events.CONTINUITY_TASK_ERROR, {
                            "task": "Chat Save",
                            "error": ("Chat sealed before its final flush — everything "
                                      "already written is encrypted at rest; it re-syncs "
                                      "on your next unlock." if sealed else
                                      f"Failed to save chat: {e}. Messages may be lost on restart.")
                        })
                except Exception:
                    pass
                return False

    def _save_rows_chat(self, conn, eff_chat, eff_name: str, is_override: bool) -> bool:
        """Persist a rows-format chat. Returns False if the chat was deleted.

        Hot path (append-only growth): INSERT only messages beyond the
        watermark — O(1) per turn, the point of rowify. After any mutation
        (setter/_needs_full_resync flag, or a shrunk list) or with no
        watermark state (first override save): full WINDOW resync — DELETE
        seq >= offset + re-INSERT the in-memory list, one transaction.
        Rows below offset (older history outside a capped load) survive.

        The chats-row UPDATE runs FIRST as the deleted-chat guard. On resync
        it also nulls the messages blob: privacy correction #4 for converted
        chats (the frozen pre-conversion blob must not outlive the first
        mutation); a no-op '[]' for born-rows chats.
        """
        # Degraded gate FIRST (F2 latch): a resync would DELETE the
        # unreadable-but-recoverable rows and rebuild from the partial
        # in-memory list — destroying exactly what repair needs. Raise so
        # _save_current_chat's except surfaces the refusal.
        if eff_name in self._rows_degraded:
            raise RuntimeError(
                f"chat degraded — read-only until repaired ('{eff_name}')")

        msgs = eff_chat.messages
        state = self._rows_state.get(eff_name)
        now = datetime.now().isoformat()
        # Vaulted (Phase 2): probed once per save; rows + settings of a
        # vaulted chat encrypt on the way to disk. _enc_value RAISES when
        # the key is gone (lock raced the save) — the outer except logs and
        # the watermark never advances, same posture as any failed save.
        vaulted = self._is_vaulted_conn(conn, eff_name)

        resync = (state is None
                  or getattr(eff_chat, "_needs_full_resync", False)
                  or len(msgs) < state["count"])

        # An empty-list resync is a pure DELETE and never enters the encrypt
        # funnel (_enc_value's raise is what protects sealed rows) — probe
        # the key explicitly or a sealed chat's rows would be destroyed
        # keyless (e.g. reset_chat via an agent/continuity override while
        # locked). Same guard as plugin_data_replace.
        if vaulted and resync and not msgs:
            try:
                from core import prompt_vault
                keyless = prompt_vault.chat_data_key() is None
            except Exception:
                keyless = True
            if keyless:
                raise RuntimeError(
                    "rows save refused — chat sealed (empty resync would "
                    "delete unreadable rows)")

        if is_override:
            if resync:
                cur = conn.execute(
                    "UPDATE chats SET messages = '[]', updated_at = ? WHERE name = ?",
                    (now, eff_name))
            else:
                cur = conn.execute(
                    "UPDATE chats SET updated_at = ? WHERE name = ?",
                    (now, eff_name))
        else:
            settings_payload = json.dumps(self.current_settings)
            if vaulted:
                settings_payload = self._enc_value(settings_payload)
            if resync:
                cur = conn.execute(
                    "UPDATE chats SET settings = ?, messages = '[]', updated_at = ? WHERE name = ?",
                    (settings_payload, now, eff_name))
            else:
                cur = conn.execute(
                    "UPDATE chats SET settings = ?, updated_at = ? WHERE name = ?",
                    (settings_payload, now, eff_name))
        if cur.rowcount == 0:
            logger.warning(
                f"Save to chat '{eff_name}' affected 0 rows — "
                f"chat was deleted. Dropping save to avoid resurrecting it."
            )
            return False

        if resync:
            offset = state["offset"] if state else 0
            conn.execute(
                "DELETE FROM chat_messages WHERE chat_name = ? AND seq >= ?",
                (eff_name, offset))
            conn.executemany(
                "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                "VALUES (?, ?, ?, ?)",
                [(eff_name, offset + i, m.get("role"), self._row_payload(vaulted, m))
                 for i, m in enumerate(msgs)])
            conn.commit()
            # Watermark advances only AFTER the commit lands (scout,
            # 2026-07-20): advancing first meant a commit-frame failure
            # (disk-full/IO at fsync) left memory claiming the write
            # happened — silent divergence, and the heal could resurrect
            # deleted rows. _convert_chat_to_rows already demonstrates
            # this ordering.
            self._rows_state[eff_name] = {"offset": offset, "count": len(msgs)}
            eff_chat._needs_full_resync = False
        elif len(msgs) > state["count"]:
            # Heal before inserting: a background append (cron/agent →
            # append_messages_to_chat on a NON-active chat) lands rows at
            # MAX(seq)+1 without touching this watermark — the active-chat
            # append path syncs it, the override lane cannot (ContextVar
            # isolation). Inserting at offset+count would PK-collide with
            # those rows, and since the in-memory list never shrinks, every
            # later save of a live override stream would fail identically.
            # Absorb the foreign rows into the in-memory list ahead of the
            # unsaved tail instead: both writers survive, in order.
            expected_next = state["offset"] + state["count"]
            store_next = conn.execute(
                "SELECT COALESCE(MAX(seq) + 1, 0) FROM chat_messages "
                "WHERE chat_name = ?", (eff_name,)).fetchone()[0]
            if store_next > expected_next:
                foreign = conn.execute(
                    "SELECT message_json FROM chat_messages "
                    "WHERE chat_name = ? AND seq >= ? ORDER BY seq",
                    (eff_name, expected_next)).fetchall()
                absorbed = []
                for r in foreign:
                    v = self._dec_value(r["message_json"], "absorbed row", eff_name)
                    if v is None:
                        # Partial absorption would desync the watermark from
                        # the store (undercount → PK collision on the next
                        # append). Abort the whole save instead — memory runs
                        # ahead of disk, standard failed-save posture.
                        raise RuntimeError(
                            f"absorbed row of '{eff_name}' unreadable — save aborted")
                    absorbed.append(json.loads(v))
                msgs[state["count"]:state["count"]] = absorbed
                state["count"] += len(absorbed)
                logger.info(f"Absorbed {len(absorbed)} background-appended "
                            f"message(s) into '{eff_name}' before save")
            conn.executemany(
                "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                "VALUES (?, ?, ?, ?)",
                [(eff_name, state["offset"] + i, msgs[i].get("role"),
                  self._row_payload(vaulted, msgs[i]))
                 for i in range(state["count"], len(msgs))])
            conn.commit()
            # Post-commit only — see the resync branch. (The heal's earlier
            # count += absorbed is safe pre-commit: those rows were already
            # committed by the OTHER writer's connection.)
            state["count"] = len(msgs)
        else:
            conn.commit()
        return True

    # ── Rowify step 3: lazy blob→rows conversion (tmp/chat-storage-rowify-plan.md) ──

    def _ensure_pre_rowify_snapshot(self):
        """One-shot whole-DB snapshot before the FIRST conversion on this
        install (migration safety net #1). VACUUM INTO gives a consistent
        copy under WAL — a naive file copy would tear. Marker latches only
        on SUCCESS; failure logs and lets conversion proceed (nightly
        backups still cover the catastrophic case)."""
        marker = self.history_dir / ".pre_rowify_snapshot_done"
        if marker.exists():
            return
        try:
            import shutil
            db_size = self._db_path.stat().st_size
            free = shutil.disk_usage(str(self.history_dir)).free
            if free < db_size * 2 + 100 * 1024 * 1024:
                logger.warning("Pre-rowify snapshot skipped: low disk space (will retry next conversion)")
                return
            dest = self.history_dir / f"pre_rowify_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            if dest.exists():
                dest.unlink()
            with self._get_connection() as conn:
                conn.execute("VACUUM INTO ?", (str(dest),))
            marker.write_text(datetime.now().isoformat(), encoding='utf-8')
            logger.info(f"Pre-rowify snapshot written: {dest.name} "
                        f"({max(1, dest.stat().st_size // (1024*1024))}MB)")
        except Exception as e:
            logger.warning(f"Pre-rowify snapshot failed (conversion proceeds; nightly backups cover): {e}")

    def _convert_chat_to_rows(self, conn, chat_name: str, source: list) -> bool:
        """Convert one blob chat to rows storage. Caller holds self._lock.

        ONE transaction: INSERT all rows (thinking_raw-stripped), STRICT
        verify (count + exact parsed equality vs the stripped source), flip
        storage_format — commit is atomic, so a reader ever sees only
        blob+no-rows or rows+flipped. The blob column is left FROZEN as the
        per-chat recovery point (nulled later, on first mutation, by
        _save_rows_chat — privacy correction #4).

        Failure discipline: transient errors (SQLITE_BUSY, disk) roll back
        WITHOUT latching so the next write retries; genuine data-shape
        errors latch conversion_failed=1 → the chat stays blob forever and
        the caller falls back to the blob write path (message still saves).
        """
        row = conn.execute(
            "SELECT storage_format, conversion_failed FROM chats WHERE name = ?",
            (chat_name,)
        ).fetchone()
        if not row or row["storage_format"] != "blob" or row["conversion_failed"]:
            return False
        try:
            if not isinstance(source, list):
                raise ValueError(f"blob source is {type(source).__name__}, not a list")
            rows_json = [self._row_json(m) for m in source]
            stripped = [json.loads(s) for s in rows_json]

            # Heal any orphan rows from a pre-existing inconsistency, then
            # insert + verify + flip inside the same txn.
            conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
            conn.executemany(
                "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                "VALUES (?, ?, ?, ?)",
                [(chat_name, i, m.get("role"), s)
                 for i, (m, s) in enumerate(zip(stripped, rows_json))])
            back = [json.loads(r["message_json"]) for r in conn.execute(
                "SELECT message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq",
                (chat_name,))]
            if back != stripped:
                raise ValueError(f"verify mismatch: {len(back)} rows vs {len(stripped)} source")
            conn.execute(
                "UPDATE chats SET storage_format = 'rows' WHERE name = ?", (chat_name,))
            conn.commit()
            self._rows_state[chat_name] = {"offset": 0, "count": len(source)}
            logger.info(f"Converted chat '{chat_name}' to rows storage ({len(source)} messages)")
            return True
        except sqlite3.OperationalError as e:
            # Transient (BUSY / disk) — do NOT latch; next write retries.
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(f"Conversion of '{chat_name}' hit transient error (will retry): {e}")
            return False
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.execute(
                    "UPDATE chats SET conversion_failed = 1 "
                    "WHERE name = ? AND storage_format = 'blob'", (chat_name,))
                conn.commit()
            except Exception:
                pass
            logger.error(f"Conversion of '{chat_name}' failed on data shape — latched to blob: {e}")
            return False

    def _maybe_convert_active_chat(self):
        """Step-3 trigger for the ACTIVE chat: runs at the end_streaming 1→0
        boundary — the only point where the active chat is reliably
        non-streaming AND tool-cycle-consistent (cancel-cleanup has already
        patched any dangling cycle). Gating conversion behind `not
        _is_streaming` inside _save_current_chat would mean the active chat
        converts NEVER — foreground saves all run mid-stream. Caller
        (end_streaming) holds self._lock. Never raises."""
        try:
            chat_name = self.active_chat_name
            chat_obj = self.current_chat
            if not chat_name or chat_obj is None:
                return
            with self._get_connection() as conn:
                probe = conn.execute(
                    "SELECT storage_format, conversion_failed FROM chats WHERE name = ?",
                    (chat_name,)
                ).fetchone()
                if not probe or probe["storage_format"] != "blob" or probe["conversion_failed"]:
                    return
                self._ensure_pre_rowify_snapshot()
                if self._convert_chat_to_rows(conn, chat_name, chat_obj.messages):
                    chat_obj._needs_full_resync = False
        except Exception as e:
            logger.warning(f"convert-on-write check skipped: {e}")

    # ── Chat Manager v1a (tmp/chat-manager.md) — by-name operations ──

    def clear_chat(self, chat_name: str) -> bool:
        """Clear a chat's messages BY NAME without switching active chats.

        The active chat routes through clear() so the in-memory list resets
        too. Total wipe regardless of storage format: blob nulled, rows
        deleted, tool images swept. Route layer refuses live-call chats."""
        if self._vault_hidden(chat_name):
            # Vault hunt G2: the one by-name op that lacked this gate — a
            # sealed chat must be indestructible by stale name (bulk-clear
            # clicked across an idle-lock). Hidden = nonexistent, even here.
            return False
        # F2 latch escape hatch: clear is ALLOWED on a degraded chat (the
        # user explicitly discards it) and unlatches — pop BEFORE the wipe
        # so the active-chat route's save isn't refused by the gate.
        self._rows_degraded.pop(chat_name, None)
        self._degraded_toasted.discard(chat_name)
        if chat_name == self.active_chat_name:
            self.clear()
            return True
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM chats WHERE name = ?", (chat_name,)).fetchone()
                if not row:
                    return False
                conn.execute(
                    "UPDATE chats SET messages = '[]', updated_at = ? WHERE name = ?",
                    (datetime.now().isoformat(), chat_name))
                conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
                conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (chat_name,))
                conn.commit()
                self._rows_state.pop(chat_name, None)
            publish(Events.CHAT_CLEARED, {"chat_name": chat_name})
            # Plugin hook twin of the event (Krem's ruling 2026-08-17: story
            # journals die with the transcript — their turn anchors point at
            # messages that no longer exist). Hook, not bus: manifest-wired
            # handlers survive plugin reloads without stacking subscribers.
            try:
                from core.hooks import hook_runner, HookEvent
                if hook_runner.has_handlers("chat_cleared"):
                    # Pre-stamped: the fallback resolver reads the ACTIVE
                    # chat, which is not the one being cleared.
                    hook_runner.fire("chat_cleared",
                                     HookEvent(metadata={"chat": chat_name},
                                               chat_name=chat_name))
            except Exception as e:
                logger.warning(f"chat_cleared hook dispatch failed: {e}")
            return True
        except Exception as e:
            logger.error(f"clear_chat('{chat_name}') failed: {e}")
            return False

    def export_chat(self, chat_name: str) -> Optional[Dict[str, Any]]:
        """Full raw export: {settings, messages} — UNCAPPED, no LLM trimming.

        Reads from the store (complete even when the in-memory load was
        capped); outside a live stream the store always matches memory."""
        if self._vault_hidden(chat_name):
            return None   # sealed vault: behaves as nonexistent (ruling 4, 2026-08-14)
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT settings, messages, storage_format FROM chats WHERE name = ?",
                    (chat_name,)).fetchone()
                if not row:
                    return None
                if row["storage_format"] == "rows":
                    messages = []
                    for r in conn.execute(
                            "SELECT message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq",
                            (chat_name,)):
                        v = self._dec_value(r["message_json"], "export row", chat_name)
                        if v is None:
                            return None   # vaulted + unreadable: no partial exports
                        messages.append(json.loads(v))
                else:
                    messages = json.loads(row["messages"])
                settings = self._settings_dict(row["settings"], chat_name)
                if settings is None:
                    return None
                return {"settings": settings, "messages": messages}
        except Exception as e:
            logger.error(f"export_chat('{chat_name}') failed: {e}")
            return None

    def rename_chat(self, old_name: str, new_name: str):
        """Rename a chat across every store that keys on the name.

        Returns (ok, result) — result is the sanitized new name on success,
        an error string on failure. Route layer owns the RAG-scope rename,
        agent refusal, and live-call refusal; this owns chats +
        chat_messages + tool_images + plugin_chat_data + active-name/marker,
        one transaction."""
        if old_name == "default":
            return False, "Cannot rename the default chat"
        if self._vault_hidden(old_name):
            return False, f"Chat '{old_name}' not found"   # sealed — as if absent
        safe_name = "".join(c for c in (new_name or "") if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_').lower()
        if not safe_name:
            return False, "Invalid new name"
        if safe_name == old_name:
            return False, "Name unchanged"
        if self._is_streaming and old_name == self.active_chat_name:
            return False, "Chat is streaming — try again in a moment"
        try:
            with self._lock, self._get_connection() as conn:
                if not conn.execute("SELECT 1 FROM chats WHERE name = ?", (old_name,)).fetchone():
                    return False, f"Chat '{old_name}' not found"
                if conn.execute("SELECT 1 FROM chats WHERE name = ?", (safe_name,)).fetchone():
                    return False, f"Chat '{safe_name}' already exists"
                now = datetime.now().isoformat()
                conn.execute("UPDATE chats SET name = ?, updated_at = ? WHERE name = ?",
                             (safe_name, now, old_name))
                conn.execute("UPDATE chat_messages SET chat_name = ? WHERE chat_name = ?",
                             (safe_name, old_name))
                conn.execute("UPDATE tool_images SET chat_name = ? WHERE chat_name = ?",
                             (safe_name, old_name))
                conn.execute("UPDATE plugin_chat_data SET chat_name = ? WHERE chat_name = ?",
                             (safe_name, old_name))
                conn.execute("UPDATE chat_messages_quarantine SET chat_name = ? WHERE chat_name = ?",
                             (safe_name, old_name))
                conn.commit()
                if old_name in self._rows_state:
                    self._rows_state[safe_name] = self._rows_state.pop(old_name)
                if old_name in self._rows_degraded:
                    self._rows_degraded[safe_name] = self._rows_degraded.pop(old_name)
                if old_name == self.active_chat_name:
                    self.active_chat_name = safe_name
                    self._save_last_active(safe_name)
            logger.info(f"Renamed chat '{old_name}' -> '{safe_name}'")
            return True, safe_name
        except Exception as e:
            logger.error(f"rename_chat('{old_name}' -> '{new_name}') failed: {e}")
            return False, str(e)

    def revert_chat_to_blob(self, chat_name: str) -> bool:
        """Down-migrate a rows chat back to blob storage (safety net #2).

        Serializes the LIVE rows — not the frozen blob — so it loses nothing
        and works even after the frozen blob was privacy-nulled. Refuses
        while the chat could be mid-write. One transaction."""
        if self._is_streaming and chat_name == self.active_chat_name:
            logger.warning(f"revert_chat_to_blob('{chat_name}') refused — streaming in progress")
            return False
        if chat_name in self._rows_degraded:
            # F2 latch: its raw row loads would throw on the corrupt rows
            # anyway — refuse with a clear reason instead of a stack trace.
            logger.warning(f"revert_chat_to_blob('{chat_name}') refused — chat is degraded")
            return False
        try:
            with self._get_connection() as conn:
                if self._is_vaulted_conn(conn, chat_name):
                    # Blob format has no encryption story — reverting a
                    # vaulted chat would either write plaintext or garble.
                    logger.warning(f"revert_chat_to_blob('{chat_name}') refused — "
                                   f"chat is vaulted (unvault first)")
                    return False
        except Exception:
            pass
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute(
                    "SELECT storage_format FROM chats WHERE name = ?", (chat_name,)
                ).fetchone()
                if not row or row["storage_format"] != "rows":
                    logger.warning(f"revert_chat_to_blob('{chat_name}') — not a rows chat")
                    return False
                msgs = [json.loads(r["message_json"]) for r in conn.execute(
                    "SELECT message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq",
                    (chat_name,))]
                conn.execute(
                    "UPDATE chats SET messages = ?, storage_format = 'blob', updated_at = ? "
                    "WHERE name = ?",
                    (json.dumps(msgs), datetime.now().isoformat(), chat_name))
                conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
                conn.commit()
                self._rows_state.pop(chat_name, None)
                logger.info(f"Reverted chat '{chat_name}' to blob storage ({len(msgs)} messages)")
                return True
        except Exception as e:
            logger.error(f"revert_chat_to_blob('{chat_name}') failed: {e}")
            return False

    def _store_message_count(self, conn, chat_name: str):
        """Store-truth message count, format-aware. None if chat missing."""
        row = conn.execute(
            """SELECT CASE WHEN storage_format = 'rows'
                      THEN (SELECT COUNT(*) FROM chat_messages cm
                            WHERE cm.chat_name = chats.name)
                      ELSE json_array_length(messages)
                 END AS n FROM chats WHERE name = ?""", (chat_name,)).fetchone()
        return None if row is None else (row["n"] or 0)

    def messages_digest(self, msgs: List[Dict[str, Any]]) -> str:
        """Content fingerprint for replace_messages' optimistic concurrency.
        Count-only checks miss equal-count mutations (in-place edit,
        remove-last + regenerate) — a compress landing after one silently
        reverts it with the stale tail. Compare digests of the same
        export_chat pipeline instead."""
        import hashlib
        return hashlib.sha256(
            json.dumps(msgs, sort_keys=True, ensure_ascii=False, default=str)
            .encode("utf-8")).hexdigest()

    def replace_messages(self, chat_name: str, new_msgs: List[Dict[str, Any]],
                         expected_count: Optional[int] = None,
                         expected_digest: Optional[str] = None):
        """Replace a chat's ENTIRE message list (the trim/compress writer).

        Returns (ok, error_str). Format-preserving: a rows chat gets a full
        row rewrite (blob stays nulled), a blob chat gets a blob dump — the
        lazy-conversion machinery keeps its own schedule, and a latched
        (conversion_failed) chat still works. The active chat routes through
        the in-memory list + _save_current_chat (the clear() pattern) so
        memory and store can't diverge.

        expected_count = optimistic concurrency: the caller read the chat at
        N messages; if the store no longer holds exactly N, someone wrote in
        between (a heartbeat turn during a minutes-long compress) — abort
        rather than clobber their words. Checked under the same lock as the
        write."""
        if self._is_streaming and chat_name == self.active_chat_name:
            return False, "Chat is streaming — try again in a moment"
        if chat_name in self._rows_degraded:
            # F2 latch: trim/compress must not rewrite over unreadable rows.
            return False, (f"Chat '{chat_name}' is degraded (unreadable rows) "
                           f"— repair it first (Chat Manager)")
        new_msgs = [dict(m) for m in new_msgs]
        try:
            with self._lock:
                if expected_count is not None:
                    with self._get_connection() as conn:
                        current = self._store_message_count(conn, chat_name)
                    if current is None:
                        return False, f"Chat '{chat_name}' not found"
                    if current != expected_count:
                        return False, (f"Chat changed during the operation "
                                       f"({expected_count} → {current} messages) "
                                       f"— aborted, nothing was written")
                if expected_digest is not None:
                    exp = self.export_chat(chat_name)
                    if exp is None:
                        return False, f"Chat '{chat_name}' not found"
                    if self.messages_digest(exp["messages"]) != expected_digest:
                        return False, ("Chat changed during the operation "
                                       "(content differs from when it was read) "
                                       "— aborted, nothing was written")
                if chat_name == self.active_chat_name:
                    self.current_chat.messages = new_msgs  # setter flags full resync
                    # Total replacement: the resync must delete from seq 0,
                    # including rows below a capped-load offset (same
                    # rationale as clear()).
                    self._rows_state[chat_name] = {"offset": 0, "count": 0}
                    if not self._save_current_chat():
                        # The bool exists precisely so callers don't report
                        # "compressed" while the store kept the old rows.
                        return False, ("Save was dropped (sealed vault or a "
                                       "concurrent chat operation) — nothing was written")
                    return True, ""
                with self._get_connection() as conn:
                    row = conn.execute(
                        "SELECT storage_format FROM chats WHERE name = ?",
                        (chat_name,)).fetchone()
                    if not row:
                        return False, f"Chat '{chat_name}' not found"
                    now = datetime.now().isoformat()
                    if row["storage_format"] == "rows":
                        _vaulted = self._is_vaulted_conn(conn, chat_name)
                        conn.execute("DELETE FROM chat_messages WHERE chat_name = ?",
                                     (chat_name,))
                        conn.executemany(
                            "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                            "VALUES (?, ?, ?, ?)",
                            [(chat_name, i, m.get("role"), self._row_payload(_vaulted, m))
                             for i, m in enumerate(new_msgs)])
                        conn.execute(
                            "UPDATE chats SET messages = '[]', updated_at = ? WHERE name = ?",
                            (now, chat_name))
                    else:
                        conn.execute(
                            "UPDATE chats SET messages = ?, updated_at = ? WHERE name = ?",
                            (json.dumps(new_msgs), now, chat_name))
                    conn.commit()
                    # Stale watermark would corrupt a later windowed save;
                    # drop it — next activation re-derives from the store.
                    self._rows_state.pop(chat_name, None)
            return True, ""
        except Exception as e:
            logger.error(f"replace_messages('{chat_name}') failed: {e}")
            return False, str(e)

    def trim_chat(self, chat_name: str, keep_first_turns: int, keep_last_turns: int,
                  preview: bool = False):
        """Turn-snapped middle trim: keep the first A and last B turns, delete
        the span between. Returns (ok, result) — result is a report dict on
        success, an error string on failure. preview=True computes the same
        report without writing. keep_last is floored at 1: deleting the
        recent end is turn surgery, which lives in the chat view, not here."""
        keep_first = max(0, int(keep_first_turns))
        keep_last = max(1, int(keep_last_turns))
        exported = self.export_chat(chat_name)  # full store read, uncapped
        if exported is None:
            return False, f"Chat '{chat_name}' not found"
        msgs = exported["messages"]
        spans = turn_spans(msgs)
        report = {"turns_total": len(spans), "messages_total": len(msgs),
                  "kept_first_turns": keep_first, "kept_last_turns": keep_last}
        if len(spans) <= keep_first + keep_last:
            report.update({"deleted_messages": 0, "deleted_tokens_est": 0,
                           "messages_after": len(msgs), "no_op": True})
            return True, report
        cut_start = spans[keep_first][0]             # first deleted message
        cut_end = spans[len(spans) - keep_last][0]   # first kept-tail message
        deleted = msgs[cut_start:cut_end]
        kept = msgs[:cut_start] + msgs[cut_end:]
        report.update({
            "deleted_messages": len(deleted),
            "deleted_tokens_est": sum(count_message_tokens(m.get("content"))
                                      for m in deleted),
            "messages_after": len(kept), "no_op": False,
        })
        if preview:
            return True, report
        ok, err = self.replace_messages(chat_name, kept,
                                        expected_count=len(msgs))
        if not ok:
            return False, err
        self._prune_orphaned_tool_images(chat_name)
        return True, report

    def _any_vaulted_rows(self) -> bool:
        """Vault hunt G3 probe: True when any vaulted chat exists. Raises on
        DB trouble — prompt_vault's mint guard treats a raise as refuse
        (fail closed)."""
        with self._get_connection() as conn:
            return conn.execute(
                "SELECT 1 FROM chats WHERE vaulted LIMIT 1").fetchone() is not None

    def _vault_hidden(self, chat_name: str) -> bool:
        """Disclosure gate (vaulted chats Phase 1): True when this chat is
        private and the vault is sealed — the caller must then behave exactly
        as if the chat doesn't exist. The ACTIVE chat is exempt: it can only
        be private-while-sealed if lock-time eviction failed (mid-stream),
        and hiding its settings would blind the very privacy gates that read
        them to ENFORCE local-only — hiding would fail open. A DB error here
        fails open instead (systemic posture, matches voice_privacy): the
        caller's own read is about to fail the same way anyway."""
        sealed = _vault_sealed()
        if not sealed:
            # Vault hunt G4: "not sealed" is not the same as "key present" —
            # a MISSING vault file (deleted / quarantined / partial restore)
            # also reads not-sealed, but a vaulted=1 row is ciphertext with
            # no key and its name must stay hidden then too. chat_data_key
            # is the truth: with the G3 mint guard it returns None in every
            # keyless state. Legacy plaintext private chats keep their
            # pre-vault visible meaning; only the sealed branch hides them.
            try:
                from core import prompt_vault
                if prompt_vault.chat_data_key() is not None:
                    return False   # key present: nothing hidden while open
            except Exception:
                pass   # unreadable state — fall through to the row check
        if chat_name == self.active_chat_name:
            return False
        try:
            with self._get_connection() as conn:
                # vaulted (Phase 2) OR the legacy plaintext flag (vaulted=0
                # pre-encryption chats). Parse in Python, NOT json_extract:
                # SQLite RAISES 'malformed JSON' on an '@enc1:' settings
                # string, and that exception fail-opened this gate for every
                # fully-vaulted chat (caught by the P3 test pack 2026-08-15).
                row = conn.execute(
                    "SELECT vaulted, settings FROM chats WHERE name = ?",
                    (chat_name,)).fetchone()
            if not row:
                return False
            if row[0]:
                return True   # hidden while sealed OR keyless (G4)
            if not sealed:
                return False  # vault absent: legacy flag keeps pre-vault visibility
            s = row[1]
            if isinstance(s, str) and s.startswith("@enc1:"):
                return True   # encrypted settings = vaulted, whatever the flag says
            try:
                return bool(json.loads(s or "{}").get("private_chat"))
            except Exception:
                # Sealed world + plaintext settings that won't parse: this
                # chat's privacy answer is unknowable, so hide it (matches
                # search_chat_content's posture on the same condition — "a
                # broken filter must not become the oracle"). The generic
                # DB-error fail-open below is deliberate systemic posture
                # and stays.
                return True
        except Exception as e:
            logger.warning(f"_vault_hidden read failed — treating as visible: {e}")
            return False

    def is_chat_degraded(self, chat_name: str):
        """Degraded-latch info for a chat ({skipped, causes, at}) or None.

        In-memory only — set when a LOAD skipped unreadable rows; cleared by
        a clean load, clear, delete, repair, or the unlock re-eval."""
        return self._rows_degraded.get(chat_name)

    def reeval_degraded_chats(self):
        """Vault-unlock hook: decrypt-cause latches may be transient (the
        rows belong to the vault that just opened). Drop them so the next
        load re-evaluates; reload the ACTIVE chat immediately if it was
        latched (re-latches on its own if the rows are truly bad).
        Parse-cause-only latches stay — corrupt JSON doesn't heal with a key."""
        try:
            drop = [n for n, info in list(self._rows_degraded.items())
                    if (info.get("causes") or {}).get("decrypt")]
            for n in drop:
                self._rows_degraded.pop(n, None)
                self._degraded_toasted.discard(n)
            if drop:
                logger.info(f"[VAULT] degraded re-eval on unlock — dropped "
                            f"{len(drop)} decrypt-cause latch(es)")
            if self.active_chat_name in drop:
                self._load_chat(self.active_chat_name)
        except Exception as e:
            logger.warning(f"degraded re-eval on unlock failed: {e}")

    # ── F2 Wave 4: chat row repair (quarantine — never destroy salvage) ──

    def _diagnose_rows_conn(self, conn, chat_name: str):
        """Classify every message row of a rows chat (read-only, caller holds
        the lock+conn). reason: 'decrypt' (undecryptable — foreign vault /
        tamper / keyless) | 'parse' (corrupt JSON). vault_mismatch: >50% of
        rows undecryptable = wrong-vault situation — the three-laws tripwire
        (restore the matching vault backup; quarantine recovers nothing)."""
        rows = conn.execute(
            "SELECT seq, message_json FROM chat_messages WHERE chat_name = ? "
            "ORDER BY seq", (chat_name,)).fetchall()
        bad = []
        seqs = []
        for r in rows:
            seqs.append(r["seq"])
            v = self._dec_value(r["message_json"], "message row", chat_name)
            if v is None:
                bad.append({"seq": r["seq"], "reason": "decrypt"})
                continue
            try:
                json.loads(v)
            except Exception:
                bad.append({"seq": r["seq"], "reason": "parse"})
        gap_count = (seqs[-1] - seqs[0] + 1) - len(seqs) if seqs else 0
        n_dec = sum(1 for b in bad if b["reason"] == "decrypt")
        from core import prompt_vault as _pv
        return {
            "total": len(seqs),
            "ok": len(seqs) - len(bad),
            "bad": bad,
            "gap_count": gap_count,
            "vault_mismatch": bool(seqs) and n_dec * 2 > len(seqs),
            "key_absent": _pv.chat_data_key() is None,
        }

    def diagnose_chat_rows(self, chat_name: str):
        """Read-only row diagnosis. Returns the report dict, or None when the
        chat doesn't exist (or is sealed-hidden), or {'error': ...} for
        non-rows chats."""
        self._ensure_db()
        if self._vault_hidden(chat_name):
            return None
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT storage_format FROM chats WHERE name = ?",
                               (chat_name,)).fetchone()
            if not row:
                return None
            if row["storage_format"] != "rows":
                return {"error": "not a rows chat — repair applies to rows storage only"}
            return self._diagnose_rows_conn(conn, chat_name)

    def repair_chat_rows(self, chat_name: str, preview: bool = True,
                         force: bool = False):
        """(ok, report_or_error). Quarantine-then-reseq repair of a rows chat.

        preview=True classifies and reports the plan — writes NOTHING.
        Real run, ONE transaction under the lock: unreadable rows move to
        chat_messages_quarantine VERBATIM (ciphertext stays ciphertext — a
        returning vault backup can still decrypt them; a plaintext copy
        would break at-rest), survivors re-INSERT contiguous from seq 0
        (payload + role untouched, never re-encrypted), read-back verify,
        commit. Then the watermark + degraded latch drop and the chat
        reloads if active.

        Guards: streaming refusal; keyless refusal when any row is
        decrypt-bad (it may decrypt fine after unlock — quarantining then
        would be theft); vault_mismatch requires force=True (three-laws:
        restore the matching vault backup instead)."""
        self._ensure_db()
        if self._vault_hidden(chat_name):
            return False, f"Chat '{chat_name}' not found"
        if self._is_streaming and chat_name == self.active_chat_name:
            return False, "Chat is streaming — try again in a moment"
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute("SELECT storage_format FROM chats WHERE name = ?",
                                   (chat_name,)).fetchone()
                if not row:
                    return False, f"Chat '{chat_name}' not found"
                if row["storage_format"] != "rows":
                    return False, "not a rows chat — repair applies to rows storage only"
                report = self._diagnose_rows_conn(conn, chat_name)
                report["planned_quarantine"] = len(report["bad"])
                report["planned_reseq"] = report["gap_count"] > 0
                if preview:
                    return True, report
                if not report["bad"] and not report["gap_count"]:
                    # Nothing to fix — clear any stale latch so the chat
                    # resumes normal life without a reload dance.
                    self._rows_degraded.pop(chat_name, None)
                    self._degraded_toasted.discard(chat_name)
                    return True, {**report, "no_op": True}
                if report["key_absent"] and any(
                        b["reason"] == "decrypt" for b in report["bad"]):
                    return False, ("vault is locked — unlock before repairing: "
                                   "encrypted rows may decrypt fine with the key")
                if report["vault_mismatch"] and not force:
                    return False, ("vault mismatch — most rows are undecryptable. "
                                   "Restore the MATCHING vault backup instead (it "
                                   "recovers everything; quarantine recovers "
                                   "nothing). Re-run with force to proceed anyway.")

                now = datetime.now().isoformat()
                reason_by_seq = {b["seq"]: b["reason"] for b in report["bad"]}
                all_rows = conn.execute(
                    "SELECT seq, role, message_json FROM chat_messages "
                    "WHERE chat_name = ? ORDER BY seq", (chat_name,)).fetchall()
                survivors = [r for r in all_rows if r["seq"] not in reason_by_seq]
                conn.executemany(
                    "INSERT INTO chat_messages_quarantine "
                    "(chat_name, orig_seq, role, message_json, reason, quarantined_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(chat_name, r["seq"], r["role"], r["message_json"],
                      reason_by_seq[r["seq"]], now)
                     for r in all_rows if r["seq"] in reason_by_seq])
                conn.execute("DELETE FROM chat_messages WHERE chat_name = ?",
                             (chat_name,))
                conn.executemany(
                    "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                    "VALUES (?, ?, ?, ?)",
                    [(chat_name, i, r["role"], r["message_json"])
                     for i, r in enumerate(survivors)])
                back = conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE chat_name = ?",
                    (chat_name,)).fetchone()[0]
                if back != len(survivors):
                    conn.rollback()
                    return False, "read-back verify failed — nothing was changed"
                conn.execute("UPDATE chats SET updated_at = ? WHERE name = ?",
                             (now, chat_name))
                conn.commit()
                self._rows_state.pop(chat_name, None)
                self._rows_degraded.pop(chat_name, None)
                self._degraded_toasted.discard(chat_name)
                report["quarantined"] = len(reason_by_seq)
                report["kept"] = len(survivors)
                logger.info(f"Repaired chat '{chat_name}': quarantined "
                            f"{len(reason_by_seq)} row(s), kept {len(survivors)}, "
                            f"reseq={report['planned_reseq']}")
            if chat_name == self.active_chat_name:
                self._load_chat(chat_name)
            return True, report
        except Exception as e:
            logger.error(f"repair_chat_rows('{chat_name}') failed: {e}", exc_info=True)
            return False, f"repair failed: {e}"

    def is_chat_hidden(self, chat_name: str) -> bool:
        """PUBLIC seam for plugins (P3): True when this chat must be treated
        as nonexistent (private + vault sealed). Explicit — plugins must not
        reverse-engineer the get_settings_for(None) sentinel, which also
        means 'no such chat'. Same semantics as every by-name store gate,
        active-chat exemption included."""
        return self._vault_hidden(chat_name)

    # ── Chat-scoped plugin data (vault v1.3) ──
    # Sealed contract: reads on a hidden chat come back empty (as-if-
    # absent, same as every by-name gate); writes RAISE — a plugin must
    # never silently drop a private deposit. Writes on a vaulted chat
    # encrypt in the funnel; keyless encrypt raises in _enc_value.

    def _plugin_data_writable(self, conn, chat_name: str):
        """Raise if this chat can't accept plugin data right now."""
        if self._vault_hidden(chat_name):
            raise RuntimeError("plugin data write refused — chat hidden (vault sealed)")
        if not conn.execute("SELECT 1 FROM chats WHERE name = ?",
                            (chat_name,)).fetchone():
            raise ValueError(f"Chat '{chat_name}' not found")

    def _plugin_data_decode(self, value, chat_name: str):
        """Stored value → Python object; None = unreadable (logged in the
        funnel) or unparseable."""
        v = self._dec_value(value, "plugin data", chat_name)
        if v is None:
            return None
        try:
            return json.loads(v)
        except Exception:
            logger.error(f"plugin data row unparseable in chat '{chat_name}'")
            return None

    def plugin_data_get(self, plugin: str, chat_name: str, key: str, default=None):
        """seq=0 slot for (plugin, chat, key), JSON-decoded. default when
        absent, hidden, or unreadable."""
        if self._vault_hidden(chat_name):
            return default
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM plugin_chat_data "
                    "WHERE plugin = ? AND chat_name = ? AND key = ? AND seq = 0",
                    (plugin, chat_name, key)).fetchone()
            if not row:
                return default
            out = self._plugin_data_decode(row["value"], chat_name)
            return default if out is None else out
        except Exception as e:
            logger.error(f"plugin_data_get({plugin}) failed: {e}")
            return default

    def plugin_data_put(self, plugin: str, chat_name: str, key: str, value):
        """Upsert the seq=0 slot. Raises on hidden chat, missing chat, or
        sealed-vault encrypt refusal — never a silent drop."""
        with self._lock, self._get_connection() as conn:
            self._plugin_data_writable(conn, chat_name)
            conn.execute(
                "INSERT OR REPLACE INTO plugin_chat_data "
                "(plugin, chat_name, key, seq, value, updated_at) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (plugin, chat_name, key,
                 self._plugin_data_payload(conn, chat_name, value),
                 datetime.now().isoformat()))
            conn.commit()

    def plugin_data_append(self, plugin: str, chat_name: str, key: str, value) -> int:
        """Append one event row (seq = max+1, starting at 1). Returns the
        seq. Same raise semantics as put. Don't mix put and append on one
        key — seq 0 would lead every read_all."""
        with self._lock, self._get_connection() as conn:
            self._plugin_data_writable(conn, chat_name)
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM plugin_chat_data "
                "WHERE plugin = ? AND chat_name = ? AND key = ?",
                (plugin, chat_name, key)).fetchone()[0]
            conn.execute(
                "INSERT INTO plugin_chat_data "
                "(plugin, chat_name, key, seq, value, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (plugin, chat_name, key, seq,
                 self._plugin_data_payload(conn, chat_name, value),
                 datetime.now().isoformat()))
            conn.commit()
            return seq

    def plugin_data_append_many(self, plugin: str, chat_name: str, key: str,
                                values: list) -> list:
        """Append several event rows in ONE transaction — all land or none.
        Returns the assigned seqs. Same raise semantics as append. Born
        2026-08-21: per-event appends let a mid-write death journal HALF a
        story resolve (the interaction landed, its set-flag didn't). All
        payloads are encoded BEFORE the first INSERT, so an encode refusal
        (sealed vault, bad value) aborts with zero rows written."""
        if not values:
            return []
        with self._lock, self._get_connection() as conn:
            self._plugin_data_writable(conn, chat_name)
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM plugin_chat_data "
                "WHERE plugin = ? AND chat_name = ? AND key = ?",
                (plugin, chat_name, key)).fetchone()[0]
            now = datetime.now().isoformat()
            rows = [(plugin, chat_name, key, seq + i,
                     self._plugin_data_payload(conn, chat_name, v), now)
                    for i, v in enumerate(values)]
            conn.executemany(
                "INSERT INTO plugin_chat_data "
                "(plugin, chat_name, key, seq, value, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", rows)
            conn.commit()
            return list(range(seq, seq + len(values)))

    def plugin_data_read_all(self, plugin: str, chat_name: str, key: str) -> list:
        """All rows for (plugin, chat, key) ordered by seq — the journal
        read. Empty when hidden. Unreadable rows are skipped (the funnel
        already error-logged them)."""
        if self._vault_hidden(chat_name):
            return []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT value FROM plugin_chat_data "
                    "WHERE plugin = ? AND chat_name = ? AND key = ? ORDER BY seq",
                    (plugin, chat_name, key)).fetchall()
            out = []
            for r in rows:
                v = self._plugin_data_decode(r["value"], chat_name)
                if v is not None:
                    out.append(v)
            return out
        except Exception as e:
            logger.error(f"plugin_data_read_all({plugin}) failed: {e}")
            return []

    def plugin_data_replace(self, plugin: str, chat_name: str, key: str, values: list):
        """Atomically swap a key's whole row set (the journal-truncate op —
        revert lives here). Rows renumber 1..len(values). Same raise
        semantics as put."""
        with self._lock, self._get_connection() as conn:
            self._plugin_data_writable(conn, chat_name)
            if not values:
                # An empty replace is a pure DELETE and never enters the
                # encrypt funnel — probe the seal explicitly so a sealed
                # chat's rows can't be wiped through the active-chat
                # exemption ("temporarily unreadable" must not become
                # "gone"; hunt 2026-08-17).
                try:
                    from core import prompt_vault
                    keyless = prompt_vault.chat_data_key() is None
                except Exception:
                    keyless = True
                if keyless and self._is_vaulted_conn(conn, chat_name):
                    raise RuntimeError(
                        "plugin data replace refused — chat sealed "
                        "(empty replace would delete unreadable rows)")
            now = datetime.now().isoformat()
            payloads = [(plugin, chat_name, key, i + 1,
                         self._plugin_data_payload(conn, chat_name, v), now)
                        for i, v in enumerate(values)]
            conn.execute(
                "DELETE FROM plugin_chat_data "
                "WHERE plugin = ? AND chat_name = ? AND key = ?",
                (plugin, chat_name, key))
            conn.executemany(
                "INSERT INTO plugin_chat_data "
                "(plugin, chat_name, key, seq, value, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", payloads)
            conn.commit()

    def plugin_data_delete(self, plugin: str, chat_name: str, key: str = None):
        """Delete one key (all seqs) or, with key=None, every key this
        plugin holds for the chat. Refuses on hidden; tolerates a missing
        chat (cleanup after chat_deleted must not throw)."""
        if self._vault_hidden(chat_name):
            raise RuntimeError("plugin data delete refused — chat hidden (vault sealed)")
        with self._lock, self._get_connection() as conn:
            if key is None:
                conn.execute(
                    "DELETE FROM plugin_chat_data WHERE plugin = ? AND chat_name = ?",
                    (plugin, chat_name))
            else:
                conn.execute(
                    "DELETE FROM plugin_chat_data "
                    "WHERE plugin = ? AND chat_name = ? AND key = ?",
                    (plugin, chat_name, key))
            conn.commit()

    def plugin_data_get_all_chats(self, plugin: str, key: str) -> dict:
        """{chat_name: decoded seq=0 value} for every chat holding `key` —
        HIDDEN CHATS OMITTED, same inheritance rule as list_chat_files.
        This is the only cross-chat read; the hidden filter lives here so
        no plugin ever re-implements it."""
        try:
            with self._get_connection() as conn:
                # JOIN on chats: rows whose owner is gone (orphans from any
                # missed cleanup path) must not read as live chats —
                # _vault_hidden answers False for a nonexistent chat.
                rows = conn.execute(
                    "SELECT p.chat_name AS chat_name, p.value AS value "
                    "FROM plugin_chat_data p JOIN chats c ON c.name = p.chat_name "
                    "WHERE p.plugin = ? AND p.key = ? AND p.seq = 0",
                    (plugin, key)).fetchall()
            out = {}
            for r in rows:
                if self._vault_hidden(r["chat_name"]):
                    continue
                v = self._plugin_data_decode(r["value"], r["chat_name"])
                if v is not None:
                    out[r["chat_name"]] = v
            return out
        except Exception as e:
            logger.error(f"plugin_data_get_all_chats({plugin}) failed: {e}")
            return {}

    def plugin_data_meta(self, plugin: str, chat_name: str, key: str):
        """{'rows': n, 'updated_at': max-ISO} for a key, or None when
        absent/hidden. Lets plugins ask 'which save is newest' without
        decrypting a single row."""
        if self._vault_hidden(chat_name):
            return None
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, MAX(updated_at) AS u "
                    "FROM plugin_chat_data "
                    "WHERE plugin = ? AND chat_name = ? AND key = ?",
                    (plugin, chat_name, key)).fetchone()
            if not row or not row["n"]:
                return None
            return {"rows": row["n"], "updated_at": row["u"]}
        except Exception as e:
            logger.error(f"plugin_data_meta({plugin}) failed: {e}")
            return None

    def plugin_data_keys(self, plugin: str, chat_name: str) -> list:
        """Distinct keys this plugin holds for the chat. Empty when hidden."""
        if self._vault_hidden(chat_name):
            return []
        try:
            with self._get_connection() as conn:
                return [r[0] for r in conn.execute(
                    "SELECT DISTINCT key FROM plugin_chat_data "
                    "WHERE plugin = ? AND chat_name = ? ORDER BY key",
                    (plugin, chat_name))]
        except Exception as e:
            logger.error(f"plugin_data_keys({plugin}) failed: {e}")
            return []

    def _plugin_data_payload(self, conn, chat_name: str, value) -> str:
        s = json.dumps(value)
        return self._enc_value(s) if self._is_vaulted_conn(conn, chat_name) else s

    def list_chat_files(self, stats: bool = False,
                        include_hidden: bool = False) -> List[Dict[str, Any]]:
        """List all available chats with metadata.

        stats=True adds size_bytes (message store + tool images) per chat —
        Chat Manager only; the hot dropdown path skips the size subqueries.

        While the vault is sealed, private chats are OMITTED entirely — no
        name, no counts, no timestamps (Krem's ruling C, 2026-08-13). Every
        list consumer (dropdown, Chat Manager, plugins, executor target
        resolution) inherits from this one filter. include_hidden=True is
        for in-process housekeeping that needs ground truth (lock-time
        eviction) — never hand it to a route or tool."""
        self._ensure_db()

        size_cols = ""
        if stats:
            # turn_count = user-message count (the trim/compress unit). Same
            # cheap-SQL rule as msg_count: role sidecar for rows chats,
            # json_each for blobs — never parse in Python on the list path.
            size_cols = """,
                              CASE WHEN storage_format = 'rows'
                                   THEN (SELECT COALESCE(SUM(LENGTH(message_json)), 0)
                                         FROM chat_messages cm WHERE cm.chat_name = chats.name)
                                   ELSE LENGTH(messages)
                              END AS msg_bytes,
                              (SELECT COALESCE(SUM(LENGTH(data)), 0)
                               FROM tool_images ti WHERE ti.chat_name = chats.name) AS img_bytes,
                              CASE WHEN storage_format = 'rows'
                                   THEN (SELECT COUNT(*) FROM chat_messages cm
                                         WHERE cm.chat_name = chats.name AND cm.role = 'user')
                                   ELSE (SELECT COUNT(*) FROM json_each(chats.messages)
                                         WHERE json_extract(json_each.value, '$.role') = 'user')
                              END AS turn_count"""

        chats = []
        sealed = (not include_hidden) and _vault_sealed()
        # Vault hunt G4: a MISSING vault file isn't "unlocked" — vaulted rows
        # are ciphertext with no key, so their names stay hidden then too.
        # chat_data_key is the truth (None in every keyless state, G3 guard).
        # Legacy plaintext private chats keep pre-vault visibility.
        keyless_vaulted = sealed
        if not include_hidden and not sealed:
            try:
                from core import prompt_vault
                keyless_vaulted = prompt_vault.chat_data_key() is None
            except Exception:
                keyless_vaulted = True
        try:
            with self._lock, self._get_connection() as conn:
                # msg_count is format-aware: COUNT(*) over chat_messages for
                # rows chats (a converted chat's frozen blob would give a
                # stale json_array_length — rowify plan, land in step 1).
                cursor = conn.execute(
                    f"""SELECT name, settings, vaulted, updated_at, created_at,
                              CASE WHEN storage_format = 'rows'
                                   THEN (SELECT COUNT(*) FROM chat_messages cm
                                         WHERE cm.chat_name = chats.name)
                                   ELSE json_array_length(messages)
                              END AS msg_count{size_cols}
                       FROM chats
                       ORDER BY updated_at DESC"""
                )
                for row in cursor:
                    # Vaulted + sealed (or vault file missing — G4): skip
                    # BEFORE any settings parse — the ciphertext can't be
                    # read and the row must not exist.
                    # (Active-chat exemption kept: Phase 0 select-invariant.)
                    if keyless_vaulted and row["vaulted"] \
                            and row["name"] != self.active_chat_name:
                        continue
                    settings = self._settings_dict(row["settings"], row["name"])
                    if settings is None:
                        # Encrypted settings without the key (include_hidden
                        # truth pass while sealed, or tamper): surface a
                        # minimal stub — enough for eviction logic (name +
                        # vaulted⇒private) without inventing content.
                        settings = {"private_chat": True} if row["vaulted"] else {}
                    # Sealed vault: legacy plaintext private chats (vaulted=0)
                    # vanish by the Phase 1 policy gate. The active chat stays
                    # (only reachable when lock-time eviction failed) — its
                    # name is already on screen.
                    if sealed and settings.get("private_chat") \
                            and row["name"] != self.active_chat_name:
                        continue

                    entry = {
                        "name": row["name"],
                        "display_name": settings.get("private_display_name") or row["name"].replace('_', ' ').title(),
                        "message_count": row["msg_count"] or 0,
                        "is_active": row["name"] == self.active_chat_name,
                        "modified": row["updated_at"],
                        "created": row["created_at"],
                        "private_chat": bool(settings.get("private_chat") or row["vaulted"]),
                        "vaulted": bool(row["vaulted"]),
                        "archived": bool(settings.get("archived")),
                        # Mode-tagged chats (game/story sessions) belong to their
                        # plugin surface; core surfaces them like private/archived
                        # and stays agnostic about what the modes mean.
                        "mode": settings.get("mode") or "",
                        # F2 latch (in-memory — only chats loaded this session
                        # can carry it; a never-activated corrupt chat shows
                        # healthy here until first activation).
                        "degraded": row["name"] in self._rows_degraded,
                        "settings": settings
                    }
                    if stats:
                        entry["size_bytes"] = (row["msg_bytes"] or 0) + (row["img_bytes"] or 0)
                        entry["turn_count"] = row["turn_count"] or 0
                    chats.append(entry)
        except Exception as e:
            logger.error(f"Error listing chats: {e}")

        return chats

    def search_chat_content(self, query: str) -> Dict[str, int]:
        """Deep content search across every chat (Chat Manager).

        Returns {chat_name: matching_message_count}. Matches message CONTENT
        only — never role keys or metadata, so searching "user" doesn't hit
        every message in the store. LIKE semantics (ASCII case-insensitive),
        wildcards escaped so "100%" means a literal percent. Format-aware
        like list_chat_files: role sidecar for rows chats, json_each for
        blobs — never parse JSON in Python on a whole-store scan."""
        q = (query or "").strip()
        if not q:
            return {}
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{esc}%"
        self._ensure_db()
        hits: Dict[str, int] = {}
        # Sealed vault: private chats are excluded from the scan — a hit
        # count is a keyword ORACLE on hidden content (recon 2026-08-13).
        # If the exclusion set itself can't be built, return NO results:
        # a broken filter must not become the oracle it exists to prevent.
        hidden: set = set()
        if _vault_sealed():
            # Vault hunt G1 sibling: built in PYTHON through the settings
            # funnel — SQL json_extract on a vaulted chat's `@enc1:` settings
            # RAISES (the seed defect's family), which used to disable search
            # entirely while sealed. Unreadable settings = hidden.
            try:
                with self._get_connection() as conn:
                    rows = conn.execute(
                        "SELECT name, settings, vaulted FROM chats").fetchall()
                for r in rows:
                    if r["vaulted"]:
                        hidden.add(r["name"])
                        continue
                    s = self._settings_dict(r["settings"], r["name"])
                    if s is None or s.get("private_chat"):
                        hidden.add(r["name"])
                hidden.discard(self.active_chat_name)
            except Exception as e:
                logger.warning(f"search privacy filter unreadable — search disabled while sealed: {e}")
                return {}
        # Vault hunt G1 (PROVEN 2026-08-15): a vaulted chat's rows are
        # `@enc1:` strings — bare json_extract RAISES on the first one and
        # killed the whole scan for EVERY chat. CASE is the only SQLite
        # construct with guaranteed evaluation order (AND terms may run in
        # any order per the docs), so the json_valid probe MUST live in a
        # CASE, not an AND. Sealed content itself is owned by the Python
        # decrypt-scan below; each scan gets its own try so one lane's
        # failure can't blank the other.
        try:
            with self._lock, self._get_connection() as conn:
                for name, n in conn.execute(
                    r"""SELECT cm.chat_name, COUNT(*) FROM chat_messages cm
                        WHERE CASE WHEN json_valid(cm.message_json)
                              THEN json_extract(cm.message_json, '$.content') LIKE ? ESCAPE '\'
                              ELSE 0 END
                        GROUP BY cm.chat_name""", (pattern,)):
                    if name not in hidden:
                        hits[name] = n
        except Exception as e:
            logger.error(f"Chat content search (rows scan) failed: {e}")
        try:
            with self._lock, self._get_connection() as conn:
                for name, n in conn.execute(
                    r"""SELECT chats.name, COUNT(*)
                        FROM chats, json_each(chats.messages)
                        WHERE COALESCE(chats.storage_format, '') != 'rows'
                          AND chats.messages IS NOT NULL
                          AND json_extract(json_each.value, '$.content') LIKE ? ESCAPE '\'
                        GROUP BY chats.name""", (pattern,)):
                    if name not in hidden:
                        hits[name] = hits.get(name, 0) + n
        except Exception as e:
            logger.error(f"Chat content search (blob scan) failed: {e}")
        # Vaulted chats while UNLOCKED: LIKE can't see ciphertext — decrypt-
        # scan in Python (Chat Manager deep search must find private content
        # when the key is present). Sealed: excluded above, oracle stays shut.
        if not _vault_sealed():
            try:
                needle = q.lower()
                with self._get_connection() as conn:
                    vaulted_names = [r[0] for r in conn.execute(
                        "SELECT name FROM chats WHERE vaulted")]
                    for name in vaulted_names:
                        n = 0
                        for r in conn.execute(
                                "SELECT message_json FROM chat_messages "
                                "WHERE chat_name = ?", (name,)):
                            v = self._dec_value(r["message_json"], "search row", name)
                            if v is None:
                                continue
                            try:
                                content = json.loads(v).get("content")
                            except Exception:
                                continue
                            hay = content if isinstance(content, str) \
                                else json.dumps(content, ensure_ascii=False)
                            if hay and needle in hay.lower():
                                n += 1
                        if n:
                            hits[name] = n
            except Exception as e:
                logger.warning(f"vaulted-chat search scan failed: {e}")
        return hits

    def create_chat(self, chat_name: str) -> bool:
        """Create new chat with default settings."""
        if not chat_name or not chat_name.strip():
            logger.error("Cannot create chat with empty name")
            return False

        safe_name = sanitize_chat_name(chat_name)
        
        self._ensure_db()
        
        try:
            with self._get_connection() as conn:
                # Check if exists
                cursor = conn.execute(
                    "SELECT 1 FROM chats WHERE name = ?", 
                    (safe_name,)
                )
                if cursor.fetchone():
                    logger.warning(f"Chat already exists: {safe_name}")
                    return False
                
                # Create new chat (created_at stamped from birth; chats that
                # predate the column stay NULL — UI shows "—"). Born as
                # 'rows' (rowify step 2): messages persist to chat_messages
                # from the first write; the blob column stays '[]' forever.
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO chats (name, settings, messages, updated_at, created_at, storage_format)
                       VALUES (?, ?, ?, ?, ?, 'rows')""",
                    (
                        safe_name,
                        json.dumps(get_user_defaults()),
                        json.dumps([]),
                        now,
                        now
                    )
                )
                conn.commit()
                logger.info(f"Created new chat: {safe_name}")
        except Exception as e:
            logger.error(f"Failed to create chat '{chat_name}': {e}")
            return False

        # Announce OUTSIDE the db connection so SSE handlers can't re-enter the
        # write lock. Every new chat — web "+", cron target, ephemeral phone call —
        # routes through here, so this single publish refreshes the chat dropdown
        # on every connected browser (main.js listens for CHAT_CREATED).
        publish(Events.CHAT_CREATED, {"name": safe_name})
        return True

    def delete_chat(self, chat_name: str) -> bool:
        """Delete chat. Recreates default if deleted, switches active if needed."""
        self._ensure_db()

        if self._vault_hidden(chat_name):
            return False   # sealed vault: behaves as nonexistent

        if self._is_streaming and chat_name == self.active_chat_name:
            logger.warning(f"Cannot delete '{chat_name}' — streaming in progress")
            return False

        try:
            with self._lock, self._get_connection() as conn:
                # Check if exists (settings fetched for vault-ref bookkeeping)
                cursor = conn.execute(
                    "SELECT settings FROM chats WHERE name = ?",
                    (chat_name,)
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Chat not found: {chat_name}")
                    return False
                deleted_prompt = (self._settings_dict(row['settings'], chat_name)
                                  or {}).get('prompt')

                was_active = (chat_name == self.active_chat_name)
                
                # Delete chat and any associated data
                conn.execute("DELETE FROM chats WHERE name = ?", (chat_name,))
                conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
                try:
                    conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (chat_name,))
                except Exception:
                    pass  # Table may not exist yet
                # v1.3 ruling (a): plugin rows die with the chat, no core
                # archive. Public chats may archive plugin-side via the
                # chat_deleted hook; private chats must not (vault wins).
                try:
                    conn.execute("DELETE FROM plugin_chat_data WHERE chat_name = ?", (chat_name,))
                except Exception:
                    pass
                # Quarantined rows die with the chat too (they ARE the chat).
                try:
                    conn.execute("DELETE FROM chat_messages_quarantine WHERE chat_name = ?", (chat_name,))
                except Exception:
                    pass
                conn.commit()
                # Reclaim freed pages now that we deleted a chat (potentially
                # with megabytes of tool_images blobs). `auto_vacuum=INCREMENTAL`
                # is enabled at _init_db but only does anything when something
                # actually calls `incremental_vacuum`. Without this call, the
                # file high-water-mark only shrinks during weekly VACUUM runs
                # in backup.py. Cap at 100 pages (~400KB) to avoid a long
                # pause on a heavy delete. Wildcard scout 2026-05-07 L1.
                try:
                    conn.execute("PRAGMA incremental_vacuum(100)")
                    conn.commit()
                except Exception:
                    pass
                logger.info(f"Deleted chat: {chat_name}")
                self._rows_state.pop(chat_name, None)
                self._rows_degraded.pop(chat_name, None)
                self._degraded_toasted.discard(chat_name)

                # Ensure default exists
                self._ensure_default_exists()
                
                # Switch to default if we deleted active. Guarded like
                # set_active_chat: _load_chat is all-or-nothing, and on
                # failure (sealed default, no key) the buffer still holds
                # the DELETED chat's messages — committing the name anyway
                # would persist them INTO default after the next unlock.
                if was_active:
                    if self._load_chat("default"):
                        self.active_chat_name = "default"
                        logger.info("Switched to default after deleting active chat")
                    else:
                        # Keep the deleted name (the deleted-chat guard drops
                        # any save from this state) and empty the buffer.
                        self.current_chat.messages = []
                        self.current_settings = get_system_defaults()
                        logger.error("Could not load 'default' after deleting the active "
                                     "chat — in-memory state cleared; saves drop until a "
                                     "chat loads")

                # Safe under self._lock (RLock; the scan re-enters it) — and
                # no path acquires scheduler._lock before history's, so the
                # scan's lock ordering stays one-directional.
                if deleted_prompt:
                    _vault_ref_sync(None, deleted_prompt)
                return True
                
        except Exception as e:
            logger.error(f"Failed to delete chat '{chat_name}': {e}")
            return False

    def _ensure_default_exists(self):
        """Ensure default chat always exists."""
        self._ensure_db()
        
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT 1 FROM chats WHERE name = 'default'"
                )
                if not cursor.fetchone():
                    now = datetime.now().isoformat()
                    conn.execute(
                        """INSERT INTO chats (name, settings, messages, updated_at, created_at, storage_format)
                           VALUES (?, ?, ?, ?, ?, 'rows')""",
                        (
                            "default",
                            json.dumps(get_user_defaults()),
                            json.dumps([]),
                            now,
                            now
                        )
                    )
                    conn.commit()
                    logger.info("Created default chat")
        except Exception as e:
            logger.error(f"Failed to ensure default chat: {e}")

    def _read_last_active(self):
        """Read last active chat name from marker file."""
        marker = self.history_dir / ".active_chat"
        try:
            if marker.exists():
                name = marker.read_text(encoding='utf-8').strip()
                return name if name else None
        except Exception:
            pass
        return None

    def _save_last_active(self, chat_name):
        """Persist active chat name for restart recovery. A private active
        chat BLANKS the marker instead (full-scrub ruling F5: no private
        name at rest in a sidecar file) — restart then lands on default,
        which is where boot eviction would put you anyway."""
        marker = self.history_dir / ".active_chat"
        try:
            if self.current_settings.get('private_chat'):
                marker.write_text("", encoding='utf-8')
            else:
                marker.write_text(chat_name, encoding='utf-8')
        except Exception:
            pass

    def set_active_chat(self, chat_name: str) -> bool:
        """Switch to a different chat - loads messages AND settings.

        SWITCH MEANS APPLY (2026-08-22): every True return fires
        `on_switched(name, settings, gen)` — the runtime-apply hook the
        system installs post-plugin-scan — so no caller can switch the
        store and forget the brain. The vault-lock eviction did exactly
        that: the sidebar painted the landing chat's toolset while the FM
        still held the private chat's (Prime, 2026-08-22). Fires on the
        same-chat no-op too: re-activating the active chat is the
        documented user repair. The settings snapshot + generation are
        captured UNDER the lock and handed over — never re-read via
        get_chat_settings(), which is brain-override-aware (a callback on a
        phone-turn thread would stamp the call chat onto the global brain).
        Fired OUTSIDE the lock: the apply takes fm._tools_lock, which a
        plugin scan holds while reaching back into this store — an
        inside-the-lock fire is a real A→B/B→A deadlock, not style. A
        callback failure never fails the switch."""
        if self._vault_hidden(chat_name):
            logger.warning("Chat switch refused — target is sealed in a locked vault")
            return False
        with self._lock:
            if chat_name != self.active_chat_name:
                if self._is_streaming:
                    logger.warning(f"Cannot switch to '{chat_name}' — streaming in progress on '{self.active_chat_name}'")
                    return False

                self._save_current_chat()

                if not self._load_chat(chat_name):
                    logger.error(f"Failed to switch to chat: {chat_name}")
                    return False
                self.active_chat_name = chat_name
                self._save_last_active(chat_name)
                self._in_tool_cycle = False  # Reset tool cycle state on chat switch
                logger.info(f"Switched to chat: {chat_name}")
            self._switch_gen = getattr(self, '_switch_gen', 0) + 1
            gen = self._switch_gen
            snapshot = dict(self.current_settings)
        cb = getattr(self, 'on_switched', None)
        if cb is not None:
            try:
                cb(chat_name, snapshot, gen)
            except Exception as e:
                logger.error(f"on_switched apply failed for '{chat_name}': {e}")
        return True

    def get_active_chat_name(self) -> str:
        """Get active chat name (thread-safe)."""
        with self._lock:
            return self.active_chat_name

    def evict_private_active(self) -> Optional[str]:
        """Vaulted chats Phase 1 (ruling B): if the ACTIVE chat is private
        while the vault is sealed, land on a safe public chat and publish
        the switch. Two callers: prompt_vault.lock() (true-vanish eviction)
        and BOOT — a restart always comes up sealed, and the .active_chat
        marker may point at a private chat (lock-time eviction never ran if
        the app died unlocked; live-caught 2026-08-14). Landing: 'default'
        unless default itself is private, else the freshest non-private
        chat, else a fresh 'scratch' chat. Returns the landing name, or
        None when no eviction was needed or possible."""
        try:
            if not _vault_sealed():
                return None
            chats = self.list_chat_files(include_hidden=True)
            by_name = {c['name']: c for c in chats}
            active = self.get_active_chat_name()
            if not by_name.get(active, {}).get('private_chat'):
                return None
            target = None
            d = by_name.get('default')
            if d is not None and not d.get('private_chat'):
                target = 'default'
            else:
                for c in chats:   # updated_at DESC — freshest non-private wins
                    # mode-tagged chats (game/story/librarian/limbo) are
                    # plugin surfaces — never auto-land someone in one.
                    if not c.get('private_chat') and not c.get('mode') \
                            and c['name'] != active:
                        target = c['name']
                        break
            wipe_landing = False
            if target is None:
                # The backrooms: hidden limbo chat as the terminal landing
                # (Krem's ruling 2026-08-15 — invisible everywhere: the
                # dropdown picker skips mode-tagged chats, Chat Manager
                # filters mode 'limbo', and the display name stays neutral).
                # History is wiped on each landing: things left in the
                # backrooms don't persist.
                br = by_name.get('backrooms')
                if br is not None and br.get('mode') != 'limbo':
                    # An existing USER chat (public, private, or sealed-
                    # unreadable) happens to be named 'backrooms' — only a
                    # chat WE previously stamped mode:limbo is ours to
                    # reclaim and wipe. Anything else: no landing.
                    logger.warning("[VAULT] no safe landing for private active "
                                   "chat — staying (sealed)")
                    return None
                if br is None and not self.create_chat('backrooms'):
                    logger.warning("[VAULT] backrooms creation failed — "
                                   "staying (sealed)")
                    return None
                self.set_named_chat_settings('backrooms', {
                    'mode': 'limbo', 'private_display_name': 'New Chat',
                    'private_chat': False}, touch_updated=False)
                target = 'backrooms'
                wipe_landing = True
            if wipe_landing:
                self.clear_named_chat_messages(target)
            if not self.set_active_chat(target):
                logger.warning("[VAULT] private-active eviction FAILED "
                               "(streaming?) — staying")
                return None
            logger.warning(f"[VAULT] active chat was private — evicted to '{target}'")
            try:
                # Server-initiated switch: origin None → every tab follows
                # (transcript refresh + dropdown adopt). The activate route
                # owns this publish on user-driven switches.
                publish(Events.CHAT_SWITCHED, {"name": target, "origin": None})
            except Exception:
                pass
            return target
        except Exception as e:
            logger.warning(f"[VAULT] private-active eviction failed: {e}")
            return None

    # ── Vaulted-chat migrations (Phase 2, 2026-08-15) ──

    def vault_chat(self, chat_name: str):
        """(ok, err): encrypt a chat IN PLACE — every message row, its
        settings, its tool images — and set vaulted=1. Requires the key.
        Synchronous by ruling ("we want that to be solid if people switch
        back and forth"). Blob chats convert to rows first; the
        conversion_failed latch refuses (surface it — no silent plaintext).
        Scrubs the frozen pre-conversion blob and checkpoints the WAL so
        plaintext frames don't outlive the migration."""
        from core import prompt_vault
        if prompt_vault.chat_data_key() is None:
            return False, "vault locked — unlock to encrypt"
        if chat_name in self._rows_degraded:
            # F2 latch: encrypting a chat with unreadable rows would seal
            # garbage (or double-wrap foreign ciphertext) into the vault.
            return False, (f"'{chat_name}' has unreadable rows — repair it "
                           f"before vaulting")
        try:
            n_rows = 0
            with self._lock, self._get_connection() as conn:
                # Vault hunt G9: without secure_delete, the plaintext OLD page
                # images survive these UPDATEs in the DB file's free space —
                # readable by exactly whom at-rest protection exists for.
                # Per-connection PRAGMA; costs only this migration's writes.
                conn.execute("PRAGMA secure_delete=ON")
                row = conn.execute(
                    "SELECT settings, messages, storage_format, conversion_failed, "
                    "vaulted FROM chats WHERE name = ?", (chat_name,)).fetchone()
                if not row:
                    return False, f"Chat '{chat_name}' not found"
                if row["vaulted"]:
                    return True, ''
                if row["storage_format"] != "rows":
                    if row["conversion_failed"]:
                        return False, (f"'{chat_name}' is latched on blob storage "
                                       f"— cannot encrypt (fix the conversion first)")
                    if not self._convert_chat_to_rows(
                            conn, chat_name, json.loads(row["messages"])):
                        return False, f"blob→rows conversion of '{chat_name}' failed"
                enc_rows = []
                for r in conn.execute(
                        "SELECT seq, message_json FROM chat_messages "
                        "WHERE chat_name = ?", (chat_name,)):
                    if not prompt_vault.is_chat_encrypted(r["message_json"]):
                        enc_rows.append((self._enc_value(r["message_json"]),
                                         chat_name, r["seq"]))
                conn.executemany(
                    "UPDATE chat_messages SET message_json = ? "
                    "WHERE chat_name = ? AND seq = ?", enc_rows)
                n_rows = len(enc_rows)
                s = self._settings_dict(row["settings"], chat_name)
                if s is None:
                    return False, f"settings of '{chat_name}' unreadable"
                # messages='[]' scrubs the frozen pre-conversion blob (ruling
                # 9 class): a recovery copy must not outlive the encryption.
                conn.execute(
                    "UPDATE chats SET settings = ?, messages = '[]', vaulted = 1 "
                    "WHERE name = ?",
                    (self._enc_value(json.dumps(s)), chat_name))
                # Vault hunt G10: collect-then-executemany (the message-row
                # pattern above) — SELECT-while-UPDATE on one table lets
                # SQLite skip rows under concurrent modification, and a
                # skipped plaintext image is never re-scanned (the vaulted=1
                # early-out above).
                enc_imgs = []
                for r in conn.execute(
                        "SELECT id, data FROM tool_images WHERE chat_name = ?",
                        (chat_name,)):
                    if not prompt_vault.is_chat_encrypted(r["data"]):
                        enc_imgs.append((self._enc_value_bytes(r["data"]), r["id"]))
                conn.executemany(
                    "UPDATE tool_images SET data = ? WHERE id = ?", enc_imgs)
                # v1.3: chat-scoped plugin data rides the same flip. rowid-
                # keyed — the 4-column PK is clumsy in an executemany WHERE.
                enc_pcd = []
                for r in conn.execute(
                        "SELECT rowid, value FROM plugin_chat_data "
                        "WHERE chat_name = ?", (chat_name,)):
                    if not prompt_vault.is_chat_encrypted(r["value"]):
                        enc_pcd.append((self._enc_value(r["value"]), r["rowid"]))
                conn.executemany(
                    "UPDATE plugin_chat_data SET value = ? WHERE rowid = ?", enc_pcd)
                conn.commit()
            self._scrub_wal()
            self._scrub_public_deposits(chat_name)
            # T8: vault-labeled log lines never carry the name — writing
            # "chat 'X' encrypted" records X as secret at the exact moment
            # it becomes one (and logs are plaintext at rest for 30 days).
            logger.info(f"[VAULT] chat encrypted ({n_rows} rows + settings + images)")
            return True, ''
        except Exception as e:
            logger.error(f"vault_chat failed: {e}")
            return False, str(e)

    def _scrub_public_deposits(self, chat_name: str):
        """Full-scrub (ruling F5, 2026-08-15): a chat that just went private
        also cleans the plaintext deposits it left while public. Best-effort
        — the encryption itself already committed. Covers: stale compress
        exports (the one CONTENT tunnel), token-metrics rows (name+counts+
        timestamps), the .active_chat marker, and — via the chat_vaulted
        hook — plugin-side deposits (mindpalace provenance)."""
        try:
            exports_dir = self.history_dir / "exports"
            # Exact-stamp match so chat 'email' never eats an export belonging
            # to chat 'email_backup'.
            pat = re.compile(re.escape(chat_name) + r"_\d{8}_\d{6}\.json$")
            if exports_dir.is_dir():
                for f in exports_dir.iterdir():
                    if pat.fullmatch(f.name):
                        try:
                            f.unlink()
                            logger.info("[VAULT] scrubbed a stale plaintext export")
                        except OSError:
                            # Windows: an AV/indexer handle can refuse the
                            # delete. Retry once, then say PLAINTEXT REMAINS
                            # at ERROR — never report a seal over a survivor.
                            time.sleep(0.2)
                            try:
                                f.unlink()
                                logger.info("[VAULT] scrubbed a stale plaintext export (retry)")
                            except OSError as e2:
                                logger.error(f"[VAULT] PLAINTEXT EXPORT REMAINS "
                                             f"at {f} — delete it manually: {e2}")
        except Exception as e:
            logger.error(f"[VAULT] export scrub failed — plaintext exports may remain: {e}")
        try:
            from core.metrics import metrics as token_metrics
            token_metrics.scrub_chat(chat_name)
        except Exception as e:
            logger.warning(f"[VAULT] metrics scrub failed: {e}")
        try:
            marker = self.history_dir / ".active_chat"
            if marker.exists() and marker.read_text(encoding='utf-8').strip() == chat_name:
                marker.write_text("", encoding='utf-8')
        except Exception:
            pass
        try:
            from core.hooks import hook_runner, HookEvent
            hook_runner.fire("chat_vaulted", HookEvent(
                metadata={"name": chat_name}, chat_name=chat_name,
                chat_private=True))
        except Exception as e:
            logger.warning(f"[VAULT] chat_vaulted hook failed: {e}")

    def unvault_chat(self, chat_name: str):
        """(ok, err): decrypt a vaulted chat back to plaintext (Chat Manager
        🔓 — 'the honest meaning of the icon'). Requires the key. STRICT:
        any undecryptable piece aborts the whole transaction — a public chat
        never sits with half-encrypted rows."""
        from core import prompt_vault
        if prompt_vault.chat_data_key() is None:
            return False, "vault locked — unlock to decrypt"
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute(
                    "SELECT settings, vaulted FROM chats WHERE name = ?",
                    (chat_name,)).fetchone()
                if not row:
                    return False, f"Chat '{chat_name}' not found"
                if not row["vaulted"]:
                    return True, ''
                dec_rows = []
                for r in conn.execute(
                        "SELECT seq, message_json FROM chat_messages "
                        "WHERE chat_name = ?", (chat_name,)):
                    v = r["message_json"]
                    if prompt_vault.is_chat_encrypted(v):
                        d = self._dec_value(v, "unvault row", chat_name)
                        if d is None:
                            return False, f"row {r['seq']} undecryptable — aborted"
                        dec_rows.append((d, chat_name, r["seq"]))
                conn.executemany(
                    "UPDATE chat_messages SET message_json = ? "
                    "WHERE chat_name = ? AND seq = ?", dec_rows)
                s = self._settings_dict(row["settings"], chat_name)
                if s is None:
                    return False, f"settings of '{chat_name}' undecryptable — aborted"
                conn.execute(
                    "UPDATE chats SET settings = ?, vaulted = 0 WHERE name = ?",
                    (json.dumps(s), chat_name))
                # Vault hunt G10 twin: collect-then-executemany — a skipped
                # still-encrypted image in a public chat would never be
                # re-scanned (vaulted=0 early-out on the seal side).
                dec_imgs = []
                for r in conn.execute(
                        "SELECT id, data FROM tool_images WHERE chat_name = ?",
                        (chat_name,)):
                    if prompt_vault.is_chat_encrypted(r["data"]):
                        d = prompt_vault.decrypt_chat_blob(r["data"])
                        if d is None:
                            return False, f"image {r['id']} undecryptable — aborted"
                        dec_imgs.append((d, r["id"]))
                conn.executemany(
                    "UPDATE tool_images SET data = ? WHERE id = ?", dec_imgs)
                # v1.3 twin: strict — a half-decrypted journal is a forked
                # playthrough, so any unreadable row aborts the whole flip.
                dec_pcd = []
                for r in conn.execute(
                        "SELECT rowid, value FROM plugin_chat_data "
                        "WHERE chat_name = ?", (chat_name,)):
                    if prompt_vault.is_chat_encrypted(r["value"]):
                        d = self._dec_value(r["value"], "unvault plugin data", chat_name)
                        if d is None:
                            return False, "plugin data row undecryptable — aborted"
                        dec_pcd.append((d, r["rowid"]))
                conn.executemany(
                    "UPDATE plugin_chat_data SET value = ? WHERE rowid = ?", dec_pcd)
                conn.commit()
            logger.info("[VAULT] chat decrypted back to plaintext")
            return True, ''
        except Exception as e:
            logger.error(f"unvault_chat failed: {e}")
            return False, str(e)

    def vault_pending_private(self) -> int:
        """Encrypt every private-flagged chat still at vaulted=0 — the
        pre-encryption legacy, or an earlier pass that failed. Also heals the
        inverse stranding (vaulted=1 with the private flag OFF — a raced flip
        left seal and flag disagreeing; the FLAG is truth everywhere else, so
        decrypt back to public). Runs at every unlock (self-healing ratchet).
        Returns the count encrypted."""
        self._ensure_db()
        pending, stranded = [], []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT name, settings, vaulted FROM chats").fetchall()
            # Vault hunt G5: parsed in PYTHON through the settings funnel.
            # The old SQL json_extract raised on any `@enc1:` settings value
            # (seed-defect family) and ONE mixed row killed the whole sweep
            # forever — and SQLite doesn't guarantee left-to-right AND order,
            # so the vaulted=0 term was no protection.
            for r in rows:
                s = self._settings_dict(r["settings"], r["name"])
                if not r["vaulted"] and s and s.get("private_chat"):
                    pending.append(r["name"])
                elif r["vaulted"] and s and not s.get("private_chat"):
                    # G6 inverse: unreadable settings (s is None) are skipped
                    # — never heal what can't be verified.
                    stranded.append(r["name"])
        except Exception as e:
            logger.warning(f"pending-private scan failed: {e}")
            return 0
        n = 0
        for name in pending:
            ok, err = self.vault_chat(name)
            if ok:
                n += 1
            else:
                logger.warning(f"[VAULT] deferred encrypt of a pending chat failed: {err}")
        for name in stranded:
            ok, err = self.unvault_chat(name)
            if ok:
                logger.warning("[VAULT] healed a raced flip — decrypted a "
                               "sealed chat whose flag said public")
            else:
                logger.warning(f"[VAULT] raced-flip heal failed: {err}")
        if n:
            logger.info(f"[VAULT] encrypted {n} pending private chat(s) at unlock")
        return n

    def _scrub_wal(self):
        """After an encrypt migration: plaintext lingers in WAL frames and
        freed pages. TRUNCATE checkpoint folds + trims the WAL; incremental
        vacuum returns freed pages. Best-effort — never fails a migration."""
        try:
            with self._get_connection() as conn:
                # Vault hunt G8: wal_checkpoint reports failure by RESULT ROW
                # (busy, log, checkpointed) — it never raises. busy=1 (an open
                # reader) means plaintext frames silently survive the seal;
                # retry once after a beat, then say so loudly.
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if row and row[0]:
                    time.sleep(0.25)
                    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if row and row[0]:
                    logger.error("[VAULT] WAL checkpoint stayed busy — pre-seal "
                                 "frames may persist until the next checkpoint")
                conn.execute("PRAGMA incremental_vacuum")
                conn.commit()
        except Exception as e:
            logger.warning(f"post-migration WAL scrub failed: {e}")

    def add_user_message(self, content: Union[str, List[Dict[str, Any]]], persona: Optional[str] = None):
        if persona is None:
            persona = self.get_chat_settings().get("persona")
        with self._lock:
            self._effective_chat().add_user_message(content, persona=persona)
            self._save_current_chat()
        publish(Events.MESSAGE_ADDED, {"role": "user"})

    def add_assistant_with_tool_calls(
        self,
        content: Optional[str],
        tool_calls: List[Dict],
        thinking: Optional[str] = None,
        thinking_raw: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None
    ):
        """Add assistant message with tool calls. Marks start of tool cycle."""
        self._in_tool_cycle = True
        persona = self.get_chat_settings().get("persona")
        with self._lock:
            self._effective_chat().add_assistant_with_tool_calls(
                content, tool_calls, thinking, thinking_raw, metadata, persona=persona
            )
            self._save_current_chat()

    def add_tool_result(self, tool_call_id: str, name: str, content: str, inputs: Optional[Dict] = None):
        with self._lock:
            self._effective_chat().add_tool_result(tool_call_id, name, content, inputs)
            self._save_current_chat()

    def add_assistant_final(
        self,
        content: str,
        thinking: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Add final assistant message. Ends tool cycle and clears thinking_raw."""
        persona = self.get_chat_settings().get("persona")
        with self._lock:
            eff = self._effective_chat()
            eff.add_assistant_final(content, thinking, metadata, persona=persona)

            # Tool cycle complete - clear thinking_raw from previous messages
            if self._in_tool_cycle:
                eff.clear_thinking_raw()
                self._in_tool_cycle = False

            self._save_current_chat()
        publish(Events.MESSAGE_ADDED, {"role": "assistant"})

    def add_message_pair(self, user_content: str, assistant_content: str):
        with self._lock:
            self._effective_chat().add_message_pair(user_content, assistant_content)
            self._save_current_chat()
        publish(Events.MESSAGE_ADDED, {"role": "pair"})

    def get_messages(self) -> List[Dict[str, str]]:
        """Get raw messages (for storage/debugging)."""
        return self.current_chat.get_messages()

    def get_messages_for_display(self) -> List[Dict[str, Any]]:
        """Get messages formatted for UI with <think> tags reconstructed."""
        return self.current_chat.get_messages_for_display()

    # ── Per-stream session (A1) — a conversation stream on a non-active chat ──
    #  routes reads/writes to ITS chat, leaving the active-chat singletons alone,
    #  so a phone call runs in its own chat concurrently with the web UI.
    def _effective_chat(self):
        """The ConversationHistory this turn operates on: a per-context stream
        override's history if set, else the active-chat singleton."""
        try:
            from core.chat.stream_brain import get_override
            o = get_override()
            if o and o.get("history") is not None:
                return o["history"]
        except Exception:
            pass
        return self.current_chat

    def _effective_chat_name(self) -> str:
        try:
            from core.chat.stream_brain import get_override
            o = get_override()
            if o and o.get("chat"):
                return o["chat"]
        except Exception:
            pass
        return self.active_chat_name

    @property
    def _in_tool_cycle(self) -> bool:
        """Tool-cycle state for THIS turn's chat. Proxies to the effective chat's
        history so a phone call and the web UI don't share one flag (which would
        drop Claude's thinking_raw mid-cycle → provider 400). Every existing call
        site — including the streaming-cleanup finally — reads through here."""
        ch = self._effective_chat()
        return getattr(ch, "_in_tool_cycle", False) if ch is not None else False

    @_in_tool_cycle.setter
    def _in_tool_cycle(self, value: bool):
        ch = self._effective_chat()
        if ch is not None:
            ch._in_tool_cycle = bool(value)

    def make_stream_session(self, chat_name: str) -> Optional[Dict[str, Any]]:
        """Build a per-context stream session for `chat_name`: its settings + a
        ConversationHistory seeded from its stored messages. system_prompt/tools
        are filled by the caller (the A1 override block in chat_streaming). None if missing."""
        settings = self.read_chat_settings(chat_name)
        if settings is None:
            return None
        hist = ConversationHistory(max_history=self.max_history)
        hist.messages = self.read_chat_messages(chat_name) or []
        return {"chat": chat_name, "settings": settings,
                "system_prompt": None, "tools": None, "history": hist}

    def make_agent_override(self, chat_name: str,
                            privacy_required: bool = False) -> Dict[str, Any]:
        """Complete stream-brain override for a background worker bound to
        `chat_name`. ALWAYS a full carrier — an override with 'chat' but no
        'history' makes _effective_chat() and _effective_chat_name()
        disagree (the cross-chat clobber class closed 2026-08-17), and a
        settings stub starves every get_chat_settings() consumer on the
        worker thread (privacy-ratchet bypass, extras decay). Privacy
        ratchets UP only: the caller's snapshot can add private_chat, never
        remove it (the spawning chat may be hidden by run time). Unreadable
        chat (sealed/missing) → empty seed; saves on a sealed chat raise
        before touching rows, so the empty seed can't wipe anything."""
        settings = None
        try:
            settings = self.read_chat_settings(chat_name)
        except Exception:
            pass
        settings = dict(settings) if settings else {}
        settings["private_chat"] = bool(privacy_required
                                        or settings.get("private_chat"))
        hist = ConversationHistory(max_history=self.max_history)
        try:
            hist.messages = self.read_chat_messages(chat_name) or []
        except Exception:
            hist.messages = []
        return {"chat": chat_name, "settings": settings,
                "system_prompt": "", "tools": None, "history": hist}

    def get_messages_for_llm(self, reserved_tokens: int = 0, provider: str = None) -> List[Dict[str, str]]:
        """Get messages for LLM with trimming applied."""
        return self._effective_chat().get_messages_for_llm(
            reserved_tokens,
            provider=provider,
            in_tool_cycle=self._in_tool_cycle
        )

    def get_turn_count(self) -> int:
        return self._effective_chat().get_turn_count()

    def read_chat_settings(self, chat_name: str) -> Optional[Dict[str, Any]]:
        """Read a chat's settings from SQLite WITHOUT switching active chat.
        Returns None if the chat doesn't exist. Applies system defaults
        on top of the stored settings so callers get a complete view.

        This replaces a legacy JSON-file path that no longer exists post
        SQLite migration — the old route code was silently 404'ing every
        non-active chat because the JSON file it expected was never
        written. Silent-default class bug (2026-04-19)."""
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT settings FROM chats WHERE name = ?", (chat_name,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                # Vaulted+sealed settings decrypt to None → absent, which IS
                # the Phase 1 contract (encryption made it physics).
                stored = self._settings_dict(row["settings"], chat_name)
                if stored is None:
                    return None
                # Legacy pre-encryption private chats (vaulted=0, plaintext
                # settings): the Phase 1 policy gate still applies. Active
                # chat exempt (privacy gates read it to ENFORCE local-only).
                if stored.get("private_chat") and chat_name != self.active_chat_name \
                        and _vault_sealed():
                    return None
                merged = get_system_defaults()
                merged.update(stored)
                return merged
        except Exception as e:
            logger.error(f"Failed to read settings for chat '{chat_name}': {e}")
            return None

    def read_chat_messages(self, chat_name: str, provider: str = None,
                           context_limit: int = None) -> List[Dict[str, Any]]:
        """Read messages from a named chat WITHOUT switching active chat.
        context_limit overrides the global trim budget for this read (the
        librarian's 128K night sessions)."""
        if self._vault_hidden(chat_name):
            return []   # sealed vault: behaves as nonexistent
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT messages, storage_format FROM chats WHERE name = ?", (chat_name,)
                )
                row = cursor.fetchone()
                if not row:
                    return []
                if row["storage_format"] == "rows":
                    # Transient read — skip_info ignored; the latch belongs
                    # to activation (_load_chat).
                    messages, _skips = self._read_rows_messages(conn, chat_name)
                else:
                    messages = json.loads(row["messages"])
                # Apply same trimming as get_messages_for_llm
                chat = ConversationHistory()
                chat.messages = messages
                return chat.get_messages_for_llm(provider=provider,
                                                 context_limit=context_limit)
        except Exception as e:
            logger.error(f"Failed to read chat '{chat_name}': {e}")
            return []

    def append_to_chat(self, chat_name: str, user_content: str, assistant_content: str):
        """Append a simple message pair to a named chat WITHOUT switching active chat."""
        self.append_messages_to_chat(chat_name, [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ])

    def append_messages_to_chat(self, chat_name: str, new_messages: list,
                                 max_wait_if_streaming: float = 60.0) -> bool:
        """Append a list of messages to a named chat WITHOUT switching active chat.

        Preserves the full conversation structure including tool_calls and tool
        results. Each message gets a timestamp if it doesn't already have one.

        If the target chat is the ACTIVE chat and a stream is in progress, wait
        for the stream to finish before appending. Scout 2 finding (2026-04-19):
        writing while the stream is mid-flight can interleave cron messages
        between a tool_call and its tool_result (breaks LLM conversation
        validity) OR result in a subsequent per-message save overwriting the
        cron write with a stale in-memory snapshot. The `_is_streaming` guard
        already protects `delete_chat` and `set_active_chat` — extending it
        here closes the asymmetry.

        Returns True if the messages were written, False if the wait timed out
        and the write was skipped to avoid corruption. Pre-2026-05-07 the wait
        used 200ms polling and fell through after 15s, writing anyway and
        risking interleaved tool_call/tool_result corruption — voice mode's
        short turn cadence made that path the common case. Now an Event
        signals stream end immediately, the wait window extended to 60s,
        and the write is SKIPPED on timeout (data loss > corruption).
        """
        self._ensure_db()

        if self._vault_hidden(chat_name):
            # Sealed vault: a background writer must not touch (or reveal) a
            # hidden chat. Loud — this is the cron/daemon write path.
            logger.warning("append refused — target chat is sealed in a locked vault")
            return False

        if chat_name in self._rows_degraded:
            # F2 latch: appending after unreadable rows interleaves new
            # content into a corrupt region — and the next full resync would
            # destroy the salvage. Read-only until repaired.
            logger.warning(f"append refused — chat '{chat_name}' is degraded "
                           f"(read-only until repaired)")
            return False

        # Defer if the target is the active chat and a stream is running.
        # Event-based wait — fires as soon as the last stream ends, no poll.
        if chat_name == self.active_chat_name and self._is_streaming:
            evt = getattr(self, '_no_streams_event', None)
            if evt is not None:
                got = evt.wait(timeout=max_wait_if_streaming)
            else:
                # Legacy fallback for fixtures that bypass __init__.
                import time as _time
                deadline = _time.time() + max_wait_if_streaming
                while self._is_streaming and _time.time() < deadline:
                    _time.sleep(0.2)
                got = not self._is_streaming
            if not got or self._is_streaming:
                # Write SKIPPED. Better to drop a heartbeat append than to
                # write through and corrupt the chat's tool_call/tool_result
                # invariants. Caller (heartbeat / cron / agent completion)
                # should treat False as "your write didn't happen" — current
                # callers ignore the return, which means the heartbeat output
                # is lost in this rare case. Acceptable trade vs corruption.
                logger.error(
                    f"append_messages_to_chat('{chat_name}') gave up after "
                    f"{max_wait_if_streaming:.0f}s waiting for active stream "
                    f"to end — write SKIPPED to avoid history corruption. "
                    f"{len(new_messages)} message(s) dropped."
                )
                return False

        timestamp = datetime.now().isoformat()
        try:
            with self._lock, self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT messages, storage_format, conversion_failed FROM chats WHERE name = ?",
                    (chat_name,)
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Chat '{chat_name}' not found — skipping append (may have been deleted)")
                    return False

                for msg in new_messages:
                    if 'timestamp' not in msg:
                        msg['timestamp'] = timestamp

                storage_format = row["storage_format"]
                # Rowify step 3: non-active blob chats lazily convert on THIS
                # write path (the active chat converts at end_streaming, but
                # converting here too is safe — we're past the stream wait,
                # under the lock). Latched chats stay blob.
                if storage_format == "blob" and not row["conversion_failed"]:
                    try:
                        self._ensure_pre_rowify_snapshot()
                        if self._convert_chat_to_rows(
                                conn, chat_name, json.loads(row["messages"])):
                            storage_format = "rows"
                    except Exception as e:
                        logger.warning(f"append-path conversion of '{chat_name}' skipped: {e}")

                if storage_format == "rows":
                    # Stateless O(1) append: next seq straight from the store
                    # (no watermark needed for non-active chats).
                    _vaulted = self._is_vaulted_conn(conn, chat_name)
                    base = conn.execute(
                        "SELECT COALESCE(MAX(seq) + 1, 0) FROM chat_messages "
                        "WHERE chat_name = ?", (chat_name,)
                    ).fetchone()[0]
                    conn.executemany(
                        "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                        "VALUES (?, ?, ?, ?)",
                        [(chat_name, base + i, m.get("role"), self._row_payload(_vaulted, m))
                         for i, m in enumerate(new_messages)])
                    conn.execute(
                        "UPDATE chats SET updated_at = ? WHERE name = ?",
                        (timestamp, chat_name))
                    conn.commit()
                else:
                    messages = json.loads(row["messages"])
                    messages.extend(new_messages)
                    result = conn.execute(
                        """UPDATE chats SET messages = ?, updated_at = ? WHERE name = ?""",
                        (json.dumps(messages), timestamp, chat_name)
                    )
                    conn.commit()
                    if result.rowcount == 0:
                        logger.warning(f"Chat '{chat_name}' was deleted during append — messages lost")
                logger.debug(f"Appended {len(new_messages)} messages to chat '{chat_name}'")

                # If this is the active chat, sync in-memory list AND the
                # rows watermark (these rows are already persisted — without
                # the count bump the next foreground save would re-INSERT
                # them at colliding seqs).
                if chat_name == self.active_chat_name:
                    for msg in new_messages:
                        self.current_chat.messages.append(msg)
                    if storage_format == "rows":
                        state = self._rows_state.get(chat_name)
                        if state is not None and state["offset"] + state["count"] == base:
                            state["count"] += len(new_messages)
                        else:
                            # Drift between watermark and store — heal via
                            # full resync on the next save instead of guessing.
                            logger.warning(
                                f"Rows watermark drift on '{chat_name}' "
                                f"(state={state}, base={base}) — flagging resync")
                            self.current_chat._needs_full_resync = True

                publish(Events.MESSAGE_ADDED, {"role": "pair", "chat_name": chat_name})
                return True
        except Exception as e:
            logger.error(f"Failed to append to chat '{chat_name}': {e}")
            return False

    def remove_last_messages(self, count: int) -> bool:
        result = self.current_chat.remove_last_messages(count)
        if result:
            self._save_current_chat()
            self._prune_orphaned_tool_images(self.active_chat_name)
            publish(Events.MESSAGE_REMOVED, {"count": count})
        return result

    def remove_from_user_message(self, user_content: str) -> bool:
        result = self.current_chat.remove_from_user_message(user_content)
        if result:
            self._save_current_chat()
            self._prune_orphaned_tool_images(self.active_chat_name)
            publish(Events.MESSAGE_REMOVED, {"from": "user_message"})
        return result

    def remove_from_assistant_timestamp(self, timestamp: str) -> bool:
        result = self.current_chat.remove_from_assistant_timestamp(timestamp)
        if result:
            self._save_current_chat()
            self._prune_orphaned_tool_images(self.active_chat_name)
            publish(Events.MESSAGE_REMOVED, {"from": "assistant_timestamp"})
        return result

    def remove_tool_call(self, tool_call_id: str) -> bool:
        """Remove a specific tool call and its result from history."""
        result = self.current_chat.remove_tool_call(tool_call_id)
        if result:
            self._save_current_chat()
            self._prune_orphaned_tool_images(self.active_chat_name)
            publish(Events.MESSAGE_REMOVED, {"tool_call_id": tool_call_id})
        return result

    def _prune_orphaned_tool_images(self, chat_name: str) -> int:
        """Delete tool_images rows for this chat whose IDs are no longer
        referenced by any message content. Called after any message-removal
        path. Without this, image blobs accumulate forever (Scout 1 finding
        2026-04-19: DB bloat at 100KB–2MB per image × heavy-use chats).
        Returns count of rows deleted.
        """
        import re
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute(
                    "SELECT messages, storage_format FROM chats WHERE name = ?", (chat_name,)
                ).fetchone()
                if not row:
                    return 0
                # Live IDs come from the AUTHORITATIVE store (rowify
                # correction #5): for a rows chat the blob is '[]'/frozen —
                # scanning it would report zero live markers and delete
                # images that are still referenced (silent data loss; golden
                # master T3 guards this exact regression).
                if row["storage_format"] == "rows":
                    live_ids = set()
                    for r in conn.execute(
                            "SELECT message_json FROM chat_messages WHERE chat_name = ?",
                            (chat_name,)):
                        v = r["message_json"]
                        # Vaulted rows hide their <<IMG>> markers behind the
                        # cipher — an undecryptable row means we CANNOT prove
                        # any image is orphaned. Skip the whole prune rather
                        # than delete live images (three-laws: her data > our
                        # tidiness). Retries on the next unlocked prune.
                        from core import prompt_vault as _pv
                        if _pv.is_chat_encrypted(v):
                            dec = self._dec_value(v, "prune scan row", chat_name)
                            if dec is None:
                                logger.info(f"prune of '{chat_name}' skipped — "
                                            f"vaulted rows unreadable (sealed)")
                                return 0
                            v = dec
                        live_ids.update(re.findall(r'<<IMG::tool:([^>]+)>>', v))
                else:
                    msgs_blob = row["messages"] or "[]"
                    # Extract all live IMG IDs from message content
                    live_ids = set(re.findall(r'<<IMG::tool:([^>]+)>>', msgs_blob))
                # Find stored image IDs for this chat that aren't in live_ids
                stored = conn.execute(
                    "SELECT id FROM tool_images WHERE chat_name = ?", (chat_name,)
                ).fetchall()
                orphans = [r["id"] for r in stored if r["id"] not in live_ids]
                if orphans:
                    placeholders = ','.join('?' * len(orphans))
                    conn.execute(
                        f"DELETE FROM tool_images WHERE chat_name = ? AND id IN ({placeholders})",
                        (chat_name, *orphans),
                    )
                    conn.commit()
                    # Reclaim freed pages from the BLOB deletes — see
                    # delete_chat for the rationale. Wildcard scout 2026-05-07 L1.
                    try:
                        conn.execute("PRAGMA incremental_vacuum(100)")
                        conn.commit()
                    except Exception:
                        pass
                    logger.debug(
                        f"Pruned {len(orphans)} orphan tool_image(s) from chat '{chat_name}'"
                    )
                return len(orphans)
        except Exception as e:
            logger.warning(f"orphan tool_image prune failed for '{chat_name}': {e}")
            return 0

    def clear(self):
        # A1: clear the EFFECTIVE chat (a per-stream override's chat, else the
        # active one). Without this, reset_chat from a phone call or a background
        # conversation wiped the operator's active WEB chat instead of the call's.
        # ONE lock hold for the whole wipe (race scout 2026-07-09 #1: the
        # unlocked gap let a concurrent continuity append survive a privacy
        # clear). RLock — the nested _save_current_chat lock re-enters fine.
        with self._lock:
            eff_chat = self._effective_chat()
            eff_name = self._effective_chat_name()
            eff_chat.clear()
            eff_chat._in_tool_cycle = False
            # Clear means WIPE, not window-resync: reset the watermark offset to 0
            # so the resync deletes every row — including rows below a capped-load
            # offset that aren't in memory. Privacy lever must be total.
            self._rows_state[eff_name] = {"offset": 0, "count": 0}
            # F2 latch escape hatch: clearing a degraded chat is an explicit
            # discard — unlatch first or the save below would be refused.
            self._rows_degraded.pop(eff_name, None)
            self._degraded_toasted.discard(eff_name)
            self._save_current_chat()  # already routes to the effective chat

            # Clear tool images for the effective chat
            try:
                with self._get_connection() as conn:
                    conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (eff_name,))
                    conn.commit()
            except Exception:
                pass  # Table may not exist yet

        publish(Events.CHAT_CLEARED, {"chat_name": eff_name})
        try:
            from core.hooks import hook_runner, HookEvent
            if hook_runner.has_handlers("chat_cleared"):
                # Pre-stamped: the fallback resolver reads the ACTIVE chat,
                # which under an override is not the one being cleared.
                hook_runner.fire("chat_cleared",
                                 HookEvent(metadata={"chat": eff_name},
                                           chat_name=eff_name))
        except Exception as e:
            logger.warning(f"chat_cleared hook dispatch failed: {e}")

    def edit_message_by_content(self, role: str, original_content: str, new_content: str) -> bool:
        """Edit message and save."""
        result = self.current_chat.edit_message_by_content(role, original_content, new_content)
        if result:
            self._save_current_chat()
        return result

    def get_chat_settings(self) -> Dict[str, Any]:
        """Get the settings that govern THIS turn's brain. Normally the active
        chat's settings; if a per-stream brain override is set for this execution
        context (a conversation stream on a non-active chat), that chat's settings.
        Unset (the web-UI common case) → active settings, byte-identical to before."""
        try:
            from core.chat.stream_brain import get_override
            o = get_override()
            if o and o.get("settings") is not None:
                return dict(o["settings"])
        except Exception:
            pass
        return self.current_settings.copy()

    def metrics_chat_label(self) -> str:
        """Chat label for the token-metrics deposit. token_usage.db is
        plaintext at rest and outlives the vault, and per-chat
        name+counts+timestamps are exactly what the lock hides — so private
        chats deposit as '__private__' (totals/summaries unaffected; nothing
        groups by chat today). Fail-closed: an unreadable answer masks."""
        try:
            if self.get_chat_settings().get('private_chat'):
                return '__private__'
            return self._effective_chat_name()
        except Exception:
            return '__private__'

    def get_settings_for(self, chat_name: str) -> Optional[Dict[str, Any]]:
        """Read a specific chat's settings from disk (does NOT activate it).
        Returns None if the chat doesn't exist."""
        if chat_name and chat_name == self.active_chat_name:
            return self.current_settings.copy()
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT settings FROM chats WHERE name = ?", (chat_name,)).fetchone()
            if not row:
                return None
            s = self._settings_dict(row['settings'], chat_name)
            if s is None:
                return None   # vaulted + sealed = absent (Phase 2 physics)
            # Legacy plaintext private chats: Phase 1 policy gate.
            if s.get('private_chat') and _vault_sealed():
                return None
            return s
        except Exception as e:
            logger.error(f"get_settings_for('{chat_name}') failed: {e}")
            return None

    def update_chat_settings(self, settings: Dict[str, Any],
                             expected_active: Optional[str] = None) -> bool:
        """Update the EFFECTIVE chat's settings and save. Normally the active chat;
        under a per-stream brain override (phone call / background conversation) the
        stream's own chat — so an AI switch_model/switch_toolset/set_voice writes to
        the chat it is actually running in, not the operator's active web chat.

        expected_active (vault hunt R5, 2026-08-15): the chat the CALLER meant
        to write. If a lock's eviction retargeted the active chat between the
        caller's check and this write, refuse — a sidebar payload must never
        merge into the eviction landing chat."""
        try:
            # Divert on the override's CHAT NAME, not history-object identity.
            # The continuity executor's override used to ship without a
            # 'history' key, so `_effective_chat() is current_chat` held even
            # mid-task and a tool's settings write fell through to the
            # operator's active chat — then _save_current_chat persisted the
            # ACTIVE chat's settings+messages under the override NAME
            # (clobbered trinity with default, 2026-08-17). Name presence is
            # the real "am I in someone else's turn" signal.
            _ov = None
            try:
                from core.chat.stream_brain import get_override
                _ov = get_override()
            except Exception:
                pass
            if _ov and _ov.get("chat"):
                # Override active — merge into the stream's own chat via a direct DB
                # write, and keep this turn's in-memory snapshot in sync.
                # set_named_chat_settings also mirrors into current_settings
                # when the override happens to target the active chat.
                eff_name = _ov["chat"]
                # R5 intent holds here too: a caller that named its target
                # must not silently write a different chat.
                if expected_active is not None and expected_active != eff_name:
                    logger.warning(
                        f"[VAULT] settings write refused — caller expected "
                        f"'{expected_active}' but a stream override targets "
                        f"'{eff_name}'")
                    return False
                ok = self.set_named_chat_settings(eff_name, settings)
                if ok:
                    if _ov.get("settings") is not None:
                        _ov["settings"].update(settings)
                    logger.info(f"Updated settings for override chat '{eff_name}'")
                return ok
            # Vault hunt R5/G6: the whole read-decide-write-seal runs under
            # the lock so a timer-thread eviction (which switches chats under
            # the same lock) can't rebind current_settings mid-body.
            with self._lock:
                if expected_active is not None \
                        and expected_active != self.active_chat_name:
                    logger.warning("[VAULT] settings write refused — active "
                                   "chat changed since the caller checked "
                                   "(eviction?)")
                    return False
                was_priv = bool(self.current_settings.get('private_chat'))
                new_priv = bool(settings.get('private_chat', was_priv))
                # Ruling F1 refusal LIFTED (v1.3, 2026-08-15): game/story
                # chats may be private now — their journals/saves live in
                # plugin_chat_data and seal with the chat.
                if new_priv and not was_priv and _vault_sealed():
                    # Vault hunt R4: the talk-stamp raced a seal. A locked
                    # vault has no private mode — refusing here beats leaving
                    # a plaintext chat wearing a private flag in a sealed
                    # world. The stamp simply re-fires next unlocked turn.
                    logger.warning("[VAULT] refused private flip — vault sealed")
                    return False
                if was_priv and not new_priv:
                    # Unflip: decrypt BEFORE the flag drops — no-op for legacy
                    # unvaulted private chats; refusal refuses the write.
                    ok, err = self.unvault_chat(self.active_chat_name)
                    if not ok:
                        logger.warning(f"[VAULT] decrypt of active chat refused "
                                       f"the settings write: {err}")
                        return False
                old_prompt = self.current_settings.get('prompt') if 'prompt' in settings else None
                self.current_settings.update(settings)
                if not self._save_current_chat():
                    # In-memory update stands (the resync latch replays it on
                    # the next good save) but the caller must hear the truth.
                    return False
                logger.info(f"Updated settings for chat '{self.active_chat_name}'")
                if 'prompt' in settings:
                    _vault_ref_sync(settings.get('prompt'), old_prompt)
                if new_priv and not was_priv:
                    # Flip to private (the talk-stamp lands here): encrypt NOW —
                    # synchronous by ruling. Failure leaves the flag; Phase 1
                    # hiding holds and the unlock sweep retries.
                    ok, err = self.vault_chat(self.active_chat_name)
                    if not ok:
                        logger.warning(f"[VAULT] encrypt of active chat deferred: {err}")
                return True
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return False

    def set_named_chat_settings(self, chat_name: str, patch: Dict[str, Any],
                                touch_updated: bool = True) -> bool:
        """Merge `patch` into a SPECIFIC (possibly non-active) chat's settings via a
        direct DB write. Lets a realtime daemon (Twilio) configure a call chat it
        never activates. Mirrors in-memory settings if it IS the active chat.

        touch_updated=False for pure-metadata patches (archive flag): updated_at
        feeds the Manager's 'Last active' sort and its 'Older than N days' bulk
        selector — bumping it for a shade toggle blinds both."""
        self._ensure_db()
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute(
                    "SELECT settings, vaulted FROM chats WHERE name = ?",
                    (chat_name,)).fetchone()
                if not row:
                    return False
                # Funnel, NOT bare json.loads: a vaulted chat's ciphertext
                # would parse-fail to {} and this write would clobber the
                # encrypted settings with a plaintext partial.
                s = self._settings_dict(row['settings'], chat_name)
                if s is None:
                    return False   # vaulted + sealed = absent, even for writers
                # Sealed vault: legacy plaintext private chats = absent too.
                if s.get('private_chat') and chat_name != self.active_chat_name \
                        and _vault_sealed():
                    return False
                was_priv = bool(s.get('private_chat'))
                new_priv = bool(patch.get('private_chat', was_priv))
                # Ruling F1 refusal LIFTED (v1.3, 2026-08-15) — see the
                # update_chat_settings twin.
                if new_priv and not was_priv and _vault_sealed():
                    # Vault hunt R4 twin: a locked vault has no private mode —
                    # no lane may flip a chat private into a sealed world
                    # (the flag would stand over plaintext until unlock).
                    logger.warning("[VAULT] refused private flip — vault sealed")
                    return False
                if was_priv and not new_priv and row['vaulted']:
                    # Unflip: DECRYPT FIRST — a public chat must never sit
                    # with encrypted rows. Refusal (sealed/tamper) refuses
                    # the whole settings write. RLock reentrant; only
                    # SELECTs have run on this conn, so the nested writer
                    # doesn't deadlock the WAL.
                    ok, err = self.unvault_chat(chat_name)
                    if not ok:
                        logger.warning(f"[VAULT] decrypt refused the settings "
                                       f"write: {err}")
                        return False
                old_prompt = s.get('prompt') if 'prompt' in patch else None
                s.update(patch)
                payload = json.dumps(s)
                if row['vaulted'] and not (was_priv and not new_priv):
                    payload = self._enc_value(payload)
                if touch_updated:
                    conn.execute("UPDATE chats SET settings = ?, updated_at = ? WHERE name = ?",
                                 (payload, datetime.now().isoformat(), chat_name))
                else:
                    conn.execute("UPDATE chats SET settings = ? WHERE name = ?",
                                 (payload, chat_name))
                conn.commit()
            if chat_name == self.active_chat_name:
                self.current_settings.update(patch)
            if 'prompt' in patch:
                _vault_ref_sync(patch.get('prompt'), old_prompt)
            if new_priv and not was_priv:
                # Flip to private: encrypt NOW (synchronous, Krem's ruling).
                # Failure leaves the flag standing — Phase 1 hiding still
                # applies and the unlock sweep retries the encryption.
                ok, err = self.vault_chat(chat_name)
                if not ok:
                    logger.warning(f"[VAULT] encrypt deferred: {err}")
            return True
        except Exception as e:
            logger.error(f"set_named_chat_settings failed for '{chat_name}': {e}")
            return False

    def get_all_prompt_settings(self) -> list:
        """Every chat's `prompt` setting (read-only) — the ground-truth scan
        for vault references-index release checks."""
        self._ensure_db()
        try:
            with self._lock, self._get_connection() as conn:
                rows = conn.execute("SELECT name, settings FROM chats").fetchall()
            out = []
            for r in rows:
                # Funnel: vaulted chats' refs are visible while unlocked;
                # sealed they're skipped (reconcile-on-unlock — the refs
                # index re-checks on the next unlocked pass).
                p = (self._settings_dict(r['settings'], r['name']) or {}).get('prompt')
                if p:
                    out.append(p)
            return out
        except Exception:
            return []

    def clear_named_chat_messages(self, chat_name: str) -> bool:
        """Empty a specific chat's message history via a direct DB write WITHOUT
        deleting the chat or touching its settings. Used by the ephemeral
        reap-on-call-start path: a caller who calls back after the TTL gets a fresh
        conversation. Mirrors the in-memory copy if it IS the active chat so a later
        save can't resurrect the old messages. Never wipes mid-stream."""
        self._ensure_db()
        if self._vault_hidden(chat_name):
            return False   # sealed vault: behaves as nonexistent
        if chat_name == self.active_chat_name and self._is_streaming:
            logger.warning(f"clear_named_chat_messages skipped for '{chat_name}' — streaming in progress")
            return False
        try:
            with self._lock, self._get_connection() as conn:
                cur = conn.execute("UPDATE chats SET messages = '[]', updated_at = ? WHERE name = ?",
                                   (datetime.now().isoformat(), chat_name))
                if cur.rowcount == 0:
                    return False
                # Rows chats keep messages in chat_messages, not the blob —
                # sweep them too (same pattern as clear_chat) or this is a
                # silent no-op on any post-rowify chat.
                conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
                conn.commit()
                self._rows_state.pop(chat_name, None)
                # F2 latch escape hatch — wipe unlatches (rows are gone).
                self._rows_degraded.pop(chat_name, None)
                self._degraded_toasted.discard(chat_name)
            if chat_name == self.active_chat_name:
                self.current_chat.messages = []
            return True
        except Exception as e:
            logger.error(f"clear_named_chat_messages failed for '{chat_name}': {e}")
            return False

    def reap_ephemeral_chats(self, now_epoch: float, source: str = "twilio",
                             exclude: Optional[set] = None) -> list:
        """Delete expired ephemeral chats. GUARDED — a chat is deleted ONLY if
        ALL hold: (a) settings['ephemeral_source'] == source, (b) it has a recorded
        ephemeral_last_call, (c) idle longer than settings['ephemeral_ttl_min'],
        (d) it is NOT the active chat, and (e) it is not in `exclude` (the daemon
        passes chats with a LIVE call — a long call must never lose its chat
        mid-conversation). A chat missing the marker is NEVER touched.
        Returns deleted names."""
        self._ensure_db()
        to_delete = []
        try:
            with self._lock, self._get_connection() as conn:
                for row in conn.execute("SELECT name, settings FROM chats").fetchall():
                    if row['name'] == self.active_chat_name:
                        continue
                    if exclude and row['name'] in exclude:
                        continue                      # live call in progress
                    try:
                        s = json.loads(row['settings'])
                    except Exception:
                        continue
                    if s.get('ephemeral_source') != source:
                        continue                      # HARD guard — only twilio-marked chats
                    last = float(s.get('ephemeral_last_call', 0) or 0)
                    if not last:
                        continue                      # never called yet — don't reap
                    ttl_min = float(s.get('ephemeral_ttl_min', 10) or 10)
                    if (now_epoch - last) > ttl_min * 60:
                        to_delete.append(row['name'])
                for name in to_delete:
                    conn.execute("DELETE FROM chats WHERE name = ?", (name,))
                    conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (name,))
                    try:
                        conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (name,))
                    except Exception:
                        pass
                    try:
                        # v1.3 rows must die with the chat — orphans read as
                        # live chats downstream and deterministic ephemeral
                        # names would resurrect them on the next call.
                        conn.execute("DELETE FROM plugin_chat_data WHERE chat_name = ?", (name,))
                    except Exception:
                        pass
                    self._rows_state.pop(name, None)
                conn.commit()
        except Exception as e:
            logger.error(f"reap_ephemeral_chats failed: {e}")
            return []
        if to_delete:
            logger.info(f"[TWILIO-REAP] deleted {len(to_delete)} expired ephemeral chat(s): {to_delete}")
            # chat_deleted fires on EVERY delete path (core/hooks.py contract)
            # — this reaper bypassed it, stranding plugin state kept OUTSIDE
            # the chat DB; deterministic ephemeral names then hand the previous
            # caller's plugin-side cache to the next caller (P1#5 / negspace
            # N31, 2026-08-31). Best-effort, post-commit.
            try:
                from core.hooks import hook_runner, HookEvent
                if hook_runner.has_handlers("chat_deleted"):
                    for name in to_delete:
                        hook_runner.fire("chat_deleted", HookEvent(metadata={"name": name}))
            except Exception as e:
                logger.warning(f"chat_deleted dispatch for reaped chats failed: {e}")
        return to_delete

    def reset_chat_scope_ref(self, setting_key: str, deleted_scope: str,
                             reset_to: str = 'default') -> list:
        """Sweep every chat's settings; any chat whose settings[setting_key] equals
        `deleted_scope` has that key rewritten to `reset_to`.

        Called when a scope is permanently deleted (memory/goal/knowledge/people)
        so chats don't silently keep pointing at a dead scope name. Without this,
        apply_scopes_from_settings on next activation sets the ContextVar to the
        ghost string and the AI writes into a room nobody sees in the UI — the
        same bug class as the mind.js hardcoded-scope one.

        Returns list of chat names that were updated. Publishes
        CHAT_SETTINGS_CHANGED per affected chat. If the ACTIVE chat was affected,
        the caller is responsible for re-applying scopes (we avoid importing
        api_fastapi here to keep the dep graph clean).
        """
        from datetime import datetime
        affected = []
        try:
            with self._lock, self._get_connection() as conn:
                cursor = conn.execute("SELECT name, settings, vaulted FROM chats")
                for row in cursor.fetchall():
                    # Funnel: while unlocked, vaulted chats' scope refs reset
                    # like anyone's (re-encrypted on write-back). Sealed →
                    # skipped; the ref inside the ciphertext goes stale and
                    # scope resolution falls back with its own warning.
                    s = self._settings_dict(row['settings'], row['name'])
                    if s is None:
                        continue
                    if s.get(setting_key) == deleted_scope:
                        s[setting_key] = reset_to
                        payload = json.dumps(s)
                        if row['vaulted']:
                            payload = self._enc_value(payload)
                        affected.append((row['name'], payload))
                for chat_name, new_settings_json in affected:
                    conn.execute(
                        "UPDATE chats SET settings = ?, updated_at = ? WHERE name = ?",
                        (new_settings_json, datetime.utcnow().isoformat() + 'Z', chat_name),
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"reset_chat_scope_ref failed for {setting_key}:{deleted_scope}: {e}")
            return [name for name, _ in affected]

        affected_names = [name for name, _ in affected]
        # If the active chat was touched, reload its in-memory settings AND
        # re-apply to ContextVars. Without the re-apply, the ContextVar keeps
        # the pre-sweep value (the now-deleted scope name), so the AI writes
        # into a ghost scope even though the chat file is correct. Found this
        # herring in my own fix — the in-memory current_settings dict and the
        # ContextVar were a two-source-of-truth problem.
        if self.active_chat_name in affected_names:
            try:
                self._load_chat(self.active_chat_name)
                from core.chat.function_manager import apply_scopes_from_settings
                # `fm` arg is legacy/unused (the function reads SCOPE_REGISTRY
                # directly), so None is safe here
                apply_scopes_from_settings(None, self.current_settings)
            except Exception as e:
                logger.warning(f"reload+apply after scope sweep failed: {e}")

        for name in affected_names:
            try:
                publish(Events.CHAT_SETTINGS_CHANGED, {
                    "chat": name,
                    "origin": "scope_cleanup",
                    "reason": f"{setting_key}:{deleted_scope}→{reset_to}",
                })
            except Exception:
                pass

        if affected_names:
            logger.info(
                f"Swept {setting_key}={deleted_scope!r} from {len(affected_names)} "
                f"chat(s) → {reset_to!r}: {affected_names}"
            )
        return affected_names

    def __len__(self):
        return len(self.current_chat)

    def remove_last_assistant_in_turn(self, timestamp: str) -> bool:
        """
        Remove only the LAST assistant message in a turn.
        Preserves user message, first assistant with tools, and tool results.
        """
        start_idx = -1
        for i, msg in enumerate(self.current_chat.messages):
            if msg.get('role') == 'assistant' and msg.get('timestamp') == timestamp:
                start_idx = i
                break
        
        if start_idx == -1:
            logger.warning(f"Assistant turn not found at {timestamp}")
            return False
        
        last_assistant_idx = start_idx
        for i in range(start_idx + 1, len(self.current_chat.messages)):
            if self.current_chat.messages[i].get('role') == 'user':
                break
            if self.current_chat.messages[i].get('role') == 'assistant':
                last_assistant_idx = i
        
        if last_assistant_idx > start_idx:
            removed = self.current_chat.messages.pop(last_assistant_idx)
            self._save_current_chat()
            logger.info(f"Removed last assistant message at index {last_assistant_idx}")
            logger.debug(f"Removed content preview: {removed.get('content', '')[:100]}")
            return True
        else:
            removed = self.current_chat.messages.pop(start_idx)
            self._save_current_chat()
            logger.info(f"Removed only assistant message at index {start_idx}")
            return True

    def edit_message_by_timestamp(self, role: str, timestamp: str, new_content: str) -> bool:
        """
        Edit a message by timestamp.
        For assistant messages, edits the LAST assistant message in that turn.
        """
        if not timestamp:
            logger.warning("No timestamp provided")
            return False
        
        if role == 'user':
            for msg in self.current_chat.messages:
                if msg.get('role') == 'user' and msg.get('timestamp') == timestamp:
                    content = msg.get('content')
                    if isinstance(content, list):
                        # Multimodal message: swap the text, keep image/file
                        # blocks. A bare string here used to drop the images.
                        others = [b for b in content
                                  if not (isinstance(b, dict) and b.get('type') == 'text')]
                        msg['content'] = [{'type': 'text', 'text': new_content}] + others
                    else:
                        msg['content'] = new_content
                    self.current_chat._needs_full_resync = True  # in-place edit
                    self._save_current_chat()
                    logger.info(f"Edited user message at {timestamp}")
                    return True
            return False
        
        if role == 'assistant':
            start_idx = -1
            for i, msg in enumerate(self.current_chat.messages):
                if msg.get('role') == 'assistant' and msg.get('timestamp') == timestamp:
                    start_idx = i
                    break
            
            if start_idx == -1:
                logger.warning(f"Assistant message not found at {timestamp}")
                return False
            
            last_assistant_idx = start_idx
            for i in range(start_idx + 1, len(self.current_chat.messages)):
                if self.current_chat.messages[i].get('role') == 'user':
                    break
                if self.current_chat.messages[i].get('role') == 'assistant':
                    last_assistant_idx = i
            
            # The editor shows thinking inline as <think>…</think> (the display
            # reconstruction). Split it back out, or the old `thinking` field
            # survives alongside the inline tag → double think block on render.
            clean, thinking = _extract_thinking_from_content(new_content)
            target = self.current_chat.messages[last_assistant_idx]
            target['content'] = clean
            if thinking:
                target['thinking'] = thinking
            else:
                target.pop('thinking', None)
            self.current_chat._needs_full_resync = True  # in-place edit
            self._save_current_chat()
            logger.info(f"Edited assistant message at index {last_assistant_idx} (turn started at {start_idx})")
            return True
        
        return False

    def save_tool_image(self, image_id: str, data: bytes, media_type: str = "image/jpeg",
                        chat_name: str = None) -> bool:
        """Save a tool-returned image blob to the database.

        chat_name defaults to the active chat. The continuity executor runs
        against a target chat WITHOUT switching active, so it passes chat_name
        explicitly — otherwise the blob keys to whatever chat happens to be
        active and gets orphaned when that chat (not the target) is deleted.
        Retrieval is by id alone, so a wrong key doesn't break rehydration —
        it's a cascade-correctness fix. 2026-06-13.
        """
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                owner = chat_name or self.active_chat_name
                # Vaulted owner chat: the image bytes encrypt like its rows
                # (Krem's point 4 — the render nobody has to see). Stored as
                # the '@enc1:' string; the BLOB column keeps what it's given.
                payload = data
                if self._is_vaulted_conn(conn, owner):
                    payload = self._enc_value_bytes(data)
                conn.execute(
                    """INSERT OR REPLACE INTO tool_images (id, chat_name, data, media_type, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (image_id, owner, payload, media_type, datetime.now().isoformat())
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save tool image '{image_id}': {e}")
            return False

    @staticmethod
    def _enc_value_bytes(data: bytes) -> str:
        from core import prompt_vault
        out = prompt_vault.encrypt_chat_blob(data)
        if out is None:
            raise RuntimeError("vault sealed — cannot write vaulted-chat image")
        return out

    def get_tool_image(self, image_id: str) -> Optional[tuple]:
        """Get a tool image by ID. Returns (data, media_type) or None.
        Vaulted images decrypt when the key is present; sealed → None (the
        route 404s — the by-ID hole from the leak table closes at rest)."""
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT data, media_type, chat_name FROM tool_images WHERE id = ?",
                    (image_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                # Hidden-owner gate (P3): crypto alone misses a legacy
                # private_chat=1/vaulted=0 chat, whose images are still
                # PLAINTEXT — the disclosure check must not rely on the
                # bytes being ciphertext.
                if self._vault_hidden(row[2]):
                    return None
                from core import prompt_vault
                data = row[0]
                if prompt_vault.is_chat_encrypted(data):
                    data = prompt_vault.decrypt_chat_blob(data)
                    if data is None:
                        return None
                return (data, row[1])
        except Exception as e:
            logger.error(f"Failed to get tool image '{image_id}': {e}")
            return None

    def _get_chat_path(self, chat_name: str) -> Path:
        """Legacy method - only used for migration detection."""
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_').lower()
        return self.history_dir / f"{safe_name}.json"
