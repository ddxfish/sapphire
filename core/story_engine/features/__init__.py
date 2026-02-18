# core/story_engine/features/__init__.py
"""
Feature modules for story engine.

Features are optional capabilities that can be enabled per game type:
- choices: Binary forced decisions
- riddles: Collaborative puzzles with hidden answers
- navigation: Room-based movement with compass directions
"""

from typing import Any, Callable, Optional

from .choices import ChoiceManager
from .riddles import RiddleManager
from .navigation import NavigationManager

__all__ = ['ChoiceManager', 'RiddleManager', 'NavigationManager', 'FEATURE_CLASSES', 'load_feature']

FEATURE_CLASSES = {
    'choices': ChoiceManager,
    'riddles': RiddleManager,
    'navigation': NavigationManager,
}


def load_feature(
    name: str,
    preset: dict,
    state_getter: Callable[[str], Any],
    state_setter: Callable,
    scene_turns_getter: Optional[Callable[[], int]] = None
):
    """
    Load a feature manager instance.
    
    Args:
        name: Feature name ('choices', 'riddles', 'navigation')
        preset: Full preset dict
        state_getter: Callable[[key], value]
        state_setter: Callable[[key, value, changed_by, turn, reason], (success, msg)]
        scene_turns_getter: Callable[[], int] for current scene turns
        
    Returns:
        Feature manager instance or None if unknown
    """
    cls = FEATURE_CLASSES.get(name)
    if not cls:
        return None
    
    return cls(
        preset=preset,
        state_getter=state_getter,
        state_setter=state_setter,
        scene_turns_getter=scene_turns_getter
    )