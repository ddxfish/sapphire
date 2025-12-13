import logging
import re
import json
from pathlib import Path

logger = logging.getLogger(__name__)

class Reset:
    """Message management module for resetting chat history."""
    
    def __init__(self):
        self.voice_chat_system = None
        self.keyword_match = None
        self.full_command = None
        
        # Dictionary to convert word numbers to digits
        self.word_to_number = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10
        }
    
    def process(self, user_input, active_chat="default"):
        """
        Process reset or remove messages command for a specific chat.
        
        Args:
            user_input: The user's command text
            active_chat: Name of the chat to reset (defaults to "default")
        """
        logger.info(f"Message management command received for chat '{active_chat}': {user_input}")
        
        # Use the full command if available
        command_text = self.full_command if self.full_command else user_input
        command_text = command_text.lower().strip()
        
        # Load the specific chat's history file
        history_dir = Path("logs/history")
        chat_file = history_dir / f"{active_chat}.json"
        
        if not chat_file.exists():
            logger.warning(f"Chat file not found: {chat_file}")
            return f"Chat '{active_chat}' not found."
        
        # Load chat history
        try:
            with open(chat_file, 'r', encoding='utf-8') as f:
                chat_history = json.load(f)
        except Exception as e:
            logger.error(f"Error loading chat history: {e}")
            return f"Error loading chat '{active_chat}'."
        
        # Check if this is a "remove last" command
        if "remove last" in command_text:
            return self._handle_remove_last(command_text, chat_history, chat_file, active_chat)
        
        # Default to full reset
        logger.info(f"Resetting chat '{active_chat}' at path: {chat_file}")
        try:
            with open(chat_file, 'w', encoding='utf-8') as f:
                json.dump([], f)
            
            # If this is the currently active chat, also clear it in memory
            if hasattr(self.voice_chat_system, 'llm_chat') and \
               hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
                session_manager = self.voice_chat_system.llm_chat.session_manager
                if session_manager.get_active_chat_name() == active_chat:
                    session_manager.current_chat.clear()
                    logger.info(f"Cleared active chat '{active_chat}' from memory")
            
            return f"Chat '{active_chat}' reset successfully."
        except Exception as e:
            logger.error(f"Error resetting chat: {e}")
            return f"Error resetting chat '{active_chat}'."
    
    def _handle_remove_last(self, command_text, chat_history, chat_file, active_chat):
        """Handle remove last X messages command."""
        # First try matching a digit number
        match = re.search(r'remove last\s+(\d+)', command_text)
        
        # If no digit found, try matching a word number
        if not match:
            word_pattern = '|'.join(self.word_to_number.keys())
            match = re.search(f'remove last\\s+({word_pattern})\\b', command_text)
            
            if match:
                word = match.group(1)
                message_count = self.word_to_number.get(word, 0)
                logger.info(f"Converted word '{word}' to number {message_count}")
            else:
                # Default to 1 if no number specified
                if "remove last message" in command_text or "remove last messages" in command_text:
                    message_count = 1
                    logger.info("No specific number found, defaulting to 1 message")
                else:
                    logger.warning("No number found in remove last command")
                    return "Please specify how many messages to remove (e.g., 'remove last 2 messages')."
        else:
            try:
                message_count = int(match.group(1))
            except ValueError:
                logger.warning(f"Invalid number in command: {match.group(1)}")
                return "Please specify a valid number of messages to remove."
        
        if message_count <= 0:
            return "Please specify a positive number of messages to remove."
            
        logger.info(f"Removing last {message_count} message pairs from chat '{active_chat}'")
        
        # Check if there are messages to remove
        if not chat_history:
            logger.info("No messages to remove - history is empty")
            return "No messages to remove."
            
        # Each pair consists of a user message and an assistant message
        messages_to_remove = min(message_count * 2, len(chat_history))
        
        if messages_to_remove > 0:
            # Remove the last N pairs
            chat_history = chat_history[:-messages_to_remove]
            
            # Save the updated history
            try:
                with open(chat_file, 'w', encoding='utf-8') as f:
                    json.dump(chat_history, f, indent=2)
                
                # If this is the currently active chat, also update it in memory
                if hasattr(self.voice_chat_system, 'llm_chat') and \
                   hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
                    session_manager = self.voice_chat_system.llm_chat.session_manager
                    if session_manager.get_active_chat_name() == active_chat:
                        session_manager.current_chat.messages = chat_history
                        logger.info(f"Updated active chat '{active_chat}' in memory")
                
                pairs_removed = messages_to_remove // 2
                logger.info(f"Removed last {pairs_removed} conversation pairs from '{active_chat}'")
                
                if pairs_removed == 1:
                    return f"Last message removed from '{active_chat}'."
                else:
                    return f"Last {pairs_removed} messages removed from '{active_chat}'."
            except Exception as e:
                logger.error(f"Error saving updated history: {e}")
                return f"Error removing messages from '{active_chat}'."
        else:
            return "No messages to remove."
    
    def attach_system(self, voice_chat_system):
        """Attach voice chat system."""
        self.voice_chat_system = voice_chat_system
        logger.info(f"Message management module attached. System type: {type(voice_chat_system)}")