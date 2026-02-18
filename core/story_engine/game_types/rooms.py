# core/story_engine/game_types/rooms.py
"""
Rooms Game Type - Graph-based room navigation with compass directions.

Examples: Zork, five_rooms (with linear room numbers)
"""

from typing import Optional
from .base import BaseGameType


class RoomsGameType(BaseGameType):
    """
    Room-based game type with graph navigation.
    
    - Iterator is typically 'player_room'
    - Content shows only current room (current_only mode)
    - Navigation feature enabled with move() tool
    - Supports choices and riddles
    """
    
    name = "rooms"
    features = ["choices", "riddles", "navigation"]
    prompt_mode = "current_only"
    
    def get_iterator_key(self, config: dict) -> Optional[str]:
        """Get position key from navigation config."""
        nav = config.get("navigation", {})
        return nav.get("position_key", config.get("iterator", "player_room"))
    
    def get_extra_tools(self) -> list[dict]:
        """Add move tool for room navigation."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "move",
                    "description": "Move in a direction (for room-based navigation). Use compass directions (north, south, east, west) or positional (up, down). The system will validate if that exit exists.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "direction": {
                                "type": "string",
                                "description": "Direction to move: north/n, south/s, east/e, west/w, up/u, down/d, etc."
                            },
                            "reason": {
                                "type": "string",
                                "description": "Brief reason for movement (for history)"
                            }
                        },
                        "required": ["direction"]
                    }
                }
            }
        ]
    
    def validate_preset(self, preset: dict) -> tuple[bool, str]:
        """Validate rooms preset structure."""
        config = preset.get("progressive_prompt", {})
        nav = config.get("navigation", {})
        
        if not nav:
            return False, "Rooms game type requires 'navigation' in progressive_prompt"
        
        connections = nav.get("connections", {})
        if not connections:
            return False, "Navigation requires 'connections' map"
        
        position_key = nav.get("position_key", "player_room")
        initial = preset.get("initial_state", {})
        
        if position_key not in initial:
            return False, f"Position key '{position_key}' not found in initial_state"
        
        # Check starting room exists in connections
        start_room = initial[position_key].get("value")
        if start_room and start_room not in connections:
            return False, f"Starting room '{start_room}' not found in connections"
        
        return True, ""
