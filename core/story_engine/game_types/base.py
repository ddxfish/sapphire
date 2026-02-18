# core/story_engine/game_types/base.py
"""
Base Game Type - Abstract base class for game paradigms.
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseGameType(ABC):
    """
    Abstract base class for game types.
    
    Game types define:
    - Which features are available (choices, riddles, navigation)
    - Prompt reveal mode (cumulative vs current_only)
    - Iterator key derivation
    - Extra tools specific to this game type
    """
    
    # Class attributes (override in subclasses)
    name: str = "base"
    features: list[str] = []  # Available features: 'choices', 'riddles', 'navigation'
    prompt_mode: str = "cumulative"  # 'cumulative' or 'current_only'
    
    @abstractmethod
    def get_iterator_key(self, config: dict) -> Optional[str]:
        """
        Get the state key used as the iterator for this game type.
        
        Args:
            config: The progressive_prompt config from preset
            
        Returns:
            State key name or None
        """
        pass
    
    def get_extra_tools(self) -> list[dict]:
        """
        Get additional tool definitions specific to this game type.
        
        Returns:
            List of tool definition dicts (OpenAI function format)
        """
        return []
    
    def validate_preset(self, preset: dict) -> tuple[bool, str]:
        """
        Validate a preset for this game type.
        
        Args:
            preset: Full preset dict
            
        Returns:
            (valid, error_message)
        """
        return True, ""
    
    def __repr__(self):
        return f"<{self.__class__.__name__} features={self.features} mode={self.prompt_mode}>"
