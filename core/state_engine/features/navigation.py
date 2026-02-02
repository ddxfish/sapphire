# core/state_engine/features/navigation.py
"""
Navigation - Room-based movement with compass directions.

Handles graph-based room connections and direction aliases.
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Direction aliases for user convenience
DIRECTION_ALIASES = {
    "n": "north", "s": "south", "e": "east", "w": "west",
    "u": "up", "d": "down", "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest"
}


class NavigationManager:
    """Manages room-based navigation for a state engine instance."""
    
    def __init__(
        self,
        preset: dict,
        state_getter: Callable[[str], Any],
        state_setter: Callable,
        scene_turns_getter: Optional[Callable[[], int]] = None
    ):
        self._config = preset.get("progressive_prompt", {}).get("navigation", {})
        self._get_state = state_getter
        self._set_state = state_setter
    
    @property
    def config(self) -> dict:
        """Raw navigation config."""
        return self._config
    
    @property
    def is_enabled(self) -> bool:
        """Check if navigation is configured."""
        return bool(self._config and self._config.get("connections"))
    
    @property
    def position_key(self) -> str:
        """State key for player position."""
        return self._config.get("position_key", "player_room")
    
    @property
    def connections(self) -> dict:
        """Room connection graph."""
        return self._config.get("connections", {})
    
    def get_current_room(self) -> Optional[str]:
        """Get current room name."""
        return self._get_state(self.position_key)
    
    def get_available_exits(self) -> list:
        """Get available exit directions from current room."""
        if not self.is_enabled:
            return []

        current_room = self.get_current_room()
        if not current_room or current_room not in self.connections:
            return []

        room_exits = self.connections[current_room]
        # Filter out metadata keys starting with _
        return [d for d in room_exits.keys() if not d.startswith("_")]

    def get_exits_with_descriptions(self) -> list:
        """Get exits with room names based on visited status."""
        if not self.is_enabled:
            return []

        current_room = self.get_current_room()
        if not current_room or current_room not in self.connections:
            return []

        room_names = self._config.get("room_names", {})
        visited = self._get_state("_visited_rooms") or []

        exits = []
        room_exits = self.connections[current_room]
        for direction, destination in room_exits.items():
            if direction.startswith("_"):
                continue
            if destination in visited:
                name = room_names.get(destination, destination)
                exits.append(f"{direction.upper()}: {name}")
            else:
                exits.append(f"{direction.upper()}: ???")
        return exits
    
    def get_room_for_direction(self, direction: str) -> tuple[Optional[str], str]:
        """
        Get destination room for a direction from current position.
        
        Args:
            direction: Direction to move (supports aliases like 'n' for 'north')
            
        Returns:
            (destination_room, error_message) - destination is None if invalid
        """
        if not self.is_enabled:
            return None, "Navigation not configured for this preset"
        
        current_room = self.get_current_room()
        if not current_room:
            return None, f"Current position unknown ({self.position_key} not set)"
        
        if current_room not in self.connections:
            return None, f"No exits defined for '{current_room}'"
        
        room_exits = self.connections[current_room]
        direction_lower = direction.lower().strip()
        
        # Try direct match first
        if direction_lower in room_exits:
            return room_exits[direction_lower], ""
        
        # Try expanding alias (n -> north)
        if direction_lower in DIRECTION_ALIASES:
            full_dir = DIRECTION_ALIASES[direction_lower]
            if full_dir in room_exits:
                return room_exits[full_dir], ""
        
        # Try contracting to alias (north -> n)
        for short, full in DIRECTION_ALIASES.items():
            if direction_lower == full and short in room_exits:
                return room_exits[short], ""
        
        available = self.get_exits_with_descriptions()
        return None, f"Can't go {direction}. Exits: {', '.join(available)}"
    
    def move(self, direction: str, turn_number: int, reason: str = None) -> tuple[bool, str]:
        """
        Move in a direction.
        
        Args:
            direction: Direction to move
            turn_number: Current turn
            reason: Optional reason for movement
            
        Returns:
            (success, message)
        """
        if not self.is_enabled:
            return False, "Navigation not configured. Use set_state() instead."
        
        current_room = self.get_current_room()
        destination, error = self.get_room_for_direction(direction)
        
        if not destination:
            return False, error
        
        # Use state_setter to move - this handles blockers automatically
        reason = reason or f"moved {direction}"
        success, msg = self._set_state(self.position_key, destination, "ai", turn_number, reason)
        
        if success:
            # Track visited rooms for fog-of-war
            visited = self._get_state("_visited_rooms") or []
            if destination not in visited:
                visited = visited + [destination]
                self._set_state("_visited_rooms", visited, "system", turn_number, "room visited")

            # Show exits with names for visited rooms, ??? for unvisited
            room_names = self._config.get("room_names", {})
            dest_name = room_names.get(destination, destination)
            exits = self.get_exits_with_descriptions()
            exits_str = f"\nExits: {', '.join(exits)}" if exits else ""
            return True, f"✓ Moved to {dest_name}{exits_str}"

        return False, msg
