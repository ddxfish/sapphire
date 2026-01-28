# core/chat/state_engine.py
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

logger = logging.getLogger(__name__)


class StateEngine:
    """Manages per-chat state with SQLite persistence and rollback support."""
    
    def __init__(self, chat_name: str, db_path: Path):
        self.chat_name = chat_name
        self._db_path = db_path
        self._current_state = {}  # Cache: key -> {value, type, label, constraints, turn}
        self._preset_name = None
        self._progressive_config = None  # {iterator, mode, base, segments}
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
                for row in cursor:
                    self._current_state[row["key"]] = {
                        "value": json.loads(row["value"]),
                        "type": row["value_type"],
                        "label": row["label"],
                        "constraints": json.loads(row["constraints"]) if row["constraints"] else None,
                        "turn": row["turn_number"]
                    }
                logger.debug(f"Loaded {len(self._current_state)} state keys for '{self.chat_name}'")
        except Exception as e:
            logger.error(f"Failed to load state for '{self.chat_name}': {e}")
            self._current_state = {}
    
    def _is_system_key(self, key: str) -> bool:
        """Check if key is system-managed (starts with _)."""
        return key.startswith("_")
    
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
            
            if is_new_key:
                # Warn AI they created a new key (might be a typo)
                # Show existing user keys (exclude system keys and the new key itself)
                existing_keys = [k for k in self._current_state.keys() 
                               if not k.startswith('_') and k != key]
                msg = f"⚠️ CREATED NEW KEY '{key}' = {value}. This key did not exist! Did you mean one of these? {existing_keys}"
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
        # Get project root from state_engine.py location (core/chat/state_engine.py -> project root)
        project_root = Path(__file__).parent.parent.parent
        
        # Search paths: user first, then core
        search_paths = [
            project_root / "user" / "state_presets" / f"{preset_name}.json",
            project_root / "core" / "state_presets" / f"{preset_name}.json",
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
                conn.commit()
            
            self._preset_name = preset_name
            self._progressive_config = preset.get("progressive_prompt")
            
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
    
    def format_for_prompt(self) -> str:
        """Format current state for system prompt injection with progressive reveal."""
        if not self._current_state:
            return "(no state)"
        
        lines = []
        for key, entry in sorted(self._current_state.items()):
            value = entry["value"]
            label = entry.get("label")
            
            # Format value for readability
            if isinstance(value, list):
                value_str = json.dumps(value)
            elif isinstance(value, bool):
                value_str = "true" if value else "false"
            else:
                value_str = str(value)
            
            # Show key (required for set_state) with optional label for readability
            if label and label != key:
                lines.append(f"{key} ({label}): {value_str}")
            else:
                lines.append(f"{key}: {value_str}")
        
        state_block = "\n".join(lines)
        
        # Add tools hint
        state_block += "\n\nTools: get_state(), set_state(key, value, reason), roll_dice(count, sides), increment_counter(key, amount)"
        
        # Add progressive prompt content (only revealed segments)
        prompt_content = self._build_progressive_prompt()
        if prompt_content:
            state_block += f"\n\n{prompt_content}"
        
        return state_block
    
    def _build_progressive_prompt(self) -> str:
        """Build prompt from progressive config, revealing only appropriate segments."""
        if not self._progressive_config:
            return ""
        
        config = self._progressive_config
        base = config.get("base", "")
        segments = config.get("segments", {})
        iterator_key = config.get("iterator")
        mode = config.get("mode", "cumulative")  # cumulative or current_only
        
        if not iterator_key or not segments:
            return base
        
        # Get current iterator value
        iterator_value = self.get_state(iterator_key)
        if iterator_value is None or not isinstance(iterator_value, (int, float)):
            return base  # Can't evaluate, just return base
        
        iterator_value = int(iterator_value)
        
        # Collect revealed segments
        revealed = []
        segment_keys = sorted([int(k) for k in segments.keys()])
        
        for seg_key in segment_keys:
            if mode == "cumulative":
                # Reveal all segments <= current iterator
                if seg_key <= iterator_value:
                    revealed.append(segments[str(seg_key)])
            else:  # current_only
                # Only reveal exact match
                if seg_key == iterator_value:
                    revealed.append(segments[str(seg_key)])
                    break
        
        # Assemble final prompt
        parts = [base] if base else []
        parts.extend(revealed)
        
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
        Used when state already exists in DB but we need the prompt config.
        """
        project_root = Path(__file__).parent.parent.parent
        search_paths = [
            project_root / "user" / "state_presets" / f"{preset_name}.json",
            project_root / "core" / "state_presets" / f"{preset_name}.json",
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
            logger.debug(f"Reloaded config for preset '{preset_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to reload preset config: {e}")
            return False