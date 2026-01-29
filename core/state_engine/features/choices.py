# core/state_engine/features/choices.py
"""
Binary Choices - Forced decisions that block progression until resolved.

Choices are now state keys that AI sets directly via set_state().
The key is defined in initial_state with type="choice".
When AI sets the value, this handler validates and applies effects.
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
        
        # Build mapping from state key -> choice config
        self._key_to_choice = {}
        for choice in self._choices:
            state_key = choice.get("state_key")
            if state_key:
                self._key_to_choice[state_key] = choice
    
    @property
    def choices(self) -> list:
        """Raw choice configs."""
        return self._choices
    
    def get_choice_keys(self) -> set:
        """Get all state keys that are choice keys."""
        return set(self._key_to_choice.keys())
    
    def is_choice_key(self, key: str) -> bool:
        """Check if a state key is a choice key."""
        return key in self._key_to_choice
    
    def get_options_for_key(self, key: str) -> list:
        """Get valid option names for a choice key."""
        choice = self._key_to_choice.get(key)
        if not choice:
            return []
        return list(choice.get("options", {}).keys())
    
    def handle_set_state(self, key: str, value: Any, turn_number: int, reason: str = "") -> tuple[bool, str]:
        """
        Handle set_state for a choice key.
        
        Validates the value is a valid option and applies effects.
        
        Returns:
            (success, message)
        """
        choice = self._key_to_choice.get(key)
        if not choice:
            return False, f"Unknown choice key: {key}"
        
        options = choice.get("options", {})
        
        # Validate value is a valid option
        if value not in options:
            available = list(options.keys())
            return False, f"Invalid choice '{value}'. Options: {available}"
        
        # Check if already chosen
        current = self._get_state(key)
        if current is not None and current != "":
            return False, f"Choice already made: {key} = {current}"
        
        # Apply the option's state changes
        option_config = options[value]
        state_changes = option_config.get("set", {})
        
        results = [f"✓ Choice made: {value}"]
        
        for change_key, change_value in state_changes.items():
            # Handle relative values like "+10" or "-20"
            if isinstance(change_value, str) and (change_value.startswith("+") or change_value.startswith("-")):
                current_val = self._get_state(change_key) or 0
                delta = int(change_value)
                change_value = current_val + delta
            
            success, msg = self._set_state(change_key, change_value, "ai", turn_number, 
                                          f"Choice consequence: {value}")
            results.append(f"  {msg}")
        
        # The actual choice key value is set by the caller (engine.set_state)
        # We just return success here
        return True, "\n".join(results)
    
    def get_pending(self, current_turn: int) -> list:
        """
        Get choices that should be presented at current turn.
        
        Returns list of choices where:
        - scene_turns >= trigger_turn
        - Choice key is still [not set]
        """
        if not self._choices:
            return []
        
        scene_turns = self._get_scene_turns() if self._get_scene_turns else 0
        pending = []
        
        for choice in self._choices:
            trigger = choice.get("trigger_turn", 0)
            if scene_turns < trigger:
                continue
            
            # Check if choice has been made (state key is set)
            state_key = choice.get("state_key")
            if state_key:
                current_value = self._get_state(state_key)
                if current_value is not None and current_value != "":
                    continue  # Already chosen
            
            pending.append(choice)
        
        return pending
    
    def get_by_id(self, choice_id: str) -> Optional[dict]:
        """Get a choice config by its ID."""
        for choice in self._choices:
            if choice.get("id") == choice_id:
                return choice
        return None
    
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
            
            state_key = choice.get("state_key")
            if not state_key:
                continue
            
            blockers.append({
                "target": required_scene,
                "state_key": state_key,
                "message": choice.get("block_message", 
                    f"You must make a choice before proceeding: {choice.get('prompt', state_key)}")
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
        
        blockers = self.get_blockers()
        logger.debug(f"[CHOICE] check_blockers: key={key}, new_value={new_value}, blockers={len(blockers)}")
        
        for blocker in blockers:
            # Coerce both to same type for comparison
            target = blocker["target"]
            try:
                if isinstance(new_value, (int, float)) or isinstance(target, (int, float)):
                    target = int(target)
                    new_value_cmp = int(new_value)
                else:
                    target = str(target)
                    new_value_cmp = str(new_value)
            except (ValueError, TypeError):
                target = str(target)
                new_value_cmp = str(new_value)
            
            logger.debug(f"[CHOICE] Checking blocker: target={target}, new_value_cmp={new_value_cmp}")
            
            if target != new_value_cmp:
                continue
            
            # Check if choice state key has a value
            state_key = blocker["state_key"]
            current_value = self._get_state(state_key)
            
            logger.debug(f"[CHOICE] Blocker matched! state_key={state_key}, current_value={current_value}")
            
            if current_value is None or current_value == "":
                logger.info(f"[CHOICE] BLOCKING advance to {new_value}: {blocker['message']}")
                return False, blocker["message"]
        
        return True, ""