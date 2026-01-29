# core/state_engine/engine.py
"""
State Engine - Per-chat state management with full history for rollback.
Enables games, simulations, and interactive stories where AI reads/writes state via tools.

Progressive Prompts: Reveal prompt segments based on iterator state (room #, turn, etc.)
to prevent AI from seeing future content.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Set up dedicated state engine logger
logger = logging.getLogger(__name__)

# Create dedicated file handler for state engine debugging
_state_log_path = Path(__file__).parent.parent.parent / "user" / "logs" / "state_engine.log"
_state_log_path.parent.mkdir(parents=True, exist_ok=True)
_state_file_handler = logging.FileHandler(_state_log_path, mode='a')
_state_file_handler.setLevel(logging.DEBUG)
_state_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(_state_file_handler)
logger.setLevel(logging.DEBUG)


class StateEngine:
    """Manages per-chat state with SQLite persistence and rollback support."""
    
    def __init__(self, chat_name: str, db_path: Path):
        self.chat_name = chat_name
        self._db_path = db_path
        self._current_state = {}  # Cache: key -> {value, type, label, constraints, turn}
        self._preset_name = None
        self._progressive_config = None  # {iterator, mode, base, segments}
        self._binary_choices = []  # [{id, trigger_turn, prompt, options, required_for_scene}]
        self._riddles = []  # [{id, type, clues, success_sets, max_attempts, ...}]
        self._scene_entered_at_turn = 0  # Turn when current scene/iterator started
        self._current_turn_for_matching = None  # Temp storage for condition matching
        self._load_state()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with WAL mode."""
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn
    
    def _load_state(self):
        """Load current state from database into cache."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT key, value, value_type, label, constraints, turn_number "
                    "FROM state_current WHERE chat_name = ?",
                    (self.chat_name,)
                )
                self._current_state = {}
                preset_name_from_db = None
                scene_entered_at = None
                for row in cursor:
                    key = row["key"]
                    # Extract system keys
                    if key == "_preset":
                        preset_name_from_db = json.loads(row["value"])
                        continue
                    if key == "_scene_entered_at":
                        scene_entered_at = json.loads(row["value"])
                        continue
                    self._current_state[key] = {
                        "value": json.loads(row["value"]),
                        "type": row["value_type"],
                        "label": row["label"],
                        "constraints": json.loads(row["constraints"]) if row["constraints"] else None,
                        "turn": row["turn_number"]
                    }
                
                # Restore scene entry tracking
                if scene_entered_at is not None:
                    self._scene_entered_at_turn = scene_entered_at
                
                # Reload preset config if we found one in DB
                if preset_name_from_db:
                    self.reload_preset_config(preset_name_from_db)
                
                logger.debug(f"Loaded {len(self._current_state)} state keys for '{self.chat_name}'" +
                            (f" (preset: {self._preset_name})" if self._preset_name else ""))
        except Exception as e:
            logger.error(f"Failed to load state for '{self.chat_name}': {e}")
            self._current_state = {}
    
    def reload_from_db(self):
        """Force reload state from database, clearing cache. Call after external DB changes."""
        logger.info(f"[STATE] Reloading state from DB for '{self.chat_name}'")
        self._current_state = {}
        self._preset_name = None
        self._progressive_config = None
        self._binary_choices = []
        self._riddles = []
        self._scene_entered_at_turn = 0
        self._load_state()
    
    def _is_system_key(self, key: str) -> bool:
        """Check if key is system-managed (starts with _)."""
        return key.startswith("_")
    
    def _persist_scene_entered_at(self, turn_number: int):
        """Persist scene entry turn to database."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.chat_name,
                        "_scene_entered_at",
                        json.dumps(turn_number),
                        "integer",
                        "System: Scene Entry Turn",
                        None,
                        datetime.now().isoformat(),
                        "system",
                        turn_number
                    )
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist scene_entered_at: {e}")
    
    def _validate_value(self, key: str, value: Any, constraints: Optional[dict]) -> tuple[bool, str]:
        """Validate value against constraints. Returns (valid, error_message)."""
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
            current = self.get_state(key)
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
            for blocker in constraints["blockers"]:
                # Check if this blocker applies to the target value
                target = blocker.get("target")
                if target is not None:
                    # target can be single value or list
                    targets = target if isinstance(target, list) else [target]
                    if value not in targets:
                        continue  # This blocker doesn't apply
                
                # Check if blocker has a "from" condition (only block when coming from certain values)
                from_values = blocker.get("from")
                if from_values is not None:
                    current = self.get_state(key)
                    from_list = from_values if isinstance(from_values, list) else [from_values]
                    if current not in from_list:
                        continue  # Not coming from a blocked origin
                
                # Check required conditions
                requires = blocker.get("requires", {})
                for req_key, req_value in requires.items():
                    actual = self.get_state(req_key)
                    if actual != req_value:
                        message = blocker.get("message", f"Cannot set {key} to {value}: requires {req_key}={req_value}")
                        return False, message
        
        return True, ""
    
    def get_state(self, key: str = None) -> Any:
        """
        Get state value(s).
        
        Args:
            key: Specific key to get, or None for all state
            
        Returns:
            Single value if key specified, dict of all state if not
        """
        if key:
            entry = self._current_state.get(key)
            return entry["value"] if entry else None
        
        # Return all state as {key: value} for simplicity
        return {k: v["value"] for k, v in self._current_state.items()}
    
    def get_state_full(self, key: str = None) -> Any:
        """Get full state entry with metadata (type, label, constraints)."""
        if key:
            return self._current_state.get(key)
        return self._current_state.copy()
    
    def get_scene_turns(self, current_turn: int) -> int:
        """
        Get number of turns spent in current scene/iterator value.
        
        Args:
            current_turn: Current global turn number
            
        Returns:
            Number of turns in current scene (0 if just entered)
        """
        # Safety check: if scene_entered_at is ahead of current_turn, something's wrong
        # (e.g., chat was cleared but state persisted) - reset to current turn
        if self._scene_entered_at_turn > current_turn:
            logger.warning(f"[STATE] scene_entered_at ({self._scene_entered_at_turn}) > current_turn ({current_turn}), resetting")
            self._scene_entered_at_turn = current_turn
            self._persist_scene_entered_at(current_turn)
        
        return current_turn - self._scene_entered_at_turn
    
    def get_visible_state(self, current_turn: int = None) -> dict:
        """
        Get state filtered by visible_from constraints.
        Used by AI tools to prevent seeing future-gated variables.
        
        Args:
            current_turn: Current turn number (for scene_turns calculation)
        """
        # Get iterator value for visibility checks
        iterator_value = None
        iterator_key = None
        
        if self._progressive_config:
            iterator_key = self._progressive_config.get("iterator")
        
        # Fallback: check for common iterator keys if no config
        if not iterator_key:
            for candidate in ("scene", "player_room", "turn", "chapter", "room"):
                if candidate in self._current_state:
                    iterator_key = candidate
                    break
        
        if iterator_key:
            val = self.get_state(iterator_key)
            if isinstance(val, (int, float)):
                iterator_value = int(val)
        
        result = {}
        for key, entry in self._current_state.items():
            if key.startswith("_"):
                continue
            
            constraints = entry.get("constraints", {}) or {}
            visible_from = constraints.get("visible_from")
            
            # If key has visible_from, only show if iterator >= threshold
            if visible_from is not None:
                if iterator_value is None or iterator_value < visible_from:
                    continue
            
            result[key] = entry["value"]
        
        # Add computed scene_turns if we have an iterator and turn info
        if current_turn is not None and iterator_key:
            result["scene_turns"] = self.get_scene_turns(current_turn)
        
        return result
    
    def set_state(self, key: str, value: Any, changed_by: str, 
                  turn_number: int, reason: str = None) -> tuple[bool, str]:
        """
        Set state value with logging.
        
        Args:
            key: State key
            value: New value
            changed_by: 'ai', 'user', or 'system'
            turn_number: Current turn (from chat history)
            reason: Optional reason for change
            
        Returns:
            (success, message)
        """
        # Block AI writes to system keys
        if changed_by == "ai" and self._is_system_key(key):
            return False, f"Cannot modify system key: {key}"
        
        # Get existing entry for constraints and old value
        existing = self._current_state.get(key, {})
        old_value = existing.get("value")
        constraints = existing.get("constraints")
        value_type = existing.get("type")
        label = existing.get("label")
        
        # Infer type if new key
        if not value_type:
            if isinstance(value, bool):
                value_type = "boolean"
            elif isinstance(value, int):
                value_type = "integer"
            elif isinstance(value, float):
                value_type = "number"
            elif isinstance(value, list):
                value_type = "array"
            elif isinstance(value, dict):
                value_type = "object"
            else:
                value_type = "string"
        
        # Validate
        valid, error = self._validate_value(key, value, constraints)
        if not valid:
            return False, error
        
        # Check binary choice blockers (for iterator changes)
        valid, error = self._check_choice_blockers(key, value)
        if not valid:
            return False, error
        
        try:
            with self._get_connection() as conn:
                # Log the change
                conn.execute(
                    """INSERT INTO state_log 
                       (chat_name, key, old_value, new_value, changed_by, turn_number, timestamp, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.chat_name,
                        key,
                        json.dumps(old_value) if old_value is not None else None,
                        json.dumps(value),
                        changed_by,
                        turn_number,
                        datetime.now().isoformat(),
                        reason
                    )
                )
                
                # Update current state
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.chat_name,
                        key,
                        json.dumps(value),
                        value_type,
                        label,
                        json.dumps(constraints) if constraints else None,
                        datetime.now().isoformat(),
                        changed_by,
                        turn_number
                    )
                )
                conn.commit()
            
            # Update cache
            self._current_state[key] = {
                "value": value,
                "type": value_type,
                "label": label,
                "constraints": constraints,
                "turn": turn_number
            }
            
            logger.debug(f"State set: {key}={value} by {changed_by} at turn {turn_number}")
            
            # Build informative message for AI
            is_new_key = old_value is None
            is_iterator = self._progressive_config and self._progressive_config.get("iterator") == key
            
            # Detect iterator change - reset scene_turns tracking
            if is_iterator and old_value != value:
                self._scene_entered_at_turn = turn_number
                self._persist_scene_entered_at(turn_number)
                logger.info(f"[STATE] Iterator changed: scene_turns reset at turn {turn_number}")
            
            if is_new_key:
                # Warn AI they created a new key (might be a typo)
                # Only show VISIBLE keys to avoid spoilers
                visible_keys = list(self.get_visible_state().keys())
                visible_keys = [k for k in visible_keys if k != key]
                msg = f"⚠️ CREATED NEW KEY '{key}' = {value}. This key did not exist! Did you mean one of these? {visible_keys}"
            elif is_iterator:
                # Special message for iterator changes
                msg = f"✓ Updated {key}: {old_value} → {value} (iterator: new content now visible)"
            elif old_value == value:
                msg = f"✓ {key} unchanged (already {value})"
            else:
                msg = f"✓ Updated {key}: {old_value} → {value}"
            
            return True, msg
            
        except Exception as e:
            logger.error(f"Failed to set state {key}: {e}")
            return False, f"Database error: {e}"
    
    def delete_key(self, key: str) -> bool:
        """Delete a state key."""
        if self._is_system_key(key):
            return False
        
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM state_current WHERE chat_name = ? AND key = ?",
                    (self.chat_name, key)
                )
                conn.commit()
            
            self._current_state.pop(key, None)
            logger.debug(f"Deleted state key: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete state key {key}: {e}")
            return False
    
    def load_preset(self, preset_name: str, turn_number: int) -> tuple[bool, str]:
        """
        Load a state preset, initializing all state keys.
        
        Args:
            preset_name: Name of preset file (without .json)
            turn_number: Current turn number
            
        Returns:
            (success, message)
        """
        # Get project root from engine.py location (core/state_engine/engine.py -> project root)
        project_root = Path(__file__).parent.parent.parent
        
        # Search paths: user first, then core
        search_paths = [
            project_root / "user" / "state_presets" / f"{preset_name}.json",
            project_root / "core" / "state_engine" / "presets" / f"{preset_name}.json",
        ]
        
        preset_path = None
        for path in search_paths:
            if path.exists():
                preset_path = path
                break
        
        if not preset_path:
            return False, f"Preset not found: {preset_name}"
        
        try:
            with open(preset_path, 'r', encoding='utf-8') as f:
                preset = json.load(f)
            
            # Clear existing state for this chat
            self.clear_all()
            
            # Load initial state
            initial_state = preset.get("initial_state", {})
            for key, spec in initial_state.items():
                value = spec.get("value")
                self._current_state[key] = {
                    "value": value,
                    "type": spec.get("type"),
                    "label": spec.get("label"),
                    "constraints": {k: v for k, v in spec.items() 
                                   if k not in ("value", "type", "label")},
                    "turn": turn_number
                }
            
            # Write to database
            with self._get_connection() as conn:
                for key, entry in self._current_state.items():
                    conn.execute(
                        """INSERT INTO state_current 
                           (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            self.chat_name,
                            key,
                            json.dumps(entry["value"]),
                            entry["type"],
                            entry["label"],
                            json.dumps(entry["constraints"]) if entry["constraints"] else None,
                            datetime.now().isoformat(),
                            "system",
                            turn_number
                        )
                    )
                    
                    # Log initial state
                    conn.execute(
                        """INSERT INTO state_log 
                           (chat_name, key, old_value, new_value, changed_by, turn_number, timestamp, reason)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            self.chat_name,
                            key,
                            None,
                            json.dumps(entry["value"]),
                            "system",
                            turn_number,
                            datetime.now().isoformat(),
                            f"Preset: {preset_name}"
                        )
                    )
                
                # Store preset name as system key for later retrieval
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.chat_name,
                        "_preset",
                        json.dumps(preset_name),
                        "string",
                        "System: Active Preset",
                        None,
                        datetime.now().isoformat(),
                        "system",
                        turn_number
                    )
                )
                conn.commit()
            
            self._preset_name = preset_name
            self._progressive_config = preset.get("progressive_prompt")
            self._binary_choices = preset.get("binary_choices", [])
            self._riddles = preset.get("riddles", [])
            
            # Initialize riddle state (attempts tracking, answer hashes)
            self._init_riddles(turn_number)
            
            # Initialize scene_turns tracking
            self._scene_entered_at_turn = turn_number
            self._persist_scene_entered_at(turn_number)
            
            logger.info(f"Loaded preset '{preset_name}' with {len(self._current_state)} keys" +
                       (f", progressive iterator: {self._progressive_config.get('iterator')}" 
                        if self._progressive_config else ""))
            return True, f"Loaded preset: {preset_name}"
            
        except Exception as e:
            logger.error(f"Failed to load preset {preset_name}: {e}")
            return False, f"Error loading preset: {e}"
    
    def clear_all(self) -> bool:
        """Clear all state for this chat."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM state_current WHERE chat_name = ?",
                    (self.chat_name,)
                )
                conn.execute(
                    "DELETE FROM state_log WHERE chat_name = ?",
                    (self.chat_name,)
                )
                conn.commit()
            
            self._current_state = {}
            self._preset_name = None
            self._progressive_config = None
            self._scene_entered_at_turn = 0
            logger.info(f"Cleared all state for '{self.chat_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to clear state: {e}")
            return False
    
    def rollback_to_turn(self, target_turn: int) -> bool:
        """
        Rollback state to a specific turn by replaying log.
        
        Args:
            target_turn: Turn number to rollback to
            
        Returns:
            Success boolean
        """
        try:
            with self._get_connection() as conn:
                # Delete log entries after target turn
                conn.execute(
                    "DELETE FROM state_log WHERE chat_name = ? AND turn_number > ?",
                    (self.chat_name, target_turn)
                )
                
                # Clear current state
                conn.execute(
                    "DELETE FROM state_current WHERE chat_name = ?",
                    (self.chat_name,)
                )
                
                # Replay log to rebuild state
                cursor = conn.execute(
                    """SELECT key, new_value, changed_by, turn_number, timestamp
                       FROM state_log 
                       WHERE chat_name = ? AND turn_number <= ?
                       ORDER BY id ASC""",
                    (self.chat_name, target_turn)
                )
                
                # Track final state per key
                rebuilt_state = {}
                for row in cursor:
                    key = row["key"]
                    rebuilt_state[key] = {
                        "value": json.loads(row["new_value"]),
                        "changed_by": row["changed_by"],
                        "turn_number": row["turn_number"],
                        "timestamp": row["timestamp"]
                    }
                
                # Write rebuilt state to state_current
                # Note: We lose type/label/constraints info on rollback
                # This is acceptable - preset reload restores them
                for key, data in rebuilt_state.items():
                    value = data["value"]
                    value_type = "string"
                    if isinstance(value, bool):
                        value_type = "boolean"
                    elif isinstance(value, int):
                        value_type = "integer"
                    elif isinstance(value, float):
                        value_type = "number"
                    elif isinstance(value, list):
                        value_type = "array"
                    elif isinstance(value, dict):
                        value_type = "object"
                    
                    conn.execute(
                        """INSERT INTO state_current 
                           (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            self.chat_name,
                            key,
                            json.dumps(value),
                            value_type,
                            None,  # label lost on rollback
                            None,  # constraints lost on rollback
                            data["timestamp"],
                            data["changed_by"],
                            data["turn_number"]
                        )
                    )
                
                conn.commit()
            
            # Refresh cache
            self._load_state()
            logger.info(f"Rolled back '{self.chat_name}' to turn {target_turn}, {len(self._current_state)} keys")
            return True
            
        except Exception as e:
            logger.error(f"Failed to rollback to turn {target_turn}: {e}")
            return False
    
    def get_history(self, key: str = None, limit: int = 100) -> list:
        """Get state change history."""
        try:
            with self._get_connection() as conn:
                if key:
                    cursor = conn.execute(
                        """SELECT key, old_value, new_value, changed_by, turn_number, timestamp, reason
                           FROM state_log 
                           WHERE chat_name = ? AND key = ?
                           ORDER BY id DESC LIMIT ?""",
                        (self.chat_name, key, limit)
                    )
                else:
                    cursor = conn.execute(
                        """SELECT key, old_value, new_value, changed_by, turn_number, timestamp, reason
                           FROM state_log 
                           WHERE chat_name = ?
                           ORDER BY id DESC LIMIT ?""",
                        (self.chat_name, limit)
                    )
                
                history = []
                for row in cursor:
                    history.append({
                        "key": row["key"],
                        "old_value": json.loads(row["old_value"]) if row["old_value"] else None,
                        "new_value": json.loads(row["new_value"]),
                        "changed_by": row["changed_by"],
                        "turn": row["turn_number"],
                        "timestamp": row["timestamp"],
                        "reason": row["reason"]
                    })
                return history
        except Exception as e:
            logger.error(f"Failed to get state history: {e}")
            return []
    
    def format_for_prompt(self, include_vars: bool = True, include_story: bool = True, current_turn: int = None) -> str:
        """
        Format current state for system prompt injection with progressive reveal.
        
        Args:
            include_vars: Include state variables (breaks caching each turn)
            include_story: Include story segments (cache-friendly, changes on scene advance)
            current_turn: Current turn number (for scene_turns calculation in conditions)
        """
        # Store for use by _match_conditions
        self._current_turn_for_matching = current_turn
        
        logger.info(f"{'='*60}")
        logger.info(f"[STATE] format_for_prompt called")
        logger.info(f"  vars={include_vars}, story={include_story}")
        logger.info(f"  current_turn={current_turn}, scene_entered_at={self._scene_entered_at_turn}")
        logger.info(f"  scene_turns={self.get_scene_turns(current_turn) if current_turn else 'N/A'}")
        logger.info(f"  progressive_config={self._progressive_config is not None}")
        
        parts = []
        
        # Get iterator value for visibility checks (needed for both vars and story)
        iterator_value = None
        if self._progressive_config:
            iterator_key = self._progressive_config.get("iterator")
            if iterator_key:
                val = self.get_state(iterator_key)
                if isinstance(val, (int, float)):
                    iterator_value = int(val)
                elif val is not None:
                    iterator_value = val  # String for room names
        
        # Add state variables if requested
        if include_vars and self._current_state:
            lines = []
            for key, entry in sorted(self._current_state.items()):
                if key.startswith("_"):
                    continue
                
                constraints = entry.get("constraints", {}) or {}
                visible_from = constraints.get("visible_from")
                if visible_from is not None and isinstance(iterator_value, int):
                    if iterator_value < visible_from:
                        continue
                
                value = entry["value"]
                label = entry.get("label")
                
                if isinstance(value, list):
                    value_str = json.dumps(value)
                elif isinstance(value, bool):
                    value_str = "true" if value else "false"
                else:
                    value_str = str(value)
                
                if label and label != key:
                    lines.append(f"{key} ({label}): {value_str}")
                else:
                    lines.append(f"{key}: {value_str}")
            
            if lines:
                parts.append("\n".join(lines))
        
        # Add tools hint
        nav_config = self._get_navigation_config()
        tools = ["get_state()", "set_state(key, value, reason)", "roll_dice(count, sides)", "increment_counter(key, amount)"]
        if nav_config and nav_config.get("connections"):
            tools.insert(2, "move(direction, reason)")
        if self._binary_choices:
            tools.append("make_choice(choice_id, option, reason)")
        if self._riddles:
            tools.append("attempt_riddle(riddle_id, answer)")
        parts.append("Tools: " + ", ".join(tools))
        
        # Add progressive prompt content (story segments) if requested
        if include_story:
            prompt_content = self._build_progressive_prompt()
            if prompt_content:
                parts.append(prompt_content)
        
        return "\n\n".join(parts) if parts else "(state engine active - use get_state())"
    
    def _parse_segment_key(self, key: str) -> tuple[str, list]:
        """
        Parse segment key into base key and conditions.
        
        Examples:
            "3" -> ("3", [])
            "3?martinez_dead" -> ("3", [("martinez_dead", "=", True)])
            "3?martinez_fate=abandoned" -> ("3", [("martinez_fate", "=", "abandoned")])
            "5?rose_trust>70" -> ("5", [("rose_trust", ">", 70)])
            "6?chen_alive,martinez_alive" -> ("6", [("chen_alive", "=", True), ("martinez_alive", "=", True)])
        
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
            
            # Check for comparison operators
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
                if isinstance(v, str):
                    if v.lower() == "true":
                        v = True
                    elif v.lower() == "false":
                        v = False
                    else:
                        try:
                            v = int(v)
                        except ValueError:
                            try:
                                v = float(v)
                            except ValueError:
                                pass  # Keep as string
            
            conditions.append((k, op, v))
        
        return base, conditions
    
    def _match_conditions(self, conditions: list) -> bool:
        """Check if all conditions match current state (AND logic)."""
        for state_key, op, expected in conditions:
            # Handle scene_turns pseudo-variable
            if state_key == "scene_turns":
                current_turn = getattr(self, '_current_turn_for_matching', None)
                if current_turn is None:
                    actual = 0  # No turn info, assume start
                else:
                    actual = self.get_scene_turns(current_turn)
                logger.debug(f"[COND] scene_turns check: current_turn={current_turn}, scene_entered={self._scene_entered_at_turn}, actual={actual}, op={op}, expected={expected}")
            else:
                actual = self.get_state(state_key)
            
            if op == "=":
                if actual != expected:
                    return False
            elif op == "!=":
                if actual == expected:
                    return False
            elif op == ">":
                if not (isinstance(actual, (int, float)) and actual > expected):
                    return False
            elif op == "<":
                if not (isinstance(actual, (int, float)) and actual < expected):
                    return False
            elif op == ">=":
                if not (isinstance(actual, (int, float)) and actual >= expected):
                    return False
            elif op == "<=":
                if not (isinstance(actual, (int, float)) and actual <= expected):
                    return False
        
        return True
    
    # ========== BINARY CHOICES ==========
    
    def get_pending_choices(self, current_turn: int) -> list:
        """
        Get binary choices that should be presented at current turn.
        
        Returns list of choices where:
        - scene_turns >= trigger_turn
        - No option has been selected yet
        """
        if not self._binary_choices:
            return []
        
        scene_turns = self.get_scene_turns(current_turn)
        pending = []
        
        for choice in self._binary_choices:
            trigger = choice.get("trigger_turn", 0)
            if scene_turns < trigger:
                continue
            
            # Check if any option has been selected
            choice_made = False
            for option_key in choice.get("options", {}).keys():
                # Check if the option's primary state key is set to true
                state_key = f"_choice_{choice['id']}_{option_key}"
                if self.get_state(state_key) == True:
                    choice_made = True
                    break
            
            if not choice_made:
                pending.append(choice)
        
        return pending
    
    def get_choice_by_id(self, choice_id: str) -> dict:
        """Get a binary choice config by its ID."""
        for choice in self._binary_choices:
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
        choice = self.get_choice_by_id(choice_id)
        if not choice:
            return False, f"Unknown choice: {choice_id}"
        
        options = choice.get("options", {})
        if option_key not in options:
            available = list(options.keys())
            return False, f"Invalid option '{option_key}'. Must be one of: {available}"
        
        # Check if choice already made
        for opt in options.keys():
            state_key = f"_choice_{choice_id}_{opt}"
            if self.get_state(state_key) == True:
                return False, f"Choice '{choice_id}' already made (selected: {opt})"
        
        # Mark this option as chosen
        choice_state_key = f"_choice_{choice_id}_{option_key}"
        self.set_state(choice_state_key, True, "system", turn_number, 
                      reason or f"Player chose {option_key}")
        
        # Apply the option's state changes
        option_config = options[option_key]
        state_changes = option_config.get("set", {})
        results = [f"✓ Choice made: {option_key}"]
        
        for key, value in state_changes.items():
            # Handle relative values like "+10" or "-20"
            if isinstance(value, str) and (value.startswith("+") or value.startswith("-")):
                current = self.get_state(key) or 0
                delta = int(value)
                value = current + delta
            
            success, msg = self.set_state(key, value, "ai", turn_number, 
                                         f"Choice consequence: {option_key}")
            results.append(f"  {msg}")
        
        return True, "\n".join(results)
    
    def get_choice_blockers(self) -> list:
        """
        Generate dynamic blockers for unresolved binary choices.
        These prevent scene advancement until choices are made.
        """
        blockers = []
        for choice in self._binary_choices:
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
    
    def _check_choice_blockers(self, key: str, new_value: Any) -> tuple[bool, str]:
        """Check if a state change is blocked by an unresolved binary choice."""
        if not self._progressive_config:
            return True, ""
        
        iterator_key = self._progressive_config.get("iterator")
        if key != iterator_key:
            return True, ""  # Only iterator changes can be blocked
        
        for blocker in self.get_choice_blockers():
            if blocker["target"] != new_value:
                continue
            
            # Check if ANY of the required options is true
            any_chosen = False
            for opt_key in blocker["requires_any"]:
                if self.get_state(opt_key) == True:
                    any_chosen = True
                    break
            
            if not any_chosen:
                return False, blocker["message"]
        
        return True, ""
    
    # ========== RIDDLE SYSTEM ==========
    
    def _init_riddles(self, turn_number: int):
        """Initialize riddle state (answer hashes, attempt counters)."""
        import hashlib
        
        for riddle in self._riddles:
            riddle_id = riddle.get("id")
            if not riddle_id:
                continue
            
            # Generate deterministic answer from seed
            answer = self._generate_riddle_answer(riddle)
            if answer is None:
                continue
            
            # Store hashed answer (AI can't see plaintext)
            answer_hash = hashlib.sha256(str(answer).encode()).hexdigest()
            self.set_state(f"_riddle_{riddle_id}_hash", answer_hash, "system", turn_number,
                          "Riddle initialized")
            
            # Initialize attempt counter
            self.set_state(f"_riddle_{riddle_id}_attempts", 0, "system", turn_number,
                          "Riddle attempts initialized")
            
            logger.debug(f"[RIDDLE] Initialized '{riddle_id}', answer_hash={answer_hash[:16]}...")
    
    def _ensure_riddles_initialized(self):
        """
        Ensure all riddles have their state initialized.
        Called on reload to handle restarts where _init_riddles wasn't run.
        Only initializes riddles that don't already have a hash.
        """
        import hashlib
        
        for riddle in self._riddles:
            riddle_id = riddle.get("id")
            if not riddle_id:
                continue
            
            # Check if already initialized
            existing_hash = self.get_state(f"_riddle_{riddle_id}_hash")
            if existing_hash:
                logger.debug(f"[RIDDLE] '{riddle_id}' already initialized")
                continue
            
            # Initialize this riddle
            answer = self._generate_riddle_answer(riddle)
            if answer is None:
                logger.warning(f"[RIDDLE] Could not generate answer for '{riddle_id}'")
                continue
            
            answer_hash = hashlib.sha256(str(answer).encode()).hexdigest()
            
            # Use turn 0 for system initialization
            self.set_state(f"_riddle_{riddle_id}_hash", answer_hash, "system", 0,
                          "Riddle initialized on reload")
            self.set_state(f"_riddle_{riddle_id}_attempts", 0, "system", 0,
                          "Riddle attempts initialized on reload")
            
            logger.info(f"[RIDDLE] Late-initialized '{riddle_id}' on reload")
    
    def _generate_riddle_answer(self, riddle: dict) -> str:
        """
        Generate riddle answer deterministically.
        
        For 'numeric' type: uses seed to generate digits
        For 'word' type: uses seed to select from wordlist
        For 'fixed' type: answer is in config (for author-defined puzzles)
        """
        import hashlib
        
        riddle_type = riddle.get("type", "fixed")
        riddle_id = riddle.get("id", "unknown")
        
        if riddle_type == "fixed":
            return riddle.get("answer")
        
        # Generate seed from chat_name + riddle_id for determinism
        seed_source = riddle.get("seed_from", "chat_name")
        if seed_source == "chat_name":
            seed = f"{self.chat_name}:{riddle_id}"
        else:
            seed = f"{seed_source}:{riddle_id}"
        
        seed_hash = hashlib.md5(seed.encode()).hexdigest()
        
        if riddle_type == "numeric":
            digits = riddle.get("digits", 4)
            # Extract digits from hash
            answer = ""
            for i in range(digits):
                answer += str(int(seed_hash[i*2:i*2+2], 16) % 10)
            return answer
        
        elif riddle_type == "word":
            wordlist = riddle.get("wordlist", ["XYZZY", "PLUGH", "PLOVER"])
            idx = int(seed_hash[:8], 16) % len(wordlist)
            return wordlist[idx]
        
        return None
    
    def get_riddle_clues(self, riddle_id: str, current_turn: int) -> list:
        """
        Get revealed clues for a riddle based on scene_turns.
        
        Returns list of clue strings that should be visible.
        """
        riddle = None
        for r in self._riddles:
            if r.get("id") == riddle_id:
                riddle = r
                break
        
        if not riddle:
            return []
        
        clues_config = riddle.get("clues", {})
        scene_turns = self.get_scene_turns(current_turn)
        revealed = []
        
        # Parse clue keys like "1", "2?scene_turns>=2", etc.
        clue_items = []
        for clue_key, clue_text in clues_config.items():
            base_key, conditions = self._parse_segment_key(clue_key)
            try:
                order = int(base_key)
            except ValueError:
                order = 999
            clue_items.append((order, conditions, clue_text))
        
        # Sort by order
        clue_items.sort(key=lambda x: x[0])
        
        # Temporarily set turn for condition matching
        self._current_turn_for_matching = current_turn
        
        for order, conditions, clue_text in clue_items:
            if not conditions:
                # Unconditional clue - always show
                revealed.append(clue_text)
            elif self._match_conditions(conditions):
                revealed.append(clue_text)
        
        self._current_turn_for_matching = None
        return revealed
    
    def attempt_riddle(self, riddle_id: str, answer: str, turn_number: int) -> tuple[bool, str]:
        """
        Attempt to solve a riddle.
        
        Returns:
            (success, message)
        """
        import hashlib
        
        riddle = None
        for r in self._riddles:
            if r.get("id") == riddle_id:
                riddle = r
                break
        
        if not riddle:
            return False, f"Unknown riddle: {riddle_id}"
        
        # Check if already solved
        solved_key = f"_riddle_{riddle_id}_solved"
        if self.get_state(solved_key) == True:
            return False, "This riddle has already been solved."
        
        # Check if locked out
        locked_key = f"_riddle_{riddle_id}_locked"
        if self.get_state(locked_key) == True:
            return False, "Too many failed attempts. The riddle is locked."
        
        # Get attempt count
        attempts_key = f"_riddle_{riddle_id}_attempts"
        attempts = self.get_state(attempts_key) or 0
        max_attempts = riddle.get("max_attempts", 999)
        
        # Check answer
        stored_hash = self.get_state(f"_riddle_{riddle_id}_hash")
        answer_hash = hashlib.sha256(str(answer).encode()).hexdigest()
        
        if answer_hash == stored_hash:
            # Success!
            self.set_state(solved_key, True, "system", turn_number, "Riddle solved")
            
            # Apply success state changes
            success_sets = riddle.get("success_sets", {})
            for key, value in success_sets.items():
                self.set_state(key, value, "ai", turn_number, f"Riddle '{riddle_id}' solved")
            
            success_msg = riddle.get("success_message", "Correct! The riddle is solved.")
            return True, f"✓ {success_msg}"
        
        # Wrong answer
        attempts += 1
        self.set_state(attempts_key, attempts, "system", turn_number, "Failed attempt")
        
        remaining = max_attempts - attempts
        if remaining <= 0:
            # Lockout
            self.set_state(locked_key, True, "system", turn_number, "Riddle locked")
            
            lockout_sets = riddle.get("lockout_sets", {})
            for key, value in lockout_sets.items():
                self.set_state(key, value, "ai", turn_number, f"Riddle '{riddle_id}' locked")
            
            lockout_msg = riddle.get("lockout_message", "Too many wrong answers. The riddle is now locked.")
            return False, f"✗ {lockout_msg}"
        
        fail_msg = riddle.get("fail_message", "That's not correct.")
        return False, f"✗ {fail_msg} ({remaining} attempts remaining)"
    
    def get_riddle_status(self, riddle_id: str) -> dict:
        """Get status of a riddle (for AI reference)."""
        riddle = None
        for r in self._riddles:
            if r.get("id") == riddle_id:
                riddle = r
                break
        
        if not riddle:
            return {"error": f"Unknown riddle: {riddle_id}"}
        
        return {
            "id": riddle_id,
            "solved": self.get_state(f"_riddle_{riddle_id}_solved") == True,
            "locked": self.get_state(f"_riddle_{riddle_id}_locked") == True,
            "attempts": self.get_state(f"_riddle_{riddle_id}_attempts") or 0,
            "max_attempts": riddle.get("max_attempts", 999)
        }

    def _select_segment(self, base_key: str, segments: dict) -> str:
        """
        Select and stack all matching segments for a base key.
        
        For turn-gated content, ALL matching conditions are stacked:
        - Base segment "1" always included if it matches
        - "1?scene_turns>=2" appended when condition matches  
        - "1?scene_turns>=5" appended when that condition also matches
        
        Returns combined content of all matching segments.
        """
        # Collect all variants for this base key
        variants = []  # [(conditions, content, priority)]
        fallback = None
        
        for seg_key, content in segments.items():
            parsed_base, conditions = self._parse_segment_key(seg_key)
            if parsed_base != base_key:
                continue
            
            if not conditions:
                fallback = content
            else:
                # Extract priority from conditions for ordering
                # Higher threshold = higher priority (shown later)
                priority = 0
                for cond in conditions:
                    if cond[0] == "scene_turns" and cond[1] in (">=", ">"):
                        priority = max(priority, cond[2])
                variants.append((conditions, content, priority))
        
        logger.debug(f"[SEGMENT] base_key={base_key}, variants={len(variants)}, has_fallback={fallback is not None}")
        
        # Build stacked content
        parts = []
        
        # Fallback (base) always comes first if present
        if fallback:
            parts.append(fallback)
        
        # Sort variants by priority (ascending) so lower thresholds come first
        variants.sort(key=lambda x: x[2])
        
        # Add ALL matching variants (stacking behavior)
        for conditions, content, priority in variants:
            match_result = self._match_conditions(conditions)
            logger.debug(f"[SEGMENT] Checking variant priority={priority}, conditions={conditions}, match={match_result}")
            if match_result:
                parts.append(content)
        
        logger.debug(f"[SEGMENT] Final parts count: {len(parts)}")
        return "".join(parts)  # No separator - content controls its own formatting
    
    def _get_navigation_config(self) -> dict:
        """Get navigation config from preset if present."""
        if not self._progressive_config:
            return {}
        return self._progressive_config.get("navigation", {})
    
    def _get_available_exits(self) -> list:
        """Get available exit directions from current room."""
        nav = self._get_navigation_config()
        if not nav:
            return []
        
        connections = nav.get("connections", {})
        position_key = nav.get("position_key", "player_room")
        current_room = self.get_state(position_key)
        
        if not current_room or current_room not in connections:
            return []
        
        room_exits = connections[current_room]
        # Filter out metadata keys starting with _
        return [d for d in room_exits.keys() if not d.startswith("_")]
    
    def get_room_for_direction(self, direction: str) -> tuple[str, str]:
        """
        Get destination room for a direction from current position.
        
        Returns:
            (destination_room, error_message) - destination is None if invalid
        """
        nav = self._get_navigation_config()
        if not nav:
            return None, "Navigation not configured for this preset"
        
        connections = nav.get("connections", {})
        position_key = nav.get("position_key", "player_room")
        current_room = self.get_state(position_key)
        
        if not current_room:
            return None, f"Current position unknown ({position_key} not set)"
        
        if current_room not in connections:
            return None, f"No exits defined for '{current_room}'"
        
        room_exits = connections[current_room]
        direction_lower = direction.lower()
        
        # Check exact match and common aliases
        aliases = {
            "n": "north", "s": "south", "e": "east", "w": "west",
            "u": "up", "d": "down", "ne": "northeast", "nw": "northwest",
            "se": "southeast", "sw": "southwest"
        }
        
        # Try direct match first
        if direction_lower in room_exits:
            return room_exits[direction_lower], ""
        
        # Try alias
        if direction_lower in aliases:
            full_dir = aliases[direction_lower]
            if full_dir in room_exits:
                return room_exits[full_dir], ""
        
        # Try reverse alias (user said "north", check for "n")
        for short, full in aliases.items():
            if direction_lower == full and short in room_exits:
                return room_exits[short], ""
        
        available = [d for d in room_exits.keys() if not d.startswith("_")]
        return None, f"Can't go {direction}. Exits: {', '.join(available)}"

    def _build_progressive_prompt(self) -> str:
        """Build prompt from progressive config, revealing only appropriate segments."""
        logger.info(f"[STATE] _build_progressive_prompt: config={self._progressive_config is not None}, preset={self._preset_name}")
        
        if not self._progressive_config:
            logger.debug("[PROMPT] No progressive_config set")
            return ""
        
        config = self._progressive_config
        base = config.get("base", "")
        segments = config.get("segments", {})
        iterator_key = config.get("iterator")
        mode = config.get("mode", "cumulative")  # cumulative or current_only
        nav_config = config.get("navigation", {})
        
        logger.debug(f"[PROMPT] iterator_key={iterator_key}, mode={mode}, segment_count={len(segments)}")
        
        # Load universal base instructions (cached after first load)
        if not hasattr(self, '_base_instructions'):
            self._base_instructions = ""
            base_path = Path(__file__).parent / "presets" / "_base.json"
            if base_path.exists():
                try:
                    with open(base_path, 'r', encoding='utf-8') as f:
                        base_data = json.load(f)
                    self._base_instructions = base_data.get("instructions", "")
                except Exception as e:
                    logger.warning(f"Could not load _base.json: {e}")
        
        if not iterator_key or not segments:
            logger.debug(f"[PROMPT] Early return: iterator_key={iterator_key}, segments={bool(segments)}")
            parts = []
            if self._base_instructions:
                parts.append(self._base_instructions)
            if base:
                parts.append(base)
            return "\n\n".join(parts) if parts else ""
        
        # Get current iterator value (can be int or string for room names)
        iterator_value = self.get_state(iterator_key)
        logger.debug(f"[PROMPT] iterator_value={iterator_value} (type={type(iterator_value).__name__})")
        
        if iterator_value is None:
            logger.debug("[PROMPT] iterator_value is None, returning base only")
            parts = []
            if self._base_instructions:
                parts.append(self._base_instructions)
            if base:
                parts.append(base)
            return "\n\n".join(parts) if parts else ""
        
        # Extract base keys from segments (strip ?conditions)
        base_keys = set()
        for seg_key in segments.keys():
            parsed_base, _ = self._parse_segment_key(seg_key)
            base_keys.add(parsed_base)
        
        logger.debug(f"[PROMPT] base_keys={sorted(base_keys)}")
        
        # Determine if we're using numeric or string keys
        is_numeric = isinstance(iterator_value, (int, float))
        
        # Collect revealed segments with variant support
        revealed = []
        
        if is_numeric:
            # Numeric mode: sort keys, cumulative or current_only
            iterator_value = int(iterator_value)
            numeric_keys = []
            for bk in base_keys:
                try:
                    numeric_keys.append(int(bk))
                except ValueError:
                    continue
            numeric_keys.sort()
            
            logger.debug(f"[PROMPT] numeric_keys={numeric_keys}, checking up to {iterator_value}")
            
            for seg_key in numeric_keys:
                if mode == "cumulative":
                    if seg_key <= iterator_value:
                        content = self._select_segment(str(seg_key), segments)
                        if content:
                            revealed.append(content)
                            logger.debug(f"[PROMPT] Revealed segment {seg_key} ({len(content)} chars)")
                else:  # current_only
                    if seg_key == iterator_value:
                        content = self._select_segment(str(seg_key), segments)
                        if content:
                            revealed.append(content)
                            logger.debug(f"[PROMPT] Revealed segment {seg_key} ({len(content)} chars)")
                        break
        else:
            # String mode (room names): only show current room's segment
            # Always current_only for room-based navigation
            content = self._select_segment(str(iterator_value), segments)
            if content:
                revealed.append(content)
                logger.debug(f"[PROMPT] Revealed room segment '{iterator_value}' ({len(content)} chars)")
        
        logger.debug(f"[PROMPT] Total revealed segments: {len(revealed)}")
        
        # Assemble final prompt
        parts = []
        if self._base_instructions:
            parts.append(self._base_instructions)
        if base:
            parts.append(base)
        parts.extend(revealed)
        
        # Inject pending binary choices
        current_turn = self._current_turn_for_matching or 0
        pending_choices = self.get_pending_choices(current_turn)
        if pending_choices:
            choice_section = ["", "⚠️ DECISION REQUIRED:"]
            for choice in pending_choices:
                choice_section.append(f"\n**{choice.get('prompt', 'Make a choice')}**")
                choice_section.append(f"Choice ID: {choice['id']}")
                choice_section.append("Options:")
                for opt_key, opt_config in choice.get("options", {}).items():
                    desc = opt_config.get("description", opt_key)
                    choice_section.append(f"  • {opt_key}: {desc}")
                choice_section.append(f"Use: make_choice(\"{choice['id']}\", \"<option>\", \"reason\")")
                if choice.get("required_for_scene"):
                    choice_section.append(f"(Must choose before advancing to scene {choice['required_for_scene']})")
            parts.append("\n".join(choice_section))
        
        # Inject riddle clues (with scene gating)
        for riddle in self._riddles:
            riddle_id = riddle.get("id")
            
            # Check scene visibility
            visible_from = riddle.get("visible_from_scene")
            if visible_from is not None:
                if isinstance(iterator_value, (int, float)) and iterator_value < visible_from:
                    continue  # Not visible yet
            
            status = self.get_riddle_status(riddle_id)
            
            if status.get("solved") or status.get("locked"):
                continue  # Don't show clues for resolved riddles
            
            clues = self.get_riddle_clues(riddle_id, current_turn)
            if clues:
                riddle_section = [f"\n🔐 RIDDLE: {riddle.get('name', riddle_id)}"]
                riddle_section.append(f"Type: {riddle.get('type', 'unknown')}")
                if riddle.get("digits"):
                    riddle_section.append(f"Format: {riddle['digits']} digits")
                riddle_section.append(f"Attempts: {status['attempts']}/{status['max_attempts']}")
                riddle_section.append("Clues revealed:")
                for i, clue in enumerate(clues, 1):
                    riddle_section.append(f"  {i}. {clue}")
                riddle_section.append(f"Use: attempt_riddle(\"{riddle_id}\", \"<answer>\")")
                parts.append("\n".join(riddle_section))
        
        # Add navigation hints if in graph mode
        if nav_config and nav_config.get("connections"):
            exits = self._get_available_exits()
            if exits:
                parts.append(f"Exits: {', '.join(exits)}")
        
        return "\n\n".join(parts)
    
    @property
    def preset_name(self) -> Optional[str]:
        return self._preset_name
    
    @property
    def progressive_config(self) -> Optional[dict]:
        return self._progressive_config
    
    @property
    def key_count(self) -> int:
        return len(self._current_state)
    
    def is_empty(self) -> bool:
        return len(self._current_state) == 0
    
    def reload_preset_config(self, preset_name: str) -> bool:
        """
        Reload progressive_config from preset WITHOUT resetting state.
        Also refreshes constraints on existing keys from preset definition.
        Used when state already exists in DB but we need the prompt config.
        """
        project_root = Path(__file__).parent.parent.parent
        search_paths = [
            project_root / "user" / "state_presets" / f"{preset_name}.json",
            project_root / "core" / "state_engine" / "presets" / f"{preset_name}.json",
        ]
        
        preset_path = None
        for path in search_paths:
            if path.exists():
                preset_path = path
                break
        
        if not preset_path:
            logger.warning(f"Preset not found for config reload: {preset_name}")
            return False
        
        try:
            with open(preset_path, 'r', encoding='utf-8') as f:
                preset = json.load(f)
            
            self._preset_name = preset_name
            self._progressive_config = preset.get("progressive_prompt")
            self._binary_choices = preset.get("binary_choices", [])
            self._riddles = preset.get("riddles", [])
            
            logger.info(f"[STATE] reload_preset_config: preset={preset_name}, has_progressive={self._progressive_config is not None}, segments={len(self._progressive_config.get('segments', {})) if self._progressive_config else 0}, choices={len(self._binary_choices)}, riddles={len(self._riddles)}")
            
            # Ensure riddles are initialized (may have been missed on restart)
            self._ensure_riddles_initialized()
            
            # Refresh constraints on existing keys from preset definition
            initial_state = preset.get("initial_state", {})
            for key, spec in initial_state.items():
                if key in self._current_state:
                    # Extract constraints (everything except value/type/label)
                    constraints = {k: v for k, v in spec.items() 
                                  if k not in ("value", "type", "label")}
                    self._current_state[key]["constraints"] = constraints if constraints else None
            
            # Persist preset name to DB for future restarts
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.chat_name,
                        "_preset",
                        json.dumps(preset_name),
                        "string",
                        "System: Active Preset",
                        None,
                        datetime.now().isoformat(),
                        "system",
                        0  # System key, turn doesn't matter
                    )
                )
                conn.commit()
            
            logger.debug(f"Reloaded config for preset '{preset_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to reload preset config: {e}")
            return False