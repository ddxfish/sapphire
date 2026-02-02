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
        self._last_advance_turn = -1  # Track to prevent double-advance in same turn

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
    
    def get_context_block(self, current_turn: int, action_summary: str = "") -> str:
        """
        Build rich context block for tool returns.
        
        Includes:
        - Action result summary
        - Current scene description  
        - State table (NEW/CURRENT/READ-ONLY sections)
        - Active riddle clues
        - Available exits
        - Tools reminder
        """
        lines = []
        
        # Action summary (passed in by tool)
        if action_summary:
            lines.append(action_summary)
            lines.append("")

        # Scene progress (so AI knows story continues, but not how many scenes)
        if self._progressive_config:
            iterator_key = self._progressive_config.get("iterator")
            if iterator_key:
                current_scene = self.get_state(iterator_key)
                entry = self.get_state_full(iterator_key)
                if entry and entry.get("constraints"):
                    max_scene = entry["constraints"].get("max")
                    if current_scene is not None and max_scene is not None:
                        if current_scene < max_scene:
                            lines.append("📍 More story ahead — use advance_scene() when ready")
                        else:
                            lines.append("📍 Final scene — story concludes here")
                        lines.append("")

        # Scene description from prompt builder
        if self._prompt_builder:
            scene_content = self._get_current_scene_content(current_turn)
            if scene_content:
                lines.append(scene_content)
                lines.append("")
        
        # State table
        state_block = self._format_state_table(current_turn)
        if state_block:
            lines.append("## State")
            lines.append(state_block)
            lines.append("")
        
        # Riddle clues
        if self._riddles:
            clues_block = self._format_riddle_clues(current_turn)
            if clues_block:
                lines.append(clues_block)
                lines.append("")
        
        # Navigation exits
        if self._navigation and self._navigation.is_enabled:
            exits = self._navigation.get_available_exits()
            if exits:
                lines.append(f"**Exits:** {', '.join(exits)}")
                lines.append("")
        
        # Tools reminder
        tools_list = self._get_available_tools_list()
        if tools_list:
            lines.append(f"**Tools:** {', '.join(tools_list)}")
        
        return "\n".join(lines).strip()
    
    def _get_current_scene_content(self, current_turn: int) -> str:
        """Extract just the current scene segment (not cumulative history)."""
        if not self._progressive_config:
            return ""
        
        iterator_key = self._progressive_config.get("iterator")
        if not iterator_key:
            return ""
        
        iterator_value = self.get_state(iterator_key)
        if iterator_value is None:
            return ""
        
        segments = self._progressive_config.get("segments", {})
        if not segments:
            return ""
        
        # Import here to avoid circular dependency
        from .prompts import select_segment
        
        # Get just the current scene's segment (not cumulative)
        content = select_segment(
            str(int(iterator_value) if isinstance(iterator_value, (int, float)) else iterator_value),
            segments,
            self.get_state,
            lambda: self.get_scene_turns(current_turn)
        )
        
        return content.strip() if content else ""
    
    def _format_state_table(self, current_turn: int) -> str:
        """Format state into NEW/CURRENT/READ-ONLY sections."""
        iterator_key = self._progressive_config.get("iterator") if self._progressive_config else None
        iterator_value = self._get_iterator_value()
        
        new_keys = []
        current_keys = []
        readonly_keys = []
        
        for key, entry in sorted(self._current_state.items()):
            if key.startswith("_"):
                continue
            
            constraints = entry.get("constraints", {}) or {}
            visible_from = constraints.get("visible_from")
            
            # Check visibility
            if visible_from is not None and isinstance(iterator_value, int):
                if iterator_value < visible_from:
                    continue
                # Just became visible this scene
                if iterator_value == visible_from:
                    new_keys.append((key, entry))
                    continue
            
            # Check if readonly (iterator or derived)
            is_readonly = (key == iterator_key) or constraints.get("readonly", False)
            
            if is_readonly:
                readonly_keys.append((key, entry))
            else:
                current_keys.append((key, entry))
        
        lines = []
        
        if new_keys:
            lines.append("NEW KEYS (just revealed):")
            for key, entry in new_keys:
                lines.append(self._format_state_line(key, entry))
        
        if current_keys:
            if new_keys:
                lines.append("")
            lines.append("CURRENT KEYS:")
            for key, entry in current_keys:
                lines.append(self._format_state_line(key, entry))
        
        if readonly_keys:
            if new_keys or current_keys:
                lines.append("")
            lines.append("READ-ONLY:")
            for key, entry in readonly_keys:
                hint = "use advance_scene()" if key == iterator_key else "derived"
                lines.append(self._format_state_line(key, entry, hint))
        
        return "\n".join(lines)
    
    def _format_state_line(self, key: str, entry: dict, hint: str = None) -> str:
        """Format a single state line with value and comment."""
        value = entry.get("value")
        constraints = entry.get("constraints", {}) or {}
        label = entry.get("label", "")
        key_type = constraints.get("type") or entry.get("type", "")
        
        # Format value
        if value is None or value == "":
            value_str = "[not set]"
        elif isinstance(value, bool):
            value_str = "true" if value else "false"
        elif isinstance(value, list):
            value_str = json.dumps(value)
        else:
            value_str = str(value)
        
        # Build comment
        comments = []
        
        # For choice keys, show valid options
        if key_type == "choice" and self._choices:
            options = self._choices.get_options_for_key(key)
            if options:
                comments.append(f"options: {', '.join(options)}")
        elif key_type == "riddle_answer":
            comments.append("answer key")
        
        if label and label != key:
            comments.append(label)
        if hint:
            comments.append(hint)
        
        comment = f"  # {', '.join(comments)}" if comments else ""
        
        return f"  {key} = {value_str}{comment}"
    
    def _format_riddle_clues(self, current_turn: int) -> str:
        """Format revealed clues for active riddles."""
        if not self._riddles or not self._riddles.riddles:
            return ""
        
        iterator_value = self._get_iterator_value()
        scene_turns = self.get_scene_turns(current_turn)
        sections = []
        
        for riddle in self._riddles.riddles:
            riddle_id = riddle.get("id")
            
            # Check scene visibility
            visible_from = riddle.get("visible_from_scene")
            if visible_from is not None:
                if isinstance(iterator_value, (int, float)) and iterator_value < visible_from:
                    continue
            
            status = self._riddles.get_status(riddle_id)
            if status.get("solved") or status.get("locked"):
                continue
            
            # Pass actual scene_turns to get correct clues
            clues = self._riddles.get_clues(riddle_id, scene_turns_override=scene_turns)
            total_clues = self._riddles.get_total_clues(riddle_id)
            if clues:
                lines = [f"**Memories for {riddle.get('name', riddle_id)}:**"]
                for i, clue in enumerate(clues, 1):
                    if i == len(clues):
                        lines.append(f"  [NEW CLUE:{i}/{total_clues}] {clue}")
                    else:
                        lines.append(f"  [CLUE:{i}/{total_clues}] {clue}")

                remaining = status['max_attempts'] - status['attempts']
                lines.append(f"  ({remaining} attempts remaining)")
                sections.append("\n".join(lines))
        
        return "\n\n".join(sections)
    
    def _get_available_tools_list(self) -> list:
        """Get list of currently available tool names."""
        tools = ["get_state()", "set_state(key, value, reason)", "advance_scene(reason)", "roll_dice(count, sides)"]
        
        if self._navigation and self._navigation.is_enabled:
            tools.append("move(direction, reason)")
        
        return tools
    
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

    def can_advance_this_turn(self, turn_number: int) -> tuple[bool, str]:
        """Check if advance_scene is allowed this turn (only once per turn)."""
        if self._last_advance_turn == turn_number:
            return False, "Already advanced this turn. Wait for player's next message."
        return True, ""

    def mark_advanced(self, turn_number: int):
        """Mark that advance_scene was used this turn."""
        self._last_advance_turn = turn_number
    
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
        """Set state value with validation and logging.
        
        Routes choice and riddle keys to their specialized handlers.
        """
        # Block AI writes to system keys
        if changed_by == "ai" and is_system_key(key):
            return False, f"Cannot modify system key: {key}"
        
        # Track special handler messages
        handler_result_msg = None
        
        # Route choice keys to ChoiceManager
        if changed_by == "ai" and self._choices and self._choices.is_choice_key(key):
            success, msg = self._choices.handle_set_state(key, value, turn_number, reason)
            if not success:
                return False, msg
            handler_result_msg = msg
            # Continue to actually store the value
        
        # Route riddle answer keys to RiddleManager
        if changed_by == "ai" and self._riddles and self._riddles.is_riddle_key(key):
            success, msg = self._riddles.handle_set_state(key, value, turn_number, reason)
            if not success:
                return False, msg
            handler_result_msg = msg
            # Continue to actually store the value
        
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
            
            # Return choice/riddle handler message if we have one
            if handler_result_msg:
                return True, handler_result_msg
            
            # Build response message
            # Check if key existed before (empty dict means truly new, not just null value)
            is_truly_new_key = not existing  # existing is {} if key wasn't in _current_state
            if is_truly_new_key:
                visible_keys = [k for k in self.get_visible_state().keys() if k != key]
                return True, f"⚠️ CREATED NEW KEY '{key}' = {value}. Did you mean one of these? {visible_keys}"
            elif is_iterator:
                return True, f"✓ Updated {key}: {old_value} → {value} (iterator: new content now visible)"
            elif old_value == value:
                return True, f"✓ {key} unchanged (already {value})"
            else:
                return True, f"✓ Set {key} = {value}"
            
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
        # Scene turns getter - accepts optional current_turn for accurate values
        # If not provided, returns 0 (safe default, callers should pass turn when possible)
        def scene_turns_getter(current_turn: int = None):
            if current_turn is not None:
                return self.get_scene_turns(current_turn)
            return 0  # Safe default when turn unknown
        
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
        
        # Prompt builder with game type for layered instructions
        game_type_name = self._game_type.name if self._game_type else "linear"
        self._prompt_builder = PromptBuilder(
            preset=preset,
            state_getter=self.get_state,
            scene_turns_getter=scene_turns_getter,
            game_type=game_type_name
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