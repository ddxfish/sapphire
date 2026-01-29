# core/state_engine/features/riddles.py
"""
Riddles - Collaborative puzzles with hidden answers.

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
    
    @property
    def riddles(self) -> list:
        """Raw riddle configs."""
        return self._riddles
    
    def set_chat_name(self, chat_name: str):
        """Set chat name for seeding (called by engine after init)."""
        self._chat_name = chat_name
    
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
    
    def get_clues(self, riddle_id: str) -> list:
        """
        Get revealed clues for a riddle based on scene_turns.
        
        Returns list of clue strings that should be visible.
        """
        riddle = self.get_by_id(riddle_id)
        if not riddle:
            return []
        
        clues_config = riddle.get("clues", {})
        revealed = []
        
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
            elif match_conditions(conditions, self._get_state, self._get_scene_turns):
                revealed.append(clue_text)
        
        return revealed
    
    def get_by_id(self, riddle_id: str) -> Optional[dict]:
        """Get a riddle config by its ID."""
        for riddle in self._riddles:
            if riddle.get("id") == riddle_id:
                return riddle
        return None
    
    def attempt(self, riddle_id: str, answer: str, turn_number: int) -> tuple[bool, str]:
        """
        Attempt to solve a riddle.
        
        Returns:
            (success, message)
        """
        riddle = self.get_by_id(riddle_id)
        if not riddle:
            return False, f"Unknown riddle: {riddle_id}"
        
        # Check if already solved
        solved_key = f"_riddle_{riddle_id}_solved"
        if self._get_state(solved_key) == True:
            return False, "This riddle has already been solved."
        
        # Check if locked out
        locked_key = f"_riddle_{riddle_id}_locked"
        if self._get_state(locked_key) == True:
            return False, "Too many failed attempts. The riddle is locked."
        
        # Get attempt count
        attempts_key = f"_riddle_{riddle_id}_attempts"
        attempts = self._get_state(attempts_key) or 0
        max_attempts = riddle.get("max_attempts", 999)
        
        # Check answer
        stored_hash = self._get_state(f"_riddle_{riddle_id}_hash")
        answer_hash = hashlib.sha256(str(answer).encode()).hexdigest()
        
        if answer_hash == stored_hash:
            # Success!
            self._set_state(solved_key, True, "system", turn_number, "Riddle solved")
            
            # Apply success state changes
            success_sets = riddle.get("success_sets", {})
            for key, value in success_sets.items():
                self._set_state(key, value, "ai", turn_number, f"Riddle '{riddle_id}' solved")
            
            success_msg = riddle.get("success_message", "Correct! The riddle is solved.")
            return True, f"✓ {success_msg}"
        
        # Wrong answer
        attempts += 1
        self._set_state(attempts_key, attempts, "system", turn_number, "Failed attempt")
        
        remaining = max_attempts - attempts
        if remaining <= 0:
            # Lockout
            self._set_state(locked_key, True, "system", turn_number, "Riddle locked")
            
            lockout_sets = riddle.get("lockout_sets", {})
            for key, value in lockout_sets.items():
                self._set_state(key, value, "ai", turn_number, f"Riddle '{riddle_id}' locked")
            
            lockout_msg = riddle.get("lockout_message", "Too many wrong answers. The riddle is now locked.")
            return False, f"✗ {lockout_msg}"
        
        fail_msg = riddle.get("fail_message", "That's not correct.")
        return False, f"✗ {fail_msg} ({remaining} attempts remaining)"
    
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
