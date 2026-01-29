# core/state_engine/validation.py
"""
State validation: constraints, type inference, blocker checking.
"""

from typing import Any, Callable, Optional


def is_system_key(key: str) -> bool:
    """Check if key is system-managed (starts with _)."""
    return key.startswith("_")


def infer_type(value: Any) -> str:
    """Infer JSON schema type from Python value."""
    if isinstance(value, bool):
        return "boolean"
    elif isinstance(value, int):
        return "integer"
    elif isinstance(value, float):
        return "number"
    elif isinstance(value, list):
        return "array"
    elif isinstance(value, dict):
        return "object"
    return "string"


def validate_value(
    key: str,
    value: Any,
    constraints: Optional[dict],
    state_getter: Callable[[str], Any]
) -> tuple[bool, str]:
    """
    Validate value against constraints.
    
    Args:
        key: State key being set
        value: New value to validate
        constraints: Constraint dict from preset (min, max, adjacent, options, blockers)
        state_getter: Callable to get current state values
        
    Returns:
        (valid, error_message)
    """
    if not constraints:
        return True, ""
    
    # Integer bounds
    if "min" in constraints and isinstance(value, (int, float)):
        if value < constraints["min"]:
            return False, f"{key} must be >= {constraints['min']}"
    
    if "max" in constraints and isinstance(value, (int, float)):
        if value > constraints["max"]:
            return False, f"{key} must be <= {constraints['max']}"
    
    # Adjacency - new value must be within ±N of current value
    if "adjacent" in constraints and isinstance(value, (int, float)):
        current = state_getter(key)
        if current is not None:
            max_step = constraints["adjacent"]
            if abs(value - current) > max_step:
                return False, f"Can only move ±{max_step} at a time (current: {current}, attempted: {value})"
    
    # Enum options
    if "options" in constraints:
        if value not in constraints["options"]:
            return False, f"{key} must be one of: {constraints['options']}"
    
    # Blockers - conditions that must be met before allowing this value
    if "blockers" in constraints:
        valid, error = _check_blockers(key, value, constraints["blockers"], state_getter)
        if not valid:
            return False, error
    
    return True, ""


def _check_blockers(
    key: str,
    value: Any,
    blockers: list[dict],
    state_getter: Callable[[str], Any]
) -> tuple[bool, str]:
    """Check blocker conditions for a state change."""
    for blocker in blockers:
        # Check if this blocker applies to the target value
        target = blocker.get("target")
        if target is not None:
            targets = target if isinstance(target, list) else [target]
            if value not in targets:
                continue  # This blocker doesn't apply
        
        # Check if blocker has a "from" condition
        from_values = blocker.get("from")
        if from_values is not None:
            current = state_getter(key)
            from_list = from_values if isinstance(from_values, list) else [from_values]
            if current not in from_list:
                continue  # Not coming from a blocked origin
        
        # Check required conditions
        requires = blocker.get("requires", {})
        for req_key, req_value in requires.items():
            actual = state_getter(req_key)
            if actual != req_value:
                message = blocker.get("message", f"Cannot set {key} to {value}: requires {req_key}={req_value}")
                return False, message
    
    return True, ""


def clamp_to_bounds(value: Any, constraints: Optional[dict]) -> Any:
    """Clamp numeric value to min/max bounds if defined."""
    if not constraints or not isinstance(value, (int, float)):
        return value
    
    if "min" in constraints and value < constraints["min"]:
        return constraints["min"]
    if "max" in constraints and value > constraints["max"]:
        return constraints["max"]
    
    return value
