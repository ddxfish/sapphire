# core/state_engine/features/riddles.py
"""
Riddles - Collaborative puzzles with hidden answers.

Riddles are now state keys that AI sets directly via set_state().
The key is defined in initial_state with type="riddle_answer".
When AI sets the value, this handler validates the answer.

The answer is generated deterministically from a seed but neither AI nor player
knows it. Clues are revealed progressively based on scene_turns.
"""

import hashlib
import logging
from typing import Any, Callable, Optional

from ..conditions import parse_segment_key, match_conditions

logger = logging.getLogger(__name__)


class RiddleManager:
    """Manages riddles for a state engine instance."""
    
    def __init__(
        self,
        preset: dict,
        state_getter: Callable[[str], Any],
        state_setter: Callable,
        scene_turns_getter: Optional[Callable[[], int]] = None,
        chat_name: str = ""
    ):
        self._riddles = preset.get("riddles", [])
        self._get_state = state_getter
        self._set_state = state_setter
        self._get_scene_turns = scene_turns_getter
        self._chat_name = chat_name
        
        # Build mapping from state key -> riddle config
        self._key_to_riddle = {}
        for riddle in self._riddles:
            state_key = riddle.get("state_key")
            if state_key:
                self._key_to_riddle[state_key] = riddle
    
    @property
    def riddles(self) -> list:
        """Raw riddle configs."""
        return self._riddles
    
    def get_riddle_keys(self) -> set:
        """Get all state keys that are riddle answer keys."""
        return set(self._key_to_riddle.keys())
    
    def is_riddle_key(self, key: str) -> bool:
        """Check if a state key is a riddle answer key."""
        return key in self._key_to_riddle
    
    def set_chat_name(self, chat_name: str):
        """Set chat name for seeding (called by engine after init)."""
        self._chat_name = chat_name
    
    def handle_set_state(self, key: str, value: Any, turn_number: int, reason: str = "") -> tuple[bool, str]:
        """
        Handle set_state for a riddle answer key.
        
        Validates the answer and applies success/fail effects.
        
        Returns:
            (success, message) - success means answer was correct
        """
        riddle = self._key_to_riddle.get(key)
        if not riddle:
            return False, f"Unknown riddle key: {key}"
        
        riddle_id = riddle.get("id", key)
        
        # Check if already solved
        if self._get_state(f"_riddle_{riddle_id}_solved") == True:
            return False, "This riddle has already been solved."
        
        # Check if locked out
        if self._get_state(f"_riddle_{riddle_id}_locked") == True:
            return False, "Too many failed attempts. The riddle is locked."
        
        # Get attempt count
        attempts_key = f"_riddle_{riddle_id}_attempts"
        attempts = self._get_state(attempts_key) or 0
        max_attempts = riddle.get("max_attempts", 999)
        
        # Check answer - handle leading zeros for numeric riddles
        stored_hash = self._get_state(f"_riddle_{riddle_id}_hash")
        value_str = str(value)

        # Pad with leading zeros if riddle has digit constraint (e.g., "0847" not "847")
        digits = riddle.get("digits")
        if digits and value_str.isdigit():
            value_str = value_str.zfill(digits)

        answer_hash = hashlib.sha256(value_str.encode()).hexdigest()
        
        if answer_hash == stored_hash:
            # Success!
            self._set_state(f"_riddle_{riddle_id}_solved", True, "system", turn_number, "Riddle solved")
            
            # Apply success state changes
            success_sets = riddle.get("success_sets", {})
            for set_key, set_value in success_sets.items():
                self._set_state(set_key, set_value, "ai", turn_number, f"Riddle '{riddle_id}' solved")
            
            success_msg = riddle.get("success_message", "Correct! The riddle is solved.")
            # Return True so engine knows to actually set the value
            return True, f"✓ {success_msg}"
        
        # Wrong answer
        attempts += 1
        self._set_state(attempts_key, attempts, "system", turn_number, "Failed attempt")
        
        remaining = max_attempts - attempts
        if remaining <= 0:
            # Lockout
            self._set_state(f"_riddle_{riddle_id}_locked", True, "system", turn_number, "Riddle locked")
            
            lockout_sets = riddle.get("lockout_sets", {})
            for set_key, set_value in lockout_sets.items():
                self._set_state(set_key, set_value, "ai", turn_number, f"Riddle '{riddle_id}' locked")
            
            lockout_msg = riddle.get("lockout_message", "Too many wrong answers. The riddle is now locked.")
            return False, f"✗ {lockout_msg}"
        
        fail_msg = riddle.get("fail_message", "That's not correct.")
        return False, f"✗ {fail_msg} ({remaining} attempts remaining)"
    
    def initialize(self, turn_number: int):
        """Initialize all riddles (answer hashes, attempt counters)."""
        for riddle in self._riddles:
            riddle_id = riddle.get("id")
            if not riddle_id:
                continue
            
            answer = self._generate_answer(riddle)
            if answer is None:
                continue
            
            # Store hashed answer (AI can't see plaintext)
            answer_hash = hashlib.sha256(str(answer).encode()).hexdigest()
            self._set_state(f"_riddle_{riddle_id}_hash", answer_hash, "system", turn_number,
                           "Riddle initialized")
            
            # Initialize attempt counter
            self._set_state(f"_riddle_{riddle_id}_attempts", 0, "system", turn_number,
                           "Riddle attempts initialized")
            
            logger.debug(f"[RIDDLE] Initialized '{riddle_id}', answer_hash={answer_hash[:16]}...")
    
    def ensure_initialized(self):
        """
        Ensure all riddles have their state initialized.
        Called on reload to handle restarts where initialize() wasn't run.
        """
        for riddle in self._riddles:
            riddle_id = riddle.get("id")
            if not riddle_id:
                continue
            
            # Check if already initialized
            existing_hash = self._get_state(f"_riddle_{riddle_id}_hash")
            if existing_hash:
                logger.debug(f"[RIDDLE] '{riddle_id}' already initialized")
                continue
            
            # Initialize this riddle
            answer = self._generate_answer(riddle)
            if answer is None:
                logger.warning(f"[RIDDLE] Could not generate answer for '{riddle_id}'")
                continue
            
            answer_hash = hashlib.sha256(str(answer).encode()).hexdigest()
            
            # Use turn 0 for system initialization
            self._set_state(f"_riddle_{riddle_id}_hash", answer_hash, "system", 0,
                           "Riddle initialized on reload")
            self._set_state(f"_riddle_{riddle_id}_attempts", 0, "system", 0,
                           "Riddle attempts initialized on reload")
            
            logger.info(f"[RIDDLE] Late-initialized '{riddle_id}' on reload")
    
    def _generate_answer(self, riddle: dict) -> Optional[str]:
        """
        Generate riddle answer deterministically.
        
        Types:
        - 'fixed': Answer is in config
        - 'numeric': Generate N digits from seed
        - 'word': Select from wordlist using seed
        """
        riddle_type = riddle.get("type", "fixed")
        riddle_id = riddle.get("id", "unknown")
        
        if riddle_type == "fixed":
            return riddle.get("answer")
        
        # Generate seed from chat_name + riddle_id for determinism
        seed_source = riddle.get("seed_from", "chat_name")
        if seed_source == "chat_name":
            seed = f"{self._chat_name}:{riddle_id}"
        else:
            seed = f"{seed_source}:{riddle_id}"
        
        seed_hash = hashlib.md5(seed.encode()).hexdigest()
        
        if riddle_type == "numeric":
            digits = riddle.get("digits", 4)
            answer = ""
            for i in range(digits):
                answer += str(int(seed_hash[i*2:i*2+2], 16) % 10)
            return answer
        
        elif riddle_type == "word":
            wordlist = riddle.get("wordlist", ["XYZZY", "PLUGH", "PLOVER"])
            idx = int(seed_hash[:8], 16) % len(wordlist)
            return wordlist[idx]
        
        return None
    
    def get_clues(self, riddle_id: str, scene_turns_override: int = None) -> list:
        """
        Get revealed clues for a riddle based on scene_turns.
        
        Args:
            riddle_id: The riddle ID
            scene_turns_override: If provided, use this value instead of calling the getter
        
        Returns list of clue strings that should be visible.
        """
        riddle = self.get_by_id(riddle_id)
        if not riddle:
            return []
        
        clues_config = riddle.get("clues", {})
        revealed = []
        
        # Create scene_turns getter - use override if provided
        if scene_turns_override is not None:
            scene_turns_getter = lambda: scene_turns_override
        else:
            scene_turns_getter = self._get_scene_turns
        
        # Parse clue keys like "1", "2?scene_turns>=2", etc.
        clue_items = []
        for clue_key, clue_text in clues_config.items():
            base_key, conditions = parse_segment_key(clue_key)
            try:
                order = int(base_key)
            except ValueError:
                order = 999
            clue_items.append((order, conditions, clue_text))
        
        # Sort by order
        clue_items.sort(key=lambda x: x[0])
        
        for order, conditions, clue_text in clue_items:
            if not conditions:
                # Unconditional clue - always show
                revealed.append(clue_text)
            elif match_conditions(conditions, self._get_state, scene_turns_getter):
                revealed.append(clue_text)
        
        return revealed
    
    def get_by_id(self, riddle_id: str) -> Optional[dict]:
        """Get a riddle config by its ID."""
        for riddle in self._riddles:
            if riddle.get("id") == riddle_id:
                return riddle
        return None
    
    def get_status(self, riddle_id: str) -> dict:
        """Get status of a riddle (for AI reference)."""
        riddle = self.get_by_id(riddle_id)
        if not riddle:
            return {"error": f"Unknown riddle: {riddle_id}"}
        
        return {
            "id": riddle_id,
            "solved": self._get_state(f"_riddle_{riddle_id}_solved") == True,
            "locked": self._get_state(f"_riddle_{riddle_id}_locked") == True,
            "attempts": self._get_state(f"_riddle_{riddle_id}_attempts") or 0,
            "max_attempts": riddle.get("max_attempts", 999)
        }