# core/state_engine/game_types/__init__.py
"""
Game Types - Define how different game paradigms work.

Each game type specifies:
- Which features are available
- Prompt reveal mode (cumulative vs current_only)
- How to determine the iterator key
- Extra tools specific to that game type
"""

from typing import Optional

from .base import BaseGameType
from .linear import LinearGameType
from .rooms import RoomsGameType

__all__ = ['BaseGameType', 'LinearGameType', 'RoomsGameType', 'GAME_TYPES', 'detect_game_type', 'get_game_type']

GAME_TYPES = {
    'linear': LinearGameType,
    'rooms': RoomsGameType,
}


def detect_game_type(preset: dict) -> str:
    """
    Auto-detect game type from preset configuration.
    
    Returns game type name string.
    """
    config = preset.get("progressive_prompt", {})
    
    # If navigation is configured, it's a rooms-based game
    if config.get("navigation"):
        return "rooms"
    
    # Default to linear
    return "linear"


def get_game_type(preset: dict) -> BaseGameType:
    """
    Get game type instance for a preset.
    
    Uses explicit game_type field if present, otherwise auto-detects.
    """
    type_name = preset.get("game_type")
    
    if not type_name:
        type_name = detect_game_type(preset)
    
    cls = GAME_TYPES.get(type_name, LinearGameType)
    return cls()
