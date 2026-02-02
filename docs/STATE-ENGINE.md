# Sapphire State Engine

The State Engine enables interactive storytelling, games, and simulations within Sapphire. It provides persistent state tracking, progressive content reveal, turn-gated hints, binary choices, riddles with clues, and dice rolls with auto-bypass.

---

## Quick Start

1. Create a preset JSON file in `user/state_presets/`
2. In Chat Settings, enable "State Engine" and select your preset
3. Enable "Story in Prompt" to inject narrative content
4. The AI now has access to state tools and sees progressive story content
5. Use "Clear Chat" to reset the game and start fresh

---

## File Locations

| Location | Purpose |
|----------|---------|
| `user/state_presets/` | Your custom presets (not tracked by git) |
| `core/state_engine/presets/` | Built-in presets that ship with Sapphire |
| `core/state_engine/presets/_base.json` | Base instructions for all game types |
| `core/state_engine/presets/_linear.json` | Instructions for linear/scene-based stories |
| `core/state_engine/presets/_rooms.json` | Instructions for room navigation mode |

User presets override core presets if they share the same filename.

---

## Resetting State

State persists in the database until explicitly reset. Three ways to reset:

| Method | What It Does |
|--------|--------------|
| **Clear Chat** (kebab menu) | Clears messages AND resets state to preset defaults |
| **Rollback** (trash icon on message) | Removes messages from that point; state rolls back to that turn |
| **Reset State** (Chat Settings → State Engine → Reset) | Resets state without clearing chat history |

**Important:** Switching presets on a chat with existing state will preserve old state values. Use Reset to start fresh with a new preset.

---

## AI Tools

When State Engine is enabled, the AI gets these tools:

| Tool | Description |
|------|-------------|
| `get_state(key?)` | Read one key or all visible state |
| `set_state(key, value, reason)` | Modify state (also used for choices and riddle answers) |
| `advance_scene(reason)` | Move to next scene (validates blockers first) |
| `roll_dice(count, sides)` | Roll dice; auto-detects riddles with `dice_dc` |
| `move(direction, reason)` | Navigate rooms (if navigation configured) |

---

## Features

### 1. State Variables

