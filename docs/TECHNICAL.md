## Technical Reference

For developers and those who want to understand the internals.

### Configuration Layers

```
core/settings_defaults.json    ← System defaults (don't edit)
        ↓ merged with
user/settings.json             ← Your overrides
        ↓ equals
Runtime config
```

On first run, `user/settings.example.json` is created as a template. Copy to `user/settings.json` and add only settings you want to override.

Settings can be changed via:
- **UI** - Gear icon (recommended)
- **File** - Edit `user/settings.json` (auto-detected within ~2 seconds)
- **API** - `PUT /api/settings/<key>`

### Reload Tiers

| Tier | When | Examples |
|------|------|----------|
| Hot | Immediate | Names, TTS voice/speed/pitch, generation params |
| Component | Restart needed | TTS/STT enabled, server URLs, module toggles |
| Restart | Restart needed | Ports, paths, model configs |

The UI indicates the tier for each setting.

### Key File Locations

| File | Purpose |
|------|---------|
| `core/settings_defaults.json` | System defaults (read-only) |
| `core/settings_help.json` | Help text for UI |
| `user/settings.json` | Your overrides |
| `user/settings/chat_defaults.json` | Defaults for new chats |
| `~/.config/sapphire/secret_key` | Password hash |

### Authentication

The bcrypt hash serves as password, API key, and session secret. Delete to reset (triggers setup wizard).

- **Linux/Mac:** `~/.config/sapphire/secret_key`
- **Windows:** `%APPDATA%\sapphire\secret_key`

### Default Ports

These are the original defaults (adjustable in Settings soon):

- Internal API: `127.0.0.1:8071`
- Web UI: `0.0.0.0:8073` (HTTPS self-signed)
- TTS Server: `5012`
- STT Server: `5050`

### SOCKS Proxy Authentication

Two options for proxy credentials:

**Option 1 - Config file** (recommended): Create `user/.socks_config` with username on line 1, password on line 2.

**Option 2 - Environment variables:**
- `SAPPHIRE_SOCKS_USERNAME`
- `SAPPHIRE_SOCKS_PASSWORD`

### Troubleshooting

**Settings not working?** Restart Sapphire, verify JSON syntax, check logs.

**Reset to defaults?** Delete `user/settings.json` or the whole user/ dir if you want.

**Can't log in?** Delete `~/.config/sapphire/secret_key` and restart.



## Prompts 
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

### Files

| File | Purpose |
|------|---------|
| `prompt_monoliths.json` | Full prompt strings |
| `prompt_pieces.json` | Component library |
| `prompt_spices.json` | Random injected snippets |