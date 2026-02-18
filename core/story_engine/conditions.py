# core/story_engine/conditions.py
"""
Condition parsing for segment keys and state matching.

Syntax: "base_key?condition1,condition2,..."
Examples:
    "3" -> base="3", no conditions
    "3?alive" -> base="3", alive=true
    "3?health>50" -> base="3", health > 50
    "3?mood=happy,trust>=30" -> base="3", mood="happy" AND trust >= 30
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def parse_segment_key(key: str) -> tuple[str, list[tuple[str, str, Any]]]:
    """
    Parse segment key into base key and conditions.
    
    Args:
        key: Segment key like "3?health>50,alive"
        
    Returns:
        (base_key, [(state_key, operator, expected_value), ...])
    """
    if "?" not in key:
        return key, []
    
    base, condition_str = key.split("?", 1)
    conditions = []
    
    for cond in condition_str.split(","):
        cond = cond.strip()
        if not cond:
            continue
        
        # Check for comparison operators (order matters - check multi-char first)
        op = "="
        k, v = None, None
        
        for check_op in (">=", "<=", "!=", ">", "<", "="):
            if check_op in cond:
                parts = cond.split(check_op, 1)
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip()
                    op = check_op
                    break
        
        if k is None:
            # Boolean shorthand: "key" means key=true
            k = cond
            v = True
            op = "="
        else:
            # Parse value type
            v = _parse_value(v)
        
        conditions.append((k, op, v))
    
    return base, conditions


def _parse_value(v: Any) -> Any:
    """Parse string value to appropriate type."""
    if not isinstance(v, str):
        return v
    
    if v.lower() == "true":
        return True
    elif v.lower() == "false":
        return False
    
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v  # Keep as string


def match_conditions(
    conditions: list[tuple[str, str, Any]],
    state_getter: Callable[[str], Any],
    scene_turns_getter: Optional[Callable[[], int]] = None
) -> bool:
    """
    Check if all conditions match current state (AND logic).
    
    Args:
        conditions: List of (state_key, operator, expected_value)
        state_getter: Callable that returns state value for a key
        scene_turns_getter: Optional callable that returns current scene_turns
        
    Returns:
        True if all conditions match
    """
    for state_key, op, expected in conditions:
        # Handle scene_turns pseudo-variable
        if state_key == "scene_turns":
            if scene_turns_getter:
                actual = scene_turns_getter()
            else:
                actual = 0
            logger.debug(f"[COND] scene_turns check: actual={actual}, op={op}, expected={expected}")
        else:
            actual = state_getter(state_key)
        
        if not _compare(actual, op, expected):
            return False
    
    return True


def _compare(actual: Any, op: str, expected: Any) -> bool:
    """Compare actual value against expected using operator."""
    if op == "=":
        return actual == expected
    elif op == "!=":
        return actual != expected
    elif op == ">":
        return isinstance(actual, (int, float)) and actual > expected
    elif op == "<":
        return isinstance(actual, (int, float)) and actual < expected
    elif op == ">=":
        return isinstance(actual, (int, float)) and actual >= expected
    elif op == "<=":
        return isinstance(actual, (int, float)) and actual <= expected
    return False
