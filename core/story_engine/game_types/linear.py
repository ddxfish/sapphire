# core/story_engine/game_types/linear.py
"""
Linear Game Type - Scene-based progression with cumulative content reveal.

Examples: Perihelion (romance tragedy), choice_riddle_test
"""

from typing import Optional
from .base import BaseGameType


class LinearGameType(BaseGameType):
    """
    Linear game type with numbered scenes.
    
    - Iterator is typically 'scene' or 'chapter'
    - Content reveals cumulatively (scene 3 shows scenes 1, 2, 3)
    - Supports choices and riddles
    """
    
    name = "linear"
    features = ["choices", "riddles"]
    prompt_mode = "cumulative"
    
    def get_iterator_key(self, config: dict) -> Optional[str]:
        """Get iterator key, defaulting to 'scene'."""
        return config.get("iterator", "scene")
    
    def validate_preset(self, preset: dict) -> tuple[bool, str]:
        """Validate linear preset structure."""
        config = preset.get("progressive_prompt", {})
        iterator = config.get("iterator")
        
        if not iterator:
            return True, ""  # Will use default 'scene'
        
        # Check initial_state has the iterator key
        initial = preset.get("initial_state", {})
        if iterator not in initial:
            return False, f"Iterator '{iterator}' not found in initial_state"
        
        return True, ""
