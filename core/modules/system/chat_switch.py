# core/modules/system/chat_switch.py
import logging
import string

logger = logging.getLogger(__name__)

class ChatSwitcher:
    """Manages switching between chat sessions."""

    def __init__(self):
        self.voice_chat_system = None

    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info("ChatSwitcher attached to system")

    def process(self, user_input: str):
        """
        Process a chat switch command.
        Accepts: "networking", "switch to work", "use default", etc.
        """
        if not user_input or not user_input.strip():
            return self._list_chats()
        
        # Parse chat name from various formats
        chat_name = self._parse_chat_name(user_input)
        
        if not chat_name:
            return "Please specify a chat name. Available chats: " + ", ".join(self._get_available_chats())
        
        # Get session manager
        if not self.voice_chat_system:
            return "System reference not available."
        
        if not hasattr(self.voice_chat_system, 'llm_chat'):
            return "Chat system not available."
        
        if not hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
            return "Session manager not available."
        
        session_manager = self.voice_chat_system.llm_chat.session_manager
        
        # Get available chats (normalized)
        available_chats = self._get_available_chats()
        
        # Normalize chat_name for comparison
        chat_name_normalized = chat_name.lower().strip()
        
        # Find matching chat (case-insensitive)
        matched_chat = None
        for chat in available_chats:
            if chat.lower().strip() == chat_name_normalized:
                matched_chat = chat
                break
        
        if not matched_chat:
            return f"Chat '{chat_name}' not found. Available: {', '.join(available_chats)}"
        
        # Switch to chat (use the actual matched name, not normalized)
        try:
            if session_manager.set_active_chat(matched_chat):
                # Announce via TTS
                if hasattr(self.voice_chat_system, 'tts'):
                    self.voice_chat_system.tts.speak(f"Switched to {matched_chat} chat.")
                return f"Switched to '{matched_chat}' chat."
            else:
                return f"Failed to switch to '{matched_chat}' chat."
        except Exception as e:
            logger.error(f"Error switching to chat '{matched_chat}': {e}", exc_info=True)
            return f"Error switching chat: {str(e)}"
    
    def _parse_chat_name(self, user_input: str) -> str:
        """
        Parse chat name from various input formats:
        - "networking" → "networking"
        - "switch to work" → "work"
        - "use default chat" → "default"
        - "load story" → "story"
        - "image." → "image" (strips punctuation)
        """
        text = user_input.lower().strip()
        
        # Remove common command words
        remove_words = ['switch', 'to', 'use', 'load', 'chat', 'open', 'go', 'the']
        words = [w for w in text.split() if w not in remove_words]
        
        # Join remaining words (supports multi-word chat names)
        chat_name = '_'.join(words) if words else ""
        
        # Strip punctuation from the result
        chat_name = chat_name.strip(string.punctuation)
        
        return chat_name
    
    def _get_available_chats(self) -> list:
        """Get list of available chat names (normalized)."""
        if not self.voice_chat_system:
            logger.warning("No voice_chat_system available")
            return []
        
        if not hasattr(self.voice_chat_system, 'llm_chat'):
            logger.warning("No llm_chat available")
            return []
        
        if not hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
            logger.warning("No session_manager available")
            return []
        
        try:
            session_manager = self.voice_chat_system.llm_chat.session_manager
            
            # Use list_chat_files() which returns list of dicts
            chat_files = session_manager.list_chat_files()
            
            # Extract just the names
            chats = [chat['name'] for chat in chat_files]
            
            logger.info(f"Found {len(chats)} chats: {chats}")
            return chats
        except Exception as e:
            logger.error(f"Error getting chat list: {e}", exc_info=True)
            return []
    
    def _list_chats(self):
        """List available chats with current chat highlighted."""
        available = self._get_available_chats()
        
        if not available:
            return "No chats available."
        
        # Get current chat
        current = "unknown"
        try:
            if self.voice_chat_system and hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
                current = self.voice_chat_system.llm_chat.session_manager.get_active_chat_name()
        except Exception as e:
            logger.error(f"Error getting current chat: {e}")
        
        # Format list with current chat marked
        chat_list = [f"Your selected chat is: {chat}. Also available are:" if chat == current else f"  {chat}" for chat in available]
    
        return chat_list