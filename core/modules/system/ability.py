# modules/system/ability.py
import logging
from core.modules.system.toolsets import toolset_manager

logger = logging.getLogger(__name__)

class AbilityManager:
    """Manages the AI's enabled functions (toolsets)."""

    def __init__(self):
        self.voice_chat_system = None

    def attach_system(self, voice_chat_system):
        """Attach voice chat system reference."""
        self.voice_chat_system = voice_chat_system
        logger.info("AbilityManager attached to system")

    def process(self, user_input: str):
        """Process a toolset change command."""
        if not user_input or not user_input.strip():
            return self._list_toolsets()

        toolset_name = user_input.strip().lower()

        if self._is_valid_toolset(toolset_name):
            return self._apply_toolset(toolset_name)

        available = self._get_available_toolsets()
        return f"Toolset '{toolset_name}' not found. Available toolsets: {', '.join(available)}"

    def _is_valid_toolset(self, toolset_name: str) -> bool:
        """Check if toolset name is valid."""
        if not self.voice_chat_system:
            return False

        if hasattr(self.voice_chat_system.llm_chat, 'function_manager'):
            return self.voice_chat_system.llm_chat.function_manager.is_valid_toolset(toolset_name)

        return toolset_manager.toolset_exists(toolset_name)

    def _get_available_toolsets(self) -> list:
        """Get list of all available toolsets."""
        if not self.voice_chat_system:
            return toolset_manager.get_toolset_names()

        if hasattr(self.voice_chat_system.llm_chat, 'function_manager'):
            return self.voice_chat_system.llm_chat.function_manager.get_available_toolsets()

        return toolset_manager.get_toolset_names()

    def _apply_toolset(self, toolset_name: str):
        """Applies a new set of enabled functions and saves to chat settings."""
        if not self.voice_chat_system:
            return "System reference not available."

        try:
            if hasattr(self.voice_chat_system.llm_chat, 'function_manager'):
                self.voice_chat_system.llm_chat.function_manager.update_enabled_functions([toolset_name])

                if hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
                    self.voice_chat_system.llm_chat.session_manager.update_chat_settings({'toolset': toolset_name})
                    logger.info(f"Saved toolset '{toolset_name}' to chat settings")

                self.voice_chat_system.tts.speak(f"Toolset {toolset_name} activated.")

                toolset_info = self.voice_chat_system.llm_chat.function_manager.get_current_toolset_info()
                function_count = toolset_info.get('function_count', 0)

                return f"Switched to toolset: '{toolset_name}' ({function_count} functions enabled)."
            else:
                logger.error("FunctionManager not found on llm_chat instance.")
                return "Error: Could not access function manager."

        except Exception as e:
            logger.error(f"Error applying toolset '{toolset_name}': {e}", exc_info=True)
            return f"Error applying toolset: {str(e)}"

    def _list_toolsets(self):
        """Lists available toolsets."""
        available = self._get_available_toolsets()
        return "Available toolsets: " + ", ".join(available)
