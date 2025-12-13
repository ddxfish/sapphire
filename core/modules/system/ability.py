# modules/system/ability.py
import logging
from core.modules.system.toolsets import toolset_manager

logger = logging.getLogger(__name__)

class AbilityManager:
    """Manages the AI's enabled functions (abilities)."""

    def __init__(self):
        self.voice_chat_system = None

    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info("AbilityManager attached to system")

    def process(self, user_input: str):
        """Process an ability change command."""
        if not user_input or not user_input.strip():
            return self._list_abilities()
        
        ability_name = user_input.strip().lower()

        # Check if ability is valid using function_manager
        if self._is_valid_ability(ability_name):
            return self._apply_ability(ability_name)
        
        available = self._get_available_abilities()
        return f"Ability '{ability_name}' not found. Available abilities: {', '.join(available)}"

    def _is_valid_ability(self, ability_name: str) -> bool:
        """Check if ability name is valid."""
        if not self.voice_chat_system:
            return False
        
        if hasattr(self.voice_chat_system.llm_chat, 'function_manager'):
            return self.voice_chat_system.llm_chat.function_manager.is_valid_ability(ability_name)
        
        # Fallback to toolset check only
        return toolset_manager.toolset_exists(ability_name)

    def _get_available_abilities(self) -> list:
        """Get list of all available abilities."""
        if not self.voice_chat_system:
            return toolset_manager.get_toolset_names()
        
        if hasattr(self.voice_chat_system.llm_chat, 'function_manager'):
            return self.voice_chat_system.llm_chat.function_manager.get_available_abilities()
        
        # Fallback to toolsets only
        return toolset_manager.get_toolset_names()

    def _apply_ability(self, ability_name: str):
        """Applies a new set of enabled functions and saves to chat settings."""
        if not self.voice_chat_system:
            return "System reference not available."

        try:
            if hasattr(self.voice_chat_system.llm_chat, 'function_manager'):
                # Pass ability name directly - function_manager handles everything
                self.voice_chat_system.llm_chat.function_manager.update_enabled_functions([ability_name])
                
                # Save to chat settings JSON (like the UI does)
                if hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
                    self.voice_chat_system.llm_chat.session_manager.update_chat_settings({'ability': ability_name})
                    logger.info(f"Saved ability '{ability_name}' to chat settings")
                
                # Announce the change
                self.voice_chat_system.tts.speak(f"Ability {ability_name} activated.")
                
                # Get info about what was enabled
                ability_info = self.voice_chat_system.llm_chat.function_manager.get_current_ability_info()
                function_count = ability_info.get('function_count', 0)
                
                return f"Switched to ability set: '{ability_name}' ({function_count} functions enabled)."
            else:
                logger.error("FunctionManager not found on llm_chat instance.")
                return "Error: Could not access function manager."

        except Exception as e:
            logger.error(f"Error applying ability '{ability_name}': {e}", exc_info=True)
            return f"Error applying ability: {str(e)}"

    def _list_abilities(self):
        """Lists available ability sets."""
        available = self._get_available_abilities()
        return "Available abilities: " + ", ".join(available)