# core/story_engine/prompts.py
"""
Progressive Prompt Assembly - Reveals content based on iterator state.

Supports two modes:
- cumulative: Show all segments up to current iterator value
- current_only: Show only the segment matching current iterator value

Segments can have conditional variants using "key?condition" syntax.

Instructions loaded from _instructions.json (unified for all story types).
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from .conditions import parse_segment_key, match_conditions

logger = logging.getLogger(__name__)

# Cached instructions
_instructions_cache: str = None


def load_instructions() -> str:
    """Load unified instructions from _instructions.json."""
    global _instructions_cache

    if _instructions_cache is not None:
        return _instructions_cache

    presets_dir = Path(__file__).parent / "presets"
    instructions_path = presets_dir / "_instructions.json"

    if instructions_path.exists():
        try:
            with open(instructions_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _instructions_cache = data.get("instructions", "")
        except Exception as e:
            logger.warning(f"Could not load _instructions.json: {e}")
            _instructions_cache = ""
    else:
        logger.warning("_instructions.json not found")
        _instructions_cache = ""

    logger.info(f"[PROMPT] Loaded instructions, {len(_instructions_cache)} chars")
    return _instructions_cache


def clear_instructions_cache():
    """Clear the instructions cache (call if files change at runtime)."""
    global _instructions_cache
    _instructions_cache = None


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
        scene_turns_getter: Optional[Callable[[int], int]] = None,  # Now takes current_turn as arg
    ):
        self._config = preset.get("progressive_prompt", {})
        self._get_state = state_getter
        self._get_scene_turns = scene_turns_getter  # Signature: (current_turn) -> scene_turns

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

        # Load unified instructions
        instructions = load_instructions()
        base = self._config.get("base", "")
        segments = self._config.get("segments", {})
        iterator_key = self.iterator_key
        mode = self.mode
        
        logger.debug(f"[PROMPT] iterator_key={iterator_key}, mode={mode}, segment_count={len(segments)}")
        
        # Early return if no segments
        if not iterator_key or not segments:
            parts = []
            if instructions:
                parts.append(instructions)
            if base:
                parts.append(base)
            return "\n\n".join(parts) if parts else ""
        
        # Get current iterator value
        iterator_value = self._get_state(iterator_key)
        logger.debug(f"[PROMPT] iterator_value={iterator_value} (type={type(iterator_value).__name__ if iterator_value else 'None'})")
        
        if iterator_value is None:
            parts = []
            if instructions:
                parts.append(instructions)
            if base:
                parts.append(base)
            return "\n\n".join(parts) if parts else ""
        
        # Collect revealed segments (pass current_turn for scene_turns calculation)
        revealed = self._collect_segments(segments, iterator_value, mode, current_turn)
        
        # Assemble prompt parts
        parts = []
        if instructions:
            parts.append(instructions)
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
            exits = self._navigation.get_exits_with_descriptions()
            if exits:
                parts.append(f"Exits: {', '.join(exits)}")
        
        return "\n\n".join(parts)
    
    def build_split(self, current_turn: int = 0) -> tuple[str, str]:
        """
        Build progressive prompt split into static and dynamic parts.

        Static: instructions, base text, current room/scene base segment.
        Dynamic: conditional segments (turn-gated, state-gated), choices, riddles, exits.

        Returns:
            (static_content, dynamic_content)
        """
        instructions = load_instructions()
        base = self._config.get("base", "")
        segments = self._config.get("segments", {})
        iterator_key = self.iterator_key
        mode = self.mode

        # Static always gets instructions + base
        static_parts = []
        if instructions:
            static_parts.append(instructions)
        if base:
            static_parts.append(base)

        if not iterator_key or not segments:
            return "\n\n".join(static_parts), ""

        iterator_value = self._get_state(iterator_key)
        if iterator_value is None:
            return "\n\n".join(static_parts), ""

        # Collect segments split into base (static) and conditional (dynamic)
        base_segs, conditional_segs = self._collect_segments_split(
            segments, iterator_value, mode, current_turn
        )
        static_parts.extend(base_segs)

        # Dynamic: conditional segments + feature content
        dynamic_parts = list(conditional_segs)

        if self._choices:
            choice_section = self._build_choices_section(current_turn)
            if choice_section:
                dynamic_parts.append(choice_section)

        if self._riddles:
            riddle_section = self._build_riddles_section(iterator_value, current_turn)
            if riddle_section:
                dynamic_parts.append(riddle_section)

        if self._navigation and self._navigation.is_enabled:
            exits = self._navigation.get_exits_with_descriptions()
            if exits:
                dynamic_parts.append(f"Exits: {', '.join(exits)}")

        return "\n\n".join(static_parts), "\n\n".join(dynamic_parts)

    def _collect_segments_split(self, segments: dict, iterator_value: Any,
                                mode: str, current_turn: int = 0) -> tuple[list, list]:
        """
        Collect segments split into base (static) and conditional (dynamic).

        Returns:
            (base_segments, conditional_segments)
        """
        base_keys = set()
        for seg_key in segments.keys():
            parsed_base, _ = parse_segment_key(seg_key)
            base_keys.add(parsed_base)

        def scene_turns_for_this_build():
            if self._get_scene_turns:
                return self._get_scene_turns(current_turn)
            return 0

        base_segs = []
        conditional_segs = []
        is_numeric = isinstance(iterator_value, (int, float))

        if is_numeric:
            iv = int(iterator_value)
            numeric_keys = sorted(int(bk) for bk in base_keys if bk.isdigit())

            for seg_key in numeric_keys:
                if mode == "cumulative" and seg_key <= iv:
                    pass
                elif mode != "cumulative" and seg_key == iv:
                    pass
                else:
                    continue

                sk = str(seg_key)
                # Base segment (no conditions) → static
                if sk in segments:
                    base_segs.append(segments[sk])
                # Conditional variants → dynamic
                for full_key, content in segments.items():
                    parsed_base, conditions = parse_segment_key(full_key)
                    if parsed_base == sk and conditions:
                        if match_conditions(conditions, self._get_state, scene_turns_for_this_build):
                            conditional_segs.append(content)

                if mode != "cumulative":
                    break
        else:
            # String iterator (room names)
            sk = str(iterator_value)
            if sk in segments:
                base_segs.append(segments[sk])
            for full_key, content in segments.items():
                parsed_base, conditions = parse_segment_key(full_key)
                if parsed_base == sk and conditions:
                    if match_conditions(conditions, self._get_state, scene_turns_for_this_build):
                        conditional_segs.append(content)

        return base_segs, conditional_segs

    def _collect_segments(self, segments: dict, iterator_value: Any, mode: str, current_turn: int = 0) -> list:
        """Collect revealed segments based on iterator value and mode."""
        # Extract base keys from segments
        base_keys = set()
        for seg_key in segments.keys():
            parsed_base, _ = parse_segment_key(seg_key)
            base_keys.add(parsed_base)

        logger.debug(f"[PROMPT] base_keys={sorted(base_keys)}")

        # Create scene_turns getter that uses current_turn
        # _get_scene_turns now expects current_turn as argument
        def scene_turns_for_this_build():
            if self._get_scene_turns:
                return self._get_scene_turns(current_turn)
            return 0

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
                        content = select_segment(str(seg_key), segments, self._get_state, scene_turns_for_this_build)
                        if content:
                            revealed.append(content)
                            logger.debug(f"[PROMPT] Revealed segment {seg_key}")
                else:  # current_only
                    if seg_key == iterator_value:
                        content = select_segment(str(seg_key), segments, self._get_state, scene_turns_for_this_build)
                        if content:
                            revealed.append(content)
                            logger.debug(f"[PROMPT] Revealed segment {seg_key}")
                        break
        else:
            # String mode (room names): only show current room's segment
            content = select_segment(str(iterator_value), segments, self._get_state, scene_turns_for_this_build)
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
            
            # Check visibility - supports both scene (numeric) and room (string)
            visible_from_scene = riddle.get("visible_from_scene")
            visible_from_room = riddle.get("visible_from_room")

            if visible_from_scene is not None:
                if isinstance(iterator_value, (int, float)) and iterator_value < visible_from_scene:
                    continue
            elif visible_from_room is not None:
                if isinstance(iterator_value, str) and iterator_value != visible_from_room:
                    continue
            
            status = self._riddles.get_status(riddle_id)
            if status.get("solved") or status.get("locked"):
                continue

            # Compute scene_turns for clue visibility
            scene_turns = self._get_scene_turns(current_turn) if self._get_scene_turns else 0
            clues = self._riddles.get_clues(riddle_id, scene_turns_override=scene_turns)
            total_clues = self._riddles.get_total_clues(riddle_id)
            if clues:
                state_key = riddle.get("state_key", f"riddle_{riddle_id}")
                lines = [f"\n## RIDDLE: {riddle.get('name', riddle_id)}"]
                if riddle.get("digits"):
                    lines.append(f"Format: {riddle['digits']} digits")
                lines.append(f"Attempts: {status['attempts']}/{status['max_attempts']}")
                lines.append("Your character's memories (weave into narrative — NEVER invent clues):")
                for i, clue in enumerate(clues, 1):
                    if i == len(clues):
                        lines.append(f"  [NEW CLUE:{i}/{total_clues}] {clue}")
                    else:
                        lines.append(f"  [CLUE:{i}/{total_clues}] {clue}")
                lines.append(f"To attempt: set_state(\"{state_key}\", \"<answer>\", \"reason\")")
                sections.append("\n".join(lines))
        
        return "\n".join(sections)