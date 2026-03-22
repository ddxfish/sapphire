# core/story_engine/loader.py
"""
Room-based story loader — parses story.yaml + rooms/*.md into preset dict.

Each room is a markdown file with YAML frontmatter:
  ---
  id: clearing
  name: The Awakening Clearing
  exits:
    north: forest_path
  ---
  ## The Awakening Clearing
  You wake on soft moss...

  ## after 2 turns
  [HINT] Sapphire glances north...

  ## if mushroom_choice = eat
  Your vision still swims with fractals...
"""

import logging
import re
import yaml
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_room_file(path: Path) -> dict:
    """
    Parse a single .md room file into a room dict.

    Returns:
        {
            "id": str,
            "name": str,
            "exits": dict,
            "type": str (optional, e.g. "death"),
            "respawn": str (optional),
            "respawn_set": dict (optional),
            "state": dict (optional, per-room state keys),
            "choice": dict (optional, binary choice definition),
            "riddle": dict (optional, riddle definition),
            "segments": dict (segment_key -> content),
        }
    """
    text = path.read_text(encoding='utf-8')

    # Split frontmatter from body
    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        raise ValueError(f"No YAML frontmatter in {path.name}")

    config = yaml.safe_load(frontmatter) or {}
    room_id = config.get("id", path.stem)

    # Parse markdown body into segments
    segments = _parse_segments(room_id, body)

    return {
        "id": room_id,
        "name": config.get("name", room_id.replace("_", " ").title()),
        "exits": config.get("exits", {}),
        "type": config.get("type"),
        "respawn": config.get("respawn"),
        "respawn_set": config.get("respawn_set"),
        "state": config.get("state"),
        "choice": config.get("choice"),
        "riddle": config.get("riddle"),
        "segments": segments,
    }


def _split_frontmatter(text: str) -> tuple:
    """Split YAML frontmatter from markdown body. Returns (frontmatter_str, body_str)."""
    text = text.strip()
    if not text.startswith("---"):
        return None, text

    # Find closing ---
    end = text.find("---", 3)
    if end == -1:
        return None, text

    frontmatter = text[3:end].strip()
    body = text[end + 3:].strip()
    return frontmatter, body


def _parse_segments(room_id: str, body: str) -> dict:
    """
    Parse markdown body into segment dict.

    Heading conventions:
      (no heading) -> base segment "room_id"
      ## after N turns -> "room_id?scene_turns>=N"
      ## if key = value -> "room_id?key=value"
      ## if key >= N -> "room_id?key>=N"
      ## if key > N -> "room_id?key>N"
      ## if key != value -> "room_id?key!=value"
    """
    segments = {}

    # Split on ## headings (but not ### or deeper)
    parts = re.split(r'^## ', body, flags=re.MULTILINE)

    # First part (before any heading) is the base segment
    if parts[0].strip():
        segments[room_id] = "\n\n## " + _strip_room_heading(parts[0], room_id)

    for part in parts[1:]:
        if not part.strip():
            continue

        # First line is the heading text
        lines = part.split("\n", 1)
        heading = lines[0].strip()
        content = lines[1] if len(lines) > 1 else ""

        seg_key = _heading_to_segment_key(room_id, heading)
        if seg_key == room_id:
            # This is the room's title heading — it's the base segment
            segments[room_id] = "\n\n## " + heading + "\n" + content
        else:
            # Conditional segment
            segments[seg_key] = "\n\n" + content.rstrip()

    # If we got nothing, use the whole body
    if not segments and body.strip():
        segments[room_id] = "\n\n" + body.strip()

    return segments


def _strip_room_heading(text: str, room_id: str) -> str:
    """If the base text starts with the room name heading, preserve it."""
    return text.strip()


def _heading_to_segment_key(room_id: str, heading: str) -> str:
    """Convert a markdown heading to a segment key."""
    h = heading.strip().lower()

    # "after N turns" -> "room_id?scene_turns>=N"
    m = re.match(r'after\s+(\d+)\s+turns?', h)
    if m:
        return f"{room_id}?scene_turns>={m.group(1)}"

    # "if key = value" / "if key >= N" / etc.
    m = re.match(r'if\s+(.+)', h)
    if m:
        condition = m.group(1).strip()
        # Parse comma-separated conditions
        conds = []
        for cond in condition.split(","):
            cond = cond.strip()
            # Match: key op value
            cm = re.match(r'(\w+)\s*(>=|<=|!=|>|<|=)\s*(.+)', cond)
            if cm:
                conds.append(f"{cm.group(1)}{cm.group(2)}{cm.group(3).strip()}")
            else:
                # Boolean shorthand: "if key" means key=true
                conds.append(f"{cond}=true")
        return f"{room_id}?{','.join(conds)}"

    # Not a condition heading — it's the room's title
    return room_id


