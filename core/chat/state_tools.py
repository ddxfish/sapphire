# core/chat/state_tools.py
"""
State Engine Tools - Exposed to AI when state_engine_enabled.
These are NOT loaded by FunctionManager's auto-scan - they're injected when state is active.
"""

import logging
import random
from typing import Any, Tuple

logger = logging.getLogger(__name__)

# Tool names for detection
STATE_TOOL_NAMES = {'get_state', 'set_state', 'roll_dice', 'increment_counter'}

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
            "description": "Set a game/simulation state value. Always provide a reason for the change.",
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
                        "description": "Brief reason for this change (logged for history)"
                    }
                },
                "required": ["key", "value"]
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
            "name": "increment_counter",
            "description": "Atomically increment (or decrement) a numeric state value. Safer than get+set for counters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "State key to increment (must be numeric)"
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Amount to add (negative to subtract)",
                        "default": 1
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for this change"
                    }
                },
                "required": ["key"]
            }
        }
    }
]


def execute(function_name: str, arguments: dict, state_engine, turn_number: int) -> Tuple[str, bool]:
    """
    Execute a state tool.
    
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
            key = arguments.get("key")
            
            if key:
                value = state_engine.get_state(key)
                if value is None:
                    return f"Key '{key}' not found in state", False
                return f"{key} = {_format_value(value)}", True
            else:
                state = state_engine.get_visible_state()
                if not state:
                    return "(no state set)", True
                lines = [f"{k}: {_format_value(v)}" for k, v in sorted(state.items())]
                return "\n".join(lines), True
        
        elif function_name == "set_state":
            key = arguments.get("key")
            value = arguments.get("value")
            reason = arguments.get("reason", "")
            
            if not key:
                return "Error: key is required", False
            
            success, msg = state_engine.set_state(key, value, "ai", turn_number, reason)
            return msg, success
        
        elif function_name == "roll_dice":
            count = arguments.get("count", 1)
            sides = arguments.get("sides", 6)
            
            # Validate
            count = max(1, min(20, int(count)))
            sides = max(2, min(100, int(sides)))
            
            rolls = [random.randint(1, sides) for _ in range(count)]
            total = sum(rolls)
            
            if count == 1:
                result = f"🎲 Rolled d{sides}: {total}"
            else:
                result = f"🎲 Rolled {count}d{sides}: {rolls} = {total}"
            
            # Log the roll to state history (optional - for audit trail)
            state_engine.set_state(
                "_last_roll", 
                {"dice": f"{count}d{sides}", "rolls": rolls, "total": total},
                "system", 
                turn_number, 
                "dice roll"
            )
            
            return result, True
        
        elif function_name == "increment_counter":
            key = arguments.get("key")
            amount = arguments.get("amount", 1)
            reason = arguments.get("reason", f"increment by {amount}")
            
            if not key:
                return "Error: key is required", False
            
            # Get current value
            current = state_engine.get_state(key)
            if current is None:
                return f"Error: key '{key}' not found", False
            
            if not isinstance(current, (int, float)):
                return f"Error: '{key}' is not numeric (value: {current})", False
            
            new_value = current + int(amount)
            clamped = False
            
            # Check constraints
            entry = state_engine.get_state_full(key)
            if entry and entry.get("constraints"):
                constraints = entry["constraints"]
                if "min" in constraints and new_value < constraints["min"]:
                    new_value = constraints["min"]
                    clamped = True
                if "max" in constraints and new_value > constraints["max"]:
                    new_value = constraints["max"]
                    clamped = True
            
            success, msg = state_engine.set_state(key, new_value, "ai", turn_number, reason)
            if success:
                label = entry.get("label", key) if entry else key
                if clamped:
                    return f"✓ {label}: {current} → {new_value} (clamped to bounds)", True
                return f"✓ {label}: {current} → {new_value}", True
            return msg, False
        
        else:
            return f"Unknown state tool: {function_name}", False
    
    except Exception as e:
        logger.error(f"State tool error in {function_name}: {e}", exc_info=True)
        return f"Error: {str(e)}", False


def _format_value(value: Any) -> str:
    """Format a value for display."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if not value:
            return "[]"
        return str(value)
    return str(value)