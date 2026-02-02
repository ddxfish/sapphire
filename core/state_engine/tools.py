# core/state_engine/tools.py
"""
State Engine Tools - Exposed to AI when state_engine_enabled.

Simplified tool set:
- get_state: Check values
- set_state: Modify values (also handles choices and riddle answers)
- advance_scene: Move story forward
- roll_dice: Random outcomes
- move: Navigation (if enabled)

All tool returns include full context block with scene, state, and clues.
"""

import logging
import random
from typing import Any, Tuple

logger = logging.getLogger(__name__)

# Tool names for detection
STATE_TOOL_NAMES = {'get_state', 'set_state', 'roll_dice', 'advance_scene', 'move'}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_state",
            "description": "Get current game/simulation state. Call with no key to see all state, or specify a key for one value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Optional: specific state key to retrieve. Omit for all state."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_state",
            "description": "Set a game/simulation state value. Use this for regular state changes, making choices, and answering riddles. The system validates automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "State key to set"
                    },
                    "value": {
                        "description": "New value (string, number, boolean, or array)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for this change"
                    }
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "advance_scene",
            "description": "Attempt to advance to the next scene/chapter. Will fail if prerequisites aren't met (choices unmade, blockers active). Returns the new scene content on success.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for advancing (what triggered the transition)"
                    }
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll dice for random outcomes. Returns individual rolls and total.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of dice to roll",
                        "minimum": 1,
                        "maximum": 20
                    },
                    "sides": {
                        "type": "integer",
                        "description": "Number of sides per die (e.g., 6 for d6, 20 for d20)",
                        "minimum": 2,
                        "maximum": 100
                    }
                },
                "required": ["count", "sides"]
            }
        }
    },
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
                        "description": "Brief reason for movement"
                    }
                },
                "required": ["direction"]
            }
        }
    }
]


def execute(function_name: str, arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """
    Execute a state tool.
    
    All successful results include the full context block.
    
    Args:
        function_name: Name of the state tool
        arguments: Tool arguments from LLM
        state_engine: StateEngine instance for this chat
        turn_number: Current turn number (user message count)
    
    Returns:
        (result_string, success_bool)
    """
    try:
        if function_name == "get_state":
            return _execute_get_state(arguments, state_engine, turn_number)
        
        elif function_name == "set_state":
            return _execute_set_state(arguments, state_engine, turn_number)
        
        elif function_name == "advance_scene":
            return _execute_advance_scene(arguments, state_engine, turn_number)
        
        elif function_name == "roll_dice":
            return _execute_roll_dice(arguments, state_engine, turn_number)
        
        elif function_name == "move":
            return _execute_move(arguments, state_engine, turn_number)
        
        else:
            return f"Unknown state tool: {function_name}", False
    
    except Exception as e:
        logger.error(f"State tool error in {function_name}: {e}", exc_info=True)
        return f"Error: {str(e)}", False


def _execute_get_state(arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """Get state - returns context block."""
    key = arguments.get("key")
    
    if key:
        # Special handling for scene_turns pseudo-variable
        if key == "scene_turns":
            scene_turns = state_engine.get_scene_turns(turn_number)
            summary = f"scene_turns = {scene_turns}"
        else:
            value = state_engine.get_state(key)
            if value is None:
                return f"Key '{key}' not found in state", False
            summary = f"{key} = {_format_value(value)}"
    else:
        summary = "Current state:"
    
    context = state_engine.get_context_block(turn_number, summary)
    return context, True


def _execute_set_state(arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """Set state - handles regular state, choices, and riddles."""
    key = arguments.get("key")
    value = arguments.get("value")
    reason = arguments.get("reason", "")
    
    if not key:
        return "Error: key is required", False
    
    # Let engine handle routing to choices/riddles based on key type
    success, msg = state_engine.set_state(key, value, "ai", turn_number, reason)
    
    if success:
        context = state_engine.get_context_block(turn_number, msg)
        return context, True
    else:
        return msg, False


def _execute_advance_scene(arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """Advance to next scene - validates prerequisites."""
    reason = arguments.get("reason", "story progression")

    # Check if already advanced this turn
    can_advance, msg = state_engine.can_advance_this_turn(turn_number)
    if not can_advance:
        return f"Error: {msg}", False

    # Get iterator config
    if not state_engine.progressive_config:
        return "Error: No progressive story configured", False

    iterator_key = state_engine.progressive_config.get("iterator")
    if not iterator_key:
        return "Error: No scene iterator configured", False
    
    # Get current value
    current = state_engine.get_state(iterator_key)
    if current is None:
        return f"Error: Iterator '{iterator_key}' not found in state", False
    
    if not isinstance(current, (int, float)):
        return f"Error: Iterator '{iterator_key}' must be numeric for advance_scene", False
    
    # Calculate next value
    new_value = int(current) + 1
    
    # Check max constraint
    entry = state_engine.get_state_full(iterator_key)
    if entry and entry.get("constraints"):
        constraints = entry["constraints"]
        max_val = constraints.get("max")
        if max_val is not None and new_value > max_val:
            return f"Cannot advance: already at final scene ({current}/{max_val})", False
    
    # Try to set - this will check choice blockers
    success, msg = state_engine.set_state(iterator_key, new_value, "ai", turn_number, reason)
    
    if success:
        state_engine.mark_advanced(turn_number)
        summary = f"✓ Advanced to scene {new_value}"
        context = state_engine.get_context_block(turn_number, summary)
        return context, True
    else:
        return msg, False


def _execute_roll_dice(arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """Roll dice - returns result with context."""
    count = arguments.get("count", 1)
    sides = arguments.get("sides", 6)
    
    # Validate
    count = max(1, min(20, int(count)))
    sides = max(2, min(100, int(sides)))
    
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    
    if count == 1:
        summary = f"🎲 Rolled d{sides}: {total}"
    else:
        summary = f"🎲 Rolled {count}d{sides}: {rolls} = {total}"
    
    # Log the roll to state (for audit trail)
    state_engine.set_state(
        "_last_roll", 
        {"dice": f"{count}d{sides}", "rolls": rolls, "total": total},
        "system", 
        turn_number, 
        "dice roll"
    )
    
    context = state_engine.get_context_block(turn_number, summary)
    return context, True


def _execute_move(arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """Move in direction - navigation mode."""
    direction = arguments.get("direction", "").strip()
    reason = arguments.get("reason", f"moved {direction}")
    
    if not direction:
        return "Error: direction is required", False
    
    if not state_engine.navigation or not state_engine.navigation.is_enabled:
        return "Error: This preset doesn't use room navigation. Use set_state() instead.", False
    
    success, msg = state_engine.navigation.move(direction, turn_number, reason)
    
    if success:
        context = state_engine.get_context_block(turn_number, msg)
        return context, True
    else:
        return msg, False


def _format_value(value: Any) -> str:
    """Format a value for display."""
    if value is None:
        return "[not set]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if not value:
            return "[]"
        return str(value)
    return str(value)