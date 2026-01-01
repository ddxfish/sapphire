# history.py
import logging
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path
import tiktoken
import config

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
    "custom_context": ""
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
    return len(get_tokenizer().encode(text))


class ConversationHistory:
    def __init__(self, max_history: int = 30):
        self.max_history = max_history
        self.messages = []

    def add_user_message(self, content: str):
        """Add user message - NO TRIMMING."""
        self.messages.append({
            "role": "user", 
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_assistant_with_tool_calls(self, content: Optional[str], tool_calls: List[Dict]):
        """Add assistant message that includes tool calls - NO TRIMMING."""
        self.messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": tool_calls,
            "timestamp": datetime.now().isoformat()
        })

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
            msg["tool_inputs"] = inputs  # NEW: Store inputs as structured data
        self.messages.append(msg)

    def add_assistant_final(self, content: str):
        """Add final assistant message (no tool calls) - NO TRIMMING."""
        self.messages.append({
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_message_pair(self, user_content: str, assistant_content: str):
        """Legacy method for adding simple user/assistant pairs - NO TRIMMING."""
        timestamp = datetime.now().isoformat()
        self.messages.append({"role": "user", "content": user_content, "timestamp": timestamp})
        self.messages.append({"role": "assistant", "content": assistant_content, "timestamp": timestamp})

    def get_messages(self) -> List[Dict[str, str]]:
        """Get ALL messages (with timestamps for storage) - NO TRIMMING."""
        return self.messages.copy()

    def get_messages_for_llm(self) -> List[Dict[str, Any]]:
        """
        Get messages formatted for LLM with TRIMMING applied.
        This is the ONLY place where trimming happens - storage is never affected.
        """
        msgs = []
        for msg in self.messages:
            llm_msg = {"role": msg["role"], "content": msg["content"]}
            
            if "tool_calls" in msg and msg["tool_calls"]:
                llm_msg["tool_calls"] = msg["tool_calls"]
            
            if msg["role"] == "tool":
                llm_msg["tool_call_id"] = msg["tool_call_id"]
                llm_msg["name"] = msg["name"]
            
            msgs.append(llm_msg)
        
        # TRIMMING STEP 1: Turn-based trimming
        if len(msgs) > self.max_history:
            user_count = sum(1 for msg in msgs if msg["role"] == "user")
            max_pairs = self.max_history // 2
            
            if user_count > max_pairs:
                user_turns_to_remove = user_count - max_pairs
                removed_users = 0
                
                while removed_users < user_turns_to_remove and len(msgs) > 0:
                    if msgs[0]["role"] == "user":
                        removed_users += 1
                    msgs.pop(0)
        
        # TRIMMING STEP 2: Token-based trimming
        max_tokens = getattr(config, 'LLM_MAX_TOKENS', 32000)
        total_tokens = sum(count_tokens(str(m.get("content", ""))) for m in msgs)
        
        while total_tokens > max_tokens and len(msgs) > 1:
            removed = msgs.pop(0)
            total_tokens -= count_tokens(str(removed.get("content", "")))
        
        return msgs

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
    

class ChatSessionManager:
    def __init__(self, max_history: int = 30, history_dir: str = "user/history"):
        self.max_history = max_history
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_chat = ConversationHistory(max_history=max_history)
        self.active_chat_name = "default"
        self.current_settings = SYSTEM_DEFAULTS.copy()  # NEW: Track current chat settings
        
        #self._init_predefined_chats()  # Now a no-op but kept for structure
        
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
        # NOTE: We no longer have predefined chats - just create default.json on first load
        # This method kept for compatibility but does nothing now
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
                # Load settings, merge with defaults for any missing keys
                file_settings = data.get("settings", {})
                self.current_settings = SYSTEM_DEFAULTS.copy()
                self.current_settings.update(file_settings)
                logger.info(f"Loaded chat '{chat_name}' with {len(data['messages'])} messages and settings")
                return True
            
            # Handle old format: just array of messages (backward compat - fresh install won't hit this)
            elif isinstance(data, list):
                self.current_chat.messages = data
                self.current_settings = SYSTEM_DEFAULTS.copy()  # Use defaults for old chats
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
                
                # Handle new format
                if isinstance(data, dict) and "messages" in data:
                    message_count = len(data["messages"])
                    settings = data.get("settings", {})
                # Handle old format (legacy, shouldn't see on fresh install)
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
                    "settings": settings  # NEW: Include settings in metadata
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
            # Create new chat with user defaults (or system defaults if not set)
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
            
            # Always ensure default exists
            self._ensure_default_exists()
            
            # If we deleted the active chat, switch to default
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
            # Settings are loaded by _load_chat now
            logger.info(f"Switched to chat: {chat_name}")
            return True
        else:
            logger.error(f"Failed to switch to chat: {chat_name}")
            return False

    def get_active_chat_name(self) -> str:
        """Get active chat name."""
        return self.active_chat_name

    def add_user_message(self, content: str):
        self.current_chat.add_user_message(content)
        self._save_current_chat()

    def add_assistant_with_tool_calls(self, content: Optional[str], tool_calls: List[Dict]):
        self.current_chat.add_assistant_with_tool_calls(content, tool_calls)
        self._save_current_chat()

    def add_tool_result(self, tool_call_id: str, name: str, content: str, inputs: Optional[Dict] = None):
        self.current_chat.add_tool_result(tool_call_id, name, content, inputs)
        self._save_current_chat()

    def add_assistant_final(self, content: str):
        self.current_chat.add_assistant_final(content)
        self._save_current_chat()

    def add_message_pair(self, user_content: str, assistant_content: str):
        self.current_chat.add_message_pair(user_content, assistant_content)
        self._save_current_chat()

    def get_messages(self) -> List[Dict[str, str]]:
        return self.current_chat.get_messages()

    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        """Get messages for LLM."""
        return self.current_chat.get_messages_for_llm()

    def get_turn_count(self) -> int:
        return self.current_chat.get_turn_count()

    def remove_last_messages(self, count: int) -> bool:
        result = self.current_chat.remove_last_messages(count)
        if result:
            self._save_current_chat()
        return result

    def remove_from_user_message(self, user_content: str) -> bool:
        result = self.current_chat.remove_from_user_message(user_content)
        if result:
            self._save_current_chat()
        return result

    def remove_from_assistant_timestamp(self, timestamp: str) -> bool:
        result = self.current_chat.remove_from_assistant_timestamp(timestamp)
        if result:
            self._save_current_chat()
        return result

    def clear(self):
        self.current_chat.clear()
        self._save_current_chat()

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
            # Merge new settings with current (allows partial updates)
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
        
        Used for continue functionality - keeps the tool execution history
        but removes the final prose to regenerate.
        """
        # Find the assistant message with this timestamp (turn start)
        start_idx = -1
        for i, msg in enumerate(self.current_chat.messages):
            if msg.get('role') == 'assistant' and msg.get('timestamp') == timestamp:
                start_idx = i
                break
        
        if start_idx == -1:
            logger.warning(f"Assistant turn not found at {timestamp}")
            return False
        
        # Find the last assistant message in this turn
        last_assistant_idx = start_idx
        for i in range(start_idx + 1, len(self.current_chat.messages)):
            if self.current_chat.messages[i].get('role') == 'user':
                break  # Next turn started
            if self.current_chat.messages[i].get('role') == 'assistant':
                last_assistant_idx = i
        
        # Only remove if there's a final assistant after tools
        if last_assistant_idx > start_idx:
            removed = self.current_chat.messages.pop(last_assistant_idx)
            self._save_current_chat()
            logger.info(f"Removed last assistant message at index {last_assistant_idx}")
            logger.debug(f"Removed content preview: {removed.get('content', '')[:100]}")
            return True
        else:
            # Only one assistant message (no final prose), remove it
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
        
        # For user messages, simple match
        if role == 'user':
            for msg in self.current_chat.messages:
                if msg.get('role') == 'user' and msg.get('timestamp') == timestamp:
                    msg['content'] = new_content
                    self._save_current_chat()
                    logger.info(f"Edited user message at {timestamp}")
                    return True
            return False
        
        # For assistant messages, find the turn and edit the LAST assistant message
        if role == 'assistant':
            # Find the assistant message with this timestamp
            start_idx = -1
            for i, msg in enumerate(self.current_chat.messages):
                if msg.get('role') == 'assistant' and msg.get('timestamp') == timestamp:
                    start_idx = i
                    break
            
            if start_idx == -1:
                logger.warning(f"Assistant message not found at {timestamp}")
                return False
            
            # Find the last assistant message in this turn (before next user message or end)
            last_assistant_idx = start_idx
            for i in range(start_idx + 1, len(self.current_chat.messages)):
                if self.current_chat.messages[i].get('role') == 'user':
                    break  # Next turn started
                if self.current_chat.messages[i].get('role') == 'assistant':
                    last_assistant_idx = i
            
            # Edit the LAST assistant message in this turn
            self.current_chat.messages[last_assistant_idx]['content'] = new_content
            self._save_current_chat()
            logger.info(f"Edited assistant message at index {last_assistant_idx} (turn started at {start_idx})")
            return True
        
        return False