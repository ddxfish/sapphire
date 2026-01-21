# history.py
import logging
import json
import os
import re
from datetime import datetime
from typing import List, Dict, Optional, Any, Union
from pathlib import Path
import tiktoken
import config
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)

# System defaults for chat settings - hardcoded fallbacks
# Primary source is user/settings/chat_defaults.json or factory chat_defaults.json
SYSTEM_DEFAULTS = {
    "prompt": "default",  
    "ability": "default", 
    "voice": "af_heart",
    "pitch": 0.98,
    "speed": 1.3,
    "spice_enabled": True,
    "spice_turns": 3,
    "inject_datetime": False,
    "custom_context": "",
    "llm_primary": "auto",      # "auto", "none", or provider key like "claude"
    "llm_model": ""             # Empty = use provider default, or specific model override
}

def get_user_defaults() -> Dict[str, Any]:
    """
    Get user's custom chat defaults, falling back to system defaults.
    Used when creating new chats.
    """
    user_defaults_path = Path("user/settings/chat_defaults.json")
    
    if user_defaults_path.exists():
        try:
            with open(user_defaults_path, 'r', encoding='utf-8') as f:

                user_defaults = json.load(f)
            # Merge: start with system defaults, override with user settings
            merged = SYSTEM_DEFAULTS.copy()
            merged.update(user_defaults)
            logger.debug(f"Using user chat defaults from {user_defaults_path}")
            return merged
        except Exception as e:
            logger.error(f"Failed to load user chat defaults: {e}")
    
    return SYSTEM_DEFAULTS.copy()

_tokenizer = None

def get_tokenizer():
    """Lazy load tokenizer once."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
    return _tokenizer

def count_tokens(text: str) -> int:
    """Accurate token count."""
    if not text:
        return 0
    return len(get_tokenizer().encode(text))


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
    def __init__(self, max_history: int = 30):
        self.max_history = max_history
        self.messages = []

    def add_user_message(self, content: Union[str, List[Dict[str, Any]]]):
        """Add user message - accepts string or content list with images."""
        self.messages.append({
            "role": "user", 
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_assistant_with_tool_calls(
        self, 
        content: Optional[str], 
        tool_calls: List[Dict],
        thinking: Optional[str] = None,
        thinking_raw: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None
    ):
        """
        Add assistant message that includes tool calls.
        
        Args:
            content: The visible response content (no thinking tags)
            tool_calls: List of tool call dicts
            thinking: Extracted thinking text (for UI display)
            thinking_raw: Original structured thinking blocks (for Claude continuity)
            metadata: Provider info, timing, tokens
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
        metadata: Optional[Dict] = None
    ):
        """
        Add final assistant message (no tool calls).
        
        Args:
            content: The visible response content (no thinking tags)
            thinking: Extracted thinking text (for UI display)
            metadata: Provider info, timing, tokens
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
                
            elif role == "tool":
                llm_msg = {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "name": msg["name"],
                    "content": msg.get("content", "")
                }
                
            elif role == "user":
                content = msg.get("content", "")
                # Handle content stored as list (for multimodal or edge cases)
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text_parts.append(block.get('text', ''))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = ' '.join(text_parts).strip()
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
            
            total_tokens = sum(count_tokens(str(m.get("content", ""))) for m in msgs)
            
            while total_tokens > effective_limit and len(msgs) > 1:
                removed = msgs.pop(0)
                total_tokens -= count_tokens(str(removed.get("content", "")))
        
        return msgs

    def clear_thinking_raw(self):
        """
        Clear thinking_raw from all messages.
        Called after tool cycle completes - we don't need raw blocks anymore.
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
        """Remove all messages starting from a specific user message to the end."""
        if not user_content:
            return False
        
        user_index = -1
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i]["role"] == "user" and self.messages[i]["content"] == user_content:
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
                return True
        return False
    