Track any game state with types and constraints:

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
    "scene": {
      "value": 1,
      "type": "integer",
      "label": "Current Scene",
      "min": 1,
      "max": 5,
      "adjacent": 1
    },
    "secret_code": {
      "value": "XYZZY",
      "type": "string",
      "label": "Hidden until scene 3",
      "visible_from": 3
    }
  }
}
```

**Constraints:**
- `min`/`max` — Enforce numeric bounds
- `adjacent` — Only allow changes within ±N of current value (prevents skipping)
- `visible_from` — Hide from AI until iterator reaches this value

### 2. Progressive Prompts

Reveal story content based on the iterator state:

```json
{
  "progressive_prompt": {
    "iterator": "scene",
    "mode": "cumulative",
    "base": "You are Sapphire, an AI narrator. Never reveal future scenes.",
    "segments": {
      "1": "## Scene 1: The Forest\nYou awaken in darkness...",
      "2": "## Scene 2: The Cave\nA cave mouth yawns...",
      "3": "## Scene 3: The Treasure\nGold glitters..."
    }
  }
}
```

**Modes:**
- `cumulative` — Shows all segments up to current value
- `current_only` — Shows only the matching segment

### 3. Conditional Segments

Branch narrative based on state:

```json
{
  "segments": {
    "2": "The dragon sleeps.",
    "2?has_sword=true": "Your sword gleams in the firelight.",
    "2?trust_ai=yes": "Your neural link hums with shared thoughts.",
    "2?trust_ai=no": "You work alone, as you chose."
  }
}
```

**Operators:** `=`, `!=`, `>`, `<`, `>=`, `<=`

**Multiple conditions (AND):** `"2?has_sword=true&gold>=50"`

### 4. Turn-Gated Hints (scene_turns)

Reveal content based on how long the player explores a scene:

```json
{
  "segments": {
    "1": "## The Garden\nMoonlight bathes the statues.",
    "1?scene_turns>=2": "\n\n[HINT] One statue points somewhere...",
    "1?scene_turns>=4": "\n\n[HINT] Footprints lead to a hidden door..."
  }
}
```

- `scene_turns` = current_turn - turn_when_scene_started
- Resets to 0 when iterator changes
- All matching conditions **stack**

### 5. Binary Choices

Force decisions that block progression:

```json
{
  "binary_choices": [
    {
      "id": "trust_decision",
      "state_key": "trust_ai",
      "prompt": "Do you trust me?",
      "visible_from_scene": 2,
      "required_for_scene": 3,
      "options": {
        "yes": {
          "description": "Give full control",
          "set": { "bond": "+10" }
        },
        "no": {
          "description": "Keep manual control",
          "set": { "bond": "-5" }
        }
      }
    }
  ]
}
```

- AI uses `set_state("trust_ai", "yes", "reason")` to choose
- `required_for_scene` blocks `advance_scene()` until choice is made
- `set` applies automatic consequences (supports `"+10"` relative values)

### 6. Riddles with Progressive Clues

Puzzles where neither AI nor player knows the answer:

```json
{
  "riddles": [
    {
      "id": "vault",
      "state_key": "vault_code",
      "name": "Vault Lock",
      "type": "fixed",
      "answer": "0451",
      "digits": 4,
      "max_attempts": 3,
      "visible_from_scene": 3,
      "clues": {
        "1": "The code was set on a special day...",
        "2?scene_turns>=2": "Something about classic games...",
        "3?scene_turns>=4": "0-4-5-1. The legendary door code."
      },
      "success_message": "The vault opens!",
      "fail_message": "Wrong code.",
      "lockout_message": "Too many attempts. Locked out.",
      "success_sets": { "vault_status": "opened_code" },
      "lockout_sets": { "vault_status": "locked_out" },
      "dice_dc": 15,
      "dice_success_sets": { "vault_status": "opened_manual" }
    }
  ]
}
```

**Clue Display Format:**
```
[CLUE:1/3] The code was set on a special day...
[NEW CLUE:2/3] Something about classic games...
```

- AI attempts with `set_state("vault_code", "0451", "reason")`
- Wrong answers decrement attempts
- Clues reveal progressively based on `scene_turns`
- `[NEW CLUE:X/Y]` marks the latest clue for narrative emphasis

### 7. Dice Rolls with Auto-Bypass

Roll dice for skill checks. Auto-detects active riddles:

```json
// In riddle config:
"dice_dc": 15,
"dice_success_sets": { "vault_status": "opened_manual" }
```

**AI just calls:**
```
roll_dice(1, 20)
```

**System auto-detects:**
- Finds active riddle with `dice_dc` in current scene
- Compares roll to DC
- On success: marks riddle solved, applies `dice_success_sets`

**Output:**
```
🎲 Rolled d20: 18 vs DC 15 — SUCCESS! (bypassed vault)
  → vault_status = opened_manual