def load_story_yaml(story_dir: Path) -> dict:
    """
    Load a story.yaml + rooms/*.md directory into a preset dict
    compatible with StoryEngine.load_preset().

    Args:
        story_dir: Directory containing story.yaml and rooms/

    Returns:
        Full preset dict (same shape as story.json)
    """
    yaml_path = story_dir / "story.yaml"
    meta = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}

    # Load all room files
    rooms_dir = story_dir / "rooms"
    rooms = []
    if rooms_dir.is_dir():
        for md_file in sorted(rooms_dir.glob("*.md")):
            try:
                room = load_room_file(md_file)
                rooms.append(room)
            except Exception as e:
                logger.error(f"Failed to load room {md_file.name}: {e}")

    if not rooms:
        logger.warning(f"No rooms found in {rooms_dir}")

    start_room = meta.get("start", rooms[0]["id"] if rooms else "start")

    # Build initial_state
    initial_state = {}

    # Global state from story.yaml
    for key, spec in (meta.get("state") or {}).items():
        if isinstance(spec, dict):
            initial_state[key] = spec
        else:
            initial_state[key] = {"value": spec, "type": _infer_type(spec)}

    # Room iterator
    initial_state["room"] = {
        "value": start_room,
        "type": "string",
        "label": "Current Location"
    }

    # Per-room state (with visible_from)
    for room in rooms:
        if room["state"]:
            for key, spec in room["state"].items():
                if isinstance(spec, dict):
                    entry = dict(spec)
                    entry.setdefault("visible_from", room["id"])
                    initial_state[key] = entry
                else:
                    initial_state[key] = {
                        "value": spec,
                        "type": _infer_type(spec),
                        "visible_from": room["id"]
                    }

    # Build segments from all rooms
    segments = {}
    for room in rooms:
        segments.update(room["segments"])

    # Build connections and room_names
    connections = {}
    room_names = {}
    for room in rooms:
        room_id = room["id"]
        room_names[room_id] = room["name"]
        if room["exits"]:
            connections[room_id] = dict(room["exits"])
        else:
            # Terminal rooms (death, ending, or rooms with no exits) get empty connections
            connections[room_id] = {}

    # Build binary_choices
    binary_choices = []
    for room in rooms:
        if room["choice"]:
            choice = dict(room["choice"])
            choice.setdefault("visible_from_room", room["id"])
            binary_choices.append(choice)
            # Add choice state key if not already in initial_state
            state_key = choice.get("state_key")
            if state_key and state_key not in initial_state:
                initial_state[state_key] = {
                    "value": "",
                    "type": "choice",
                    "label": choice.get("label", state_key),
                    "visible_from": room["id"]
                }

    # Build riddles
    riddles = []
    for room in rooms:
        if room["riddle"]:
            riddle = dict(room["riddle"])
            riddle.setdefault("visible_from_room", room["id"])
            # Parse clue keys: "2 after 2 turns" -> "2?scene_turns>=2"
            if "clues" in riddle:
                riddle["clues"] = _parse_riddle_clues(riddle["clues"])
            riddles.append(riddle)
            # Add riddle state key if not already in initial_state
            state_key = riddle.get("state_key")
            if state_key and state_key not in initial_state:
                initial_state[state_key] = {
                    "value": "",
                    "type": "riddle_answer",
                    "label": riddle.get("name", state_key),
                    "visible_from": room["id"]
                }

    # Build death_rooms
    death_rooms = {}
    for room in rooms:
        if room["type"] == "death":
            death_rooms[room["id"]] = {
                "respawn": room.get("respawn", start_room),
                "respawn_set": room.get("respawn_set", {})
            }

    # Assemble preset
    preset = {
        "name": meta.get("name", story_dir.name.replace("_", " ").title()),
        "description": meta.get("description", ""),
        "initial_state": initial_state,
        "progressive_prompt": {
            "iterator": "room",
            "mode": "current_only",
            "base": meta.get("base", ""),
            "segments": segments,
            "navigation": {
                "position_key": "room",
                "connections": connections,
                "room_names": room_names,
                "death_rooms": death_rooms,
            }
        },
    }

    if binary_choices:
        preset["binary_choices"] = binary_choices
    if riddles:
        preset["riddles"] = riddles

    logger.info(f"[LOADER] Loaded story '{preset['name']}' — {len(rooms)} rooms, "
                f"{len(binary_choices)} choices, {len(riddles)} riddles, "
                f"{len(death_rooms)} death rooms")

    return preset


def _parse_riddle_clues(clues: dict) -> dict:
    """
    Parse riddle clue keys from human-friendly format to engine format.

    "2 after 2 turns" -> "2?scene_turns>=2"
    "3 after 4 turns" -> "3?scene_turns>=4"
    Standard keys like "1", "2?scene_turns>=2" pass through unchanged.
    """
    parsed = {}
    for key, value in clues.items():
        m = re.match(r'^(\d+)\s+after\s+(\d+)\s+turns?$', str(key).strip())
        if m:
            parsed[f"{m.group(1)}?scene_turns>={m.group(2)}"] = value
        else:
            parsed[str(key)] = value
    return parsed


def _infer_type(value: Any) -> str:
    """Infer state type from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "string"