class ChatSessionManager:
    def __init__(self, max_history: int = 30, history_dir: str = "user/history"):
        self.max_history = max_history
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_chat = ConversationHistory(max_history=max_history)
        self.active_chat_name = "default"
        self.current_settings = SYSTEM_DEFAULTS.copy()
        
        # Track if we're in an active tool cycle (for Claude thinking_raw)
        self._in_tool_cycle = False
        
        # Create default.json if it doesn't exist
        default_path = self.history_dir / "default.json"
        if not default_path.exists():
            logger.info("Creating default chat with user defaults")
            new_chat_data = {
                "settings": get_user_defaults(),
                "messages": []
            }
            try:
                with open(default_path, 'w', encoding='utf-8') as f:
                    json.dump(new_chat_data, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to create default chat: {e}")
        
        # Load default chat
        if default_path.exists():
            self._load_chat("default")
        
        logger.info(f"ChatSessionManager initialized")

    def _init_predefined_chats(self):
        """Create predefined chat files if they don't exist - removed, no longer needed."""
        pass

    def _get_chat_path(self, chat_name: str) -> Path:
        """Get path for chat file."""
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_name = safe_name.replace(' ', '_').lower()
        return self.history_dir / f"{safe_name}.json"

    def _load_chat(self, chat_name: str) -> bool:
        """Load chat from file - loads messages AND settings."""
        path = self._get_chat_path(chat_name)
        if not path.exists():
            logger.warning(f"Chat file not found: {path}")
            return False
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Handle new format: {"settings": {}, "messages": []}
            if isinstance(data, dict) and "messages" in data:
                self.current_chat.messages = data["messages"]
                file_settings = data.get("settings", {})
                self.current_settings = SYSTEM_DEFAULTS.copy()
                self.current_settings.update(file_settings)
                logger.info(f"Loaded chat '{chat_name}' with {len(data['messages'])} messages and settings")
                return True
            
            # Handle old format: just array of messages
            elif isinstance(data, list):
                self.current_chat.messages = data
                self.current_settings = SYSTEM_DEFAULTS.copy()
                logger.info(f"Loaded legacy chat '{chat_name}' with {len(data)} messages, using default settings")
                return True
            else:
                logger.error(f"Invalid chat file format: {path}")
                return False
        except Exception as e:
            logger.error(f"Failed to load chat '{chat_name}': {e}")
            return False

    def _save_current_chat(self):
        """Save current chat to disk - saves settings AND messages."""
        path = self._get_chat_path(self.active_chat_name)
        try:
            save_data = {
                "settings": self.current_settings,
                "messages": self.current_chat.messages
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved chat '{self.active_chat_name}' ({len(self.current_chat.messages)} messages, settings included)")
        except Exception as e:
            logger.error(f"Failed to save chat '{self.active_chat_name}': {e}")

    def list_chat_files(self) -> List[Dict[str, Any]]:
        """List all available chats with metadata."""
        chats = []
        for path in self.history_dir.glob("*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                display_name = path.stem.replace('_', ' ').title()
                
                if isinstance(data, dict) and "messages" in data:
                    message_count = len(data["messages"])
                    settings = data.get("settings", {})
                elif isinstance(data, list):
                    message_count = len(data)
                    settings = {}
                else:
                    message_count = 0
                    settings = {}
                
                chats.append({
                    "name": path.stem,
                    "display_name": display_name,
                    "message_count": message_count,
                    "is_active": path.stem == self.active_chat_name,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "settings": settings
                })
            except Exception as e:
                logger.error(f"Error reading chat file {path}: {e}")
        
        chats.sort(key=lambda x: x["modified"], reverse=True)
        return chats

    def create_chat(self, chat_name: str) -> bool:
        """Create new chat with default settings."""
        if not chat_name or not chat_name.strip():
            logger.error("Cannot create chat with empty name")
            return False
        
        path = self._get_chat_path(chat_name)
        if path.exists():
            logger.warning(f"Chat already exists: {chat_name}")
            return False
        
        try:
            new_chat_data = {
                "settings": get_user_defaults(),
                "messages": []
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(new_chat_data, f, indent=2)
            logger.info(f"Created new chat: {chat_name} with default settings")
            return True
        except Exception as e:
            logger.error(f"Failed to create chat '{chat_name}': {e}")
            return False

    def delete_chat(self, chat_name: str) -> bool:
        """Delete chat file. Recreates default if deleted, switches active if needed."""
        path = self._get_chat_path(chat_name)
        
        try:
            if not path.exists():
                logger.warning(f"Chat file not found: {chat_name}")
                return False
            
            was_active = (chat_name == self.active_chat_name)
            path.unlink()
            logger.info(f"Deleted chat: {chat_name}")
            
            self._ensure_default_exists()
            
            if was_active:
                self._load_chat("default")
                self.active_chat_name = "default"
                logger.info("Switched to default after deleting active chat")
            
            return True
        except Exception as e:
            logger.error(f"Failed to delete chat '{chat_name}': {e}")
            return False
    
    def _ensure_default_exists(self):
        """Ensure default.json always exists."""
        default_path = self.history_dir / "default.json"
        if not default_path.exists():
            new_chat_data = {
                "settings": get_user_defaults(),
                "messages": []
            }
            with open(default_path, 'w', encoding='utf-8') as f:
                json.dump(new_chat_data, f, indent=2)
            logger.info("Recreated default chat")

    def set_active_chat(self, chat_name: str) -> bool:
        """Switch to a different chat - loads messages AND settings."""
        if chat_name == self.active_chat_name:
            return True
        
        self._save_current_chat()
        
        if self._load_chat(chat_name):
            self.active_chat_name = chat_name
            self._in_tool_cycle = False  # Reset tool cycle state on chat switch
            logger.info(f"Switched to chat: {chat_name}")
            return True
        else:
            logger.error(f"Failed to switch to chat: {chat_name}")
            return False

    def get_active_chat_name(self) -> str:
        """Get active chat name."""
        return self.active_chat_name

    def add_user_message(self, content: Union[str, List[Dict[str, Any]]]):
        self.current_chat.add_user_message(content)
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
        self.current_chat.add_assistant_with_tool_calls(
            content, tool_calls, thinking, thinking_raw, metadata
        )
        self._save_current_chat()

    def add_tool_result(self, tool_call_id: str, name: str, content: str, inputs: Optional[Dict] = None):
        self.current_chat.add_tool_result(tool_call_id, name, content, inputs)
        self._save_current_chat()

    def add_assistant_final(
        self, 
        content: str,
        thinking: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Add final assistant message. Ends tool cycle and clears thinking_raw."""
        self.current_chat.add_assistant_final(content, thinking, metadata)
        
        # Tool cycle complete - clear thinking_raw from previous messages
        if self._in_tool_cycle:
            self.current_chat.clear_thinking_raw()
            self._in_tool_cycle = False
            
        self._save_current_chat()
        publish(Events.MESSAGE_ADDED, {"role": "assistant"})

    def add_message_pair(self, user_content: str, assistant_content: str):
        self.current_chat.add_message_pair(user_content, assistant_content)
        self._save_current_chat()
        publish(Events.MESSAGE_ADDED, {"role": "pair"})

    def get_messages(self) -> List[Dict[str, str]]:
        """Get raw messages (for storage/debugging)."""
        return self.current_chat.get_messages()

    def get_messages_for_display(self) -> List[Dict[str, Any]]:
        """Get messages formatted for UI with <think> tags reconstructed."""
        return self.current_chat.get_messages_for_display()

    def get_messages_for_llm(self, reserved_tokens: int = 0, provider: str = None) -> List[Dict[str, str]]:
        """Get messages for LLM with trimming applied."""
        return self.current_chat.get_messages_for_llm(
            reserved_tokens, 
            provider=provider,
            in_tool_cycle=self._in_tool_cycle
        )

    def get_turn_count(self) -> int:
        return self.current_chat.get_turn_count()

    def remove_last_messages(self, count: int) -> bool:
        result = self.current_chat.remove_last_messages(count)
        if result:
            self._save_current_chat()
            publish(Events.MESSAGE_REMOVED, {"count": count})
        return result

    def remove_from_user_message(self, user_content: str) -> bool:
        result = self.current_chat.remove_from_user_message(user_content)
        if result:
            self._save_current_chat()
            publish(Events.MESSAGE_REMOVED, {"from": "user_message"})
        return result

    def remove_from_assistant_timestamp(self, timestamp: str) -> bool:
        result = self.current_chat.remove_from_assistant_timestamp(timestamp)
        if result:
            self._save_current_chat()
            publish(Events.MESSAGE_REMOVED, {"from": "assistant_timestamp"})
        return result

    def clear(self):
        self.current_chat.clear()
        self._in_tool_cycle = False
        self._save_current_chat()
        publish(Events.CHAT_CLEARED)

    def edit_message_by_content(self, role: str, original_content: str, new_content: str) -> bool:
        """Edit message and save."""
        result = self.current_chat.edit_message_by_content(role, original_content, new_content)
        if result:
            self._save_current_chat()
        return result

    def get_chat_settings(self) -> Dict[str, Any]:
        """Get current chat's settings."""
        return self.current_settings.copy()

    def update_chat_settings(self, settings: Dict[str, Any]) -> bool:
        """Update current chat's settings and save."""
        try:
            self.current_settings.update(settings)
            self._save_current_chat()
            logger.info(f"Updated settings for chat '{self.active_chat_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return False

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
            self._save_current_chat()
            logger.info(f"Edited assistant message at index {last_assistant_idx} (turn started at {start_idx})")
            return True
        
        return False