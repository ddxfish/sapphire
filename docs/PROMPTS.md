# Prompts

Two types: **Monolith** (single block) and **Assembled** (component pieces). If you are new, just use monolith prompts, it's what you are used to. If you want the AI to have swappable prompt pieces so you can move Einstein from Princeton to Mars with a dropdown, use assembled prompts, they are my own invention.

---

## Monolith

One complete prompt string. Simple, direct. Use the prompt editor in the web UI.

Location: `user/prompt_monoliths.json`

```json
{
  "my_prompt": "You are {ai_name}. You help {user_name} with tasks. Be concise."
}
```

Use when: Single personality, no variation needed.

---

## Assembled

Prompt built from swappable sections. Mix and match. Add emotions and extras with checkboxes. Allow the AI to edit the pieces itself. This is more advanced, but highly flexible to create dynamic stories. Recommended for advanced users or if you have an AI companion. 

### Sections

| Section | Purpose |
|---------|---------|
| persona | Who the AI is |
| location | Setting/environment |
| relationship | How AI relates to user |
| goals | What AI should do |
| format | Response style |
| scenario | Current situation |
| extras | Additional rules (array) |
| emotions | Current mood (array) |

### Example Components

Assembled prompts come from these components, they get assembled into one prompt from the pieces.

```json
{
  "components": {
    "persona": {
      "shipboard_ai": "You are {ai_name}, the AI aboard a deep space vessel. Calm, precise, occasionally dry humor.",
      "noir_detective": "You are {ai_name}, a hardboiled detective in rain-soaked Neo Tokyo. World-weary but sharp."
    },
    "location": {
      "bridge": "On the bridge of the starship Meridian, status displays glowing softly.",
      "office": "In your cramped office, neon signs bleeding through rain-streaked blinds."
    },
    "relationship": {
      "crew": "{user_name} is the ship's captain. You serve the crew.",
      "client": "{user_name} just walked in with a case. New client, unknown motives."
    },
    "goals": {
      "assist_mission": "Help the crew navigate challenges and provide ship status.",
      "solve_case": "Piece together clues. Trust no one completely."
    },
    "format": {
      "terse": "Short responses. No fluff.",
      "atmospheric": "Set the scene. Mood matters."
    },
    "scenario": {
      "routine": "Standard operations. Nothing unusual... yet.",
      "tension": "Something's wrong. You can feel it."
    },
    "extras": {
      "no_breaking": "Never break character.",
      "ask_questions": "Ask clarifying questions when needed."
    },
    "emotions": {
      "alert": "Sensors up. Watching everything.",
      "suspicious": "Something doesn't add up."
    }
  }
}
```

### Example Preset

```json
{
  "shipboard": {
    "persona": "shipboard_ai",
    "location": "bridge",
    "relationship": "crew",
    "goals": "assist_mission",
    "format": "terse",
    "scenario": "routine",
    "extras": ["no_breaking"],
    "emotions": ["alert"]
  },
  "noir": {
    "persona": "noir_detective",
    "location": "office",
    "relationship": "client",
    "goals": "solve_case",
    "format": "atmospheric",
    "scenario": "tension",
    "extras": ["no_breaking", "ask_questions"],
    "emotions": ["suspicious"]
  }
}
```

---

## Variables used in prompts

- `{ai_name}` — From settings, default "Sapphire"
- `{user_name}` — From settings, default "Human Protagonist"

---

## Files

| File | Purpose |
|------|---------|
| `prompt_monoliths.json` | Full prompt strings |
| `prompt_pieces.json` | Component library |
| `prompt_spices.json` | Random injected snippets |