```

### 8. Scene Advancement

Use `advance_scene(reason)` instead of manually setting the iterator:

```
advance_scene("Player solved the puzzle")
```

**Will fail if:**
- A required choice hasn't been made
- Already at final scene
- Adjacent constraint violated

**AI sees:**
- "📍 More story ahead — use advance_scene() when ready"
- "📍 Final scene — story concludes here"

(Scene count is hidden to prevent meta-gaming)

### 9. Navigation (Room-Based)

For spatial exploration instead of linear scenes:

```json
{
  "progressive_prompt": {
    "iterator": "room",
    "navigation": {
      "position_key": "room",
      "connections": {
        "entrance": { "north": "hallway", "east": "garden" },
        "hallway": { "south": "entrance", "north": "throne" }
      }
    },
    "segments": {
      "entrance": "The grand entrance hall...",
      "hallway": "A long corridor..."
    }
  }
}
```

AI uses `move("north", "reason")` to navigate.

---

## Complete Example Preset

```json
{
  "name": "Escape Pod",
  "description": "Escape a damaged ship before it falls into a black hole",
  "game_type": "linear",

  "initial_state": {
    "scene": {
      "value": 1,
      "type": "integer",
      "label": "Current Scene",
      "min": 1,
      "max": 3,
      "adjacent": 1
    },
    "trust": {
      "value": "",
      "type": "choice",
      "label": "Trust decision"
    },
    "airlock": {
      "value": "sealed",
      "type": "string",
      "label": "Airlock status"
    },
    "code_answer": {
      "value": "",
      "type": "riddle_answer",
      "label": "Airlock code attempt"
    }
  },

  "binary_choices": [
    {
      "id": "trust_check",
      "state_key": "trust",
      "prompt": "Do you trust the AI to take control?",
      "visible_from_scene": 1,
      "required_for_scene": 2,
      "options": {
        "yes": { "description": "Full neural link" },
        "no": { "description": "Manual control only" }
      }
    }
  ],

  "riddles": [
    {
      "id": "airlock",
      "state_key": "code_answer",
      "name": "Airlock Override",
      "type": "fixed",
      "answer": "1234",
      "digits": 4,
      "max_attempts": 3,
      "visible_from_scene": 2,
      "clues": {
        "1": "The code is your anniversary date...",
        "2?scene_turns>=2": "Month and day you first met: January 23rd",
        "3?scene_turns>=4": "01-23... wait, that's 0123. Or is it 1234?"
      },
      "success_sets": { "airlock": "opened" },
      "lockout_sets": { "airlock": "jammed" },
      "dice_dc": 15,
      "dice_success_sets": { "airlock": "forced" }
    }
  ],

  "progressive_prompt": {
    "iterator": "scene",
    "mode": "cumulative",
    "base": "You are an AI companion on a damaged ship. Be emotional but concise.",
    "segments": {
      "1": "## Scene 1: Alarms\nThe ship shudders. You have minutes.",
      "1?scene_turns>=2": "\n\nYou notice the escape pod bay on your map.",

      "2": "## Scene 2: The Lock\nThe airlock needs a 4-digit code.",
      "2?trust=yes": "\n\nYour neural link lets you sense the code at the edge of memory.",
      "2?trust=no": "\n\nYou must relay clues verbally. It's slower.",

      "3": "## Scene 3: Escape\nThe moment of truth.",
      "3?airlock=opened": "\n\nThe pod launches. You made it. Together.",
      "3?airlock=forced": "\n\nSparks fly, but you're free. Not elegant, but alive.",
      "3?airlock=jammed": "\n\nThe lock is jammed. As the event horizon approaches, you hold each other."
    }
  }
}
```

---

## Preset Design Tips

1. **Use `airlock_status` not `ending`** — Set intermediate state, then resolve in final scene
2. **Hide spoilers with `visible_from`** — AI can't see state until the right moment
3. **Early clues subtle, late clues obvious** — Help stuck players without spoiling
4. **Write clues as character memories** — "You remember..." not "HINT: the answer is..."
5. **Test with 2-3 scenes first** — Expand after core mechanics work
6. **Use `dice_dc` for alternate paths** — Let players bypass puzzles with luck
7. **Required choices block progression** — Use `required_for_scene` to force decisions

---

## For AI Story Writers

> **If you're an AI creating a preset:** Copy the example above as a template. Key rules:
>
> 1. Put file in `user/state_presets/your_story.json`
> 2. Use `game_type: "linear"` for scene-based stories
> 3. Set `adjacent: 1` on scene to prevent skipping
> 4. Use `visible_from` to hide spoilers
> 5. Write clues as narrative discoveries, not meta-hints
> 6. Test riddles — the AI doesn't know answers either
> 7. Always provide `dice_dc` as a bypass option for riddles

---

## Troubleshooting

**Clues all appear at once:** Check that `scene_turns` conditions are working. Ensure the engine reloads properly after Clear Chat.

**AI narrates ending early:** Don't set `ending` directly — use intermediate state like `door_status`, then resolve in final scene.

**Choices not blocking:** Verify `required_for_scene` matches the scene number you want to block.

**Dice bypass not working:** Ensure riddle has `dice_dc` set and isn't already solved/locked.

**State persists after Clear Chat:** The state engine should reset automatically. Check server logs for `[CLEAR]` messages.
