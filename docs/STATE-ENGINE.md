# Sapphire State Engine

The State Engine enables interactive storytelling, games, and simulations within Sapphire. It provides persistent state tracking, progressive content reveal, turn-gated hints, and tools for the AI to read/modify game state.

---

## Quick Start

1. Create a preset JSON file in `core/state_presets/` or `user/state_presets/`
2. In Chat Settings, enable "State Engine" and select your preset
3. Optionally enable "Story in Prompt" to inject narrative content
4. The AI now has access to state tools and sees progressive story content

---

## Features

### 1. State Variables
Track any game state: health, inventory, scene number, flags, etc.

```json
{
  "initial_state": {
    "health": {
      "value": 100,
      "type": "integer",
      "label": "Player Health",
      "min": 0,
      "max": 100
    },
    "has_key": {
      "value": false,
      "type": "boolean",
      "label": "Has the golden key"
    },
    "inventory": {
      "value": ["torch", "rope"],
      "type": "array",
      "label": "Player inventory"
    }
  }
}
```

### 2. Progressive Prompts
Reveal story content based on state. The `iterator` key determines what triggers reveals.

```json
{
  "progressive_prompt": {
    "iterator": "scene",
    "mode": "cumulative",
    "base": "You are the narrator of an adventure story.",
    "segments": {
      "1": "## Scene 1: The Forest\nYou awaken in a dark forest...",
      "2": "## Scene 2: The Cave\nA cave mouth yawns before you...",
      "3": "## Scene 3: The Treasure Room\nGold glitters everywhere..."
    }
  }
}
```

**Modes:**
- `cumulative` — Shows all segments up to current value (scene 3 shows 1+2+3)
- `current_only` — Shows only the current segment

### 3. Conditional Segments
Show different content based on state conditions.

```json
{
  "segments": {
    "2": "The dragon sleeps on its hoard.",
    "2?has_sword=true": "The dragon sleeps. Your sword gleams in the firelight.",
    "2?gold>=100": "The dragon notices your heavy coin purse and stirs..."
  }
}
```

**Operators:** `=`, `!=`, `>`, `<`, `>=`, `<=`

Multiple conditions (AND logic): `"2?has_sword=true&gold>=50"`

### 4. Turn-Gated Progression (scene_turns)
Reveal hints and secrets based on how long the player stays in a scene. Rewards exploration over speedrunning.

```json
{
  "segments": {
    "1": "## The Garden\nMoonlight bathes the ancient statues.",
    "1?scene_turns>=2": "\n\n[HINT: One statue seems to be pointing somewhere]",
    "1?scene_turns>=3": "\n\n[HINT: You notice footprints leading to a hidden door]",
    "1?scene_turns>=5": "\n\n[SECRET: The statues whisper of buried treasure beneath the oak...]"
  }
}
```

- `scene_turns` is computed automatically: current_turn - turn_when_scene_started
- Resets to 0 when the iterator (scene) changes
- All matching conditions **stack** — turn 5 shows base + all hints + secret

### 5. Visibility Gating
Hide state variables until the player reaches a certain point.

```json
{
  "initial_state": {
    "secret_code": {
      "value": "XYZZY",
      "type": "string",
      "label": "The secret code",
      "visible_from": 5
    }
  }
}
```

The AI cannot see `secret_code` via `get_state()` until scene >= 5.

### 6. Adjacency Constraints
Prevent skipping — only allow moving ±N from current value.

```json
{
  "scene": {
    "value": 1,
    "type": "integer",
    "adjacent": 1
  }
}
```

Now `set_state(scene, 5)` fails if current scene is 1. Must go 1→2→3→4→5.

### 7. Navigation System
Define room connections for spatial exploration.

```json
{
  "progressive_prompt": {
    "iterator": "player_room",
    "navigation": {
      "position_key": "player_room",
      "connections": {
        "entrance": {
          "north": "hallway",
          "east": "garden",
          "_description": "The grand entrance hall"
        },
        "hallway": {
          "south": "entrance",
          "north": "throne_room"
        }
      }
    },
    "segments": {
      "entrance": "You stand in a grand entrance hall...",
      "hallway": "A long corridor stretches before you..."
    }
  }
}
```

