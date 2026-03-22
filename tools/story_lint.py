#!/usr/bin/env python3
"""
Story Lint — structural analysis and validation for per-room story format.

A dev tool for AI and humans building stories. Understands the full story
structure without needing to read every room file at once.

Usage:
    python tools/story_lint.py overview <story_dir>
    python tools/story_lint.py validate <story_dir>
    python tools/story_lint.py room <story_dir> <room_id>
    python tools/story_lint.py map <story_dir>
    python tools/story_lint.py flow <story_dir>
    python tools/story_lint.py state <story_dir>
    python tools/story_lint.py paths <story_dir> [--from start] [--to room_id]
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.story_engine.loader import load_room_file, load_story_yaml


# ============================================================
# Data Loading
# ============================================================

def load_rooms(story_dir: Path) -> list[dict]:
    """Load all room files from a story directory."""
    rooms_dir = story_dir / "rooms"
    if not rooms_dir.is_dir():
        return []
    rooms = []
    for md in sorted(rooms_dir.glob("*.md")):
        try:
            rooms.append(load_room_file(md))
        except Exception as e:
            rooms.append({"id": md.stem, "_error": str(e)})
    return rooms


def load_meta(story_dir: Path) -> dict:
    """Load story.yaml metadata."""
    import yaml
    yaml_path = story_dir / "story.yaml"
    if not yaml_path.exists():
        return {}
    return yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}


# ============================================================
# Overview — compact structural summary
# ============================================================

def overview(story_dir: Path) -> str:
    """Compact structural summary of a story."""
    meta = load_meta(story_dir)
    rooms = load_rooms(story_dir)
    preset = load_story_yaml(story_dir)

    room_map = {r["id"]: r for r in rooms if "_error" not in r}
    errors = [r for r in rooms if "_error" in r]

    choices = preset.get("binary_choices", [])
    riddles = preset.get("riddles", [])
    nav = preset["progressive_prompt"]["navigation"]
    death_rooms = nav.get("death_rooms", {})
    connections = nav["connections"]

    # Count segments
    total_segments = len(preset["progressive_prompt"]["segments"])
    conditional_segments = sum(1 for k in preset["progressive_prompt"]["segments"] if "?" in k)

    lines = []
    lines.append(f"# {meta.get('name', story_dir.name)}")
    if meta.get("description"):
        lines.append(f"{meta['description']}")
    lines.append("")
    lines.append(f"Rooms: {len(room_map)} | Choices: {len(choices)} | Riddles: {len(riddles)} | "
                 f"Death rooms: {len(death_rooms)} | Segments: {total_segments} ({conditional_segments} conditional)")
    lines.append(f"Start: {meta.get('start', '???')}")
    if errors:
        lines.append(f"ERRORS: {len(errors)} rooms failed to parse")
    lines.append("")

    # Map
    lines.append("## Map")
    start = meta.get("start", "")
    for room_id in connections:
        exits = connections[room_id]
        room = room_map.get(room_id, {})
        room_type = room.get("type", "")
        name = nav["room_names"].get(room_id, room_id)

        # Room label
        tags = []
        if room_id == start:
            tags.append("START")
        if room_type == "death":
            respawn = death_rooms.get(room_id, {}).get("respawn", "?")
            tags.append(f"DEATH→{respawn}")
        if not exits:
            tags.append("TERMINAL")
        if any(c.get("visible_from_room") == room_id for c in choices):
            choice_ids = [c["id"] for c in choices if c.get("visible_from_room") == room_id]
            tags.append(f"CHOICE:{','.join(choice_ids)}")
        if any(r.get("visible_from_room") == room_id for r in riddles):
            riddle_ids = [r["id"] for r in riddles if r.get("visible_from_room") == room_id]
            tags.append(f"RIDDLE:{','.join(riddle_ids)}")

        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        exit_str = ", ".join(f"{d}→{dest}" for d, dest in exits.items()) if exits else "(no exits)"
        lines.append(f"  {room_id} ({name}){tag_str}")
        lines.append(f"    → {exit_str}")

    lines.append("")

    # Choices summary
    if choices:
        lines.append("## Choices")
        for c in choices:
            opts = list(c["options"].keys())
            blocking = f" [blocks: {c['required_for_room']}]" if c.get("required_for_room") else ""
            lines.append(f"  {c['id']}: {c['state_key']} @ {c.get('visible_from_room', '?')} "
                         f"→ {', '.join(opts)}{blocking}")
        lines.append("")

    # Riddles summary
    if riddles:
        lines.append("## Riddles")
        for r in riddles:
            clue_count = len(r.get("clues", {}))
            dice = f", dice DC {r['dice_dc']}" if r.get("dice_dc") else ""
            lines.append(f"  {r['id']}: {r['state_key']} @ {r.get('visible_from_room', '?')} "
                         f"— {clue_count} clues, {r.get('max_attempts', '?')} attempts{dice}")
        lines.append("")

    # Global state
    global_state = meta.get("state", {})
    if global_state:
        lines.append("## Global State")
        for key, spec in global_state.items():
            if isinstance(spec, dict):
                t = spec.get("type", "?")
                v = spec.get("value", "?")
                lines.append(f"  {key}: {t} = {v}")
            else:
                lines.append(f"  {key} = {spec}")
        lines.append("")

    # Per-room state
    room_state_keys = []
    for room in rooms:
        if "_error" in room:
            continue
        if room.get("state"):
            for key, spec in room["state"].items():
                t = spec.get("type", "?") if isinstance(spec, dict) else type(spec).__name__
                room_state_keys.append((key, t, room["id"]))
    if room_state_keys:
        lines.append("## Room State Keys")
        for key, t, room_id in room_state_keys:
            lines.append(f"  {key}: {t} (visible_from: {room_id})")
        lines.append("")

    if errors:
        lines.append("## Parse Errors")
        for r in errors:
            lines.append(f"  {r['id']}: {r['_error']}")

    return "\n".join(lines)


# ============================================================
# Validate — find structural problems
# ============================================================

def validate(story_dir: Path) -> str:
    """Find structural problems in a story."""
    meta = load_meta(story_dir)
    rooms = load_rooms(story_dir)
    preset = load_story_yaml(story_dir)

    room_map = {r["id"]: r for r in rooms if "_error" not in r}
    room_ids = set(room_map.keys())
    nav = preset["progressive_prompt"]["navigation"]
    connections = nav["connections"]
    death_rooms = nav.get("death_rooms", {})
    choices = preset.get("binary_choices", [])
    riddles = preset.get("riddles", [])
    segments = preset["progressive_prompt"]["segments"]
    start = meta.get("start", "")

    issues = []  # (severity, message)

    # --- Parse errors ---
    for r in rooms:
        if "_error" in r:
            issues.append(("ERROR", f"Room '{r['id']}' failed to parse: {r['_error']}"))

    # --- Start room ---
    if not start:
        issues.append(("ERROR", "No 'start' defined in story.yaml"))
    elif start not in room_ids:
        issues.append(("ERROR", f"Start room '{start}' does not exist"))

    # --- Orphan rooms (no incoming connections) ---
    incoming = defaultdict(set)
    for room_id, exits in connections.items():
        for direction, dest in exits.items():
            incoming[dest].add(room_id)
    # Death room respawns also count as incoming
    for dr_id, dr_info in death_rooms.items():
        respawn = dr_info.get("respawn")
        if respawn:
            incoming[respawn].add(dr_id)

    for room_id in room_ids:
        if room_id == start:
            continue
        if room_id not in incoming:
            issues.append(("WARN", f"Orphan room '{room_id}' — no incoming connections (unreachable)"))

    # --- Dangling exits (point to nonexistent rooms) ---
    for room_id, exits in connections.items():
        for direction, dest in exits.items():
            if dest not in room_ids:
                issues.append(("ERROR", f"Room '{room_id}' exit '{direction}' → '{dest}' (room does not exist)"))

    # --- Dead ends (non-terminal, non-death rooms with no outward exits) ---
    for room_id, exits in connections.items():
        if not exits and room_id not in death_rooms:
            room = room_map.get(room_id, {})
            room_type = room.get("type")
            if room_type != "death":
                # Check if it's an intentional terminal (like endings)
                # Terminal rooms are fine — just flag them as info
                issues.append(("INFO", f"Terminal room '{room_id}' — no outward exits"))

    # --- Choice references ---
    for c in choices:
        vis = c.get("visible_from_room")
        if vis and vis not in room_ids:
            issues.append(("ERROR", f"Choice '{c['id']}' visible_from_room '{vis}' does not exist"))
        req = c.get("required_for_room")
        if req and req not in room_ids:
            issues.append(("ERROR", f"Choice '{c['id']}' required_for_room '{req}' does not exist"))
        # Check options aren't empty
        if not c.get("options"):
            issues.append(("WARN", f"Choice '{c['id']}' has no options"))

    # --- Riddle references ---
    for r in riddles:
        vis = r.get("visible_from_room")
        if vis and vis not in room_ids:
            issues.append(("ERROR", f"Riddle '{r['id']}' visible_from_room '{vis}' does not exist"))
        if not r.get("clues"):
            issues.append(("WARN", f"Riddle '{r['id']}' has no clues"))

    # --- Death room respawn targets ---
    for dr_id, dr_info in death_rooms.items():
        respawn = dr_info.get("respawn")
        if not respawn:
            issues.append(("WARN", f"Death room '{dr_id}' has no respawn target"))
        elif respawn not in room_ids:
            issues.append(("ERROR", f"Death room '{dr_id}' respawn target '{respawn}' does not exist"))

    # --- State key conflicts (same key in multiple rooms with different types) ---
    state_defs = {}  # key -> [(type, room_id)]
    for room in rooms:
        if "_error" in room or not room.get("state"):
            continue
        for key, spec in room["state"].items():
            t = spec.get("type", "?") if isinstance(spec, dict) else type(spec).__name__
            state_defs.setdefault(key, []).append((t, room["id"]))
    for key, defs in state_defs.items():
        types = set(t for t, _ in defs)
        if len(types) > 1:
            locs = ", ".join(f"{t}@{r}" for t, r in defs)
            issues.append(("ERROR", f"State key '{key}' defined with conflicting types: {locs}"))
        if len(defs) > 1 and len(types) == 1:
            locs = ", ".join(r for _, r in defs)
            issues.append(("WARN", f"State key '{key}' defined in multiple rooms: {locs}"))

    # --- Reachability analysis (BFS from start) ---
    if start and start in room_ids:
        reachable = set()
        queue = [start]
        while queue:
            current = queue.pop(0)
            if current in reachable:
                continue
            reachable.add(current)
            for dest in connections.get(current, {}).values():
                if dest not in reachable:
                    queue.append(dest)
            # Death room respawns
            if current in death_rooms:
                respawn = death_rooms[current].get("respawn")
                if respawn and respawn not in reachable:
                    queue.append(respawn)

        unreachable = room_ids - reachable
        for room_id in unreachable:
            issues.append(("ERROR", f"Room '{room_id}' is unreachable from start '{start}'"))

    # --- Conditional segments reference valid state keys ---
    all_state_keys = set(preset["initial_state"].keys())
    all_state_keys.add("scene_turns")  # pseudo-variable
    for seg_key in segments:
        if "?" not in seg_key:
            continue
        _, cond_str = seg_key.split("?", 1)
        for cond in cond_str.split(","):
            # Extract key name (before operator)
            import re
            m = re.match(r'(\w+)', cond)
            if m:
                cond_key = m.group(1)
                if cond_key not in all_state_keys:
                    issues.append(("WARN", f"Segment '{seg_key}' references unknown state key '{cond_key}'"))

    # --- One-way connections (A→B but no B→A) ---
    for room_id, exits in connections.items():
        for direction, dest in exits.items():
            dest_exits = connections.get(dest, {})
            if room_id not in dest_exits.values():
                if dest not in death_rooms and room_map.get(dest, {}).get("type") != "death":
                    issues.append(("INFO", f"One-way connection: {room_id} →{direction}→ {dest} (no return path)"))

    # Format output
    if not issues:
        return f"✓ No issues found in {len(room_ids)} rooms."

    # Sort by severity
    severity_order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    issues.sort(key=lambda x: severity_order.get(x[0], 99))

    lines = [f"Story: {meta.get('name', story_dir.name)} — {len(room_ids)} rooms\n"]
    errors = sum(1 for s, _ in issues if s == "ERROR")
    warns = sum(1 for s, _ in issues if s == "WARN")
    infos = sum(1 for s, _ in issues if s == "INFO")
    lines.append(f"Found: {errors} errors, {warns} warnings, {infos} info\n")

    for severity, msg in issues:
        icon = {"ERROR": "✗", "WARN": "⚠", "INFO": "ℹ"}.get(severity, "?")
        lines.append(f"  {icon} [{severity}] {msg}")

    return "\n".join(lines)


# ============================================================
# Room — detailed view of a single room
# ============================================================

def room_detail(story_dir: Path, room_id: str) -> str:
    """Detailed view of a single room."""
    rooms = load_rooms(story_dir)
    room_map = {r["id"]: r for r in rooms}
    preset = load_story_yaml(story_dir)
    nav = preset["progressive_prompt"]["navigation"]
    connections = nav["connections"]
    segments = preset["progressive_prompt"]["segments"]

    room = room_map.get(room_id)
    if not room:
        return f"Room '{room_id}' not found. Available: {', '.join(sorted(room_map.keys()))}"

    if "_error" in room:
        return f"Room '{room_id}' has parse error: {room['_error']}"

    lines = []
    lines.append(f"# {room['name']} ({room['id']})")
    if room.get("type"):
        lines.append(f"Type: {room['type']}")
        if room.get("respawn"):
            lines.append(f"Respawn: {room['respawn']}")
            if room.get("respawn_set"):
                lines.append(f"Respawn state: {room['respawn_set']}")
    lines.append("")

    # Exits
    exits = room.get("exits", {})
    lines.append("## Exits")
    if exits:
        for d, dest in exits.items():
            dest_name = nav["room_names"].get(dest, dest)
            lines.append(f"  {d} → {dest} ({dest_name})")
    else:
        lines.append("  (none — terminal room)")
    lines.append("")

    # Incoming connections
    incoming = []
    for src_id, src_exits in connections.items():
        for d, dest in src_exits.items():
            if dest == room_id:
                incoming.append(f"{src_id} via {d}")
    lines.append("## Incoming")
    if incoming:
        for ic in incoming:
            lines.append(f"  ← {ic}")
    else:
        lines.append("  (none — only reachable as start or via respawn)")
    lines.append("")

    # State keys
    if room.get("state"):
        lines.append("## State Keys (defined here)")
        for key, spec in room["state"].items():
            if isinstance(spec, dict):
                lines.append(f"  {key}: {spec.get('type', '?')} = {spec.get('value', '?')} — {spec.get('label', '')}")
            else:
                lines.append(f"  {key} = {spec}")
        lines.append("")

    # Choice
    if room.get("choice"):
        c = room["choice"]
        lines.append("## Choice")
        lines.append(f"  ID: {c.get('id', '?')}")
        lines.append(f"  Key: {c.get('state_key', '?')}")
        if c.get("required_for_room"):
            lines.append(f"  Blocks: {c['required_for_room']}")
        lines.append(f"  Options:")
        for opt, cfg in c.get("options", {}).items():
            desc = cfg.get("description", "")
            sets = cfg.get("set", {})
            set_str = f" → {sets}" if sets else ""
            lines.append(f"    {opt}: {desc}{set_str}")
        lines.append("")

    # Riddle
    if room.get("riddle"):
        r = room["riddle"]
        lines.append("## Riddle")
        lines.append(f"  ID: {r.get('id', '?')}")
        lines.append(f"  Key: {r.get('state_key', '?')}")
        lines.append(f"  Type: {r.get('type', '?')}")
        lines.append(f"  Attempts: {r.get('max_attempts', '?')}")
        if r.get("dice_dc"):
            lines.append(f"  Dice bypass: DC {r['dice_dc']}")
        lines.append(f"  Clues ({len(r.get('clues', {}))}):")
        for ck, cv in r.get("clues", {}).items():
            lines.append(f"    [{ck}] {cv[:80]}{'...' if len(cv) > 80 else ''}")
        lines.append("")

    # Segments (conditional content)
    room_segments = {k: v for k, v in segments.items()
                     if k == room_id or k.startswith(f"{room_id}?")}
    if room_segments:
        lines.append("## Segments")
        for sk in sorted(room_segments.keys()):
            content = room_segments[sk]
            preview = content.strip()[:100].replace("\n", " ")
            if sk == room_id:
                lines.append(f"  [base] {preview}...")
            else:
                cond = sk.split("?", 1)[1]
                lines.append(f"  [if {cond}] {preview}...")
        lines.append("")

    # Word count
    total_words = sum(len(v.split()) for v in room_segments.values())
    lines.append(f"Word count: {total_words}")

    return "\n".join(lines)


# ============================================================
# Map — ASCII connection graph
# ============================================================

def ascii_map(story_dir: Path) -> str:
    """ASCII art map of room connections."""
    meta = load_meta(story_dir)
    preset = load_story_yaml(story_dir)
    nav = preset["progressive_prompt"]["navigation"]
    connections = nav["connections"]
    room_names = nav["room_names"]
    death_rooms = nav.get("death_rooms", {})
    start = meta.get("start", "")

    lines = []
    lines.append(f"# {meta.get('name', '?')} — Room Graph\n")

    # BFS from start to show rooms in discovery order
    visited = []
    queue = [start] if start else list(connections.keys())[:1]
    seen = set()

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        visited.append(current)
        for dest in connections.get(current, {}).values():
            if dest not in seen:
                queue.append(dest)
        if current in death_rooms:
            respawn = death_rooms[current].get("respawn")
            if respawn and respawn not in seen:
                queue.append(respawn)

    # Add any remaining rooms not reachable
    for room_id in connections:
        if room_id not in seen:
            visited.append(room_id)

    for room_id in visited:
        exits = connections.get(room_id, {})
        name = room_names.get(room_id, room_id)

        # Tags
        tags = []
        if room_id == start:
            tags.append("★")
        if room_id in death_rooms:
            tags.append("💀")
        if not exits:
            tags.append("◉")

        tag_str = " ".join(tags)
        prefix = f"{tag_str} " if tag_str else ""

        lines.append(f"{prefix}{room_id}")
        for direction, dest in exits.items():
            dest_name = room_names.get(dest, dest)
            arrow = "──" if dest in connections and room_id in [v for v in connections.get(dest, {}).values()] else "─→"
            lines.append(f"  ├─{direction}─{arrow} {dest} ({dest_name})")

        if room_id in death_rooms:
            respawn = death_rooms[room_id].get("respawn", "?")
            lines.append(f"  └─respawn──→ {respawn}")

        lines.append("")

    lines.append("Legend: ★ start | 💀 death | ◉ terminal | ── bidirectional | ─→ one-way")

    return "\n".join(lines)


# ============================================================
# Flow — narrative flow analysis
# ============================================================

def flow_analysis(story_dir: Path) -> str:
    """Analyze narrative flow — critical path, optional content, pacing."""
    meta = load_meta(story_dir)
    rooms = load_rooms(story_dir)
    preset = load_story_yaml(story_dir)
    nav = preset["progressive_prompt"]["navigation"]
    connections = nav["connections"]
    death_rooms = nav.get("death_rooms", {})
    choices = preset.get("binary_choices", [])
    riddles = preset.get("riddles", [])
    segments = preset["progressive_prompt"]["segments"]
    start = meta.get("start", "")

    room_map = {r["id"]: r for r in rooms if "_error" not in r}
    room_ids = set(room_map.keys())

    lines = []
    lines.append(f"# Flow Analysis: {meta.get('name', '?')}\n")

    # Find terminal rooms
    terminals = [rid for rid, exits in connections.items()
                 if not exits and rid not in death_rooms]

    # Find all paths from start to each terminal (BFS, limit depth)
    lines.append("## Paths to Endings")
    if not terminals:
        lines.append("  No terminal rooms found!")
    else:
        for terminal in terminals:
            paths = _find_all_paths(connections, start, terminal, max_depth=20)
            if paths:
                shortest = min(paths, key=len)
                longest = max(paths, key=len)
                lines.append(f"\n  To {terminal} ({nav['room_names'].get(terminal, '?')}):")
                lines.append(f"    Paths found: {len(paths)}")
                lines.append(f"    Shortest ({len(shortest)-1} moves): {' → '.join(shortest)}")
                if len(longest) != len(shortest):
                    lines.append(f"    Longest  ({len(longest)-1} moves): {' → '.join(longest)}")
            else:
                lines.append(f"\n  To {terminal}: NO PATH FOUND from {start}")
    lines.append("")

    # Critical path (rooms on ALL shortest paths to any terminal)
    all_critical = set()
    for terminal in terminals:
        paths = _find_all_paths(connections, start, terminal, max_depth=20)
        if paths:
            shortest_len = min(len(p) for p in paths)
            shortest_paths = [p for p in paths if len(p) == shortest_len]
            # Rooms on ALL shortest paths
            if shortest_paths:
                common = set(shortest_paths[0])
                for p in shortest_paths[1:]:
                    common &= set(p)
                all_critical |= common

    if all_critical:
        lines.append("## Critical Path Rooms")
        lines.append(f"  Rooms on ALL shortest paths: {', '.join(sorted(all_critical))}")
        optional = room_ids - all_critical - set(death_rooms.keys())
        if optional:
            lines.append(f"  Optional rooms: {', '.join(sorted(optional))}")
        lines.append("")

    # Choice flow — which rooms are affected by each choice
    if choices:
        lines.append("## Choice Impact")
        for c in choices:
            key = c.get("state_key", "?")
            affected = [sk for sk in segments if f"{key}=" in sk]
            rooms_affected = set()
            for sk in affected:
                base = sk.split("?")[0]
                rooms_affected.add(base)
            lines.append(f"  {c['id']} ({key}):")
            lines.append(f"    Options: {', '.join(c.get('options', {}).keys())}")
            if c.get("required_for_room"):
                lines.append(f"    Blocks movement to: {c['required_for_room']}")
            if rooms_affected:
                lines.append(f"    Affects content in: {', '.join(sorted(rooms_affected))}")
            # State mutations
            for opt, cfg in c.get("options", {}).items():
                sets = cfg.get("set", {})
                if sets:
                    lines.append(f"    {opt} → {sets}")
        lines.append("")

    # Pacing — word counts per room
    lines.append("## Pacing (word counts)")
    for room_id in sorted(room_ids):
        room_segs = {k: v for k, v in segments.items()
                     if k == room_id or k.startswith(f"{room_id}?")}
        base_words = len(segments.get(room_id, "").split())
        total_words = sum(len(v.split()) for v in room_segs.values())
        cond_count = sum(1 for k in room_segs if "?" in k)
        bar = "█" * (total_words // 20) or "▏"
        lines.append(f"  {room_id:20s} {bar} {total_words}w ({cond_count} variants)")

    return "\n".join(lines)


def _find_all_paths(connections: dict, start: str, end: str, max_depth: int = 20) -> list:
    """Find all paths between two rooms (BFS with path tracking, depth-limited)."""
    paths = []
    queue = [(start, [start])]

    while queue:
        current, path = queue.pop(0)
        if len(path) > max_depth:
            continue
        if current == end:
            paths.append(path)
            continue
        for dest in connections.get(current, {}).values():
            if dest not in path:  # No cycles
                queue.append((dest, path + [dest]))

    return paths


# ============================================================
# State — full state key analysis
# ============================================================

def state_analysis(story_dir: Path) -> str:
    """Full state key analysis — where defined, where used, where mutated."""
    meta = load_meta(story_dir)
    rooms = load_rooms(story_dir)
    preset = load_story_yaml(story_dir)
    segments = preset["progressive_prompt"]["segments"]
    choices = preset.get("binary_choices", [])
    riddles = preset.get("riddles", [])
    import re

    lines = []
    lines.append(f"# State Analysis: {meta.get('name', '?')}\n")

    # Collect all state keys and their info
    all_keys = {}  # key -> {type, defined_in, visible_from, used_in_conditions, mutated_by}

    # From initial_state
    for key, spec in preset["initial_state"].items():
        if key == "room":
            continue
        all_keys[key] = {
            "type": spec.get("type", "?"),
            "value": spec.get("value", "?"),
            "label": spec.get("label", ""),
            "visible_from": spec.get("visible_from"),
            "used_in": [],
            "mutated_by": [],
        }

    # Where used in conditions
    for seg_key in segments:
        if "?" not in seg_key:
            continue
        base, cond_str = seg_key.split("?", 1)
        for cond in cond_str.split(","):
            m = re.match(r'(\w+)', cond)
            if m and m.group(1) != "scene_turns":
                key = m.group(1)
                if key in all_keys:
                    all_keys[key]["used_in"].append(base)

    # Where mutated by choices
    for c in choices:
        for opt, cfg in c.get("options", {}).items():
            for key, val in cfg.get("set", {}).items():
                if key in all_keys:
                    all_keys[key]["mutated_by"].append(f"choice:{c['id']}.{opt}")

    # Where mutated by riddles
    for r in riddles:
        for key, val in r.get("success_sets", {}).items():
            if key in all_keys:
                all_keys[key]["mutated_by"].append(f"riddle:{r['id']}.success")
        for key, val in r.get("lockout_sets", {}).items():
            if key in all_keys:
                all_keys[key]["mutated_by"].append(f"riddle:{r['id']}.lockout")
        for key, val in r.get("dice_success_sets", {}).items():
            if key in all_keys:
                all_keys[key]["mutated_by"].append(f"riddle:{r['id']}.dice")

    # Print state table
    for key in sorted(all_keys.keys()):
        info = all_keys[key]
        lines.append(f"## {key}")
        lines.append(f"  Type: {info['type']} | Default: {info['value']} | {info['label']}")
        if info["visible_from"]:
            lines.append(f"  Visible from: {info['visible_from']}")
        if info["used_in"]:
            rooms_used = sorted(set(info["used_in"]))
            lines.append(f"  Conditions in: {', '.join(rooms_used)}")
        if info["mutated_by"]:
            lines.append(f"  Mutated by: {', '.join(info['mutated_by'])}")
        if not info["used_in"] and not info["mutated_by"]:
            lines.append(f"  ⚠ Unused — no conditions reference it, nothing mutates it")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# Paths — find paths between rooms
# ============================================================

def find_paths(story_dir: Path, from_room: str = None, to_room: str = None) -> str:
    """Find paths between specific rooms."""
    meta = load_meta(story_dir)
    preset = load_story_yaml(story_dir)
    nav = preset["progressive_prompt"]["navigation"]
    connections = nav["connections"]
    room_names = nav["room_names"]

    start = from_room or meta.get("start", "")
    if start not in connections:
        return f"Room '{start}' not found"

    lines = []

    if to_room:
        if to_room not in connections:
            return f"Target room '{to_room}' not found"
        paths = _find_all_paths(connections, start, to_room)
        lines.append(f"Paths from {start} to {to_room}: {len(paths)}\n")
        for i, path in enumerate(paths, 1):
            steps = []
            for j, room in enumerate(path):
                name = room_names.get(room, room)
                if j < len(path) - 1:
                    # Find direction
                    next_room = path[j + 1]
                    direction = "?"
                    for d, dest in connections.get(room, {}).items():
                        if dest == next_room:
                            direction = d
                            break
                    steps.append(f"{room} ─{direction}→")
                else:
                    steps.append(room)
            lines.append(f"  Path {i} ({len(path)-1} moves): {' '.join(steps)}")
    else:
        # Show all reachable rooms from start with distances
        distances = {}
        queue = [(start, 0)]
        while queue:
            current, dist = queue.pop(0)
            if current in distances:
                continue
            distances[current] = dist
            for dest in connections.get(current, {}).values():
                if dest not in distances:
                    queue.append((dest, dist + 1))

        lines.append(f"Reachable from {start}:\n")
        for room, dist in sorted(distances.items(), key=lambda x: x[1]):
            name = room_names.get(room, room)
            lines.append(f"  {'  ' * dist}{room} ({name}) — {dist} moves")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Story Lint — structural analysis for per-room stories")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # overview
    p_overview = subparsers.add_parser("overview", help="Compact structural summary")
    p_overview.add_argument("story_dir", type=Path)

    # validate
    p_validate = subparsers.add_parser("validate", help="Find structural problems")
    p_validate.add_argument("story_dir", type=Path)

    # room
    p_room = subparsers.add_parser("room", help="Detailed view of a single room")
    p_room.add_argument("story_dir", type=Path)
    p_room.add_argument("room_id", type=str)

    # map
    p_map = subparsers.add_parser("map", help="ASCII connection graph")
    p_map.add_argument("story_dir", type=Path)

    # flow
    p_flow = subparsers.add_parser("flow", help="Narrative flow analysis")
    p_flow.add_argument("story_dir", type=Path)

    # state
    p_state = subparsers.add_parser("state", help="State key analysis")
    p_state.add_argument("story_dir", type=Path)

    # paths
    p_paths = subparsers.add_parser("paths", help="Find paths between rooms")
    p_paths.add_argument("story_dir", type=Path)
    p_paths.add_argument("--from", dest="from_room", type=str, default=None)
    p_paths.add_argument("--to", dest="to_room", type=str, default=None)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    story_dir = args.story_dir.resolve()
    if not (story_dir / "story.yaml").exists():
        print(f"Error: No story.yaml found in {story_dir}")
        sys.exit(1)

    if args.command == "overview":
        print(overview(story_dir))
    elif args.command == "validate":
        print(validate(story_dir))
    elif args.command == "room":
        print(room_detail(story_dir, args.room_id))
    elif args.command == "map":
        print(ascii_map(story_dir))
    elif args.command == "flow":
        print(flow_analysis(story_dir))
    elif args.command == "state":
        print(state_analysis(story_dir))
    elif args.command == "paths":
        print(find_paths(story_dir, args.from_room, args.to_room))


if __name__ == "__main__":
    main()
