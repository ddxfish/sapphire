# core/story_engine/tools.py
"""
Story Engine Tools - Exposed to AI when story engine is enabled.

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
STORY_TOOL_NAMES = {'get_state', 'set_state', 'roll_dice', 'advance_scene', 'move'}

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
            "description": "Roll dice for random outcomes. Auto-detects active riddles with dice_dc and applies bypass effects on success.",
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
                    },
                    "riddle_id": {
                        "type": "string",
                        "description": "Optional: riddle ID to bypass. On success, applies dice_success_sets from riddle config."
                    },
                    "dc": {
                        "type": "integer",
                        "description": "Optional: difficulty class. Roll must meet or exceed this to succeed."
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


def execute(function_name: str, arguments: dict, story_engine, turn_number: int) -> Tuple[str, bool]:
    """
    Execute a state tool.
    
    All successful results include the full context block.
    
    Args:
        function_name: Name of the state tool
        arguments: Tool arguments from LLM
        story_engine: StateEngine instance for this chat
        turn_number: Current turn number (user message count)
    
    Returns:
        (result_string, success_bool)
    """
    try:
        if function_name == "get_state":
            return _execute_get_state(arguments, story_engine, turn_number)
        
        elif function_name == "set_state":
            return _execute_set_state(arguments, story_engine, turn_number)
        
        elif function_name == "advance_scene":
            return _execute_advance_scene(arguments, story_engine, turn_number)
        
        elif function_name == "roll_dice":
            return _execute_roll_dice(arguments, story_engine, turn_number)
        
        elif function_name == "move":
            return _execute_move(arguments, story_engine, turn_number)
        
        else:
            return f"Unknown state tool: {function_name}", False
    
    except Exception as e:
        logger.error(f"State tool error in {function_name}: {e}", exc_info=True)
        return f"Error: {str(e)}", False


def _execute_get_state(arguments: dict, story_engine, turn_number: int) -> Tuple[str, bool]:
    """Get state - returns context block."""
    key = arguments.get("key")
    
    if key:
        # Special handling for scene_turns pseudo-variable
        if key == "scene_turns":
            scene_turns = story_engine.get_scene_turns(turn_number)
            summary = f"scene_turns = {scene_turns}"
        else:
            value = story_engine.get_state(key)
            if value is None:
                return f"Key '{key}' not found in state", False
            summary = f"{key} = {_format_value(value)}"
    else:
        summary = "Current state:"
    
    context = story_engine.get_context_block(turn_number, summary)
    return context, True


def _execute_set_state(arguments: dict, story_engine, turn_number: int) -> Tuple[str, bool]:
    """Set state - handles regular state, choices, and riddles."""
    key = arguments.get("key")
    value = arguments.get("value")
    reason = arguments.get("reason", "")

    if not key:
        return "Error: key is required", False

    # Check if this is a riddle attempt (before we call set_state)
    is_riddle_attempt = story_engine._riddles and story_engine._riddles.is_riddle_key(key)

    # Let engine handle routing to choices/riddles based on key type
    success, msg = story_engine.set_state(key, value, "ai", turn_number, reason)

    if success:
        context = story_engine.get_context_block(turn_number, msg)
        # Terminal instruction for riddle attempts - let player react to result
        if is_riddle_attempt:
            context += "\n\n⛔ STOP — Narrate this attempt to the player. Do NOT guess again or make additional tool calls this turn."
        return context, True
    else:
        return msg, False


def _execute_advance_scene(arguments: dict, story_engine, turn_number: int) -> Tuple[str, bool]:
    """Advance to next scene - validates prerequisites."""
    reason = arguments.get("reason", "story progression")

    # Check if already advanced this turn
    can_advance, msg = story_engine.can_advance_this_turn(turn_number)
    if not can_advance:
        return f"Error: {msg}", False

    # Get iterator config
    if not story_engine.progressive_config:
        return "Error: No progressive story configured", False

    iterator_key = story_engine.progressive_config.get("iterator")
    if not iterator_key:
        return "Error: No scene iterator configured", False
    
    # Get current value
    current = story_engine.get_state(iterator_key)
    if current is None:
        return f"Error: Iterator '{iterator_key}' not found in state", False
    
    if not isinstance(current, (int, float)):
        return f"Error: Iterator '{iterator_key}' must be numeric for advance_scene", False
    
    # Calculate next value
    new_value = int(current) + 1
    
    # Check max constraint
    entry = story_engine.get_state_full(iterator_key)
    if entry and entry.get("constraints"):
        constraints = entry["constraints"]
        max_val = constraints.get("max")
        if max_val is not None and new_value > max_val:
            return f"Cannot advance: already at final scene ({current}/{max_val})", False
    
    # Try to set - this will check choice blockers
    success, msg = story_engine.set_state(iterator_key, new_value, "ai", turn_number, reason)
    
    if success:
        story_engine.mark_advanced(turn_number)
        summary = f"✓ Advanced to scene {new_value}"
        context = story_engine.get_context_block(turn_number, summary)
        # Terminal instruction - AI must narrate new scene before more tool calls
        context += "\n\n⛔ STOP — Narrate this new scene to the player. Do NOT make additional tool calls this turn."
        return context, True
    else:
        return msg, False


def _execute_roll_dice(arguments: dict, story_engine, turn_number: int) -> Tuple[str, bool]:
    """Roll dice - returns result with context. Auto-detects active riddle with dice_dc."""
    count = arguments.get("count", 1)
    sides = arguments.get("sides", 6)
    riddle_id = arguments.get("riddle_id")
    dc = arguments.get("dc")

    # Validate
    count = max(1, min(20, int(count)))
    sides = max(2, min(100, int(sides)))

    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)

    if count == 1:
        summary = f"🎲 Rolled d{sides}: {total}"
    else:
        summary = f"🎲 Rolled {count}d{sides}: {rolls} = {total}"

    # Auto-detect active riddle with dice_dc if not specified
    if riddle_id is None and dc is None and story_engine._riddles:
        # Find active riddles (not solved, not locked) with dice_dc configured
        for riddle in story_engine._riddles.riddles:
            rid = riddle.get("id")
            if not rid:
                continue
            # Check if riddle has dice_dc
            riddle_dc = riddle.get("dice_dc")
            if riddle_dc is None:
                continue
            # Check if already solved or locked
            status = story_engine._riddles.get_status(rid)
            if status.get("solved") or status.get("locked"):
                continue
            # Check visibility - supports both scene (numeric) and room (string)
            visible_from_scene = riddle.get("visible_from_scene")
            visible_from_room = riddle.get("visible_from_room")
            current_pos = story_engine.get_state(story_engine._progressive_config.get("iterator")) if story_engine._progressive_config else None

            if visible_from_scene is not None:
                if isinstance(current_pos, (int, float)) and current_pos < visible_from_scene:
                    continue
            elif visible_from_room is not None:
                if isinstance(current_pos, str) and current_pos != visible_from_room:
                    continue
            # Found an active riddle with dice_dc - use it
            riddle_id = rid
            dc = riddle_dc
            logger.info(f"[DICE] Auto-detected riddle '{riddle_id}' with DC {dc}")
            break

    # Check for riddle bypass
    if riddle_id and dc is not None:
        dc = int(dc)
        if total >= dc:
            # Success! Apply riddle's dice_success_sets if available
            summary += f" vs DC {dc} — SUCCESS! (bypassed {riddle_id})"
            if story_engine._riddles:
                riddle = story_engine._riddles.get_by_id(riddle_id)
                if riddle:
                    # Mark riddle as solved via bypass
                    story_engine.set_state(f"_riddle_{riddle_id}_solved", True, "system",
                                          turn_number, "Dice bypass")
                    # Apply dice_success_sets (or fall back to success_sets)
                    success_sets = riddle.get("dice_success_sets") or riddle.get("success_sets", {})
                    for key, value in success_sets.items():
                        story_engine.set_state(key, value, "ai", turn_number, f"Dice bypass: {riddle_id}")
                        summary += f"\n  → {key} = {value}"
        else:
            summary += f" vs DC {dc} — FAILED (need {dc}+)"

    # Log the roll to state (for audit trail)
    story_engine.set_state(
        "_last_roll",
        {"dice": f"{count}d{sides}", "rolls": rolls, "total": total, "dc": dc, "riddle_id": riddle_id},
        "system",
        turn_number,
        "dice roll"
    )

    context = story_engine.get_context_block(turn_number, summary)
    return context, True


def _execute_move(arguments: dict, story_engine, turn_number: int) -> Tuple[str, bool]:
    """Move in direction - navigation mode."""
    direction = arguments.get("direction", "").strip()
    reason = arguments.get("reason", f"moved {direction}")
    
    if not direction:
        return "Error: direction is required", False
    
    if not story_engine.navigation or not story_engine.navigation.is_enabled:
        return "Error: This preset doesn't use room navigation. Use set_state() instead.", False
    
    success, msg = story_engine.navigation.move(direction, turn_number, reason)
    
    if success:
        context = story_engine.get_context_block(turn_number, msg)
        # Terminal instruction - AI must respond before more tool calls
        context += "\n\n⛔ STOP — Narrate this location to the player. Do NOT make additional tool calls this turn."
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