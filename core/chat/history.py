# history.py - Chat history with SQLite storage for atomic writes
import logging
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional, Any, Union
from pathlib import Path
import tiktoken
import config
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

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
        in_tool_cycle: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get messages formatted for LLM with TRIMMING applied.
        
        Args:
            reserved_tokens: Tokens to reserve for system prompt + current user message.
            provider: Target provider ('claude', 'lmstudio', etc) for format decisions.
            in_tool_cycle: True if we're mid-tool-cycle and need thinking_raw for Claude.
        
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
            logger.warning(f"User message not found for deletion: {user_content[:50]}...")
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
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        self._db_path = self.history_dir / "sapphire_history.db"
        self._lock = threading.RLock()
        
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

        # Rowify: pre-warm the one-shot snapshot at BOOT when legacy blob
        # chats exist — the lazy trigger would otherwise run VACUUM INTO
        # under self._lock mid-traffic and stall every chat op for the copy
        # duration (race scout + day-ruiner 2026-07-09, same finding). Here
        # nothing else is running yet. Lazy call sites remain as backstops.
        try:
            if not (self.history_dir / ".pre_rowify_snapshot_done").exists():
                with self._get_connection() as conn:
                    has_blob = conn.execute(
                        "SELECT 1 FROM chats WHERE storage_format = 'blob' LIMIT 1"
                    ).fetchone()
                if has_blob:
                    self._ensure_pre_rowify_snapshot()
        except Exception as e:
            logger.warning(f"Boot snapshot pre-warm skipped: {e}")

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
        with self._lock:
            cur = getattr(self, '_streaming_count', 0)
            self._streaming_count = cur - 1 if cur > 0 else 0
            if self._streaming_count == 0:
                evt = getattr(self, '_no_streams_event', None)
                if evt is not None:
                    evt.set()
                # Rowify step 3: the 1→0 transition is THE conversion point
                # for the active chat (see _maybe_convert_active_chat).
                # Guarded getattr: some fixtures build this object without
                # __init__. Never raises — this runs inside request finallys.
                if getattr(self, '_rows_state', None) is not None:
                    self._maybe_convert_active_chat()

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
        """
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
                        created_at TEXT
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

    def _read_rows_messages(self, conn, chat_name: str) -> List[Dict[str, Any]]:
        """Read a rows-format chat's messages from chat_messages, oldest first.

        Caps at the newest _ROWS_LOAD_CAP messages (DESC + reverse) so a huge
        chat can't OOM the load — same invariant as the blob path's 50MB guard.
        """
        rows = conn.execute(
            "SELECT message_json FROM chat_messages WHERE chat_name = ? "
            "ORDER BY seq DESC LIMIT ?",
            (chat_name, self._ROWS_LOAD_CAP)
        ).fetchall()
        return [json.loads(r["message_json"]) for r in reversed(rows)]

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

                if row["storage_format"] == "rows":
                    self.current_chat.messages = self._read_rows_messages(conn, chat_name)
                    # Watermark: memory now mirrors the store exactly. offset
                    # is non-zero when the load was capped (we hold only the
                    # newest window; older rows stay untouched on disk).
                    total = conn.execute(
                        "SELECT COALESCE(MAX(seq) + 1, 0) FROM chat_messages "
                        "WHERE chat_name = ?", (chat_name,)
                    ).fetchone()[0]
                    loaded = len(self.current_chat.messages)
                    self._rows_state[chat_name] = {"offset": total - loaded,
                                                   "count": loaded}
                else:
                    raw_messages = row["messages"]
                    # Guard against OOM on massive chat blobs (>50MB)
                    if len(raw_messages) > 50 * 1024 * 1024:
                        logger.warning(f"Chat '{chat_name}' messages blob too large ({len(raw_messages) // 1024 // 1024}MB), truncating to last 5000 messages")
                        all_msgs = json.loads(raw_messages)
                        self.current_chat.messages = all_msgs[-5000:]
                    else:
                        self.current_chat.messages = json.loads(raw_messages)
                    self._rows_state.pop(chat_name, None)
                # The assignments above tripped the setter's resync flag —
                # memory matches the store right now, so clear it.
                self.current_chat._needs_full_resync = False
                file_settings = json.loads(row["settings"])
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

    def _save_current_chat(self):
        """Save current chat to SQLite atomically."""
        # Privacy mode: keep messages in memory only, don't persist to disk
        try:
            from core.privacy import is_privacy_mode
            if is_privacy_mode():
                logger.debug("Privacy mode active - skipping chat persistence")
                return
        except ImportError:
            pass

        self._ensure_db()

        # A1: save to the EFFECTIVE chat (a per-stream override's chat, else the
        # active one). For an override we persist messages ONLY — the target chat's
        # settings/markers are owned elsewhere (the daemon/reaper) and must not be
        # clobbered on every turn. For the active chat, behavior is unchanged.
        eff_chat = self._effective_chat()
        eff_name = self._effective_chat_name()
        is_override = eff_chat is not self.current_chat

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
                        return

                    if fmt_row["storage_format"] == "rows":
                        if not self._save_rows_chat(conn, eff_chat, eff_name, is_override):
                            return
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
                            return
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
                            return
                # Write-through (2026-07-05): the operator may be VIEWING the
                # override's chat (watching a live call). Keep the in-memory
                # singleton in sync so /api/history serves fresh turns and a
                # later switch-away can't clobber the row with a stale snapshot
                # (the mid-call-amnesia + vanishing-bubble root cause).
                if is_override and self.current_chat is not None \
                        and eff_name == self.active_chat_name:
                    self.current_chat.messages = [dict(m) for m in eff_chat.messages]
                logger.debug(f"Saved chat '{eff_name}' ({len(eff_chat.messages)} messages)")
            except Exception as e:
                logger.error(f"Failed to save chat '{eff_name}': {e}")
                try:
                    publish(Events.CONTINUITY_TASK_ERROR, {
                        "task": "Chat Save",
                        "error": f"Failed to save chat: {e}. Messages may be lost on restart."
                    })
                except Exception:
                    pass

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
        msgs = eff_chat.messages
        state = self._rows_state.get(eff_name)
        now = datetime.now().isoformat()

        resync = (state is None
                  or getattr(eff_chat, "_needs_full_resync", False)
                  or len(msgs) < state["count"])

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
            if resync:
                cur = conn.execute(
                    "UPDATE chats SET settings = ?, messages = '[]', updated_at = ? WHERE name = ?",
                    (json.dumps(self.current_settings), now, eff_name))
            else:
                cur = conn.execute(
                    "UPDATE chats SET settings = ?, updated_at = ? WHERE name = ?",
                    (json.dumps(self.current_settings), now, eff_name))
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
                [(eff_name, offset + i, m.get("role"), self._row_json(m))
                 for i, m in enumerate(msgs)])
            self._rows_state[eff_name] = {"offset": offset, "count": len(msgs)}
            eff_chat._needs_full_resync = False
        elif len(msgs) > state["count"]:
            conn.executemany(
                "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                "VALUES (?, ?, ?, ?)",
                [(eff_name, state["offset"] + i, msgs[i].get("role"), self._row_json(msgs[i]))
                 for i in range(state["count"], len(msgs))])
            state["count"] = len(msgs)
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
            try:
                from core.privacy import is_privacy_mode
                if is_privacy_mode():
                    return
            except ImportError:
                pass
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
            return True
        except Exception as e:
            logger.error(f"clear_chat('{chat_name}') failed: {e}")
            return False

    def export_chat(self, chat_name: str) -> Optional[Dict[str, Any]]:
        """Full raw export: {settings, messages} — UNCAPPED, no LLM trimming.

        Reads from the store (complete even when the in-memory load was
        capped); outside a live stream the store always matches memory."""
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT settings, messages, storage_format FROM chats WHERE name = ?",
                    (chat_name,)).fetchone()
                if not row:
                    return None
                if row["storage_format"] == "rows":
                    messages = [json.loads(r["message_json"]) for r in conn.execute(
                        "SELECT message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq",
                        (chat_name,))]
                else:
                    messages = json.loads(row["messages"])
                return {"settings": json.loads(row["settings"]), "messages": messages}
        except Exception as e:
            logger.error(f"export_chat('{chat_name}') failed: {e}")
            return None

    def rename_chat(self, old_name: str, new_name: str):
        """Rename a chat across every store that keys on the name.

        Returns (ok, result) — result is the sanitized new name on success,
        an error string on failure. Route layer owns the RAG-scope rename,
        agent refusal, and live-call refusal; this owns chats +
        chat_messages + tool_images + active-name/marker, one transaction."""
        if old_name == "default":
            return False, "Cannot rename the default chat"
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
                conn.commit()
                if old_name in self._rows_state:
                    self._rows_state[safe_name] = self._rows_state.pop(old_name)
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

    def replace_messages(self, chat_name: str, new_msgs: List[Dict[str, Any]],
                         expected_count: Optional[int] = None):
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
                if chat_name == self.active_chat_name:
                    self.current_chat.messages = new_msgs  # setter flags full resync
                    # Total replacement: the resync must delete from seq 0,
                    # including rows below a capped-load offset (same
                    # rationale as clear()).
                    self._rows_state[chat_name] = {"offset": 0, "count": 0}
                    self._save_current_chat()
                    return True, ""
                with self._get_connection() as conn:
                    row = conn.execute(
                        "SELECT storage_format FROM chats WHERE name = ?",
                        (chat_name,)).fetchone()
                    if not row:
                        return False, f"Chat '{chat_name}' not found"
                    now = datetime.now().isoformat()
                    if row["storage_format"] == "rows":
                        conn.execute("DELETE FROM chat_messages WHERE chat_name = ?",
                                     (chat_name,))
                        conn.executemany(
                            "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                            "VALUES (?, ?, ?, ?)",
                            [(chat_name, i, m.get("role"), self._row_json(m))
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

    def list_chat_files(self, stats: bool = False) -> List[Dict[str, Any]]:
        """List all available chats with metadata.

        stats=True adds size_bytes (message store + tool images) per chat —
        Chat Manager only; the hot dropdown path skips the size subqueries."""
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
        try:
            with self._lock, self._get_connection() as conn:
                # msg_count is format-aware: COUNT(*) over chat_messages for
                # rows chats (a converted chat's frozen blob would give a
                # stale json_array_length — rowify plan, land in step 1).
                cursor = conn.execute(
                    f"""SELECT name, settings, updated_at, created_at,
                              CASE WHEN storage_format = 'rows'
                                   THEN (SELECT COUNT(*) FROM chat_messages cm
                                         WHERE cm.chat_name = chats.name)
                                   ELSE json_array_length(messages)
                              END AS msg_count{size_cols}
                       FROM chats
                       ORDER BY updated_at DESC"""
                )
                for row in cursor:
                    settings = json.loads(row["settings"])

                    entry = {
                        "name": row["name"],
                        "display_name": settings.get("private_display_name") or row["name"].replace('_', ' ').title(),
                        "message_count": row["msg_count"] or 0,
                        "is_active": row["name"] == self.active_chat_name,
                        "modified": row["updated_at"],
                        "created": row["created_at"],
                        "private_chat": bool(settings.get("private_chat")),
                        "settings": settings
                    }
                    if stats:
                        entry["size_bytes"] = (row["msg_bytes"] or 0) + (row["img_bytes"] or 0)
                        entry["turn_count"] = row["turn_count"] or 0
                    chats.append(entry)
        except Exception as e:
            logger.error(f"Error listing chats: {e}")
        
        return chats

    def create_chat(self, chat_name: str) -> bool:
        """Create new chat with default settings."""
        if not chat_name or not chat_name.strip():
            logger.error("Cannot create chat with empty name")
            return False
        
        # Sanitize name
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_').lower()
        
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

        if self._is_streaming and chat_name == self.active_chat_name:
            logger.warning(f"Cannot delete '{chat_name}' — streaming in progress")
            return False

        try:
            with self._lock, self._get_connection() as conn:
                # Check if exists
                cursor = conn.execute(
                    "SELECT 1 FROM chats WHERE name = ?", 
                    (chat_name,)
                )
                if not cursor.fetchone():
                    logger.warning(f"Chat not found: {chat_name}")
                    return False
                
                was_active = (chat_name == self.active_chat_name)
                
                # Delete chat and any associated data
                conn.execute("DELETE FROM chats WHERE name = ?", (chat_name,))
                conn.execute("DELETE FROM chat_messages WHERE chat_name = ?", (chat_name,))
                try:
                    conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (chat_name,))
                except Exception:
                    pass  # Table may not exist yet
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

                # Ensure default exists
                self._ensure_default_exists()
                
                # Switch to default if we deleted active
                if was_active:
                    self._load_chat("default")
                    self.active_chat_name = "default"
                    logger.info("Switched to default after deleting active chat")
                
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
        """Persist active chat name for restart recovery."""
        try:
            from core.privacy import is_privacy_mode
            if is_privacy_mode():
                return  # Don't leak chat name to disk during privacy mode
        except ImportError:
            pass
        marker = self.history_dir / ".active_chat"
        try:
            marker.write_text(chat_name, encoding='utf-8')
        except Exception:
            pass

    def set_active_chat(self, chat_name: str) -> bool:
        """Switch to a different chat - loads messages AND settings."""
        with self._lock:
            if chat_name == self.active_chat_name:
                return True

            if self._is_streaming:
                logger.warning(f"Cannot switch to '{chat_name}' — streaming in progress on '{self.active_chat_name}'")
                return False

            self._save_current_chat()

            if self._load_chat(chat_name):
                self.active_chat_name = chat_name
                self._save_last_active(chat_name)
                self._in_tool_cycle = False  # Reset tool cycle state on chat switch
                logger.info(f"Switched to chat: {chat_name}")
                return True
            else:
                logger.error(f"Failed to switch to chat: {chat_name}")
                return False

    def get_active_chat_name(self) -> str:
        """Get active chat name (thread-safe)."""
        with self._lock:
            return self.active_chat_name

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
        are filled by the caller (LLMChat.resolve_stream_brain). None if missing."""
        settings = self.read_chat_settings(chat_name)
        if settings is None:
            return None
        hist = ConversationHistory(max_history=self.max_history)
        hist.messages = self.read_chat_messages(chat_name) or []
        return {"chat": chat_name, "settings": settings,
                "system_prompt": None, "tools": None, "history": hist}

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
                stored = json.loads(row["settings"]) if row["settings"] else {}
                merged = get_system_defaults()
                merged.update(stored)
                return merged
        except Exception as e:
            logger.error(f"Failed to read settings for chat '{chat_name}': {e}")
            return None

    def read_chat_messages(self, chat_name: str, provider: str = None) -> List[Dict[str, Any]]:
        """Read messages from a named chat WITHOUT switching active chat."""
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
                    messages = self._read_rows_messages(conn, chat_name)
                else:
                    messages = json.loads(row["messages"])
                # Apply same trimming as get_messages_for_llm
                chat = ConversationHistory()
                chat.messages = messages
                return chat.get_messages_for_llm(provider=provider)
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
                # under the lock). Latched or privacy-mode chats stay blob.
                if storage_format == "blob" and not row["conversion_failed"]:
                    try:
                        from core.privacy import is_privacy_mode
                        _privacy = is_privacy_mode()
                    except ImportError:
                        _privacy = False
                    if not _privacy:
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
                    base = conn.execute(
                        "SELECT COALESCE(MAX(seq) + 1, 0) FROM chat_messages "
                        "WHERE chat_name = ?", (chat_name,)
                    ).fetchone()[0]
                    conn.executemany(
                        "INSERT INTO chat_messages (chat_name, seq, role, message_json) "
                        "VALUES (?, ?, ?, ?)",
                        [(chat_name, base + i, m.get("role"), self._row_json(m))
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
                        live_ids.update(re.findall(r'<<IMG::tool:([^>]+)>>', r["message_json"]))
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
            self._save_current_chat()  # already routes to the effective chat

            # Clear tool images for the effective chat
            try:
                with self._get_connection() as conn:
                    conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (eff_name,))
                    conn.commit()
            except Exception:
                pass  # Table may not exist yet

        publish(Events.CHAT_CLEARED, {"chat_name": eff_name})

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
            return json.loads(row['settings'])
        except Exception as e:
            logger.error(f"get_settings_for('{chat_name}') failed: {e}")
            return None

    def update_chat_settings(self, settings: Dict[str, Any]) -> bool:
        """Update the EFFECTIVE chat's settings and save. Normally the active chat;
        under a per-stream brain override (phone call / background conversation) the
        stream's own chat — so an AI switch_model/switch_toolset/set_voice writes to
        the chat it is actually running in, not the operator's active web chat."""
        try:
            eff_chat = self._effective_chat()
            if eff_chat is not self.current_chat:
                # Override active — merge into the stream's own chat via a direct DB
                # write, and keep this turn's in-memory snapshot in sync.
                eff_name = self._effective_chat_name()
                ok = self.set_named_chat_settings(eff_name, settings)
                if ok:
                    try:
                        from core.chat.stream_brain import get_override
                        o = get_override()
                        if o is not None and o.get("settings") is not None:
                            o["settings"].update(settings)
                    except Exception:
                        pass
                    logger.info(f"Updated settings for override chat '{eff_name}'")
                return ok
            self.current_settings.update(settings)
            self._save_current_chat()
            logger.info(f"Updated settings for chat '{self.active_chat_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return False

    def set_named_chat_settings(self, chat_name: str, patch: Dict[str, Any]) -> bool:
        """Merge `patch` into a SPECIFIC (possibly non-active) chat's settings via a
        direct DB write. Lets a realtime daemon (Twilio) configure a call chat it
        never activates. Mirrors in-memory settings if it IS the active chat."""
        self._ensure_db()
        try:
            with self._lock, self._get_connection() as conn:
                row = conn.execute("SELECT settings FROM chats WHERE name = ?", (chat_name,)).fetchone()
                if not row:
                    return False
                try:
                    s = json.loads(row['settings'])
                except Exception:
                    s = {}
                s.update(patch)
                conn.execute("UPDATE chats SET settings = ?, updated_at = ? WHERE name = ?",
                             (json.dumps(s), datetime.now().isoformat(), chat_name))
                conn.commit()
            if chat_name == self.active_chat_name:
                self.current_settings.update(patch)
            return True
        except Exception as e:
            logger.error(f"set_named_chat_settings failed for '{chat_name}': {e}")
            return False

    def clear_named_chat_messages(self, chat_name: str) -> bool:
        """Empty a specific chat's message history via a direct DB write WITHOUT
        deleting the chat or touching its settings. Used by the ephemeral
        reap-on-call-start path: a caller who calls back after the TTL gets a fresh
        conversation. Mirrors the in-memory copy if it IS the active chat so a later
        save can't resurrect the old messages. Never wipes mid-stream."""
        self._ensure_db()
        if chat_name == self.active_chat_name and self._is_streaming:
            logger.warning(f"clear_named_chat_messages skipped for '{chat_name}' — streaming in progress")
            return False
        try:
            with self._lock, self._get_connection() as conn:
                cur = conn.execute("UPDATE chats SET messages = '[]', updated_at = ? WHERE name = ?",
                                   (datetime.now().isoformat(), chat_name))
                conn.commit()
                if cur.rowcount == 0:
                    return False
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
                    try:
                        conn.execute("DELETE FROM tool_images WHERE chat_name = ?", (name,))
                    except Exception:
                        pass
                conn.commit()
        except Exception as e:
            logger.error(f"reap_ephemeral_chats failed: {e}")
            return []
        if to_delete:
            logger.info(f"[TWILIO-REAP] deleted {len(to_delete)} expired ephemeral chat(s): {to_delete}")
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
                cursor = conn.execute("SELECT name, settings FROM chats")
                for row in cursor.fetchall():
                    try:
                        s = json.loads(row['settings'])
                    except Exception:
                        continue
                    if s.get(setting_key) == deleted_scope:
                        s[setting_key] = reset_to
                        affected.append((row['name'], json.dumps(s)))
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
            
            self.current_chat.messages[last_assistant_idx]['content'] = new_content
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
                conn.execute(
                    """INSERT OR REPLACE INTO tool_images (id, chat_name, data, media_type, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (image_id, chat_name or self.active_chat_name, data, media_type, datetime.now().isoformat())
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save tool image '{image_id}': {e}")
            return False

    def get_tool_image(self, image_id: str) -> Optional[tuple]:
        """Get a tool image by ID. Returns (data, media_type) or None."""
        self._ensure_db()
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT data, media_type FROM tool_images WHERE id = ?",
                    (image_id,)
                )
                row = cursor.fetchone()
                return (row[0], row[1]) if row else None
        except Exception as e:
            logger.error(f"Failed to get tool image '{image_id}': {e}")
            return None

    def _get_chat_path(self, chat_name: str) -> Path:
        """Legacy method - only used for migration detection."""
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_').lower()
        return self.history_dir / f"{safe_name}.json"
