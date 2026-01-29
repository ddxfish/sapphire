# core/state_engine/prompts.py
"""
Progressive Prompt Assembly - Reveals content based on iterator state.

Supports two modes:
- cumulative: Show all segments up to current iterator value
- current_only: Show only the segment matching current iterator value

Segments can have conditional variants using "key?condition" syntax.
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from .conditions import parse_segment_key, match_conditions

logger = logging.getLogger(__name__)

# Cached base instructions (loaded once)
_base_instructions_cache: Optional[str] = None


def load_base_instructions() -> str:
    """Load universal base instructions from _base.json (cached)."""
    global _base_instructions_cache
    
    if _base_instructions_cache is not None:
        return _base_instructions_cache
    
    base_path = Path(__file__).parent / "presets" / "_base.json"
    if base_path.exists():
        try:
            with open(base_path, 'r', encoding='utf-8') as f:
                base_data = json.load(f)
            _base_instructions_cache = base_data.get("instructions", "")
        except Exception as e:
            logger.warning(f"Could not load _base.json: {e}")
            _base_instructions_cache = ""
    else:
        _base_instructions_cache = ""
    
    return _base_instructions_cache


def select_segment(
    base_key: str,
    segments: dict,
    state_getter: Callable[[str], Any],
    scene_turns_getter: Optional[Callable[[], int]] = None
) -> str:
    """
    Select and stack all matching segments for a base key.
    
    For turn-gated content, ALL matching conditions are stacked:
    - Base segment "1" always included if it matches
    - "1?scene_turns>=2" appended when condition matches
    - "1?scene_turns>=5" appended when that condition also matches
    
    Returns combined content of all matching segments.
    """
    variants = []  # [(conditions, content, priority)]
    fallback = None
    
    for seg_key, content in segments.items():
        parsed_base, conditions = parse_segment_key(seg_key)
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
        if match_conditions(conditions, state_getter, scene_turns_getter):
            parts.append(content)
            logger.debug(f"[SEGMENT] Matched variant priority={priority}")
    
    logger.debug(f"[SEGMENT] Final parts count: {len(parts)}")
    return "".join(parts)  # No separator - content controls its own formatting


class PromptBuilder:
    """Builds progressive prompts from preset configuration."""
    
    def __init__(
        self,
        preset: dict,
        state_getter: Callable[[str], Any],
        scene_turns_getter: Optional[Callable[[], int]] = None
    ):
        self._config = preset.get("progressive_prompt", {})
        self._get_state = state_getter
        self._get_scene_turns = scene_turns_getter
        
        # Feature managers (set by engine after init)
        self._choices = None
        self._riddles = None
        self._navigation = None
    
    def set_features(self, choices=None, riddles=None, navigation=None):
        """Set feature managers for prompt injection."""
        self._choices = choices
        self._riddles = riddles
        self._navigation = navigation
    
    @property
    def config(self) -> dict:
        """Raw progressive prompt config."""
        return self._config
    
    @property
    def iterator_key(self) -> Optional[str]:
        """State key used as the iterator."""
        return self._config.get("iterator")
    
    @property
    def mode(self) -> str:
        """Reveal mode: 'cumulative' or 'current_only'."""
        return self._config.get("mode", "cumulative")
    
    def build(self, current_turn: int = 0) -> str:
        """
        Build the progressive prompt for the current state.
        
        Args:
            current_turn: Current turn number for scene_turns calculation
        """
        logger.info(f"[PROMPT] Building progressive prompt, config={bool(self._config)}")
        
        base_instructions = load_base_instructions()
        base = self._config.get("base", "")
        segments = self._config.get("segments", {})
        iterator_key = self.iterator_key
        mode = self.mode
        
        logger.debug(f"[PROMPT] iterator_key={iterator_key}, mode={mode}, segment_count={len(segments)}")
        
        # Early return if no segments
        if not iterator_key or not segments:
            parts = []
            if base_instructions:
                parts.append(base_instructions)
            if base:
                parts.append(base)
            return "\n\n".join(parts) if parts else ""
        
        # Get current iterator value
        iterator_value = self._get_state(iterator_key)
        logger.debug(f"[PROMPT] iterator_value={iterator_value} (type={type(iterator_value).__name__ if iterator_value else 'None'})")
        
        if iterator_value is None:
            parts = []
            if base_instructions:
                parts.append(base_instructions)
            if base:
                parts.append(base)
            return "\n\n".join(parts) if parts else ""
        
        # Collect revealed segments
        revealed = self._collect_segments(segments, iterator_value, mode)
        
        # Assemble prompt parts
        parts = []
        if base_instructions:
            parts.append(base_instructions)
        if base:
            parts.append(base)
        parts.extend(revealed)
        
        # Inject feature content
        if self._choices:
            choice_section = self._build_choices_section(current_turn)
            if choice_section:
                parts.append(choice_section)
        
        if self._riddles:
            riddle_section = self._build_riddles_section(iterator_value, current_turn)
            if riddle_section:
                parts.append(riddle_section)
        
        if self._navigation and self._navigation.is_enabled:
            exits = self._navigation.get_available_exits()
            if exits:
                parts.append(f"Exits: {', '.join(exits)}")
        
        return "\n\n".join(parts)
    
    def _collect_segments(self, segments: dict, iterator_value: Any, mode: str) -> list:
        """Collect revealed segments based on iterator value and mode."""
        # Extract base keys from segments
        base_keys = set()
        for seg_key in segments.keys():
            parsed_base, _ = parse_segment_key(seg_key)
            base_keys.add(parsed_base)
        
        logger.debug(f"[PROMPT] base_keys={sorted(base_keys)}")
        
        revealed = []
        is_numeric = isinstance(iterator_value, (int, float))
        
        if is_numeric:
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
                        content = select_segment(str(seg_key), segments, self._get_state, self._get_scene_turns)
                        if content:
                            revealed.append(content)
                            logger.debug(f"[PROMPT] Revealed segment {seg_key}")
                else:  # current_only
                    if seg_key == iterator_value:
                        content = select_segment(str(seg_key), segments, self._get_state, self._get_scene_turns)
                        if content:
                            revealed.append(content)
                            logger.debug(f"[PROMPT] Revealed segment {seg_key}")
                        break
        else:
            # String mode (room names): only show current room's segment
            content = select_segment(str(iterator_value), segments, self._get_state, self._get_scene_turns)
            if content:
                revealed.append(content)
                logger.debug(f"[PROMPT] Revealed room segment '{iterator_value}'")
        
        logger.debug(f"[PROMPT] Total revealed segments: {len(revealed)}")
        return revealed
    
    def _build_choices_section(self, current_turn: int) -> str:
        """Build prompt section for pending binary choices."""
        if not self._choices:
            return ""
        
        pending = self._choices.get_pending(current_turn)
        if not pending:
            return ""
        
        lines = ["", "⚠️ DECISION REQUIRED:"]
        for choice in pending:
            state_key = choice.get("state_key", choice.get("id"))
            lines.append(f"\n**{choice.get('prompt', 'Make a choice')}**")
            lines.append(f"Set: {state_key}")
            lines.append("Options:")
            for opt_key, opt_config in choice.get("options", {}).items():
                desc = opt_config.get("description", opt_key)
                lines.append(f"  • \"{opt_key}\": {desc}")
            lines.append(f"Use: set_state(\"{state_key}\", \"<option>\", \"reason\")")
            if choice.get("required_for_scene"):
                lines.append(f"(Must choose before advancing to scene {choice['required_for_scene']})")
        
        return "\n".join(lines)
    
    def _build_riddles_section(self, iterator_value: Any, current_turn: int) -> str:
        """Build prompt section for active riddles."""
        if not self._riddles:
            return ""
        
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
            
            clues = self._riddles.get_clues(riddle_id)
            if clues:
                state_key = riddle.get("state_key", f"riddle_{riddle_id}")
                lines = [f"\n🔐 RIDDLE: {riddle.get('name', riddle_id)}"]
                if riddle.get("digits"):
                    lines.append(f"Format: {riddle['digits']} digits")
                lines.append(f"Attempts: {status['attempts']}/{status['max_attempts']}")
                lines.append("Clues revealed:")
                for i, clue in enumerate(clues, 1):
                    lines.append(f"  {i}. {clue}")
                lines.append(f"Use: set_state(\"{state_key}\", \"<answer>\", \"reason\")")
                sections.append("\n".join(lines))
        
        return "\n".join(sections)