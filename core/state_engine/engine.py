# core/state_engine/engine.py
"""
State Engine - Per-chat state management with full history for rollback.
Enables games, simulations, and interactive stories where AI reads/writes state via tools.

This module orchestrates:
- SQLite persistence with WAL mode
- Feature modules (choices, riddles, navigation)
- Game types (linear, rooms)
- Progressive prompt building
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .validation import is_system_key, validate_value, infer_type
from .prompts import PromptBuilder
from .game_types import get_game_type
from .features import ChoiceManager, RiddleManager, NavigationManager

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
        self._progressive_config = None
        self._scene_entered_at_turn = 0
        
        # Feature managers (initialized on preset load)
        self._choices: Optional[ChoiceManager] = None
        self._riddles: Optional[RiddleManager] = None
        self._navigation: Optional[NavigationManager] = None
        self._prompt_builder: Optional[PromptBuilder] = None
        self._game_type = None
        
        self._load_state()
    
    # ==================== DATABASE OPERATIONS ====================
    
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
                
                if scene_entered_at is not None:
                    self._scene_entered_at_turn = scene_entered_at
                
                if preset_name_from_db:
                    self.reload_preset_config(preset_name_from_db)
                
                logger.debug(f"Loaded {len(self._current_state)} state keys for '{self.chat_name}'" +
                            (f" (preset: {self._preset_name})" if self._preset_name else ""))
        except Exception as e:
            logger.error(f"Failed to load state for '{self.chat_name}': {e}")
            self._current_state = {}
    
    def reload_from_db(self):
        """Force reload state from database, clearing cache."""
        logger.info(f"[STATE] Reloading state from DB for '{self.chat_name}'")
        self._current_state = {}
        self._preset_name = None
        self._progressive_config = None
        self._scene_entered_at_turn = 0
        self._choices = None
        self._riddles = None
        self._navigation = None
        self._prompt_builder = None
        self._load_state()
    
    def _persist_system_key(self, key: str, value: Any, turn_number: int):
        """Persist a system key to database."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.chat_name, key, json.dumps(value), "string", f"System: {key}",
                     None, datetime.now().isoformat(), "system", turn_number)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist {key}: {e}")
    
    # ==================== STATE ACCESS ====================
    
    def get_state(self, key: str = None) -> Any:
        """Get state value(s). Returns single value if key specified, dict of all state if not."""
        if key:
            entry = self._current_state.get(key)
            return entry["value"] if entry else None
        return {k: v["value"] for k, v in self._current_state.items()}
    
    def get_state_full(self, key: str = None) -> Any:
        """Get full state entry with metadata (type, label, constraints)."""
        if key:
            return self._current_state.get(key)
        return self._current_state.copy()
    
    def get_scene_turns(self, current_turn: int) -> int:
        """Get number of turns spent in current scene/iterator value."""
        if self._scene_entered_at_turn > current_turn:
            logger.warning(f"[STATE] scene_entered_at ({self._scene_entered_at_turn}) > current_turn ({current_turn}), resetting")
            self._scene_entered_at_turn = current_turn
            self._persist_system_key("_scene_entered_at", current_turn, current_turn)
        return current_turn - self._scene_entered_at_turn
    
    def get_visible_state(self, current_turn: int = None) -> dict:
        """Get state filtered by visible_from constraints."""
        iterator_value = self._get_iterator_value()
        
        result = {}
        for key, entry in self._current_state.items():
            if key.startswith("_"):
                continue
            
            constraints = entry.get("constraints", {}) or {}
            visible_from = constraints.get("visible_from")
            
            if visible_from is not None:
                if isinstance(iterator_value, int) and iterator_value < visible_from:
                    continue
            
            result[key] = entry["value"]
        
        if current_turn is not None and self._progressive_config:
            result["scene_turns"] = self.get_scene_turns(current_turn)
        
        return result
    
    def _get_iterator_value(self) -> Any:
        """Get current iterator value."""
        if not self._progressive_config:
            return None
        iterator_key = self._progressive_config.get("iterator")
        if iterator_key:
            val = self.get_state(iterator_key)
            if isinstance(val, (int, float)):
                return int(val)
            return val
        return None
    
    # ==================== STATE MODIFICATION ====================
    
    def set_state(self, key: str, value: Any, changed_by: str, 
                  turn_number: int, reason: str = None) -> tuple[bool, str]:
        """Set state value with validation and logging."""
        # Block AI writes to system keys
        if changed_by == "ai" and is_system_key(key):
            return False, f"Cannot modify system key: {key}"
        
        existing = self._current_state.get(key, {})
        old_value = existing.get("value")
        constraints = existing.get("constraints")
        value_type = existing.get("type") or infer_type(value)
        label = existing.get("label")
        
        # Validate against constraints
        valid, error = validate_value(key, value, constraints, self.get_state)
        if not valid:
            return False, error
        
        # Check binary choice blockers (for iterator changes)
        if self._choices:
            valid, error = self._choices.check_blockers(key, value)
            if not valid:
                return False, error
        
        try:
            with self._get_connection() as conn:
                # Log the change
                conn.execute(
                    """INSERT INTO state_log 
                       (chat_name, key, old_value, new_value, changed_by, turn_number, timestamp, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.chat_name, key,
                     json.dumps(old_value) if old_value is not None else None,
                     json.dumps(value), changed_by, turn_number,
                     datetime.now().isoformat(), reason)
                )
                
                # Update current state
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.chat_name, key, json.dumps(value), value_type, label,
                     json.dumps(constraints) if constraints else None,
                     datetime.now().isoformat(), changed_by, turn_number)
                )
                conn.commit()
            
            # Update cache
            self._current_state[key] = {
                "value": value, "type": value_type, "label": label,
                "constraints": constraints, "turn": turn_number
            }
            
            logger.debug(f"State set: {key}={value} by {changed_by} at turn {turn_number}")
            
            # Detect iterator change - reset scene_turns tracking
            is_iterator = self._progressive_config and self._progressive_config.get("iterator") == key
            if is_iterator and old_value != value:
                self._scene_entered_at_turn = turn_number
                self._persist_system_key("_scene_entered_at", turn_number, turn_number)
                logger.info(f"[STATE] Iterator changed: scene_turns reset at turn {turn_number}")
            
            # Build response message
            is_new_key = old_value is None
            if is_new_key:
                visible_keys = [k for k in self.get_visible_state().keys() if k != key]
                return True, f"⚠️ CREATED NEW KEY '{key}' = {value}. Did you mean one of these? {visible_keys}"
            elif is_iterator:
                return True, f"✓ Updated {key}: {old_value} → {value} (iterator: new content now visible)"
            elif old_value == value:
                return True, f"✓ {key} unchanged (already {value})"
            else:
                return True, f"✓ Updated {key}: {old_value} → {value}"
            
        except Exception as e:
            logger.error(f"Failed to set state {key}: {e}")
            return False, f"Database error: {e}"
    
    def delete_key(self, key: str) -> bool:
        """Delete a state key."""
        if is_system_key(key):
            return False
        
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM state_current WHERE chat_name = ? AND key = ?",
                            (self.chat_name, key))
                conn.commit()
            self._current_state.pop(key, None)
            logger.debug(f"Deleted state key: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete state key {key}: {e}")
            return False
    
    def clear_all(self) -> bool:
        """Clear all state for this chat."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM state_current WHERE chat_name = ?", (self.chat_name,))
                conn.execute("DELETE FROM state_log WHERE chat_name = ?", (self.chat_name,))
                conn.commit()
            
            self._current_state = {}
            self._preset_name = None
            self._progressive_config = None
            self._scene_entered_at_turn = 0
            self._choices = None
            self._riddles = None
            self._navigation = None
            self._prompt_builder = None
            logger.info(f"Cleared all state for '{self.chat_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to clear state: {e}")
            return False
    
    def rollback_to_turn(self, target_turn: int) -> bool:
        """Rollback state to a specific turn by replaying log."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM state_log WHERE chat_name = ? AND turn_number > ?",
                            (self.chat_name, target_turn))
                conn.execute("DELETE FROM state_current WHERE chat_name = ?", (self.chat_name,))
                
                cursor = conn.execute(
                    """SELECT key, new_value, changed_by, turn_number, timestamp
                       FROM state_log WHERE chat_name = ? AND turn_number <= ?
                       ORDER BY id ASC""",
                    (self.chat_name, target_turn)
                )
                
                rebuilt_state = {}
                for row in cursor:
                    rebuilt_state[row["key"]] = {
                        "value": json.loads(row["new_value"]),
                        "changed_by": row["changed_by"],
                        "turn_number": row["turn_number"],
                        "timestamp": row["timestamp"]
                    }
                
                for key, data in rebuilt_state.items():
                    value = data["value"]
                    conn.execute(
                        """INSERT INTO state_current 
                           (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (self.chat_name, key, json.dumps(value), infer_type(value),
                         None, None, data["timestamp"], data["changed_by"], data["turn_number"])
                    )
                conn.commit()
            
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
                           FROM state_log WHERE chat_name = ? AND key = ?
                           ORDER BY id DESC LIMIT ?""",
                        (self.chat_name, key, limit)
                    )
                else:
                    cursor = conn.execute(
                        """SELECT key, old_value, new_value, changed_by, turn_number, timestamp, reason
                           FROM state_log WHERE chat_name = ?
                           ORDER BY id DESC LIMIT ?""",
                        (self.chat_name, limit)
                    )
                
                return [{
                    "key": row["key"],
                    "old_value": json.loads(row["old_value"]) if row["old_value"] else None,
                    "new_value": json.loads(row["new_value"]),
                    "changed_by": row["changed_by"],
                    "turn": row["turn_number"],
                    "timestamp": row["timestamp"],
                    "reason": row["reason"]
                } for row in cursor]
        except Exception as e:
            logger.error(f"Failed to get state history: {e}")
            return []
    
    # ==================== PRESETS ====================
    
    def load_preset(self, preset_name: str, turn_number: int) -> tuple[bool, str]:
        """Load a state preset, initializing all state keys."""
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
            return False, f"Preset not found: {preset_name}"
        
        try:
            with open(preset_path, 'r', encoding='utf-8') as f:
                preset = json.load(f)
            
            self.clear_all()
            
            # Initialize state from preset
            initial_state = preset.get("initial_state", {})
            for key, spec in initial_state.items():
                value = spec.get("value")
                self._current_state[key] = {
                    "value": value,
                    "type": spec.get("type"),
                    "label": spec.get("label"),
                    "constraints": {k: v for k, v in spec.items() if k not in ("value", "type", "label")},
                    "turn": turn_number
                }
            
            # Write to database
            with self._get_connection() as conn:
                for key, entry in self._current_state.items():
                    conn.execute(
                        """INSERT INTO state_current 
                           (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (self.chat_name, key, json.dumps(entry["value"]), entry["type"],
                         entry["label"], json.dumps(entry["constraints"]) if entry["constraints"] else None,
                         datetime.now().isoformat(), "system", turn_number)
                    )
                    conn.execute(
                        """INSERT INTO state_log 
                           (chat_name, key, old_value, new_value, changed_by, turn_number, timestamp, reason)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (self.chat_name, key, None, json.dumps(entry["value"]),
                         "system", turn_number, datetime.now().isoformat(), f"Preset: {preset_name}")
                    )
                
                conn.execute(
                    """INSERT OR REPLACE INTO state_current 
                       (chat_name, key, value, value_type, label, constraints, updated_at, updated_by, turn_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.chat_name, "_preset", json.dumps(preset_name), "string",
                     "System: Active Preset", None, datetime.now().isoformat(), "system", turn_number)
                )
                conn.commit()
            
            # Initialize features
            self._preset_name = preset_name
            self._progressive_config = preset.get("progressive_prompt")
            self._game_type = get_game_type(preset)
            self._init_features(preset, turn_number)
            
            # Initialize scene tracking
            self._scene_entered_at_turn = turn_number
            self._persist_system_key("_scene_entered_at", turn_number, turn_number)
            
            logger.info(f"Loaded preset '{preset_name}' with {len(self._current_state)} keys, game_type={self._game_type.name}")
            return True, f"Loaded preset: {preset_name}"
            
        except Exception as e:
            logger.error(f"Failed to load preset {preset_name}: {e}")
            return False, f"Error loading preset: {e}"
    
    def reload_preset_config(self, preset_name: str) -> bool:
        """Reload progressive_config from preset WITHOUT resetting state."""
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
            self._game_type = get_game_type(preset)
            
            # Reinitialize features (they need the preset config)
            self._init_features(preset, 0)
            
            # Ensure riddles are initialized
            if self._riddles:
                self._riddles.ensure_initialized()
            
            # Refresh constraints on existing keys
            initial_state = preset.get("initial_state", {})
            for key, spec in initial_state.items():
                if key in self._current_state:
                    constraints = {k: v for k, v in spec.items() if k not in ("value", "type", "label")}
                    self._current_state[key]["constraints"] = constraints if constraints else None
            
            # Persist preset name
            self._persist_system_key("_preset", preset_name, 0)
            
            logger.info(f"Reloaded config for preset '{preset_name}', game_type={self._game_type.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to reload preset config: {e}")
            return False
    
    def _init_features(self, preset: dict, turn_number: int):
        """Initialize feature managers from preset."""
        # Scene turns getter for features
        def scene_turns_getter():
            return self.get_scene_turns(turn_number)
        
        # Always initialize choices and riddles (they're lightweight if empty)
        self._choices = ChoiceManager(
            preset=preset,
            state_getter=self.get_state,
            state_setter=self.set_state,
            scene_turns_getter=scene_turns_getter
        )
        
        self._riddles = RiddleManager(
            preset=preset,
            state_getter=self.get_state,
            state_setter=self.set_state,
            scene_turns_getter=scene_turns_getter,
            chat_name=self.chat_name
        )
        
        # Initialize riddles if this is a fresh load
        if turn_number > 0 and self._riddles.riddles:
            self._riddles.initialize(turn_number)
        
        # Navigation only if game type supports it
        if self._game_type and 'navigation' in self._game_type.features:
            self._navigation = NavigationManager(
                preset=preset,
                state_getter=self.get_state,
                state_setter=self.set_state
            )
        else:
            self._navigation = None
        
        # Prompt builder
        self._prompt_builder = PromptBuilder(
            preset=preset,
            state_getter=self.get_state,
            scene_turns_getter=scene_turns_getter
        )
        self._prompt_builder.set_features(
            choices=self._choices,
            riddles=self._riddles,
            navigation=self._navigation
        )
    
    # ==================== PROMPT GENERATION ====================
    
    def format_for_prompt(self, include_vars: bool = True, include_story: bool = True, 
                          current_turn: int = None) -> str:
        """Format current state for system prompt injection."""
        logger.info(f"[STATE] format_for_prompt: vars={include_vars}, story={include_story}")
        
        parts = []
        
        # State variables
        if include_vars and self._current_state:
            lines = []
            iterator_value = self._get_iterator_value()
            
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
        
        # Tools hint
        tools = ["get_state()", "set_state(key, value, reason)", "roll_dice(count, sides)", "increment_counter(key, amount)"]
        if self._navigation and self._navigation.is_enabled:
            tools.insert(2, "move(direction, reason)")
        if self._choices and self._choices.choices:
            tools.append("make_choice(choice_id, option, reason)")
        if self._riddles and self._riddles.riddles:
            tools.append("attempt_riddle(riddle_id, answer)")
        parts.append("Tools: " + ", ".join(tools))
        
        # Progressive prompt content
        if include_story and self._prompt_builder:
            prompt_content = self._prompt_builder.build(current_turn or 0)
            if prompt_content:
                parts.append(prompt_content)
        
        return "\n\n".join(parts) if parts else "(state engine active - use get_state())"
    
    # ==================== FEATURE ACCESSORS ====================
    
    @property
    def choices(self) -> Optional[ChoiceManager]:
        return self._choices
    
    @property
    def riddles(self) -> Optional[RiddleManager]:
        return self._riddles
    
    @property
    def navigation(self) -> Optional[NavigationManager]:
        return self._navigation
    
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