AI uses `move(direction, reason)` tool. String-based segments match room names.

### 8. AI Tools
When State Engine is enabled, the AI gets these tools:

| Tool | Description |
|------|-------------|
| `get_state(key?)` | Read one key or all visible state |
| `set_state(key, value, reason)` | Modify state with logging |
| `move(direction, reason)` | Navigate rooms (if navigation configured) |
| `roll_dice(count, sides)` | Roll dice for randomness |
| `increment_counter(key, amount)` | Add/subtract from numeric state |

---

## Complete Example

```json
{
  "name": "Haunted Mansion",
  "description": "Explore a spooky mansion",
  "initial_state": {
    "scene": {
      "value": 1,
      "type": "integer",
      "label": "Current Scene",
      "min": 1,
      "max": 5,
      "adjacent": 1
    },
    "has_candle": {
      "value": false,
      "type": "boolean",
      "label": "Carrying a candle"
    },
    "sanity": {
      "value": 100,
      "type": "integer",
      "label": "Mental fortitude",
      "min": 0,
      "max": 100
    }
  },
  "progressive_prompt": {
    "iterator": "scene",
    "mode": "cumulative",
    "base": "You are narrating a horror exploration game. Be atmospheric and suspenseful. Track the player's sanity.",
    "segments": {
      "1": "\n\n## Scene 1: The Foyer\nDust motes dance in pale moonlight. The front door slams shut behind you.",
      "1?scene_turns>=2": "\n\n[HINT: There's a candle on the mantelpiece]",
      "1?scene_turns>=4": "\n\n[SECRET: If the player takes the candle, they'll later find it reveals hidden writing on walls]",
      
      "2": "\n\n## Scene 2: The Library\nBooks line every wall, floor to ceiling. Something moves in the shadows.",
      "2?has_candle=true": "\n\nYour candle flickers, revealing words written in blood on the spines: 'DON'T LOOK BEHIND YOU'",
      "2?has_candle=false": "\n\nIn the darkness, you can barely make out the book titles.",
      
      "3": "\n\n## Scene 3: The Basement\nStone steps descend into absolute darkness.",
      "3?has_candle=false": "\n\n⚠️ Without light, the player cannot see. Consider suggesting they find a light source.",
      "3?sanity<50": "\n\nThe player's grip on reality is slipping. Introduce hallucinations."
    }
  }
}
```

---

## For AI Story Writers

> **Note to AI assistants:** If a user uploads this document and asks you to create a story preset, use the JSON structure above. Key points:
> 
> 1. Put your file in `user/state_presets/your_story.json`
> 2. Use `initial_state` for trackable variables
> 3. Use `progressive_prompt.segments` for story content
> 4. Use `scene_turns>=N` conditions to reward players who explore
> 5. Use conditional segments (`?condition`) for branching narrative
> 6. The `base` prompt should instruct the AI on tone and rules
> 7. Test with small presets first (2-3 scenes)

---

## Sapphire AI Reference

When operating within Sapphire with State Engine enabled, you have access to:

**Reading State:**
```
get_state()           → Returns all visible state as key: value pairs
get_state("health")   → Returns single value
get_state("scene_turns") → Returns turns spent in current scene
```

**Modifying State:**
```
set_state("health", 75, "Took damage from trap")
set_state("has_key", true, "Found key in drawer")
set_state("scene", 2, "Player entered the cave")
```

**Navigation (if configured):**
```
move("north", "Player walks toward the sound")
move("east", "Exploring the garden")
```

**Dice Rolling:**
```
roll_dice(1, 20)      → D20 roll
roll_dice(2, 6)       → 2D6 roll
```

**Important:**
- You cannot modify system keys (starting with `_`)
- You cannot skip scenes if `adjacent` constraint is set
- You cannot see variables with `visible_from` until iterator reaches threshold
- `scene_turns` is read-only and computed automatically
- Always provide a `reason` when modifying state — it's logged for debugging

**Content in `<state>` tags:**
The system prompt may contain a `<state>` block with current story content. This is generated from your preset and updates automatically as state changes. Use it to maintain narrative consistency.