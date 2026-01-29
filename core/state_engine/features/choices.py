# core/state_engine/features/choices.py
"""
Binary Choices - Forced decisions that block progression until resolved.

Choices are defined in presets with trigger conditions and state consequences.
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ChoiceManager:
    """Manages binary choices for a state engine instance."""
    
    def __init__(
        self,
        preset: dict,
        state_getter: Callable[[str], Any],
        state_setter: Callable,
        scene_turns_getter: Optional[Callable[[], int]] = None
    ):
        self._choices = preset.get("binary_choices", [])
        self._progressive_config = preset.get("progressive_prompt", {})
        self._get_state = state_getter
        self._set_state = state_setter
        self._get_scene_turns = scene_turns_getter
    
    @property
    def choices(self) -> list:
        """Raw choice configs."""
        return self._choices
    
    def get_pending(self, current_turn: int) -> list:
        """
        Get choices that should be presented at current turn.
        
        Returns list of choices where:
        - scene_turns >= trigger_turn
        - No option has been selected yet
        """
        if not self._choices:
            return []
        
        scene_turns = self._get_scene_turns() if self._get_scene_turns else 0
        pending = []
        
        for choice in self._choices:
            trigger = choice.get("trigger_turn", 0)
            if scene_turns < trigger:
                continue
            
            # Check if any option has been selected
            if not self._is_choice_made(choice):
                pending.append(choice)
        
        return pending
    
    def _is_choice_made(self, choice: dict) -> bool:
        """Check if any option for this choice has been selected."""
        choice_id = choice.get("id", "")
        for option_key in choice.get("options", {}).keys():
            state_key = f"_choice_{choice_id}_{option_key}"
            if self._get_state(state_key) == True:
                return True
        return False
    
    def get_by_id(self, choice_id: str) -> Optional[dict]:
        """Get a choice config by its ID."""
        for choice in self._choices:
            if choice.get("id") == choice_id:
                return choice
        return None
    
    def make_choice(self, choice_id: str, option_key: str, turn_number: int, reason: str = None) -> tuple[bool, str]:
        """
        Make a binary choice, setting the option's state values.
        
        Args:
            choice_id: ID of the choice from preset
            option_key: Which option was selected
            turn_number: Current turn
            reason: Optional reason for the choice
            
        Returns:
            (success, message)
        """
        choice = self.get_by_id(choice_id)
        if not choice:
            return False, f"Unknown choice: {choice_id}"
        
        options = choice.get("options", {})
        if option_key not in options:
            available = list(options.keys())
            return False, f"Invalid option '{option_key}'. Must be one of: {available}"
        
        # Check if choice already made
        for opt in options.keys():
            state_key = f"_choice_{choice_id}_{opt}"
            if self._get_state(state_key) == True:
                return False, f"Choice '{choice_id}' already made (selected: {opt})"
        
        # Mark this option as chosen
        choice_state_key = f"_choice_{choice_id}_{option_key}"
        self._set_state(choice_state_key, True, "system", turn_number, 
                       reason or f"Player chose {option_key}")
        
        # Apply the option's state changes
        option_config = options[option_key]
        state_changes = option_config.get("set", {})
        results = [f"✓ Choice made: {option_key}"]
        
        for key, value in state_changes.items():
            # Handle relative values like "+10" or "-20"
            if isinstance(value, str) and (value.startswith("+") or value.startswith("-")):
                current = self._get_state(key) or 0
                delta = int(value)
                value = current + delta
            
            success, msg = self._set_state(key, value, "ai", turn_number, 
                                          f"Choice consequence: {option_key}")
            results.append(f"  {msg}")
        
        return True, "\n".join(results)
    
    def get_blockers(self) -> list:
        """
        Generate dynamic blockers for unresolved binary choices.
        These prevent scene advancement until choices are made.
        """
        blockers = []
        for choice in self._choices:
            required_scene = choice.get("required_for_scene")
            if not required_scene:
                continue
            
            choice_id = choice["id"]
            options = choice.get("options", {})
            
            # Build OR condition: at least one option must be true
            option_keys = [f"_choice_{choice_id}_{opt}" for opt in options.keys()]
            
            blockers.append({
                "target": required_scene,
                "choice_id": choice_id,
                "requires_any": option_keys,
                "message": choice.get("block_message", 
                    f"You must make a choice before proceeding: {choice.get('prompt', choice_id)}")
            })
        
        return blockers
    
    def check_blockers(self, key: str, new_value: Any) -> tuple[bool, str]:
        """
        Check if a state change is blocked by an unresolved binary choice.
        
        Only checks if key is the iterator key.
        """
        iterator_key = self._progressive_config.get("iterator")
        if not iterator_key or key != iterator_key:
            return True, ""  # Only iterator changes can be blocked
        
        for blocker in self.get_blockers():
            if blocker["target"] != new_value:
                continue
            
            # Check if ANY of the required options is true
            any_chosen = False
            for opt_key in blocker["requires_any"]:
                if self._get_state(opt_key) == True:
                    any_chosen = True
                    break
            
            if not any_chosen:
                return False, blocker["message"]
        
        return True, ""
